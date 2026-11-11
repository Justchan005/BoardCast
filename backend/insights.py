"""Operations and cost summary for Student Affairs (/admin/insights).

Every figure is counted from records BoardCast already keeps (items, the run trace, AI briefs,
editions, clicks). Nothing here is estimated by a model. Figures that cannot be measured by the
system, such as time spent promoting an event by hand, are entered by the reader on the page
and are labelled there as assumptions.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from statistics import median

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from shared.schemas import ItemStatus, Role

from . import auth, config, extract, issue_builder, prereview, store, trace

router = APIRouter()

PURPOSES = {"extract": "Read the poster", "verify_poster": "Second read (pre-review tool)",
            "prereview": "Pre-review agent turns", "choose_spotlight": "Choose the spotlight event"}


def _aware(ts):
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return None


def _purpose(name: str) -> str:
    return "prereview" if re.fullmatch(r"prereview_turn_\d+", name or "") else (name or "other")


def _rows(collection: str) -> list[dict]:
    return [snap.to_dict() or {} for snap in store.db().collection(collection).stream()]


def summary() -> dict:
    items = store.list_items([s.value for s in ItemStatus])
    steps = [s for s in _rows(trace.COLLECTION) if s.get("kind") in trace.KINDS]
    briefs = _rows(prereview.COLLECTION)
    issues = _rows("issues")

    by_status = {s.value: 0 for s in ItemStatus}
    by_type: dict[str, int] = {}
    for item in items:
        by_status[item.get("status")] = by_status.get(item.get("status"), 0) + 1
        by_type[item.get("type")] = by_type.get(item.get("type"), 0) + 1

    # ---- human decisions, and how long a submission waited for one
    sent_for_review: dict[str, list[datetime]] = {}
    for step in steps:
        ts = _aware(step.get("ts"))
        if step.get("kind") == "human" and step.get("name") in ("submitted", "corrected") and step.get("item_id") and ts:
            sent_for_review.setdefault(step["item_id"], []).append(ts)
    waits, outcomes = [], {"ready_approved": 0, "flagged_returned": 0, "flagged_approved_after_check": 0,
                           "ready_returned": 0, "no_brief": 0}
    approved = rejected = 0
    for step in steps:
        if step.get("kind") != "human" or step.get("name") not in ("approved", "rejected"):
            continue
        is_approval = step["name"] == "approved"
        approved, rejected = approved + is_approval, rejected + (not is_approval)
        ts = _aware(step.get("ts"))
        earlier = [t for t in sent_for_review.get(step.get("item_id"), []) if ts and t <= ts]
        if earlier:
            waits.append((ts - max(earlier)).total_seconds() / 60)
        advice = (step.get("detail") or {}).get("ai_brief_at_decision")
        key = {("looks_ready", True): "ready_approved", ("needs_attention", False): "flagged_returned",
               ("needs_attention", True): "flagged_approved_after_check",
               ("looks_ready", False): "ready_returned"}.get((advice, is_approval), "no_brief")
        outcomes[key] += 1

    # ---- the pre-review agent
    done = [b for b in briefs if b.get("status") == "done"]
    flags_by_check = {tool: 0 for tool in prereview.CHECK_TOOLS}
    for brief in done:
        for check in brief.get("checks") or []:
            if check.get("outcome") == "flag" and check.get("tool") in flags_by_check:
                flags_by_check[check["tool"]] += 1

    # ---- model usage, as reported by the API for each call
    models = [s for s in steps if s.get("kind") == "model"]
    totals = trace.totals(steps)
    poster_owner = {i["poster_uri"]: i["id"] for i in items if i.get("poster_uri")}
    with_ai = {s.get("item_id") or poster_owner.get(s.get("poster_uri")) or s.get("poster_uri") for s in models}
    with_ai.discard(None)
    by_purpose = []
    for purpose, label in PURPOSES.items():
        rows = [s for s in models if _purpose(s.get("name")) == purpose]
        if not rows:
            continue
        tokens_in = sum(s.get("tokens_in") or 0 for s in rows)
        tokens_out = sum(s.get("tokens_out") or 0 for s in rows)
        timed = [s["ms"] for s in rows if isinstance(s.get("ms"), int)]
        by_purpose.append({"purpose": purpose, "label": label, "calls": len(rows),
                           "failed": sum(not s.get("ok", True) for s in rows),
                           "prompts": sorted({s["prompt"] for s in rows if s.get("prompt")}),
                           "tokens_in": tokens_in, "tokens_out": tokens_out,
                           "avg_ms": round(sum(timed) / len(timed)) if timed else None,
                           "estimated_cost_usd": trace.cost_usd(tokens_in, tokens_out)})
    cost = totals["estimated_cost_usd"]

    # ---- reach
    clicks = {"register": 0, "calendar": 0}
    for click in _rows("clicks"):
        if click.get("kind") in clicks:
            clicks[click["kind"]] += 1
    delivered = [i for i in issues if (i.get("delivery_summary") or {}).get("sent")]

    return {
        "generated_at": store.now().isoformat(),
        "ai_configured": extract.ai_configured(), "model": config.GEMINI_MODEL,
        "prices_configured": trace.cost_usd(0, 0) is not None,
        "pipeline": {"total": len(items), "by_status": by_status, "by_type": by_type},
        "review": {"decisions": approved + rejected, "approved": approved, "rejected": rejected,
                   "median_minutes_to_decision": round(median(waits), 1) if waits else None,
                   "timed_decisions": len(waits), "outcomes": outcomes},
        "agent": {"briefs": len(briefs), "done": len(done),
                  "unavailable": sum(b.get("status") == "unavailable" for b in briefs),
                  "looks_ready": sum(b.get("recommendation") == "looks_ready" for b in done),
                  "needs_attention": sum(b.get("recommendation") == "needs_attention" for b in done),
                  "overridden_by_rules": sum(bool(b.get("overridden_by_rules")) for b in done),
                  "points_dropped_without_evidence": sum(b.get("dropped_points") or 0 for b in done),
                  "flags_by_check": [{"tool": tool, "label": prereview.TOOL_LABELS[tool], "flagged": count}
                                     for tool, count in flags_by_check.items()]},
        "ai": {"model_calls": totals["model_calls"], "failed_calls": sum(not s.get("ok", True) for s in models),
               "tokens_in": totals["tokens_in"], "tokens_out": totals["tokens_out"],
               "model_seconds": round(totals["model_ms"] / 1000, 1), "estimated_cost_usd": cost,
               "submissions_with_ai": len(with_ai),
               "cost_per_submission_usd": round(cost / len(with_ai), 6) if cost is not None and with_ai else None,
               "by_purpose": by_purpose},
        "reach": {"subscribers": len(store.active_subscribers()), "editions_delivered": len(delivered),
                  "emails_delivered": sum(i["delivery_summary"]["sent"] for i in delivered),
                  "register_clicks": clicks["register"], "calendar_clicks": clicks["calendar"],
                  "reminders_set": len(_rows("saved"))},
    }


@router.get("/admin/insights", response_class=HTMLResponse)
def insights_page():
    return HTMLResponse(issue_builder.env.get_template("insights.html").render(
        client_id=config.OAUTH_CLIENT_ID, dev_mode=config.DEV_MODE,
        demo_switcher=config.LOCAL_MODE, demo_view="affairs"))


@router.get("/admin/api/insights")
def insights(email: str = Depends(auth.web_user)):
    auth.require_role(email, Role.STUDENT_AFFAIRS)
    return summary()
