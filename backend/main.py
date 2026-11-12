"""BoardCast API (Cloud Run).

Protected routes: called only by the ADK agent / Cloud Scheduler (ID token checked),
and every action re-checks the end user's role.
Public routes: calendar files, tracked links, subscribe/unsubscribe.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import mimetypes
from datetime import date, timedelta
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from google.cloud import pubsub_v1, storage
from PIL import Image
from pydantic import BaseModel, ValidationError

from shared.schemas import ItemStatus, ItemType, Role

from pathlib import Path

from . import audit, auth, checks, config, delivery, email_preview, extract, insights, issue_builder, mailer, prereview, reminders, reviews, runlog, store, tokens, trace, web, submission
from .ics import build_ics
from .qr_rules import apply_qr_links

app = FastAPI(title="BoardCast API")
app.include_router(web.router)
app.include_router(submission.router)
app.include_router(runlog.router)
app.include_router(insights.router)
app.include_router(email_preview.router)
# Windows registry associations can label .js as text/plain. Serve scripts with
# their actual MIME type so browsers can enforce X-Content-Type-Options safely.
mimetypes.init()
mimetypes.add_type("text/javascript", ".js")
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent.parent / "static"), name="static")
from .hosted_demo import install as install_hosted_demo
install_hosted_demo(app)
_publisher = None


def publish(topic: str, payload: dict) -> None:
    global _publisher
    if config.LOCAL_MODE:
        audit.log("system", "local_event", payload.get("item_id"), {"topic": topic, **payload})
        return
    try:
        _publisher = _publisher or pubsub_v1.PublisherClient()
        _publisher.publish(_publisher.topic_path(config.PROJECT_ID, topic), json.dumps(payload).encode())
    except Exception as e:  # submission still succeeds; the event is retried by the reviewer queue
        audit.log("system", "pubsub_failed", payload.get("item_id"), {"error": str(e)})


# ---------------------------------------------------------------- request bodies

class ExtractReq(BaseModel):
    user_email: str
    type: ItemType
    gcs_uri: str                      # gs://bucket/path.jpg uploaded by the agent (local:posters/x.jpg in LOCAL_MODE)


class SubmitReq(BaseModel):
    user_email: str
    type: ItemType
    data: dict = {}
    poster_uri: Optional[str] = None
    description: Optional[str] = None     # the club's own 1-2 sentences for students (optional)
    show_poster: Optional[bool] = None    # show the original poster in the newsletter


class DecisionReq(BaseModel):
    user_email: str
    decision: Literal["approve", "reject"]
    note: Optional[str] = None
    acknowledge_flags: bool = False       # the reviewer confirmed the AI brief's red flags in chat


# ---------------------------------------------------------------- protected: agent

def _load_poster(uri: str) -> tuple[bytes, str]:
    """gs://bucket/path on Google Cloud; local:posters/<name> in LOCAL_MODE (only inside LOCAL_DATA_DIR)."""
    if uri.startswith("local:"):
        if not config.LOCAL_MODE:
            raise HTTPException(400, "Local posters are only allowed in LOCAL_MODE")
        root = (config.LOCAL_DATA_DIR / "posters").resolve()
        path = (config.LOCAL_DATA_DIR / uri.removeprefix("local:")).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "Poster not found")
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        image = path.read_bytes()
    else:
        bucket, _, path = uri.removeprefix("gs://").partition("/")
        blob = storage.Client().bucket(bucket).blob(path)
        image = blob.download_as_bytes()
        mime = blob.content_type or "image/jpeg"
    if not mime.lower().startswith("image/") or image.lstrip().startswith(b"%PDF"):
        raise HTTPException(415, "Please upload a poster image, such as PNG or JPG. "
                                 "PDFs and other documents aren't supported.")
    return image, mime


# The poster image is shown to students as-is, so it must not contradict the approved details.
POSTER_FACTS = ("date", "start_time", "end_time", "venue")


def _poster_key(uri: str) -> str:
    return hashlib.sha256(uri.encode()).hexdigest()[:32]


def _fact(field: str, value) -> str:
    s = str(value or "").strip().lower()
    return s[:5] if field in ("start_time", "end_time") else re.sub(r"\s+", " ", s)


def _remember_poster_facts(uri: str, data: dict) -> None:
    """Keep what the poster itself says (before any correction in chat)."""
    store.db().collection("poster_reads").document(_poster_key(uri)).set(
        {"uri": uri, "facts": {f: data.get(f) for f in POSTER_FACTS}})


