"""Exercise the hosted gate with in-process ASGI requests, never network or Gemini."""
import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest
from starlette.responses import JSONResponse

from backend import hosted_demo as gate


ORIGIN = "https://boardcast.example"
CODE = "judge-access-code-for-testing"
SECRET = "test-only-session-secret-32-characters-minimum"
AFFAIRS = "studentaffairs.demo@apu.edu.my"


@pytest.fixture(autouse=True)
def hosted_environment(monkeypatch):
    monkeypatch.setenv("HOSTED_DEMO", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", ORIGIN)
    monkeypatch.setenv("DEMO_ACCESS_CODE", CODE)
    monkeypatch.setenv("DEMO_SESSION_SECRET", SECRET)
    monkeypatch.setattr(gate.config, "LOCAL_MODE", True)


@pytest.fixture
def app():
    async def upstream(scope, receive, send):
        await JSONResponse({"reached": scope["path"]}, headers={"cache-control": "public, max-age=3600"})(scope, receive, send)
    return gate.HostedDemoMiddleware(upstream, settings=gate._Settings.from_environment())


def request(app, method="GET", path="/demo", *, token=None, origin=None, headers=None, content=None, data=None, base=ORIGIN):
    pairs = list(headers or [])
    if token is not None:
        pairs.append(("Cookie", f"{app.settings.cookie_name}={token}"))
    if origin is not None:
        pairs.append(("Origin", origin))

    async def perform():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base, follow_redirects=False) as client:
            return await client.request(method, path, headers=pairs, content=content, data=data)
    return asyncio.run(perform())


def login(app, code=CODE, **kwargs):
    return request(app, "POST", "/judge/login", origin=ORIGIN, data={"access_code": code}, **kwargs)


class RecordingApp:
    def __init__(self):
        self.middleware = []

    def add_middleware(self, *args, **kwargs):
        self.middleware.append((args, kwargs))


@pytest.mark.parametrize("value", ["", "false", "FALSE", "0"])
def test_normal_local_mode_is_untouched(monkeypatch, value):
    monkeypatch.setenv("HOSTED_DEMO", value)
    monkeypatch.delenv("DEMO_ACCESS_CODE")
    monkeypatch.setattr(gate.config, "LOCAL_MODE", False)
    app = RecordingApp()
    assert gate.install(app) is False
    assert app.middleware == []


def test_install_registers_middleware_and_rejects_typo(monkeypatch):
    app = RecordingApp()
    assert gate.install(app) is True
    assert app.middleware[0][0] == (gate.HostedDemoMiddleware,)
    monkeypatch.setenv("HOSTED_DEMO", "treu")
    with pytest.raises(RuntimeError, match="HOSTED_DEMO"):
        gate.install(RecordingApp())


