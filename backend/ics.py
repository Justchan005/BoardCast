"""Build an .ics calendar file from an approved event. Works in Outlook, Google, Apple."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .checks import _as_date, _as_time


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_ics(item: dict) -> str:
    e = item["data"]
    tz = ZoneInfo(config.TIMEZONE)
    d, start, end = _as_date(e["date"]), _as_time(e["start_time"]), _as_time(e.get("end_time"))
    dt_start = datetime.combine(d, start, tzinfo=tz)
    dt_end = datetime.combine(d, end, tzinfo=tz) if end else dt_start + timedelta(hours=1)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//BoardCast//APU Campus Weekly//EN",
        "BEGIN:VEVENT",
        f"UID:{item['id']}@boardcast",
        f"DTSTAMP:{_fmt(datetime.now(timezone.utc))}",
        f"DTSTART:{_fmt(dt_start)}",
        f"DTEND:{_fmt(dt_end)}",
        f"SUMMARY:{_esc(e['title'])}",
        f"LOCATION:{_esc(e.get('venue') or '')}",
        f"DESCRIPTION:{_esc('By ' + e.get('organiser', ''))}",
        "BEGIN:VALARM",
        "TRIGGER:-PT1H",
        "ACTION:DISPLAY",
        "DESCRIPTION:Reminder",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"
