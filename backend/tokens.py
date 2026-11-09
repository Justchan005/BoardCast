"""Signed links and verification codes.

- Email links (preferences, unsubscribe) carry an HMAC-signed token instead of a raw email,
  so nobody can change or unsubscribe someone else.
- Subscribe codes are 6 digits, stored only as a hash, valid for 15 minutes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from . import config  # noqa: F401  (loads .env before LINK_SECRET is read)

_SECRET = os.getenv("LINK_SECRET", "dev-only-change-me").encode()
_SIGNATURE_BYTES = 16


def sign(email: str, purpose: str = "prefs") -> str:
    msg = f"{purpose}:{email.lower()}".encode()
    sig = hmac.new(_SECRET, msg, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
    return base64.urlsafe_b64encode(msg + b"." + sig).decode().rstrip("=")


def verify(token: str, purpose: str = "prefs") -> str | None:
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        # The binary signature can itself contain periods. Read its fixed length so links
        # already sent keep working, including ones the old separator-based parser rejected.
        boundary = len(raw) - _SIGNATURE_BYTES - 1
        if boundary <= 0 or raw[boundary:boundary + 1] != b".":
            return None
        msg, sig = raw[:boundary], raw[boundary + 1:]
        expected = hmac.new(_SECRET, msg, hashlib.sha256).digest()[:_SIGNATURE_BYTES]
        if not hmac.compare_digest(sig, expected):
            return None
        p, separator, email = msg.decode("utf-8").partition(":")
    except (ValueError, TypeError):
        return None
    if p != purpose or not separator or not email:
        return None
    return email


def new_code() -> tuple[str, str, datetime]:
    code = f"{secrets.randbelow(1_000_000):06d}"
    return code, hash_code(code), datetime.now(timezone.utc) + timedelta(minutes=15)


def hash_code(code: str) -> str:
    return hmac.new(_SECRET, code.encode(), hashlib.sha256).hexdigest()
