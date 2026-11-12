"""Send email through the Gmail API.

Prototype: a dedicated Gmail account with GMAIL_TOKEN_JSON or a private token file.
Ordinary local sends stay in the outbox; only explicit send_real previews contact Gmail.
Production: APU's official sender (see README, Production).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path

import httplib2
from google_auth_httplib2 import AuthorizedHttp
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
log = logging.getLogger("boardcast.mailer")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_INLINE_BYTES = 20 * 1024 * 1024


class MailerConfigurationError(ValueError):
    """A local sender setup problem whose message is safe to show to a reviewer."""


def _credentials() -> Credentials:
    """Load server-only credentials without contacting Google or exposing their contents."""
    try:
        raw = os.environ.get("GMAIL_TOKEN_JSON", "").strip()
        if not raw:
            path = Path(os.environ.get("GMAIL_TOKEN_FILE", "").strip() or "token.json")
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            raw = path.read_text(encoding="utf-8")
        info = json.loads(raw)
        if not isinstance(info, dict) or not all(
            isinstance(info.get(key), str) and info[key].strip()
            for key in ("refresh_token", "client_id", "client_secret")
        ):
            raise ValueError("Missing refresh credentials")
        if any(info.get(key) is not None and not isinstance(info[key], str) for key in ("token", "expiry")):
            raise ValueError("Malformed token metadata")
        # Passing requested scopes to Credentials does not prove a token was granted them.
        # If saved scopes are present, refuse a known incompatible token locally.
        saved_scopes = info.get("scopes")
        if saved_scopes:
            saved_scopes = saved_scopes.split() if isinstance(saved_scopes, str) else saved_scopes
            if not isinstance(saved_scopes, list) or not any(scope in saved_scopes for scope in (
                SCOPES[0], "https://www.googleapis.com/auth/gmail.compose",
                "https://www.googleapis.com/auth/gmail.modify", "https://mail.google.com/",
            )):
                raise ValueError("Sender token does not allow sending")
        return Credentials.from_authorized_user_info(info, SCOPES)
    except FileNotFoundError:
        raise MailerConfigurationError(
            "Connect a Gmail sender first. Run the Gmail connection script to create token.json."
        ) from None
    except (OSError, ValueError, TypeError, KeyError):
        raise MailerConfigurationError(
            "The Gmail sender configuration is invalid. Reconnect the sender using the Gmail connection script."
        ) from None


def real_email_status() -> dict:
    """Configuration presence only; this never refreshes a token or makes a network request."""
    try:
        _credentials()
    except MailerConfigurationError as exc:
        return {"configured": False, "provider": "gmail", "message": str(exc)}
    return {"configured": True, "provider": "gmail",
            "message": "Gmail sender configured. Google validates access when you send."}


def _service():
    http = AuthorizedHttp(_credentials(), http=httplib2.Http(timeout=30))
    return build("gmail", "v1", http=http, cache_discovery=False)


def validate_recipient(value: str) -> str:
    """Accept exactly one ordinary email address, never display names or header syntax."""
    if not isinstance(value, str) or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("Enter one email address without a display name.")
    email = value.strip()
    if len(email) > 254 or email.count("@") != 1 or not email.isascii():
        raise ValueError("Enter one valid email address.")
    local, domain = email.rsplit("@", 1)
    if (not local or len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local
            or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)):
        raise ValueError("Enter one valid email address.")
    labels = domain.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                              for part in labels):
        raise ValueError("Enter one valid email address.")
    return email.lower()


def send_real(to: str, subject: str, html: str,
              inline_images: dict[str, bytes] | None = None) -> str:
    """Explicit single-recipient preview send, including when the rest of the app is local.

    The caller owns permission checks, confirmation and durable duplicate protection.
    Ordinary send() deliberately continues to save local notifications to the outbox.
    """
    to = validate_recipient(to)
    if not isinstance(subject, str) or re.search(r"[\x00-\x1f\x7f]", subject):
        raise ValueError("The email subject contains an invalid character.")
    images = inline_images or {}
    if sum(len(content) for content in images.values()) > MAX_INLINE_BYTES:
        raise ValueError("The email posters are too large to send. Use smaller poster images.")
    msg = MIMEMultipart("related")
    msg["To"] = to
    msg["Subject"] = subject
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(
        "Campus Weekly newsletter preview. Student Affairs sent this copy to the selected address. "
        "Open the HTML version of this email to read the approved stories and embedded posters. "
        "This preview does not subscribe you to future newsletters.", "plain", "utf-8"))
    alternative.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alternative)
    for cid, content in images.items():
        if not re.fullmatch(r"[A-Za-z0-9._-]+(?:@[A-Za-z0-9.-]+)?", cid):
            raise ValueError("The email contains an invalid poster identifier.")
        part = MIMEImage(content, _subtype="jpeg")
        part.add_header("Content-ID", f"<{cid}>")
        part.add_header("Content-Disposition", "inline", filename=f"poster-{len(msg.get_payload())}.jpg")
        msg.attach(part)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    sent = _service().users().messages().send(userId="me", body={"raw": raw}).execute(num_retries=0)
    return sent["id"]


def _to_outbox(to: str, subject: str, html: str) -> str:
    """LOCAL_MODE: save the email as an HTML file you can open; never contact Gmail."""
    from . import config
    outbox = config.LOCAL_DATA_DIR / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d-%H%M%S-%f}_{re.sub(r'[^a-z0-9]+', '-', to.lower())}.html"
    head = f"<p style='font:13px monospace;background:#ffe'>To: {escape(to)}<br>Subject: {escape(subject)}</p>\n"
    (outbox / name).write_text(head + html, encoding="utf-8")
    log.info("LOCAL_MODE: email to %s saved to outbox/%s", to, name)
    return name


def send(to: str, subject: str, html: str, unsubscribe_url: str) -> str:
    from . import config
    if config.LOCAL_MODE:
        return _to_outbox(to, subject, html)
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    msg.attach(MIMEText("This email needs an HTML-capable mail app.", "plain"))
    msg.attach(MIMEText(html, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = _service().users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent["id"]
