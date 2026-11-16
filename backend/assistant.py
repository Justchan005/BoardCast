"""BoardCast Assistant: one chat for club reps and Student Affairs, inside the staff pages.

What the model decides (prompts/assistant.md):
    which read-only tools answer the question, and how to explain the result.
What it cannot do:
    change anything. Tools named prepare_* only return a confirmation card. The person clicks
    the card's button, and the browser calls the same endpoint as the review desk, which checks
    the role, the item version and the AI red-flag acknowledgement again.
Fixed rules, enforced in code:
    1. The signed-in identity and role come from the request, never from the chat text.
    2. Every tool re-checks the role; club reps only see their own club's submissions.
    3. Cards (titles, counts, statuses, flags) are built by code from stored records, not written
       by the model, so what the user acts on is always the real record.
    4. At most MAX_TURNS model turns per message. Without Gemini, or when it fails, a deterministic
       intent router answers the common questions with the same tools ("quick answers" mode).
Each Gemini call is written to the run trace as purpose "assistant", so insights include its tokens.
"""
from __future__ import annotations


import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Callable, Literal, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from shared import prompts
from shared.schemas import ItemStatus, Role

from . import audit, auth, checks, config, extract, prereview, reviews, store, trace

router = APIRouter()
log_ = logging.getLogger("boardcast.assistant")

MAX_TURNS = 5
_LOW_THINKING = True   # quick chat answers; switched off if the configured model rejects thinking levels
MAX_HISTORY = 16
MAX_CARDS = 4
LIST_LIMIT = 12
KIND = {"event": "Event", "announcement": "Opportunity", "achievement": "Achievement"}
STATUS_LABEL = {"draft": "Needs fixing", "pending": "Waiting for review", "approved": "Approved",
                "published": "Published", "rejected": "Returned to club"}
LIST_STATUSES = ("pending", "draft", "approved", "rejected", "published", "all")


# ---------------------------------------------------------------- request / response

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=60)


@dataclass
class Ctx:
    email: str
    role: str
    club_id: Optional[str]
    cards: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)

    @property
    def is_affairs(self) -> bool:
        return self.role == Role.STUDENT_AFFAIRS

    def add_card(self, card: dict) -> None:
        key = card.get("key") or card["type"]
        card["key"] = key
        self.cards = [c for c in self.cards if c.get("key") != key] + [jsonable_encoder(card)]


class ToolError(Exception):
    """A tool refused or could not answer; its message is shown to the model and the user."""


# ---------------------------------------------------------------- shared helpers

def _tz() -> ZoneInfo:
    return ZoneInfo(config.TIMEZONE)


def today() -> date:
    return datetime.now(_tz()).date()


def _aware(ts) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        try:
            return _aware(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _local_day(ts) -> Optional[date]:
    moment = _aware(ts)
    return moment.astimezone(_tz()).date() if moment else None


def _item_day(item: dict) -> Optional[date]:
    data = item.get("data") or {}
    raw = data.get("date") or data.get("deadline")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _scoped(ctx: Ctx, statuses: list[str]) -> list[dict]:
    items = store.list_items(statuses)
    if not ctx.is_affairs:
        items = [i for i in items if i.get("club_id") == ctx.club_id]
    return items


def _get_scoped(ctx: Ctx, item_id: str) -> dict:
    item = store.get_item(str(item_id or "").strip())
    if not item or (not ctx.is_affairs and item.get("club_id") != ctx.club_id):
        raise ToolError("I couldn't find that submission in what you can see.")
    return item


def _brief(item: dict) -> Optional[dict]:
    """The current AI pre-review brief, reduced to what a reviewer needs; None when there is none."""
    try:
        brief = prereview.get(item)
    except Exception:
        return None
    status = brief.get("status")
    if status in (None, "none"):
        return None
    if status != "done":
        return {"status": status}
    flags = reviews.open_flags(item) if brief.get("current") else []
    return {"status": "done", "current": bool(brief.get("current")),
            "recommendation": brief.get("recommendation"), "headline": brief.get("headline"),
            "flags": flags[:6]}


def _poster_url(item: dict) -> Optional[str]:
    if not item.get("poster_uri"):
        return None
    from .main import staff_poster_url   # main imports this module
    return staff_poster_url(item["id"])


def _row(item: dict, *, brief: bool = True, full: bool = False) -> dict:
    data = item.get("data") or {}
    row = {
        "id": item["id"], "type": item.get("type"), "kind": KIND.get(item.get("type"), item.get("type")),
        "status": item.get("status"), "status_label": STATUS_LABEL.get(item.get("status"), item.get("status")),
        "title": data.get("title") or "Untitled submission",
        "organiser": data.get("organiser") or data.get("team") or item.get("club_id") or "Campus office",
        "club_id": item.get("club_id"), "submitted_by": item.get("submitted_by"),
        "date": data.get("date") or data.get("deadline"), "start_time": data.get("start_time"),
        "venue": data.get("venue"), "fee": data.get("fee"),
        "issues": list(item.get("issues") or []),
        "rejection_reason": item.get("rejection_reason"),
        "created_at": item.get("created_at"), "updated_at": item.get("updated_at"),
        "poster_url": _poster_url(item),
    }
    if brief and item.get("status") == "pending":
        row["brief"] = _brief(item)
    if full:
        row["data"] = data
        row["description"] = item.get("description")
        row["show_poster"] = bool(item.get("show_poster"))
    return jsonable_encoder(row)


def _for_model(row: dict) -> dict:
    """What the model needs to talk about a row; long fields stay in the card."""
    keep = ("id", "kind", "status", "title", "organiser", "date", "start_time", "venue", "fee",
            "issues", "rejection_reason", "submitted_by")
    out = {k: row.get(k) for k in keep if row.get(k) not in (None, "", [])}
    brief = row.get("brief")
    if brief:
        out["ai_pre_review"] = {k: brief.get(k) for k in ("status", "recommendation", "flags") if brief.get(k)}
    return out


def _norm(text) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def _find_by_title(ctx: Ctx, query: str, statuses: list[str]) -> list[dict]:
    wanted = _norm(query)
    if not wanted:
        return []
    scored = []
    for item in _scoped(ctx, statuses):
        title = _norm((item.get("data") or {}).get("title"))
        if not title:
            continue
        score = 1.0 if wanted in title else SequenceMatcher(None, wanted, title).ratio()
        if score >= 0.6:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], str((pair[1].get("data") or {}).get("title"))))
    best = scored[0][0] if scored else 0
    return [item for score, item in scored if score >= best - 0.05][:5]