def poster_conflicts(uri: str | None, data: dict) -> list[str]:
    """Fields where the approved details differ from what the poster shows. A field the poster
    left blank is not a conflict (the club only added information)."""
    if not uri:
        return []
    snap = store.db().collection("poster_reads").document(_poster_key(uri)).get()
    if not snap.exists:
        return []
    facts = (snap.to_dict() or {}).get("facts", {})
    return [f for f in POSTER_FACTS if facts.get(f) and _fact(f, facts[f]) != _fact(f, data.get(f))]


def _poster_choice(wanted: bool, uri: str | None, data: dict) -> tuple[bool, list[str]]:
    conflicts = poster_conflicts(uri, data) if wanted and uri else []
    return bool(wanted and uri and not conflicts), conflicts


NOT_A_POSTER = ("This image doesn't look like a campus poster, so nothing was filled in. "
                "Upload the poster image, or enter the details manually.")


@app.post("/items/extract")
def extract_item(req: ExtractReq, _=Depends(auth.agent_caller)):
    auth.require_role(req.user_email, Role.CLUB_REP, Role.STUDENT_AFFAIRS)
    image, mime_type = _load_poster(req.gcs_uri)
    keys = {"poster_uri": req.gcs_uri}
    with trace.capture() as model_calls:
        try:
            result = extract.extract(req.type, image=image, mime_type=mime_type)
        except ValueError as e:
            raise HTTPException(422, str(e))
        finally:   # the Gemini call is traced whether it succeeded, failed validation or timed out
            trace.flush(model_calls, actor="gemini", **keys)
    data = result.model_dump(mode="json")
    if str(data.get("title") or "").strip().upper() == "UNREADABLE":   # prompts/extract.md: not a poster
        trace.record("code", "not_a_poster", "The model reported that the image is not a poster; nothing was filled in",
                     ok=False, actor="code", detail={"requested_by": req.user_email.lower()}, **keys)
        raise HTTPException(422, NOT_A_POSTER)
    from . import qr  # needs the zbar system library; imported here so the rest runs without it
    links = qr.decode_links(image)
    apply_qr_links(req.type.value, data, links)  # QR beats model guess (backend/qr_rules.py)
    _remember_poster_facts(req.gcs_uri, data)
    missing = checks.missing_fields(req.type.value, data)
    trace.record("code", "qr_decode", f"{len(links)} QR link{'' if len(links) == 1 else 's'} decoded in code; the model never reads QR codes",
                 actor="code", detail={"links": links}, **keys)
    trace.record("code", "required_fields",
                 "Every required field was found on the poster" if not missing
                 else "The club is asked to fill in: " + ", ".join(missing),
                 actor="code", detail={"missing": missing, "requested_by": req.user_email.lower()}, **keys)
    return {"data": data, "qr_links": links, "missing": missing}


@app.post("/items")
def submit_item(req: SubmitReq, _=Depends(auth.agent_caller)):
    role = auth.require_role(req.user_email, Role.CLUB_REP, Role.STUDENT_AFFAIRS)
    schema = extract.SCHEMAS[req.type]
    try:
        data = schema.model_validate(req.data).model_dump(mode="json")
    except ValidationError as e:
        raise HTTPException(422, f"Invalid {req.type.value}: {e.errors()}")

    description = (req.description or "").strip() or None
    issues = checks.run_all(req.type.value, data) + checks.description_problems(description, data)
    show_poster, poster_off = _poster_choice(bool(req.show_poster), req.poster_uri, data)

    # Achievements were already approved through APU's own process: fast lane.
    fast_lane = req.type == ItemType.ACHIEVEMENT and role["role"] == Role.STUDENT_AFFAIRS and not issues
    status = ItemStatus.APPROVED if fast_lane else (ItemStatus.PENDING if not issues else ItemStatus.DRAFT)

    item_id = store.create_item({
        "type": req.type.value, "status": status.value, "club_id": role.get("club_id"),
        "submitted_by": req.user_email.lower(), "data": data,
        "poster_uri": req.poster_uri, "issues": issues, "description": description,
        "poster_wanted": bool(req.show_poster), "show_poster": show_poster,
    })
    audit.log(req.user_email, "submit", item_id, {"status": status.value, "issues": issues})
    _trace_submission("submitted", item_id, req.user_email, status, issues, poster_off, bool(req.poster_uri),
                      fast_lane=fast_lane)
    if status == ItemStatus.PENDING:
        publish(config.PUBSUB_TOPIC_SUBMITTED, {"item_id": item_id})
        prereview.schedule(item_id, actor=req.user_email)
    return {"id": item_id, "status": status.value, "issues": issues, **_poster_note(poster_off)}


