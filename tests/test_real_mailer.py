"""Explicit Gmail preview transport; all provider calls are mocked."""
import base64
from email import policy
from email.parser import BytesParser
import io
import json
from unittest.mock import Mock

from PIL import Image
import pytest

from backend import config, mailer


@pytest.fixture(autouse=True)
def isolate_sender(monkeypatch, tmp_path):
    monkeypatch.delenv("GMAIL_TOKEN_JSON", raising=False)
    monkeypatch.delenv("GMAIL_TOKEN_FILE", raising=False)
    monkeypatch.setattr(mailer, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(mailer, "build", Mock(side_effect=AssertionError("No real network in tests")))


def token_info(**overrides):
    return {"refresh_token": "fake-refresh-not-a-secret", "client_id": "fake-client",
            "client_secret": "fake-client-secret", "scopes": mailer.SCOPES, **overrides}


def test_status_without_token_is_safe_and_does_not_use_network():
    status = mailer.real_email_status()
    assert status["configured"] is False
    assert status["provider"] == "gmail"
    assert "Connect a Gmail sender" in status["message"]
    mailer.build.assert_not_called()


def test_default_token_file_is_root_relative_even_after_chdir(tmp_path, monkeypatch):
    (tmp_path / "token.json").write_text(json.dumps(token_info()), encoding="utf-8")
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert mailer.real_email_status()["configured"] is True
    assert mailer._credentials().client_id == "fake-client"
    mailer.build.assert_not_called()


def test_configured_relative_file_and_json_precedence(tmp_path, monkeypatch):
    (tmp_path / "sender.json").write_text(json.dumps(token_info(client_id="file-client")), encoding="utf-8")
    monkeypatch.setenv("GMAIL_TOKEN_FILE", "sender.json")
    assert mailer._credentials().client_id == "file-client"
    monkeypatch.setenv("GMAIL_TOKEN_JSON", json.dumps(token_info(client_id="env-client")))
    assert mailer._credentials().client_id == "env-client"
    assert "env-client" not in str(mailer.real_email_status())


@pytest.mark.parametrize("bad_value", ["TOP_SECRET malformed JSON", "[]", "null", '{}',
    json.dumps(token_info(refresh_token="")), json.dumps(token_info(scopes=["openid"])),
    json.dumps(token_info(expiry="not-a-date")), json.dumps(token_info(expiry=123)),
    json.dumps(token_info(token={"value": "TOP_SECRET"}))])
def test_malformed_credentials_report_only_actionable_safe_error(bad_value, monkeypatch):
    monkeypatch.setenv("GMAIL_TOKEN_JSON", bad_value)
    result = mailer.real_email_status()
    assert result["configured"] is False
    assert "Reconnect" in result["message"]
    assert "TOP_SECRET" not in str(result)
    assert "fake-client-secret" not in str(result)
    with pytest.raises(mailer.MailerConfigurationError):
        mailer._service()


def test_service_has_finite_timeout_and_does_not_broaden_scopes(monkeypatch):
    monkeypatch.setenv("GMAIL_TOKEN_JSON", json.dumps(token_info()))
    build = Mock(return_value=object())
    monkeypatch.setattr(mailer, "build", build)
    mailer._service()
    kwargs = build.call_args.kwargs
    assert kwargs["http"].http.timeout == 30
    assert kwargs["http"].credentials.scopes == mailer.SCOPES
    assert kwargs["cache_discovery"] is False


@pytest.mark.parametrize("address", [
    "", "someone", "a@localhost", "a@example.com,b@example.com", "a@example.com; b@example.com",
    "Name <a@example.com>", "a@example.com\r\nBcc:b@example.com", " a@example.com\n",
    "a..b@example.com", ".a@example.com", "a.@example.com", "a@-example.com", "a@example..com",
    "a" * 65 + "@example.com", "a@" + "b" * 64 + ".com", "a@exämple.com",
])
def test_recipient_validation_blocks_multiple_addresses_and_injection(address):
    with pytest.raises(ValueError):
        mailer.validate_recipient(address)


def test_recipient_normalization():
    assert mailer.validate_recipient(" Student+Preview@Example.COM ") == "student+preview@example.com"


def test_real_send_builds_related_html_with_embedded_jpeg_and_no_retries(monkeypatch):
    service = Mock()
    request = service.users.return_value.messages.return_value.send.return_value
    request.execute.return_value = {"id": "gmail-preview-1"}
    monkeypatch.setattr(mailer, "_service", lambda: service)
    picture = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(picture, format="JPEG")
    content = picture.getvalue()
    result = mailer.send_real("Student@Example.com", "[Preview] Campus Weekly",
                             '<p>Approved story</p><img src="cid:poster-1@boardcast">',
                             {"poster-1@boardcast": content})
    assert result == "gmail-preview-1"
    request.execute.assert_called_once_with(num_retries=0)
    sent = service.users.return_value.messages.return_value.send.call_args.kwargs
    assert sent["userId"] == "me"
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(sent["body"]["raw"]))
    assert message["To"] == "student@example.com"
    assert message["Subject"] == "[Preview] Campus Weekly"
    assert message.get_content_type() == "multipart/related"
    parts = list(message.walk())
    assert any(p.get_content_type() == "text/plain" and "does not subscribe" in p.get_content() for p in parts)
    assert any(p.get_content_type() == "text/html" and "Approved story" in p.get_content() for p in parts)
    poster = next(p for p in parts if p.get_content_type() == "image/jpeg")
    assert poster["Content-ID"] == "<poster-1@boardcast>"
    assert poster.get_content_disposition() == "inline"
    assert poster.get_payload(decode=True) == content
    assert not (config.LOCAL_DATA_DIR / "outbox").exists()


