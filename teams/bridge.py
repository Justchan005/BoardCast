"""Runs the BoardCast ADK agents (coordinator -> intake / review) for one chat message.

The Teams bot (teams/bot.py) only translates Teams <-> this bridge, so the agents, prompts
and tools are exactly the ones used by `adk web` and Gemini Enterprise. Who the user is
(their APU email, verified by Microsoft) becomes the ADK user_id; the agent's tools pass
it to the API, which checks the role in Firestore, so Teams adds no new way in.
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

MAX_POSTER_BYTES = 15 * 1024 * 1024
POSTER_TYPES = ("image/png", "image/jpeg", "image/webp", "image/heic", "image/heif")


@dataclass
class Upload:
    data: bytes
    mime_type: str
    name: str = "poster"


def session_id_for(conversation_id: str, user_email: str) -> str:
    """One ADK session per Teams chat and user (ids must be short and URL-safe)."""
    return "teams-" + hashlib.sha256(f"{conversation_id}|{user_email}".encode()).hexdigest()[:32]


def build_message(text: str, uploads: list[Upload]) -> types.Content:
    """User text + attached posters as inline files. SaveFilesAsArtifactsPlugin (agent.py)
    stores them as session artifacts, which read_attached_poster() loads."""
    parts = [types.Part(text=text or "(sent an attachment)")]
    for u in uploads:
        parts.append(types.Part(inline_data=types.Blob(data=u.data, mime_type=u.mime_type, display_name=u.name)))
    return types.Content(role="user", parts=parts)


def usable_uploads(uploads: list[Upload]) -> tuple[list[Upload], list[str]]:
    """Keeps poster images; returns (kept, reasons for anything dropped)."""
    kept, dropped = [], []
    for u in uploads:
        if u.mime_type not in POSTER_TYPES or u.data.lstrip().startswith(b"%PDF"):
            dropped.append(f"{u.name}: please upload a poster image, such as PNG or JPG. "
                           "PDFs and other documents aren't supported.")
        elif len(u.data) > MAX_POSTER_BYTES:
            dropped.append(f"{u.name}: larger than 15 MB")
        else:
            kept.append(u)
    return kept, dropped


class AgentBridge:
    def __init__(self, runner: Runner | None = None):
        if runner is None:
            from boardcast_agent.agent import app as adk_app  # imported late: needs prompts + model env
            # In-memory sessions: chats are kept per Cloud Run instance, so the Teams
            # service runs with max-instances=1 (see infra/deploy_teams.sh).
            runner = Runner(app=adk_app, session_service=InMemorySessionService(),
                            artifact_service=InMemoryArtifactService(), auto_create_session=True)
        self.runner = runner

    async def ask(self, *, user_email: str, conversation_id: str, text: str,
                  uploads: list[Upload] | None = None) -> str:
        message = build_message(text, uploads or [])
        replies: list[str] = []
        async for event in self.runner.run_async(
                user_id=user_email.lower(), session_id=session_id_for(conversation_id, user_email),
                new_message=message):
            if event.is_final_response() and event.content and event.content.parts:
                text_out = "".join(p.text or "" for p in event.content.parts if not getattr(p, "thought", False))
                if text_out.strip():
                    replies.append(text_out.strip())
        return "\n\n".join(replies) or "Sorry, I didn't get a reply. Please try again."
