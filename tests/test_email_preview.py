"""Real-email previews are tested with a fake Gmail transport; never send mail."""
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from html.parser import HTMLParser
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from httplib2 import Response
from PIL import Image

from backend import config, editor, email_preview, issue_builder, mailer, main, store
from backend.localdb import LocalClient

SA = "studentaffairs.test@apu.edu.my"
REP = "club.test@mail.apu.edu.my"
TO = "reader@example.com"


@pytest.fixture
def edition(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DISABLED", "true")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "HOSTED_DEMO", False)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://127.0.0.1:8081")
    monkeypatch.setattr(store, "_db", LocalClient(tmp_path / "db.json"))
    monkeypatch.setattr(editor, "plan_issue", lambda items: (editor.fallback_plan(items), {}))
    monkeypatch.setattr(mailer, "real_email_status", lambda: {"configured": True, "provider": "gmail", "message": "Configured"})
    for address, role in [(SA, "student_affairs"), (REP, "club_rep")]:
        store.db().collection("roles").document(address).set({"role": role, "club_id": "demo"})
    (tmp_path / "posters").mkdir()
    Image.new("RGB", (40, 60), "navy").save(tmp_path / "posters" / "event.png")
    store.create_item({"id": "event", "type": "event", "status": "approved", "poster_uri": "local:posters/event.png",
        "show_poster": True, "data": {"title": "Approved workshop", "organiser": "Club", "date": "2099-11-11",
        "start_time": "16:00", "venue": "Original hall", "fee": "Free",
        "registration": {"required": True, "link": "https://forms.gle/real-registration"}}})
    store.create_item({"id": "notice", "type": "announcement", "status": "approved", "data": {
        "title": "Volunteer opportunity", "summary": "Meet the team.", "action_link": "https://forms.office.com/volunteer"}})
    issue_id, _ = issue_builder.build_preview(date(2099, 11, 9))
    calls = []
    monkeypatch.setattr(mailer, "send_real", lambda *args: calls.append(args) or "gmail-message-id")
    return issue_id, calls


@pytest.fixture
def api(edition):
    with TestClient(main.app, base_url="http://127.0.0.1:8081") as client:
        yield client


def payload(issue_id, **overrides):
    return {"recipient": TO, "confirmed": True, "expected_revision": email_preview.revision(store.get_issue(issue_id)), **overrides}


def post(api, issue_id, body=None, actor=SA, **kwargs):
    return api.post(f"/admin/api/issues/{issue_id}/email-preview", json=body or payload(issue_id),
                    headers={"X-Dev-User": actor, **kwargs})


def test_real_preview_preserves_publication_and_subscriptions_and_deduplicates(api, edition):
    issue_id, calls = edition
    original = store.get_issue(issue_id)
    result = post(api, issue_id)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "sent"
    assert "Gmail accepted" in result.json()["message"]
    assert len(calls) == 1 and calls[0][0] == TO
    assert store.get_issue(issue_id) == original
    assert store.get_item("event")["status"] == "approved"
    assert not store.get_subscriber(TO)
    assert not list((config.LOCAL_DATA_DIR / "outbox").glob("*"))
    again = post(api, issue_id)
    assert again.json()["duplicate"] and len(calls) == 1


@pytest.mark.parametrize("actor", [REP, "unknown@apu.edu.my"])
def test_only_student_affairs_can_send(api, edition, actor):
    assert post(api, edition[0], actor=actor).status_code == 403
    assert not edition[1]


def test_confirmation_and_single_address_required(api, edition):
    issue_id, calls = edition
    assert post(api, issue_id, payload(issue_id, confirmed=False)).status_code == 422
    for address in ["one@example.com,two@example.com", "Name <one@example.com>", "a@example.com\r\nBcc:b@example.com", "not-an-email"]:
        assert post(api, issue_id, payload(issue_id, recipient=address)).status_code == 422
    assert not calls


