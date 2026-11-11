"""Staff routes for the pre-review brief and the run trace of one submission.

Access is the same as opening the submission: Student Affairs see everything, club reps
only their own club's items. Starting a pre-review is for Student Affairs only.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from shared.schemas import Role

from . import auth, config, issue_builder, prereview, store, trace

router = APIRouter()


def _item(item_id: str, email: str) -> dict:
    from .submission import _own_item   # submission imports main lazily; keep the same ownership rule
    return _own_item(item_id, email)


def _engagement(item_id: str) -> dict:
    from .web import _click_counts
    return _click_counts({item_id}).get(item_id, {"register": 0, "calendar": 0, "saved": 0})


@router.get("/admin/trace/{item_id}", response_class=HTMLResponse)
def trace_page(item_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item_id):
        raise HTTPException(404, "Submission not found")
    return HTMLResponse(issue_builder.env.get_template("trace.html").render(
        item_id=item_id, client_id=config.OAUTH_CLIENT_ID, dev_mode=config.DEV_MODE,
        demo_switcher=config.LOCAL_MODE, demo_view="affairs"))


@router.get("/admin/api/items/{item_id}/brief")
def brief(item_id: str, email: str = Depends(auth.web_user)):
    return prereview.get(_item(item_id, email))


@router.post("/admin/api/items/{item_id}/prereview")
def run_prereview(item_id: str, email: str = Depends(auth.web_user)):
    """Run (or re-run) the pre-review agent now. It briefs the reviewer; it cannot approve."""
    auth.require_role(email, Role.STUDENT_AFFAIRS)
    if not store.get_item(item_id):
        raise HTTPException(404, "Submission not found")
    return prereview.run(item_id, trigger="manual", actor=email)


@router.get("/admin/api/items/{item_id}/trace")
def item_trace(item_id: str, email: str = Depends(auth.web_user)):
    item = _item(item_id, email)
    steps = trace.for_item(item)
    return {"item": {"id": item["id"], "type": item["type"], "status": item["status"],
                     "title": (item.get("data") or {}).get("title"), "submitted_by": item.get("submitted_by"),
                     "has_poster": bool(item.get("poster_uri"))},
            "steps": [trace.public(step) for step in steps], "totals": trace.totals(steps),
            "brief": prereview.get(item), "engagement": _engagement(item_id),
            "prices_configured": trace.cost_usd(0, 0) is not None}


@router.get("/admin/api/items/{item_id}/trace/export")
def export_trace(item_id: str, email: str = Depends(auth.web_user)):
    """The run log as Markdown, for the submission pack (the browser saves it as a file)."""
    item = _item(item_id, email)
    return {"filename": f"run-log-{item_id}.md", "markdown": trace.to_markdown(item, trace.for_item(item))}
