"""Structured newsletter design validation, placement and immutable publication."""
from collections import Counter
from datetime import date, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError
from httplib2 import Response

from backend import config, delivery, editor, issue_builder, main, store, tokens
from backend.localdb import LocalClient
from shared.schemas import EditorPlan

SA, REP = "design@apu.edu.my", "design.rep@mail.apu.edu.my"
PATH = "/admin/api/newsletter-template"


def event(item_id, title=None):
    return {"id": item_id, "type": "event", "status": "approved", "show_poster": False,
            "private_note": "not part of layout metadata", "data": {
        "title": title or f"Workshop {item_id}", "organiser": "Club", "date": "2099-11-11",
        "start_time": "16:00", "venue": "Hall A", "fee": "Free",
        "summary": "Bring a laptop. Details https://forms.gle/campus.",
        "registration": {"required": True, "link": "https://forms.gle/campus"}}}


@pytest.fixture
def design_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DISABLED", "true")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://boardcast.test")
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(tokens, "_SECRET", b"design-test-only")
    database = LocalClient(tmp_path / "design.json")
    monkeypatch.setattr(store, "_db", database)
    database.collection("roles").document(SA).set({"role": "student_affairs"})
    database.collection("roles").document(REP).set({"role": "club_rep", "club_id": "club"})
    monkeypatch.setattr(editor, "plan_issue", lambda items: (editor.fallback_plan(items, "e2"), {"source": "test"}))
    monkeypatch.setattr(main.mailer, "send", lambda *args, **kwargs: pytest.fail("Unexpected real delivery"))
    for item_id in ("e1", "e2", "e3"):
        store.create_item(event(item_id))
    store.create_item({"id": "win", "type": "achievement", "status": "approved", "data": {
        "title": "Robotics finalists", "team": "Campus Robotics", "competition": "Campus Cup",
        "result": "Finalist", "consent_confirmed": True}})
    store.create_item({"id": "notice", "type": "announcement", "status": "approved", "data": {
        "title": "Campus opportunity", "organiser": "Student Affairs", "summary": "Help welcome new students."}})
    return database


@pytest.fixture
def api(design_db):
    with TestClient(main.app) as client:
        yield client


def headers(email=SA):
    return {"X-Dev-User": email}


def make_issue():
    return issue_builder.build_preview(date(2099, 11, 9))[0]


def candidate(**changes):
    return {**issue_builder.default_newsletter_design("newspaper"), "headline_font": "times",
            "body_font": "verdana", "accent_color": "#663399", "columns": 3, "lead_id": "win",
            "story_order": ["e3", "e2", "e1", "notice"],
            "column_map": {"e1": 1, "e2": 2, "e3": 3, "notice": 3}, **changes}


