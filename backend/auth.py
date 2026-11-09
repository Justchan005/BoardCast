"""Two checks on every protected call:

1. The caller is a trusted service (the ADK agent or Cloud Scheduler), proven by a
   Google-signed ID token. Nobody else can reach /items or /issues.
2. The end user the agent is acting for has the right role in Firestore `roles`.
   The UI hiding a button is NOT security; this is.
"""
from __future__ import annotations

from fastapi import Header, HTTPException
from google.auth.transport import requests as g_requests
from google.oauth2 import id_token

from . import config, store

_req = g_requests.Request()


def _verify_service_token(authorization: str | None, allowed: set[str]) -> str:
    if config.DEV_MODE:
        return "dev"
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing service token")
    token = authorization.removeprefix("Bearer ")
    try:
        claims = id_token.verify_oauth2_token(token, _req, audience=config.API_AUDIENCE)
    except ValueError:
        raise HTTPException(401, "Invalid service token")
    email = claims.get("email", "")
    if email not in allowed:
        raise HTTPException(403, "Caller is not an allowed service")
    return email


def agent_caller(authorization: str | None = Header(default=None)) -> str:
    """Dependency: request must come from the ADK agent's service account."""
    return _verify_service_token(authorization, {config.AGENT_SERVICE_ACCOUNT})


def scheduler_caller(authorization: str | None = Header(default=None)) -> str:
    """Dependency: request must come from Cloud Scheduler (or the agent, for manual sends)."""
    return _verify_service_token(
        authorization, {config.SCHEDULER_SERVICE_ACCOUNT, config.AGENT_SERVICE_ACCOUNT}
    )


def web_user(authorization: str | None = Header(default=None),
             x_dev_user: str | None = Header(default=None)) -> str:
    """Dependency for the /admin web pages: the person signed in with Google
    (Google Identity Services). Returns their verified email."""
    if config.DEV_MODE and x_dev_user:
        return x_dev_user.lower()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Please sign in")
    try:
        claims = id_token.verify_oauth2_token(authorization.removeprefix("Bearer "), _req,
                                              audience=config.OAUTH_CLIENT_ID)
    except ValueError:
        raise HTTPException(401, "Sign-in expired, please sign in again")
    if not claims.get("email_verified"):
        raise HTTPException(401, "Email not verified")
    return claims["email"].lower()


def require_role(user_email: str, *roles: str) -> dict:
    """Look up the signed-in user's role; raise 403 if it isn't one of `roles`."""
    record = store.get_role(user_email) if user_email else None
    if not record or record.get("role") not in roles:
        raise HTTPException(403, "You don't have permission to do this.")
    # An unassigned rep must never match staff-owned items whose club_id is also None.
    if record.get("role") == "club_rep":
        club_id = record.get("club_id")
        if not isinstance(club_id, str) or not club_id.strip():
            raise HTTPException(403, "Ask Student Affairs to assign your account to a club before continuing.")
    return record