def _latest_issue() -> Optional[dict]:
    snaps = store.db().collection("issues").order_by("week_start", direction="DESCENDING").limit(50).stream()
    docs = [s.to_dict() for s in snaps]
    docs = [d for d in docs if d and d.get("status") != "superseded"]
    return max(docs, key=lambda d: (str(d.get("week_start", "")), str(d.get("created_at", ""))), default=None)


def _n(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count} {noun if count == 1 else (plural or noun + 's')}"


# ---------------------------------------------------------------- tools

def tool_get_dashboard(ctx: Ctx, args: dict) -> dict:
    items = _scoped(ctx, [s.value for s in ItemStatus])
    by_status = {s.value: 0 for s in ItemStatus}
    for item in items:
        by_status[item.get("status")] = by_status.get(item.get("status"), 0) + 1
    pending = [i for i in items if i.get("status") == "pending"]
    now_day = today()
    arrived_today = [i for i in pending if _local_day(i.get("created_at")) == now_day]
    flagged, ready, no_brief = [], [], []
    for item in pending:
        brief = _brief(item)
        if brief and brief.get("status") == "done" and brief.get("current"):
            (flagged if brief.get("recommendation") == "needs_attention" else ready).append(item)
        else:
            no_brief.append(item)
    oldest = min(pending, key=lambda i: str(i.get("created_at") or ""), default=None)
    oldest_info = None
    if oldest and _aware(oldest.get("created_at")):
        hours = (store.now() - _aware(oldest["created_at"])).total_seconds() / 3600
        oldest_info = {"title": (oldest.get("data") or {}).get("title"), "id": oldest["id"],
                       "waiting_hours": round(max(hours, 0), 1)}
    result = {"scope": "all clubs" if ctx.is_affairs else f"club {ctx.club_id}",
              "waiting_for_review": len(pending), "arrived_today": len(arrived_today),
              "ai_flagged": len(flagged), "ai_looks_ready": len(ready), "no_ai_brief_yet": len(no_brief),
              "needs_fixing_drafts": by_status["draft"], "approved_for_next_edition": by_status["approved"],
              "returned_to_club": by_status["rejected"], "published": by_status["published"],
              "oldest_waiting": oldest_info}
    tiles = [{"label": "Waiting for review", "value": len(pending), "tone": "accent" if pending else "calm"},
             {"label": "Arrived today", "value": len(arrived_today), "tone": "calm"},
             {"label": "AI flagged", "value": len(flagged), "tone": "warn" if flagged else "calm"},
             {"label": "Approved, next edition", "value": by_status["approved"], "tone": "ok"}]
    if not ctx.is_affairs:
        tiles[2] = {"label": "Needs fixing", "value": by_status["draft"], "tone": "warn" if by_status["draft"] else "calm"}
        tiles[3] = {"label": "Returned to club", "value": by_status["rejected"], "tone": "bad" if by_status["rejected"] else "calm"}
    if ctx.is_affairs:
        issue = _latest_issue()
        if issue:
            result["latest_edition"] = {"week_start": issue.get("week_start"), "status": issue.get("status"),
                                        "items": len(issue.get("item_ids") or [])}
    actions = []
    if pending:
        actions.append({"kind": "prompt", "label": "Show waiting submissions",
                        "prompt": "Show me the submissions waiting for review"})
    if ctx.is_affairs and ready:
        actions.append({"kind": "prompt", "label": f"Approve the {len(ready)} that look ready",
                        "prompt": "Prepare approval for everything that looks ready"})
    if not ctx.is_affairs and by_status["draft"] + by_status["rejected"]:
        actions.append({"kind": "prompt", "label": "What do I need to fix?",
                        "prompt": "Which of my submissions need fixing, and why?"})
    note = (f"Oldest waiting: {oldest_info['title']} ({oldest_info['waiting_hours']} h)" if oldest_info else None)
    ctx.add_card({"type": "stats", "key": "dashboard",
                  "title": "Today at a glance" if ctx.is_affairs else "Your club at a glance",
                  "subtitle": now_day.strftime("%A, %d %B %Y").replace(" 0", " "), "tiles": tiles,
                  "note": note, "actions": actions})
    return result


