"""Explicit, single-recipient Gmail previews from the local review desk.

This never changes subscription or publication state. Normal local mail still goes
to the outbox. A durable receipt prevents duplicate sends and records uncertainty.
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import timedelta
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from google.auth.exceptions import RefreshError
from PIL import Image
from pydantic import BaseModel, Field

from shared.schemas import Role
from . import auth, checks, config, delivery, issue_builder, mailer, store

router = APIRouter()
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


def _local_request(request: Request) -> None:
    if not config.LOCAL_MODE:
        raise HTTPException(404, "Not found")
    # This feature uses local demo identities. Reject DNS rebinding and cross-origin
    # browser requests rather than exposing those identities as public mail access.
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "Open BoardCast on localhost to send a preview.")
    origin = request.headers.get("origin")
    if origin:
        expected = urlsplit(str(request.base_url))
        supplied = urlsplit(origin)
        if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc):
            raise HTTPException(403, "Open the email preview form in this BoardCast window.")


def _edition(doc: dict):
    if doc.get("status") not in {"preview", "sent"}:
        raise HTTPException(409, "Use an active preview or a completed edition. Held and unfinished editions cannot be emailed.")
    return issue_builder.from_saved(doc, for_send=doc["status"] != "sent")


def revision(doc: dict) -> str | None:
    """Bind the reviewer action to the actual approved copy and saved design."""
    if doc.get("status") not in {"preview", "sent"}:
        return None
    edition, plan = _edition(doc)
    return _revision(doc, edition, plan)


def _revision(doc: dict, edition: dict, plan) -> str | None:
    if not issue_builder.all_items(edition):
        return None
    value = json.dumps({"id": doc["id"], "edition": edition, "plan": plan.model_dump()},
                       sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def _poster_images(items: list[dict]) -> tuple[dict[str, str], dict[str, bytes]]:
    from .main import _load_poster
    sources, images = {}, {}
    for item in items:
        if not item.get("show_poster") or not item.get("poster_uri"):
            continue
        try:
            raw, _ = _load_poster(item["poster_uri"])
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("image size")
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > 25_000_000:
                    raise ValueError("image dimensions")
                image = source.convert("RGB")
                image.thumbnail((900, 1400))
                output = io.BytesIO()
                image.save(output, "JPEG", quality=85, optimize=True)
            cid = "poster-" + hashlib.sha256(item["id"].encode()).hexdigest()[:24] + "@boardcast"
            sources[item["id"]] = "cid:" + cid
            images[cid] = output.getvalue()
        except Exception:
            raise HTTPException(422, "A poster could not be included. Check the edition's images before sending.") from None
        if sum(map(len, images.values())) > MAX_TOTAL_BYTES:
            raise HTTPException(422, "The preview has too many large posters. Reduce the images before sending.")
    return sources, images


class _RemotePreviewHTML(HTMLParser):
    """Adapt generated email HTML without altering its layout or global URL config.

    Only rendered BoardCast links are transformed. No URL is fetched. Images come
    from the approved snapshot; local web/calendar links are omitted, and tracked
    registration links are replaced with the approved external destination.
    """
    def __init__(self, items: list[dict], posters: dict[str, str]):
        super().__init__(convert_charrefs=False)
        self.items = {i["id"]: i for i in items}
        self.posters = posters
        self.output = []
        self.anchors = []
        self.omit = 0
        self.base = urlsplit(config.PUBLIC_BASE_URL)

    def internal_path(self, value: str) -> str | None:
        url = urlsplit(value)
        if (not url.netloc and not url.scheme) or (url.scheme, url.netloc) == (self.base.scheme, self.base.netloc):
            return url.path
        return None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            path = self.internal_path(attrs.get("href") or "")
            if path and path.startswith("/r/"):
                item = self.items.get(path[3:]) or {}
                data = item.get("data") or {}
                target = (data.get("registration") or {}).get("link") if item.get("type") == "event" else data.get("action_link")
                if target and not checks.link_problem(target):
                    attrs["href"] = target
                    self.anchors.append("a")
                else:
                    self.anchors.append("omit")
                    self.omit += 1
                    return
            elif path and path.startswith("/p/"):
                self.anchors.append("unwrap")
                return
            elif path is not None:
                self.anchors.append("omit")
                self.omit += 1
                return
            else:
                self.anchors.append("a")
        if self.omit:
            return
        if tag == "img":
            path = self.internal_path(attrs.get("src") or "")
            item_id = path[3:-4] if path and path.startswith("/p/") and path.endswith(".jpg") else None
            if item_id not in self.posters:
                # A missing approved image must not turn into a broken localhost image.
                raise HTTPException(422, "A poster is unavailable. Check the edition before sending.")
            attrs["src"] = self.posters[item_id]
        values = "".join(" " + k if v is None else f' {k}="{escape(v, quote=True)}"' for k, v in attrs.items())
        self.output.append(f"<{tag}{values}>")

    def handle_endtag(self, tag):
        if tag == "a":
            kind = self.anchors.pop() if self.anchors else "a"
            if kind == "omit":
                self.omit -= 1
                return
            if kind == "unwrap":
                return
        if not self.omit:
            self.output.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if not self.omit:
            self.output.append(data)

    def handle_entityref(self, name):
        self.handle_data(f"&{name};")

    def handle_charref(self, name):
        self.handle_data(f"&#{name};")

    def handle_comment(self, data):
        if not self.omit:
            self.output.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.output.append(f"<!{decl}>")


def render(doc: dict, *, snapshot=None) -> tuple[str, str, dict[str, bytes]]:
    edition, plan = snapshot if snapshot is not None else _edition(doc)
    items = issue_builder.all_items(edition)
    if not items:
        raise HTTPException(409, "No approved items remain. Rebuild the preview before sending.")
    posters, images = _poster_images(items)
    # No subscription token or online-edition link is issued for a one-off copy.
    html = issue_builder.render_email(edition, plan, None)
    converted = _RemotePreviewHTML(items, posters)
    converted.feed(html)
    converted.close()
    html = "".join(converted.output)
    note = ('<div style="padding:14px 20px;font:14px/1.5 Arial,sans-serif;color:#24334a;background:#eef3fa;">'
            '<strong>BoardCast preview copy</strong> · Sent by Student Affairs to the selected address. '
            'You have not been subscribed. Posters are included; online-edition and calendar links '
            'are unavailable while BoardCast runs locally.</div>')
    import re
    html = re.sub(r"(<body\b[^>]*>)", lambda m: m[1] + note, html, count=1, flags=re.I)
    if len(html.encode()) + sum(map(len, images.values())) > MAX_TOTAL_BYTES:
        raise HTTPException(422, "This preview is too large to email. Reduce its content before sending.")
    subject = "[Preview] " + " ".join(plan.subject_line.split())
    return subject, html, images


def _result(receipt: dict, duplicate: bool = False) -> dict:
    state = receipt["status"]
    messages = {
        "sent": "Gmail accepted this preview. Check the recipient's inbox or spam folder.",
        "sending": "This preview is already being sent. Do not send another copy while it finishes.",
        "unknown": "Delivery could not be confirmed. Check the sender's Sent folder before taking further action; this copy will not be resent automatically.",
        "failed": receipt.get("error") or "Gmail rejected this send. Check the sender setup before trying again.",
    }
    return {"status": state, "recipient": receipt["recipient"], "message": messages[state], "duplicate": duplicate}


def send_preview(issue_id: str, recipient: str, expected_revision: str, actor: str) -> dict:
    doc = store.get_issue(issue_id)
    if not doc:
        raise HTTPException(404, "Edition not found")
    # Capture once: the reviewed hash, rendered copy and claim checks must refer
    # to the same item versions, even if another reviewer edits during rendering.
    edition, plan = _edition(doc)
    current_revision = _revision(doc, edition, plan)
    if not current_revision or current_revision != expected_revision:
        raise HTTPException(409, "The approved content or template has changed. Refresh and review the edition before sending.")
    key = ("email_previews", hashlib.sha256(f"{issue_id}|{current_revision}|{recipient}".encode()).hexdigest())
    existing = store.db().collection(key[0]).document(key[1]).get().to_dict()
    if existing and existing.get("status") in {"sent", "unknown"}:
        return _result(existing, True)
    status = mailer.real_email_status()
    if not status["configured"]:
        raise HTTPException(503, status["message"])
    subject, html, images = render(doc, snapshot=(edition, plan))
    items = issue_builder.all_items(edition)
    issue_key = ("issues", issue_id)
    item_keys = [("items", item["id"]) for item in items] if doc["status"] == "preview" else []
    owner = uuid.uuid4().hex

    def claim(rows):
        if rows[issue_key] != doc or any(rows[item_key] != item for item_key, item in zip(item_keys, items)):
            raise HTTPException(409, "This edition changed. Refresh and review it before sending.")
        receipt = rows[key]
        if receipt and receipt.get("status") in {"sent", "unknown", "sending"}:
            if receipt["status"] == "sending" and receipt["updated_at"] < store.now() - timedelta(minutes=2):
                return {key: {"status": "unknown", "updated_at": store.now()}}
            return {}
        return {key: {"issue_id": issue_id, "revision": current_revision, "recipient": recipient,
                      "status": "sending", "owner": owner, "requested_by": actor,
                      "subject": subject, "updated_at": store.now(), "error": None}}

    changed = store.atomic_documents([issue_key, *item_keys, key], claim)
    receipt = changed[key]
    if receipt.get("owner") != owner:
        return _result(receipt, True)
    try:
        message_id = mailer.send_real(recipient, subject, html, images)
    except (mailer.MailerConfigurationError, RefreshError):
        state, message_id = "failed", None
        error = "The sender could not be authorised. Reconnect Gmail using the setup guide, then try again."
    except Exception as exc:
        state, message_id = ("failed" if delivery._definitely_rejected(exc) else "unknown"), None
        error = "Gmail rejected this send. Check account permissions and quota before trying again." if state == "failed" else None
    else:
        state, error = "sent", None

    def finish(rows):
        if (rows[key] or {}).get("owner") != owner:
            return {}
        return {key: {"status": state, "message_id": message_id, "error": error, "updated_at": store.now()}}

    try:
        saved = store.atomic_documents([key], finish)[key]
    except Exception:
        # Sending may have succeeded. Keep the pre-send receipt; never claim failure
        # and tempt an immediate retry after a local write problem.
        return _result({**receipt, "status": "unknown"})
    return _result(saved)


class PreviewRequest(BaseModel):
    recipient: str = Field(min_length=3, max_length=254)
    confirmed: bool = False
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.get("/admin/api/real-email/status", dependencies=[Depends(_local_request)])
def connection_status(email: str = Depends(auth.web_user)):
    auth.require_role(email, Role.STUDENT_AFFAIRS)
    return mailer.real_email_status()


@router.post("/admin/api/issues/{issue_id}/email-preview", dependencies=[Depends(_local_request)])
def email_preview(issue_id: str, body: PreviewRequest, email: str = Depends(auth.web_user)):
    auth.require_role(email, Role.STUDENT_AFFAIRS)
    if not body.confirmed:
        raise HTTPException(422, "Confirm the recipient before sending a real email.")
    try:
        recipient = mailer.validate_recipient(body.recipient)
    except ValueError:
        raise HTTPException(422, "Enter one email address, without a name or a recipient list.") from None
    return send_preview(issue_id, recipient, body.expected_revision, email)


@router.get("/admin/email-setup", response_class=HTMLResponse, dependencies=[Depends(_local_request)])
def setup_page():
    return HTMLResponse(issue_builder.env.get_template("email_setup.html").render(
        demo_switcher=True, demo_view="affairs"))
