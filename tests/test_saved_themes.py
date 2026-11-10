"""Named theme library boundaries, persistence and concurrent editing."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend import config, main, newsletter_themes, store
from backend.localdb import LocalClient

PATH = "/admin/api/newsletter-themes"
STAFF = {"X-Dev-User": "themes@apu.edu.my"}
HEADER = [{"cells": [["date"], ["name"]]}, {"cells": [["intro"]]}]
STORY = [{"cells": [["title"]]}, {"cells": [["details"], ["picture"]]}]


@pytest.fixture
def library_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DISABLED", "true")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "DEV_MODE", True)
    database = LocalClient(tmp_path / "themes.json")
    monkeypatch.setattr(store, "_db", database)
    database.collection("roles").document("themes@apu.edu.my").set({"role": "student_affairs"})
    database.collection("roles").document("rep@apu.edu.my").set({"role": "club_rep", "club_id": "test"})
    return database


@pytest.fixture
def api(library_db):
    with TestClient(main.app) as client:
        yield client


def design(**changes):
    return {"header_rows": deepcopy(HEADER), "story_rows": deepcopy(STORY),
            "paper_color": "#FFF5E6", "headline_font": "georgia", **changes}


def create(api, name="Campus editorial", **changes):
    response = api.post(PATH, headers=STAFF, json={"name": name, "design": design(**changes)})
    assert response.status_code == 201, response.text
    return response.json()["theme"]


@pytest.mark.parametrize("headers,expected", [({}, 401), ({"X-Dev-User": "rep@apu.edu.my"}, 403),
                                             ({"X-Dev-User": "outsider@apu.edu.my"}, 403)])
def test_library_endpoints_require_student_affairs(api, library_db, headers, expected):
    before = library_db.path.read_bytes()
    assert api.get(PATH, headers=headers).status_code == expected
    assert api.post(PATH, headers=headers, json={"name": "Test", "design": {}}).status_code == expected
    assert api.put(PATH + "/" + "a" * 32, headers=headers,
                   json={"name": "Test", "revision": 1}).status_code == expected
    assert library_db.path.read_bytes() == before


@pytest.mark.parametrize("name", ["", "   ", "a" * 61, "line\nbreak", "tab\tname", "zero\x00name",
                                  "hidden\u200bname", 12, None, True])
def test_invalid_names_never_write(api, library_db, name):
    before = library_db.path.read_bytes()
    assert api.post(PATH, headers=STAFF, json={"name": name, "design": {}}).status_code == 422
    assert library_db.path.read_bytes() == before


def test_library_is_empty_then_sorted_shared_and_persistent_after_reopen(api, library_db, monkeypatch):
    assert api.get(PATH, headers=STAFF).json() == {"themes": []}
    first = create(api, "  Science   Ｗeek ", lead_id="old-story", story_order=["old-story"], column_map={"old-story": 3})
    second = create(api, "Arts & culture")
    assert first["name"] == "Science Week"
    assert set(first) == {"id", "name", "design", "revision"}
    assert first["revision"] == 1 and len(first["id"]) == 32
    assert not {"lead_id", "story_order", "column_map"}.intersection(first["design"])
    assert first["design"]["story_rows"] == STORY
    monkeypatch.setattr(store, "_db", LocalClient(library_db.path))
    library_db.collection("roles").document("other@apu.edu.my").set({"role": "student_affairs"})
    monkeypatch.setattr(store, "_db", LocalClient(library_db.path))
    assert api.get(PATH, headers={"X-Dev-User": "other@apu.edu.my"}).json()["themes"] == [second, first]


def test_names_are_unique_after_casefold_and_unicode_normalization(api, library_db):
    create(api, "Straße Theme")
    before = library_db.path.read_bytes()
    for name in ["STRASSE THEME", " strasse  theme "]:
        result = api.post(PATH, headers=STAFF, json={"name": name, "design": {}})
        assert result.status_code == 409
    assert library_db.path.read_bytes() == before


def test_rename_preserves_design_and_releases_old_name(api):
    saved = create(api)
    response = api.put(PATH + "/" + saved["id"], headers=STAFF,
                       json={"name": "Newspaper for November", "revision": saved["revision"]})
    assert response.status_code == 200
    renamed = response.json()["theme"]
    assert renamed["id"] == saved["id"]
    assert renamed["name"] == "Newspaper for November" and renamed["revision"] == 2
    assert renamed["design"] == saved["design"]
    create(api, saved["name"])


def test_update_replaces_saved_appearance_and_strips_story_assignments(api):
    saved = create(api)
    next_design = design(paper_color="#AABBCC", story_rows=[{"cells": [["picture", "title", "details"]]}],
                         lead_id="another", column_map={"another": 2}, story_order=["another"])
    result = api.put(PATH + "/" + saved["id"], headers=STAFF,
                     json={"name": saved["name"], "revision": 1, "design": next_design})
    assert result.status_code == 200
    updated = result.json()["theme"]
    assert updated["revision"] == 2 and updated["design"]["paper_color"] == "#AABBCC"
    assert updated["design"]["story_rows"] == next_design["story_rows"]
    assert not {"lead_id", "story_order", "column_map"}.intersection(updated["design"])


def test_stale_updates_and_conflicting_renames_are_atomic(api, library_db):
    saved, other = create(api), create(api, "Other")
    api.put(PATH + "/" + saved["id"], headers=STAFF, json={"name": "New name", "revision": 1})
    before = library_db.path.read_bytes()
    assert api.put(PATH + "/" + saved["id"], headers=STAFF,
                   json={"name": "Lost update", "revision": 1, "design": {}}).status_code == 409
    assert api.put(PATH + "/" + saved["id"], headers=STAFF,
                   json={"name": other["name"].upper(), "revision": 2}).status_code == 409
    assert library_db.path.read_bytes() == before


@pytest.mark.parametrize("revision", [None, 0, -1, True, "1", 1.1])
def test_update_requires_a_strict_positive_revision(api, revision):
    saved = create(api)
    assert api.put(PATH + "/" + saved["id"], headers=STAFF,
                   json={"name": "Renamed", "revision": revision}).status_code == 422


def test_invalid_design_and_extra_fields_cannot_enter_library(api, library_db):
    saved = create(api)
    before = library_db.path.read_bytes()
    for fields in [{"design": None}, {"design": {"accent_color": "red;display:none"}},
                   {"design": {"story_rows": [{"cells": [["title"]]}]}}, {"html": "<script>"}]:
        assert api.put(PATH + "/" + saved["id"], headers=STAFF,
                       json={"name": "Updated", "revision": 1, **fields}).status_code == 422
    assert api.post(PATH, headers=STAFF, json={"name": "New"}).status_code == 422
    assert library_db.path.read_bytes() == before


def test_unknown_and_invalid_ids_are_not_found_without_writes(api, library_db):
    before = library_db.path.read_bytes()
    for theme_id in ["not-a-theme", "a" * 32, "a" * 128, "bad.dot"]:
        assert api.put(PATH + "/" + theme_id, headers=STAFF,
                       json={"name": "New name", "revision": 1}).status_code == 404
    assert library_db.path.read_bytes() == before


def test_capacity_does_not_prevent_renaming_or_updating(api, library_db, monkeypatch):
    monkeypatch.setattr(newsletter_themes, "MAX_THEMES", 2)
    first, second = create(api, "One"), create(api, "Two")
    before = library_db.path.read_bytes()
    assert api.post(PATH, headers=STAFF, json={"name": "Three", "design": {}}).status_code == 409
    assert library_db.path.read_bytes() == before
    assert api.put(PATH + "/" + first["id"], headers=STAFF,
                   json={"name": "Renamed", "revision": 1, "design": {}}).status_code == 200


def test_library_changes_do_not_change_defaults_issues_or_frozen_snapshots(api, library_db):
    publication = {"newsletter_template": "custom", "newsletter_design": design()}
    issue = {"id": "issue", "status": "sent", "delivery_frozen": True,
             "newsletter_design": design(), "delivery_snapshot": deepcopy(publication)}
    library_db.collection("settings").document("publication").set(publication)
    library_db.collection("issues").document("issue").set(issue)
    library_db.collection("issues").document("editable").set({"id": "editable", "status": "preview", **publication})
    before_publication = deepcopy(library_db.data["settings"]["publication"])
    before_issues = deepcopy(library_db.data["issues"])
    saved = create(api)
    api.put(PATH + "/" + saved["id"], headers=STAFF,
            json={"name": "Changed", "revision": 1, "design": design(paper_color="#112233")})
    assert library_db.data["settings"]["publication"] == before_publication
    assert library_db.data["issues"] == before_issues


def test_unsafe_stored_entries_are_not_exposed_and_cloud_cells_decode(api, library_db):
    saved = create(api)
    record = library_db.collection(newsletter_themes.COLLECTION).document(saved["id"]).get().to_dict()
    encoded = store._firestore_publication_fields(record)
    library_db.collection(newsletter_themes.COLLECTION).document(saved["id"]).set(encoded)
    library_db.collection(newsletter_themes.COLLECTION).document("b" * 32).set({"name": "Broken", "revision": 1,
                         "newsletter_design": {"story_rows": []}, "updated_by": "secret@example.test"})
    assert api.get(PATH, headers=STAFF).json() == {"themes": [saved]}


def _assert_no_nested_arrays(value):
    kind = value._pb.WhichOneof("value_type")
    if kind == "array_value":
        for child in value.array_value.values:
            assert child._pb.WhichOneof("value_type") != "array_value"
            _assert_no_nested_arrays(child)
    elif kind == "map_value":
        for child in value.map_value.fields.values():
            _assert_no_nested_arrays(child)


def test_real_firestore_write_protobuf_preserves_library_rows_without_nested_arrays(monkeypatch):
    from google.cloud.firestore_v1 import _helpers
    from google.cloud.firestore_v1.transaction import Transaction
    transaction = Transaction(None)
    refs = {}

    def document(collection, theme_id):
        ref = refs.setdefault((collection, theme_id), Mock())
        ref._document_path = f"projects/unit/databases/(default)/documents/{collection}/{theme_id}"
        ref.get.return_value.to_dict.return_value = None
        return ref

    client = SimpleNamespace(collection=lambda collection: SimpleNamespace(document=lambda theme_id: document(collection, theme_id)),
                             transaction=lambda: transaction)
    monkeypatch.setattr(config, "LOCAL_MODE", False)
    monkeypatch.setattr(store, "_db", client)
    monkeypatch.setattr(store.firestore, "transactional", lambda function: function)
    saved = newsletter_themes.create_theme(newsletter_themes.NewTheme(name="Cloud", design=design()), "staff@apu.edu.my")
    assert len(transaction._write_pbs) == 2
    for write in transaction._write_pbs:
        for value in write.update.fields.values():
            _assert_no_nested_arrays(value)
    record = _helpers.decode_dict(transaction._write_pbs[1].update.fields, None)
    assert record["newsletter_design"]["story_rows"][0]["cells"][0] == {"blocks": ["title"]}
    assert newsletter_themes._public_theme(saved["id"], record) == saved
    for ref in refs.values():
        ref.get.assert_called_once_with(transaction=transaction)
        ref.set.assert_not_called()