def _trace_submission(action: str, item_id: str, email: str, status: ItemStatus, issues: list[str],
                      poster_off: list[str], has_poster: bool, *, fast_lane: bool = False) -> None:
    trace.record("human", action, "The club confirmed the details and sent them for review" if action == "submitted"
                 else "The details were corrected and sent again", item_id=item_id, actor=email,
                 detail={"has_poster": has_poster})
    if status == ItemStatus.PENDING:
        outcome = "No problems found; queued for Student Affairs"
    elif status == ItemStatus.APPROVED:
        outcome = "Achievement from Student Affairs, already approved through APU's own process: fast lane"
    else:
        outcome = f"{len(issues)} problem(s) found; kept as a draft for the club to fix"
    trace.record("code", "submission_checks", outcome, item_id=item_id, actor="code", ok=not issues,
                 detail={"status": status.value, "problems": issues, "poster_hidden_because_it_differs_in": poster_off,
                         "fast_lane": fast_lane,
                         "checks": ["required fields", "date not in the past", "https and allowed link domains",
                                    "description adds no new numbers", "consent for achievements",
                                    "poster agrees with the confirmed details"]})


def _poster_note(conflicts: list[str]) -> dict:
    """Not a blocking problem: the item still goes ahead, just without the poster image."""
    if not conflicts:
        return {}
    return {"poster_hidden": f"The poster won't be shown: it still has the old {', '.join(conflicts)}. "
                             "Attach an updated poster to show it."}


@app.patch("/items/{item_id}")
def edit_item(item_id: str, req: SubmitReq, _=Depends(auth.agent_caller)):
    role = auth.require_role(req.user_email, Role.CLUB_REP, Role.STUDENT_AFFAIRS)
    item = store.get_item(item_id) or {}
    if not item:
        raise HTTPException(404, "Item not found")
    if role["role"] == Role.CLUB_REP and item.get("club_id") != role.get("club_id"):
        raise HTTPException(403, "You can only edit your own club's items.")
    try:
        data = extract.SCHEMAS[ItemType(item["type"])].model_validate({**item["data"], **req.data}).model_dump(mode="json")
    except ValidationError as e:
        raise HTTPException(422, f"Invalid {item['type']}: {e.errors()}")
    description = item.get("description") if req.description is None else (req.description.strip() or None)
    poster_uri = req.poster_uri or item.get("poster_uri")       # a fix may attach an updated poster
    if req.show_poster is not None:
        wanted = bool(req.show_poster)                          # the rep changed their choice
    else:
        wanted = bool(item.get("poster_wanted")) or bool(req.poster_uri)   # new poster = they want it shown
    show_poster, poster_off = _poster_choice(wanted, poster_uri, data)
    issues = checks.run_all(item["type"], data) + checks.description_problems(description, data)
    status = ItemStatus.PENDING if not issues else ItemStatus.DRAFT
    store.update_item(item_id, data=data, issues=issues, status=status.value, description=description,
                      poster_uri=poster_uri, poster_wanted=wanted, show_poster=show_poster, approved_by=None,
                      **reviews.REJECTION_FIELDS)
    audit.log(req.user_email, "edit", item_id, {"issues": issues})
    _trace_submission("corrected", item_id, req.user_email, status, issues, poster_off, bool(poster_uri))
    if status == ItemStatus.PENDING:
        prereview.schedule(item_id, actor=req.user_email)
    return {"id": item_id, "status": status.value, "issues": issues, **_poster_note(poster_off)}


@app.get("/items")
def list_queue(user_email: str, status: str = "pending", _=Depends(auth.agent_caller)):
    role = auth.require_role(user_email, Role.CLUB_REP, Role.STUDENT_AFFAIRS)
    items = store.list_items([status])
    if role["role"] == Role.CLUB_REP:
        items = [i for i in items if i.get("club_id") == role.get("club_id")]
    return {"items": items}


@app.post("/items/{item_id}/decision")
def decide(item_id: str, req: DecisionReq, _=Depends(auth.agent_caller)):
    return reviews.decide_item(item_id, req.decision, req.note, req.user_email, via="agent",
                               acknowledge_flags=req.acknowledge_flags)


# ---------------------------------------------------------------- protected: scheduler

def _next_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


