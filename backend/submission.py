"""Authenticated club web intake and an explicitly local demonstration workspace."""
from __future__ import annotations

import io
import re
import uuid
from datetime import datetime
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from shared.schemas import ItemType, Role
from . import auth, checks, config, extract, issue_builder, store, audit, trace

router = APIRouter()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000


def _staff(email: str) -> dict:
    role = auth.require_role(email, Role.CLUB_REP, Role.STUDENT_AFFAIRS)
    if role['role'] == Role.CLUB_REP and not role.get('club_id'):
        raise HTTPException(403, 'Ask Student Affairs to assign your account to a club before submitting.')
    return role


def _own_item(item_id: str, email: str) -> dict:
    role = _staff(email)
    item = store.get_item(item_id)
    if not item:
        raise HTTPException(404, "Submission not found")
    if role['role'] != Role.STUDENT_AFFAIRS and item.get('club_id') != role.get('club_id'):
        raise HTTPException(403, "You can only open your club's submissions.")
    return item


def _own_upload(upload_id: str, email: str) -> dict:
    role = _staff(email)
    if not re.fullmatch(r'[0-9a-f]{32}', upload_id):
        raise HTTPException(404, 'Poster upload not found')
    snap = store.db().collection('poster_uploads').document(upload_id).get()
    upload = snap.to_dict() if snap.exists else None
    if not upload:
        raise HTTPException(404, "Poster upload not found")
    if role['role'] != Role.STUDENT_AFFAIRS and upload['uploaded_by'] != email.lower():
        raise HTTPException(403, "This poster was uploaded by another account. Upload your own copy.")
    return upload


@router.get('/submit', response_class=HTMLResponse)
def submission_page():
    return HTMLResponse(issue_builder.env.get_template('submit.html').render(
        client_id=config.OAUTH_CLIENT_ID, dev_mode=config.DEV_MODE, local_mode=config.LOCAL_MODE,
        demo_switcher=config.LOCAL_MODE, demo_view='club',
        ai_configured=extract.ai_configured(), required_fields=checks.REQUIRED))


@router.post('/admin/api/uploads')
async def upload_poster(file: UploadFile = File(...), email: str = Depends(auth.web_user)):
    role = _staff(email)
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, 'Keep the poster under 10 MB.')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in {'PNG', 'JPEG', 'WEBP'}:
                raise ValueError('format')
            if im.width * im.height > MAX_IMAGE_PIXELS:
                raise HTTPException(413, 'The poster is too large. Use an image under 25 megapixels.')
            im.verify()
        # Store a decoded image, not arbitrary bytes or user-supplied filenames/metadata.
        with Image.open(io.BytesIO(raw)) as im:
            clean = io.BytesIO()
            im.convert('RGB').save(clean, 'JPEG', quality=94)
            image = clean.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(415, 'Choose a valid PNG, JPG or WebP poster image. PDFs are not supported.')
    upload_id = uuid.uuid4().hex
    name = f'web-{upload_id}.jpg'
    if config.LOCAL_MODE:
        folder = config.LOCAL_DATA_DIR / 'posters'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(image)
        uri = f'local:posters/{name}'
    else:
        if not config.POSTER_BUCKET:
            raise HTTPException(503, 'Poster storage is not configured.')
        from google.cloud import storage
        storage.Client().bucket(config.POSTER_BUCKET).blob(f'posters/{name}').upload_from_string(
            image, content_type='image/jpeg')
        uri = f'gs://{config.POSTER_BUCKET}/posters/{name}'
    store.db().collection('poster_uploads').document(upload_id).set({
        'id': upload_id, 'uri': uri, 'uploaded_by': email.lower(), 'club_id': role.get('club_id'),
        'created_at': store.now(), 'original_name': Path(file.filename or 'poster').name,
    })
    audit.log(email, 'web_poster_uploaded', None, {'upload_id': upload_id})
    trace.record('human', 'poster_uploaded', 'Poster uploaded; decoded and re-saved as a clean JPEG (metadata removed)',
                 poster_uri=uri, actor=email, detail={'bytes': len(image)})
    return {'upload_id': upload_id, 'filename': Path(file.filename or 'poster').name}


class ExtractUpload(BaseModel):
    type: ItemType = ItemType.EVENT


@router.post('/admin/api/uploads/{upload_id}/extract')
def extract_upload(upload_id: str, body: ExtractUpload, email: str = Depends(auth.web_user)):
    upload = _own_upload(upload_id, email)
    if not extract.ai_configured():
        raise HTTPException(503, 'Gemini is not configured. Enter the poster details manually; you can still submit for review.')
    from . import main
    try:
        result = main.extract_item(main.ExtractReq(user_email=email, type=body.type, gcs_uri=upload['uri']))
    except HTTPException:
        raise
    except Exception as exc:
        audit.log(email, 'web_extraction_failed', None, {'error_type': type(exc).__name__})
        trace.record('code', 'manual_entry_fallback', 'Gemini was unavailable; the club was asked to enter the details manually',
                     poster_uri=upload['uri'], actor='code', ok=False, detail={'error_type': type(exc).__name__})
        raise HTTPException(502, 'The poster could not be read with Gemini. Your upload is saved; enter or correct the details manually.')
    store.db().collection('poster_uploads').document(upload_id).update({
        'extracted_type': body.type.value, 'extracted_data': result['data'], 'extracted_at': store.now()})
    return result


class WebSubmission(BaseModel):
    type: ItemType
    data: dict
    description: str = Field(default='', max_length=2000)
    upload_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    show_poster: bool = True
    confirmed: bool = False


