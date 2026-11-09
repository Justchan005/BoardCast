"""1-hour event reminders (the only email besides the Monday issue).

A student "saves" an event by clicking Register or Add to calendar in their own weekly
email (those links carry a signed token, see main.py). Cloud Scheduler calls
POST /reminders/run every hour; each saved event starting in the next WINDOW minutes gets
one reminder, then is marked reminded so it is never sent twice. All plain code, no AI.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import audit, config, issue_builder, mailer, store, tokens
from .checks import _as_date, _as_time

# Hourly job + 90-minute window: every event is reminded once, 30–90 minutes before it starts.
WINDOW = timedelta(minutes=90)


def event_start(item: dict) -> datetime | None:
    e = item.get("data") or {}
    d, t = _as_date(e.get("date")), _as_time(e.get("start_time"))
    return datetime.combine(d, t, tzinfo=ZoneInfo(config.TIMEZONE)) if d and t else None


def is_due(item: dict | None, now: datetime) -> bool:
    if not item or item.get("type") != "event" or item.get("status") not in ("approved", "published"):
        return False
    start = event_start(item)
    return bool(start and now < start <= now + WINDOW)


def run(now: datetime | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config.TIMEZONE))
    sent, skipped, failed = 0, 0, []
    items: dict[str, dict | None] = {}
    for save in store.unreminded_saves():
        item_id = save["item_id"]
        if item_id not in items:
            items[item_id] = store.get_item(item_id)
        item = items[item_id]
        if not is_due(item, now):
            continue
        sub = store.get_subscriber(save["email"])
        if not sub or not sub.get("active"):
            store.mark_reminded(save["id"])  # unsubscribed: never remind
            skipped += 1
            continue
        unsub = f"{config.PUBLIC_BASE_URL}/unsubscribe?token={tokens.sign(save['email'])}"
        html = issue_builder.env.get_template("reminder_email.html").render(
            e=item["data"], item=item, base=config.PUBLIC_BASE_URL, unsubscribe_url=unsub)
        try:
            mailer.send(save["email"], f"Starting soon: {item['data']['title']}"[:80], html, unsub)
            store.mark_reminded(save["id"])
            sent += 1
        except Exception as e:  # one bad address must not stop the rest; retried next hour
            failed.append({"email": save["email"], "error": str(e)})
    audit.log("scheduler", "reminders_run", None, {"sent": sent, "skipped": skipped, "failed": len(failed)})
    return {"sent": sent, "skipped": skipped, "failed": failed}
