"""Assemble the Monday issue from APPROVED items only: one edition for every student.

Campus Weekly reads like a newspaper of the clubs' own submissions:
1. collect()      approved items for the week (code)
2. editor.plan    spotlight, headline, teaser, subject (Gemini, validated)
3. render_*()     email and web magazine in fixed numbered sections (templates):
                  week at a glance, events (the club's words + poster), campus wins, opportunities
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from shared.schemas import EditorPlan

from . import config, store, tokens
from .checks import _as_date, _as_time, description_urls, instagram_link_problem, link_problem

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))


def _hm(value) -> str:
    """'16:00' -> '4:00pm'."""
    t = _as_time(value) if value else None
    if not t:
        return ""
    return f"{(t.hour % 12) or 12}:{t.minute:02d}{'am' if t.hour < 12 else 'pm'}"


def _nice_date(value) -> str:
    d = _as_date(value) if value else None
    return f"{d:%a} {d.day} {d:%b}" if d else ""  # no %-d: it crashes on Windows


def _is_free(fee) -> bool:
    return (fee or "").strip().lower() == "free"


def _linkify_description(value: str | None) -> Markup:
    """Keep the club's words escaped; only turn approved HTTPS URLs into direct links."""
    text = str(value or "")
    parts, cursor = [], 0
    for start, end, url in description_urls(text):
        parts.append(escape(text[cursor:start]))
        if link_problem(url) is None:
            parts.append(Markup('<a href="{}" rel="noopener noreferrer" '
                                'style="color:#0B7BB5;font-weight:bold;">{}</a>').format(url, url))
        else:
            parts.append(escape(url))
        cursor = end
    parts.append(escape(text[cursor:]))
    return Markup("").join(parts)


env.filters.update(hm=_hm, nice_date=_nice_date, linkify_description=_linkify_description)
env.tests["free"] = _is_free
env.tests["safe_instagram"] = lambda url: bool(url) and instagram_link_problem(url) is None

def week_range(monday: date) -> tuple[date, date]:
    return monday, monday + timedelta(days=6)


def collect(monday: date, statuses: tuple[str, ...] = ("approved",)) -> dict:
    """Everything approved since the last issue, like a newspaper printing what was submitted:
    events on any upcoming date (next week or a month away), in date order. Sending an issue marks
    its items published, so each item appears once. Past events and closed opportunities are skipped."""
    start, end = week_range(monday)
    items = store.list_items(list(statuses))
    events = sorted(
        (i for i in items if i["type"] == "event" and i["data"].get("date")
         and _as_date(i["data"]["date"]) >= start),
        key=lambda i: (_as_date(i["data"]["date"]), str(i["data"].get("start_time"))),
    )
    return {
        "events": events,
        "announcements": [i for i in items if i["type"] == "announcement"
                          and not (i["data"].get("deadline") and _as_date(i["data"]["deadline"]) < start)],
        "achievements": [i for i in items if i["type"] == "achievement"],
        "week_start": start, "week_end": end,
    }


def from_saved(issue_doc: dict, *, for_send: bool = False) -> tuple[dict, EditorPlan]:
    """Recheck approval on previews; sent editions use the approved content saved at delivery.

    A preview only records candidates. Rejected, pending, missing or already-published items
    must not enter a new send. If the candidates changed, replace the old headline and teaser
    too: they may mention a removed item or its old details.
    """
    if not for_send and issue_doc.get("delivery_frozen") and issue_doc.get("delivery_snapshot"):
        issue_doc = issue_doc["delivery_snapshot"]
    monday = date.fromisoformat(issue_doc["week_start"])
    saved_items = issue_doc.get("items")
    if not for_send and (issue_doc.get("status") == "sent" or issue_doc.get("delivery_frozen")) and saved_items is not None:
        items = saved_items
        changed = False
    else:
        # Old sent issues have no snapshot. Only show their currently approved/public items.
        allowed = {"approved", "published"} if not for_send and issue_doc.get("status") == "sent" else {"approved"}
        items = [i for i in (store.get_item(x) for x in issue_doc.get("item_ids", []))
                 if i and i.get("status") in allowed]
        changed = saved_items is None or items != saved_items
    plan = EditorPlan.model_validate(issue_doc.get("editor_plan") or {})
    if changed:
        from .editor import fallback_plan
        plan = fallback_plan(items, plan.spotlight_item_id)
    # Persisted/legacy preview copy is untrusted too. Validate it against this edition,
    # even when its approved candidate list has not changed.
    from .editor import validate
    plan, _ = validate(plan, items)
    start, end = week_range(monday)
    issue = {
        "events": sorted((i for i in items if i["type"] == "event"),
                         key=lambda i: (_as_date(i["data"]["date"]), str(i["data"].get("start_time")))),
        "announcements": [i for i in items if i["type"] == "announcement"],
        "achievements": [i for i in items if i["type"] == "achievement"],
        "week_start": start, "week_end": end,
    }
    return issue, plan


