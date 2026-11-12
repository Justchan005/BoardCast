"""Access gate for a temporary, shared judge demo, never production identity.

Opt-in only. The hosted demo uses disposable local records and the two seeded demo
roles. A shared access code grants access to that complete demonstration. It does
not represent independent Club or Student Affairs accounts.
"""
from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config

SESSION_SECONDS = 4 * 60 * 60
MAX_LOGIN_BYTES = 4096
DEMO_IDENTITIES = frozenset({"clubrep.demo@mail.apu.edu.my", "studentaffairs.demo@apu.edu.my"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SERVICE_PREFIXES = ("/items", "/issues", "/reminders", "/extract")
SECURITY_HEADERS = {
    b"cache-control": b"no-store, max-age=0",
    b"pragma": b"no-cache",
    b"x-robots-tag": b"noindex, nofollow, noarchive",
    b"x-frame-options": b"SAMEORIGIN",
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"same-origin",
}


def _origin(value: str) -> tuple[str, str, int]:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Invalid origin")
    return parts.scheme.lower(), parts.hostname.lower(), parts.port or (443 if parts.scheme == "https" else 80)


@dataclass(frozen=True)
class _Settings:
    origin: tuple[str, str, int]
    access_code: str
    signing_key: bytes
    secure_cookie: bool
    cookie_name: str

    @classmethod
    def from_environment(cls):
        if not config.LOCAL_MODE:
            raise RuntimeError("HOSTED_DEMO requires LOCAL_MODE=true and disposable demo data.")
        code = os.environ.get("DEMO_ACCESS_CODE", "").strip()
        secret = os.environ.get("DEMO_SESSION_SECRET", "").strip()
        if not 16 <= len(code) <= 256:
            raise RuntimeError("DEMO_ACCESS_CODE must contain between 16 and 256 characters.")
        if len(secret) < 32:
            raise RuntimeError("DEMO_SESSION_SECRET must contain at least 32 characters.")
        base = os.environ.get("PUBLIC_BASE_URL", config.PUBLIC_BASE_URL).strip()
        try:
            parts = urlsplit(base)
            origin = _origin(base)
            if parts.path not in {"", "/"} or parts.query or parts.fragment:
                raise ValueError("Use an origin without a path")
            secure = origin[0] == "https"
            if not secure and origin[1] not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("HTTPS is required for a public demo")
        except ValueError:
            raise RuntimeError("PUBLIC_BASE_URL must be the demo's HTTPS origin. HTTP is allowed only for loopback QA.") from None
        # Changing either setting invalidates every previously issued session.
        key = hmac.new(secret.encode(), b"boardcast-judge-session\0" + code.encode(), hashlib.sha256).digest()
        return cls(origin, code, key, secure,
                   "__Host-boardcast_judge" if secure else "boardcast_judge_loopback")

    def session(self) -> str:
        now = int(time.time())
        data = json.dumps({"iat": now, "exp": now + SESSION_SECONDS, "nonce": secrets.token_hex(16)},
                          separators=(",", ":")).encode()
        payload = base64.urlsafe_b64encode(data).decode().rstrip("=")
        signature = hmac.new(self.signing_key, payload.encode(), hashlib.sha256).hexdigest()
        return payload + "." + signature

    def valid_session(self, value: str) -> bool:
        if not value or len(value) > 512:
            return False
        try:
            payload, signature = value.split(".")
            expected = hmac.new(self.signing_key, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return False
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            now = int(time.time())
            return (type(data.get("iat")) is int and type(data.get("exp")) is int
                    and now - SESSION_SECONDS <= data["iat"] <= now + 30
                    and now < data["exp"] <= data["iat"] + SESSION_SECONDS
                    and isinstance(data.get("nonce"), str) and len(data["nonce"]) == 32)
        except (ValueError, TypeError, AttributeError, UnicodeError):
            return False


class _Limiter:
    """Bounded process-local limits, backed by a global ceiling for each activity."""
    def __init__(self):
        self.entries: OrderedDict[tuple, deque] = OrderedDict()
        self.lock = threading.Lock()

    def take(self, limits: list[tuple[tuple, int, int]]) -> int:
        now = time.monotonic()
        with self.lock:
            rows = []
            retry = 0
            for key, count, window in limits:
                times = self.entries.setdefault(key, deque())
                self.entries.move_to_end(key)
                while times and times[0] <= now - window:
                    times.popleft()
                if len(times) >= count:
                    retry = max(retry, math.ceil(times[0] + window - now))
                rows.append(times)
            if not retry:
                for times in rows:
                    times.append(now)
            while len(self.entries) > 2048:
                self.entries.popitem(last=False)
            return max(retry, 0)


def _login_page(error: str = "") -> str:
    # Errors are fixed strings supplied by this module. Never reflect request data.
    message = f'<p class="error" role="alert">{error}</p>' if error else ""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Enter the BoardCast judge demo</title>
<style>*{box-sizing:border-box}html{color-scheme:light}body{margin:0;min-height:100vh;background:#102b4b;color:#172b43;font:17px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;display:grid;place-items:center;padding:24px}main{width:100%;max-width:530px;background:#fff;padding:clamp(26px,6vw,48px);border-radius:16px;box-shadow:0 18px 50px #06182955}h1{font-size:clamp(28px,5vw,38px);line-height:1.15;margin:0 0 20px;letter-spacing:-.025em;text-wrap:balance}p{margin:0 0 24px;color:#40566e}label{display:block;font-weight:650;margin-bottom:8px}input,button{width:100%;min-height:52px;border-radius:8px;font:inherit}input{border:1px solid #7890aa;padding:11px 14px;color:#172b43;background:#fff}button{margin-top:18px;border:1px solid #154c8d;background:#154c8d;color:#fff;font-weight:650;cursor:pointer}button:hover{background:#103e74}input:focus-visible,button:focus-visible{outline:3px solid #397bc8;outline-offset:4px}.note{font-size:14px;margin:24px 0 0}.error{color:#9b242c;background:#fff2f2;padding:12px 14px;border-radius:6px}::selection{background:#c9ddf7;color:#102b4b}</style></head>
<body><main><h1>Try the BoardCast judge demo</h1><p>Follow a club submission through review and into Campus Weekly. Enter the access code shared by the team to begin.</p>""" + message + """
<form action="/judge/login" method="post"><label for="access-code">Demo access code</label>
<input id="access-code" name="access_code" type="password" autocomplete="current-password" minlength="16" maxlength="256" required autofocus>
<button type="submit">Open BoardCast</button></form><p class="note">This is a shared demonstration with sample accounts. Use test content. Data can reset when the service restarts; email stays in the demo outbox.</p></main></body></html>"""


def _is_api(path: str) -> bool:
    return path.startswith("/admin/api/") or path.startswith(SERVICE_PREFIXES) or path == "/openapi.json"


def _blocked(path: str) -> bool:
    return (path.startswith(SERVICE_PREFIXES)
            or path.startswith("/admin/api/real-email") or path.startswith("/admin/email-setup")
            or (path.startswith("/admin/api/issues/") and "/email-preview" in path))


def _expensive(method: str, path: str) -> bool:
    if method not in {"POST", "PATCH"}:
        return False
    return (path == "/admin/api/demo/preview"
            or path == "/admin/api/submissions" or path.startswith("/admin/api/submissions/")
            or (path.startswith("/admin/api/uploads/") and path.rstrip("/").endswith("/extract"))
            or (path.startswith("/admin/api/items/") and path.rstrip("/").endswith("/prereview")))


class HostedDemoMiddleware:
    def __init__(self, app, settings: _Settings):
        self.app = app
        self.settings = settings
        self.limiter = _Limiter()

    def _correct_host(self, headers: Headers) -> bool:
        values = headers.getlist("host")
        if len(values) != 1 or any(char in values[0] for char in "/?#\\ \t\r\n"):
            return False
        try:
            return _origin(self.settings.origin[0] + "://" + values[0]) == self.settings.origin
        except ValueError:
            return False

    def _same_origin(self, headers: Headers) -> bool:
        if headers.get("sec-fetch-site", "").lower() not in {"", "none", "same-origin"}:
            return False
        origins = headers.getlist("origin")
        referers = headers.getlist("referer")
        if len(origins) > 1 or len(referers) > 1:
            return False
        origin = origins[0] if origins else (referers[0] if referers else "")
        try:
            if origins:
                parts = urlsplit(origin)
                if parts.path or parts.query or parts.fragment:
                    return False
            return _origin(origin) == self.settings.origin
        except ValueError:
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.app(scope, receive, send)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() not in SECURITY_HEADERS]
                message = {**message, "headers": headers + list(SECURITY_HEADERS.items())}
            await send(message)

        async def respond(response):
            await response(scope, receive, secure_send)

        path, method = scope.get("path", "/"), scope["method"].upper()
        if path == "/healthz" and method in {"GET", "HEAD"}:
            return await self.app(scope, receive, secure_send)
        request = Request(scope, receive=receive)
        headers = request.headers
        if not self._correct_host(headers):
            return await respond(JSONResponse({"detail": "Open the configured BoardCast demo address."}, status_code=400))
        session = request.cookies.get(self.settings.cookie_name, "")
        authenticated = self.settings.valid_session(session)
        if (authenticated or path == "/judge/login") and method not in SAFE_METHODS and not self._same_origin(headers):
            return await respond(JSONResponse({"detail": "Submit this action from the BoardCast demo page."}, status_code=403))

        if path == "/judge/login":
            if method == "GET":
                response = RedirectResponse("/demo", status_code=303) if authenticated else HTMLResponse(_login_page())
                response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'self'"
                return await respond(response)
            if method != "POST":
                return await respond(JSONResponse({"detail": "Method not allowed"}, status_code=405, headers={"Allow": "GET, POST"}))
            peer = scope.get("client") or ("unknown", 0)
            retry = self.limiter.take([(("login", peer[0]), 10, 600), (("login-global",), 200, 600)])
            if retry:
                return await respond(HTMLResponse(_login_page("Too many sign-in attempts. Wait a few minutes, then try again."),
                                                  status_code=429, headers={"Retry-After": str(retry)}))
            if headers.get("content-type", "").split(";", 1)[0].lower() != "application/x-www-form-urlencoded":
                return await respond(JSONResponse({"detail": "Use the access-code form."}, status_code=415))
            try:
                declared = headers.get("content-length")
                if declared is not None and not 0 <= int(declared) <= MAX_LOGIN_BYTES:
                    raise ValueError("size")
                async def read_body():
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > MAX_LOGIN_BYTES:
                            raise ValueError("size")
                    return bytes(body)
                raw = await asyncio.wait_for(read_body(), timeout=5)
                fields = parse_qs(raw.decode("utf-8"), max_num_fields=8, strict_parsing=True)
                codes = fields.get("access_code", [])
                code = codes[0].strip() if len(codes) == 1 else ""
            except (ValueError, UnicodeError, ClientDisconnect):
                return await respond(JSONResponse({"detail": "The access-code form is invalid or too large."}, status_code=400))
            except asyncio.TimeoutError:
                return await respond(JSONResponse({"detail": "The sign-in request timed out. Try again."}, status_code=408))
            if not hmac.compare_digest(code.encode(), self.settings.access_code.encode()):
                return await respond(HTMLResponse(_login_page("The access code was not recognised. Check the code shared by the team."), status_code=401))
            response = RedirectResponse("/demo", status_code=303)
            response.set_cookie(self.settings.cookie_name, self.settings.session(), max_age=SESSION_SECONDS,
                                secure=self.settings.secure_cookie, httponly=True, samesite="strict", path="/")
            return await respond(response)

        if not authenticated:
            if method in {"GET", "HEAD"} and not _is_api(path):
                return await respond(RedirectResponse("/judge/login", status_code=303))
            return await respond(JSONResponse({"detail": "Enter the judge demo access code first."}, status_code=401))
        if path == "/judge/logout":
            if method != "POST":
                return await respond(JSONResponse({"detail": "Use the Leave demo button."}, status_code=405, headers={"Allow": "POST"}))
            response = RedirectResponse("/judge/login", status_code=303)
            response.delete_cookie(self.settings.cookie_name, path="/", secure=self.settings.secure_cookie,
                                   httponly=True, samesite="strict")
            return await respond(response)
        if _blocked(path):
            return await respond(JSONResponse({"detail": "This action is unavailable in the hosted judge demo."}, status_code=404))
        identities = headers.getlist("x-dev-user")
        if identities and (len(identities) != 1 or identities[0].strip().lower() not in DEMO_IDENTITIES):
            return await respond(JSONResponse({"detail": "Choose the Club or Student Affairs demo account."}, status_code=403))
        if path.startswith("/admin/api/") and not identities:
            return await respond(JSONResponse({"detail": "Choose the Club or Student Affairs demo account."}, status_code=401))
        if _expensive(method, path):
            fingerprint = hashlib.sha256(session.encode()).hexdigest()
            retry = self.limiter.take([(("ai-burst", fingerprint), 12, 300), (("ai-session", fingerprint), 40, 3600),
                                       (("ai-global",), 120, 3600)])
            if retry:
                return await respond(JSONResponse({"detail": "The demo's AI request limit has been reached. Wait before trying another AI action."},
                                                  status_code=429, headers={"Retry-After": str(retry)}))
        await self.app(scope, receive, secure_send)


def install(app) -> bool:
    """Call once during app construction; invalid hosted configuration fails startup."""
    enabled = os.environ.get("HOSTED_DEMO", "").strip().lower()
    if enabled in {"", "false", "0"}:
        return False
    if enabled not in {"true", "1"}:
        raise RuntimeError("HOSTED_DEMO must be true or false.")
    settings = _Settings.from_environment()
    app.add_middleware(HostedDemoMiddleware, settings=settings)
    return True
