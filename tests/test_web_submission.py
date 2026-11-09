"""Web intake, source-image handling, review permissions and publication-preview regressions."""
import io
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from backend import config, editor, issue_builder, mailer, main, store, submission, tokens
from backend.localdb import LocalClient

REP = "rep.web@mail.apu.edu.my"
OTHER = "other.web@mail.apu.edu.my"
SA = "reviewer.web@apu.edu.my"
UNASSIGNED = "unassigned.web@mail.apu.edu.my"
EVENT = {"title": "Build a campus tool", "organiser": "Computing Club", "date": "2099-11-11",
         "start_time": "16:00", "end_time": "18:00", "venue": "Campus Hall", "fee": "Free",
         "registration": {"required": True, "link": "https://forms.gle/example"},
         "summary": "Bring your laptop and meet the club."}


@pytest.fixture
def web_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DISABLED", "true")
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "DEV_MODE", True)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    monkeypatch.setattr(config, "PROJECT_ID", "")
    monkeypatch.setattr(config, "USE_VERTEX", False)
    monkeypatch.setattr(tokens, "_SECRET", b"web-intake-test-only")
    client = LocalClient(tmp_path / "web.json")
    monkeypatch.setattr(store, "_db", client)
    for email, role in [(REP, {"role": "club_rep", "club_id": "club-a"}),
                        (OTHER, {"role": "club_rep", "club_id": "club-b"}),
                        (SA, {"role": "student_affairs"}),
                        (UNASSIGNED, {"role": "club_rep"})]:
        client.collection("roles").document(email).set(role)
    monkeypatch.setattr(main, "publish", lambda *args: None)
    monkeypatch.setattr(main.mailer, "send", lambda *args, **kwargs: "test-message-no-delivery")
    monkeypatch.setattr(editor, "plan_issue", lambda items: (editor.fallback_plan(items), {"fallback": True}))
    return client


@pytest.fixture
def api(web_db):
    with TestClient(main.app) as client:
        yield client


def headers(email=REP):
    return {"X-Dev-User": email}


def image_bytes(fmt="PNG", color="blue", size=(20, 20)):
    output = io.BytesIO()
    image = Image.new("RGB", size, color)
    if fmt == "PNG":
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "untrusted-test-metadata")
        image.save(output, fmt, pnginfo=metadata)
    else:
        image.save(output, fmt)
    return output.getvalue()


def upload(api, *, email=REP, raw=None, filename="poster.png"):
    return api.post("/admin/api/uploads", headers=headers(email),
                    files={"file": (filename, raw if raw is not None else image_bytes(), "image/png")})


def submit_event(api, *, email=REP, data=None, **extra):
    return api.post("/admin/api/submissions", headers=headers(email),
                    json={"type": "event", "data": EVENT if data is None else data,
                          "confirmed": True, **extra})


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_image_upload_reencodes_content_and_uses_generated_path(api, web_db, fmt):
    response = upload(api, raw=image_bytes(fmt), filename="../../untrusted-name.png")
    assert response.status_code == 200
    upload_id = response.json()["upload_id"]
    saved = web_db.collection("poster_uploads").document(upload_id).get().to_dict()
    assert saved["uploaded_by"] == REP and saved["club_id"] == "club-a"
    assert saved["uri"] == f"local:posters/web-{upload_id}.jpg"
    content, mime = main._load_poster(saved["uri"])
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(content)) as im:
        assert im.format == "JPEG" and im.size == (20, 20)
    assert b"untrusted-test-metadata" not in content


@pytest.mark.parametrize("raw", [b"%PDF-1.7 fake document", b"not an image", b"", image_bytes("GIF")])
def test_upload_rejects_documents_and_invalid_images(api, web_db, raw):
    assert upload(api, raw=raw).status_code == 415
    assert not list(web_db.collection("poster_uploads").stream())


def test_upload_limits_bytes_before_image_decoding(api, web_db, monkeypatch):
    monkeypatch.setattr(submission, "MAX_UPLOAD_BYTES", 100)
    assert upload(api, raw=b"x" * 101).status_code == 413
    assert not list(web_db.collection("poster_uploads").stream())


def test_upload_limits_decoded_image_dimensions(api, web_db, monkeypatch):
    monkeypatch.setattr(submission, "MAX_IMAGE_PIXELS", 100)
    assert upload(api, raw=image_bytes(size=(11, 10))).status_code == 413
    assert not list(web_db.collection("poster_uploads").stream())


