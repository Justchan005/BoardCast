"""Hosted boot configuration and sample seeding, with no network or server startup."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest

from scripts import run_hosted_demo as launcher


def env(**overrides):
    return {
        "RENDER_EXTERNAL_URL": "https://boardcast-test.onrender.com",
        "DEMO_ACCESS_CODE": "judge-test-code-123456",
        "DEMO_SESSION_SECRET": "test-session-key-" * 3,
        "LINK_SECRET": "test-link-key-" * 3,
        **overrides,
    }


@pytest.mark.parametrize("origin", [
    "", "http://boardcast-test.onrender.com", "https://localhost", "https://127.0.0.1",
    "https://10.0.0.5", "https://user:pass@example.com", "https://example.com/path",
    "https://example.com?key=x", "https://example.com#x", "https://bad_host.example",
    "https://example.com:bad", "https://example.com:0", "https://[::1]",
])
def test_hosted_origin_rejects_unsafe_or_broken_links(origin):
    with pytest.raises(ValueError):
        launcher.configure_environment(env(RENDER_EXTERNAL_URL=origin))


def test_hosted_settings_isolate_storage_and_credentials():
    values = env(
        LOCAL_MODE="false", DEV_MODE="false", PREREVIEW_AUTO="true",
        LOCAL_DATA_DIR="/original-campus-data", PORT="10001",
        GOOGLE_CLOUD_PROJECT="campus-project", GOOGLE_APPLICATION_CREDENTIALS="secret.json",
        CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="another-secret.json",
        GMAIL_TOKEN_JSON="private-token", GMAIL_TOKEN_FILE="token.json",
        OAUTH_CLIENT_ID="live-client", GEMINI_API_KEY="optional-test-model-key",
    )
    settings = launcher.configure_environment(values)
    assert settings.public_url == "https://boardcast-test.onrender.com"
    assert settings.port == 10001
    assert settings.data_dir == Path("/tmp/boardcast-demo/data")
    assert values["LOCAL_DATA_DIR"] == str(settings.data_dir)
    assert values["PYTHON_DOTENV_DISABLED"] == "1"
    assert values["LOCAL_MODE"] == values["DEV_MODE"] == values["HOSTED_DEMO"] == "true"
    assert values["PREREVIEW_AUTO"] == "false"
    assert values["WEB_CONCURRENCY"] == "1"
    assert "GMAIL_TOKEN_JSON" not in values
    assert values["GMAIL_TOKEN_FILE"].endswith(".real-email-disabled")
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in values
    assert "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE" not in values
    assert values["GOOGLE_CLOUD_PROJECT"] == values["OAUTH_CLIENT_ID"] == ""
    assert values["GEMINI_API_KEY"] == "optional-test-model-key"
    assert values["GOOGLE_GENAI_USE_VERTEXAI"] == "false"


@pytest.mark.parametrize("key", ["DEMO_ACCESS_CODE", "DEMO_SESSION_SECRET", "LINK_SECRET"])
def test_missing_or_short_access_secrets_prevent_startup(key):
    for value in ("", "too-short", " " * 40):
        with pytest.raises(ValueError, match=key):
            launcher.configure_environment(env(**{key: value}))


@pytest.mark.parametrize("value", ["zero", "0", "-1", "65536"])
def test_invalid_ports_are_rejected(value):
    with pytest.raises(ValueError, match="PORT"):
        launcher.configure_environment(env(PORT=value))


def test_public_url_override_and_no_required_model_key():
    values = env(PUBLIC_BASE_URL="https://demo.example.com/")
    settings = launcher.configure_environment(values)
    assert settings.public_url == values["BOARDCAST_API_URL"] == "https://demo.example.com"
    assert "GEMINI_API_KEY" not in values


def test_loopback_and_custom_storage_require_explicit_qa_flag(tmp_path):
    values = env(PUBLIC_BASE_URL="http://127.0.0.1:8084")
    with pytest.raises(ValueError):
        launcher.configure_environment(values)
    with pytest.raises(ValueError, match="custom data folder"):
        launcher.configure_environment(env(), test_data_dir=tmp_path)
    settings = launcher.configure_environment(values, local_test=True, test_data_dir=tmp_path)
    assert settings.public_url == "http://127.0.0.1:8084"
    assert settings.data_dir == tmp_path.resolve()


def test_vertex_api_key_mode_can_be_explicit_without_cloud_project():
    values = env(GOOGLE_GENAI_USE_VERTEXAI="true", GOOGLE_CLOUD_PROJECT="must-clear")
    launcher.configure_environment(values)
    assert values["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    assert values["GOOGLE_CLOUD_PROJECT"] == ""


def test_seeding_is_offline_idempotent_and_preserves_judge_changes(tmp_path, monkeypatch):
    from backend import config, editor, extract, mailer, store
    from backend.localdb import LocalClient

    def forbidden(*args, **kwargs):
        pytest.fail("Hosted startup must not call AI or real email.")

    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://boardcast-test.onrender.com")
    monkeypatch.setattr(store, "_db", LocalClient(tmp_path / "db.json"))
    monkeypatch.setattr(extract, "client", forbidden)
    monkeypatch.setattr(editor, "plan_issue", forbidden)
    monkeypatch.setattr(mailer, "send", forbidden)
    monkeypatch.setattr(mailer, "send_real", forbidden)

    launcher.seed_demo()
    assert store.get_role("studentaffairs.demo@apu.edu.my")["role"] == "student_affairs"
    assert store.get_subscriber("student.demo@mail.apu.edu.my")["active"]
    sample = store.get_issue("sample-edition")
    assert sample["status"] == "preview"
    assert sample["week_start"] == datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()
    assert sample["editor_report"]["source"] == "sample"
    assert sample["item_ids"]
    assert all(item["data"]["title"].startswith("[SAMPLE]") for item in sample["items"])
    assert list((tmp_path / "posters").glob("*.png"))
    outbox = list((tmp_path / "outbox").glob("*.html"))
    assert len(outbox) == 1
    html = outbox[0].read_text(encoding="utf-8")
    assert "[SAMPLE]" in html
    assert "https://boardcast-test.onrender.com" in html

    store.update_item("sample-ev-4", status="rejected")
    store.upsert_subscriber({"email": "student.demo@mail.apu.edu.my", "active": False})
    original = deepcopy(store.db().data)
    # Simulate another process start reading the same filesystem, not a new demo.
    monkeypatch.setattr(store, "_db", LocalClient(tmp_path / "db.json"))
    launcher.seed_demo()
    assert store.db().data == original
    assert len(list((tmp_path / "outbox").glob("*.html"))) == 1


def test_entrypoint_uses_one_worker_and_never_starts_server_in_test(monkeypatch):
    values = env()
    monkeypatch.setattr(launcher.os, "environ", values)
    fake_main = ModuleType("backend.main")
    fake_main.app = object()
    monkeypatch.setitem(sys.modules, "backend.main", fake_main)
    import uvicorn
    calls = []
    monkeypatch.setattr(launcher, "seed_demo", lambda: calls.append("seed"))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    launcher.main([])
    assert calls == ["seed", (fake_main.app, {"host": "0.0.0.0", "port": 10000, "workers": 1})]


def owner_env(**overrides):
    return env(HOSTED_EMAIL_PREVIEW="true", DEMO_OWNER_CODE="owner-only-test-code-32-characters-minimum",
               HOSTED_EMAIL_RECIPIENT="Test.Owner@example.com", **overrides)


def test_explicit_owner_test_only_retains_json_sender_credentials():
    token = json.dumps({"refresh_token": "test-refresh", "client_id": "test-client", "client_secret": "test-secret"})
    values = owner_env(GMAIL_TOKEN_JSON=token, GMAIL_TOKEN_FILE="private-token.json",
                       GMAIL_OTHER_SECRET="unexpected", GOOGLE_APPLICATION_CREDENTIALS="adc.json")
    launcher.configure_environment(values)
    assert values["GMAIL_TOKEN_JSON"] == token
    assert values["GMAIL_TOKEN_FILE"].endswith(".real-email-disabled")
    assert "GMAIL_OTHER_SECRET" not in values
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in values
    assert values["HOSTED_EMAIL_RECIPIENT"] == "test.owner@example.com"
    assert values["HOSTED_EMAIL_PREVIEW"] == "true"


@pytest.mark.parametrize("key,value", [
    ("HOSTED_EMAIL_PREVIEW", "yes"), ("DEMO_OWNER_CODE", "short"),
    ("DEMO_OWNER_CODE", "x" * 257), ("HOSTED_EMAIL_RECIPIENT", ""),
    ("HOSTED_EMAIL_RECIPIENT", "one@example.com,two@example.com"),
    ("HOSTED_EMAIL_RECIPIENT", "Owner <one@example.com>"),
    ("HOSTED_EMAIL_RECIPIENT", "one@example.com\r\nBcc: two@example.com"),
    ("GMAIL_TOKEN_JSON", "not json"), ("GMAIL_TOKEN_JSON", "{}"),
])
def test_unsafe_owner_opt_in_stops_launcher(key, value):
    values = owner_env()
    values[key] = value
    with pytest.raises(ValueError):
        launcher.configure_environment(values)


def test_owner_and_judge_codes_must_be_different():
    values = owner_env()
    values["DEMO_ACCESS_CODE"] = values["DEMO_OWNER_CODE"]
    with pytest.raises(ValueError, match="different"):
        launcher.configure_environment(values)


def test_owner_details_do_not_enable_email_without_explicit_opt_in():
    values = owner_env(GMAIL_TOKEN_JSON="a token that must be removed")
    values["HOSTED_EMAIL_PREVIEW"] = "false"
    launcher.configure_environment(values)
    assert values["HOSTED_EMAIL_PREVIEW"] == "false"
    assert "GMAIL_TOKEN_JSON" not in values
