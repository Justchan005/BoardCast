"""Gemini selects a spotlight; code renders copy from approved fields only.

Free-form model prose is never published, including prose in older saved previews.
If selection fails, the earliest event becomes the spotlight.
"""
from __future__ import annotations

import json
import time

from google.genai import types
from pydantic import BaseModel, Field

from shared import prompts
from shared.schemas import EditorPlan

from . import audit, config, extract, trace
from .checks import _as_date


def client():
    """The shared Gemini client, looked up at call time (never a copy taken at import)."""
    return extract.client()

HEADLINE_MAX, TEASER_MAX_WORDS, SUBJECT_MAX = 90, 35, 60


class SpotlightChoice(BaseModel):
    spotlight_item_id: str | None = Field(default=None, description="An event id from the approved input, or null")


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    clipped = text[:limit - 1]
    return (clipped.rsplit(" ", 1)[0] or clipped).rstrip() + "…"


def _events_by_date(items: list[dict]) -> list[dict]:
    events = [i for i in items if i["type"] == "event"]
    return sorted(events, key=lambda i: (str(_as_date(i["data"].get("date")) or ""), str(i["data"].get("start_time"))))


def fallback_plan(items: list[dict], spotlight_id: str | None = None) -> EditorPlan:
    """Render approved titles/dates without inventing or mixing facts."""
    from .issue_builder import _nice_date
    events = _events_by_date(items)
    spot = next((e for e in events if e["id"] == spotlight_id), events[0] if events else None)
    if not spot:
        return EditorPlan(spotlight_item_id=None, headline="Campus Weekly", teaser="",
                          subject_line="Campus Weekly: campus updates" if items else
                          "Campus Weekly: a quiet week on campus")
    others = [e for e in events if e is not spot]
    title = spot["data"]["title"]
    more = f", plus {len(others)} more event{'s' if len(others) != 1 else ''} coming up" if others else ""
    # Include complete title/date pairs only; never cut a date or splice claims.
    clauses = []
    for event in others:
        clause = f"{event['data']['title']} ({_nice_date(event['data'].get('date'))})"
        candidate = "; ".join([*clauses, clause])
        if len(("Also coming up: " + candidate).split()) <= TEASER_MAX_WORDS:
            clauses.append(clause)
        if len(clauses) == 3:
            break
    also = "; ".join(clauses)
    return EditorPlan(spotlight_item_id=spot["id"], headline=_shorten(title, HEADLINE_MAX - len(more)) + more,
                      teaser=f"Also coming up: {also}." if also else "",
                      subject_line=_shorten(f"Campus Weekly: {title}", SUBJECT_MAX))


def validate(plan: EditorPlan, items: list[dict]) -> tuple[EditorPlan, list[str]]:
    """Keep only a valid spotlight id; regenerate all prose from approved fields.

Also applies to stored plans created before this rule was introduced. A number
appearing somewhere in an issue is not evidence for a claim about another field.
"""
    event_ids = {i["id"] for i in items if i["type"] == "event"}
    spotlight = plan.spotlight_item_id if plan.spotlight_item_id in event_ids else None
    plain = fallback_plan(items, spotlight)
    replaced = [field for field in ("spotlight_item_id", "headline", "teaser", "subject_line")
                if getattr(plan, field) != getattr(plain, field)]
    return plain, replaced


def plan_issue(items: list[dict]) -> tuple[EditorPlan, dict]:
    """Returns (validated plan, report). The report shows what validation replaced (useful for the deck)."""
    if not items:
        return fallback_plan(items), {"source": "empty"}
    prompt = prompts.load("editor")
    source = f"{prompt.name}@{prompt.version}"
    payload = [{"id": i["id"], "type": i["type"], "data": i["data"]} for i in items]
    started = time.perf_counter()
    resp = None
    try:
        resp = client().models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[prompt.text, json.dumps(payload, default=str)],
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               response_schema=SpotlightChoice, temperature=0),
        )
        choice = resp.parsed if isinstance(resp.parsed, SpotlightChoice) else SpotlightChoice.model_validate_json(resp.text)
    except Exception as e:
        trace.note_model_call(purpose="choose_spotlight", prompt=source, model=config.GEMINI_MODEL, started=started,
                              resp=resp, error=e, summary="Spotlight choice failed; code used the earliest event",
                              detail={"fallback": "earliest event by date"})
        audit.log("gemini", "editor_failed", None, {"error": str(e)})
        return fallback_plan(items), {"source": "fallback", "error": str(e)}
    plan, replaced = validate(EditorPlan(spotlight_item_id=choice.spotlight_item_id), items)
    report = {"source": source, "copy_source": "approved_fields",
              "replaced": [field for field in replaced if field == "spotlight_item_id"]}
    trace.note_model_call(
        purpose="choose_spotlight", prompt=source, model=config.GEMINI_MODEL, started=started, resp=resp,
        summary=("Chose the spotlight event; code rendered every published word from approved fields"
                 if not report["replaced"] else
                 "Chose an id that is not an approved event; code replaced it with the earliest event"),
        detail={"model_choice": choice.spotlight_item_id, "used": plan.spotlight_item_id,
                "candidates": len([i for i in items if i["type"] == "event"]),
                "output_check": "id must be an approved event in the input"})
    audit.log("gemini", "editor_plan", None, report)
    return plan, report