def test_upload_requires_authenticated_staff(api, web_db):
    files = {"file": ("poster.png", image_bytes(), "image/png")}
    assert api.post("/admin/api/uploads", files=files).status_code == 401
    assert upload(api, email="student@mail.apu.edu.my").status_code == 403
    assert not list(web_db.collection("poster_uploads").stream())


@pytest.mark.parametrize("path", ["/submit", "/admin", "/admin/club", "/subscribe", "/demo", "/demo/outbox"])
def test_student_staff_and_demo_pages_render(api, path):
    response = api.get(path)
    assert response.status_code == 200
    assert "<main" in response.text and "BoardCast" in response.text


@pytest.mark.parametrize("with_poster", [False, True])
def test_manual_submission_reaches_sa_queue_and_requires_sa_approval(api, with_poster):
    extra = {"upload_id": upload(api).json()["upload_id"]} if with_poster else {}
    response = submit_event(api, user_email=SA, club_id="spoofed-club", **extra)
    assert response.status_code == 200 and response.json()["status"] == "pending"
    item_id = response.json()["id"]
    item = store.get_item(item_id)
    assert item["submitted_by"] == REP and item["club_id"] == "club-a"
    queue = api.get("/admin/api/queue", headers=headers(SA)).json()["items"]
    assert [row["id"] for row in queue] == [item_id]
    self_approval = api.post(f"/admin/api/items/{item_id}/decision", headers=headers(), json={"decision": "approve"})
    assert self_approval.status_code == 403
    assert store.get_item(item_id)["status"] == "pending"
    approval = api.post(f"/admin/api/items/{item_id}/decision", headers=headers(SA), json={"decision": "approve"})
    assert approval.status_code == 200 and approval.json()["status"] == "approved"
    assert store.get_item(item_id)["approved_by"] == SA


def test_missing_gemini_key_keeps_upload_available_for_manual_submission(api, web_db):
    upload_id = upload(api).json()["upload_id"]
    response = api.post(f"/admin/api/uploads/{upload_id}/extract", headers=headers(), json={"type": "event"})
    assert response.status_code == 503 and "manually" in response.json()["detail"]
    assert web_db.collection("poster_uploads").document(upload_id).get().exists
    assert submit_event(api, upload_id=upload_id).json()["status"] == "pending"


def test_failed_extraction_returns_recoverable_error_without_losing_upload(api, web_db, monkeypatch):
    upload_id = upload(api).json()["upload_id"]
    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-key-never-sent")

    def fail(*args):
        raise RuntimeError("simulated model failure")

    monkeypatch.setattr(main, "extract_item", fail)
    response = api.post(f"/admin/api/uploads/{upload_id}/extract", headers=headers(), json={"type": "event"})
    assert response.status_code == 502
    assert "manually" in response.json()["detail"]
    assert web_db.collection("poster_uploads").document(upload_id).get().exists


def test_other_club_cannot_reuse_upload_or_edit_and_open_submission(api):
    upload_id = upload(api).json()["upload_id"]
    item_id = submit_event(api, upload_id=upload_id).json()["id"]
    original = store.get_item(item_id)
    assert submit_event(api, email=OTHER, upload_id=upload_id).status_code == 403
    assert api.post(f"/admin/api/uploads/{upload_id}/extract", headers=headers(OTHER), json={"type": "event"}).status_code == 403
    assert api.get(f"/admin/api/submissions/{item_id}", headers=headers(OTHER)).status_code == 403
    edit = api.patch(f"/admin/api/submissions/{item_id}", headers=headers(OTHER),
                     json={"type": "event", "data": {**EVENT, "title": "Changed"}, "confirmed": True})
    assert edit.status_code == 403
    assert store.get_item(item_id) == original


def test_unassigned_club_rep_cannot_access_sa_items_or_upload(api):
    item_id = submit_event(api, email=SA).json()["id"]
    assert store.get_item(item_id)["club_id"] is None
    assert api.get(f"/admin/api/submissions/{item_id}", headers=headers(UNASSIGNED)).status_code == 403
    assert submit_event(api, email=UNASSIGNED).status_code == 403
    assert upload(api, email=UNASSIGNED).status_code == 403