def all_items(issue: dict) -> list[dict]:
    return issue["events"] + issue["announcements"] + issue["achievements"]


# ---------------------------------------------------------------- rendering

# Fixed, numbered sections like a newspaper; a section with nothing in it is left out.
SECTIONS = [("glance", "Coming up", "📅", "events"),
            ("events", "Events", "📣", "events"),
            ("wins", "Campus wins", "🏆", "achievements"),
            ("opportunities", "Opportunities", "📌", "announcements")]


def _context(issue: dict, plan: EditorPlan) -> dict:
    """One edition for everyone. The spotlight event leads the Events section."""
    from .editor import validate
    plan, _ = validate(plan, all_items(issue))
    spotlight = next((e for e in issue["events"] if e["id"] == plan.spotlight_item_id), None)
    events = ([spotlight] if spotlight else []) + [e for e in issue["events"] if e is not spotlight]
    if not plan.headline:          # an issue saved before the Editor wrote headlines
        from .editor import fallback_plan
        plain = fallback_plan(all_items(issue), plan.spotlight_item_id)
        plan = plan.model_copy(update={"headline": plain.headline, "teaser": plain.teaser})
    sections, n = [], 0
    for key, title, emoji, source in SECTIONS:
        if issue[source]:
            n += 1
            sections.append({"key": key, "title": f"{n}. {title.upper()}", "emoji": emoji})
    free = sum(1 for e in issue["events"] if _is_free(e["data"].get("fee")))
    return {
        "issue": issue, "plan": plan, "spotlight": spotlight, "events": events, "sections": sections,
        "base": config.PUBLIC_BASE_URL,
        "stats": {"events": len(issue["events"]), "free": free,
                  "wins": len(issue["achievements"]), "opportunities": len(issue["announcements"])},
    }


def render_email(issue: dict, plan: EditorPlan, subscriber: dict | None,
                 unsubscribe_url: str = "", issue_id: str = "", prefs_url: str = "") -> str:
    ctx = _context(issue, plan)
    # Links in a student's own email save the event for a 1-hour reminder (signed, per student).
    save_q = f"?s={tokens.sign(subscriber['email'], 'save')}" if subscriber and subscriber.get("email") else ""
    ctx.update(unsubscribe_url=unsubscribe_url, issue_id=issue_id, prefs_url=prefs_url, save_q=save_q)
    return env.get_template("weekly_email.html").render(**ctx)


def render_magazine(issue: dict, plan: EditorPlan, issue_id: str) -> str:
    ctx = _context(issue, plan)
    ctx["issue_id"] = issue_id
    return env.get_template("magazine.html").render(**ctx)


def build_preview(monday: date) -> tuple[str, dict]:
    """Sunday evening: plan + store next week's issue for Student Affairs to review."""
    from . import editor
    from fastapi import HTTPException
    for snap in store.db().collection("issues").where("week_start", "==", monday.isoformat()).stream():
        previous = snap.to_dict()
        if previous.get("status") in ("sending", "partial", "needs_attention") or (
                previous.get("status") == "held" and previous.get("delivery_frozen")):
            raise HTTPException(409, "This week's edition has started delivery. Resolve its delivery status before rebuilding it.")
    issue = collect(monday)
    items = all_items(issue)
    plan, report = editor.plan_issue(items)
    # Re-running the preview replaces the older one for that week, so Monday sends the newest.
    older = (store.db().collection("issues").where("status", "==", "preview")
             .where("week_start", "==", monday.isoformat()).stream())
    for snap in older:
        store.db().collection("issues").document(snap.id).update({"status": "superseded"})
    issue_id = store.save_issue({
        "week_start": monday.isoformat(),
        "status": "preview",
        "item_ids": [i["id"] for i in items],
        "items": items,
        "editor_plan": plan.model_dump(),
        "editor_report": report,
    })
    return issue_id, report
