"""Tools the BoardCast agent can call. Each one calls the Cloud Run API, which does the
real work and re-checks the user's role. The agent never writes to the database itself.

DAY-1 SPIKE (confirm before building on it):
  1. How the signed-in Gemini Enterprise user's email reaches the agent -> _user_email()
  2. How a poster attached in the Gemini Enterprise chat reaches the agent as an
     artifact -> read_attached_poster()
  Fill in whatever the docs / a quick test shows; everything else stays the same.
"""
from __future__ import annotations

import mimetypes
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import google.auth.transport.requests
import requests
from google.adk.tools import ToolContext
from google.cloud import storage
from google.oauth2 import id_token

API_URL = os.environ.get("BOARDCAST_API_URL", "http://localhost:8080")
POSTER_BUCKET = os.environ.get("POSTER_BUCKET", "")
DEV_USER_EMAIL = os.environ.get("DEV_USER_EMAIL", "")
LOCAL_DATA_DIR = Path(os.environ.get("LOCAL_DATA_DIR") or Path(__file__).resolve().parents[2] / "local_data")


def _local_api() -> bool:
    url = urlsplit(API_URL)
    return url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}


def _headers() -> dict:
    if _local_api():
        return {}
    token = id_token.fetch_id_token(google.auth.transport.requests.Request(), API_URL)
    return {"Authorization": f"Bearer {token}"}


def _call(method: str, path: str, **kwargs) -> dict:
    payload = kwargs.get("json") or kwargs.get("params") or {}
    if not payload.get("user_email"):
        return {"error": "I couldn't verify your signed-in account. Please sign in again.", "status_code": 401}
    try:
        r = requests.request(method, f"{API_URL}{path}", headers=_headers(), timeout=60, **kwargs)
    except requests.RequestException:
        return {"error": "BoardCast is temporarily unavailable. Please try again.", "status_code": 502}
    try:
        result = r.json()
    except ValueError:
        return {"error": "BoardCast returned an unreadable response. Please try again.",
                "status_code": r.status_code if r.status_code >= 400 else 502}
    if not isinstance(result, dict):
        return {"error": "BoardCast returned an unreadable response. Please try again.", "status_code": 502}
    if r.status_code >= 400:
        return {"error": result.get("detail", "BoardCast could not complete that request."),
                "status_code": r.status_code}
    return result


def _user_email(tool_context: ToolContext) -> str:
    """Use the authenticated adapter's user id; local impersonation stays local.

    Verify the user_id mapping on Gemini Enterprise before enabling that surface.
    Session state and DEV_USER_EMAIL must never override production identity.
    """
    ctx = getattr(tool_context, "_invocation_context", None)
    user_id = getattr(ctx, "user_id", "") if ctx else ""
    if isinstance(user_id, str) and "@" in user_id:
        return user_id.strip().lower()
    cloud_runtime = os.getenv("K_SERVICE") or os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID")
    if _local_api() and not cloud_runtime:
        return (tool_context.state.get("user:email") or DEV_USER_EMAIL).strip().lower()
    return ""


def _store_poster(data: bytes, mime_type: str) -> str:
    """Saves the poster where the API can read it: the GCS bucket, or (no bucket set,
    local run) LOCAL_DATA_DIR/posters, returned as local:posters/<name>."""
    name = uuid.uuid4().hex + (mimetypes.guess_extension(mime_type or "") or ".jpg")
    if POSTER_BUCKET:
        storage.Client().bucket(POSTER_BUCKET).blob(f"posters/{name}").upload_from_string(data, content_type=mime_type)
        return f"gs://{POSTER_BUCKET}/posters/{name}"
    folder = LOCAL_DATA_DIR / "posters"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return f"local:posters/{name}"