def test_connection_status_does_not_send_and_club_is_denied(api, edition):
    assert api.get("/admin/api/real-email/status", headers={"X-Dev-User": SA}).json()["configured"]
    assert api.get("/admin/api/real-email/status", headers={"X-Dev-User": REP}).status_code == 403
    assert not edition[1]


def test_rejects_public_hosts_and_cross_origin_requests(api, edition):
    issue_id, calls = edition
    assert post(api, issue_id, Host="attacker.example").status_code == 403
    assert post(api, issue_id, Origin="https://attacker.example").status_code == 403
    assert post(api, issue_id, Origin="null").status_code == 403
    assert not calls
    assert post(api, issue_id, Origin="http://127.0.0.1:8081").json()["status"] == "sent"


def test_configuration_failure_never_attempts_send(api, edition, monkeypatch):
    monkeypatch.setattr(mailer, "real_email_status", lambda: {"configured": False, "message": "Connect Gmail first"})
    assert post(api, edition[0]).status_code == 503
    assert not edition[1]


@pytest.mark.parametrize("state", ["held", "superseded", "sending", "partial", "needs_attention"])
def test_unfinished_or_held_editions_cannot_send(api, edition, state):
    issue_id, calls = edition
    body = payload(issue_id)
    store.db().collection("issues").document(issue_id).update({"status": state})
    assert post(api, issue_id, body).status_code == 409
    assert not calls


def test_revocation_and_design_changes_invalidate_reviewed_version(api, edition):
    issue_id, calls = edition
    body = payload(issue_id)
    store.update_item("event", status="rejected")
    assert post(api, issue_id, body).status_code == 409
    body = payload(issue_id)
    store.db().collection("issues").document(issue_id).update({"newsletter_template": "newspaper"})
    assert post(api, issue_id, body).status_code == 409
    assert not calls


def test_refresh_allows_remaining_approved_content_and_excludes_revoked_item(api, edition):
    issue_id, calls = edition
    store.update_item("event", status="rejected")
    result = post(api, issue_id)
    assert result.json()["status"] == "sent"
    assert "Approved workshop" not in calls[0][2]
    assert "Volunteer opportunity" in calls[0][2]
    assert calls[0][3] == {}


def test_empty_edition_cannot_send(api, edition):
    body = payload(edition[0])
    for item_id in ["event", "notice"]:
        store.update_item(item_id, status="rejected")
    assert post(api, edition[0], body).status_code == 409
    assert not edition[1]


def test_revocation_during_render_cannot_send_old_copy(api, edition, monkeypatch):
    original = email_preview.render
    def revoked_after_render(*args, **kwargs):
        result = original(*args, **kwargs)
        store.update_item("event", status="rejected")
        return result
    monkeypatch.setattr(email_preview, "render", revoked_after_render)
    assert post(api, edition[0]).status_code == 409
    assert not edition[1]


def test_sent_copy_uses_immutable_snapshot(api, edition):
    issue_id, calls = edition
    doc = store.get_issue(issue_id)
    issue, _ = issue_builder.from_saved(doc)
    store.db().collection("issues").document(issue_id).update({"status": "sent", "items": issue_builder.all_items(issue)})
    event = store.get_item("event")
    event["data"]["title"] = "New unapproved title"
    store.update_item("event", data=event["data"], status="pending")
    assert post(api, issue_id).json()["status"] == "sent"
    assert "Approved workshop" in calls[0][2] and "New unapproved title" not in calls[0][2]


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.urls = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.urls += [v for k, v in attrs if k in {"src", "href"}]


