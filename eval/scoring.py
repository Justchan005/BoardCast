"""Field-level scoring for poster extraction. Pure functions, no cloud access, unit-tested."""
from __future__ import annotations

import re

FIELDS = ["title", "organiser", "date", "start_time", "end_time", "venue", "fee", "category", "registration.link"]
FIELDS_BY_TYPE = {
    "event": FIELDS,
    "announcement": ["title", "organiser", "deadline", "action_link"],
    "achievement": ["title", "team", "competition", "result", "date"],
}
ALL_FIELDS = list(dict.fromkeys(f for fs in FIELDS_BY_TYPE.values() for f in fs))


def get(d: dict, path: str):
    for part in path.split("."):
        d = (d or {}).get(part) if isinstance(d, dict) else None
    return d


def norm(field: str, v) -> str:
    """Normalise so formatting differences don't count as errors."""
    if v is None or str(v).strip() == "":
        return ""
    s = str(v).strip().lower()
    if field in ("start_time", "end_time"):
        m = re.match(r"(\d{1,2}):(\d{2})", s)
        return f"{int(m.group(1)):02d}:{m.group(2)}" if m else s
    if field in ("date", "deadline"):
        return s[:10]
    if field in ("registration.link", "action_link"):
        return s.rstrip("/").removeprefix("https://").removeprefix("http://")
    if field == "fee":
        return s.replace(" ", "")
    s = re.sub(r"[•·|]|\s[-–—]\s", " ", s)   # separators ("On Campus • APU" / "On Campus - APU"); keeps D-6-04
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s&@./-]", "", s)).strip()


def matches(field: str, expected, got) -> bool:
    """`expected` may be a list of answers a human accepted up front (e.g. a venue written two
    ways on the same poster); the model only needs one of them. Never widen it after a run."""
    options = expected if isinstance(expected, list) else [expected]
    return any(norm(field, e) == norm(field, got) for e in options)


def score(expected: dict, got: dict, fields: list[str] = FIELDS) -> dict[str, bool]:
    """True per field when the normalised values match. A field missing from `expected`
    is skipped (not every poster labels every field)."""
    return {f: matches(f, get(expected, f), get(got, f)) for f in fields if f in _flat_keys(expected)}


def _flat_keys(d: dict, prefix: str = "") -> set[str]:
    keys = set()
    for k, v in d.items():
        path = f"{prefix}{k}"
        keys.add(path)
        if isinstance(v, dict):
            keys |= _flat_keys(v, path + ".")
    return keys


def summarise(rows: list[dict[str, bool]]) -> dict[str, tuple[int, int]]:
    """{field: (correct, total)} plus "overall"."""
    out: dict[str, list[int]] = {}
    for r in rows:
        for f, ok in r.items():
            c = out.setdefault(f, [0, 0])
            c[0] += int(ok)
            c[1] += 1
    total = [sum(v[0] for v in out.values()), sum(v[1] for v in out.values())]
    return {**{f: tuple(v) for f, v in out.items()}, "overall": tuple(total)}