def tool_list_submissions(ctx: Ctx, args: dict) -> dict:
    status = str(args.get("status") or "pending").lower()
    if status not in LIST_STATUSES:
        raise ToolError(f"Unknown status {status!r}. Use one of: {', '.join(LIST_STATUSES)}.")
    item_type = args.get("item_type")
    if item_type and item_type not in KIND:
        raise ToolError("item_type must be event, announcement or achievement.")
    statuses = [s.value for s in ItemStatus] if status == "all" else [status]
    items = _scoped(ctx, statuses)
    if item_type:
        items = [i for i in items if i.get("type") == item_type]
    if args.get("arrived_today"):
        items = [i for i in items if _local_day(i.get("created_at")) == today()]
    search = _norm(args.get("search"))
    if search:
        items = [i for i in items if search in _norm(" ".join(str(x or "") for x in (
            (i.get("data") or {}).get("title"), (i.get("data") or {}).get("organiser"),
            (i.get("data") or {}).get("team"), i.get("club_id"), i.get("submitted_by"))))]
    # Oldest first for the review queue (fair order); newest first otherwise.
    items.sort(key=lambda i: str(i.get("created_at") or ""), reverse=status != "pending")
    try:
        limit = max(1, min(int(args.get("limit") or LIST_LIMIT), 25))
    except (TypeError, ValueError):
        limit = LIST_LIMIT
    rows = [_row(i) for i in items[:limit]]
    label = "All submissions" if status == "all" else STATUS_LABEL.get(status, status)
    ctx.add_card({"type": "queue", "key": f"queue-{status}", "title": label, "status": status,
                  "total": len(items), "items": rows,
                  "can_decide": ctx.is_affairs and status == "pending"})
    return {"status": status, "total": len(items), "shown": len(rows), "items": [_for_model(r) for r in rows]}


def tool_get_submission(ctx: Ctx, args: dict) -> dict:
    item_id, title = str(args.get("item_id") or "").strip(), str(args.get("title") or "").strip()
    if item_id:
        item = _get_scoped(ctx, item_id)
    elif title:
        matches = _find_by_title(ctx, title, [s.value for s in ItemStatus])
        if not matches:
            raise ToolError(f"No submission you can see has a title like “{title}”.")
        if len(matches) > 1 and _norm(title) != _norm((matches[0].get("data") or {}).get("title")):
            rows = [_row(i) for i in matches]
            ctx.add_card({"type": "queue", "key": "matches", "title": "Which one did you mean?",
                          "total": len(rows), "items": rows, "can_decide": False})
            return {"ambiguous": True, "candidates": [_for_model(r) for r in rows],
                    "note": "Ask the user which one they mean."}
        item = matches[0]
    else:
        raise ToolError("Give an item_id or a title.")
    row = _row(item, full=True)
    if item.get("status") != "pending":
        row["brief"] = None
    ctx.add_card({"type": "item", "key": f"item-{item['id']}", "item": row,
                  "can_decide": ctx.is_affairs and item.get("status") == "pending",
                  "can_correct": item.get("status") in ("draft", "pending", "rejected", "approved")})
    out = _for_model(row)
    out.update(data=row.get("data"), description=row.get("description"))
    return out


def tool_get_edition(ctx: Ctx, args: dict) -> dict:
    if not ctx.is_affairs:
        raise ToolError("Only Student Affairs manages the weekly edition.")
    issue = _latest_issue()
    approved = _scoped(ctx, ["approved"])
    info = None
    if issue:
        info = {"id": issue.get("id"), "week_start": issue.get("week_start"), "status": issue.get("status"),
                "items": len(issue.get("item_ids") or []), "delivery": issue.get("delivery_summary") or {},
                "magazine_url": f"/m/{issue.get('id')}"}
    status = (issue or {}).get("status")
    ctx.add_card({"type": "edition", "key": "edition", "issue": info, "approved_waiting": len(approved),
                  "approved_titles": [(i.get("data") or {}).get("title") for i in approved[:6]],
                  "local_mode": config.LOCAL_MODE,
                  "can_build": config.LOCAL_MODE,
                  "can_publish": config.LOCAL_MODE and status in ("preview", "partial"),
                  "can_hold": bool(issue) and status not in ("sent", "held", "superseded")})
    return {"latest_edition": info, "approved_not_yet_in_an_edition": len(approved),
            "schedule": "Preview built Sunday evening; sent to subscribers Monday 8am unless held.",
            "local_demo_mode": config.LOCAL_MODE}


def tool_list_upcoming_events(ctx: Ctx, args: dict) -> dict:
    try:
        days = max(1, min(int(args.get("days") or 14), 90))
    except (TypeError, ValueError):
        days = 14
    start, end = today(), today() + timedelta(days=days)
    events = []
    for item in _scoped(ctx, ["approved", "published"]):
        if item.get("type") != "event":
            continue
        day = _item_day(item)
        if day and start <= day <= end:
            events.append((day, str((item.get("data") or {}).get("start_time") or ""), item))
    events.sort(key=lambda e: (e[0], e[1]))
    rows = [_row(i, brief=False) for _, _, i in events[:LIST_LIMIT]]
    ctx.add_card({"type": "events", "key": "events", "title": f"Approved events in the next {days} days",
                  "total": len(events), "items": rows})
    return {"from": start.isoformat(), "to": end.isoformat(), "total": len(events),
            "events": [_for_model(r) for r in rows]}


def tool_get_engagement(ctx: Ctx, args: dict) -> dict:
    from .web import _click_counts
    items = {i["id"]: i for i in _scoped(ctx, ["approved", "published"])}
    counts = _click_counts(set(items))
    rows = []
    for item_id, item in items.items():
        c = counts.get(item_id, {"register": 0, "calendar": 0, "saved": 0})
        rows.append({"id": item_id, "title": (item.get("data") or {}).get("title"), **c})
    rows.sort(key=lambda r: (r["register"] + r["calendar"] + r["saved"], r["title"] or ""), reverse=True)
    totals = {k: sum(r[k] for r in rows) for k in ("register", "calendar", "saved")}
    ctx.add_card({"type": "engagement", "key": "engagement", "title": "Student engagement",
                  "totals": totals, "rows": rows[:8], "total_items": len(rows)})
    return {"totals": totals, "top": rows[:8], "items_counted": len(rows),
            "meaning": "register = Register link clicks; calendar = Add to calendar clicks; "
                       "saved = students who will get a 1-hour reminder"}