@app.post("/issues/preview")
def issue_preview(_=Depends(auth.scheduler_caller)):
    """Sunday evening: build next week's issue and send the preview to Student Affairs."""
    today = date.today()
    issue_id, report = issue_builder.build_preview(_next_monday(today))
    audit.log("scheduler", "preview_built", None, {"issue_id": issue_id, **report})
    for sa in store.db().collection("roles").where("role", "==", "student_affairs").stream():
        try:
            mailer.send(sa.id, "Campus Weekly preview: review before Monday 8am",
                        f"<p>Next week's issue is ready.</p><p><a href='{config.PUBLIC_BASE_URL}/m/{issue_id}'>Read the preview</a>"
                        f" · <a href='{config.PUBLIC_BASE_URL}/admin'>Approve items or hold the issue</a></p>"
                        "<p>If you do nothing, it sends Monday at 8am.</p>", unsubscribe_url="")
        except Exception as e:
            audit.log("system", "preview_notify_failed", None, {"to": sa.id, "error": str(e)})
    return {"issue_id": issue_id, "report": report}


class HoldReq(BaseModel):
    user_email: str


@app.post("/issues/{issue_id}/hold")
def hold_issue(issue_id: str, req: HoldReq, _=Depends(auth.agent_caller)):
    auth.require_role(req.user_email, Role.STUDENT_AFFAIRS)
    store.hold_issue(issue_id)
    audit.log(req.user_email, "hold_issue", None, {"issue_id": issue_id})
    return {"issue_id": issue_id, "status": "held",
            "note": "New deliveries are stopped. An email already being sent cannot be recalled."}


@app.post("/issues/send")
def issue_send(_=Depends(auth.scheduler_caller)):
    """Claim an edition, then deliver/retry recipients without repeating recorded successes."""
    return delivery.send_latest()


@app.post("/issues/retry")
def issue_retry(_=Depends(auth.scheduler_caller)):
    """Recover interrupted deliveries; never start a fresh preview before its weekly send."""
    return delivery.send_latest(retry_only=True)


@app.post("/reminders/run")
def reminders_run(_=Depends(auth.scheduler_caller)):
    """Hourly: email students about saved events starting in the next ~hour."""
    return reminders.run()


# ---------------------------------------------------------------- public

def _remember(s: Optional[str], item_id: str, via: str) -> None:
    """Links in a student's own email carry ?s=<signed token>; clicking one saves the event
    for a 1-hour reminder. Links without a valid token (web magazine, forwarded) still work."""
    email = tokens.verify(s, "save") if s else None
    if email and store.save_event(email, item_id, via):
        audit.log(email, "saved_event", item_id, {"via": via})


@app.get("/cal/{item_id}.ics")
def calendar_file(item_id: str, s: Optional[str] = None):
    item = store.get_item(item_id)
    if not item or item["type"] != "event" or item["status"] not in ("approved", "published"):
        raise HTTPException(404, "Event not found")
    store.log_click(item_id, "calendar")
    _remember(s, item_id, "calendar")
    return Response(build_ics(item), media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="{item_id}.ics"'})


@app.get("/r/{item_id}")
def tracked_register(item_id: str, s: Optional[str] = None):
    """Redirects only to the link stored on an approved item (no open redirect)."""
    item = store.get_item(item_id)
    data = (item or {}).get("data", {})
    link = (data.get("registration") or {}).get("link") or data.get("action_link")   # event or announcement
    if not item or item["status"] not in ("approved", "published") or not link:
        raise HTTPException(404, "Link not found")
    if checks.link_problem(link):
        raise HTTPException(404, "Link is not available")
    store.log_click(item_id, "register")
    _remember(s, item_id, "register")
    return RedirectResponse(link, status_code=302)


POSTER_WIDTH = 600   # email column width; about 100-150 KB as JPEG


def staff_poster_url(item_id: str) -> str:
    """Signed link for the /admin review queue: staff see a poster before it is approved.
    Only handed out by /admin/api/* after the Google sign-in and role check."""
    return f"/p/{item_id}.jpg?t={tokens.sign(item_id, 'poster')}"


@app.get("/p/{item_id}.jpg")
def poster_image(item_id: str, t: Optional[str] = None):
    """The original poster for the newsletter: only approved items whose club chose to show
    it, or any item's poster with a staff link (t). Resized + re-encoded, which also strips
    EXIF data (e.g. phone location)."""
    item = store.get_item(item_id)
    staff = bool(t) and tokens.verify(t, "poster") == item_id
    public = bool(item) and item["status"] in ("approved", "published") and item.get("show_poster")
    if not item or not item.get("poster_uri") or not (public or staff):
        raise HTTPException(404, "Poster not found")
    raw, _ = _load_poster(item["poster_uri"])
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if img.width > POSTER_WIDTH:
        img = img.resize((POSTER_WIDTH, round(img.height * POSTER_WIDTH / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True)
    cache = "public, max-age=3600" if public else "private, no-store"   # unapproved posters are never cached
    return Response(out.getvalue(), media_type="image/jpeg", headers={"Cache-Control": cache})


@app.get("/healthz")
def health():
    return {"ok": True}