def test_correction_replaces_poster_and_requires_fresh_approval(api):
    first = upload(api).json()["upload_id"]
    item_id = submit_event(api, upload_id=first).json()["id"]
    assert api.post(f"/admin/api/items/{item_id}/decision", headers=headers(SA), json={"decision": "approve"}).status_code == 200
    old_uri = store.get_item(item_id)["poster_uri"]
    second = upload(api, raw=image_bytes(color="red")).json()["upload_id"]
    response = api.patch(f"/admin/api/submissions/{item_id}", headers=headers(),
                         json={"type": "event", "data": {**EVENT, "venue": "Updated Hall"},
                               "confirmed": True, "upload_id": second, "show_poster": True})
    assert response.status_code == 200 and response.json()["status"] == "pending"
    current = store.get_item(item_id)
    assert current["approved_by"] is None
    assert current["poster_uri"] != old_uri and second in current["poster_uri"]
    assert current["show_poster"] is True and current["data"]["venue"] == "Updated Hall"


def test_missing_required_field_stays_draft(api):
    response = submit_event(api, data={**EVENT, "fee": None})
    assert response.status_code == 200 and response.json()["status"] == "draft"
    assert "Missing: fee" in response.json()["issues"]
    item_id = response.json()["id"]
    assert api.post(f"/admin/api/items/{item_id}/decision", headers=headers(SA), json={"decision": "approve"}).status_code == 409


def test_submit_requires_explicit_confirmation(api, web_db):
    assert submit_event(api, confirmed=False).status_code == 422
    assert not list(web_db.collection("items").stream())


@pytest.mark.parametrize("upload_id", ["../other", "nested/document", "not-an-upload", "a" * 33])
def test_malformed_attachment_ids_are_rejected_before_storage(api, web_db, upload_id):
    assert submit_event(api, upload_id=upload_id).status_code == 422
    assert not list(web_db.collection("items").stream())


def test_invalid_corrected_date_returns_validation_error_without_mutation(api):
    item_id = submit_event(api).json()["id"]
    original = store.get_item(item_id)
    response = api.patch(f"/admin/api/submissions/{item_id}", headers=headers(),
                         json={"type": "event", "data": {"date": "not-a-date"}, "confirmed": True})
    assert response.status_code == 422
    assert store.get_item(item_id) == original


def test_publication_preview_escapes_club_text_and_has_student_actions(api):
    response = submit_event(api, data={**EVENT, "title": "<script>alert('x')</script>"},
                            description="<img src=x onerror=alert('x')> Join the club.")
    assert response.status_code == 200
    detail = api.get(f"/admin/api/submissions/{response.json()['id']}", headers=headers(SA))
    assert detail.status_code == 200
    html = detail.json()["publication_preview"]
    assert "<script>" not in html and "<img " not in html
    assert "&lt;script&gt;" in html and "&lt;img " in html
    assert "Register here" in html and "Add to calendar" in html


@pytest.mark.parametrize("link,allowed", [("javascript:alert(1)", False),
                                         ("https://user@instagram.com/p/example", False),
                                         ("https://instagram.com.evil.example/p/example", False),
                                         ("https://www.instagram.com/p/example/", True)])
def test_preview_filters_unsafe_legacy_instagram_sources(api, link, allowed):
    item_id = store.create_item({"type": "achievement", "status": "pending", "club_id": "club-a",
                                "submitted_by": REP, "issues": [],
                                "data": {"title": "Campus win", "team": "Our team", "competition": "Campus contest",
                                         "result": "Winner", "consent_confirmed": True, "instagram_url": link}})
    response = api.get(f"/admin/api/submissions/{item_id}", headers=headers(SA))
    assert response.status_code == 200
    html = response.json()["publication_preview"]
    assert ("View on Instagram" in html) is allowed
    if not allowed:
        assert link not in html


def test_stale_review_version_cannot_approve_changed_submission(api, monkeypatch):
    # Windows clocks can return the same value for two rapid writes. A revision must
    # still change, so approval never applies to content the reviewer did not see.
    frozen = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "now", lambda: frozen)
    item_id = submit_event(api).json()["id"]
    stale = api.get(f"/admin/api/submissions/{item_id}", headers=headers(SA)).json()["updated_at"]
    edit = api.patch(f"/admin/api/submissions/{item_id}", headers=headers(),
                     json={"type": "event", "data": {"venue": "New Hall"}, "confirmed": True})
    assert edit.status_code == 200
    response = api.post(f"/admin/api/items/{item_id}/decision", headers=headers(SA),
                        json={"decision": "approve", "expected_updated_at": stale})
    assert response.status_code == 409 and store.get_item(item_id)["status"] == "pending"
    current = api.get(f"/admin/api/submissions/{item_id}", headers=headers(SA)).json()["updated_at"]
    assert api.post(f"/admin/api/items/{item_id}/decision", headers=headers(SA),
                    json={"decision": "approve", "expected_updated_at": current}).status_code == 200


