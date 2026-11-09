"""Local mode, saved events + 1-hour reminders, and the club report. Run: pytest -q (no cloud)."""
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("AUDIT_DISABLED", "true")

import pytest                                           # noqa: E402
from fastapi.testclient import TestClient               # noqa: E402

from backend import config, issue_builder, main, reminders, store, tokens  # noqa: E402
from backend.localdb import LocalClient                 # noqa: E402
from shared.schemas import EditorPlan                   # noqa: E402

MYT = ZoneInfo("Asia/Kuala_Lumpur")
REP, OTHER_REP, SA, STUDENT = ("rep@mail.apu.edu.my", "other@mail.apu.edu.my",
                               "sa@apu.edu.my", "student@mail.apu.edu.my")


def _event(item_id, club, date="2099-11-11", start="16:00", status="approved", link="https://forms.gle/x"):
    return {"id": item_id, "type": "event", "status": status, "club_id": club, "submitted_by": REP,
            "issues": [], "data": {"title": f"Event {item_id}", "organiser": "Club", "date": date,
                                   "start_time": start, "venue": "Hall", "fee": "Free",
                                   "registration": {"required": bool(link), "link": link}}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh local database + outbox per test; never touches local_data/."""
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    monkeypatch.delenv("GMAIL_TOKEN_JSON", raising=False)
    client = LocalClient(tmp_path / "db.json")
    monkeypatch.setattr(store, "_db", client)
    client.collection("roles").document(REP).set({"role": "club_rep", "club_id": "club-a"})
    client.collection("roles").document(OTHER_REP).set({"role": "club_rep", "club_id": "club-b"})
    client.collection("roles").document(SA).set({"role": "student_affairs"})
    store.upsert_subscriber({"email": STUDENT, "interests": ["tech"], "active": True})
    return client


@pytest.fixture
def api(db):
    return TestClient(main.app)


# ---------- campus handles event and venue approval ----------


@pytest.mark.parametrize("existing_status", ["pending", "approved", "published"])
@pytest.mark.parametrize("action", ["submit", "edit"])
def test_overlapping_events_can_reach_poster_review(db, api, monkeypatch, existing_status, action):
    monkeypatch.setattr(main, "publish", lambda *args: None)
    existing = _event("other-club", "club-b", status=existing_status)
    existing["data"]["end_time"] = "18:00"
    store.create_item(existing)
    data = {**_event("new-poster", "club-a")["data"], "start_time": "17:00", "end_time": "19:00"}
    payload = {"user_email": REP, "type": "event", "data": data}

    if action == "submit":
        response = api.post("/items", json=payload)
    else:
        original = _event("new-poster", "club-a", date="2099-11-12")
        store.create_item({**original, "approved_by": SA})
        response = api.patch("/items/new-poster", json=payload)

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "pending" and result["issues"] == []
    item = store.get_item(result["id"])
    assert item.get("approved_by") is None
    assert item["data"]["venue"] == "Hall" and item["data"]["start_time"] == "17:00:00"
    decision = api.post(f"/items/{result['id']}/decision", json={"user_email": SA, "decision": "approve"})
    assert decision.status_code == 200 and decision.json()["status"] == "approved"
    assert store.get_item("other-club")["status"] == existing_status


@pytest.mark.parametrize("missing_fee", [False, True])
def test_resubmitting_old_draft_clears_clash_warning_and_keeps_content_checks(db, api, missing_fee):
    store.create_item(_event("other-club", "club-b"))
    draft = _event("old-draft", "club-a", status="draft")
    draft["issues"] = ['Clashes with "Event other-club" at the same venue and time']
    if missing_fee:
        draft["data"]["fee"] = None
        draft["issues"].append("Missing: fee")
    store.create_item(draft)

    response = api.patch("/items/old-draft", json={"user_email": REP, "type": "event", "data": {}})

    assert response.status_code == 200
    expected = ["Missing: fee"] if missing_fee else []
    assert response.json()["issues"] == expected
    assert response.json()["status"] == ("draft" if missing_fee else "pending")
    assert store.get_item("old-draft")["issues"] == expected


# ---------- local Firestore stand-in ----------

def test_localdb_queries_and_persistence(tmp_path):
    c = LocalClient(tmp_path / "x.json")
    when = datetime(2026, 11, 9, 8, tzinfo=timezone.utc)
    c.collection("issues").document("a").set({"status": "preview", "week_start": "2026-11-02", "at": when})
    c.collection("issues").document("b").set({"status": "preview", "week_start": "2026-11-09"})
    c.collection("issues").document("c").set({"status": "sent", "week_start": "2026-11-16"})
    c.collection("issues").document("a").update({"note": "hi"})
    latest = list(c.collection("issues").where("status", "==", "preview")
                  .order_by("week_start", direction="DESCENDING").limit(1).stream())
    assert [s.id for s in latest] == ["b"]
    assert {s.id for s in c.collection("issues").where("status", "in", ["sent", "preview"]).stream()} == {"a", "b", "c"}
    reloaded = LocalClient(tmp_path / "x.json").collection("issues").document("a").get().to_dict()
    assert reloaded["at"] == when and reloaded["note"] == "hi"     # datetimes survive a restart
    assert not c.collection("issues").document("zzz").get().exists


# ---------- reminders ----------

def test_reminder_window():
    item = _event("e1", "club-a", date="2026-11-11", start="16:00")
    start = datetime(2026, 11, 11, 16, 0, tzinfo=MYT)
    assert reminders.is_due(item, start - timedelta(minutes=60))
    assert reminders.is_due(item, start - timedelta(minutes=89))
    assert not reminders.is_due(item, start - timedelta(minutes=120))   # too early, next hourly run
    assert not reminders.is_due(item, start + timedelta(minutes=1))     # already started
    assert not reminders.is_due({**item, "status": "rejected"}, start - timedelta(minutes=60))


def test_email_links_save_event_and_remind_once(db, api):
    store.create_item(_event("e1", "club-a", date="2026-11-11", start="16:00"))
    issue = {"events": [store.get_item("e1")], "announcements": [], "achievements": [],
             "week_start": datetime(2026, 11, 9).date(), "week_end": datetime(2026, 11, 15).date()}
    html = issue_builder.render_email(issue, EditorPlan(), store.get_subscriber(STUDENT))
    s = tokens.sign(STUDENT, "save")
    assert f"/r/e1?s={s}" in html          # "Open link" under the poster saves the event for a reminder

    assert api.get(f"/r/e1?s={s}", follow_redirects=False).status_code == 302
    assert api.get(f"/cal/e1.ics?s={s}").status_code == 200             # second save: no duplicate
    assert api.get("/cal/e1.ics?s=forged").status_code == 200           # bad token: file still served, nothing saved
    assert len(store.unreminded_saves()) == 1

    now = datetime(2026, 11, 11, 15, 0, tzinfo=MYT)
    assert reminders.run(now)["sent"] == 1
    assert reminders.run(now)["sent"] == 0                              # never twice
    outbox = list((config.LOCAL_DATA_DIR / "outbox").glob("*.html"))
    assert len(outbox) == 1 and "Starting soon" in outbox[0].read_text(encoding="utf-8")


def test_unsubscribed_student_gets_no_reminder(db):
    store.create_item(_event("e1", "club-a", date="2026-11-11", start="16:00"))
    store.save_event(STUDENT, "e1", "calendar")
    store.upsert_subscriber({"email": STUDENT, "active": False})
    result = reminders.run(datetime(2026, 11, 11, 15, 0, tzinfo=MYT))
    assert result == {"sent": 0, "skipped": 1, "failed": []}
    assert not store.unreminded_saves()


# ---------- club report ----------

def test_club_report_shows_only_own_club(db, api):
    store.create_item(_event("a1", "club-a"))
    store.create_item(_event("b1", "club-b"))
    store.log_click("a1", "register")
    store.log_click("a1", "calendar")
    store.save_event(STUDENT, "a1", "calendar")

    rep = api.get("/admin/api/club", headers={"X-Dev-User": REP}).json()
    assert [r["id"] for r in rep["rows"]] == ["a1"]
    assert rep["totals"] == {"register": 1, "calendar": 1, "saved": 1}

    sa = api.get("/admin/api/club", headers={"X-Dev-User": SA}).json()
    assert {r["id"] for r in sa["rows"]} == {"a1", "b1"}

    assert api.get("/admin/api/club", headers={"X-Dev-User": STUDENT}).status_code == 403


def test_local_poster_path_cannot_escape_posters_folder(db, api):
    r = api.post("/items/extract", json={"user_email": REP, "type": "event", "gcs_uri": "local:../db.json"})
    assert r.status_code == 404


@pytest.mark.parametrize("filename", ["poster.pdf", "renamed.png"])
def test_pdf_is_rejected_before_ai_extraction(db, api, tmp_path, monkeypatch, filename):
    from unittest.mock import Mock
    posters = tmp_path / "posters"
    posters.mkdir()
    (posters / filename).write_bytes(b"%PDF-1.7\nposter")
    extraction = Mock()
    monkeypatch.setattr(main.extract, "extract", extraction)

    response = api.post("/items/extract", json={
        "user_email": REP, "type": "event", "gcs_uri": f"local:posters/{filename}"})

    assert response.status_code == 415
    assert "PNG or JPG" in response.json()["detail"]
    extraction.assert_not_called()


def test_rebuilt_preview_replaces_the_older_one(db, monkeypatch):
    from datetime import date
    from backend import editor
    monkeypatch.setattr(editor, "plan_issue", lambda items: (editor.fallback_plan(items), {"source": "test"}))
    store.create_item(_event("e1", "club-a", date="2026-11-11"))
    first, _ = issue_builder.build_preview(date(2026, 11, 9))
    second, _ = issue_builder.build_preview(date(2026, 11, 9))
    assert store.get_issue(first)["status"] == "superseded"
    assert store.get_issue(second)["status"] == "preview"


# ---------- approval is checked again when an issue is sent ----------

def _preview_for(monkeypatch, *items):
    from backend import editor
    monkeypatch.setattr(editor, "plan_issue", lambda rows: (editor.fallback_plan(rows), {"source": "test"}))
    for item in items:
        store.create_item(item)
    issue_id, _ = issue_builder.build_preview(date(2099, 11, 9))
    return issue_id


@pytest.fixture
def sent_emails(monkeypatch):
    messages = []

    def send(to, subject, html, unsubscribe_url):
        messages.append({"to": to, "subject": subject, "html": html})
        return "test-message"

    monkeypatch.setattr(main.mailer, "send", send)
    return messages


@pytest.mark.parametrize("via", ["web", "agent"])
def test_rejected_preview_item_is_excluded_from_send(db, api, monkeypatch, sent_emails, via):
    issue_id = _preview_for(monkeypatch, _event("withdrawn", "club-a"),
                            _event("kept", "club-a", date="2099-11-12"))
    if via == "web":
        decision = api.post("/admin/api/items/withdrawn/decision", json={"decision": "reject", "note": "Event cancelled."},
                            headers={"X-Dev-User": SA})
    else:
        decision = api.post("/items/withdrawn/decision", json={"user_email": SA, "decision": "reject", "note": "Event cancelled."})
    assert decision.status_code == 200
    preview = api.get(f"/m/{issue_id}")
    assert "Event withdrawn" not in preview.text and "Event kept" in preview.text

    result = api.post("/issues/send").json()

    assert result["sent"] == 1 and result["excluded_item_ids"] == ["withdrawn"]
    newsletters = [m for m in sent_emails if m["to"] == STUDENT]
    assert len(newsletters) == 1
    assert "Event withdrawn" not in newsletters[0]["subject"] + newsletters[0]["html"]
    assert "Event kept" in newsletters[0]["html"]
    assert store.get_item("withdrawn")["status"] == "rejected"
    assert store.get_item("kept")["status"] == "published"
    assert store.get_issue(issue_id)["item_ids"] == ["kept"]
    assert api.post("/issues/send").json()["sent"] == 0  # an already-sent edition is not selected again


def test_edited_item_waits_for_new_approval_then_uses_new_details(db, api, monkeypatch, sent_emails):
    issue_id = _preview_for(monkeypatch, {**_event("edited", "club-a"), "approved_by": SA})
    edited = api.patch("/items/edited", json={
        "user_email": REP, "type": "event", "data": {"title": "Updated workshop", "venue": "New Hall"}})
    assert edited.json()["status"] == "pending" and store.get_item("edited")["approved_by"] is None

    result = api.post("/issues/send").json()
    assert result["sent"] == 0 and result["reason"] == "no approved items remain"
    assert sent_emails == [] and store.get_issue(issue_id)["status"] == "preview"
    assert store.get_item("edited")["status"] == "pending"

    approved = api.post("/items/edited/decision", json={"user_email": SA, "decision": "approve"})
    assert approved.status_code == 200
    assert api.post("/issues/send").json()["sent"] == 1
    assert "Updated workshop" in sent_emails[0]["subject"] and "New Hall" in sent_emails[0]["html"]
    assert "Event edited" not in sent_emails[0]["subject"] + sent_emails[0]["html"]
    assert store.get_item("edited")["status"] == "published"


@pytest.mark.parametrize("status", ["draft", "pending", "rejected", "published"])
def test_no_email_when_no_preview_items_are_still_approved(db, api, monkeypatch, sent_emails, status):
    issue_id = _preview_for(monkeypatch, _event("removed", "club-a"))
    store.update_item("removed", status=status)

    result = api.post("/issues/send").json()

    assert result["sent"] == 0 and result["excluded_item_ids"] == ["removed"]
    assert sent_emails == [] and store.get_item("removed")["status"] == status
    assert store.get_issue(issue_id)["status"] == "preview"


def test_legacy_preview_rechecks_approval_and_drops_missing_items(db, api, sent_emails):
    store.create_item(_event("withdrawn", "club-a", status="rejected"))
    store.create_item(_event("kept", "club-a"))
    issue_id = store.save_issue({
        "week_start": "2099-11-09", "status": "preview",
        "item_ids": ["withdrawn", "missing", "kept"],
        "editor_plan": EditorPlan(headline="Event withdrawn", teaser="Event withdrawn",
                                  subject_line="Event withdrawn").model_dump(),
    })  # an existing preview saved before item snapshots were added

    assert api.post("/issues/send").json()["sent"] == 1
    assert "Event withdrawn" not in sent_emails[0]["subject"] + sent_emails[0]["html"]
    assert store.get_issue(issue_id)["item_ids"] == ["kept"]
    assert store.get_item("withdrawn")["status"] == "rejected"


def test_unchanged_preview_revalidates_its_editor_plan(db, api, monkeypatch, sent_emails):
    issue_id = _preview_for(monkeypatch, _event("kept", "club-a"))
    plan = EditorPlan(spotlight_item_id="kept", headline="A workshop for campus",
                      teaser="Join your club.", subject_line="Campus Weekly: join your club")
    store.db().collection("issues").document(issue_id).update({"editor_plan": plan.model_dump()})

    assert api.post("/issues/send").json()["sent"] == 1
    from backend import editor
    validated, _ = editor.validate(plan, store.get_issue(issue_id)["items"])
    assert sent_emails[0]["subject"] == validated.subject_line
    assert validated.headline in sent_emails[0]["html"]
    assert store.get_issue(issue_id)["editor_plan"] == validated.model_dump()


def test_sent_edition_does_not_display_later_unapproved_edits(db, api, monkeypatch, sent_emails):
    issue_id = _preview_for(monkeypatch, _event("kept", "club-a"))
    assert api.post("/issues/send").json()["sent"] == 1
    edited = api.patch("/items/kept", json={
        "user_email": REP, "type": "event", "data": {"title": "Unapproved replacement", "venue": "Unreviewed room"}})
    assert edited.json()["status"] == "pending"

    magazine = api.get(f"/m/{issue_id}")

    assert magazine.status_code == 200 and "Event kept" in magazine.text
    assert "Unapproved replacement" not in magazine.text and "Unreviewed room" not in magazine.text
    assert store.get_item("kept")["status"] == "pending"


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_delivery_completion_does_not_overwrite_a_later_decision(db, api, monkeypatch, status):
    issue_id = _preview_for(monkeypatch, _event("changing", "club-a"))

    def send(to, subject, html, unsubscribe_url):
        item = store.get_item("changing")
        store.update_item("changing", status=status, data={**item["data"], "title": "Later change"})
        return "test-message"

    monkeypatch.setattr(main.mailer, "send", send)
    assert api.post("/issues/send").json()["sent"] == 1
    assert store.get_item("changing")["status"] == status
    assert store.get_item("changing")["data"]["title"] == "Later change"
    assert store.get_issue(issue_id)["items"][0]["data"]["title"] == "Event changing"


def test_failed_delivery_keeps_approved_items_available_for_retry(db, api, monkeypatch):
    issue_id = _preview_for(monkeypatch, _event("retry", "club-a"))

    def fail(*args, **kwargs):
        # A provider rejection is safely retryable. A lost response is ambiguous and
        # must be reconciled instead of risking a duplicate email (test_delivery.py).
        from googleapiclient.errors import HttpError
        from httplib2 import Response
        raise HttpError(Response({"status": "429"}), b'{"error":{"message":"quota exhausted"}}')

    monkeypatch.setattr(main.mailer, "send", fail)
    result = api.post("/issues/send").json()
    assert result["sent"] == 0 and len(result["failed"]) == 1
    assert store.get_issue(issue_id)["status"] == "preview"
    assert store.get_item("retry")["status"] == "approved"


def test_held_preview_is_not_sent(db, api, monkeypatch, sent_emails):
    issue_id = _preview_for(monkeypatch, _event("held", "club-a"))
    assert api.post(f"/issues/{issue_id}/hold", json={"user_email": SA}).status_code == 200
    assert api.post("/issues/send").json()["sent"] == 0
    assert sent_emails == [] and store.get_item("held")["status"] == "approved"


# ---------- rejection feedback reaches the original uploader ----------

def _reject(api, item_id="notice", note="Please correct the venue.", via="web", actor=SA, retry=False):
    if via == "web":
        return api.post(f"/admin/api/items/{item_id}/decision",
                        json={"decision": "reject", "note": note, "retry_notification": retry},
                        headers={"X-Dev-User": actor})
    return api.post(f"/items/{item_id}/decision",
                    json={"user_email": actor, "decision": "reject", "note": note})


@pytest.mark.parametrize("via", ["web", "agent"])
def test_rejection_emails_uploader_and_shows_reason_in_own_club_report(db, api, sent_emails, via):
    store.create_item(_event("notice", "club-a", status="pending"))

    response = _reject(api, via=via)

    assert response.status_code == 200 and response.json()["notification_status"] == "sent"
    assert len(sent_emails) == 1 and sent_emails[0]["to"] == REP
    message = sent_emails[0]
    assert "Event notice" in message["subject"]
    assert "Please correct the venue." in message["html"] and "notice" in message["html"]
    assert "How to correct and resubmit" in message["html"] and "/admin/club" in message["html"]
    item = store.get_item("notice")
    assert item["status"] == "rejected" and item["approved_by"] is None
    assert item["rejection_reason"] == "Please correct the venue." and item["rejected_by"] == SA
    assert item["rejection_notification"]["status"] == "sent"
    own = api.get("/admin/api/club", headers={"X-Dev-User": REP}).json()["rows"]
    assert own[0]["rejection_reason"] == "Please correct the venue."
    assert own[0]["rejection_notification_status"] == "sent"
    assert api.get("/admin/api/club", headers={"X-Dev-User": OTHER_REP}).json()["rows"] == []


@pytest.mark.parametrize("via", ["web", "agent"])
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_rejection_needs_a_reason_before_status_changes_or_email(db, api, sent_emails, via, reason):
    store.create_item(_event("notice", "club-a", status="pending"))
    response = _reject(api, note=reason, via=via)
    assert response.status_code == 422 and "reason" in response.json()["detail"]
    assert store.get_item("notice")["status"] == "pending" and sent_emails == []


@pytest.mark.parametrize("via", ["web", "agent"])
def test_club_rep_cannot_trigger_rejection_or_notification(db, api, sent_emails, via):
    store.create_item(_event("notice", "club-a", status="pending"))
    assert _reject(api, via=via, actor=REP).status_code == 403
    assert store.get_item("notice")["status"] == "pending" and sent_emails == []


def test_repeated_rejection_sends_one_email(db, api, sent_emails):
    store.create_item(_event("notice", "club-a", status="pending"))
    first = _reject(api)
    second = _reject(api, via="agent")
    assert first.json()["notification_status"] == second.json()["notification_status"] == "sent"
    assert len(sent_emails) == 1


def test_repeated_request_during_email_delivery_does_not_send_twice(db, api, monkeypatch):
    from backend import reviews
    store.create_item(_event("notice", "club-a", status="pending"))
    calls = []

    def send(*args, **kwargs):
        calls.append(args)
        repeated = reviews.decide_item("notice", "reject", "Please correct the venue.", SA, via="agent")
        assert repeated["notification_status"] == "sending"
        return "test-message"

    monkeypatch.setattr(main.mailer, "send", send)
    assert _reject(api).json()["notification_status"] == "sent"
    assert len(calls) == 1


@pytest.mark.parametrize("via", ["web", "agent"])
def test_failed_rejection_email_preserves_decision_and_can_be_retried(db, api, monkeypatch, via):
    from unittest.mock import Mock
    store.create_item(_event("notice", "club-a", status="pending"))
    delivery = Mock(side_effect=[RuntimeError("test mail outage"), "test-message"])
    monkeypatch.setattr(main.mailer, "send", delivery)

    failed = _reject(api, via=via).json()

    assert failed["status"] == "rejected" and failed["notification_status"] == "failed"
    assert "warning" in failed
    assert store.get_item("notice")["rejection_reason"] == "Please correct the venue."
    rows = api.get("/admin/api/club", headers={"X-Dev-User": SA}).json()["rows"]
    assert rows[0]["rejection_notification_status"] == "failed"
    assert _reject(api, via=via, retry=True).json()["notification_status"] == "sent"
    assert _reject(api, via=via, retry=True).json()["notification_status"] == "sent"
    assert delivery.call_count == 2


@pytest.mark.parametrize("new_status,new_reason", [("pending", None), ("rejected", "Use a clearer poster.")])
def test_retry_from_an_old_report_cannot_reject_a_changed_submission(db, api, sent_emails, new_status, new_reason):
    store.create_item({**_event("notice", "club-a", status=new_status), "rejection_reason": new_reason})
    response = _reject(api, retry=True)
    assert response.status_code == 409 and "changed" in response.json()["detail"]
    item = store.get_item("notice")
    assert item["status"] == new_status and item["rejection_reason"] == new_reason
    assert sent_emails == []


def test_resubmission_clears_old_reason_and_later_rejection_sends_new_notice(db, api, sent_emails):
    store.create_item(_event("notice", "club-a", status="pending"))
    assert _reject(api).status_code == 200
    first_notice = store.get_item("notice")["rejection_notification"]["id"]
    edited = api.patch("/items/notice", json={
        "user_email": REP, "type": "event", "data": {"venue": "New Hall"}})
    assert edited.json()["status"] == "pending"
    item = store.get_item("notice")
    assert item["rejection_reason"] is None and item["rejection_notification"] is None
    assert item["rejected_by"] is None and item["rejected_at"] is None
    assert _reject(api).status_code == 200
    assert len(sent_emails) == 2 and store.get_item("notice")["rejection_notification"]["id"] != first_notice


def test_rejection_delivery_does_not_restore_reason_after_resubmission(db, api, monkeypatch):
    from backend import reviews
    store.create_item(_event("notice", "club-a", status="pending"))

    def send(*args, **kwargs):
        store.update_item("notice", status="pending", **reviews.REJECTION_FIELDS)
        return "test-message"

    monkeypatch.setattr(main.mailer, "send", send)
    assert _reject(api).status_code == 200
    item = store.get_item("notice")
    assert item["status"] == "pending" and item["rejection_reason"] is None
    assert item["rejection_notification"] is None


def test_rejection_email_escapes_content_in_local_outbox(db, api):
    from html import escape
    item = _event("notice", "club-a", status="pending")
    title = '<script>alert("title")</script>'
    reason = '<img src=x onerror=alert(1)>\nPlease correct the venue.'
    item["data"]["title"] = title
    store.create_item(item)

    assert _reject(api, note=reason).json()["notification_status"] == "sent"
    emails = list((config.LOCAL_DATA_DIR / "outbox").glob("*.html"))
    assert len(emails) == 1
    html = emails[0].read_text(encoding="utf-8")
    assert REP in html and escape(title) in html and escape(reason) in html
    assert "<script>" not in html and "<img src=x" not in html


def test_missing_uploader_email_reports_failure_without_undoing_rejection(db, api, sent_emails):
    item = _event("notice", "club-a", status="pending")
    item.pop("submitted_by")
    store.create_item(item)
    response = _reject(api).json()
    assert response["status"] == "rejected" and response["notification_status"] == "failed"
    assert "warning" in response and sent_emails == []
    assert store.get_item("notice")["rejection_reason"] == "Please correct the venue."


def test_approval_clears_rejection_feedback_without_sending_rejection_email(db, api, sent_emails):
    store.create_item({**_event("notice", "club-a", status="pending"),
                       "rejection_reason": "Old reason", "rejection_notification": {"status": "sent"}})
    response = api.post("/items/notice/decision", json={"user_email": SA, "decision": "approve"})
    assert response.status_code == 200 and response.json()["status"] == "approved"
    item = store.get_item("notice")
    assert item["rejection_reason"] is None and item["rejection_notification"] is None
    assert item["approved_by"] == SA and sent_emails == []


# ---------- club description + poster in the newsletter ----------

@pytest.mark.parametrize("link", [None, "", "   "])
def test_poster_without_separate_registration_url_can_be_submitted_and_edited(db, api, tmp_path, link):
    uri = _poster(tmp_path)
    data = _event("optional", "club-a")["data"]
    data["registration"] = {"required": True, "link": link}
    response = api.post("/items", json={"user_email": REP, "type": "event", "data": data,
                        "poster_uri": uri, "show_poster": True,
                        "description": "Use the QR on our poster to register."})
    assert response.status_code == 200 and response.json()["status"] == "pending"
    item_id = response.json()["id"]
    item = store.get_item(item_id)
    assert item["show_poster"] is True and item["data"]["registration"] == {"required": True, "link": None}
    updated = api.patch(f"/items/{item_id}", json={"user_email": REP, "type": "event", "data": {}})
    assert updated.json()["status"] == "pending" and updated.json()["issues"] == []


def test_optional_link_in_description_is_accepted_and_clickable_in_both_editions(db, api, tmp_path):
    uri = _poster(tmp_path)
    data = _event("description-link", "club-a")["data"]
    data["registration"] = {"required": True, "link": None}
    url = "https://forms.gle/form98765?group=4321&source=poster"
    response = api.post("/items", json={"user_email": REP, "type": "event", "data": data,
                        "poster_uri": uri, "show_poster": True,
                        "description": f"Register here ({url}). Bring a laptop."})
    assert response.status_code == 200 and response.json()["status"] == "pending"
    item_id = response.json()["id"]
    store.update_item(item_id, status="approved")
    issue = issue_builder.collect(date(2099, 11, 9))
    from html import escape
    for html in (issue_builder.render_email(issue, EditorPlan(), {"email": STUDENT}),
                 issue_builder.render_magazine(issue, EditorPlan(), "test-issue")):
        assert f'href="{escape(url, quote=True)}"' in html
        assert f"/p/{item_id}.jpg" in html
        assert f"/r/{item_id}" not in html and "No registration needed" not in html


@pytest.mark.parametrize("link", [None, "https://forms.gle/optional"])
def test_register_link_only_appears_when_supplied(db, link):
    item = _event("optional", "club-a", link=link)
    item["data"]["registration"]["required"] = True
    store.create_item(item)
    issue = issue_builder.collect(date(2099, 11, 9))
    for html in (issue_builder.render_email(issue, EditorPlan(), {"email": STUDENT}),
                 issue_builder.render_magazine(issue, EditorPlan(), "test-issue")):
        assert ("/r/optional" in html) == bool(link)
        assert ("Register here" in html) == bool(link)


def test_description_links_keep_html_escaped_and_unsafe_urls_unclickable():
    from html.parser import HTMLParser

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.hrefs, self.tags = [], []

        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
            if tag == "a":
                self.hrefs.append(dict(attrs)["href"])

    description = ('<script>alert("x")</script> (https://forms.gle/abc?x=1&y=2). '
                   'https://unknown.example/x http://forms.gle/y javascript:alert(1) https://[invalid/x')
    html = str(issue_builder._linkify_description(description))
    parser = Links()
    parser.feed(html)
    assert parser.hrefs == ["https://forms.gle/abc?x=1&y=2"]
    assert parser.tags == ["a"] and "&lt;script&gt;" in html


def test_description_may_not_add_facts():
    from backend import checks
    data = {"date": "2026-11-11", "start_time": "16:00", "fee": "Free"}
    assert checks.description_problems("Hands-on session, bring a laptop.", data) == []
    assert checks.description_problems("Only RM5 at 7pm!", data)          # 5 and 7 are not in the fields
    assert checks.description_problems("word " * 41, data)                 # too long


def _poster(tmp_path) -> str:
    from PIL import Image
    (tmp_path / "posters").mkdir(exist_ok=True)
    Image.new("RGB", (1200, 1500), (20, 40, 90)).save(tmp_path / "posters" / "p.png")
    return "local:posters/p.png"


def test_submit_with_description_and_poster_then_serve_it(db, api, tmp_path):
    uri = _poster(tmp_path)
    data = _event("x", "club-a")["data"]
    r = api.post("/items", json={"user_email": REP, "type": "event", "data": data, "poster_uri": uri,
                                 "description": "Hands-on session, bring a laptop.", "show_poster": True}).json()
    item = store.get_item(r["id"])
    assert r["status"] == "pending" and item["description"].startswith("Hands-on") and item["show_poster"] is True
    assert api.get(f"/p/{r['id']}.jpg").status_code == 404                  # not approved yet

    store.update_item(r["id"], status="approved")
    img = api.get(f"/p/{r['id']}.jpg")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(img.content)).width == 600                 # resized for email

    api.patch(f"/items/{r['id']}", json={"user_email": REP, "type": "event", "data": {}, "show_poster": False})
    assert store.get_item(r["id"])["description"].startswith("Hands-on")    # kept when not sent
    store.update_item(r["id"], status="approved")
    assert api.get(f"/p/{r['id']}.jpg").status_code == 404                  # club turned it off


def test_poster_choice_needs_a_poster(db, api):
    r = api.post("/items", json={"user_email": REP, "type": "event", "data": _event("y", "club-a")["data"],
                                 "show_poster": True}).json()
    assert store.get_item(r["id"])["show_poster"] is False


def test_outdated_poster_is_hidden_and_an_updated_one_restores_it(db, api, tmp_path):
    uri = _poster(tmp_path)
    poster_read = {**_event("x", "club-a")["data"], "venue": "LAB 6-01", "fee": None}
    main._remember_poster_facts(uri, poster_read)                      # what /items/extract saw

    # Rep adds the fee (blank on the poster) and keeps the venue: poster is fine to show.
    same = {**poster_read, "venue": "Lab 6-01", "fee": "Free"}          # case differences don't count
    r = api.post("/items", json={"user_email": REP, "type": "event", "data": same, "poster_uri": uri,
                                 "show_poster": True}).json()
    assert store.get_item(r["id"])["show_poster"] is True and "poster_hidden" not in r

    # Rep moves the venue: the poster now shows the wrong room, so it is hidden, and the rep is told.
    r2 = api.patch(f"/items/{r['id']}", json={"user_email": REP, "type": "event", "data": {"venue": "Lab 6-02"}}).json()
    item = store.get_item(r["id"])
    assert item["show_poster"] is False and item["poster_wanted"] is True and "venue" in r2["poster_hidden"]
    assert r2["status"] == "pending"                                    # the event itself still goes ahead

    # Rep attaches an updated poster that says Lab 6-02: it is shown again.
    new_uri = "local:posters/p2.png"
    main._remember_poster_facts(new_uri, {**poster_read, "venue": "Lab 6-02"})
    r3 = api.patch(f"/items/{r['id']}", json={"user_email": REP, "type": "event", "data": {}, "poster_uri": new_uri}).json()
    assert store.get_item(r["id"])["show_poster"] is True and "poster_hidden" not in r3


# ---------- poster board ----------

def _with_poster(item, tmp_path, show=True):
    from PIL import Image
    (tmp_path / "posters").mkdir(exist_ok=True)
    Image.new("RGB", (900, 1125), (18, 32, 74)).save(tmp_path / "posters" / f"{item['id']}.png")
    return {**item, "poster_uri": f"local:posters/{item['id']}.png", "show_poster": show}


def _link_with_binary_period(purpose):
    import base64
    for n in range(1000):
        value = f"case{n}@mail.apu.edu.my"
        link = tokens.sign(value, purpose)
        raw = base64.urlsafe_b64decode(link + "=" * (-len(link) % 4))
        if b"." in raw[-16:]:
            return value, link
    raise AssertionError("No period-containing signature found for the test")


@pytest.mark.parametrize("path,query,purpose", [
    ("/preferences", "token", "prefs"), ("/unsubscribe", "token", "prefs"),
    ("/subscribe/verify", "t", "verify"),
])
def test_subscription_pages_accept_previously_broken_links(db, api, monkeypatch, path, query, purpose):
    monkeypatch.setattr(tokens, "_SECRET", b"boardcast-link-regression-test")
    email, link = _link_with_binary_period(purpose)
    store.upsert_subscriber({"email": email, "active": True})
    assert api.get(path, params={query: link}).status_code == 200
    assert store.get_subscriber(email)["active"] == (path != "/unsubscribe")
    assert api.get(path, params={query: "!invalid!"}).status_code == 400


def test_registration_and_calendar_accept_previously_broken_reminder_links(db, api, monkeypatch):
    monkeypatch.setattr(tokens, "_SECRET", b"boardcast-link-regression-test")
    email, link = _link_with_binary_period("save")
    store.upsert_subscriber({"email": email, "active": True})
    store.create_item(_event("save-case", "club-a"))
    assert api.get("/r/save-case", params={"s": link}, follow_redirects=False).status_code == 302
    assert api.get("/cal/save-case.ics", params={"s": link}).status_code == 200
    saves = store.unreminded_saves()
    assert len(saves) == 1 and saves[0]["email"] == email


def test_staff_preview_accepts_previously_broken_poster_link(db, api, tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "_SECRET", b"boardcast-link-regression-test")
    item_id, link = _link_with_binary_period("poster")
    store.create_item(_with_poster(_event(item_id, "club-a", status="pending"), tmp_path))
    assert api.get(f"/p/{item_id}.jpg").status_code == 404
    assert api.get(f"/p/{item_id}.jpg", params={"t": link}).status_code == 200
    assert api.get(f"/p/{item_id}.jpg", params={"t": "!invalid!"}).status_code == 404


def test_staff_link_shows_a_pending_poster_but_the_public_cannot(db, api, tmp_path):
    store.create_item(_with_poster(_event("p1", "club-a", status="pending"), tmp_path))
    store.create_item(_with_poster(_event("p2", "club-a", status="pending"), tmp_path))
    assert api.get("/p/p1.jpg").status_code == 404                                  # not approved: private
    staff = main.staff_poster_url("p1")
    r = api.get(staff)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "no-store" in r.headers["cache-control"]                                 # never cached
    assert api.get(staff.replace("p1.jpg", "p2.jpg")).status_code == 404           # link is for one poster only
    assert api.get("/p/p1.jpg?t=forged").status_code == 404
    queue = api.get("/admin/api/queue", headers={"X-Dev-User": SA}).json()["items"]
    assert queue[0]["poster_url"].startswith("/p/") and "?t=" in queue[0]["poster_url"]


def test_email_reads_like_a_newspaper_of_club_submissions(db, tmp_path):
    store.create_item(_with_poster({**_event("e1", "club-a", date="2026-11-11"),
                                    "description": "Hands-on session, bring a laptop."}, tmp_path))
    store.create_item(_event("e2", "club-a", date="2026-11-12", link=None))          # typed in chat, no poster
    store.create_item({"id": "a1", "type": "announcement", "status": "approved", "submitted_by": SA, "issues": [],
                       "data": {"title": "Ambassadors wanted", "organiser": "Student Affairs",
                                "summary": "Represent APU at open days.", "action_link": "https://forms.office.com/x"}})
    issue = {"events": [store.get_item("e1"), store.get_item("e2")], "announcements": [store.get_item("a1")],
             "achievements": [], "week_start": datetime(2026, 11, 9).date(), "week_end": datetime(2026, 11, 15).date()}
    plan = EditorPlan(spotlight_item_id="e2", headline="Event e2, plus 1 more event coming up",
                      teaser="Event e1 on Wednesday.", subject_line="Campus Weekly: Event e2")
    html = issue_builder.render_email(issue, plan, store.get_subscriber(STUDENT))
    from backend import editor
    validated, _ = editor.validate(plan, issue_builder.all_items(issue))
    assert validated.headline in html and validated.teaser in html
    assert "1. COMING UP" in html and "2. EVENTS" in html and "3. OPPORTUNITIES" in html
    assert "CAMPUS WINS" not in html                                               # empty sections are left out
    assert html.index("Event e2</b>") < html.index("Event e1</b>", html.index("2. EVENTS"))   # spotlight leads
    assert "Hands-on session, bring a laptop." in html                             # the club's own words
    assert "Represent APU at open days." in html
    assert '/p/e1.jpg' in html and 'alt="Poster: Event e1, Wed 11 Nov 4:00pm, Hall"' in html
    assert "/p/e2.jpg" not in html                                                 # no poster: text only
    assert "/r/e1?s=" in html and "Register here" in html                           # tracked, saves the event
    assert "/r/a1?s=" in html and "Find out more" in html
    assert "Picked for you" not in html                                          # one edition for everyone
    assert "Add to calendar" in html and "/cal/e1.ics?s=" in html


def test_open_link_works_for_announcements(db, api):
    store.create_item({"id": "a1", "type": "announcement", "status": "approved", "submitted_by": SA, "issues": [],
                       "data": {"title": "x", "organiser": "y", "summary": "z", "action_link": "https://forms.office.com/x"}})
    r = api.get("/r/a1", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://forms.office.com/x"


def test_issue_carries_everything_approved_on_any_upcoming_date(db):
    from datetime import date
    store.create_item(_event("next-week", "club-a", date="2026-11-11"))
    store.create_item(_event("next-month", "club-a", date="2026-12-15"))                    # weeks away: still in
    store.create_item(_event("past", "club-a", date="2026-11-01"))                          # already happened: out
    store.create_item(_event("sent-before", "club-a", date="2026-11-20", status="published"))  # printed once only
    store.create_item(_event("not-approved", "club-a", date="2026-11-12", status="pending"))
    store.create_item({"id": "closed", "type": "announcement", "status": "approved", "submitted_by": SA, "issues": [],
                       "data": {"title": "Old call", "organiser": "SA", "summary": "x", "deadline": "2026-11-02T23:59:00"}})
    store.create_item({"id": "open", "type": "announcement", "status": "approved", "submitted_by": SA, "issues": [],
                       "data": {"title": "New call", "organiser": "SA", "summary": "x", "deadline": "2026-12-01T23:59:00"}})
    issue = issue_builder.collect(date(2026, 11, 9))
    assert [e["id"] for e in issue["events"]] == ["next-week", "next-month"]
    assert [a["id"] for a in issue["announcements"]] == ["open"]
