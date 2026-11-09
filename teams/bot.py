"""BoardCast in Microsoft Teams (Microsoft 365 Agents SDK + FastAPI).

    uvicorn teams.bot:app --port 3978        # Teams / Agents Playground post to /api/messages

Teams -> /api/messages (Microsoft-signed JWT, checked by the SDK)
      -> who is this? their APU email from Teams (conversation member lookup)
      -> poster photos downloaded from Teams
      -> teams/bridge.py runs the same ADK agents as `adk web` / Gemini Enterprise
      -> the agents' tools call the BoardCast API, which checks the user's role.

Settings (env or .env):
    TEAMS_BOT_ID, TEAMS_BOT_SECRET, TEAMS_TENANT_ID   bot registration (Teams Developer Portal)
    TEAMS_LOCAL=true                                  no registration: Agents Playground on a laptop
    DEV_USER_EMAIL                                    who you are in the Playground (local only)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

try:  # local runs: read the repo's .env (Cloud Run sets real env vars)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from fastapi import FastAPI, Request
from microsoft_agents.activity import ActivityTypes
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (AgentApplication, AgentAuthConfiguration, MemoryStorage,
                                           TurnContext, TurnState)
from microsoft_agents.hosting.core.app.m365_attachment_downloader import M365AttachmentDownloader
from microsoft_agents.hosting.fastapi import CloudAdapter, jwt_authorization_decorator, start_agent_process

from .bridge import AgentBridge, Upload, usable_uploads

log = logging.getLogger("boardcast.teams")

BOT_ID = os.getenv("TEAMS_BOT_ID", "")
BOT_SECRET = os.getenv("TEAMS_BOT_SECRET", "")
TENANT_ID = os.getenv("TEAMS_TENANT_ID", "")
LOCAL = os.getenv("TEAMS_LOCAL", "false").lower() == "true"
DEV_USER_EMAIL = os.getenv("DEV_USER_EMAIL", "")

if not BOT_ID and not LOCAL:
    raise RuntimeError("Set TEAMS_BOT_ID / TEAMS_BOT_SECRET / TEAMS_TENANT_ID, or TEAMS_LOCAL=true on a laptop.")

# No registration (laptop + Agents Playground): anonymous requests are accepted. Never on Cloud Run:
# there the SDK only accepts requests signed by Microsoft for this bot id.
auth_config = AgentAuthConfiguration(client_id=BOT_ID or None, client_secret=BOT_SECRET or None,
                                     tenant_id=TENANT_ID or None, anonymous_allowed=not BOT_ID)
connections = MsalConnectionManager(connections_configurations={"SERVICE_CONNECTION": auth_config})
adapter = CloudAdapter(connection_manager=connections)

bot = AgentApplication[TurnState](
    storage=MemoryStorage(),
    connection_manager=connections,
    file_downloaders=[M365AttachmentDownloader(connections, use_anonymous=not BOT_ID)],
    # Gemini + tool calls can take longer than Teams' 15 s request limit: answer the request
    # at once and reply when ready (needs the bot id; Cloud Run runs with CPU always on).
    long_running_messages=bool(BOT_ID),
    bot_app_id=BOT_ID,
)
_bridge: AgentBridge | None = None

WELCOME = (
    "Hi, I'm **BoardCast**, the APU Campus Weekly assistant.\n\n"
    "- **Club reps:** attach your poster as an image (such as PNG or JPG) and say what it is "
    "(event, announcement or achievement). "
    "I'll read it, ask only for what's missing, and send it to Student Affairs.\n"
    "- **Student Affairs:** ask *\"what's waiting for review?\"* to approve, reject or hold this week's issue.\n\n"
    "I will never ask for your password."
)


def bridge() -> AgentBridge:
    global _bridge
    if _bridge is None:
        _bridge = AgentBridge()
    return _bridge


def sniff_mime(data: bytes, declared: str) -> str:
    """Teams labels every image as image/png and SharePoint files as octet-stream; trust the bytes."""
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "image/heic"
    return declared


async def user_email(context: TurnContext) -> str | None:
    """The signed-in Teams user's email, as Microsoft reports it (not anything they typed).
    Teams' "get member" API returns email / userPrincipalName as extra fields on the member."""
    sender = context.activity.from_property
    email = None
    try:
        client = context.turn_state.get("ConnectorClient")
        member = await client.conversations.get_conversation_member(context.activity.conversation.id, sender.id)
        extra = member.model_extra or {}
        email = extra.get("email") or extra.get("userPrincipalName")
    except Exception as e:  # noqa: BLE001  Playground / non-Teams channels
        log.info("member lookup failed (%s)", e)
    if not email and LOCAL:
        email = DEV_USER_EMAIL
    return email.lower() if email else None


def wrong_tenant(context: TurnContext) -> bool:
    tenant = (context.activity.conversation.tenant_id if context.activity.conversation else None)
    return bool(TENANT_ID and tenant and tenant != TENANT_ID)


@bot.conversation_update("membersAdded")
async def on_members_added(context: TurnContext, _state: TurnState):
    for member in context.activity.members_added or []:
        if member.id != context.activity.recipient.id:
            await context.send_activity(WELCOME)


@bot.activity(ActivityTypes.message)
async def on_message(context: TurnContext, state: TurnState):
    if wrong_tenant(context):
        await context.send_activity("BoardCast is only available to APU accounts.")
        return
    email = await user_email(context)
    if not email:
        await context.send_activity("I couldn't confirm your APU account, so I can't help with this. Please try again later.")
        return

    files = state.temp.input_files or []
    uploads = [Upload(f.content, sniff_mime(f.content, f.content_type), f.filename or "poster")
               for f in files if f.content and not f.content_type.startswith("application/vnd.microsoft")]
    uploads, dropped = usable_uploads(uploads)
    text = (context.activity.text or "").strip()
    if dropped:
        await context.send_activity("Attachments not used:\n" + "\n".join(f"- {reason}" for reason in dropped))
        if not uploads:
            return  # ask for a replacement image without sending the rejected file to Gemini

    try:
        reply = await bridge().ask(user_email=email, conversation_id=context.activity.conversation.id,
                                   text=text, uploads=uploads)
    except Exception:  # noqa: BLE001
        log.exception("agent failed for %s", email)
        reply = "Sorry, something went wrong on my side. Please try again in a minute."
    await context.send_activity(reply)


@bot.error
async def on_error(context: TurnContext, error: Exception):
    log.exception("turn failed: %s", error)
    await context.send_activity("Sorry, something went wrong on my side.")


app = FastAPI(title="BoardCast Teams bot")
app.state.agent_configuration = connections.get_default_connection_configuration()


@app.post("/api/messages")
@jwt_authorization_decorator
async def messages(request: Request):
    return await start_agent_process(request, bot, adapter)


@app.get("/healthz")
def health():
    return {"ok": True, "registered": bool(BOT_ID)}
