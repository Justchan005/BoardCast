"""Load versioned prompt files from prompts/*.md.

Each file starts with a small header:
    ---
    name: extract
    version: v2
    changelog: what changed
    ---
The version is logged with every Gemini call so outputs can be traced to the prompt that made them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_CANDIDATES = [
    Path(os.environ["PROMPTS_DIR"]) if os.environ.get("PROMPTS_DIR") else None,
    Path(__file__).resolve().parent.parent / "prompts",          # repo root
    Path(__file__).resolve().parent / "prompts",                  # copied next to this file (agent deploy)
]


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str

    def render(self, **values) -> str:
        out = self.text
        for k, v in values.items():
            out = out.replace("{" + k + "}", str(v))
        return out


def _dir() -> Path:
    for p in _CANDIDATES:
        if p and p.is_dir():
            return p
    raise FileNotFoundError("prompts/ folder not found; set PROMPTS_DIR")


@lru_cache(maxsize=None)
def load(name: str) -> Prompt:
    raw = (_dir() / f"{name}.md").read_text(encoding="utf-8")
    meta, body = {}, raw
    if raw.startswith("---"):
        _, header, body = raw.split("---", 2)
        for line in header.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return Prompt(name=meta.get("name", name), version=meta.get("version", "v0"), text=body.strip())
