"""Poster / text -> validated JSON with Gemini structured output.

Gemini only reads what is on the poster. Every number, date and link it returns is
checked again in checks.py, and QR codes are decoded in code (qr.py), not by the model.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional, Type

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError
from zoneinfo import ZoneInfo

from shared import prompts
from shared.schemas import AchievementExtraction, AnnouncementExtraction, EventExtraction, ItemType

from . import audit, config, trace

_client: Optional[genai.Client] = None

SCHEMAS: dict[ItemType, Type[BaseModel]] = {
    ItemType.EVENT: EventExtraction,
    ItemType.ANNOUNCEMENT: AnnouncementExtraction,
    ItemType.ACHIEVEMENT: AchievementExtraction,
}



def ai_configured() -> bool:
    """True when a Gemini credential is set. False means manual entry only, clearly labelled."""
    return bool(config.GEMINI_API_KEY or (config.USE_VERTEX and config.PROJECT_ID))


def client() -> genai.Client:
    global _client
    if _client is None:
        if config.USE_VERTEX and config.PROJECT_ID:   # Cloud Run: service account + project
            _client = genai.Client(vertexai=True, project=config.PROJECT_ID, location=config.LOCATION)
        elif config.USE_VERTEX:                         # laptop: Agent Platform API key (express mode)
            _client = genai.Client(vertexai=True, api_key=config.GEMINI_API_KEY)
        else:                                           # AI Studio Gemini API key
            _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def extract(item_type: ItemType, *, image: bytes | None = None, mime_type: str = "image/jpeg",
            text: str | None = None, year: int | None = None, retries: int = 1) -> BaseModel:
    schema = SCHEMAS[item_type]
    prompt = prompts.load("extract")
    if year is None:   # posters rarely print the year: use the current one in Malaysia time
        year = datetime.now(ZoneInfo(config.TIMEZONE)).year
    parts: list = [prompt.render(year=year), f"Item type: {item_type.value}"]
    if image:
        parts.append(types.Part.from_bytes(data=image, mime_type=mime_type))
    if text:
        parts.append(text)

    last_error: Exception | None = None
    source = f"{prompt.name}@{prompt.version}"
    for attempt in range(1, retries + 2):
        started = time.perf_counter()
        try:
            resp = client().models.generate_content(
                model=config.GEMINI_MODEL,
                contents=parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0,
                ),
            )
        except Exception as e:   # network, quota, credentials: reported to the caller, and traced
            trace.note_model_call(purpose="extract", prompt=source, model=config.GEMINI_MODEL, started=started,
                                  error=e, summary="Gemini could not be reached to read the poster",
                                  detail={"attempt": attempt, "item_type": item_type.value})
            raise
        try:
            # .parsed is already a schema instance when the SDK could parse it
            result = resp.parsed if isinstance(resp.parsed, schema) else schema.model_validate_json(resp.text)
        except (ValidationError, ValueError, TypeError) as e:   # TypeError: an empty (blocked) response
            last_error = e
            trace.note_model_call(purpose="extract", prompt=source, model=config.GEMINI_MODEL, started=started,
                                  resp=resp, error=e, summary="Output failed schema validation; retried",
                                  detail={"attempt": attempt, "item_type": item_type.value})
            continue
        trace.note_model_call(purpose="extract", prompt=source, model=config.GEMINI_MODEL, started=started,
                              resp=resp, summary=f"Read the poster into the {schema.__name__} schema",
                              detail={"attempt": attempt, "item_type": item_type.value,
                                      "temperature": 0, "output_check": "Pydantic schema validation"})
        audit.log("gemini", "extract", None, {"prompt": source, "model": config.GEMINI_MODEL})
        return result
    raise ValueError(f"Gemini output failed schema validation: {last_error}")