def tool_get_insights(ctx: Ctx, args: dict) -> dict:
    if not ctx.is_affairs:
        raise ToolError("Insights are available to Student Affairs.")
    from .insights import summary
    s = summary()
    review, ai, reach = s["review"], s["ai"], s["reach"]
    cost = ai.get("estimated_cost_usd")
    tiles = [{"label": "Submissions", "value": s["pipeline"]["total"], "tone": "calm"},
             {"label": "Decisions made", "value": review["decisions"], "tone": "calm"},
             {"label": "Median wait (min)", "value": review["median_minutes_to_decision"] if
              review["median_minutes_to_decision"] is not None else "–", "tone": "calm"},
             {"label": "Subscribers", "value": reach["subscribers"], "tone": "calm"},
             {"label": "Gemini calls", "value": ai["model_calls"], "tone": "calm"},
             {"label": "Est. AI cost", "value": f"US${cost:.4f}" if cost is not None else "Prices not set",
              "tone": "calm"}]
    ctx.add_card({"type": "stats", "key": "insights", "title": "Operations insights", "tiles": tiles,
                  "actions": [{"kind": "link", "label": "Open insights", "href": "/admin/insights"}]})
    return {"pipeline": s["pipeline"], "review": review,
            "ai": {k: ai[k] for k in ("model_calls", "failed_calls", "tokens_in", "tokens_out", "estimated_cost_usd")},
            "pre_review_agent": {k: s["agent"][k] for k in ("briefs", "looks_ready", "needs_attention", "overridden_by_rules")},
            "reach": reach}


def tool_get_submission_rules(ctx: Ctx, args: dict) -> dict:
    item_type = args.get("item_type")
    types_ = [item_type] if item_type in checks.REQUIRED else list(checks.REQUIRED)
    return {"required_fields": {t: checks.REQUIRED[t] for t in types_},
            "allowed_link_domains": config.ALLOWED_LINK_DOMAINS,
            "rules": ["Dates cannot be in the past.", "Links must use https and an allowed domain.",
                      "The club's description (up to 40 words) cannot add numbers that are not in the details.",
                      "Achievements need the team's consent confirmed.",
                      "A registration link is optional, even when registration is required.",
                      "Poster uploads must be images (PNG or JPG), up to 10 MB."],
            "where": "Submit or correct at /submit. Track status and reasons at /admin/club."}


def tool_prepare_decision(ctx: Ctx, args: dict) -> dict:
    if not ctx.is_affairs:
        raise ToolError("Only Student Affairs can approve or return submissions.")
    decision = str(args.get("decision") or "").lower()
    if decision not in ("approve", "reject"):
        raise ToolError("decision must be approve or reject.")
    item = _get_scoped(ctx, args.get("item_id"))
    if item.get("status") != "pending":
        raise ToolError(f"“{(item.get('data') or {}).get('title')}” is {STATUS_LABEL.get(item.get('status'), item.get('status'))}, "
                        "so it can't be decided now. Only submissions waiting for review can be approved or returned.")
    reason = " ".join(str(args.get("reason") or "").split())[:2000]
    problems = checks.run_all(item["type"], item["data"]) + checks.description_problems(item.get("description"), item["data"])
    flags = reviews.open_flags(item) if decision == "approve" else []
    row = _row(item, full=True)
    blocked = decision == "approve" and bool(problems)
    ctx.add_card({"type": "decision", "key": f"decision-{item['id']}", "decision": decision, "item": row,
                  "reason": reason, "problems": problems, "flags": flags, "blocked": blocked})
    return {"prepared": not blocked, "title": row["title"], "decision": decision,
            "open_problems": problems, "ai_flags_to_acknowledge": flags,
            "needs_reason": decision == "reject" and not reason,
            "note": ("Approval is blocked until the club fixes the problems." if blocked else
                     "A confirmation card is shown. Nothing changes until the user presses its button.")}


def tool_prepare_bulk_approval(ctx: Ctx, args: dict) -> dict:
    if not ctx.is_affairs:
        raise ToolError("Only Student Affairs can approve submissions.")
    wanted = args.get("item_ids") if isinstance(args.get("item_ids"), list) else []
    pending = _scoped(ctx, ["pending"])
    if wanted:
        ids = {str(w) for w in wanted}
        pending = [i for i in pending if i["id"] in ids]
    pending.sort(key=lambda i: str(i.get("created_at") or ""))
    ready, excluded = [], []
    for item in pending:
        problems = checks.run_all(item["type"], item["data"]) + checks.description_problems(item.get("description"), item["data"])
        flags = reviews.open_flags(item)
        row = _row(item)
        if problems:
            excluded.append({**row, "excluded_because": "Open problems: " + "; ".join(problems[:2])})
        elif flags:
            excluded.append({**row, "excluded_because": f"AI raised {_n(len(flags), 'point')} to check; review it on its own."})
        else:
            ready.append(row)
    ctx.add_card({"type": "bulk", "key": "bulk", "ready": ready, "excluded": excluded})
    return {"ready_to_approve": [r["title"] for r in ready],
            "excluded": [{"title": r["title"], "why": r["excluded_because"]} for r in excluded],
            "note": "A confirmation card lists them; the user ticks and confirms. Nothing is approved yet."}


@dataclass(frozen=True)
class Tool:
    fn: Callable[[Ctx, dict], dict]
    description: str
    params: dict
    affairs_only: bool = False
    step: str = ""


def _s(desc: str, enum: list[str] | None = None):
    from google.genai import types
    return types.Schema(type=types.Type.STRING, description=desc, enum=enum)


def _i(desc: str):
    from google.genai import types
    return types.Schema(type=types.Type.INTEGER, description=desc)


def _b(desc: str):
    from google.genai import types
    return types.Schema(type=types.Type.BOOLEAN, description=desc)


