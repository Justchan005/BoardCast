"""Run trace: one ordered log per submission of what the model chose, which tools ran,
what code checked and what a human decided.

Every step has a `kind`:
    model   a Gemini call (prompt name@version, model, tokens, latency)
    tool    a tool the pre-review agent chose to call (arguments + result summary)
    code    a deterministic check or action (same result every time)
    human   a decision by a signed-in person
    system  delivery and other background actions

Steps are keyed by item_id, or by poster_uri for work done before the item exists
(reading a poster happens before the club confirms and submits). Writing a step never
breaks the main action: a failed trace write is logged and ignored.
"""
from __future__ import annotations

import itertools
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

log_ = logging.getLogger("boardcast.trace")
COLLECTION = "trace"
KINDS = ("model", "tool", "code", "human", "system")
_TEXT_LIMIT = 500
_capture: ContextVar[list | None] = ContextVar("boardcast_trace_capture", default=None)
_seq = itertools.count()


# ---------------------------------------------------------------- model-call capture

@contextmanager
def capture() -> Iterator[list[dict]]:
    """Collect model calls made inside the block (extract.py, editor.py, prereview.py
    report each Gemini call here) so the caller can attach them to an item."""
    calls: list[dict] = []
    token = _capture.set(calls)
    try:
        yield calls
    finally:
        _capture.reset(token)


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def usage(resp) -> dict:
    """Token counts as reported by the API; None when the response carries none."""
    meta = getattr(resp, "usage_metadata", None)
    tokens_in = _int(getattr(meta, "prompt_token_count", None))
    visible = _int(getattr(meta, "candidates_token_count", None))
    thoughts = _int(getattr(meta, "thoughts_token_count", None))
    tokens_out = None if visible is None and thoughts is None else (visible or 0) + (thoughts or 0)
    return {"tokens_in": tokens_in, "tokens_out": tokens_out}


def note_model_call(*, purpose: str, prompt: str, model: str, started: float, resp=None,
                    error: Exception | None = None, summary: str = "", detail: dict | None = None) -> dict:
    """Report one Gemini call. `started` is a time.perf_counter() value taken before the call."""
    call = {"purpose": purpose, "prompt": prompt, "model": model,
            "ms": max(0, round((time.perf_counter() - started) * 1000)),
            "ok": error is None, "summary": summary, "detail": dict(detail or {}), **usage(resp)}
    if error is not None:
        call["detail"]["error"] = f"{type(error).__name__}: {error}"
    calls = _capture.get()
    if calls is not None:
        calls.append(call)
    return call


# ---------------------------------------------------------------- writing steps

