"""Loads prompts/*.md for the agent. Looks in the repo's prompts/ folder when running
locally, and in a copy next to this file when deployed (infra/deploy_agent.sh copies it)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CANDIDATES = [_HERE / "prompts", _HERE.parent.parent / "prompts"]


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str


def load(name: str) -> Prompt:
    folder = next((p for p in _CANDIDATES if p.is_dir()), None)
    if folder is None:
        raise FileNotFoundError("prompts/ not found next to the agent or in the repo root")
    raw = (folder / f"{name}.md").read_text(encoding="utf-8")
    meta, body = {}, raw
    if raw.startswith("---"):
        _, header, body = raw.split("---", 2)
        for line in header.strip().splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return Prompt(meta.get("name", name), meta.get("version", "v0"), body.strip())
