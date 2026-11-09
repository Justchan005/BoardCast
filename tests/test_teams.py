"""Teams bot: the ADK bridge and Teams-specific handling. Run: pytest -q (no Teams, no cloud)."""
import asyncio
import os
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

os.environ.setdefault("TEAMS_LOCAL", "true")

from google.genai import types                                      # noqa: E402

from teams.bridge import AgentBridge, Upload, build_message, session_id_for, usable_uploads  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32


class _Event:
    def __init__(self, text, final=True):
        self.content = types.Content(role="model", parts=[types.Part(text=text)])
        self._final = final

    def is_final_response(self):
        return self._final


class FakeRunner:
    """Stands in for the ADK Runner: records what it was asked, replies with fixed events."""
    def __init__(self, events):
        self.events, self.calls = events, []

    async def run_async(self, *, user_id, session_id, new_message):
        self.calls.append({"user_id": user_id, "session_id": session_id, "message": new_message})
        for e in self.events:
            yield e


def test_bridge_passes_teams_email_and_poster_to_the_agent():
    runner = FakeRunner([_Event("thinking", final=False), _Event("I read your poster. What is the fee?")])
    reply = asyncio.run(AgentBridge(runner).ask(
        user_email="ClubRep@Mail.APU.edu.my", conversation_id="a:1Xyz", text="New event",
        uploads=[Upload(PNG, "image/png", "poster.png")]))
    assert reply == "I read your poster. What is the fee?"
    call = runner.calls[0]
    assert call["user_id"] == "clubrep@mail.apu.edu.my"           # the API checks this email's role
    blob = call["message"].parts[1].inline_data
    assert blob.mime_type == "image/png" and blob.display_name == "poster.png"


def test_same_chat_keeps_its_session_and_users_do_not_share_one():
    a = session_id_for("conv-1", "a@mail.apu.edu.my")
    assert a == session_id_for("conv-1", "a@mail.apu.edu.my")
    assert a != session_id_for("conv-1", "b@mail.apu.edu.my")
    assert a != session_id_for("conv-2", "a@mail.apu.edu.my")
    assert a.startswith("teams-") and len(a) < 64


def test_only_posters_reach_the_agent():
    kept, dropped = usable_uploads([Upload(PNG, "image/png", "p.png"), Upload(b"x", "text/plain", "notes.txt"),
                                    Upload(b"0" * (16 * 1024 * 1024), "image/jpeg", "huge.jpg")])
    assert [u.name for u in kept] == ["p.png"]
    assert len(dropped) == 2
    assert build_message("", []).parts[0].text  # an attachment-only message still has text


def test_pdf_uploads_are_rejected_even_when_labelled_as_images():
    pdf = b"%PDF-1.7\nposter"
    kept, dropped = usable_uploads([
        Upload(pdf, "application/pdf", "poster.pdf"),
        Upload(pdf, "image/png", "renamed.png"),
        Upload(PNG, "image/png", "poster.png"),
    ])
    assert [u.name for u in kept] == ["poster.png"]
    assert len(dropped) == 2 and all("PNG or JPG" in reason for reason in dropped)


@pytest.mark.parametrize("with_image", [False, True])
def test_bot_explains_rejected_pdf_and_only_forwards_valid_images(monkeypatch, with_image):
    from teams import bot
    context = SimpleNamespace(
        activity=SimpleNamespace(text="Please add our event", conversation=SimpleNamespace(id="review-chat")),
        send_activity=AsyncMock(),
    )
    files = [SimpleNamespace(content=b"%PDF-1.7", content_type="application/pdf", filename="poster.pdf")]
    if with_image:
        files.append(SimpleNamespace(content=PNG, content_type="image/png", filename="poster.png"))
    state = SimpleNamespace(temp=SimpleNamespace(input_files=files))
    agent = SimpleNamespace(ask=AsyncMock(return_value="I read your poster."))
    monkeypatch.setattr(bot, "wrong_tenant", lambda context: False)
    monkeypatch.setattr(bot, "user_email", AsyncMock(return_value="rep@mail.apu.edu.my"))
    monkeypatch.setattr(bot, "bridge", lambda: agent)

    asyncio.run(bot.on_message(context, state))

    notice = context.send_activity.call_args_list[0].args[0]
    assert "poster.pdf" in notice and "PNG or JPG" in notice
    if with_image:
        assert [u.name for u in agent.ask.call_args.kwargs["uploads"]] == ["poster.png"]
    else:
        agent.ask.assert_not_called()


@pytest.mark.parametrize("mime_type", ["application/pdf", "image/png"])
def test_chat_agent_rejects_pdf_before_storage_or_extraction(monkeypatch, mime_type):
    from boardcast_agent import tools
    context = SimpleNamespace(
        list_artifacts=AsyncMock(return_value=["poster"]),
        load_artifact=AsyncMock(return_value=types.Part.from_bytes(data=b"%PDF-1.7", mime_type=mime_type)),
    )
    upload, api = Mock(), Mock()
    monkeypatch.setattr(tools, "_store_poster", upload)
    monkeypatch.setattr(tools, "_call", api)

    result = asyncio.run(tools.read_attached_poster("event", context))

    assert "PNG or JPG" in result["error"]
    upload.assert_not_called()
    api.assert_not_called()


def test_chat_agent_still_reads_an_image_poster(monkeypatch):
    from boardcast_agent import tools
    context = SimpleNamespace(
        state={"user:email": "rep@mail.apu.edu.my"},
        list_artifacts=AsyncMock(return_value=["poster.png"]),
        load_artifact=AsyncMock(return_value=types.Part.from_bytes(data=PNG, mime_type="image/png")),
    )
    upload = Mock(return_value="local:posters/p.png")
    api = Mock(return_value={"data": {"title": "Workshop"}})
    monkeypatch.setattr(tools, "_store_poster", upload)
    monkeypatch.setattr(tools, "_call", api)

    result = asyncio.run(tools.read_attached_poster("event", context))

    assert result == {"data": {"title": "Workshop"}, "poster_uri": "local:posters/p.png"}
    upload.assert_called_once_with(PNG, "image/png")
    api.assert_called_once_with("POST", "/items/extract", json={
        "user_email": "rep@mail.apu.edu.my", "type": "event", "gcs_uri": "local:posters/p.png"})


def test_mime_type_is_read_from_the_file_not_from_teams():
    from teams.bot import sniff_mime
    assert sniff_mime(JPEG, "image/png") == "image/jpeg"           # Teams calls every image png
    assert sniff_mime(b"%PDF-1.7", "application/octet-stream") == "application/pdf"
    assert sniff_mime(b"hello", "text/plain") == "text/plain"


def test_app_package_has_manifest_and_icons(tmp_path, monkeypatch):
    from teams import build_package
    monkeypatch.setattr(build_package, "OUT", tmp_path)
    zip_path = build_package.build("00000000-0000-0000-0000-000000000001", "https://bot.example.run.app")
    with zipfile.ZipFile(zip_path) as z:
        assert sorted(z.namelist()) == ["color.png", "manifest.json", "outline.png"]
        manifest = z.read("manifest.json").decode()
    assert "${" not in manifest and "bot.example.run.app" in manifest