async def read_attached_poster(item_type: str, tool_context: ToolContext, artifact_name: str = "") -> dict:
    """Reads the poster image the user attached in this chat and extracts its details.

    Args:
        item_type: "event", "announcement" or "achievement".
        artifact_name: Exact filename in the current message's Uploaded Artifact marker.
            Required when more than one attachment could be selected; never guess by ordering.

    Returns:
        The extracted fields, any registration links decoded from QR codes, and the
        list of required fields that are still missing.
    """
    # Artifact lists are sorted by filename, not upload time. Prefer an explicit
    # name or the current invocation's upload metadata, never names[-1].
    names = await tool_context.list_artifacts()
    if not names:
        return {"error": "No poster found in this chat. Ask the user to attach the poster image."}
    if artifact_name and artifact_name not in names:
        return {"error": "That attachment isn't available. Use the exact Uploaded Artifact filename.",
                "available_artifacts": names}
    if not artifact_name:
        ctx = getattr(tool_context, "_invocation_context", None)
        content = getattr(ctx, "user_content", None)
        current = set()
        for part in getattr(content, "parts", None) or []:
            blob = getattr(part, "inline_data", None) or getattr(part, "file_data", None)
            filename = getattr(blob, "display_name", None)
            if filename in names:
                current.add(filename)
            text = getattr(part, "text", "") or ""
            current.update(name for name in names if text == f'[Uploaded Artifact: "{name}"]')
        candidates = list(current) if current else names
        if len(candidates) != 1:
            return {"error": "Choose the poster from the current message by its exact filename.",
                    "available_artifacts": candidates}
        artifact_name = candidates[0]
    part = await tool_context.load_artifact(artifact_name)
    blob = part.inline_data if part else None
    if not blob or not blob.data:
        return {"error": "The attachment could not be read. Ask the user to attach the poster as an image."}
    if not (blob.mime_type or "").lower().startswith("image/") or blob.data.lstrip().startswith(b"%PDF"):
        return {"error": "Please upload a poster image, such as PNG or JPG. "
                         "PDFs and other documents aren't supported."}
    email = _user_email(tool_context)
    if not email:
        return {"error": "I couldn't verify your signed-in account. Please sign in again.", "status_code": 401}
    uri = _store_poster(blob.data, blob.mime_type)
    result = _call("POST", "/items/extract", json={
        "user_email": email, "type": item_type, "gcs_uri": uri})
    result["poster_uri"] = uri
    return result


def submit_item(item_type: str, data: dict, tool_context: ToolContext, poster_uri: str = "",
                description: str = "", show_poster: bool = True) -> dict:
    """Submits an event, announcement or achievement for Student Affairs approval.
    Only call this after the user has confirmed every field and seen the preview.

    Args:
        item_type: "event", "announcement" or "achievement".
        data: The confirmed fields, matching the extraction for that type. registration.link is
            optional even if registration.required is true; do not require a separate URL.
        poster_uri: The poster path returned by read_attached_poster, if any.
        description: The club's own 1-2 sentence description for students, in their words
            (optional; empty means the poster's summary is used). May include an optional HTTPS URL.
        show_poster: Show the club's original poster in the newsletter. Default true (Campus Weekly
            is a poster board); false only if the club asked not to show it.

    Returns:
        The new item id, its status, and any problems that must be fixed first
        (missing fields, an unsafe link, a description that adds facts).
    """
    return _call("POST", "/items", json={
        "user_email": _user_email(tool_context), "type": item_type, "data": data,
        "poster_uri": poster_uri or None, "description": description or None, "show_poster": show_poster})


def fix_item(item_id: str, changes: dict, tool_context: ToolContext, description: str = "",
             show_poster: str = "", poster_uri: str = "") -> dict:
    """Updates an item the user already submitted, then re-runs all checks.

    Args:
        item_id: The id returned by submit_item.
        changes: Only the fields to change, e.g. {"fee": "Free"}. Use {} if only the
            description or poster choice changes.
        description: New description, or empty to keep the current one.
        show_poster: "yes" or "no" to change the poster choice, or empty to keep it.
        poster_uri: An updated poster from read_attached_poster, to replace an outdated one.
    """
    body = {"user_email": _user_email(tool_context), "type": "event", "data": changes}
    if description:
        body["description"] = description
    if show_poster in ("yes", "no"):
        body["show_poster"] = show_poster == "yes"
    if poster_uri:
        body["poster_uri"] = poster_uri
    return _call("PATCH", f"/items/{item_id}", json=body)


def list_pending(tool_context: ToolContext, status: str = "pending") -> dict:
    """Lists items waiting for review (Student Affairs) or the user's own club items (club reps).

    Args:
        status: "pending", "draft", "approved" or "rejected".
    """
    return _call("GET", "/items", params={"user_email": _user_email(tool_context), "status": status})


def decide_item(item_id: str, decision: str, tool_context: ToolContext, note: str = "",
                acknowledge_flags: bool = False) -> dict:
    """Approves or rejects a pending item. Student Affairs only; others are refused.

    Args:
        item_id: The item to decide on.
        decision: "approve" or "reject".
        note: Required reason for a rejection, emailed to the uploader and shown in the club report.
            Optional for approval. Ask the reviewer for a reason instead of inventing one.
        acknowledge_flags: Set true only after an approval was refused because the AI pre-review
            raised points to check, you read those points to the reviewer, and the reviewer
            answered that they have checked them. Never set it on the first attempt.
    """
    return _call("POST", f"/items/{item_id}/decision", json={
        "user_email": _user_email(tool_context), "decision": decision, "note": note or None,
        "acknowledge_flags": bool(acknowledge_flags)})


def hold_issue(issue_id: str, tool_context: ToolContext) -> dict:
    """Stops this week's newsletter from sending on Monday. Student Affairs only.

    Args:
        issue_id: The issue id from the Sunday preview.
    """
    return _call("POST", f"/issues/{issue_id}/hold", json={"user_email": _user_email(tool_context)})
