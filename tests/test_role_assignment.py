"""All legacy routes must reject club representatives without a usable club assignment."""
import pytest
from fastapi.testclient import TestClient

from backend import config, main, store
from backend.localdb import LocalClient

REP = "unassigned@mail.apu.edu.my"
SA = "staff@apu.edu.my"


@pytest.fixture
def unassigned_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setenv("AUDIT_DISABLED", "true")
    client = LocalClient(tmp_path / "roles.json")
    monkeypatch.setattr(store, "_db", client)
    client.collection("roles").document(REP).set({"role": "club_rep"})
    client.collection("roles").document(SA).set({"role": "student_affairs"})
    store.create_item({"id": "staff-owned", "type": "event", "status": "pending",
                       "club_id": None, "submitted_by": SA, "issues": [],
                       "data": {"title": "Staff-owned event", "organiser": "Student Affairs",
                                "date": "2099-11-11", "start_time": "16:00",
                                "venue": "Hall", "fee": "Free"}})
    return client


@pytest.mark.parametrize("assignment", [None, "", "   ", 0, []])
def test_legacy_queue_rejects_missing_or_invalid_club_assignment(unassigned_db, assignment):
    unassigned_db.collection("roles").document(REP).update({"club_id": assignment})
    with TestClient(main.app) as api:
        response = api.get("/admin/api/queue", headers={"X-Dev-User": REP})
        assert response.status_code == 403
        assert "assign your account" in response.json()["detail"]
        assert api.get("/items", params={"user_email": REP}).status_code == 403


def test_legacy_edit_cannot_modify_staff_item_through_null_club_match(unassigned_db):
    original = store.get_item("staff-owned")
    with TestClient(main.app) as api:
        response = api.patch("/items/staff-owned", json={"user_email": REP, "type": "event",
                                                        "data": {"title": "Unauthorized replacement"}})
        assert response.status_code == 403
    assert store.get_item("staff-owned") == original


def test_student_affairs_can_review_without_club_assignment(unassigned_db):
    with TestClient(main.app) as api:
        response = api.get("/admin/api/queue", headers={"X-Dev-User": SA})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["staff-owned"]


def test_assigned_rep_retains_access_but_cannot_see_staff_items(unassigned_db):
    unassigned_db.collection("roles").document(REP).update({"club_id": "club-a"})
    with TestClient(main.app) as api:
        response = api.get("/admin/api/queue", headers={"X-Dev-User": REP})
    assert response.status_code == 200 and response.json()["items"] == []