def test_ordinary_local_send_stays_in_outbox_even_with_gmail_credentials(monkeypatch):
    monkeypatch.setenv("GMAIL_TOKEN_JSON", json.dumps(token_info()))
    forbidden = Mock(side_effect=AssertionError("Ordinary local sends must not contact Gmail"))
    monkeypatch.setattr(mailer, "_service", forbidden)
    name = mailer.send("student@example.com", "Campus Weekly", "<p>Local copy</p>", "")
    assert "Local copy" in (config.LOCAL_DATA_DIR / "outbox" / name).read_text(encoding="utf-8")
    forbidden.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"subject": "Subject\r\nBcc:bad@example.com"},
    {"inline_images": {"id\r\nX-Header:bad": b"image"}},
    {"to": "a@example.com,b@example.com"},
])
def test_bad_headers_are_rejected_before_provider_is_constructed(kwargs, monkeypatch):
    service = Mock(side_effect=AssertionError("Must reject before network"))
    monkeypatch.setattr(mailer, "_service", service)
    values = {"to": "student@example.com", "subject": "[Preview] Campus Weekly", "html": "<p>Approved</p>"}
    values.update(kwargs)
    with pytest.raises(ValueError):
        mailer.send_real(**values)
    service.assert_not_called()


def test_transport_does_not_retry_an_ambiguous_timeout(monkeypatch):
    service = Mock()
    execute = service.users.return_value.messages.return_value.send.return_value.execute
    execute.side_effect = TimeoutError("provider may have accepted the message")
    monkeypatch.setattr(mailer, "_service", lambda: service)
    with pytest.raises(TimeoutError):
        mailer.send_real("student@example.com", "[Preview] Campus Weekly", "<p>Approved</p>")
    execute.assert_called_once_with(num_retries=0)


def test_gmail_helper_can_be_imported_without_starting_oauth(tmp_path, monkeypatch, capsys):
    from scripts import get_gmail_token
    monkeypatch.setattr(get_gmail_token, "PROJECT_ROOT", tmp_path)
    assert get_gmail_token.main([]) == 1
    assert "Desktop OAuth credentials were not found" in capsys.readouterr().out
    assert not (tmp_path / "token.json").exists()
