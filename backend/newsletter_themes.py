"""Named, reusable Custom themes shared by Student Affairs.

Saving a library theme does not change a publication. Editions receive a copy of
its appearance when staff select and apply it through the publication editor.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import issue_builder, store

MAX_THEMES = 30
COLLECTION = "newsletter_themes"
REGISTRY = ("settings", "newsletter_theme_names")
_ID = re.compile(r"[a-f0-9]{32}\Z")


def normalize_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Enter a theme name")
    value = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("Theme names cannot contain control characters")
    value = " ".join(value.split())
    if not 1 <= len(value) <= 60:
        raise ValueError("Theme names must contain 1 to 60 characters")
    return value


def _name_key(name: str) -> str:
    return hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()


class NewTheme(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    design: issue_builder.NewsletterDesign

    _normalize_name = field_validator("name", mode="before")(normalize_name)


class ThemeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    revision: int = Field(strict=True, ge=1)
    design: issue_builder.NewsletterDesign | None = None

    _normalize_name = field_validator("name", mode="before")(normalize_name)

    @model_validator(mode="after")
    def design_is_not_null(self):
        if "design" in self.model_fields_set and self.design is None:
            raise ValueError("Omit design to rename a theme, or provide a complete design")
        return self


def _public_theme(theme_id: str, record: dict) -> dict | None:
    """Only expose valid library data, never stored audit or internal fields."""
    try:
        if not _ID.fullmatch(theme_id):
            return None
        revision = record.get("revision")
        if type(revision) is not int or revision < 1:
            return None
        decoded = store.newsletter_design_from_storage(record.get("newsletter_design"))
        valid = NewTheme(name=record.get("name"), design=decoded)
        return {"id": theme_id, "name": valid.name, "revision": revision,
                "design": issue_builder.default_design_fields(valid.design, "custom")}
    except (ValidationError, TypeError, AttributeError):
        return None


def list_themes() -> list[dict]:
    themes = [_public_theme(snapshot.id, snapshot.to_dict())
              for snapshot in store.db().collection(COLLECTION).limit(MAX_THEMES).stream()]
    return sorted((theme for theme in themes if theme), key=lambda theme: (theme["name"].casefold(), theme["id"]))


def create_theme(body: NewTheme, email: str) -> dict:
    theme_id = uuid.uuid4().hex
    key = (COLLECTION, theme_id)
    design = issue_builder.default_design_fields(body.design, "custom")

    def create(rows):
        registry = rows[REGISTRY] or {}
        names = dict(registry.get("names") or {})
        name_key = _name_key(body.name)
        if name_key in names:
            raise HTTPException(409, "A saved theme already uses that name. Choose another name or update that theme.")
        if len(names) >= MAX_THEMES:
            raise HTTPException(409, "You can save up to 30 themes. Update an existing theme instead.")
        if rows[key] is not None:
            raise HTTPException(409, "Could not reserve a new theme. Please try again.")
        names[name_key] = theme_id
        timestamp = store.now()
        return {REGISTRY: {"names": names}, key: {
            "name": body.name, "newsletter_design": design, "revision": 1,
            "created_at": timestamp, "updated_at": timestamp,
            "created_by": email, "updated_by": email,
        }}

    saved = store.atomic_documents([REGISTRY, key], create)
    return _public_theme(theme_id, saved[key])


def update_theme(theme_id: str, body: ThemeUpdate, email: str) -> dict:
    if not _ID.fullmatch(theme_id):
        raise HTTPException(404, "Saved theme not found")
    key = (COLLECTION, theme_id)

    def update(rows):
        current = _public_theme(theme_id, rows[key]) if rows[key] else None
        if current is None:
            raise HTTPException(404, "Saved theme not found")
        if current["revision"] != body.revision:
            raise HTTPException(409, "This theme changed since you opened it. Reload saved themes before updating it.")
        names = dict((rows[REGISTRY] or {}).get("names") or {})
        new_name_key = _name_key(body.name)
        if new_name_key in names and names[new_name_key] != theme_id:
            raise HTTPException(409, "A saved theme already uses that name. Choose another name.")
        old_name_key = _name_key(current["name"])
        if names.get(old_name_key) == theme_id:
            names.pop(old_name_key)
        names[new_name_key] = theme_id
        fields = {"name": body.name, "revision": current["revision"] + 1,
                  "updated_at": store.now(), "updated_by": email}
        if "design" in body.model_fields_set:
            fields["newsletter_design"] = issue_builder.default_design_fields(body.design, "custom")
        return {REGISTRY: {"names": names}, key: fields}

    saved = store.atomic_documents([REGISTRY, key], update)
    return _public_theme(theme_id, saved[key])
