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
import re
from typing import Annotated, Literal

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from shared.schemas import EditorPlan

from . import config, store, tokens
from .checks import _as_date, _as_time, description_urls, instagram_link_problem, link_problem

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))

# Template filenames are selected only from this allowlist, never from saved/user input.
NEWSLETTER_TEMPLATES = (
    {"id": "classic", "label": "Classic", "description": "A clean, familiar campus digest."},
    {"id": "newspaper", "label": "Newspaper", "description": "An editorial newspaper layout for Campus Weekly."},
    {"id": "spotlight", "label": "Spotlight", "description": "A prominent lead poster followed by visual stories."},
    {"id": "bulletin", "label": "Bulletin", "description": "Compact poster-and-story rows for a quick campus catch-up."},
    {"id": "custom", "label": "Custom", "description": "Arrange reusable title, picture and story placeholders."},
)
_NEWSLETTER_FILES = {
    "classic": {"email": "weekly_email.html", "web": "magazine.html"},
    "newspaper": {"email": "weekly_email_newspaper.html", "web": "magazine_newspaper.html"},
    "spotlight": {"email": "weekly_email_variant.html", "web": "magazine_variant.html"},
    "bulletin": {"email": "weekly_email_variant.html", "web": "magazine_variant.html"},
    "custom": {"email": "weekly_email_theme.html", "web": "magazine_theme.html"},
}

DESIGN_FONTS = (
    {"id": "georgia", "label": "Georgia"}, {"id": "times", "label": "Times New Roman"},
    {"id": "arial", "label": "Arial"}, {"id": "verdana", "label": "Verdana"},
)
_FONT_STACKS = {
    "georgia": 'Georgia, "Times New Roman", serif',
    "times": '"Times New Roman", Times, serif',
    "arial": "Arial, Helvetica, sans-serif",
    "verdana": "Verdana, Geneva, sans-serif",
}
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
DesignItemId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
DesignColor = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
ThemeBlock = Literal["name", "date", "intro", "title", "picture", "details"]
ThemeCell = Annotated[list[ThemeBlock], Field(max_length=3)]


class NewsletterThemeRow(BaseModel):
    """One full-width cell or two equally sized, mobile-stacking cells."""
    model_config = ConfigDict(extra="forbid")
    cells: list[ThemeCell] = Field(min_length=1, max_length=2)


ThemeRows = Annotated[list[NewsletterThemeRow], Field(min_length=1, max_length=6)]


class NewsletterDesign(BaseModel):
    """Bounded visual choices and item references, never executable HTML or CSS."""
    model_config = ConfigDict(extra="forbid")
    headline_font: Literal["georgia", "times", "arial", "verdana"] = "arial"
    body_font: Literal["georgia", "times", "arial", "verdana"] = "arial"
    paper_color: DesignColor = "#FFFFFF"
    text_color: DesignColor = "#1B1B1B"
    accent_color: DesignColor = "#0B7BB5"
    columns: Literal[1, 2, 3] = 1
    lead_image: Literal["left", "right"] = "left"
    lead_id: DesignItemId | None = None
    story_order: list[DesignItemId] = Field(default_factory=list, max_length=200)
    column_map: dict[DesignItemId, Literal[1, 2, 3]] = Field(default_factory=dict, max_length=200)
    header_rows: ThemeRows | None = None
    story_rows: ThemeRows | None = None

    @field_validator("header_rows", "story_rows")
    @classmethod
    def complete_theme_blocks(cls, rows, info):
        if rows is not None:
            required = {"name", "date", "intro"} if info.field_name == "header_rows" else {"title", "picture", "details"}
            blocks = [block for row in rows for cell in row.cells for block in cell]
            if len(blocks) != len(required) or set(blocks) != required:
                raise ValueError("Every section placeholder must appear exactly once")
        return rows

    @field_validator("paper_color", "text_color", "accent_color")
    @classmethod
    def normalize_color(cls, value):
        return value.upper()

    @field_validator("columns", mode="before")
    @classmethod
    def integer_columns(cls, value):
        if type(value) is not int:
            raise ValueError("Column count must be an integer")
        return value

    @field_validator("column_map", mode="before")
    @classmethod
    def integer_assignments(cls, value):
        if isinstance(value, dict) and any(type(column) is not int for column in value.values()):
            raise ValueError("Column assignments must be integers")
        return value


def newsletter_template(value=None) -> str:
    """Old editions and unrecognized stored values retain the Classic layout."""
    return value if isinstance(value, str) and value in _NEWSLETTER_FILES else "classic"


def variant_style(design: dict) -> dict:
    """Derive publication surfaces and readable text for any allowed accent."""
    def rgb(value):
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))

    def blend(foreground, background, weight):
        return "#" + "".join(f"{round(a * weight + b * (1 - weight)):02X}"
                             for a, b in zip(rgb(foreground), rgb(background)))

    def linear(channel):
        value = channel / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    accent = design["accent_color"]
    luminance = sum(weight * linear(channel) for weight, channel in
                    zip((0.2126, 0.7152, 0.0722), rgb(accent)))
    return {"accent_ink": "#000000" if (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) else "#FFFFFF",
            "surface": blend(accent, design["paper_color"], 0.07),
            "line": blend(design["text_color"], design["paper_color"], 0.18),
            "muted": blend(design["text_color"], design["paper_color"], 0.76)}


