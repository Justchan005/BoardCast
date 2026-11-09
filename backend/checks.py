"""Deterministic checks run on every submission before it can be approved.

Returns a list of human-readable issues. Empty list = ready for Student Affairs.
These are plain code, not AI, so the result is the same every time.
Event and venue approval happens in the campus system before promotion in BoardCast.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import config

REQUIRED = {
    "event": ["title", "organiser", "date", "start_time", "venue", "fee"],
    "announcement": ["title", "organiser", "summary"],
    "achievement": ["title", "team", "competition", "result"],
}


def missing_fields(item_type: str, data: dict) -> list[str]:
    return [f for f in REQUIRED[item_type] if not data.get(f)]


def _web_link_problem(url: str) -> tuple[str | None, str]:
    """Reject ambiguous URL forms before using their hostname in an allowlist."""
    if not isinstance(url, str) or re.search(r"[\\\s\x00-\x1f\x7f]", url):
        return "Link is not a valid web address", ""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        # Accessing port validates malformed and out-of-range values.
        parsed.port
    except ValueError:
        return "Link is not a valid web address", ""
    if parsed.scheme != "https":
        return f"Link must start with https: {url}", host
    if (not host or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
            or any(not label or label.startswith("-") or label.endswith("-") for label in host.split("."))
            or parsed.username is not None or parsed.password is not None
            or parsed.netloc.endswith(":")):
        return "Link is not a valid web address (credentials and malformed hosts are not allowed)", host
    return None, host


def link_problem(url: str | None) -> str | None:
    if url is None or url == "":
        return None
    problem, host = _web_link_problem(url)
    if problem:
        return problem
    if not any(host == d.lower() or host.endswith("." + d.lower()) for d in config.ALLOWED_LINK_DOMAINS):
        return f"Link domain not on the allowed list, needs admin review: {host}"
    return None


def instagram_link_problem(url: str | None) -> str | None:
    """An optional achievement source must be an unambiguous HTTPS Instagram URL."""
    if url is None or url == "":
        return None
    problem, host = _web_link_problem(url)
    if problem:
        return problem
    if host != "instagram.com" and not host.endswith(".instagram.com"):
        return "Achievement link must use an Instagram domain"
    return None


def _as_date(v) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _as_time(v) -> time | None:
    if v is None or isinstance(v, time):
        return v
    return time.fromisoformat(str(v))


def in_the_past(item_type: str, data: dict) -> bool:
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    if item_type == "event":
        d = _as_date(data.get("date"))
        return bool(d and d < today)
    if item_type == "announcement" and data.get("deadline"):
        return _as_date(data["deadline"]) < today
    return False


DESCRIPTION_MAX_WORDS = 40
_number = re.compile(r"\d+")
_description_url = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def description_urls(text: str) -> list[tuple[int, int, str]]:
    """Locate written web URLs, leaving surrounding sentence punctuation as plain text."""
    urls = []
    for match in _description_url.finditer(text):
        url = match.group().rstrip(".,;:!?")
        while url and url[-1] in ")]}":
            opening = {")": "(", "]": "[", "}": "{"}[url[-1]]
            if url.count(url[-1]) <= url.count(opening):
                break
            url = url[:-1].rstrip(".,;:!?")
        urls.append((match.start(), match.start() + len(url), url))
    return urls


def description_problems(description: str | None, data: dict) -> list[str]:
    """The club's own description is shown to students as written, so it may not add facts:
    every number in its prose (dates, times, fees) must already be in the confirmed fields.
    Optional URLs are checked separately; digits inside a URL are not event facts."""
    if not description:
        return []
    problems = []
    if len(description.split()) > DESCRIPTION_MAX_WORDS:
        problems.append(f"Description is longer than {DESCRIPTION_MAX_WORDS} words")
    prose = description
    for start, end, url in reversed(description_urls(description)):
        if p := link_problem(url):
            problems.append(p)
        prose = prose[:start] + " " + prose[end:]
    known = set(_number.findall(json.dumps(data, default=str)))
    extra = sorted(set(_number.findall(prose)) - known)
    if extra:
        problems.append("Description mentions numbers not in the event details "
                        f"({', '.join(extra)}); put dates, times and fees in the fields instead")
    return problems


def run_all(item_type: str, data: dict) -> list[str]:
    issues = [f"Missing: {f}" for f in missing_fields(item_type, data)]
    if in_the_past(item_type, data):
        issues.append("Date is already in the past")
    if item_type == "event":
        reg = data.get("registration") or {}
        if p := link_problem(reg.get("link")):
            issues.append(p)
    if item_type == "announcement" and (p := link_problem(data.get("action_link"))):
        issues.append(p)
    if item_type == "achievement":
        if not data.get("consent_confirmed"):
            issues.append("Winners' consent to be featured not confirmed")
        if p := instagram_link_problem(data.get("instagram_url")):
            issues.append(p)
    return issues
