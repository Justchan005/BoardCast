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
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config

SESSION_SECONDS = 4 * 60 * 60
OWNER_SESSION_SECONDS = 15 * 60
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
    owner_code: str | None = None
    owner_signing_key: bytes = b""

    @property
    def owner_cookie_name(self) -> str:
        return "__Host-boardcast_owner" if self.secure_cookie else "boardcast_owner_loopback"

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
        preview_flag = os.environ.get("HOSTED_EMAIL_PREVIEW", "false").strip().lower()
        if preview_flag not in {"", "false", "0", "true", "1"}:
            raise RuntimeError("HOSTED_EMAIL_PREVIEW must be true or false.")
        owner_code, owner_key = None, b""
        if preview_flag in {"true", "1"}:
            owner_code = os.environ.get("DEMO_OWNER_CODE", "").strip()
            if not 32 <= len(owner_code) <= 256 or hmac.compare_digest(owner_code.encode(), code.encode()):
                raise RuntimeError("DEMO_OWNER_CODE must be 32–256 characters and different from the judge access code.")
            from .mailer import validate_recipient
            try:
                validate_recipient(os.environ.get("HOSTED_EMAIL_RECIPIENT", ""))
            except ValueError:
                raise RuntimeError("HOSTED_EMAIL_RECIPIENT must be one valid email address.") from None
            owner_key = hmac.new(key, b"boardcast-owner-session\0" + owner_code.encode(), hashlib.sha256).digest()
        return cls(origin, code, key, secure,
                   "__Host-boardcast_judge" if secure else "boardcast_judge_loopback", owner_code, owner_key)

    def session(self) -> str:
        return self._session(self.signing_key, SESSION_SECONDS)

    def owner_session(self, judge_session: str) -> str:
        if not self.owner_code or not self.valid_session(judge_session):
            raise ValueError("Owner sessions require enabled owner access and a valid judge session.")
        return self._session(self.owner_signing_key, OWNER_SESSION_SECONDS,
                             hashlib.sha256(judge_session.encode()).hexdigest())

    @staticmethod
    def _session(key: bytes, duration: int, binding: str | None = None) -> str:
        now = int(time.time())
        data = json.dumps({"iat": now, "exp": now + duration, "nonce": secrets.token_hex(16), "binding": binding},
                          separators=(",", ":")).encode()
        payload = base64.urlsafe_b64encode(data).decode().rstrip("=")
        signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        return payload + "." + signature

    def valid_session(self, value: str) -> bool:
        return self._valid_session(value, self.signing_key, SESSION_SECONDS)

    def valid_owner_session(self, value: str, judge_session: str) -> bool:
        return bool(self.owner_code and self.valid_session(judge_session)
                    and self._valid_session(value, self.owner_signing_key, OWNER_SESSION_SECONDS,
                                            hashlib.sha256(judge_session.encode()).hexdigest()))

    @staticmethod
    def _valid_session(value: str, key: bytes, duration: int, binding: str | None = None) -> bool:
        if not value or len(value) > 512:
            return False
        try:
            payload, signature = value.split(".")
            expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return False
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            now = int(time.time())
            return (type(data.get("iat")) is int and type(data.get("exp")) is int
                    and now - duration <= data["iat"] <= now + 30
                    and now < data["exp"] <= data["iat"] + duration
                    and data.get("binding") == binding
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


def _login_page(error: str = "", *, owner: bool = False) -> str:
    # Errors are fixed strings supplied by this module. Never reflect request data.
    message = f'<p class="error" role="alert">{error}</p>' if error else ""
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Enter the BoardCast judge demo</title>
<style>*{box-sizing:border-box}html{color-scheme:light}body{margin:0;min-height:100vh;background:#102b4b;color:#172b43;font:17px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;display:grid;place-items:center;padding:24px}main{width:100%;max-width:530px;background:#fff;padding:clamp(26px,6vw,48px);border-radius:16px;box-shadow:0 18px 50px #06182955}h1{font-size:clamp(28px,5vw,38px);line-height:1.15;margin:0 0 20px;letter-spacing:-.025em;text-wrap:balance}p{margin:0 0 24px;color:#40566e}label{display:block;font-weight:650;margin-bottom:8px}input,button{width:100%;min-height:52px;border-radius:8px;font:inherit}input{border:1px solid #7890aa;padding:11px 14px;color:#172b43;background:#fff}button{margin-top:18px;border:1px solid #154c8d;background:#154c8d;color:#fff;font-weight:650;cursor:pointer}button:hover{background:#103e74}input:focus-visible,button:focus-visible{outline:3px solid #397bc8;outline-offset:4px}.note{font-size:14px;margin:24px 0 0}.error{color:#9b242c;background:#fff2f2;padding:12px 14px;border-radius:6px}::selection{background:#c9ddf7;color:#102b4b}</style></head>
<body><main><h1>Try the BoardCast judge demo</h1><p>Follow a club submission through review and into Campus Weekly. Enter the access code shared by the team to begin.</p>""" + message + """
<form action="/judge/login" method="post"><label for="access-code">Demo access code</label>
<input id="access-code" name="access_code" type="password" autocomplete="current-password" minlength="16" maxlength="256" required autofocus>
<button type="submit">Open BoardCast</button></form><p class="note">This is a shared demonstration with sample accounts. Use test content. Data can reset when the service restarts; ordinary publishing stays in the demo outbox.</p></main></body></html>"""
    if owner:
        html = (html.replace("Enter the BoardCast judge demo", "Unlock the owner email test")
                .replace("Try the BoardCast judge demo", "Unlock the owner email test")
                .replace("Follow a club submission through review and into Campus Weekly. Enter the access code shared by the team to begin.",
                         "Enter the private owner code to send a newsletter preview to the single test address configured by the owner.")
                .replace("/judge/login", "/judge/owner").replace("access-code", "owner-code")
                .replace("access_code", "owner_code").replace("Demo access code", "Private owner code")
                .replace('minlength="16"', 'minlength="32"').replace("Open BoardCast", "Unlock email test")
                .replace("This is a shared demonstration with sample accounts. Use test content. Data can reset when the service restarts; ordinary publishing stays in the demo outbox.",
                         "Owner access lasts 15 minutes in this browser. Keep the owner code private. Ordinary publication still stays in the demo outbox."))
    return html


def _is_api(path: str) -> bool:
    return path.startswith("/admin/api/") or path.startswith(SERVICE_PREFIXES) or path == "/openapi.json"


def _email_preview_path(path: str) -> bool:
    return re.fullmatch(r"/admin/api/issues/[^/]+/email-preview", path) is not None


def _blocked(path: str, method: str = "POST") -> bool:
    if path == "/admin/api/real-email/status" and method == "GET":
        return False
    if _email_preview_path(path) and method == "POST":
        return False
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
        # Only this middleware can grant owner access. Never derive it from headers.
        scope.setdefault("state", {})["hosted_owner"] = False
        if path == "/healthz" and method in {"GET", "HEAD"}:
            return await self.app(scope, receive, secure_send)
        request = Request(scope, receive=receive)
        headers = request.headers
        if not self._correct_host(headers):
            return await respond(JSONResponse({"detail": "Open the configured BoardCast demo address."}, status_code=400))
        session = request.cookies.get(self.settings.cookie_name, "")
        authenticated = self.settings.valid_session(session)
        owner_authenticated = (authenticated and self.settings.valid_owner_session(
            request.cookies.get(self.settings.owner_cookie_name, ""), session))
        scope["state"]["hosted_owner"] = bool(owner_authenticated)
        if (authenticated or path == "/judge/login") and method not in SAFE_METHODS and not self._same_origin(headers):
            return await respond(JSONResponse({"detail": "Submit this action from the BoardCast demo page."}, status_code=403))

        if path in {"/judge/login", "/judge/owner"}:
            owner_unlock = path == "/judge/owner"
            if owner_unlock and not authenticated:
                return await respond(RedirectResponse("/judge/login", status_code=303) if method in {"GET", "HEAD"}
                                     else JSONResponse({"detail": "Enter the judge demo access code first."}, status_code=401))
            if owner_unlock and not self.settings.owner_code:
                return await respond(JSONResponse({"detail": "Owner email testing is disabled."}, status_code=404))
            destination = "/admin?demo=student-affairs" if owner_unlock else "/demo"
            already_unlocked = owner_authenticated if owner_unlock else authenticated
            if method == "GET":
                response = (RedirectResponse(destination, status_code=303) if already_unlocked else
                            HTMLResponse(_login_page(owner=owner_unlock)))
                response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'self'"
                return await respond(response)
            if method != "POST":
                return await respond(JSONResponse({"detail": "Method not allowed"}, status_code=405, headers={"Allow": "GET, POST"}))
            peer = scope.get("client") or ("unknown", 0)
            activity = "owner-login" if owner_unlock else "login"
            retry = self.limiter.take([((activity, peer[0]), 10, 600), ((activity + "-global",), 200, 600)])
            if retry:
                return await respond(HTMLResponse(_login_page("Too many sign-in attempts. Wait a few minutes, then try again.", owner=owner_unlock),
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
                codes = fields.get("owner_code" if owner_unlock else "access_code", [])
                code = codes[0].strip() if len(codes) == 1 else ""
            except (ValueError, UnicodeError, ClientDisconnect):
                return await respond(JSONResponse({"detail": "The access-code form is invalid or too large."}, status_code=400))
            except asyncio.TimeoutError:
                return await respond(JSONResponse({"detail": "The sign-in request timed out. Try again."}, status_code=408))
            expected_code = self.settings.owner_code if owner_unlock else self.settings.access_code
            if not hmac.compare_digest(code.encode(), expected_code.encode()):
                return await respond(HTMLResponse(_login_page("The code was not recognised. Check the code and try again.", owner=owner_unlock), status_code=401))
            response = RedirectResponse(destination, status_code=303)
            response.set_cookie(self.settings.owner_cookie_name if owner_unlock else self.settings.cookie_name,
                                self.settings.owner_session(session) if owner_unlock else self.settings.session(),
                                max_age=OWNER_SESSION_SECONDS if owner_unlock else SESSION_SECONDS,
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
            response.delete_cookie(self.settings.owner_cookie_name, path="/", secure=self.settings.secure_cookie,
                                   httponly=True, samesite="strict")
            return await respond(response)
        if _blocked(path, method):
            return await respond(JSONResponse({"detail": "This action is unavailable in the hosted judge demo."}, status_code=404))
        identities = headers.getlist("x-dev-user")
        if identities and (len(identities) != 1 or identities[0].strip().lower() not in DEMO_IDENTITIES):
            return await respond(JSONResponse({"detail": "Choose the Club or Student Affairs demo account."}, status_code=403))
        if path.startswith("/admin/api/") and not identities:
            return await respond(JSONResponse({"detail": "Choose the Club or Student Affairs demo account."}, status_code=401))
        if path == "/admin/api/real-email/status" or _email_preview_path(path):
            if identities[0].strip().lower() != "studentaffairs.demo@apu.edu.my":
                return await respond(JSONResponse({"detail": "Use the Student Affairs demo account."}, status_code=403))
            if _email_preview_path(path):
                if not self.settings.owner_code:
                    return await respond(JSONResponse({"detail": "Owner email testing is disabled."}, status_code=404))
                if not owner_authenticated:
                    return await respond(JSONResponse({"detail": "Unlock the owner email test first."}, status_code=403))
                # Key by the configured owner, not the cookie: re-unlocking cannot
                # reset the limit. A restart still resets these process-local caps.
                owner_id = hashlib.sha256(self.settings.owner_signing_key).hexdigest()
                retry = self.limiter.take([(("owner-email", owner_id), 3, 3600), (("owner-email-global",), 10, 3600)])
                if retry:
                    return await respond(JSONResponse({"detail": "The owner's email test limit has been reached. Wait before sending again."},
                                                      status_code=429, headers={"Retry-After": str(retry)}))
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