@pytest.mark.parametrize("template,custom", [("classic",False),("newspaper",False),("spotlight",False),("bulletin",False),("custom",True),("classic",True),("newspaper",True)])
def test_remote_email_has_embedded_posters_and_usable_links_in_every_layout(edition, template, custom):
    doc = store.get_issue(edition[0])
    doc["newsletter_template"] = template
    doc["newsletter_design"] = issue_builder.default_newsletter_design(template) if custom else None
    subject, html, images = email_preview.render(doc)
    urls = Links(html).urls
    assert subject.startswith("[Preview]")
    assert "BoardCast preview copy" in html and "You have not been subscribed" in html
    assert not any("127.0.0.1" in url or "localhost" in url or url.startswith("/") for url in urls)
    assert "https://forms.gle/real-registration" in urls
    assert "https://forms.office.com/volunteer" in urls
    assert len(images) == 1
    cid, raw = next(iter(images.items()))
    assert "cid:" + cid in urls
    assert Image.open(io.BytesIO(raw)).format == "JPEG"
    assert "Add to calendar" not in html
    assert "Unsubscribe</a>" not in html


def test_missing_poster_stops_send_before_claim(api, edition):
    (config.LOCAL_DATA_DIR / "posters" / "event.png").unlink()
    assert post(api, edition[0]).status_code == 422
    assert not edition[1]
    assert list(store.db().collection("email_previews").stream()) == []


def test_uncertain_send_is_never_automatically_retried(api, edition, monkeypatch):
    calls = []
    def timeout(*args):
        calls.append(args)
        raise TimeoutError("secret-provider-response")
    monkeypatch.setattr(mailer, "send_real", timeout)
    result = post(api, edition[0])
    assert result.json()["status"] == "unknown" and "secret" not in result.text
    assert post(api, edition[0]).json()["duplicate"]
    assert len(calls) == 1


def test_malformed_provider_success_cannot_be_treated_as_safe_failure(api, edition, monkeypatch):
    import json
    calls = []
    def malformed_response(*args):
        calls.append(args)
        raise json.JSONDecodeError("malformed provider success", "not-json", 0)
    monkeypatch.setattr(mailer, "send_real", malformed_response)
    assert post(api, edition[0]).json()["status"] == "unknown"
    assert post(api, edition[0]).json()["duplicate"]
    assert len(calls) == 1


def test_known_rejection_can_be_retried_without_raw_error_leak(api, edition, monkeypatch):
    def rejected(*args):
        raise HttpError(Response({"status":"403"}), b'{"error":{"message":"secret-details"}}')
    monkeypatch.setattr(mailer, "send_real", rejected)
    result = post(api, edition[0])
    assert result.json()["status"] == "failed" and "secret" not in result.text
    monkeypatch.setattr(mailer, "send_real", lambda *args: "gmail-id")
    assert post(api, edition[0]).json()["status"] == "sent"