class StoryIds(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "data-story-id" in values:
            self.ids.append(values["data-story-id"])


INVALID_DESIGNS = [
    {"css": "body{display:none}"}, {"html": "<script>alert(1)</script>"},
    {"headline_font": "Arial; background:url(https://evil.test)"}, {"body_font": "comic-sans"},
    {"paper_color": "red"}, {"text_color": "#123"}, {"accent_color": "#123456;background:red"},
    {"accent_color": "#123456\n"}, {"paper_color": "#12345678"}, {"text_color": None},
    {"columns": 0}, {"columns": 4}, {"columns": True}, {"columns": "2"},
    {"lead_image": "url(evil)"}, {"lead_id": "../other"}, {"lead_id": ""}, {"lead_id": "x" * 129},
    {"story_order": ["good", "items/other"]}, {"story_order": ["x"] * 201},
    {"column_map": {"../item": 1}}, {"column_map": {"item": 0}}, {"column_map": {"item": 4}},
    {"column_map": {"item": True}}, {"column_map": {"item": "2"}},
    {"column_map": {f"item-{i}": 1 for i in range(201)}}, [], "<style>body{}</style>",
]


@pytest.mark.parametrize("design", INVALID_DESIGNS)
def test_design_rejects_css_html_invalid_ids_and_unbounded_placements(api, design):
    body = {"template": "newspaper", "design": design}
    assert api.put(PATH, headers=headers(), json=body).status_code == 422
    assert api.post(PATH + "/preview", headers=headers(), json=body).status_code == 422
    assert issue_builder.publication_settings() == {"template": "classic", "design": None}


def test_design_controls_are_student_affairs_only(api):
    body = {"template": "newspaper", "design": candidate()}
    assert api.put(PATH, json=body).status_code == 401
    assert api.put(PATH, headers=headers(REP), json=body).status_code == 403
    assert api.post(PATH + "/preview", headers=headers(REP), json=body).status_code == 403


def test_settings_expose_only_safe_item_metadata_and_known_fonts(api):
    value = api.get(PATH, headers=headers()).json()
    assert {font["id"] for font in value["fonts"]} == {"georgia", "times", "arial", "verdana"}
    assert value["design"] is None
    assert value["lead_id"] == value["items"][0]["id"]
    assert all(set(item) == {"id", "title", "type", "poster_url"} for item in value["items"])
    assert "private_note" not in str(value)
    assert value["design_defaults"]["classic"]["columns"] == 1
    assert value["design_defaults"]["newspaper"]["body_font"] == "georgia"
    assert value["design_defaults"]["newspaper"]["accent_color"] == "#9D3229"


def test_approved_items_refresh_before_the_first_edition_exists(api):
    pending = event("new-event")
    pending["status"] = "pending"
    store.create_item(pending)
    before = api.get("/admin/api/preview", headers=headers()).json()
    assert before["issue"] is None
    assert "new-event" not in [item["id"] for item in before["layout_items"]]
    assert api.post("/admin/api/items/new-event/decision", headers=headers(), json={"decision": "approve"}).status_code == 200
    after = api.get("/admin/api/preview", headers=headers()).json()
    assert after["issue"] is None
    assert "new-event" in [item["id"] for item in after["layout_items"]]
    assert after["layout_lead_id"] == after["layout_items"][0]["id"]


def test_explicit_partial_design_inherits_selected_preset_and_normalizes_colors(api):
    response = api.put(PATH, headers=headers(), json={"template": "newspaper", "design": {"accent_color": "#aabbcc"}})
    assert response.status_code == 200
    design = response.json()["design"]
    assert design["accent_color"] == "#AABBCC"
    assert design["headline_font"] == design["body_font"] == "georgia"
    assert design["paper_color"] == "#F7F3E8" and design["columns"] == 2


def test_default_design_strips_placements_but_exact_issue_keeps_them(api, design_db):
    issue_id = make_issue()
    response = api.put(PATH, headers=headers(), json={"template": "newspaper", "design": candidate(), "issue_id": issue_id})
    assert response.status_code == 200
    assert response.json()["issue_design"]["lead_id"] == "win"
    assert response.json()["issue_design"]["column_map"]["e3"] == 3
    saved_default = design_db.collection("settings").document("publication").get().to_dict()["newsletter_design"]
    assert not {"lead_id", "story_order", "column_map"}.intersection(saved_default)
    assert response.json()["design"] == saved_default
    future, _ = issue_builder.build_preview(date(2099, 11, 16))
    future_design = store.get_issue(future)["newsletter_design"]
    assert future_design["accent_color"] == "#663399"
    assert future_design["lead_id"] is None and future_design["story_order"] == [] and future_design["column_map"] == {}


def test_omitted_design_preserves_existing_values_and_null_resets(api):
    issue_id = make_issue()
    saved = api.put(PATH, headers=headers(), json={"template": "newspaper", "design": candidate(), "issue_id": issue_id}).json()
    kept = api.put(PATH, headers=headers(), json={"template": "classic", "issue_id": issue_id}).json()
    assert kept["design"] == saved["design"] and kept["issue_design"] == saved["issue_design"]
    reset = api.put(PATH, headers=headers(), json={"template": "classic", "design": None, "issue_id": issue_id}).json()
    assert reset["design"] is None and reset["issue_design"] is None
    assert store.get_issue(issue_id)["newsletter_design"] is None


def test_replacing_column_assignments_removes_old_map_keys(api):
    issue_id = make_issue()
    api.put(PATH, headers=headers(), json={"template": "newspaper", "design": candidate(), "issue_id": issue_id})
    response = api.put(PATH, headers=headers(), json={"template": "newspaper",
        "design": candidate(column_map={"e1": 3}), "issue_id": issue_id})
    assert response.status_code == 200
    assert store.get_issue(issue_id)["newsletter_design"]["column_map"] == {"e1": 3}


def test_firestore_atomic_map_write_replaces_design_instead_of_merging_removed_keys(monkeypatch):
    # Build the real SDK write protobuf locally; no client, credentials or RPC exists.
    from google.cloud.firestore_v1.transaction import Transaction
    transaction = Transaction(None)
    ref = Mock()
    ref._document_path = "projects/unit/databases/(default)/documents/issues/edition"
    ref.get.return_value.to_dict.return_value = {"newsletter_design": {"column_map": {"e1": 1, "e2": 2}}}
    client = SimpleNamespace(collection=lambda name: SimpleNamespace(document=lambda item_id: ref),
                             transaction=lambda: transaction)
    monkeypatch.setattr(config, "LOCAL_MODE", False)
    monkeypatch.setattr(store, "_db", client)
    monkeypatch.setattr(store.firestore, "transactional", lambda function: function)
    key = ("issues", "edition")
    store.atomic_documents([key], lambda rows: {key: {"newsletter_design": {"column_map": {"e1": 3}}}})
    write = transaction._write_pbs[0]
    assert list(write.update_mask.field_paths) == ["newsletter_design"]
    encoded_map = write.update.fields["newsletter_design"].map_value.fields["column_map"].map_value.fields
    assert set(encoded_map) == {"e1"}
    ref.set.assert_not_called()


def test_latest_issue_metadata_preserves_preset_order_separately_from_custom_non_event_lead(api):
    issue_id = make_issue()
    first = api.get("/admin/api/preview", headers=headers()).json()["issue"]
    assert first["layout_lead_id"] == first["layout_items"][0]["id"] == "e2"
    api.put(PATH, headers=headers(), json={"template": "newspaper", "design": candidate(), "issue_id": issue_id})
    saved = api.get("/admin/api/preview", headers=headers()).json()["issue"]
    assert saved["layout_lead_id"] == saved["layout_items"][0]["id"] == "e2"
    assert [item["id"] for item in saved["layout_items"]] == [item["id"] for item in first["layout_items"]]
    assert saved["newsletter_design"]["lead_id"] == "win"
    assert all(set(item) == {"id", "title", "type", "poster_url"} for item in saved["layout_items"])


@pytest.mark.parametrize("columns", [1, 2, 3])
def test_placement_is_complete_deduplicated_and_email_columns_never_exceed_two(design_db, columns):
    issue = issue_builder.collect(date(2099, 11, 9))
    issue.update(newsletter_template="newspaper", newsletter_design=candidate(columns=columns,
        story_order=["e3", "e2", "e3", "removed"], column_map={"e1": 1, "e2": 2, "e3": 3, "removed": 1}))
    context = issue_builder._context(issue, EditorPlan(spotlight_item_id="e2"))
    assert context["custom_lead"]["id"] == "win"
    ids = [context["custom_lead"]["id"]] + [item["id"] for column in context["custom_columns"] for item in column]
    assert Counter(ids) == Counter({"e1": 1, "e2": 1, "e3": 1, "win": 1, "notice": 1})
    assert len(context["custom_columns"]) == columns
    assert len(context["custom_email_columns"]) == min(columns, 2)
    assert "e3" in [item["id"] for item in context["custom_columns"][min(3, columns) - 1]]
    if columns == 3:
        assert context["custom_email_columns"][1] == context["custom_columns"][1] + context["custom_columns"][2]


def test_stale_custom_lead_falls_back_to_valid_spotlight(design_db):
    issue = issue_builder.collect(date(2099, 11, 9))
    issue["newsletter_design"] = candidate(lead_id="removed")
    context = issue_builder._context(issue, EditorPlan(spotlight_item_id="e2"))
    assert context["custom_lead"]["id"] == "e2"
    assert all(item["id"] != "e2" for column in context["custom_columns"] for item in column)


@pytest.mark.parametrize("template", ["classic", "newspaper"])
@pytest.mark.parametrize("channel", ["email", "web"])
def test_custom_render_uses_safe_styles_and_renders_every_item_once(design_db, template, channel):
    issue = issue_builder.collect(date(2099, 11, 9))
    issue.update(newsletter_template=template, newsletter_design=candidate(lead_image="right"))
    plan = EditorPlan(spotlight_item_id="e2")
    html = issue_builder.render_email(issue, plan, None) if channel == "email" else issue_builder.render_magazine(issue, plan, "edition")
    assert StoryIds(html).ids[0] == "win"
    assert Counter(StoryIds(html).ids) == Counter({"e1": 1, "e2": 1, "e3": 1, "win": 1, "notice": 1})
    assert "#663399" in html and "Verdana" in html and "Times New Roman" in html
    assert "color:#663399;font-weight:bold;" in html  # shared copy and linkified description URLs
    assert "data-newsletter-template=\"custom\"" in html


def test_preview_design_is_read_only_and_omission_or_null_have_distinct_meanings(api, design_db, monkeypatch):
    issue_id = make_issue()
    api.put(PATH, headers=headers(), json={"template": "newspaper", "design": candidate(), "issue_id": issue_id})
    before = design_db.path.read_bytes()
    monkeypatch.setattr(editor, "plan_issue", lambda *args: pytest.fail("Preview must not call Gemini"))
    monkeypatch.setattr(tokens, "sign", lambda *args: pytest.fail("Preview must not make personal links"))
    for extra, custom in [({}, True), ({"design": None}, False), ({"design": {"columns": 1}}, True)]:
        response = api.post(PATH + "/preview", headers=headers(), json={"template": "newspaper", "issue_id": issue_id, **extra})
        assert response.status_code == 200 and response.json()["item_count"] == 5
        assert ('data-newsletter-template="custom"' in response.json()["html"]) == custom
    assert design_db.path.read_bytes() == before


@pytest.mark.parametrize("status", ["sent", "partial", "sending", "held", "needs_attention"])
def test_frozen_or_started_issue_cannot_mutate_design_or_default(api, design_db, status):
    issue_id = make_issue()
    design_db.collection("issues").document(issue_id).update({"status": status, "delivery_frozen": True})
    before = design_db.path.read_bytes()
    response = api.put(PATH, headers=headers(), json={"template": "newspaper", "design": candidate(), "issue_id": issue_id})
    assert response.status_code == 409
    assert design_db.path.read_bytes() == before
    assert api.post(PATH + "/preview", headers=headers(), json={"template": "newspaper", "design": candidate(), "issue_id": issue_id}).status_code == 200
    assert design_db.path.read_bytes() == before


def test_partial_delivery_retries_its_frozen_design_after_default_change(api, monkeypatch):
    issue_id = make_issue()
    api.put(PATH, headers=headers(), json={"template": "newspaper", "design": candidate(), "issue_id": issue_id})
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
    snapshot = store.get_issue(issue_id)["delivery_snapshot"]
    assert snapshot["newsletter_design"]["accent_color"] == "#663399"
    api.put(PATH, headers=headers(), json={"template": "classic", "design": {"accent_color": "#123456"}})
    monkeypatch.setattr(main.mailer, "send", lambda email, subject, html, unsubscribe_url:
                        calls.append((email, html)) or "gmail-b")
    assert main.issue_retry()["status"] == "sent"
    assert calls[-1] == calls[-2] and "#663399" in calls[-1][1]
    assert store.get_issue(issue_id)["newsletter_design"] == snapshot["newsletter_design"]
    assert "#663399" in api.get(f"/m/{issue_id}").text


def test_unattempted_recipient_after_crash_uses_snapshot_design(api, monkeypatch):
    issue_id = make_issue()
    api.put(PATH, headers=headers(), json={"template": "newspaper", "design": candidate(), "issue_id": issue_id})
    for email in ("a@mail.apu.edu.my", "b@mail.apu.edu.my"):
        store.upsert_subscriber({"email": email, "active": True})
    claimed = delivery._claim(issue_id, "old-worker")
    prepared, _ = delivery._prepare(claimed, "old-worker")
    receipt = delivery._begin(prepared, "a@mail.apu.edu.my", "old-worker",
                              {"subject": "Earlier", "html": "Earlier", "unsubscribe_url": ""}, True)
    delivery._record(prepared, "a@mail.apu.edu.my", receipt["attempt"], "sent", message_id="gmail-a")
    store.db().collection("issues").document(issue_id).update({"delivery_lease_until": store.now() - timedelta(seconds=1)})
    api.put(PATH, headers=headers(), json={"template": "classic", "design": {"accent_color": "#123456"}})
    calls = []
    monkeypatch.setattr(main.mailer, "send", lambda email, subject, html, unsubscribe_url:
                        calls.append((email, html)) or "gmail-b")
    assert main.issue_retry()["status"] == "sent"
    assert calls[0][0] == "b@mail.apu.edu.my" and "#663399" in calls[0][1]
    assert "#123456" not in calls[0][1] and StoryIds(calls[0][1]).ids[0] == "win"


@pytest.mark.parametrize("template", ["classic", "newspaper"])
def test_legacy_none_design_keeps_preset_output_after_default_changes(api, template):
    issue_id = make_issue()
    store.db().collection("issues").document(issue_id).update({"newsletter_template": template})
    edition, plan = issue_builder.from_saved(store.get_issue(issue_id))
    before = issue_builder.render_email(edition, plan, None)
    api.put(PATH, headers=headers(), json={"template": "newspaper", "design": candidate()})
    edition, plan = issue_builder.from_saved(store.get_issue(issue_id))
    assert edition["newsletter_design"] is None
    assert issue_builder.render_email(edition, plan, None) == before


def test_invalid_stored_css_disables_custom_render_and_description_accent_is_validated(design_db):
    issue = issue_builder.collect(date(2099, 11, 9))
    issue["newsletter_design"] = {"accent_color": "#123456;display:none"}
    assert issue_builder._context(issue, EditorPlan())["design"] is None
    safe = str(issue_builder._linkify_description("Details https://forms.gle/campus", "#AABBCC"))
    assert "color:#AABBCC" in safe
    unsafe = str(issue_builder._linkify_description("Details https://forms.gle/campus", "#123456;background:red"))
    assert "color:#0B7BB5" in unsafe and "background:red" not in unsafe
