"""Web UI: student pages (subscribe, preferences, magazine) and the /admin review queue.

Students never sign in: they prove their APU email with a 6-digit code, and every link
in their emails carries a signed token. Staff sign in with Google on /admin; the backend
verifies the Google ID token and checks their role in Firestore.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from shared.schemas import ItemStatus, Role

from . import audit, auth, config, issue_builder, mailer, reviews, store, subscriptions, tokens

router = APIRouter()
log = logging.getLogger("boardcast.web")


def page(name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(issue_builder.env.get_template(name).render(base=config.PUBLIC_BASE_URL, **ctx))


def _send_or_log(to: str, subject: str, html: str) -> None:
    try:
        mailer.send(to, subject, html, unsubscribe_url="")
    except Exception as e:
        if not config.DEV_MODE:
            raise
        log.warning("DEV_MODE: email to %s not sent (%s). Content:\n%s", to, e, html)


# ================================================================ students

@router.get("/subscribe", response_class=HTMLResponse)
def subscribe_form(error: str = ""):
    return page("subscribe.html", error=error)


@router.post("/subscribe")
def subscribe_submit(email: str = Form(...)):
    """One edition for everyone, so the only thing we need is a verified APU email."""
    email = email.strip().lower()
    if not subscriptions.valid_email(email):
        return RedirectResponse("/subscribe?error=Please+use+your+APU+email", status_code=303)
    try:
        code = subscriptions.request_code(email)
    except subscriptions.ChallengeThrottled as e:
        response = page("subscribe.html", error=str(e))
        response.status_code = 429
        response.headers["Retry-After"] = str(e.retry_after)
        return response
    _send_or_log(email, f"Your BoardCast code: {code}",
                 f"<p>Your BoardCast verification code is <b style='font-size:20px'>{code}</b>.</p>"
                 "<p>Codes expire 15 minutes after the first request. A resend replaces the previous code. "
                 "BoardCast never asks for your password.</p>")
    return RedirectResponse(f"/subscribe/verify?t={tokens.sign(email, 'verify')}", status_code=303)


@router.get("/subscribe/verify", response_class=HTMLResponse)
def verify_form(t: str, error: str = ""):
    email = tokens.verify(t, "verify") or ""
    if not email:
        raise HTTPException(400, "Link not valid")
    return page("verify.html", email=email, token=t, error=error)


@router.post("/subscribe/verify")
def verify_submit(t: str = Form(...), code: str = Form(...)):
    email = tokens.verify(t, "verify")
    if not email or not subscriptions.verify_code(email, code):
        query = urlencode({"t": t, "error": "Wrong, expired, or locked code. Use the latest code. "
                           "After five incorrect attempts, wait until it expires before requesting another."})
        return RedirectResponse(f"/subscribe/verify?{query}", status_code=303)
    audit.log(email, "subscribed")
    return page("message.html", title="You're subscribed",
                body="Your first Campus Weekly arrives on Monday at 8am.",
                link=f"/preferences?token={tokens.sign(email)}", link_text="Manage your subscription")


@router.get("/preferences", response_class=HTMLResponse)
def prefs_form(token: str):
    """Manage subscription: shows the address and the unsubscribe button (signed link)."""
    email = tokens.verify(token)
    if not email:
        raise HTTPException(400, "Link not valid")
    sub = store.get_subscriber(email) or {"email": email, "active": False}
    return page("preferences.html", sub=sub, token=token)


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(token: str):
    email = tokens.verify(token)
    if not email:
        raise HTTPException(400, "Link not valid")
    store.upsert_subscriber({"email": email, "active": False})
    audit.log(email, "unsubscribed")
    return page("message.html", title="You're unsubscribed",
                body="You won't get Campus Weekly any more.",
                link="/subscribe", link_text="Subscribe again")


@router.get("/m/{issue_id}", response_class=HTMLResponse)
def magazine(issue_id: str):
    doc = store.get_issue(issue_id)
    if not doc:
        raise HTTPException(404, "Issue not found")
    issue, plan = issue_builder.from_saved(doc)
    return HTMLResponse(issue_builder.render_magazine(issue, plan, issue_id))


# ================================================================ staff (/admin)

@router.get("/admin", response_class=HTMLResponse)
def admin_page():
    return page("admin.html", client_id=config.OAUTH_CLIENT_ID, dev_mode=config.DEV_MODE, local_mode=config.LOCAL_MODE)


@router.get("/admin/api/me")
def me(email: str = Depends(auth.web_user)):
    role = auth.require_role(email, Role.CLUB_REP, Role.STUDENT_AFFAIRS)
    return {"email": email, **role}


@router.get("/admin/api/queue")
def queue(email: str = Depends(auth.web_user)):
    role = auth.require_role(email, Role.CLUB_REP, Role.STUDENT_AFFAIRS)
    items = store.list_items(["pending", "draft", "approved"])
    if role["role"] == Role.CLUB_REP:
        items = [i for i in items if i.get("club_id") == role.get("club_id")]
    items.sort(key=lambda i: str(i.get("created_at")), reverse=True)
    from .main import staff_poster_url  # main imports this module, so import at call time
    for i in items:
        i["poster_url"] = staff_poster_url(i["id"]) if i.get("poster_uri") else None
    return {"items": items}


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    note: Optional[str] = None
    retry_notification: bool = False
    expected_updated_at: str | None = None


@router.post("/admin/api/items/{item_id}/decision")
def decide(item_id: str, body: Decision, email: str = Depends(auth.web_user)):
    return reviews.decide_item(item_id, body.decision, body.note, email, via="web",
                               retry_notification=body.retry_notification, expected_updated_at=body.expected_updated_at)


@router.get("/admin/api/preview")
def latest_preview(email: str = Depends(auth.web_user)):
    auth.require_role(email, Role.STUDENT_AFFAIRS)
    snaps = store.db().collection("issues").order_by("week_start", direction="DESCENDING").limit(50).stream()
    doc = max((s.to_dict() for s in snaps), key=lambda d: (str(d.get('week_start', '')), str(d.get('created_at', ''))), default=None)
    if not doc:
        return {"issue": None}
    return {"issue": {k: doc.get(k) for k in ("id", "week_start", "status", "editor_plan", "editor_report", "delivery_summary")},
            "magazine_url": f"/m/{doc['id']}"}


@router.post("/admin/api/issues/{issue_id}/hold")
def hold(issue_id: str, email: str = Depends(auth.web_user)):
    auth.require_role(email, Role.STUDENT_AFFAIRS)
    store.hold_issue(issue_id)
    audit.log(email, "hold_issue", None, {"issue_id": issue_id, "via": "web"})
    return {"issue_id": issue_id, "status": "held"}


@router.get("/admin/api/clicks")
def clicks(email: str = Depends(auth.web_user)):
    """Register + calendar clicks per item (club reps see their own club only)."""
    role = auth.require_role(email, Role.CLUB_REP, Role.STUDENT_AFFAIRS)
    items = {i["id"]: i for i in store.list_items(["approved", "published"])}
    if role["role"] == Role.CLUB_REP:
        items = {k: v for k, v in items.items() if v.get("club_id") == role.get("club_id")}
    counts = _click_counts(set(items))
    return {"rows": [{"id": k, "title": items[k]["data"].get("title"), **v} for k, v in counts.items()]}


def _click_counts(item_ids: set[str]) -> dict[str, dict[str, int]]:
    """Register / calendar clicks, and students who saved the event (get a reminder), per item."""
    counts: dict[str, dict[str, int]] = {}
    blank = lambda: {"register": 0, "calendar": 0, "saved": 0}  # noqa: E731
    for c in store.db().collection("clicks").stream():
        c = c.to_dict()
        if c["item_id"] in item_ids and c.get("kind") in ("register", "calendar"):
            counts.setdefault(c["item_id"], blank())[c["kind"]] += 1
    for s in store.db().collection("saved").stream():
        s = s.to_dict()
        if s["item_id"] in item_ids:
            counts.setdefault(s["item_id"], blank())["saved"] += 1
    return counts


@router.get("/admin/club", response_class=HTMLResponse)
def club_page():
    return page("club.html", client_id=config.OAUTH_CLIENT_ID, dev_mode=config.DEV_MODE)


@router.get("/admin/api/club")
def club_report(email: str = Depends(auth.web_user)):
    """Club report: every item of the rep's club (Student Affairs: all clubs) with its status,
    open problems and click counts. Counts are plain code, never AI."""
    role = auth.require_role(email, Role.CLUB_REP, Role.STUDENT_AFFAIRS)
    items = store.list_items([s.value for s in ItemStatus])
    if role["role"] == Role.CLUB_REP:
        items = [i for i in items if i.get("club_id") == role.get("club_id")]
    counts = _click_counts({i["id"] for i in items})
    zero = {"register": 0, "calendar": 0, "saved": 0}
    rows = [{"id": i["id"], "type": i["type"], "status": i["status"], "club_id": i.get("club_id"),
             "title": i["data"].get("title"), "date": i["data"].get("date"),
             "issues": i.get("issues", []), "rejection_reason": i.get("rejection_reason"),
             "rejection_notification_status": (i.get("rejection_notification") or {}).get("status"),
             **counts.get(i["id"], zero)} for i in items]
    rows.sort(key=lambda r: (str(r["date"] or ""), r["title"] or ""), reverse=True)
    totals = {k: sum(r[k] for r in rows) for k in zero}
    return {"club_id": role.get("club_id"), "role": role["role"], "rows": rows, "totals": totals}