@pytest.mark.parametrize("setting,value", [
    ("DEMO_ACCESS_CODE", "short"), ("DEMO_ACCESS_CODE", "x" * 257),
    ("DEMO_SESSION_SECRET", "short"), ("PUBLIC_BASE_URL", "http://boardcast.example"),
    ("PUBLIC_BASE_URL", "https://boardcast.example/path"),
    ("PUBLIC_BASE_URL", "https://user:password@boardcast.example"),
    ("PUBLIC_BASE_URL", "https://boardcast.example?redirect=example.com"),
    ("PUBLIC_BASE_URL", "https://boardcast.example:badport"),
])
def test_invalid_hosted_configuration_fails_before_serving(monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    with pytest.raises(RuntimeError):
        gate.install(RecordingApp())


def test_hosted_requires_local_storage(monkeypatch):
    monkeypatch.setattr(gate.config, "LOCAL_MODE", False)
    with pytest.raises(RuntimeError, match="LOCAL_MODE"):
        gate.install(RecordingApp())


@pytest.mark.parametrize("path", ["/demo", "/admin", "/submit", "/docs", "/redoc", "/static/app.css", "/p/secret.jpg", "/m/latest", "/r/link", "/cal/event.ics", "/healthz/"])
def test_pages_and_assets_require_signed_session(app, path):
    response = request(app, path=path, headers=[("X-Dev-User", AFFAIRS), ("Authorization", "Bearer dev")])
    assert response.status_code == 303
    assert response.headers["location"] == "/judge/login"
    assert "reached" not in response.text


@pytest.mark.parametrize("method,path", [("GET", "/openapi.json"), ("GET", "/admin/api/queue"), ("POST", "/admin/api/demo/publish"), ("POST", "/healthz"), ("POST", "/items")])
def test_unauthenticated_apis_and_mutations_return_401(app, method, path):
    response = request(app, method, path)
    assert response.status_code == 401
    assert response.json()["detail"]


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_only_exact_health_read_is_public_even_for_proxy_host(app, method):
    response = request(app, method, "/healthz", headers=[("Host", "internal-render-health")])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


def test_login_issues_protected_cookie_with_fixed_destination(app):
    response = request(app, "POST", "/judge/login?next=https://evil.example", origin=ORIGIN, data={"access_code": CODE})
    assert response.status_code == 303
    assert response.headers["location"] == "/demo"
    cookie = response.headers["set-cookie"]
    assert "__Host-boardcast_judge=" in cookie
    for attribute in ["HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=14400"]:
        assert attribute in cookie
    assert "Domain=" not in cookie
    assert CODE not in cookie and SECRET not in cookie
    token = response.cookies[app.settings.cookie_name]
    assert request(app, token=token).status_code == 200
    assert request(app, path="/judge/login", token=token).headers["location"] == "/demo"


def test_login_error_does_not_reflect_input_or_reveal_secret(app):
    attacker_text = "<script>alert('bad')</script>"
    response = login(app, code=attacker_text)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert attacker_text not in response.text and CODE not in response.text and SECRET not in response.text
    page = request(app, path="/judge/login")
    assert '<label for="access-code">' in page.text
    assert 'name="viewport"' in page.text
    assert "form-action 'self'" in page.headers["content-security-policy"]


@pytest.mark.parametrize("bad_token", ["unsigned", "a.b.c", "x" * 600, "a." + "0" * 64, ""])
def test_malformed_and_forged_sessions_do_not_authenticate(app, bad_token):
    assert request(app, token=bad_token).status_code == 303


def test_tampered_expired_and_rotated_sessions_rejected(app, monkeypatch):
    now = 1800000000
    monkeypatch.setattr(gate.time, "time", lambda: now)
    token = app.settings.session()
    assert app.settings.valid_session(token)
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert not app.settings.valid_session(tampered)
    monkeypatch.setattr(gate.time, "time", lambda: now + gate.SESSION_SECONDS)
    assert not app.settings.valid_session(token)
    monkeypatch.setattr(gate.time, "time", lambda: now)
    monkeypatch.setenv("DEMO_ACCESS_CODE", CODE + "-rotated")
    assert not gate._Settings.from_environment().valid_session(token)
    monkeypatch.setenv("DEMO_ACCESS_CODE", CODE)
    monkeypatch.setenv("DEMO_SESSION_SECRET", SECRET + "-rotated")
    assert not gate._Settings.from_environment().valid_session(token)


def test_even_signed_invalid_session_payloads_fail_closed(app):
    for payload in [[], None, {"iat": True, "exp": 99999999999}, {"iat": "now"}, {"iat": 0, "exp": 1, "nonce": "a" * 32}]:
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        signed = encoded + "." + hmac.new(app.settings.signing_key, encoded.encode(), hashlib.sha256).hexdigest()
        assert not app.settings.valid_session(signed)


@pytest.mark.parametrize("base", ["http://127.0.0.1:8080", "http://localhost:8080", "http://[::1]:8080"])
def test_loopback_qa_can_use_http_without_weakening_public_cookie(monkeypatch, base):
    monkeypatch.setenv("PUBLIC_BASE_URL", base)
    settings = gate._Settings.from_environment()
    assert not settings.secure_cookie
    assert settings.cookie_name == "boardcast_judge_loopback"


@pytest.mark.parametrize("headers", [
    [], [("Origin", "https://evil.example")], [("Origin", "null")],
    [("Origin", ORIGIN), ("Sec-Fetch-Site", "cross-site")],
    [("Origin", ORIGIN), ("Sec-Fetch-Site", "same-site")],
    [("Origin", ORIGIN + "/not-an-origin")],
    [("Origin", ORIGIN), ("Origin", ORIGIN)],
    [("Origin", "https://evil.example"), ("Referer", ORIGIN + "/admin")],
    [("Referer", "https://evil.example/admin")],
])
def test_authenticated_mutations_deny_missing_or_cross_site_origin(app, headers):
    response = request(app, "POST", "/admin/api/demo/publish", token=app.settings.session(), headers=headers + [("X-Dev-User", AFFAIRS)])
    assert response.status_code == 403


@pytest.mark.parametrize("headers", [[("Origin", ORIGIN)], [("Referer", ORIGIN + "/admin")], [("Origin", ORIGIN), ("Sec-Fetch-Site", "same-origin")]])
def test_same_origin_mutations_reach_application(app, headers):
    response = request(app, "POST", "/admin/api/demo/publish", token=app.settings.session(), headers=headers + [("X-Dev-User", AFFAIRS)])
    assert response.status_code == 200


def test_login_is_also_protected_from_cross_site_submission(app):
    response = request(app, "POST", "/judge/login", origin="https://evil.example", data={"access_code": CODE})
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("host", ["localhost", "evil.example", "boardcast.example/evil", "boardcast.example?x=1", "boardcast.example:444", "user@boardcast.example"])
def test_host_cannot_be_overridden_by_proxy_headers(app, host):
    response = request(app, token=app.settings.session(), headers=[("Host", host), ("X-Forwarded-Host", "boardcast.example"), ("Forwarded", "host=boardcast.example;proto=https")])
    assert response.status_code == 400


@pytest.mark.parametrize("path", ["/items", "/items/123/decision", "/items/extract", "/issues/send", "/reminders/run", "/extract", "/admin/api/real-email/status", "/admin/email-setup", "/admin/api/issues/current/email-preview"])
def test_service_credentials_and_real_email_are_unavailable_even_after_entry(app, path):
    response = request(app, "POST", path, token=app.settings.session(), origin=ORIGIN, headers=[("X-Dev-User", AFFAIRS), ("Authorization", "Bearer dev")])
    assert response.status_code == 404


def test_demo_apis_require_only_seeded_role_identities(app):
    token = app.settings.session()
    assert request(app, path="/admin/api/me", token=token).status_code == 401
    for identity in gate.DEMO_IDENTITIES:
        assert request(app, path="/admin/api/me", token=token, headers=[("X-Dev-User", identity)]).status_code == 200
    for headers in [[("X-Dev-User", "anyone@apu.edu.my")], [("X-Dev-User", AFFAIRS), ("X-Dev-User", AFFAIRS)]]:
        assert request(app, path="/admin/api/me", token=token, headers=headers).status_code == 403


def test_logout_requires_same_origin_post_and_clears_cookie(app):
    token = app.settings.session()
    assert request(app, path="/judge/logout", token=token).status_code == 405
    assert request(app, "POST", "/judge/logout", token=token, origin="https://evil.example").status_code == 403
    response = request(app, "POST", "/judge/logout", token=token, origin=ORIGIN)
    assert response.status_code == 303
    assert response.headers["location"] == "/judge/login"
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


@pytest.mark.parametrize("content,headers,status", [
    ("{}", [("Content-Type", "application/json")], 415),
    ("access_code=" + "x" * 4097, [("Content-Type", "application/x-www-form-urlencoded")], 400),
    ("access_code=" + CODE, [("Content-Type", "application/x-www-form-urlencoded"), ("Content-Length", "-1")], 400),
    ("access_code=" + CODE + "&access_code=" + CODE, [("Content-Type", "application/x-www-form-urlencoded")], 401),
    ("bad-format", [("Content-Type", "application/x-www-form-urlencoded")], 400),
])
def test_login_body_is_bounded_and_unambiguous(app, content, headers, status):
    response = request(app, "POST", "/judge/login", content=content, headers=headers, origin=ORIGIN)
    assert response.status_code == status
    assert "set-cookie" not in response.headers


def test_bad_logins_are_throttled_and_forged_forwarding_does_not_reset_limit(app):
    for index in range(10):
        assert login(app, "wrong", headers=[("X-Forwarded-For", f"192.0.2.{index}")]).status_code == 401
    response = login(app)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert "set-cookie" not in response.headers


def test_expensive_actions_share_budget_but_reads_remain_available(app):
    token = app.settings.session()
    headers = [("X-Dev-User", AFFAIRS)]
    paths = ["/admin/api/demo/preview", "/admin/api/uploads/123/extract", "/admin/api/items/123/prereview", "/admin/api/submissions"]
    for index in range(12):
        response = request(app, "POST", paths[index % len(paths)], token=token, origin=ORIGIN, headers=headers)
        assert response.status_code == 200
    response = request(app, "POST", paths[0], token=token, origin=ORIGIN, headers=headers)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert request(app, path="/admin/api/queue", token=token, headers=headers).status_code == 200


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
def test_all_responses_have_private_non_indexable_headers(app, path):
    response = request(app, path=path)
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["x-content-type-options"] == "nosniff"