def default_newsletter_design(template="classic") -> dict:
    values = {"headline_font": "arial", "body_font": "arial", "paper_color": "#FFFFFF",
              "text_color": "#1B1B1B", "accent_color": "#0B7BB5", "columns": 1,
              "lead_image": "left", "lead_id": None, "story_order": [], "column_map": {},
              "header_rows": None, "story_rows": None}
    if newsletter_template(template) == "newspaper":
        values.update(headline_font="georgia", body_font="georgia", paper_color="#F7F3E8",
                      text_color="#23231F", accent_color="#9D3229", columns=2)
    if newsletter_template(template) in ("spotlight", "bulletin"):
        values.update(accent_color="#1F4FD8", columns=2 if template == "spotlight" else 1)
        if template == "spotlight":
            values.update(headline_font="georgia")
    if newsletter_template(template) == "custom":
        values.update(header_rows=[{"cells": [["name"]]}, {"cells": [["date", "intro"]]}],
                      story_rows=[{"cells": [["picture"], ["title", "details"]]}])
    return values


def newsletter_design(value, template="classic") -> dict | None:
    """Resolve bounded design data, with safe defaults for the standalone Custom theme."""
    custom = newsletter_template(template) == "custom"
    defaults = default_newsletter_design(template)
    if value is None and not custom:
        return None
    raw = value.model_dump(exclude_unset=True) if isinstance(value, NewsletterDesign) else store.newsletter_design_from_storage(value)
    if raw is None and custom:
        raw = {}
    if not isinstance(raw, dict):
        return NewsletterDesign.model_validate(defaults).model_dump() if custom else None
    try:
        resolved = {**defaults, **raw}
        if custom:
            for field in ("header_rows", "story_rows"):
                if resolved[field] is None:
                    resolved[field] = defaults[field]
        return NewsletterDesign.model_validate(resolved).model_dump()
    except ValidationError:
        return NewsletterDesign.model_validate(defaults).model_dump() if custom else None


def default_design_fields(value, template="classic") -> dict | None:
    design = newsletter_design(value, template)
    return {key: field for key, field in design.items() if key not in ("lead_id", "story_order", "column_map")} if design else None


def publication_settings() -> dict:
    saved = store.db().collection("settings").document("publication").get().to_dict() or {}
    template = newsletter_template(saved.get("newsletter_template"))
    return {"template": template, "design": default_design_fields(saved.get("newsletter_design"), template)}


def default_newsletter_template() -> str:
    return publication_settings()["template"]


def layout_editable(issue: dict) -> bool:
    return issue.get("status") == "preview" and not issue.get("delivery_frozen")


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


def _linkify_description(value: str | None, accent: str | None = None) -> Markup:
    """Keep the club's words escaped; only turn approved HTTPS URLs into direct links."""
    text = str(value or "")
    color = accent if isinstance(accent, str) and _HEX_COLOR.fullmatch(accent) else "#0B7BB5"
    parts, cursor = [], 0
    for start, end, url in description_urls(text):
        parts.append(escape(text[cursor:start]))
        if link_problem(url) is None:
            parts.append(Markup('<a href="{}" rel="noopener noreferrer" '
                                'style="color:{};font-weight:bold;">{}</a>').format(url, color, url))
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
        "newsletter_template": newsletter_template(issue_doc.get("newsletter_template")),
        "newsletter_design": newsletter_design(issue_doc.get("newsletter_design"), issue_doc.get("newsletter_template")),
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
    newspaper_order = events + issue["achievements"] + issue["announcements"]
    newspaper_lead = spotlight or (newspaper_order[0] if newspaper_order else None)
    newspaper_stories = list(newspaper_order)
    if newspaper_lead is not None:
        newspaper_stories.remove(newspaper_lead)  # remove the lead once, retaining every other story
    design = newsletter_design(issue.get("newsletter_design"), issue.get("newsletter_template"))
    design_fonts = {key: _FONT_STACKS[design[f"{key}_font"]] for key in ("headline", "body")} if design else {}
    custom_lead, custom_columns, custom_email_columns = None, [], []
    if design is not None:
        # Stored references may outlive an item or its approval. Resolve them only
        # against this edition, deduplicate them, and always append new/unplaced items.
        by_id = {item["id"]: item for item in newspaper_order}
        custom_lead = by_id.get(design["lead_id"]) or newspaper_lead
        ordered_ids = list(dict.fromkeys([item_id for item_id in design["story_order"] if item_id in by_id]
                                        + [item["id"] for item in newspaper_order]))
        supporting = [by_id[item_id] for item_id in ordered_ids
                      if custom_lead is None or item_id != custom_lead["id"]]
        custom_columns = [[] for _ in range(design["columns"])]
        unplaced = []
        for item in supporting:
            assigned = design["column_map"].get(item["id"])
            if assigned is None:
                unplaced.append(item)
            else:
                custom_columns[min(assigned, design["columns"]) - 1].append(item)
        for item in unplaced:
            min(custom_columns, key=len).append(item)
        custom_email_columns = [list(column) for column in custom_columns[:2]]
        if len(custom_columns) == 3:
            custom_email_columns[1].extend(custom_columns[2])
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
    variant = newsletter_template(issue.get("newsletter_template"))
    variant_design = design or default_newsletter_design(variant)
    variant_columns = custom_columns if design else [newspaper_stories[index::variant_design["columns"]]
                                                     for index in range(variant_design["columns"])]
    variant_email_columns = [list(column) for column in variant_columns[:2]]
    if len(variant_columns) == 3:
        variant_email_columns[1].extend(variant_columns[2])
    variant_lead = custom_lead if design else newspaper_lead
    return {
        "issue": issue, "plan": plan, "spotlight": spotlight, "events": events, "sections": sections,
        "newspaper_lead": newspaper_lead, "newspaper_stories": newspaper_stories,
        "design": design, "design_fonts": design_fonts, "custom_lead": custom_lead,
        "custom_columns": custom_columns, "custom_email_columns": custom_email_columns,
        "variant": variant, "variant_design": variant_design, "variant_style": variant_style(variant_design),
        "variant_fonts": {key: _FONT_STACKS[variant_design[f"{key}_font"]] for key in ("headline", "body")},
        "variant_lead": variant_lead, "variant_columns": variant_columns,
        "variant_email_columns": variant_email_columns,
        "variant_stories": ([variant_lead] if variant_lead else []) +
                           [item for column in variant_columns for item in column],
        "publication_accent": design["accent_color"] if design else "#0B7BB5",
        "base": config.PUBLIC_BASE_URL,
        "stats": {"events": len(issue["events"]), "free": free,
                  "wins": len(issue["achievements"]), "opportunities": len(issue["announcements"])},
    }