def _clean(value: Any, depth: int = 0) -> Any:
    """Keep trace documents small and storable: short strings, plain JSON types only."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _TEXT_LIMIT else value[:_TEXT_LIMIT - 1] + "…"
    if depth >= 4:
        return _clean(str(value))
    if isinstance(value, dict):
        return {str(k): _clean(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v, depth + 1) for v in list(value)[:25]]
    return _clean(str(value))


def record(kind: str, name: str, summary: str, *, item_id: str | None = None,
           poster_uri: str | None = None, issue_id: str | None = None, actor: str = "system",
           ok: bool = True, detail: dict | None = None, prompt: str | None = None,
           model: str | None = None, tokens_in: int | None = None, tokens_out: int | None = None,
           ms: int | None = None) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown trace kind: {kind}")
    row = {"ts": datetime.now(timezone.utc), "seq": next(_seq), "kind": kind, "name": name,
           "summary": _clean(summary), "item_id": item_id, "poster_uri": poster_uri,
           "issue_id": issue_id, "actor": (actor or "system").lower(), "ok": bool(ok),
           "detail": _clean(detail or {}), "prompt": prompt, "model": model,
           "tokens_in": _int(tokens_in), "tokens_out": _int(tokens_out), "ms": _int(ms)}
    try:
        from . import store  # imported lazily so offline tools (eval) need no database
        store.db().collection(COLLECTION).add(row)
    except Exception as exc:  # the main action must never fail because of its trace
        log_.warning("trace write failed: %s %s (%s)", kind, name, exc)


def flush(calls: list[dict], *, name_prefix: str = "", **keys) -> None:
    """Persist captured model calls as `model` steps, then empty the list."""
    for call in calls:
        record("model", name_prefix + call["purpose"],
               call["summary"] or ("Gemini call succeeded" if call["ok"] else "Gemini call failed"),
               ok=call["ok"], detail=call["detail"], prompt=call["prompt"], model=call["model"],
               tokens_in=call["tokens_in"], tokens_out=call["tokens_out"], ms=call["ms"], **keys)
    calls.clear()


# ---------------------------------------------------------------- reading

def _sort_key(step: dict):
    ts = step.get("ts")
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (ts or datetime.min.replace(tzinfo=timezone.utc), step.get("seq") or 0)


def for_item(item: dict) -> list[dict]:
    """Every step for this submission, oldest first: steps recorded against its id, plus
    steps recorded against its poster before the item existed."""
    from . import store
    col = store.db().collection(COLLECTION)
    rows = {snap.id: snap.to_dict() for snap in col.where("item_id", "==", item["id"]).stream()}
    if item.get("poster_uri"):
        for snap in col.where("poster_uri", "==", item["poster_uri"]).stream():
            step = snap.to_dict()
            if step.get("item_id") in (None, item["id"]):
                rows.setdefault(snap.id, step)
    return sorted(rows.values(), key=_sort_key)


def cost_usd(tokens_in: int | None, tokens_out: int | None) -> float | None:
    """Estimated cost from the configured per-million-token prices; None if not configured."""
    if config.GEMINI_PRICE_INPUT_PER_M is None or config.GEMINI_PRICE_OUTPUT_PER_M is None:
        return None
    return round(((tokens_in or 0) * config.GEMINI_PRICE_INPUT_PER_M
                  + (tokens_out or 0) * config.GEMINI_PRICE_OUTPUT_PER_M) / 1_000_000, 6)


def totals(steps: list[dict]) -> dict:
    models = [s for s in steps if s["kind"] == "model"]
    tokens_in = sum(s.get("tokens_in") or 0 for s in models)
    tokens_out = sum(s.get("tokens_out") or 0 for s in models)
    counted = any(s.get("tokens_in") is not None or s.get("tokens_out") is not None for s in models)
    return {
        "steps": len(steps),
        "model_calls": len(models),
        "tool_calls": sum(s["kind"] == "tool" for s in steps),
        "code_checks": sum(s["kind"] == "code" for s in steps),
        "human_decisions": sum(s["kind"] == "human" for s in steps),
        "failed_steps": sum(not s.get("ok", True) for s in steps),
        "tokens_in": tokens_in if counted else None,
        "tokens_out": tokens_out if counted else None,
        "model_ms": sum(s.get("ms") or 0 for s in models),
        "estimated_cost_usd": cost_usd(tokens_in, tokens_out) if counted else None,
    }


def public(step: dict) -> dict:
    """A step as sent to the browser (no storage keys the page does not need)."""
    ts = step.get("ts")
    return {"ts": ts.isoformat() if isinstance(ts, datetime) else ts,
            **{k: step.get(k) for k in ("kind", "name", "summary", "actor", "ok", "detail", "prompt",
                                         "model", "tokens_in", "tokens_out", "ms", "issue_id")}}


# ---------------------------------------------------------------- Markdown run log

def _cell(value) -> str:
    return str("" if value is None else value).replace("|", "\\|").replace("\n", " ")


def to_markdown(item: dict, steps: list[dict]) -> str:
    """The run log for the submission pack: one input linked to every action and the result."""
    data, t = item.get("data") or {}, totals(steps)
    lines = [
        f"# Run log: {_cell(data.get('title') or 'Untitled submission')}",
        "",
        f"- Submission id: `{item['id']}` · type: {item.get('type')} · final status: **{item.get('status')}**",
        f"- Submitted by: {item.get('submitted_by')} · poster attached: {'yes' if item.get('poster_uri') else 'no'}",
        f"- Steps: {t['steps']} ({t['model_calls']} model, {t['tool_calls']} tool, {t['code_checks']} code, "
        f"{t['human_decisions']} human) · failed steps: {t['failed_steps']}",
    ]
    if t["tokens_in"] is not None:
        cost = f" · estimated cost: US${t['estimated_cost_usd']:.6f}" if t["estimated_cost_usd"] is not None else ""
        lines.append(f"- Tokens: {t['tokens_in']} in / {t['tokens_out']} out · model time: {t['model_ms']} ms{cost}")
    lines += ["", "| # | Time (UTC) | Kind | Step | Actor | Prompt / model | Tokens in/out | ms | OK | What happened |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for n, s in enumerate(steps, 1):
        ts = s.get("ts")
        when = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime) else _cell(ts)
        source = " / ".join(x for x in (s.get("prompt"), s.get("model")) if x)
        tokens = "" if s.get("tokens_in") is None and s.get("tokens_out") is None else \
            f"{s.get('tokens_in') or 0}/{s.get('tokens_out') or 0}"
        lines.append(f"| {n} | {when} | {s['kind']} | {_cell(s['name'])} | {_cell(s.get('actor'))} | {_cell(source)} | "
                     f"{tokens} | {_cell(s.get('ms'))} | {'yes' if s.get('ok', True) else 'NO'} | {_cell(s.get('summary'))} |")
    lines += ["", "## Step details", ""]
    for n, s in enumerate(steps, 1):
        if s.get("detail"):
            lines.append(f"**{n}. {s['name']}**")
            lines += [f"- {_cell(k)}: `{_cell(v)}`" for k, v in s["detail"].items()]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