def _ids(desc: str):
    from google.genai import types
    return types.Schema(type=types.Type.ARRAY, description=desc, items=_s("Submission id"))


TOOLS: dict[str, Tool] = {
    "get_dashboard": Tool(tool_get_dashboard, "Counts for right now: submissions waiting for review, how many arrived "
                          "today, AI-flagged, drafts, approved for the next edition, oldest waiting. Use for "
                          "'how many do I need to approve today', 'what's my status', overviews.", {},
                          step="Checked today's numbers"),
    "list_submissions": Tool(tool_list_submissions, "List submissions the user can see, filtered by status, type or "
                             "search text. The list is shown to the user as cards.",
                             {"status": lambda: _s("Status to list; default pending", list(LIST_STATUSES)),
                              "item_type": lambda: _s("Optional type", ["event", "announcement", "achievement"]),
                              "search": lambda: _s("Optional words in the title, organiser, club or uploader"),
                              "arrived_today": lambda: _b("Only items submitted today"),
                              "limit": lambda: _i("How many to show, at most 25")},
                             step="Looked through submissions"),
    "get_submission": Tool(tool_get_submission, "Full details of one submission: fields, description, open problems, "
                           "AI pre-review flags and rejection reason. Give item_id, or title when you only know the name.",
                           {"item_id": lambda: _s("Submission id"), "title": lambda: _s("Title or part of it")},
                           step="Opened a submission"),
    "get_edition": Tool(tool_get_edition, "The latest Campus Weekly edition: status, week, item count, delivery, "
                        "and how many approved items are waiting for the next one.", {}, affairs_only=True,
                        step="Checked the weekly edition"),
    "list_upcoming_events": Tool(tool_list_upcoming_events, "Approved or published events happening from today for a "
                                 "number of days.", {"days": lambda: _i("Days ahead, default 14")},
                                 step="Looked up upcoming events"),
    "get_engagement": Tool(tool_get_engagement, "Register clicks, Add to calendar clicks and reminders set, per approved "
                           "or published item the user can see.", {}, step="Counted student engagement"),
    "get_insights": Tool(tool_get_insights, "Operations metrics: decisions, median wait for a decision, AI calls, "
                         "tokens, estimated cost, subscribers, emails delivered.", {}, affairs_only=True,
                         step="Read the insights"),
    "get_submission_rules": Tool(tool_get_submission_rules, "Required fields per type, allowed link domains and "
                                 "submission rules. Use for how-to questions.",
                                 {"item_type": lambda: _s("Optional type", ["event", "announcement", "achievement"])},
                                 step="Checked the submission rules"),
    "prepare_decision": Tool(tool_prepare_decision, "Show a confirmation card to approve or return (reject) ONE pending "
                             "submission. Does not change anything; the user confirms on the card.",
                             {"item_id": lambda: _s("Submission id"),
                              "decision": lambda: _s("approve or reject", ["approve", "reject"]),
                              "reason": lambda: _s("Required for reject: what the club must correct, in the user's words")},
                             affairs_only=True, step="Prepared a decision for you to confirm"),
    "prepare_bulk_approval": Tool(tool_prepare_bulk_approval, "Show a confirmation card to approve several pending "
                                  "submissions at once. Items with open problems or AI flags are listed separately "
                                  "and excluded. Does not change anything.",
                                  {"item_ids": lambda: _ids("Optional ids; empty means every pending item")},
                                  affairs_only=True, step="Prepared a batch approval for you to confirm"),
}
REQUIRED_ARGS = {"prepare_decision": ["item_id", "decision"]}


def tools_for(ctx: Ctx) -> list[str]:
    return [name for name, tool in TOOLS.items() if ctx.is_affairs or not tool.affairs_only]


def run_tool(ctx: Ctx, name: str, args: dict) -> dict:
    tool = TOOLS.get(name)
    if tool is None or name not in tools_for(ctx):
        result = {"error": f"The tool {name!r} is not available to you."}
    else:
        try:
            result = tool.fn(ctx, dict(args or {}))
        except ToolError as exc:
            result = {"error": str(exc)}
        except HTTPException as exc:
            result = {"error": str(exc.detail)}
        except Exception as exc:   # a broken tool must not break the chat
            log_.warning("assistant tool %s failed: %s", name, exc)
            result = {"error": f"That lookup failed ({type(exc).__name__}). Try again, or use the page directly."}
    allowed = tool is not None and name in tools_for(ctx)
    label = ((tool.step or name) if "error" not in result else "Couldn't finish that check") if allowed \
        else "Not available for your role"
    ctx.steps.append({"tool": name, "label": label, "ok": "error" not in result})
    return jsonable_encoder(result)


def declarations(ctx: Ctx):
    from google.genai import types
    out = []
    for name in tools_for(ctx):
        tool = TOOLS[name]
        params = None
        if tool.params:
            params = types.Schema(type=types.Type.OBJECT, required=REQUIRED_ARGS.get(name) or None,
                                  properties={k: make() for k, make in tool.params.items()})
        out.append(types.FunctionDeclaration(name=name, description=tool.description, parameters=params))
    return out


# ---------------------------------------------------------------- suggestions

def starters(ctx: Ctx) -> list[str]:
    if ctx.is_affairs:
        return ["How many posters do I need to approve today?", "Show me what the AI flagged",
                "Approve everything that looks ready", "What's in the next Campus Weekly?",
                "Which events are coming up this week?", "How is engagement this week?"]
    return ["What's the status of my submissions?", "Why was my poster returned?",
            "What do I need to submit an event?", "How many students clicked Register?",
            "Which events are coming up this week?", "When will my event be published?"]


