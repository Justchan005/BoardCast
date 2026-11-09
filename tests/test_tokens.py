"""Signed links: existing format, binary separators, tampering and malformed input."""
import base64
import hashlib
import hmac

import pytest

from backend import tokens

TEST_SECRET = b"boardcast-link-regression-test"


@pytest.fixture(autouse=True)
def test_signing_key(monkeypatch):
    monkeypatch.setattr(tokens, "_SECRET", TEST_SECRET)


def _old_format(payload: bytes) -> str:
    signature = hmac.new(TEST_SECRET, payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + b"." + signature).decode().rstrip("=")


@pytest.mark.parametrize("purpose", ["prefs", "verify", "save", "poster"])
def test_existing_links_work_even_when_binary_signatures_contain_periods(purpose):
    affected = 0
    for number in range(1000):
        value = f"Student.{number}@mail.apu.edu.my"
        payload = f"{purpose}:{value.lower()}".encode()
        link = _old_format(payload)
        raw = base64.urlsafe_b64decode(link + "=" * (-len(link) % 4))
        affected += b"." in raw[-16:]
        assert tokens.sign(value, purpose) == link  # previously sent links need no migration
        assert tokens.verify(link, purpose) == value.lower()
    assert affected > 0


def test_padded_links_are_still_accepted():
    link = tokens.sign("student@mail.apu.edu.my")
    assert tokens.verify(link + "=" * (-len(link) % 4)) == "student@mail.apu.edu.my"


@pytest.mark.parametrize("invalid", ["", ".", "!invalid!", "abc", "☃", None, b"bytes"])
def test_malformed_links_return_none_without_crashing(invalid):
    assert tokens.verify(invalid) is None


@pytest.mark.parametrize("payload", [b"prefs:\xff", b"prefs", b"prefs:", b":student@mail.apu.edu.my"])
def test_signed_but_invalid_payloads_are_rejected(payload):
    assert tokens.verify(_old_format(payload)) is None


def test_modified_recipient_signature_and_purpose_are_rejected():
    link = tokens.sign("student@mail.apu.edu.my")
    raw = base64.urlsafe_b64decode(link + "=" * (-len(link) % 4))
    changed = raw.replace(b"student@", b"another@", 1)
    assert tokens.verify(base64.urlsafe_b64encode(changed).decode()) is None
    for index in range(1, 17):
        changed = bytearray(raw)
        changed[-index] ^= 1
        assert tokens.verify(base64.urlsafe_b64encode(changed).decode()) is None
    assert tokens.verify(link, "verify") is None
    assert tokens.verify(link + "!") is None
    assert tokens.verify(base64.urlsafe_b64encode(raw[:-17] + b":" + raw[-16:]).decode()) is None
