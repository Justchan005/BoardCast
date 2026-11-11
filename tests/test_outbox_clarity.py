"""Readable local outbox metadata without interpreting or exposing body HTML."""
import os
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from backend import checks, config, extract, main, submission


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'LOCAL_MODE', True)
    monkeypatch.setattr(config, 'LOCAL_DATA_DIR', tmp_path)
    monkeypatch.setattr(extract, 'ai_configured', lambda: False)
    (tmp_path / 'outbox').mkdir()
    with TestClient(main.app) as client:
        yield client


def save_message(name, subject, recipient, body='<p>Message body</p>'):
    path = config.LOCAL_DATA_DIR / 'outbox' / name
    path.write_text(f"<p style='font:13px monospace;background:#ffe'>To: {escape(recipient)}"
                    f"<br>Subject: {escape(subject)}</p>\n{body}", encoding='utf-8')
    return path


def test_outbox_shows_subject_recipient_and_local_saved_time(client):
    path = save_message('20261111-010000_student.html', 'Campus Weekly — Clubs & events',
                        'student@mail.apu.edu.my')
    timestamp = datetime(2026, 11, 11, 1, 0, tzinfo=timezone.utc).timestamp()
    os.utime(path, (timestamp, timestamp))
    response = client.get('/demo/outbox')
    assert response.status_code == 200
    assert 'Campus Weekly — Clubs &amp; events' in response.text
    assert 'To: student@mail.apu.edu.my' in response.text
    assert '11 Nov 2026, 09:00 AM MYT' in response.text
    assert 'datetime="2026-11-11T09:00:00+08:00"' in response.text
    assert 'Newsletter · Latest' in response.text
    assert 'Saved file details' in response.text


def test_outbox_header_is_escaped_and_does_not_parse_body_metadata(client):
    save_message('20261111-student.html', '<script>alert("x")</script>', '<img src=x>',
                 '<p>To: body-private@example.com<br>Subject: Fake body subject</p>')
    response = client.get('/demo/outbox')
    assert '<script>alert(' not in response.text
    assert '<img src=x>' not in response.text
    assert '&lt;script&gt;' in response.text
    assert 'body-private@example.com' not in response.text
    assert 'Fake body subject' not in response.text


def test_outbox_verification_overview_omits_code(client):
    save_message('20261111-code.html', 'Your BoardCast code: 654321', 'student@mail.apu.edu.my')
    response = client.get('/demo/outbox')
    assert 'Your BoardCast verification code' in response.text
    assert 'Verification · Latest' in response.text
    assert '654321' not in response.text


def test_outbox_legacy_and_unreadable_messages_have_honest_fallbacks(client):
    path = config.LOCAL_DATA_DIR / 'outbox' / '20261111-legacy.html'
    path.write_bytes(b'<main>\xff<p>To: not-metadata@example.com<br>Subject: Body text</p></main>')
    response = client.get('/demo/outbox')
    assert response.status_code == 200
    assert 'Saved email' in response.text and 'Recipient unavailable' in response.text
    assert 'not-metadata@example.com' not in response.text
    missing = submission._outbox_summary(path.with_name('missing.html'))
    assert missing['saved_label'] == 'Saved time unavailable'


def test_submit_progress_requirements_come_from_server_checks(client):
    import json

    class AppParser(HTMLParser):
        required = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get('id') == 'app':
                self.required = json.loads(attrs['data-required-fields'])

    response = client.get('/submit')
    parser = AppParser()
    parser.feed(response.text)
    assert parser.required == checks.REQUIRED
    assert 'id="validation-help"' in response.text
