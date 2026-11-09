"""Offline regressions for partial sends, concurrent workers and uncertain outcomes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event

import pytest
from fastapi import HTTPException
from googleapiclient.errors import HttpError
from httplib2 import Response

from backend import config, delivery, editor, issue_builder, main, store
from backend.localdb import LocalClient


@pytest.fixture
def edition(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DISABLED", "true")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(store, "_db", LocalClient(tmp_path / "db.json"))
    monkeypatch.setattr(editor, "plan_issue", lambda items: (editor.fallback_plan(items), {"source": "test"}))
    store.create_item({"id": "event", "type": "event", "status": "approved", "data": {
        "title": "Approved workshop", "organiser": "Club", "date": "2099-11-11",
        "start_time": "16:00", "venue": "Original hall", "fee": "Free",
        "registration": {"required": False}}})
    for email in ("a@mail.apu.edu.my", "b@mail.apu.edu.my"):
        store.upsert_subscriber({"email": email, "active": True})
    issue_id, _ = issue_builder.build_preview(date(2099, 11, 9))
    return issue_id


def reject_request():
    return HttpError(Response({"status": "429"}), b'{"error":{"message":"quota exhausted"}}')


def test_partial_send_retries_only_failed_recipient_with_frozen_payload(edition, monkeypatch):
    calls = []

    def first(to, subject, html, unsubscribe_url):
        calls.append((to, subject, html))
        if to.startswith("b@"):
            raise reject_request()
        return "gmail-a"

    monkeypatch.setattr(main.mailer, "send", first)
    result = main.issue_send()
    assert result["sent"] == 1 and result["status"] == "partial"
    item = store.get_item("event")
    store.update_item("event", status="pending", data={**item["data"], "title": "Unapproved replacement"})
    failed_payload = calls[-1]
    store.upsert_subscriber({"email": "new@mail.apu.edu.my", "active": True})
    monkeypatch.setattr(main.mailer, "send", lambda to, subject, html, unsubscribe_url:
                        calls.append((to, subject, html)) or "gmail-b")

    result = main.issue_send()

    assert result["sent"] == 1 and result["status"] == "sent"
    assert result["delivery_summary"]["sent"] == 2
    assert [call[0] for call in calls] == ["a@mail.apu.edu.my", "b@mail.apu.edu.my", "b@mail.apu.edu.my"]
    assert calls[-1] == failed_payload
    assert "Unapproved replacement" not in calls[-1][2]
    assert store.get_item("event")["status"] == "pending"
    assert store.get_issue(edition)["items"][0]["data"]["title"] == "Approved workshop"
    assert main.issue_send()["sent"] == 0


def test_concurrent_scheduler_calls_claim_one_issue(edition, monkeypatch):
    started, release = Event(), Event()
    calls = []

    def slow_send(to, *args):
        calls.append(to)
        started.set()
        assert release.wait(5)
        return "gmail-id"

    monkeypatch.setattr(main.mailer, "send", slow_send)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(main.issue_send)
        assert started.wait(5)
        try:
            assert main.issue_send()["sent"] == 0
            assert calls == ["a@mail.apu.edu.my"]
        finally:
            release.set()
        assert first.result(timeout=5)["sent"] == 2
    assert calls == ["a@mail.apu.edu.my", "b@mail.apu.edu.my"]


def test_hold_during_delivery_stops_next_recipient_and_is_not_overwritten(edition, monkeypatch):
    calls = []

    def hold_after_acceptance(to, *args):
        calls.append(to)
        store.hold_issue(edition)
        return "gmail-id"

    monkeypatch.setattr(main.mailer, "send", hold_after_acceptance)
    result = main.issue_send()
    assert result["sent"] == 1 and result["status"] == "held"
    assert result["delivery_summary"]["pending"] == 1
    assert store.get_issue(edition)["status"] == "held"
    assert main.issue_send()["sent"] == 0
    assert calls == ["a@mail.apu.edu.my"]
    with pytest.raises(HTTPException) as blocked:
        issue_builder.build_preview(date(2099, 11, 9))
    assert blocked.value.status_code == 409


def test_unknown_provider_outcome_is_not_automatically_retried(edition, monkeypatch):
    calls = []

    def timeout(to, *args):
        calls.append(to)
        raise TimeoutError("response lost after possible acceptance")

    monkeypatch.setattr(main.mailer, "send", timeout)
    result = main.issue_send()
    assert result["sent"] == 0 and result["status"] == "needs_attention"
    assert result["delivery_summary"]["unknown"] == 2 and result["warning"]
    assert main.issue_send()["sent"] == 0
    assert len(calls) == 2


def test_expired_worker_lease_preserves_uncertain_recipient(edition, monkeypatch):
    claim = delivery._claim(edition, "crashed-worker")
    prepared, reason = delivery._prepare(claim, "crashed-worker")
    assert reason is None
    receipt = delivery._begin(prepared, "a@mail.apu.edu.my", "crashed-worker",
                              {"subject": "Frozen", "html": "Frozen", "unsubscribe_url": ""}, True)
    assert receipt["status"] == "sending"
    store.db().collection("issues").document(edition).update({"delivery_lease_until": store.now() - timedelta(seconds=1)})
    calls = []
    monkeypatch.setattr(main.mailer, "send", lambda to, *args: calls.append(to) or "gmail-b")

    result = main.issue_send()

    assert calls == ["b@mail.apu.edu.my"]
    assert result["status"] == "needs_attention"
    assert result["delivery_summary"]["unknown"] == 1
    assert result["delivery_summary"]["sent"] == 1


def test_all_definite_failures_recheck_approval_before_retry(edition, monkeypatch):
    def fail(*args):
        raise reject_request()

    monkeypatch.setattr(main.mailer, "send", fail)
    result = main.issue_send()
    assert result["status"] == "preview" and result["delivery_summary"]["failed"] == 2
    assert store.get_item("event")["status"] == "approved"
    store.update_item("event", status="rejected")
    monkeypatch.setattr(main.mailer, "send", lambda *args: pytest.fail("Rejected content must not be sent"))
    result = main.issue_send()
    assert result["sent"] == 0 and result["reason"] == "no approved items remain"


def test_unsubscribed_failed_recipient_is_skipped_on_retry(edition, monkeypatch):
    def first(to, *args):
        if to.startswith("b@"):
            raise reject_request()
        return "gmail-a"

    monkeypatch.setattr(main.mailer, "send", first)
    main.issue_send()
    store.upsert_subscriber({"email": "b@mail.apu.edu.my", "active": False})
    monkeypatch.setattr(main.mailer, "send", lambda *args: pytest.fail("No delivery is due"))
    result = main.issue_send()
    assert result["status"] == "sent" and result["delivery_summary"]["skipped"] == 1


def test_hold_checks_missing_and_completed_issue(edition, monkeypatch):
    with pytest.raises(HTTPException) as missing:
        store.hold_issue("does-not-exist")
    assert missing.value.status_code == 404
    monkeypatch.setattr(main.mailer, "send", lambda *args: "gmail-id")
    main.issue_send()
    with pytest.raises(HTTPException) as completed:
        store.hold_issue(edition)
    assert completed.value.status_code == 409


def test_recovery_job_never_starts_a_fresh_preview(edition, monkeypatch):
    monkeypatch.setattr(main.mailer, "send", lambda *args: pytest.fail("Fresh previews wait for the weekly send"))
    assert delivery.send_latest(retry_only=True)["sent"] == 0
    assert store.get_issue(edition)["status"] == "preview"


def test_recovery_job_retries_a_definitely_rejected_batch(edition, monkeypatch):
    def fail(*args):
        raise reject_request()

    monkeypatch.setattr(main.mailer, "send", fail)
    assert main.issue_send()["delivery_summary"]["failed"] == 2
    calls = []
    monkeypatch.setattr(main.mailer, "send", lambda to, *args: calls.append(to) or "gmail-id")
    result = delivery.send_latest(retry_only=True)
    assert result["sent"] == 2 and result["status"] == "sent"
    assert len(calls) == 2


def test_legacy_issue_uses_frozen_snapshot_while_delivery_is_in_progress(edition):
    store.db().collection("issues").document(edition).update({"items": None})
    claim = delivery._claim(edition, "worker")
    prepared, reason = delivery._prepare(claim, "worker")
    assert reason is None and prepared["delivery_frozen"]
    current = store.get_item("event")
    store.update_item("event", status="pending", data={**current["data"], "title": "Later unapproved edit"})
    rendered, _ = issue_builder.from_saved(store.get_issue(edition))
    assert rendered["events"][0]["data"]["title"] == "Approved workshop"
