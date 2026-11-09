"""Shared review decisions and rejection emails for the web queue and chat agent."""
from __future__ import annotations

import uuid

from fastapi import HTTPException

from shared.schemas import Role

from . import audit, auth, config, checks, issue_builder, mailer, store

REJECTION_FIELDS = {
    "rejection_reason": None, "rejected_by": None, "rejected_at": None, "rejection_notification": None,
}


def _result(item: dict, notice: dict) -> dict:
    result = {"id": item["id"], "status": "rejected", "rejection_reason": item["rejection_reason"],
              "notification_status": notice["status"]}
    if notice["status"] == "failed":
        result["warning"] = ("The submission was rejected, but its email could not be sent. "
                             "The reason is saved in the club report, where Student Affairs can retry the email.")
    return result


def _notify_rejection(item: dict) -> dict:
    notice = dict(item["rejection_notification"])
    recipient = (item.get("submitted_by") or "").strip().lower()
    try:
        if not recipient:
            raise ValueError("The submission has no uploader email address.")
        title = str(item.get("data", {}).get("title") or "Your submission")
        html = issue_builder.env.get_template("rejection_email.html").render(
            title=title, item_id=item["id"], reason=item["rejection_reason"],
            report_url=f"{config.PUBLIC_BASE_URL.rstrip('/')}/admin/club",
        )
        subject = f"BoardCast submission rejected: {' '.join(title.split())}"[:160]
        message_id = mailer.send(recipient, subject, html, unsubscribe_url="")
        notice.update(status="sent", sent_at=store.now(), message_id=message_id)
        audit.log("system", "rejection_email_sent", item["id"], {"to": recipient, "notice_id": notice["id"]})
    except Exception as exc:
        notice.update(status="failed")
        audit.log("system", "rejection_email_failed", item["id"],
                  {"to": recipient, "notice_id": notice["id"], "error": str(exc)})

    # Sending can take time. Do not restore old rejection details after a resubmission or new decision.
    current = store.get_item(item["id"])
    if current and current.get("status") == "rejected" \
            and (current.get("rejection_notification") or {}).get("id") == notice["id"]:
        store.update_item_if_unchanged(current, rejection_notification=notice)
    return _result(item, notice)


def decide_item(item_id: str, decision: str, note: str | None, reviewer: str, *, via: str,
                retry_notification: bool = False, expected_updated_at: str | None = None) -> dict:
    auth.require_role(reviewer, Role.STUDENT_AFFAIRS)
    item = store.get_item(item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    if expected_updated_at:
        from datetime import datetime
        try:
            expected = datetime.fromisoformat(expected_updated_at.replace('Z', '+00:00'))
        except ValueError:
            raise HTTPException(422, 'Invalid review version. Refresh the submission.')
        if item.get('updated_at') != expected:
            raise HTTPException(409, 'This submission changed since you opened it. Refresh and review the new version.')
    reason = (note or "").strip()
    if retry_notification and (decision != "reject" or item["status"] != "rejected"
                               or item.get("rejection_reason") != reason):
        raise HTTPException(409, "This rejection has changed. Refresh the club report before retrying its email.")
    if decision == "reject" and not reason:
        raise HTTPException(422, "Please give a rejection reason so the uploader knows what to correct.")
    if len(reason) > 2000:
        raise HTTPException(422, "Keep the review note within 2,000 characters.")

    if decision == "approve":
        if item["status"] != "pending":
            raise HTTPException(409, "Only pending items with no open problems can be approved.")
        problems = checks.run_all(item['type'], item['data']) + checks.description_problems(item.get('description'), item['data'])
        if problems:
            raise HTTPException(409, 'This submission needs corrections before approval: ' + '; '.join(problems))
        fields = {**REJECTION_FIELDS, "status": "approved", "approved_by": reviewer.lower()}
    else:
        same_rejection = item["status"] == "rejected" and item.get("rejection_reason") == reason
        previous = item.get("rejection_notification") or {}
        if same_rejection and previous.get("status") in ("sent", "sending"):
            return _result(item, previous)  # repeating a click/request does not send another email
        notice = {"id": uuid.uuid4().hex, "status": "sending", "started_at": store.now()}
        fields = {"status": "rejected", "approved_by": None, "rejection_reason": reason,
                  "rejected_by": item.get("rejected_by") if same_rejection else reviewer.lower(),
                  "rejected_at": item.get("rejected_at") if same_rejection else store.now(),
                  "rejection_notification": notice}

    if not store.update_item_if_unchanged(item, **fields):
        raise HTTPException(409, "This submission changed while you were reviewing it. Refresh and review it again.")
    audit.log(reviewer, decision, item_id, {"note": reason or None, "via": via})
    if decision == "reject":
        return _notify_rejection({**item, **fields})
    return {"id": item_id, "status": "approved"}
