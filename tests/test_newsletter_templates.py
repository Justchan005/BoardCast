"""Newsletter layout settings, faithful previews and frozen delivery editions."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from httplib2 import Response

from backend import config, delivery, editor, issue_builder, main, store, tokens
from backend.localdb import LocalClient

SA = "layouts@apu.edu.my"
REP = "rep.layouts@mail.apu.edu.my"
PATH = "/admin/api/newsletter-template"


@pytest.fixture
def layout_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DISABLED", "true")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    database = LocalClient(tmp_path / "layouts.json")
    monkeypatch.setattr(store, "_db", database)
    database.collection("roles").document(SA).set({"role": "student_affairs"})
    database.collection("roles").document(REP).set({"role": "club_rep", "club_id": "club"})
    monkeypatch.setattr(editor, "plan_issue", lambda items: (editor.fallback_plan(items), {"source": "test"}))
    monkeypatch.setattr(main.mailer, "send", lambda *args, **kwargs: pytest.fail("Unexpected email send"))
    return database


@pytest.fixture
def api(layout_db):
    with TestClient(main.app) as client:
        yield client


def headers(email=SA):
    return {"X-Dev-User": email}


def add_event(item_id="event", title="Approved workshop", status="approved"):
    store.create_item({"id": item_id, "type": "event", "status": status, "data": {
        "title": title, "organiser": "Club", "date": "2099-11-11", "start_time": "16:00",
        "venue": "Campus Hall", "fee": "Free", "registration": {"required": False}}})


def make_issue():
    add_event()
    return issue_builder.build_preview(date(2099, 11, 9))[0]


def selected_files(monkeypatch):
    paths = []
    original = issue_builder.env.get_template

    def tracked(name, *args, **kwargs):
        paths.append(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(issue_builder.env, "get_template", tracked)
    return paths


@pytest.mark.parametrize("method,path,body", [
    ("GET", PATH, None), ("PUT", PATH, {"template": "newspaper"}),
    ("POST", PATH + "/preview", {"template": "newspaper"}),
])
def test_layout_controls_require_student_affairs(api, method, path, body):
    assert api.request(method, path, json=body).status_code == 401
    assert api.request(method, path, json=body, headers=headers(REP)).status_code == 403
    assert not store.db().collection("settings").document("publication").get().exists


def test_default_layout_and_available_choices(api, layout_db):
    response = api.get(PATH, headers=headers())
    assert response.status_code == 200
    assert response.json()["template"] == "classic"
    assert {entry["id"] for entry in response.json()["templates"]} == {"classic", "newspaper", "spotlight", "bulletin", "custom"}
    assert not layout_db.collection("settings").document("publication").get().exists


@pytest.mark.parametrize("value", ["../base.html", "weekly_email_newspaper.html", "NEWSPAPER", "", None, {}])
def test_template_input_rejects_values_outside_the_allowlist(api, value):
    assert api.put(PATH, headers=headers(), json={"template": value}).status_code == 422
    assert api.post(PATH + "/preview", headers=headers(), json={"template": value}).status_code == 422
    assert issue_builder.default_newsletter_template() == "classic"


@pytest.mark.parametrize("issue_id", ["", "issues/other", "../issue", "white space", "line\nbreak", "x" * 129])
def test_issue_ids_cannot_be_empty_or_firestore_paths(api, issue_id):
    body = {"template": "newspaper", "issue_id": issue_id}
    assert api.put(PATH, headers=headers(), json=body).status_code == 422
    assert api.post(PATH + "/preview", headers=headers(), json=body).status_code == 422
    assert issue_builder.default_newsletter_template() == "classic"


def test_default_selection_persists_and_applies_to_new_editions(api, layout_db, monkeypatch):
    first = make_issue()
    response = api.put(PATH, headers=headers(), json={"template": "newspaper"})
    assert response.json() == {"template": "newspaper", "issue_id": None, "applied_to_issue": False,
                               "design": None, "issue_design": None}
    monkeypatch.setattr(store, "_db", LocalClient(layout_db.path))
    assert issue_builder.default_newsletter_template() == "newspaper"
    assert store.get_issue(first)["newsletter_template"] == "classic"
    second, _ = issue_builder.build_preview(date(2099, 11, 16))
    assert store.get_issue(second)["newsletter_template"] == "newspaper"


def test_selection_updates_the_exact_preview_and_default_atomically(api):
    issue_id = make_issue()
    old = store.get_issue(issue_id)
    response = api.put(PATH, headers=headers(), json={"template": "newspaper", "issue_id": issue_id})
    assert response.status_code == 200 and response.json()["applied_to_issue"]
    changed = store.get_issue(issue_id)
    assert changed["newsletter_template"] == issue_builder.default_newsletter_template() == "newspaper"
    assert changed["items"] == old["items"] and changed["editor_plan"] == old["editor_plan"]
    visible = api.get("/admin/api/preview", headers=headers()).json()["issue"]
    assert visible["newsletter_template"] == "newspaper" and visible["layout_editable"]


@pytest.mark.parametrize("status,frozen", [("sent", True), ("partial", True), ("sending", False),
    ("held", False), ("needs_attention", True), ("superseded", False), ("preview", True)])
def test_started_or_closed_edition_rejects_layout_change_without_changing_default(api, status, frozen):
    issue_id = make_issue()
    store.db().collection("issues").document(issue_id).update({"status": status, "delivery_frozen": frozen})
    response = api.put(PATH, headers=headers(), json={"template": "newspaper", "issue_id": issue_id})
    assert response.status_code == 409
    assert issue_builder.default_newsletter_template() == "classic"
    assert store.get_issue(issue_id)["newsletter_template"] == "classic"
    assert not api.get("/admin/api/preview", headers=headers()).json()["issue"]["layout_editable"]


def test_stale_layout_apply_loses_to_delivery_claim(api):
    issue_id = make_issue()
    assert api.get("/admin/api/preview", headers=headers()).json()["issue"]["layout_editable"]
    assert delivery._claim(issue_id, "worker") is not None
    assert api.put(PATH, headers=headers(), json={"template": "newspaper", "issue_id": issue_id}).status_code == 409
    assert issue_builder.default_newsletter_template() == "classic"


def test_unknown_issue_does_not_change_default(api):
    assert api.put(PATH, headers=headers(), json={"template": "newspaper", "issue_id": "missing"}).status_code == 404
    assert api.post(PATH + "/preview", headers=headers(), json={"template": "newspaper", "issue_id": "missing"}).status_code == 404
    assert issue_builder.default_newsletter_template() == "classic"


@pytest.mark.parametrize("template", ["classic", "newspaper", "spotlight", "bulletin"])
@pytest.mark.parametrize("saved_edition", [False, True])
def test_layout_preview_is_read_only_escaped_and_has_no_personal_tokens(api, layout_db, monkeypatch, template, saved_edition):
    add_event(title="Workshop <script>alert('poster')</script>")
    add_event("unapproved", "Unapproved content", status="pending")
    issue_id = issue_builder.build_preview(date(2099, 11, 9))[0] if saved_edition else None
    before = layout_db.path.read_bytes()
    monkeypatch.setattr(editor, "plan_issue", lambda *args: pytest.fail("Layout preview must not call Gemini"))
    monkeypatch.setattr(tokens, "sign", lambda *args: pytest.fail("Layout preview must not create personalized tokens"))
    response = api.post(PATH + "/preview", headers=headers(), json={"template": template, "issue_id": issue_id})
    assert response.status_code == 200
    result = response.json()
    assert result["item_count"] == 1 and result["source"] == ("edition" if saved_edition else "approved")
    assert "&lt;script&gt;" in result["html"] and "<script>" not in result["html"]
    assert "Unapproved content" not in result["html"]
    assert "?s=" not in result["html"] and "unsubscribe?token=" not in result["html"]
    assert layout_db.path.read_bytes() == before


def test_saved_edition_preview_preserves_its_spotlight(api, monkeypatch):
    issue_id = make_issue()
    add_event("second", "Selected spotlight")
    items = [store.get_item("event"), store.get_item("second")]
    plan = editor.fallback_plan(items, "second")
    store.db().collection("issues").document(issue_id).update({
        "items": items, "item_ids": [item["id"] for item in items], "editor_plan": plan.model_dump()})
    seen = []
    original = issue_builder.render_email

    def render(edition, chosen, *args, **kwargs):
        seen.append(chosen.spotlight_item_id)
        return original(edition, chosen, *args, **kwargs)

    monkeypatch.setattr(issue_builder, "render_email", render)
    assert api.post(PATH + "/preview", headers=headers(), json={"template": "newspaper", "issue_id": issue_id}).status_code == 200
    assert seen == ["second"]


@pytest.mark.parametrize("template,email_path,web_path", [
    ("classic", "weekly_email.html", "magazine.html"),
    ("newspaper", "weekly_email_newspaper.html", "magazine_newspaper.html"),
    ("spotlight", "weekly_email_variant.html", "magazine_variant.html"),
    ("bulletin", "weekly_email_variant.html", "magazine_variant.html"),
])
def test_selected_layout_renders_email_and_web(api, monkeypatch, template, email_path, web_path):
    issue_id = make_issue()
    api.put(PATH, headers=headers(), json={"template": template, "issue_id": issue_id})
    paths = selected_files(monkeypatch)
    edition, plan = issue_builder.from_saved(store.get_issue(issue_id))
    assert "Approved workshop" in issue_builder.render_email(edition, plan, None)
    assert "Approved workshop" in api.get(f"/m/{issue_id}").text
    assert email_path in paths and web_path in paths


@pytest.mark.parametrize("stored", [None, "../base.html", {"bad": "value"}])
def test_legacy_and_invalid_stored_layouts_remain_classic(api, stored):
    issue_id = make_issue()
    store.db().collection("issues").document(issue_id).update({"newsletter_template": stored})
    api.put(PATH, headers=headers(), json={"template": "newspaper"})
    edition, _ = issue_builder.from_saved(store.get_issue(issue_id))
    assert edition["newsletter_template"] == "classic"
    assert api.get("/admin/api/preview", headers=headers()).json()["issue"]["newsletter_template"] == "classic"


def test_partial_delivery_keeps_its_layout_after_default_changes(api, monkeypatch):
    issue_id = make_issue()
    api.put(PATH, headers=headers(), json={"template": "newspaper", "issue_id": issue_id})
    for email in ("a@mail.apu.edu.my", "b@mail.apu.edu.my"):
        store.upsert_subscriber({"email": email, "active": True})
    calls = []

    def first(email, subject, html, unsubscribe_url):
        calls.append((email, html))
        if email.startswith("b@"):
            raise HttpError(Response({"status": "429"}), b'{"error":{"message":"retry later"}}')
        return "gmail-a"

    monkeypatch.setattr(main.mailer, "send", first)
    assert main.issue_send()["status"] == "partial"
    assert store.get_issue(issue_id)["delivery_snapshot"]["newsletter_template"] == "newspaper"
    assert api.put(PATH, headers=headers(), json={"template": "classic"}).status_code == 200
    monkeypatch.setattr(main.mailer, "send", lambda email, subject, html, unsubscribe_url:
                        calls.append((email, html)) or "gmail-b")
    assert main.issue_retry()["status"] == "sent"
    assert calls[-1] == calls[-2]
    assert store.get_issue(issue_id)["newsletter_template"] == "newspaper"
    edition, _ = issue_builder.from_saved(store.get_issue(issue_id))
    assert edition["newsletter_template"] == "newspaper"


def test_recovered_unattempted_recipient_uses_frozen_layout(api, monkeypatch):
    issue_id = make_issue()
    api.put(PATH, headers=headers(), json={"template": "newspaper", "issue_id": issue_id})
    for email in ("a@mail.apu.edu.my", "b@mail.apu.edu.my"):
        store.upsert_subscriber({"email": email, "active": True})
    claimed = delivery._claim(issue_id, "interrupted-worker")
    prepared, reason = delivery._prepare(claimed, "interrupted-worker")
    assert reason is None
    receipt = delivery._begin(prepared, "a@mail.apu.edu.my", "interrupted-worker",
                              {"subject": "Previous delivery", "html": "Previous delivery", "unsubscribe_url": ""}, True)
    delivery._record(prepared, "a@mail.apu.edu.my", receipt["attempt"], "sent", message_id="gmail-a")
    store.db().collection("issues").document(issue_id).update({"delivery_lease_until": store.now() - timedelta(seconds=1)})
    api.put(PATH, headers=headers(), json={"template": "classic"})
    calls = []
    paths = selected_files(monkeypatch)
    monkeypatch.setattr(main.mailer, "send", lambda email, *args: calls.append(email) or "gmail-b")
    assert main.issue_retry()["status"] == "sent"
    assert calls == ["b@mail.apu.edu.my"]
    assert "weekly_email_newspaper.html" in paths and "weekly_email.html" not in paths
    assert store.get_issue(issue_id)["newsletter_template"] == "newspaper"