def follow_ups(ctx: Ctx) -> list[str]:
    types_ = [c["type"] for c in ctx.cards]
    if ctx.is_affairs:
        if "decision" in types_ or "bulk" in types_:
            return ["What's still waiting?", "What's in the next Campus Weekly?"]
        if "queue" in types_:
            return ["Approve everything that looks ready", "Show me what the AI flagged", "Show drafts that need fixing"]
        if "edition" in types_:
            return ["Show approved submissions", "How is engagement this week?"]
        return ["Show me the submissions waiting for review", "What's in the next Campus Weekly?",
                "Show operations insights"]
    if "queue" in types_ or "item" in types_:
        return ["How do I correct a submission?", "How many students clicked Register?"]
    return ["What's the status of my submissions?", "What do I need to submit an event?",
            "Which events are coming up this week?"]


# ---------------------------------------------------------------- deterministic fallback

ID_RE = re.compile(r"\b([0-9a-f]{12}|sample-[a-z]+-\d+)\b")


def _offline(ctx: Ctx, text: str, *, reason: str | None = None) -> str:
    """Quick answers without Gemini: an intent router over the same tools and cards."""
    t = " ".join(text.lower().split())
    intro = (reason + "\n\n") if reason else ""

    def has(*words):
        return any(re.search(r"\b" + re.escape(w), t) for w in words)

    id_match = ID_RE.search(t)
    polite = r"^(?:please\s+|pls\s+|can you\s+|could you\s+|go ahead and\s+)?"
    if ctx.is_affairs and has("approve all", "approve every", "bulk", "all that look ready", "everything that looks ready",
                              "all ready", "batch approv"):
        r = run_tool(ctx, "prepare_bulk_approval", {})
        if "error" in r:
            return intro + r["error"]
        if not r["ready_to_approve"]:
            return intro + "Nothing is ready for a one-click approval right now." + (
                f" {_n(len(r['excluded']), 'submission')} need{'s' if len(r['excluded']) == 1 else ''} a closer look first."
                if r["excluded"] else "")
        return intro + (f"**{_n(len(r['ready_to_approve']), 'submission')}** look ready. Untick any you want to "
                        "check first, then confirm on the card.")
    decision = ("approve" if re.search(polite + r"approve\b", t) else
                "reject" if re.search(polite + r"(?:reject|return)\b", t) else None)
    if ctx.is_affairs and decision:
        target = None
        if id_match:
            target = id_match.group(1)
        else:
            query = re.split(r"\b(?:because|reason)\b", t)[0]
            query = re.sub(r"\b(please|pls|can you|could you|go ahead and|approve|reject|return|the|poster|"
                           r"submission|for|it|to|club)\b", " ", query)
            matches = _find_by_title(ctx, query, ["pending"])
            if len(matches) == 1:
                target = matches[0]["id"]
        if target:
            reason_match = re.search(r"\b(?:because|reason:?)\s+(.+)$", text, re.IGNORECASE | re.DOTALL)
            r = run_tool(ctx, "prepare_decision", {"item_id": target, "decision": decision,
                                                    "reason": reason_match.group(1) if reason_match else ""})
            if "error" in r:
                return intro + r["error"]
            if not r["prepared"]:
                return intro + f"“{r['title']}” can't be approved yet: " + "; ".join(r["open_problems"]) + "."
            extra = (f" The AI pre-review raised {_n(len(r['ai_flags_to_acknowledge']), 'point')}; tick the "
                     "confirmation on the card after checking them." if r["ai_flags_to_acknowledge"] else "")
            if decision == "reject" and r["needs_reason"]:
                extra = " Add the reason the club should see, then confirm."
            return intro + f"Ready to {'approve' if decision == 'approve' else 'return'} **{r['title']}**.{extra}"
        tool_list_submissions(ctx, {"status": "pending"})
        return intro + "Which submission do you mean? Use the buttons on its card below."
    if decision:   # a club rep asked to approve or return something
        run_tool(ctx, "list_submissions", {"status": "pending"})
        return intro + ("Approvals are made by **Student Affairs**, so I can't approve or return submissions for you. "
                        "Here's what of yours is waiting for their review. I can help you check or correct any of them.")
    if id_match:
        r = run_tool(ctx, "get_submission", {"item_id": id_match.group(1)})
        return intro + (r.get("error") or
                        f"Here is **{r.get('title')}** ({STATUS_LABEL.get(r.get('status'), r.get('status'))}).")
    if has("flag", "attention", "risky"):
        r = run_tool(ctx, "list_submissions", {"status": "pending"})
        flagged = [i for i in r.get("items", [])
                   if (i.get("ai_pre_review") or {}).get("recommendation") == "needs_attention"]
        return intro + (f"{_n(len(flagged), 'waiting submission')} {'was' if len(flagged) == 1 else 'were'} flagged by "
                        "the AI pre-review. Open one to see what to check." if flagged else
                        "The AI pre-review hasn't flagged anything that's waiting.")
    if has("click", "engagement", "registered", "register link", "reminder", "popular"):
        r = run_tool(ctx, "get_engagement", {})
        tot = r["totals"]
        return intro + (f"Students clicked Register **{tot['register']}** times and Add to calendar **{tot['calendar']}** "
                        f"times; **{tot['saved']}** reminders are set.")
    if has("how do i", "how to", "how can i", "what do i need", "required", "rule", "help", "what can you"):
        r = run_tool(ctx, "get_submission_rules", {})
        req = r["required_fields"]
        return intro + ("Here's how BoardCast works:\n"
                        "- **Submit** a poster at /submit. Gemini fills in the details; you check and confirm them.\n"
                        f"- **Required:** events need {', '.join(req['event'])}; opportunities need "
                        f"{', '.join(req['announcement'])}; achievements need {', '.join(req['achievement'])}.\n"
                        "- **Review:** Student Affairs approves it, or returns it with a reason that is emailed to you.\n"
                        "- **Correct:** open the submission's card and press Correct; it goes back for review.\n"
                        "- **Publish:** approved items go out in Campus Weekly on Monday at 8am.")
    if not ctx.is_affairs and has("when will", "when is", "when does") and has("publish", "newsletter", "out"):
        run_tool(ctx, "list_submissions", {"status": "approved"})
        return intro + ("Approved submissions go out in the next Campus Weekly, sent to subscribers on **Monday at 8am**. "
                        "Anything still waiting needs Student Affairs' approval first.")
    if has("why", "returned", "rejected"):
        r = run_tool(ctx, "list_submissions", {"status": "rejected"})
        if not r.get("total"):
            return intro + "Nothing has been returned."
        first = r["items"][0]
        return intro + (f"{_n(r['total'], 'submission')} {'was' if r['total'] == 1 else 'were'} returned. The latest, "
                        f"**{first['title']}**, was returned because: “{first.get('rejection_reason') or 'no reason saved'}”. "
                        "Correct it from its card and it goes back for review.")
    if has("draft", "fix", "correct"):
        r = run_tool(ctx, "list_submissions", {"status": "draft"})
        return intro + (f"{_n(r['total'], 'draft')} need{'s' if r['total'] == 1 else ''} fixing. Each card lists the "
                        "problems and has a Correct button." if r.get("total") else "No drafts need fixing.")
    if ctx.is_affairs and has("edition", "newsletter", "campus weekly", "publish", "hold", "preview", "issue"):
        r = run_tool(ctx, "get_edition", {})
        e = r.get("latest_edition")
        waiting = _n(r["approved_not_yet_in_an_edition"], "approved submission")
        if not e:
            return intro + f"No edition has been built yet. {waiting} will go into the next one."
        return intro + (f"The latest edition (week of {e['week_start']}) is **{e['status']}** with "
                        f"{_n(e['items'], 'item')}. {waiting} will go into the next one. {r['schedule']}")
    if has("event", "upcoming", "this week", "happening", "calendar"):
        days = 7 if has("week") else 14
        r = run_tool(ctx, "list_upcoming_events", {"days": days})
        return intro + (f"{_n(r['total'], 'approved event')} between {r['from']} and {r['to']}." if r.get("total")
                        else f"No approved events are scheduled in the next {days} days yet.")
    if ctx.is_affairs and has("insight", "cost", "token", "metric", "median", "operation"):
        r = run_tool(ctx, "get_insights", {})
        return intro + (f"{r['review']['decisions']} review decisions so far and {r['ai']['model_calls']} Gemini calls. "
                        "The card has the key figures.")
    if not has("how many", "overview", "summary", "glance", "dashboard", "today") and \
            has("status", "waiting", "pending", "queue", "show", "list", "my submission", "approved", "published"):
        status = ("approved" if has("approved") else "published" if has("published") else
                  "pending" if ctx.is_affairs or has("waiting", "pending") else "all")
        r = run_tool(ctx, "list_submissions", {"status": status})
        label = {"pending": "waiting for review", "approved": "approved", "published": "published",
                 "all": "on record for your club"}[status]
        return intro + (f"{_n(r['total'], 'submission')} {'is' if r['total'] == 1 else 'are'} {label}."
                        if r.get("total") else f"No submissions are {label} right now.")
    r = run_tool(ctx, "get_dashboard", {})
    if ctx.is_affairs:
        approved = r["approved_for_next_edition"]
        return intro + (f"You have **{_n(r['waiting_for_review'], 'submission')} waiting for review** "
                        f"({r['arrived_today']} arrived today, {r['ai_flagged']} flagged by the AI pre-review). "
                        f"{_n(approved, 'approved item')} {'is' if approved == 1 else 'are'} lined up for the next edition.")
    return intro + (f"Your club has {r['waiting_for_review']} waiting for review, {r['needs_fixing_drafts']} to fix, "
                    f"{r['approved_for_next_edition']} approved and {r['published']} published.")


