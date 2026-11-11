"""Reusable placeholder themes: bounded data, persistence and frozen editions."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import config, delivery, editor, issue_builder, main, store, tokens
from backend.localdb import LocalClient


PATH = "/admin/api/newsletter-template"
STAFF = {"X-Dev-User": "theme@apu.edu.my"}
HEADER = [{"cells": [["date"], ["name"]]}, {"cells": [["intro"]]}]
STORY = [{"cells": [["title"]]}, {"cells": [["details"], ["picture"]]}]


@pytest.fixture
def theme_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DISABLED", "true")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://boardcast.test")
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(tokens, "_SECRET", b"theme-test-only")
    database = LocalClient(tmp_path / "theme.json")
    monkeypatch.setattr(store, "_db", database)
    database.collection("roles").document("theme@apu.edu.my").set({"role": "student_affairs"})
    monkeypatch.setattr(editor, "plan_issue", lambda items: (editor.fallback_plan(items), {"source": "test"}))
    monkeypatch.setattr(main.mailer, "send", lambda *args, **kwargs: pytest.fail("Unexpected real delivery"))
    store.create_item({"id": "theme-event", "type": "event", "status": "approved", "show_poster": False,
                       "data": {"title": "Theme workshop", "organiser": "Club", "date": "2099-11-11",
                                "start_time": "16:00", "venue": "Hall", "fee": "Free",
                                "summary": "Bring a laptop.", "registration": {"required": False}}})
    return database


@pytest.fixture
def api(theme_db):
    with TestClient(main.app) as client:
        yield client


def theme(**changes):
    return {"header_rows": deepcopy(HEADER), "story_rows": deepcopy(STORY), **changes}


INVALID_ROWS = [
    [], [{"cells": []}], [{"cells": [[], [], []]}],
    [{"cells": [["title", "picture", "details", "title"]]}],
    [{"cells": [["title", "picture"]]}],
    [{"cells": [["title", "picture", "picture"]]}],
    [{"cells": [["name", "date", "intro"]]}],
    [{"cells": [["title", "picture", "<script>alert(1)</script>"]]}],
    [{"cells": [["title", "picture", "details"]], "style": "display:none"}],
    [{"cells": [["title", "picture", "details"]]}] + [{"cells": [[]]}] * 6,
    [{"cells": "title"}], [{"cells": [[1, 2, 3]]}], "arbitrary HTML",
]


@pytest.mark.parametrize("rows", INVALID_ROWS)
def test_invalid_placeholder_layout_rejected_before_save_or_preview(api, theme_db, rows):
    before = theme_db.path.read_bytes()
    body = {"template": "custom", "design": theme(story_rows=rows)}
    assert api.put(PATH, headers=STAFF, json=body).status_code == 422
    assert api.post(PATH + "/preview", headers=STAFF, json=body).status_code == 422
    assert theme_db.path.read_bytes() == before


def test_header_placeholders_cannot_omit_duplicate_or_use_story_blocks():
    for blocks in (["name", "date"], ["name", "name", "intro"], ["title", "picture", "details"]):
        with pytest.raises(ValidationError):
            issue_builder.NewsletterDesign(header_rows=[{"cells": [blocks]}])


def test_rows_support_full_width_split_and_empty_drop_targets():
    rows = [{"cells": [[], ["title"]]}, {"cells": [[]]}, {"cells": [["details", "picture"]]}]
    result = issue_builder.newsletter_design(theme(story_rows=rows), "custom")
    assert result["story_rows"] == rows
    assert result["header_rows"] == HEADER


@pytest.mark.parametrize("raw", [None, {}, {"header_rows": None, "story_rows": None}])
def test_custom_always_resolves_complete_defaults_without_affecting_legacy_presets(raw):
    result = issue_builder.newsletter_design(raw, "custom")
    assert result["header_rows"] == [{"cells": [["name"]]}, {"cells": [["date", "intro"]]}]
    assert result["story_rows"] == [{"cells": [["picture"], ["title", "details"]]}]
    assert result["headline_font"] == "arial"
    assert issue_builder.newsletter_design(None, "classic") is None
    assert issue_builder.newsletter_design(None, "newspaper") is None
    result["story_rows"][0]["cells"][0].clear()
    assert issue_builder.default_newsletter_design("custom")["story_rows"][0]["cells"][0] == ["picture"]


@pytest.mark.parametrize("raw", [{"story_rows": []}, {"paper_color": "url(evil)"}, "<html>"])
def test_invalid_stored_custom_theme_uses_safe_defaults(raw):
    assert issue_builder.newsletter_design(raw, "custom") == issue_builder.default_newsletter_design("custom")


def test_custom_is_discoverable_and_preserves_rows_as_future_appearance_defaults(api, theme_db):
    choices = api.get(PATH, headers=STAFF).json()
    assert [item["id"] for item in choices["templates"]] == ["classic", "newspaper", "spotlight", "bulletin", "custom"]
    assert choices["design_defaults"]["custom"]["story_rows"]
    issue_id = issue_builder.build_preview(date(2099, 11, 9))[0]
    response = api.put(PATH, headers=STAFF, json={"template": "custom", "issue_id": issue_id,
        "design": theme(lead_id="theme-event", story_order=["theme-event"], column_map={"theme-event": 2})})
    assert response.status_code == 200
    result = response.json()
    assert result["design"]["header_rows"] == HEADER
    assert result["design"]["story_rows"] == STORY
    assert not {"lead_id", "story_order", "column_map"}.intersection(result["design"])
    assert result["issue_design"]["lead_id"] == "theme-event"
    future_id = issue_builder.build_preview(date(2099, 11, 16))[0]
    future = store.get_issue(future_id)
    assert future["newsletter_template"] == "custom"
    assert future["newsletter_design"]["story_rows"] == STORY
    assert future["newsletter_design"]["lead_id"] is None
    assert future["newsletter_design"]["column_map"] == {}


def test_omitted_rows_preserve_saved_design_and_custom_null_resets_to_safe_theme(api):
    issue_id = issue_builder.build_preview(date(2099, 11, 9))[0]
    api.put(PATH, headers=STAFF, json={"template": "custom", "design": theme(), "issue_id": issue_id})
    kept = api.put(PATH, headers=STAFF, json={"template": "custom", "issue_id": issue_id}).json()
    assert kept["issue_design"]["story_rows"] == STORY
    reset = api.put(PATH, headers=STAFF, json={"template": "custom", "design": None, "issue_id": issue_id}).json()
    assert reset["issue_design"] == issue_builder.default_newsletter_design("custom")


def test_preview_uses_reordered_placeholders_without_mutating_state_or_calling_ai(api, theme_db, monkeypatch):
    issue_id = issue_builder.build_preview(date(2099, 11, 9))[0]
    before = theme_db.path.read_bytes()
    monkeypatch.setattr(editor, "plan_issue", lambda *args: pytest.fail("Preview must not call Gemini"))
    monkeypatch.setattr(tokens, "sign", lambda *args: pytest.fail("Preview must not create personal links"))
    response = api.post(PATH + "/preview", headers=STAFF,
                        json={"template": "custom", "design": theme(), "issue_id": issue_id})
    assert response.status_code == 200
    assert response.json()["item_count"] == 1
    assert "Theme workshop" in response.json()["html"]
    assert theme_db.path.read_bytes() == before


def test_frozen_snapshot_keeps_theme_when_defaults_or_outer_document_change(api, theme_db):
    issue_id = issue_builder.build_preview(date(2099, 11, 9))[0]
    api.put(PATH, headers=STAFF, json={"template": "custom", "design": theme(), "issue_id": issue_id})
    store.upsert_subscriber({"email": "theme.student@mail.apu.edu.my", "active": True})
    claimed = delivery._claim(issue_id, "theme-worker")
    delivery._prepare(claimed, "theme-worker")
    snapshot = deepcopy(store.get_issue(issue_id)["delivery_snapshot"])
    assert snapshot["newsletter_design"]["story_rows"] == STORY
    before = theme_db.path.read_bytes()
    blocked = api.put(PATH, headers=STAFF, json={"template": "custom", "design": None, "issue_id": issue_id})
    assert blocked.status_code == 409
    assert theme_db.path.read_bytes() == before
    api.put(PATH, headers=STAFF, json={"template": "custom", "design": None})
    theme_db.collection("issues").document(issue_id).update({"newsletter_template": "classic", "newsletter_design": None})
    edition, _ = issue_builder.from_saved(store.get_issue(issue_id))
    assert edition["newsletter_template"] == "custom"
    assert edition["newsletter_design"] == snapshot["newsletter_design"]
    assert store.get_issue(issue_id)["delivery_snapshot"] == snapshot


def assert_firestore_has_no_direct_nested_arrays(value):
    kind = value._pb.WhichOneof("value_type")
    if kind == "array_value":
        for child in value.array_value.values:
            assert child._pb.WhichOneof("value_type") != "array_value"
            assert_firestore_has_no_direct_nested_arrays(child)
    elif kind == "map_value":
        for child in value.map_value.fields.values():
            assert_firestore_has_no_direct_nested_arrays(child)


@pytest.mark.parametrize("collection,snapshot", [("settings", False), ("issues", False), ("issues", True)])
def test_firestore_transaction_encodes_cells_without_nested_arrays(monkeypatch, collection, snapshot):
    # Exercise the real SDK protobuf conversion without a client or network call.
    from google.cloud.firestore_v1 import _helpers
    from google.cloud.firestore_v1.transaction import Transaction
    transaction = Transaction(None)
    ref = Mock()
    ref._document_path = f"projects/unit/databases/(default)/documents/{collection}/theme"
    ref.get.return_value.to_dict.return_value = {}
    client = SimpleNamespace(collection=lambda name: SimpleNamespace(document=lambda item_id: ref),
                             transaction=lambda: transaction)
    monkeypatch.setattr(config, "LOCAL_MODE", False)
    monkeypatch.setattr(store, "_db", client)
    monkeypatch.setattr(store.firestore, "transactional", lambda function: function)
    design = issue_builder.newsletter_design(theme(story_rows=[{"cells": [[], ["title"]]},
                                                  {"cells": [["details", "picture"]]}]), "custom")
    payload = {"newsletter_template": "custom", "newsletter_design": design}
    fields = {"delivery_snapshot": payload} if snapshot else payload
    before = deepcopy(fields)
    key = (collection, "theme")
    result = store.atomic_documents([key], lambda rows: {key: fields})
    write = transaction._write_pbs[0]
    for value in write.update.fields.values():
        assert_firestore_has_no_direct_nested_arrays(value)
    decoded = _helpers.decode_dict(write.update.fields, None)
    stored = decoded["delivery_snapshot"] if snapshot else decoded
    assert stored["newsletter_design"]["story_rows"][0]["cells"][0] == {"blocks": []}
    assert issue_builder.newsletter_design(stored["newsletter_design"], "custom") == design
    assert result[key] == fields == before  # do not mutate API or in-memory transaction results
    assert set(write.update_mask.field_paths) == set(fields)
    ref.set.assert_not_called()


def test_firestore_new_issue_write_encodes_theme_and_frozen_read_restores_it(monkeypatch):
    from google.cloud.firestore_v1 import _helpers
    ref = Mock()
    monkeypatch.setattr(config, "LOCAL_MODE", False)
    monkeypatch.setattr(store, "_db", SimpleNamespace(collection=lambda name:
                        SimpleNamespace(document=lambda item_id: ref)))
    design = issue_builder.newsletter_design(theme(), "custom")
    issue = {"id": "cloud-theme", "status": "sent", "week_start": "2099-11-09", "items": [],
             "item_ids": [], "editor_plan": {}, "newsletter_template": "custom", "newsletter_design": design}
    assert store.save_issue(issue) == "cloud-theme"
    persisted = ref.set.call_args.args[0]
    protobuf = _helpers.encode_dict(persisted)
    for value in protobuf.values():
        assert_firestore_has_no_direct_nested_arrays(value)
    saved = _helpers.decode_dict(protobuf, None)
    edition, _ = issue_builder.from_saved({"delivery_frozen": True, "delivery_snapshot": saved})
    assert edition["newsletter_design"] == design
    assert issue["newsletter_design"] == design


def test_cloud_encoded_settings_are_returned_to_editor_as_lists(api, theme_db):
    # Simulate an existing cloud row through the same raw settings-read path.
    encoded = store._firestore_publication_fields({"newsletter_template": "custom", "newsletter_design": theme()})
    theme_db.collection("settings").document("publication").set(encoded)
    response = api.get(PATH, headers=STAFF)
    assert response.status_code == 200
    assert response.json()["design"]["header_rows"] == HEADER
    assert response.json()["design"]["story_rows"] == STORY


@pytest.mark.parametrize("frozen", [False, True])
def test_raw_cloud_issue_stream_restores_theme_in_preview_metadata(api, theme_db, frozen):
    issue_id = issue_builder.build_preview(date(2099, 11, 9))[0]
    document = store.get_issue(issue_id)
    document.update(newsletter_template="custom", newsletter_design=theme())
    if frozen:
        snapshot = {**document, "status": "sent"}
        document.update(status="sent", delivery_frozen=True, delivery_snapshot=snapshot,
                        newsletter_template="classic", newsletter_design=None)
    encoded = store._firestore_publication_fields(document)
    assert store._firestore_publication_fields(encoded) == encoded
    theme_db.collection("issues").document(issue_id).set(encoded)
    response = api.get("/admin/api/preview", headers=STAFF)
    assert response.status_code == 200
    result = response.json()["issue"]
    assert result["newsletter_template"] == "custom"
    assert result["newsletter_design"]["header_rows"] == HEADER
    assert result["newsletter_design"]["story_rows"] == STORY
    assert result["layout_editable"] is not frozen


def test_storage_cell_wrappers_cannot_bypass_api_validation_or_preserve_unknown_fields(api):
    encoded = store._firestore_publication_fields({"newsletter_design": theme()})["newsletter_design"]
    assert api.put(PATH, headers=STAFF, json={"template": "custom", "design": encoded}).status_code == 422
    encoded["story_rows"][0]["cells"][0]["html"] = "<script>bad</script>"
    assert issue_builder.newsletter_design(encoded, "classic") is None
