"""Atomic, bounded email verification challenges, separate from subscription state."""
from __future__ import annotations

import hmac
import math
import re
from datetime import datetime, timezone

from google.cloud import firestore

from . import config, store, tokens

RESEND_SECONDS = 60
MAX_ATTEMPTS = 5


class ChallengeThrottled(Exception):
    def __init__(self, retry_after: int, message: str):
        super().__init__(message)
        self.retry_after = max(1, retry_after)


def valid_email(email: str) -> bool:
    """Keep document ids and mail recipients to a single supported campus address."""
    local, separator, domain = email.partition("@")
    return bool(separator and re.fullmatch(r"[A-Za-z0-9.!#$%&'*+=?^_`{|}~-]+", local)
                and ("@" + domain) in config.APU_EMAIL_DOMAINS)


def _atomic(client, operation):
    if config.LOCAL_MODE:
        with client.lock:
            return operation(None)
    return firestore.transactional(operation)(client.transaction())


def _read(ref, transaction):
    snap = ref.get() if transaction is None else ref.get(transaction=transaction)
    return snap.to_dict() if snap.exists else None


def _write(ref, data, transaction, *, merge=False):
    if transaction is None:
        ref.set(data, merge=merge)
    else:
        transaction.set(ref, data, merge=merge)


def request_code(email: str) -> str:
    """Reserve one challenge and return its code for delivery; never disable a subscriber.

    Resending does not reset guesses during a live challenge window. Exhausted
    challenges cannot be replaced until they expire, including by parallel requests.
    """
    email = email.strip().lower()
    if not valid_email(email):
        raise ValueError("Please use your APU email")
    client = store.db()
    ref = client.collection("subscription_challenges").document(email)
    code, code_hash, expires = tokens.new_code()
    now = datetime.now(timezone.utc)

    def reserve(transaction):
        previous = _read(ref, transaction) or {}
        live = bool(previous.get("expires_at") and previous["expires_at"] > now)
        attempts = previous.get("attempts", 0) if live else 0
        if attempts >= MAX_ATTEMPTS:
            raise ChallengeThrottled(math.ceil((previous["expires_at"] - now).total_seconds()),
                                     "Too many incorrect codes. Wait for the current code to expire before requesting another.")
        sent_at = previous.get("requested_at")
        remaining = RESEND_SECONDS - (now - sent_at).total_seconds() if sent_at else 0
        if remaining > 0:
            raise ChallengeThrottled(math.ceil(remaining), "Please wait a minute before requesting another code.")
        # Keep the original expiry when resending, so requests cannot extend a guessing window.
        _write(ref, {"email": email, "code_hash": code_hash,
                     "expires_at": previous["expires_at"] if live else expires,
                     "requested_at": now, "attempts": attempts, "consumed": False}, transaction)
        return code

    return _atomic(client, reserve)


def verify_code(email: str, code: str) -> bool:
    """Consume a successful code and activate its subscriber in the same transaction."""
    email = email.strip().lower()
    if not valid_email(email):
        return False
    client = store.db()
    ref = client.collection("subscription_challenges").document(email)
    sub_ref = client.collection("subscribers").document(email)
    now = datetime.now(timezone.utc)
    code = code.strip()

    def consume(transaction):
        challenge = _read(ref, transaction)
        if (not challenge or challenge.get("consumed") or not challenge.get("expires_at")
                or challenge["expires_at"] <= now or challenge.get("attempts", 0) >= MAX_ATTEMPTS):
            return False
        correct = bool(re.fullmatch(r"[0-9]{6}", code) and challenge.get("code_hash")
                       and hmac.compare_digest(challenge["code_hash"], tokens.hash_code(code)))
        if not correct:
            _write(ref, {"attempts": challenge.get("attempts", 0) + 1}, transaction, merge=True)
            return False
        _write(ref, {"consumed": True, "code_hash": None}, transaction, merge=True)
        _write(sub_ref, {"email": email, "active": True, "verify_code_hash": None,
                         "verify_expires": None}, transaction, merge=True)
        return True

    return _atomic(client, consume)
