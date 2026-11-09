"""Poster / text -> validated JSON with Gemini structured output.

Gemini only reads what is on the poster. Every number, date and link it returns is
checked again in checks.py, and QR codes are decoded in code (qr.py), not by the model.
"""
from __future__ import annotations

from typing import Optional, Type

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from shared import prompts
from shared.schemas import AchievementExtraction, AnnouncementExtraction, EventExtraction, ItemType

from . import audit, config

_client: Optional[genai.Client] = None

SCHEMAS: dict[ItemType, Type[BaseModel]] = {
    ItemType.EVENT: EventExtraction,
    ItemType.ANNOUNCEMENT: AnnouncementExtraction,
    ItemType.ACHIEVEMENT: AchievementExtraction,
}



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
            text: str | None = None, year: int = 2026, retries: int = 1) -> BaseModel:
    schema = SCHEMAS[item_type]
    prompt = prompts.load("extract")
    parts: list = [prompt.render(year=year), f"Item type: {item_type.value}"]
    if image:
        parts.append(types.Part.from_bytes(data=image, mime_type=mime_type))
    if text:
        parts.append(text)

    last_error: Exception | None = None
    for _ in range(retries + 1):
        resp = client().models.generate_content(
            model=config.GEMINI_MODEL,
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0,
            ),
        )
        try:
            # .parsed is already a schema instance when the SDK could parse it
            result = resp.parsed if isinstance(resp.parsed, schema) else schema.model_validate_json(resp.text)
            audit.log("gemini", "extract", None, {"prompt": f"{prompt.name}@{prompt.version}", "model": config.GEMINI_MODEL})
            return result
        except (ValidationError, ValueError) as e:
            last_error = e
    raise ValueError(f"Gemini output failed schema validation: {last_error}")
