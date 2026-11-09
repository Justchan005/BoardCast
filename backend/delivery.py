"""Durable newsletter delivery with exclusive claims and recipient-level retries.

Gmail and Firestore do not share a transaction. If the process dies after Gmail accepts
an email but before its receipt is recorded, that recipient becomes ``unknown`` on
lease recovery and is NOT sent again automatically. A human must reconcile it with
the sender's Sent mailbox. This is deliberately not an exactly-once guarantee.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from googleapiclient.errors import HttpError

from . import audit, config, issue_builder, mailer, store, tokens

LEASE = timedelta(minutes=10)
RETRYABLE_ISSUES = ("preview", "partial", "sending")


def _receipt_key(issue: dict, email: str) -> tuple[str, str]:
    value = f"{issue['id']}|{issue['delivery_batch']}|{email.lower()}"
    return "deliveries", hashlib.sha256(value.encode()).hexdigest()


def _owns(issue: dict | None, owner: str) -> bool:
    return bool(issue and issue.get("status") == "sending"
                and issue.get("delivery_owner") == owner
                and issue.get("delivery_lease_until", store.now()) > store.now())


def _claim(issue_id: str, owner: str) -> dict | None:
    key = ("issues", issue_id)
    batch = uuid.uuid4().hex

    def claim(rows):
        issue = rows[key]
        if not issue or issue.get("status") not in RETRYABLE_ISSUES:
            return None
        if issue.get("delivery_owner") and issue.get("delivery_lease_until", store.now()) > store.now():
            return None
        fields = {"status": "sending", "delivery_owner": owner,
                  "delivery_lease_until": store.now() + LEASE}
        if issue["status"] == "preview":
            # No email from a previous batch was accepted or had an uncertain outcome.
            # Recheck approval and refresh recipients before another attempt.
            fields.update(delivery_batch=batch, delivery_snapshot=None, delivery_recipients=[],
                          delivery_frozen=False, delivery_summary={})
        return {key: fields}

    changed = store.atomic_documents([key], claim)
    return changed[key] if changed else None


def _prepare(issue: dict, owner: str) -> tuple[dict | None, str | None]:
    if issue.get("delivery_snapshot") is not None:
        return issue, None
    edition, plan = issue_builder.from_saved(issue, for_send=True)
    items = issue_builder.all_items(edition)
    ids = [item["id"] for item in items]
    excluded = [item_id for item_id in issue.get("item_ids", []) if item_id not in ids]
    recipients = sorted({s["email"].lower() for s in store.active_subscribers()}) if items else []
    reason = "no approved items remain" if not items else ("no active subscribers" if not recipients else None)
    snapshot = {"id": issue["id"], "week_start": issue["week_start"], "status": "sent",
                "items": items, "item_ids": ids, "editor_plan": plan.model_dump(),
                "excluded_item_ids": excluded,
                "editor_report": {**(issue.get("editor_report") or {}), "send_check": {
                    "excluded_item_ids": excluded,
                    "plan_refreshed": plan.model_dump() != issue.get("editor_plan")}}}
    key = ("issues", issue["id"])

    def prepare(rows):
        if not _owns(rows[key], owner):
            return None
        if reason:
            return {key: {"status": "preview", "delivery_owner": None,
                          "delivery_lease_until": None, "delivery_excluded_item_ids": excluded}}
        return {key: {"delivery_snapshot": snapshot, "delivery_recipients": recipients,
                      "delivery_started_at": store.now(), "delivery_frozen": True}}

    changed = store.atomic_documents([key], prepare)
    return (changed[key] if changed else None), reason


def _begin(issue: dict, email: str, owner: str, payload: dict, active: bool) -> dict | None:
    issue_key, receipt_key = ("issues", issue["id"]), _receipt_key(issue, email)
    attempt = uuid.uuid4().hex

    def begin(rows):
        current, receipt = rows[issue_key], rows[receipt_key]
        if not _owns(current, owner):
            return None
        if receipt and receipt.get("status") in ("sent", "skipped", "unknown"):
            return {}
        if receipt and receipt.get("status") == "sending":
            # A former worker lost its lease without durably recording Gmail's result.
            return {receipt_key: {"status": "unknown", "error": "Delivery interrupted; check the sender's Sent mailbox before retrying.",
                                  "updated_at": store.now()}}
        fields = {"issue_id": issue["id"], "batch": issue["delivery_batch"], "email": email,
                  "status": "sending" if active else "skipped", "attempt": attempt,
                  "attempts": (receipt or {}).get("attempts", 0) + int(active),
                  "payload": (receipt or {}).get("payload") or payload,
                  "updated_at": store.now(), "error": None}
        return {receipt_key: fields,
                issue_key: {"delivery_lease_until": store.now() + LEASE}}

    changed = store.atomic_documents([issue_key, receipt_key], begin)
    receipt = changed.get(receipt_key) if changed else None
    return receipt if receipt and receipt.get("attempt") == attempt and receipt["status"] == "sending" else None


def _record(issue: dict, email: str, attempt: str, status: str, **details) -> None:
    key = _receipt_key(issue, email)

    def record(rows):
        receipt = rows[key]
        if not receipt or receipt.get("attempt") != attempt or receipt.get("status") not in ("sending", "unknown"):
            return None
        return {key: {"status": status, "updated_at": store.now(), **details}}

    store.atomic_documents([key], record)


def _definitely_rejected(exc: Exception) -> bool:
    # An explicit 4xx response means Gmail rejected this request (including 429).
    # Timeouts, connection errors, 5xx and unexpected failures can follow acceptance.
    return (isinstance(exc, HttpError) and 400 <= int(exc.resp.status) < 500
            and int(exc.resp.status) not in (408, 499))


def _finish(issue: dict, owner: str) -> dict:
    issue_key = ("issues", issue["id"])
    keys = [_receipt_key(issue, email) for email in issue["delivery_recipients"]]

    def finish(rows):
        current = rows[issue_key]
        if not current or current.get("delivery_owner") != owner:
            return None
        summary = {state: 0 for state in ("sent", "failed", "unknown", "skipped", "pending")}
        for key in keys:
            state = (rows[key] or {}).get("status", "pending")
            summary["unknown" if state == "sending" else state] += 1
        remaining = summary["failed"] + summary["pending"]
        if remaining:
            status = "partial" if summary["sent"] or summary["unknown"] else "preview"
        else:
            status = "needs_attention" if summary["unknown"] else "sent"
        snapshot = current["delivery_snapshot"]
        fields = {"status": "held" if current["status"] == "held" else status,
                  "delivery_summary": summary, "sent": summary["sent"],
                  "delivery_owner": None, "delivery_lease_until": None,
                  "delivery_frozen": bool(summary["sent"] or summary["unknown"])}
        if summary["sent"] or summary["unknown"]:
            fields.update({k: snapshot[k] for k in ("items", "item_ids", "editor_plan", "editor_report")})
        return {issue_key: fields}

    changed = store.atomic_documents([issue_key, *keys], finish)
    result = changed[issue_key] if changed else store.get_issue(issue["id"])
    if (result.get("delivery_summary") or {}).get("sent"):
        for item in issue["delivery_snapshot"]["items"]:
            store.publish_if_unchanged(item)
    return result


def send_latest(*, retry_only: bool = False) -> dict:
    candidates = [doc.to_dict() for doc in store.db().collection("issues")
                  .where("status", "in", list(RETRYABLE_ISSUES)).stream()]
    if retry_only:
        # A frequent recovery job must not deliver Sunday's fresh preview early.
        candidates = [issue for issue in candidates if issue["status"] in ("sending", "partial")
                      or (issue.get("delivery_summary") or {}).get("failed")]
    # Finish interrupted/partial editions before beginning a newer preview.
    candidates.sort(key=lambda d: (d["status"] != "preview", d["week_start"]), reverse=True)
    owner = uuid.uuid4().hex
    issue = next((claimed for candidate in candidates
                  if (claimed := _claim(candidate["id"], owner))), None)
    if not issue:
        return {"sent": 0, "reason": "no preview waiting (held, complete, or already being delivered)"}
    issue, reason = _prepare(issue, owner)
    if not issue:
        return {"sent": 0, "reason": "issue held or claimed by another worker"}
    if reason:
        return {"sent": 0, "failed": [], "reason": reason,
                "excluded_item_ids": issue.get("delivery_excluded_item_ids", [])}
    snapshot = issue["delivery_snapshot"]
    edition, plan = issue_builder.from_saved(snapshot)
    sent, failed, unknown = 0, [], []
    for email in issue["delivery_recipients"]:
        unsub = f"{config.PUBLIC_BASE_URL}/unsubscribe?token={tokens.sign(email)}"
        payload = {"subject": plan.subject_line, "unsubscribe_url": unsub,
                   "html": issue_builder.render_email(edition, plan, {"email": email},
                           unsubscribe_url=unsub, issue_id=issue["id"],
                           prefs_url=f"{config.PUBLIC_BASE_URL}/preferences?token={tokens.sign(email)}")}
        active = bool((store.get_subscriber(email) or {}).get("active"))
        receipt = _begin(issue, email, owner, payload, active)
        if receipt is None:
            continue
        payload = receipt["payload"]
        try:
            message_id = mailer.send(email, payload["subject"], payload["html"], payload["unsubscribe_url"])
        except Exception as exc:
            status = "failed" if _definitely_rejected(exc) else "unknown"
            _record(issue, email, receipt["attempt"], status, error=str(exc))
            (failed if status == "failed" else unknown).append({"email": email, "error": str(exc)})
        else:
            # A crash or DB failure here leaves 'sending'; recovery never blindly re-sends it.
            _record(issue, email, receipt["attempt"], "sent", message_id=message_id, error=None)
            sent += 1
    result = _finish(issue, owner)
    summary = result.get("delivery_summary", {})
    audit.log("scheduler", "issue_delivery", None,
              {"issue_id": issue["id"], "sent_this_run": sent, "summary": summary})
    response = {"sent": sent, "failed": failed, "unknown": unknown,
                "excluded_item_ids": snapshot["excluded_item_ids"],
                "issue_id": issue["id"], "status": result["status"], "delivery_summary": summary}
    if summary.get("unknown"):
        response["warning"] = "Some deliveries have an unknown outcome. Check the sender's Sent mailbox; they will not be retried automatically."
    return response