# ---------------------------------------------------------------- the Gemini agent loop

def _system(ctx: Ctx) -> tuple[str, str]:
    prompt = prompts.load("assistant")
    text = prompt.render(name=ctx.email, role_label="Student Affairs officer" if ctx.is_affairs else "club representative",
                         club_note="" if ctx.is_affairs else f" for the club “{ctx.club_id}”",
                         today=today().strftime("%A %d %B %Y"))
    return text, f"{prompt.name}@{prompt.version}"


def _contents(messages: list[ChatMessage]):
    from google.genai import types
    history = messages[-MAX_HISTORY:]
    while history and history[0].role != "user":
        history = history[1:]
    return [types.Content(role="user" if m.role == "user" else "model", parts=[types.Part.from_text(text=m.text)])
            for m in history]


def _text(resp) -> str:
    parts = []
    for candidate in (resp.candidates or [])[:1]:
        for part in (getattr(candidate.content, "parts", None) or []):
            if getattr(part, "text", None) and not getattr(part, "thought", False):
                parts.append(part.text)
    return "".join(parts).strip()


def run_agent(ctx: Ctx, messages: list[ChatMessage]) -> str:
    from google.genai import types
    system, source = _system(ctx)
    tools = [types.Tool(function_declarations=declarations(ctx))]
    contents = _contents(messages)
    if not contents:
        raise ValueError("No user message to answer.")

    def call(mode):
        global _LOW_THINKING
        for attempt in (1, 2):   # one retry: busy (429/503) responses are usually brief
            options = {"thinking_config": types.ThinkingConfig(thinking_level="low")} if _LOW_THINKING else {}
            cfg = types.GenerateContentConfig(
                system_instruction=system, tools=tools, temperature=0.2,
                tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode=mode)),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True), **options)
            started = time.perf_counter()
            try:
                resp = extract.client().models.generate_content(model=config.GEMINI_MODEL, contents=contents,
                                                                config=cfg)
                break
            except Exception as exc:
                if _LOW_THINKING and "thinking" in str(exc).lower():
                    _LOW_THINKING = False   # this model has no thinking levels: use its default from now on
                meta = trace.note_model_call(purpose="assistant", prompt=source, model=config.GEMINI_MODEL,
                                             started=started, error=exc, detail={"attempt": attempt})
                trace.record("model", "assistant", "Assistant could not reach Gemini", ok=False, actor=ctx.email,
                             detail=meta["detail"], prompt=source, model=config.GEMINI_MODEL, ms=meta["ms"])
                if attempt == 2:
                    raise
                time.sleep(1.2)
        meta = trace.note_model_call(purpose="assistant", prompt=source, model=config.GEMINI_MODEL,
                                     started=started, resp=resp)
        names = [c.name for c in (resp.function_calls or [])]
        trace.record("model", "assistant", ("Assistant chose: " + ", ".join(names)) if names else "Assistant answered",
                     actor=ctx.email, detail={"tools_chosen": names, "role": ctx.role}, prompt=source,
                     model=config.GEMINI_MODEL, tokens_in=meta["tokens_in"], tokens_out=meta["tokens_out"], ms=meta["ms"])
        return resp

    for turn in range(1, MAX_TURNS + 1):
        resp = call(types.FunctionCallingConfigMode.AUTO if turn < MAX_TURNS else types.FunctionCallingConfigMode.NONE)
        calls = list(resp.function_calls or [])
        if not calls:
            return _text(resp)
        candidate = (resp.candidates or [None])[0]
        if candidate is None or candidate.content is None:
            raise ValueError("Gemini returned no content.")
        contents.append(candidate.content)   # whole, so thought signatures and calls stay paired
        parts = []
        for fc in calls[:6]:
            result = run_tool(ctx, fc.name or "", dict(fc.args or {}))
            parts.append(types.Part.from_function_response(name=fc.name or "unknown", response=result))
        contents.append(types.Content(role="user", parts=parts))
    return ""