def layout_items(issue: dict, plan: EditorPlan) -> tuple[list[dict], str | None]:
    """Preset-ordered metadata; custom placement lives separately in newsletter_design."""
    context = _context(issue, plan)
    lead = context["newspaper_lead"]
    ordered = ([lead] if lead else []) + [item for item in
        context["events"] + issue["achievements"] + issue["announcements"]
        if lead is None or item["id"] != lead["id"]]
    rows = [{"id": item["id"], "title": str(item["data"].get("title") or item["data"].get("team") or "Untitled"),
             "type": item["type"], "poster_url": f"{config.PUBLIC_BASE_URL}/p/{item['id']}.jpg"
                 if item.get("show_poster") and item.get("poster_uri") else None} for item in ordered]
    return rows, lead["id"] if lead else None


def render_email(issue: dict, plan: EditorPlan, subscriber: dict | None,
                 unsubscribe_url: str = "", issue_id: str = "", prefs_url: str = "") -> str:
    ctx = _context(issue, plan)
    # Links in a student's own email save the event for a 1-hour reminder (signed, per student).
    save_q = f"?s={tokens.sign(subscriber['email'], 'save')}" if subscriber and subscriber.get("email") else ""
    ctx.update(unsubscribe_url=unsubscribe_url, issue_id=issue_id, prefs_url=prefs_url, save_q=save_q)
    template = newsletter_template(issue.get("newsletter_template"))
    filename = (_NEWSLETTER_FILES[template]["email"] if template in ("spotlight", "bulletin") else
                "weekly_email_theme.html" if template == "custom" else "weekly_email_custom.html"
                if ctx["design"] is not None else _NEWSLETTER_FILES[template]["email"])
    return env.get_template(filename).render(**ctx)


def render_magazine(issue: dict, plan: EditorPlan, issue_id: str) -> str:
    ctx = _context(issue, plan)
    ctx["issue_id"] = issue_id
    template = newsletter_template(issue.get("newsletter_template"))
    filename = (_NEWSLETTER_FILES[template]["web"] if template in ("spotlight", "bulletin") else
                "magazine_theme.html" if template == "custom" else "magazine_custom.html"
                if ctx["design"] is not None else _NEWSLETTER_FILES[template]["web"])
    return env.get_template(filename).render(**ctx)


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
    from . import trace
    with trace.capture() as model_calls:
        plan, report = editor.plan_issue(items)
    # Re-running the preview replaces the older one for that week, so Monday sends the newest.
    older = (store.db().collection("issues").where("status", "==", "preview")
             .where("week_start", "==", monday.isoformat()).stream())
    for snap in older:
        store.db().collection("issues").document(snap.id).update({"status": "superseded"})
    settings = publication_settings()
    issue_id = store.save_issue({
        "week_start": monday.isoformat(),
        "status": "preview",
        "item_ids": [i["id"] for i in items],
        "items": items,
        "editor_plan": plan.model_dump(),
        "editor_report": report,
        "newsletter_template": settings["template"],
        "newsletter_design": newsletter_design(settings["design"], settings["template"]),
    })
    # The spotlight choice belongs to the edition and to the event that was chosen.
    trace.flush(model_calls, issue_id=issue_id, item_id=plan.spotlight_item_id, actor="gemini")
    return issue_id, report