def test_approval_rechecks_legacy_data_even_if_saved_issues_are_empty(api):
    item_id = submit_event(api).json()["id"]
    store.update_item(item_id, data={**EVENT, "fee": None}, issues=[])
    response = api.post(f"/admin/api/items/{item_id}/decision", headers=headers(SA), json={"decision": "approve"})
    assert response.status_code == 409 and "Missing: fee" in response.json()["detail"]
    assert store.get_item(item_id)["status"] == "pending"


@pytest.mark.parametrize("method,path", [("get", "/demo"), ("get", "/demo/outbox"),
                                         ("get", "/demo/outbox/example.html"),
                                         ("post", "/admin/api/demo/preview"),
                                         ("post", "/admin/api/demo/publish")])
def test_demo_routes_are_unavailable_outside_local_mode(api, monkeypatch, method, path):
    monkeypatch.setattr(config, "LOCAL_MODE", False)
    assert getattr(api, method)(path, headers=headers(SA)).status_code == 404


@pytest.mark.parametrize("path", ["/admin/api/demo/preview", "/admin/api/demo/publish"])
def test_local_demo_publish_controls_require_student_affairs(api, path):
    assert api.post(path, headers=headers()).status_code == 403


def test_local_outbox_email_is_sandboxed_and_prevents_parent_path_access(api, tmp_path):
    folder = config.LOCAL_DATA_DIR / "outbox"
    folder.mkdir()
    (folder / "sample.html").write_text("<p>Offline test email</p>", encoding="utf-8")
    (config.LOCAL_DATA_DIR / "outside.html").write_text("PRIVATE TEST MARKER", encoding="utf-8")
    response = api.get("/demo/outbox/sample.html")
    assert response.status_code == 200 and "Offline test email" in response.text
    assert "sandbox" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    assert api.get("/demo/outbox/..%5Coutside.html").status_code == 404


def test_local_mailer_never_uses_gmail_even_when_credentials_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_MODE", True)
    monkeypatch.setattr(config, "LOCAL_DATA_DIR", tmp_path)
    monkeypatch.setenv("GMAIL_TOKEN_JSON", '{"access_token":"test-token-never-used"}')

    def unexpected_gmail():
        pytest.fail("Local mode attempted to contact Gmail")

    monkeypatch.setattr(mailer, "_service", unexpected_gmail)
    filename = mailer.send("student@mail.apu.edu.my", "Local-only email", "<p>Test message</p>", "")
    saved = tmp_path / "outbox" / filename
    assert saved.is_file() and "Test message" in saved.read_text(encoding="utf-8")


def test_email_calendar_link_is_signed_and_magazine_link_is_public(api):
    item_id = submit_event(api).json()["id"]
    assert api.post(f"/admin/api/items/{item_id}/decision", headers=headers(SA), json={"decision": "approve"}).status_code == 200
    item = store.get_item(item_id)
    issue = {"events": [item], "announcements": [], "achievements": [],
             "week_start": date(2099, 11, 9), "week_end": date(2099, 11, 15)}
    student = {"email": "calendar.student@mail.apu.edu.my", "active": True}
    store.upsert_subscriber(student)
    token = tokens.sign(student["email"], "save")
    plan = editor.fallback_plan([item])
    email = issue_builder.render_email(issue, plan, student)
    magazine = issue_builder.render_magazine(issue, plan, "test-issue")
    assert f'href="{config.PUBLIC_BASE_URL}/cal/{item_id}.ics?s={token}"' in email
    assert f'href="/cal/{item_id}.ics"' in magazine and token not in magazine
    response = api.get(f"/cal/{item_id}.ics", params={"s": token})
    assert response.status_code == 200 and "BEGIN:VCALENDAR" in response.text
    saved = store.unreminded_saves()
    assert len(saved) == 1 and saved[0]["email"] == student["email"]