def _fallback_text(ctx: Ctx) -> str:
    return ("Here's what I found." if ctx.cards else
            "I couldn't put an answer together. Try asking another way, for example “what's waiting for review?”")


def prune_cards(ctx: Ctx) -> None:
    """Show the cards a person acts on, without repeats: a confirmation card replaces the detail
    card of the same submission, and a batch card replaces lists and details it already shows."""
    cards = ctx.cards
    if any(c["type"] == "bulk" for c in cards):
        cards = [c for c in cards if c["type"] not in ("item", "queue")]
    decided = {c["item"]["id"] for c in cards if c["type"] == "decision"}
    cards = [c for c in cards if not (c["type"] == "item" and c["item"]["id"] in decided)]
    cards = [c for c in cards if not (decided and c["type"] == "queue" and c.get("items")
                                      and all(r["id"] in decided for r in c["items"]))]
    if len(cards) > MAX_CARDS:
        actions = [c for c in cards if c["type"] in ("decision", "bulk")]
        others = [c for c in cards if c["type"] not in ("decision", "bulk")]
        room = max(MAX_CARDS - len(actions), 0)
        keep = actions[-MAX_CARDS:] + (others[-room:] if room else [])
        cards = [c for c in cards if any(c is k for k in keep)]
    ctx.cards = cards


def chat(ctx: Ctx, messages: list[ChatMessage]) -> dict:
    question = messages[-1].text
    mode = "gemini" if extract.ai_configured() else "quick"
    reply = ""
    if mode == "gemini":
        try:
            reply = run_agent(ctx, messages)
        except Exception as exc:
            log_.warning("assistant fell back to quick answers: %s", exc)
            mode = "quick"
            ctx.cards, ctx.steps = [], []
            reply = _offline(ctx, question, reason="_Gemini is unavailable right now, so here's a quick answer._")
    else:
        reply = _offline(ctx, question)
    prune_cards(ctx)
    reply = (reply or _fallback_text(ctx)).strip()[:6000]
    audit.log(ctx.email, "assistant_chat", None, {"mode": mode, "tools": [s["tool"] for s in ctx.steps]})
    on_cards = {a.get("prompt") for c in ctx.cards for a in c.get("actions") or []}
    suggestions = [s for s in follow_ups(ctx) if s not in on_cards]
    return {"reply": reply, "cards": ctx.cards, "steps": ctx.steps, "mode": mode, "suggestions": suggestions}


# ---------------------------------------------------------------- routes

def _ctx(email: str) -> Ctx:
    role = auth.require_role(email, Role.CLUB_REP, Role.STUDENT_AFFAIRS)
    return Ctx(email=email.lower(), role=role["role"], club_id=role.get("club_id"))


@router.get("/admin/api/assistant/context")
def assistant_context(email: str = Depends(auth.web_user)):
    ctx = _ctx(email)
    snapshot = tool_get_dashboard(ctx, {})
    return {"email": ctx.email, "role": ctx.role, "club_id": ctx.club_id,
            "ai": extract.ai_configured(), "model": config.GEMINI_MODEL if extract.ai_configured() else None,
            "local_mode": config.LOCAL_MODE, "starters": starters(ctx),
            "snapshot": {k: snapshot[k] for k in ("waiting_for_review", "arrived_today", "ai_flagged",
                                                  "needs_fixing_drafts", "returned_to_club",
                                                  "approved_for_next_edition")}}


@router.post("/admin/api/assistant/chat")
def assistant_chat(body: ChatRequest, email: str = Depends(auth.web_user)):
    if body.messages[-1].role != "user":
        raise HTTPException(422, "The last message must be from the user.")
    return chat(_ctx(email), body.messages)
