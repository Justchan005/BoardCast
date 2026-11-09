"""Send email through the Gmail API.

Prototype: a dedicated project Gmail account, OAuth token stored in Secret Manager
(GMAIL_TOKEN_JSON). Production: APU's official sender (see README, Production).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
log = logging.getLogger("boardcast.mailer")


def _service():
    creds = Credentials.from_authorized_user_info(json.loads(os.environ["GMAIL_TOKEN_JSON"]), SCOPES)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


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
