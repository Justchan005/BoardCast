"""Exercise the public hosted demo with in-process requests, never real AI or email."""
import asyncio

import httpx
import pytest
from starlette.responses import JSONResponse

from backend import hosted_demo as gate


ORIGIN = "https://boardcast.example"
AFFAIRS = "studentaffairs.demo@apu.edu.my"
CLUB = "clubrep.demo@mail.apu.edu.my"


@pytest.fixture(autouse=True)
def hosted_environment(monkeypatch):
    monkeypatch.setenv("HOSTED_DEMO", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", ORIGIN)
    monkeypatch.setenv("HOSTED_EMAIL_PREVIEW", "false")
    for name in ("DEMO_ACCESS_CODE", "DEMO_OWNER_CODE", "DEMO_SESSION_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(gate.config, "LOCAL_MODE", True)


@pytest.fixture
def app():
    async def upstream(scope, receive, send):
        await JSONResponse({"reached": scope["path"]}, headers={"cache-control": "public, max-age=3600"})(scope, receive, send)
    return gate.HostedDemoMiddleware(upstream, settings=gate._Settings.from_environment())


def request(app, method="GET", path="/demo", *, origin=None, headers=None, data=None, base=ORIGIN, peer="127.0.0.1"):
    pairs = list(headers or [])
    if origin is not None:
        pairs.append(("Origin", origin))

    async def perform():
        transport = httpx.ASGITransport(app=app, client=(peer, 123))
        async with httpx.AsyncClient(transport=transport, base_url=base, follow_redirects=False) as client:
            return await client.request(method, path, headers=pairs, data=data)
    return asyncio.run(perform())


class RecordingApp:
    def __init__(self):
        self.middleware = []

    def add_middleware(self, *args, **kwargs):
        self.middleware.append((args, kwargs))


@pytest.mark.parametrize("value", ["", "false", "FALSE", "0"])
def test_normal_local_mode_is_untouched(monkeypatch, value):
    monkeypatch.setenv("HOSTED_DEMO", value)
    monkeypatch.setattr(gate.config, "LOCAL_MODE", False)
    app = RecordingApp()
    assert gate.install(app) is False
    assert app.middleware == []


def test_install_registers_public_middleware_without_access_secrets(monkeypatch):
    app = RecordingApp()
    assert gate.install(app) is True
    assert app.middleware[0][0] == (gate.HostedDemoMiddleware,)
    monkeypatch.setenv("HOSTED_DEMO", "treu")
    with pytest.raises(RuntimeError, match="HOSTED_DEMO"):
        gate.install(RecordingApp())


@pytest.mark.parametrize("value", [
    "http://boardcast.example", "https://boardcast.example/path",
    "https://user:password@boardcast.example", "https://boardcast.example?redirect=example.com",
    "https://boardcast.example:badport",
])
def test_invalid_hosted_origin_fails_before_serving(monkeypatch, value):
    monkeypatch.setenv("PUBLIC_BASE_URL", value)
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        gate.install(RecordingApp())


def test_hosted_requires_local_storage(monkeypatch):
    monkeypatch.setattr(gate.config, "LOCAL_MODE", False)
    with pytest.raises(RuntimeError, match="LOCAL_MODE"):
        gate.install(RecordingApp())


@pytest.mark.parametrize("path", ["/", "/demo", "/admin", "/submit", "/docs", "/redoc", "/static/app.css", "/p/poster.jpg", "/m/latest", "/r/link", "/cal/event.ics", "/openapi.json"])
def test_pages_and_assets_are_open_without_cookies_or_codes(app, path):
    response = request(app, path=path)
    assert response.status_code == 200
    assert response.json()["reached"] == path
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("path,destination", [
    ("/judge/login", "/demo"), ("/judge/owner", "/admin?demo=student-affairs"), ("/judge/logout", "/demo"),
])
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST"])
def test_old_access_urls_redirect_without_a_form_or_code(app, path, destination, method):
    response = request(app, method, path + "?next=https://evil.example", origin=ORIGIN)
    assert response.status_code == 303
    assert response.headers["location"] == destination
    assert "set-cookie" not in response.headers
    assert "password" not in response.text


@pytest.mark.parametrize("name", ["DEMO_ACCESS_CODE", "DEMO_OWNER_CODE", "DEMO_SESSION_SECRET"])
def test_leftover_access_settings_cannot_reenable_a_gate(monkeypatch, name):
    monkeypatch.setenv(name, "old")
    assert gate.install(RecordingApp()) is True


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_health_check_is_public_even_for_proxy_host(app, method):
    response = request(app, method, "/healthz", headers=[("Host", "internal-render-health")])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"


@pytest.mark.parametrize("base", ["http://127.0.0.1:8080", "http://localhost:8080", "http://[::1]:8080"])
def test_explicit_loopback_qa_origin_is_supported(monkeypatch, base):
    monkeypatch.setenv("PUBLIC_BASE_URL", base)
    assert gate._Settings.from_environment().origin == gate._origin(base)


@pytest.mark.parametrize("headers", [
    [], [("Origin", "https://evil.example")], [("Origin", "null")],
    [("Origin", ORIGIN), ("Sec-Fetch-Site", "cross-site")],
    [("Origin", ORIGIN), ("Sec-Fetch-Site", "same-site")],
    [("Origin", ORIGIN + "/not-an-origin")],
    [("Origin", ORIGIN), ("Origin", ORIGIN)],
    [("Origin", "https://evil.example"), ("Referer", ORIGIN + "/admin")],
    [("Referer", "https://evil.example/admin")],
])
def test_public_mutations_still_deny_missing_or_cross_site_origin(app, headers):
    response = request(app, "POST", "/admin/api/demo/publish", headers=headers + [("X-Dev-User", AFFAIRS)])
    assert response.status_code == 403


@pytest.mark.parametrize("headers", [[("Origin", ORIGIN)], [("Referer", ORIGIN + "/admin")], [("Origin", ORIGIN), ("Sec-Fetch-Site", "same-origin")]])
def test_same_origin_mutations_reach_application_without_cookies(app, headers):
    response = request(app, "POST", "/admin/api/demo/publish", headers=headers + [("X-Dev-User", AFFAIRS)])
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/judge/login", "/judge/owner", "/judge/logout", "/admin/api/issues/current/email-preview"])
def test_legacy_forms_and_email_reject_cross_site_post(app, path):
    assert request(app, "POST", path, origin="https://evil.example").status_code == 403


@pytest.mark.parametrize("host", ["localhost", "evil.example", "boardcast.example/evil", "boardcast.example?x=1", "boardcast.example:444", "user@boardcast.example"])
def test_host_cannot_be_overridden_by_proxy_headers(app, host):
    response = request(app, headers=[("Host", host), ("X-Forwarded-Host", "boardcast.example"), ("Forwarded", "host=boardcast.example;proto=https")])
    assert response.status_code == 400


@pytest.mark.parametrize("path", ["/items", "/items/123/decision", "/items/extract", "/issues/send", "/reminders/run", "/extract", "/admin/api/real-email/status", "/admin/email-setup", "/admin/api/issues/current/email-preview"])
def test_service_endpoints_and_disabled_email_remain_unavailable(app, path):
    response = request(app, "POST", path, origin=ORIGIN, headers=[("X-Dev-User", AFFAIRS), ("Authorization", "Bearer dev")])
    assert response.status_code == 404


def test_demo_apis_require_only_seeded_role_selection(app):
    assert request(app, path="/admin/api/me").status_code == 401
    for identity in gate.DEMO_IDENTITIES:
        assert request(app, path="/admin/api/me", headers=[("X-Dev-User", identity)]).status_code == 200
    for headers in [[("X-Dev-User", "anyone@apu.edu.my")], [("X-Dev-User", AFFAIRS), ("X-Dev-User", AFFAIRS)]]:
        assert request(app, path="/admin/api/me", headers=headers).status_code == 403


def test_expensive_actions_share_budget_but_reads_remain_available(app):
    paths = ["/admin/api/demo/preview", "/admin/api/uploads/123/extract", "/admin/api/items/123/prereview", "/admin/api/submissions"]
    for index in range(12):
        response = request(app, "POST", paths[index % len(paths)], origin=ORIGIN,
                           headers=[("X-Dev-User", AFFAIRS), ("X-Forwarded-For", f"192.0.2.{index}"), ("Cookie", f"old={index}")])
        assert response.status_code == 200
    response = request(app, "POST", paths[0], origin=ORIGIN, headers=[("X-Dev-User", AFFAIRS)])
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert request(app, path="/admin/api/queue", headers=[("X-Dev-User", AFFAIRS)]).status_code == 200


def test_limiter_is_bounded_and_releases_expired_budget(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(gate.time, "monotonic", lambda: clock[0])
    limiter = gate._Limiter()
    assert limiter.take([(("sample",), 1, 60)]) == 0
    assert limiter.take([(("sample",), 1, 60)]) == 60
    clock[0] += 61
    assert limiter.take([(("sample",), 1, 60)]) == 0
    for index in range(2200):
        limiter.take([(("unique", index), 1, 60)])
    assert len(limiter.entries) <= 2048


@pytest.mark.parametrize("path", ["/judge/login", "/healthz", "/admin/api/queue", "/docs"])
def test_all_responses_have_non_indexable_headers(app, path):
    response = request(app, path=path)
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.fixture
def email_app(monkeypatch, app):
    monkeypatch.setenv("HOSTED_EMAIL_PREVIEW", "true")
    monkeypatch.setenv("HOSTED_EMAIL_RECIPIENT", "owner-test@example.com")
    app.settings = gate._Settings.from_environment()
    return app


@pytest.mark.parametrize("key,value", [
    ("HOSTED_EMAIL_PREVIEW", "maybe"), ("HOSTED_EMAIL_RECIPIENT", ""),
    ("HOSTED_EMAIL_RECIPIENT", "one@example.com,two@example.com"),
])
def test_invalid_email_configuration_stops_startup(monkeypatch, key, value):
    monkeypatch.setenv("HOSTED_EMAIL_PREVIEW", "true")
    monkeypatch.setenv("HOSTED_EMAIL_RECIPIENT", "owner-test@example.com")
    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError):
        gate._Settings.from_environment()


def test_email_status_and_send_are_available_without_owner_session(email_app):
    headers = [("X-Dev-User", AFFAIRS)]
    assert request(email_app, path="/admin/api/real-email/status", headers=headers).status_code == 200
    result = request(email_app, "POST", "/admin/api/issues/sample/email-preview", origin=ORIGIN, headers=headers)
    assert result.status_code == 200
    assert "set-cookie" not in result.headers


@pytest.mark.parametrize("method,path", [("GET", "/admin/api/real-email/status"), ("POST", "/admin/api/issues/sample/email-preview")])
def test_email_is_only_in_student_affairs_role(email_app, method, path):
    assert request(email_app, method, path, origin=ORIGIN, headers=[("X-Dev-User", CLUB)]).status_code == 403


def test_email_limit_is_shared_and_cannot_be_reset_by_cookies_or_peer_changes(email_app):
    path = "/admin/api/issues/sample/email-preview"
    for index in range(3):
        result = request(email_app, "POST", path, origin=ORIGIN, peer=f"192.0.2.{index}",
                         headers=[("X-Dev-User", AFFAIRS), ("Cookie", f"old={index}")])
        assert result.status_code == 200
    result = request(email_app, "POST", path, origin=ORIGIN, peer="198.51.100.5", headers=[("X-Dev-User", AFFAIRS)])
    assert result.status_code == 429
    assert int(result.headers["retry-after"]) > 0
    assert request(email_app, path="/admin/api/real-email/status", headers=[("X-Dev-User", AFFAIRS)]).status_code == 200