def test_concurrent_clicks_only_send_one_message(edition, monkeypatch):
    issue_id, calls = edition
    started, finish = Event(), Event()
    def slow(*args):
        calls.append(args)
        started.set()
        assert finish.wait(10)
        return "gmail-id"
    monkeypatch.setattr(mailer, "send_real", slow)
    version = payload(issue_id)["expected_revision"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(email_preview.send_preview, issue_id, TO, version, SA)
        try:
            assert started.wait(10)
            second = email_preview.send_preview(issue_id, TO, version, SA)
            assert second["status"] == "sending" and second["duplicate"]
        finally:
            finish.set()
        assert first.result()["status"] == "sent"
    assert len(calls) == 1


def test_abandoned_claim_becomes_unknown_instead_of_resending(api, edition, monkeypatch):
    def crash(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(mailer, "send_real", crash)
    issue_id = edition[0]
    with pytest.raises(KeyboardInterrupt):
        email_preview.send_preview(issue_id, TO, payload(issue_id)["expected_revision"], SA)
    doc = next(store.db().collection("email_previews").stream())
    store.db().collection("email_previews").document(doc.id).update({"updated_at": store.now()-timedelta(minutes=3)})
    assert post(api, issue_id).json()["status"] == "unknown"
    assert not edition[1]


def test_issue_metadata_contains_review_revision(api, edition):
    result = api.get("/admin/api/preview", headers={"X-Dev-User": SA})
    assert result.json()["issue"]["email_revision"] == payload(edition[0])["expected_revision"]


@pytest.fixture
def hosted_api(edition, monkeypatch):
    """A trusted server-side test stub supplies the owner's verified middleware state.

    Gate tests separately verify cookie/CSRF checks. This exercises the endpoint's
    independent owner, role, recipient and receipt safeguards without real OAuth.
    """
    monkeypatch.setattr(config, "HOSTED_DEMO", True)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://boardcast.example")
    monkeypatch.setenv("HOSTED_EMAIL_PREVIEW", "true")
    monkeypatch.setenv("HOSTED_EMAIL_RECIPIENT", "owner@example.com")
    state = {"owner": False}
    app = FastAPI()

    @app.middleware("http")
    async def trusted_owner_state(request, call_next):
        request.state.hosted_owner = state["owner"]
        return await call_next(request)

    app.include_router(email_preview.router)
    with TestClient(app, base_url="https://boardcast.example") as client:
        yield client, state


def test_hosted_locked_status_does_not_read_credentials_or_reveal_owner_inbox(hosted_api, edition, monkeypatch):
    client, _ = hosted_api
    def forbidden():
        raise AssertionError("Locked judge requests must not inspect OAuth configuration")
    monkeypatch.setattr(mailer, "real_email_status", forbidden)
    response = client.get("/admin/api/real-email/status", headers={"X-Dev-User": SA, "X-Hosted-Owner": "true"})
    assert response.status_code == 200
    assert response.json() == {"configured": False, "owner_required": True, "hosted": True,
                               "message": "Unlock owner email testing to send a real preview."}
    assert "owner@example.com" not in response.text and "inbox_url" not in response.text
    assert not edition[1]


@pytest.mark.parametrize("owner_value", [False, "true", 1, None])
def test_hosted_send_requires_verified_boolean_owner_state(hosted_api, edition, owner_value):
    client, state = hosted_api
    state["owner"] = owner_value
    response = post(client, edition[0], payload(edition[0], recipient="owner@example.com"), **{"X-Hosted-Owner": "true"})
    assert response.status_code == 403
    assert not edition[1]
    assert list(store.db().collection("email_previews").stream()) == []


@pytest.mark.parametrize("flag", ["true", "1"])
def test_hosted_owner_status_discloses_fixed_inbox_only_to_student_affairs(hosted_api, edition, monkeypatch, flag):
    client, state = hosted_api
    state["owner"] = True
    monkeypatch.setenv("HOSTED_EMAIL_PREVIEW", flag)
    response = client.get("/admin/api/real-email/status", headers={"X-Dev-User": SA})
    assert response.status_code == 200
    result = response.json()
    assert result["configured"] and result["hosted"] and result["owner_required"] is False
    assert result["fixed_recipient"] == "owner@example.com"
    assert result["inbox_url"] == "https://mail.google.com/mail/u/0/#inbox"
    assert client.get("/admin/api/real-email/status", headers={"X-Dev-User": REP}).status_code == 403
    assert not edition[1]


@pytest.mark.parametrize("flag", ["", "false", "treu"])
def test_hosted_email_feature_requires_explicit_opt_in(hosted_api, edition, monkeypatch, flag):
    client, state = hosted_api
    state["owner"] = True
    monkeypatch.setenv("HOSTED_EMAIL_PREVIEW", flag)
    response = client.get("/admin/api/real-email/status", headers={"X-Dev-User": SA})
    assert response.status_code == 200
    assert not response.json()["configured"] and not response.json()["owner_required"]
    assert "fixed_recipient" not in response.json() and "inbox_url" not in response.json()
    assert post(client, edition[0], payload(edition[0], recipient="owner@example.com")).status_code == 404
    assert not edition[1]


@pytest.mark.parametrize("recipient", ["", "bad-secret-config", "first@example.com,second@example.com", "owner@example.com\r\nBcc:other@example.com"])
def test_hosted_missing_or_invalid_fixed_recipient_fails_closed(hosted_api, edition, monkeypatch, recipient):
    client, state = hosted_api
    state["owner"] = True
    monkeypatch.setenv("HOSTED_EMAIL_RECIPIENT", recipient)
    response = client.get("/admin/api/real-email/status", headers={"X-Dev-User": SA})
    assert response.status_code == 200
    assert not response.json()["configured"]
    assert "fixed_recipient" not in response.json()
    assert "bad-secret-config" not in response.text and "Bcc" not in response.text
    assert post(client, edition[0], payload(edition[0], recipient="owner@example.com")).status_code == 503
    assert not edition[1]


def test_hosted_owner_cannot_send_to_arbitrary_address_or_without_confirmation(hosted_api, edition):
    client, state = hosted_api
    state["owner"] = True
    issue_id, calls = edition
    assert post(client, issue_id, payload(issue_id, recipient="someone-else@example.com")).status_code == 403
    assert post(client, issue_id, payload(issue_id, recipient="owner+alias@example.com")).status_code == 403
    assert post(client, issue_id, payload(issue_id, recipient="owner@example.com", confirmed=False)).status_code == 422
    assert post(client, issue_id, payload(issue_id, recipient="owner@example.com"), actor=REP).status_code == 403
    assert not calls


def test_hosted_owner_preview_preserves_cid_images_and_deduplicates(hosted_api, edition):
    client, state = hosted_api
    state["owner"] = True
    issue_id, calls = edition
    original = store.get_issue(issue_id)
    body = payload(issue_id, recipient="OWNER@example.com")
    response = post(client, issue_id, body)
    assert response.status_code == 200 and response.json()["status"] == "sent"
    assert len(calls) == 1 and calls[0][0] == "owner@example.com"
    html, images = calls[0][2], calls[0][3]
    assert "Owner test from the shared BoardCast judge demo" in html
    assert "BoardCast runs locally" not in html
    assert "demo-only online-edition and calendar links have been omitted" in html
    urls = Links(html).urls
    assert len(images) == 1 and all("cid:" + cid in urls for cid in images)
    assert not any("boardcast.example" in url for url in urls)
    assert "https://forms.gle/real-registration" in urls
    assert store.get_issue(issue_id) == original
    assert not store.get_subscriber("owner@example.com")
    assert post(client, issue_id, body).json()["duplicate"] and len(calls) == 1
    state["owner"] = False
    assert post(client, issue_id, body).status_code == 403


def test_hosted_owner_still_requires_current_review_revision(hosted_api, edition):
    client, state = hosted_api
    state["owner"] = True
    issue_id, calls = edition
    body = payload(issue_id, recipient="owner@example.com")
    store.db().collection("issues").document(issue_id).update({"newsletter_template": "newspaper"})
    assert post(client, issue_id, body).status_code == 409
    assert not calls


def test_hosted_setup_guide_never_opens_even_for_owner_or_localhost_host(hosted_api):
    client, state = hosted_api
    state["owner"] = True
    for headers in [{}, {"Host": "localhost"}]:
        assert client.get("/admin/email-setup", headers=headers).status_code == 404


def test_hosted_email_still_requires_local_demo_mode(hosted_api, edition, monkeypatch):
    client, state = hosted_api
    state["owner"] = True
    monkeypatch.setattr(config, "LOCAL_MODE", False)
    assert client.get("/admin/api/real-email/status", headers={"X-Dev-User": SA}).status_code == 404
    assert post(client, edition[0], payload(edition[0], recipient="owner@example.com")).status_code == 404
    assert not edition[1]


def test_hosted_sender_authorisation_failure_does_not_refer_to_blocked_setup_page(hosted_api, edition, monkeypatch):
    client, state = hosted_api
    state["owner"] = True
    def unauthorised(*args):
        raise mailer.MailerConfigurationError("secret-token-provider-details")
    monkeypatch.setattr(mailer, "send_real", unauthorised)
    response = post(client, edition[0], payload(edition[0], recipient="owner@example.com"))
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "demo owner" in response.json()["message"]
    assert "setup guide" not in response.text and "secret-token" not in response.text
