"""Agent trust boundaries and attachment correction regressions; no cloud calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google.genai import types

from agent.boardcast_agent import tools


@pytest.fixture(autouse=True)
def local_agent(monkeypatch):
    monkeypatch.setattr(tools, "API_URL", "http://localhost:8081")
    monkeypatch.setattr(tools, "DEV_USER_EMAIL", "local-demo@mail.apu.edu.my")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", raising=False)


def context(names, *, current_name=None, user_id="rep@mail.apu.edu.my"):
    parts = [types.Part(text=f'[Uploaded Artifact: "{current_name}"]')] if current_name else []
    return SimpleNamespace(
        state={},
        _invocation_context=SimpleNamespace(user_id=user_id, user_content=types.Content(role="user", parts=parts)),
        list_artifacts=AsyncMock(return_value=names),
        load_artifact=AsyncMock(return_value=types.Part.from_bytes(data=b"corrected poster", mime_type="image/png")),
    )


@pytest.mark.parametrize("explicit", [True, False])
def test_new_poster_wins_even_when_old_filename_sorts_last(monkeypatch, explicit):
    ctx = context(["a-corrected.png", "z-original.png"], current_name=None if explicit else "a-corrected.png")
    upload, api = Mock(return_value="local:posters/new.png"), Mock(return_value={"data": {"title": "Corrected"}})
    monkeypatch.setattr(tools, "_store_poster", upload)
    monkeypatch.setattr(tools, "_call", api)
    result = asyncio.run(tools.read_attached_poster("event", ctx, "a-corrected.png" if explicit else ""))
    ctx.load_artifact.assert_awaited_once_with("a-corrected.png")
    assert result["poster_uri"] == "local:posters/new.png"
    assert api.call_args.kwargs["json"]["user_email"] == "rep@mail.apu.edu.my"


@pytest.mark.parametrize("requested", ["", "missing.png"])
def test_ambiguous_or_unknown_poster_never_reuses_an_old_file(monkeypatch, requested):
    ctx = context(["a-corrected.png", "z-original.png"])
    upload = Mock()
    monkeypatch.setattr(tools, "_store_poster", upload)
    result = asyncio.run(tools.read_attached_poster("event", ctx, requested))
    assert "error" in result
    ctx.load_artifact.assert_not_awaited()
    upload.assert_not_called()


def test_production_identity_ignores_dev_and_session_override(monkeypatch):
    monkeypatch.setattr(tools, "API_URL", "https://boardcast.example.run.app")
    ctx = context([], user_id="clubrep@mail.apu.edu.my")
    ctx.state["user:email"] = "studentaffairs@apu.edu.my"
    assert tools._user_email(ctx) == "clubrep@mail.apu.edu.my"
    ctx._invocation_context.user_id = "opaque-user-id"
    assert tools._user_email(ctx) == ""


@pytest.mark.parametrize("runtime", ["K_SERVICE", "GOOGLE_CLOUD_AGENT_ENGINE_ID"])
def test_cloud_runtime_cannot_enable_local_identity_by_default_url(monkeypatch, runtime):
    monkeypatch.setenv(runtime, "boardcast")
    assert tools._user_email(context([], user_id="user")) == ""


def test_local_adk_keeps_demo_identity():
    ctx = context([], user_id="user")
    assert tools._user_email(ctx) == "local-demo@mail.apu.edu.my"
    ctx.state["user:email"] = "Reviewer@APU.edu.my"
    assert tools._user_email(ctx) == "reviewer@apu.edu.my"


def test_missing_identity_blocks_api_and_poster_storage(monkeypatch):
    monkeypatch.setattr(tools, "API_URL", "https://boardcast.example.run.app")
    ctx = context(["poster.png"], user_id="unknown")
    request, upload = Mock(), Mock()
    monkeypatch.setattr(tools.requests, "request", request)
    monkeypatch.setattr(tools, "_store_poster", upload)
    assert tools.submit_item("event", {"title": "Workshop"}, ctx)["status_code"] == 401
    assert asyncio.run(tools.read_attached_poster("event", ctx))["status_code"] == 401
    request.assert_not_called()
    upload.assert_not_called()


def test_localhost_prefix_is_not_an_auth_bypass(monkeypatch):
    monkeypatch.setattr(tools, "API_URL", "http://localhost.example.invalid")
    fetch = Mock(return_value="verified-service-token")
    monkeypatch.setattr(tools.id_token, "fetch_id_token", fetch)
    assert tools._headers() == {"Authorization": "Bearer verified-service-token"}
    fetch.assert_called_once()


def test_transport_failure_and_non_json_response_are_actionable(monkeypatch):
    monkeypatch.setattr(tools.requests, "request", Mock(side_effect=tools.requests.Timeout("timeout")))
    assert tools._call("GET", "/items", params={"user_email": "rep@mail.apu.edu.my"})["status_code"] == 502
    monkeypatch.setattr(tools.requests, "request", Mock(return_value=SimpleNamespace(
        status_code=503, json=Mock(side_effect=ValueError("HTML error page")))))
    assert tools._call("GET", "/items", params={"user_email": "rep@mail.apu.edu.my"})["status_code"] == 503
