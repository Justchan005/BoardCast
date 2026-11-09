"""Shared data schemas for BoardCast.

These Pydantic models are the single source of truth for:
- Gemini structured output (response_schema)
- ADK tool arguments (function calling)
- Firestore documents
- Email / web magazine rendering
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


class ItemType(str, Enum):
    EVENT = "event"
    ANNOUNCEMENT = "announcement"
    ACHIEVEMENT = "achievement"


class ItemStatus(str, Enum):
    DRAFT = "draft"              # extracted, still missing fields
    PENDING = "pending"          # waiting for Student Affairs
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"      # went out in an issue


class Role(str, Enum):
    CLUB_REP = "club_rep"
    STUDENT_AFFAIRS = "student_affairs"


Category = Literal["tech", "career", "sports", "arts", "culture", "volunteer", "social", "academic", "other"]


class Registration(BaseModel):
    required: bool = Field(description="True if students must register before attending")
    link: Optional[str] = Field(default=None, description="Optional registration URL exactly as provided or decoded from the QR code; null if absent, even when registration is required")

    @field_validator("link")
    @classmethod
    def _optional_link(cls, value: Optional[str]) -> Optional[str]:
        return (value.strip() or None) if value is not None else None


# ---------- What Gemini extracts from a poster (no ids, no status) ----------

class EventExtraction(BaseModel):
    title: str
    organiser: Optional[str] = None
    date: Optional[dt.date] = Field(default=None, description="Event date; null if not on the poster")
    start_time: Optional[dt.time] = Field(default=None, description="24-hour HH:MM, Malaysia time, no time zone")
    end_time: Optional[dt.time] = Field(default=None, description="24-hour HH:MM, Malaysia time, no time zone")
    venue: Optional[str] = None
    fee: Optional[str] = Field(default=None, description='e.g. "Free", "RM10", "RM20/team"; null if not stated')
    category: Category = "other"
    audience: Optional[str] = Field(default=None, description="Who it is for, if stated")
    registration: Registration = Registration(required=False)
    summary: Optional[str] = Field(default=None, description="One or two sentences copied or closely paraphrased from the poster")

    @field_validator("start_time", "end_time")
    @classmethod
    def _wall_clock(cls, v: Optional[dt.time]) -> Optional[dt.time]:
        """Times on a poster are Malaysia wall-clock times. Gemini sometimes adds a time-zone
        offset (e.g. 15:00-08:00); keep the hour and minute as printed, drop the offset."""
        return v.replace(tzinfo=None, second=0, microsecond=0) if v else v


class AnnouncementExtraction(BaseModel):
    title: str
    organiser: str
    summary: str
    deadline: Optional[datetime] = None
    action_link: Optional[str] = None
    audience: Optional[str] = None


class AchievementExtraction(BaseModel):
    title: str
    team: str
    competition: str
    result: str = Field(description='e.g. "1st place", "Finalist"')
    date: Optional[dt.date] = None
    instagram_url: Optional[str] = None
    consent_confirmed: bool = False


# ---------- Stored item (Firestore: items/{id}) ----------

class Item(BaseModel):
    id: str
    type: ItemType
    status: ItemStatus
    club_id: Optional[str] = None
    submitted_by: str                      # email of the club rep / office
    data: dict                             # one of the *Extraction models, as dict
    poster_uri: Optional[str] = None       # gs://... original upload
    issues: list[str] = []                 # problems found by checks.py
    approved_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[datetime] = None
    rejection_notification: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


# ---------- Editor agent output ----------

class EditorPlan(BaseModel):
    """The only words AI writes in Campus Weekly: the top of the issue. Everything below it is
    the clubs' own submissions, laid out by code in fixed numbered sections."""
    spotlight_item_id: Optional[str] = Field(default=None, description="Id of the event to lead with")
    headline: str = Field(default="", description="At most 90 characters: the spotlight event, and how many more events follow")
    teaser: str = Field(default="", description="One sentence, at most 35 words, naming two or three other items")
    subject_line: str = Field(default="Campus Weekly", description="At most 60 characters")


class Subscriber(BaseModel):
    email: str                             # APU email, verified
    active: bool = True