def _request(body: WebSubmission, email: str):
    from .main import SubmitReq
    _staff(email)
    if not body.confirmed:
        raise HTTPException(422, 'Check and confirm the details before submitting.')
    upload = _own_upload(body.upload_id, email) if body.upload_id else None
    return SubmitReq(user_email=email, type=body.type, data=body.data,
                     description=body.description, poster_uri=upload['uri'] if upload else None,
                     show_poster=body.show_poster)


@router.post('/admin/api/submissions')
def submit(body: WebSubmission, email: str = Depends(auth.web_user)):
    from .main import submit_item
    result = submit_item(_request(body, email))
    return result


@router.patch('/admin/api/submissions/{item_id}')
def correct(item_id: str, body: WebSubmission, email: str = Depends(auth.web_user)):
    from .main import edit_item
    item = _own_item(item_id, email)
    if body.type.value != item['type']:
        raise HTTPException(422, 'Keep the original submission type when correcting an item.')
    return edit_item(item_id, _request(body, email))


@router.get('/admin/api/submissions/{item_id}')
def submission_detail(item_id: str, email: str = Depends(auth.web_user)):
    from .main import staff_poster_url
    item = _own_item(item_id, email)
    item['poster_url'] = staff_poster_url(item_id) if item.get('poster_uri') else None
    item['publication_preview'] = issue_builder.env.get_template('_publication_copy.html').render(
        item=item, base='', save_q='', preview=True)
    return item


def _local_only():
    if not config.LOCAL_MODE:
        raise HTTPException(404, 'Not found')


@router.get('/demo', response_class=HTMLResponse)
def demo_page():
    _local_only()
    items = store.list_items(['draft', 'pending', 'approved', 'published', 'rejected'])
    latest = max(items, key=lambda i: str(i.get('created_at') or ''), default=None)
    return HTMLResponse(issue_builder.env.get_template('demo.html').render(
        demo_switcher=config.LOCAL_MODE, demo_view='home', ai_configured=extract.ai_configured(),
        model=config.GEMINI_MODEL, latest_item_id=latest['id'] if latest else None))


def _outbox_summary(path: Path) -> dict:
    """Read only the local mailer's escaped metadata header, never email body HTML."""
    subject, recipient, kind = 'Saved email', 'Recipient unavailable', 'Email'
    try:
        if path.resolve().parent != path.parent.resolve():
            raise OSError('Outbox entry points outside its folder')
        with path.open(encoding='utf-8', errors='replace') as message:
            prefix = message.read(8192)
        header = re.match(r"\s*<p\b[^>]*>To:\s*([^<]*)<br\s*/?>\s*Subject:\s*([^<]*)</p>",
                          prefix, flags=re.IGNORECASE)
        if header:
            recipient = ' '.join(unescape(header[1]).split())[:320] or recipient
            subject = ' '.join(unescape(header[2]).split())[:300] or subject
            if subject.startswith('Your BoardCast code:'):
                subject, kind = 'Your BoardCast verification code', 'Verification'
            elif subject.startswith('BoardCast submission rejected:'):
                kind = 'Submission update'
            elif subject.startswith('Starting soon:'):
                kind = 'Event reminder'
            elif subject.startswith('Campus Weekly preview:'):
                kind = 'Review preview'
            elif subject.startswith('Campus Weekly'):
                kind = 'Newsletter'
        saved = datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo(config.TIMEZONE))
        saved_at, saved_label = saved.isoformat(), saved.strftime('%d %b %Y, %I:%M %p').lstrip('0') + ' MYT'
    except OSError:
        saved_at, saved_label = '', 'Saved time unavailable'
    return {'name': path.name, 'subject': subject, 'recipient': recipient, 'kind': kind,
            'saved_at': saved_at, 'saved_label': saved_label}


@router.get('/demo/outbox', response_class=HTMLResponse)
def demo_outbox(message: str = ''):
    _local_only()
    folder = config.LOCAL_DATA_DIR / 'outbox'
    files = sorted(folder.glob('*.html'), key=lambda p: p.name, reverse=True)[:50]
    filenames = [p.name for p in files]
    selected = message if message in filenames else (filenames[0] if filenames else None)
    return HTMLResponse(issue_builder.env.get_template('outbox.html').render(
        files=[_outbox_summary(path) for path in files], selected=selected,
        demo_switcher=config.LOCAL_MODE, demo_view='outbox'))


@router.get('/demo/outbox/{filename}', response_class=HTMLResponse)
def demo_email(filename: str):
    _local_only()
    root = (config.LOCAL_DATA_DIR / 'outbox').resolve()
    path = (root / filename).resolve()
    if path.parent != root or path.suffix != '.html' or not path.is_file():
        raise HTTPException(404, 'Email not found')
    return FileResponse(path, media_type='text/html', headers={
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "sandbox allow-downloads allow-popups allow-popups-to-escape-sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; frame-ancestors 'self'",
    })


@router.post('/admin/api/demo/preview')
def demo_preview(email: str = Depends(auth.web_user)):
    _local_only()
    auth.require_role(email, Role.STUDENT_AFFAIRS)
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    issue_id, report = issue_builder.build_preview(today)
    return {'issue_id': issue_id, 'magazine_url': f'/m/{issue_id}', 'report': report}


@router.post('/admin/api/demo/publish')
def demo_publish(email: str = Depends(auth.web_user)):
    _local_only()
    auth.require_role(email, Role.STUDENT_AFFAIRS)
    from .main import issue_send
    return issue_send()
