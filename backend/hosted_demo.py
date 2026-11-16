"""Public, temporary judge demo with isolated data and bounded actions.

The two seeded roles are presentation choices, not independent authenticated
accounts. No access code or sign-in is required to experience the demo.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import math
import os
import re
import threading
import time
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from . import config

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
    email_preview: bool = False

    @classmethod
    def from_environment(cls):
        if not config.LOCAL_MODE:
            raise RuntimeError("HOSTED_DEMO requires LOCAL_MODE=true and disposable demo data.")
        base = os.environ.get("PUBLIC_BASE_URL", config.PUBLIC_BASE_URL).strip()
        try:
            parts = urlsplit(base)
            origin = _origin(base)
            if parts.path not in {"", "/"} or parts.query or parts.fragment:
                raise ValueError("Use an origin without a path")
            if origin[0] != "https" and origin[1] not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("HTTPS is required for a public demo")
        except ValueError:
            raise RuntimeError("PUBLIC_BASE_URL must be the demo's HTTPS origin. HTTP is allowed only for loopback QA.") from None
        preview_flag = os.environ.get("HOSTED_EMAIL_PREVIEW", "false").strip().lower()
        if preview_flag not in {"", "false", "0", "true", "1"}:
            raise RuntimeError("HOSTED_EMAIL_PREVIEW must be true or false.")
        email_preview = preview_flag in {"true", "1"}
        if email_preview:
            from .mailer import validate_recipient
            try:
                validate_recipient(os.environ.get("HOSTED_EMAIL_RECIPIENT", ""))
            except ValueError:
                raise RuntimeError("HOSTED_EMAIL_RECIPIENT must be one valid email address.") from None
        return cls(origin, email_preview)


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
        if path == "/healthz" and method in {"GET", "HEAD"}:
            return await self.app(scope, receive, secure_send)
        request = Request(scope, receive=receive)
        headers = request.headers
        if not self._correct_host(headers):
            return await respond(JSONResponse({"detail": "Open the configured BoardCast demo address."}, status_code=400))
        if method not in SAFE_METHODS and not self._same_origin(headers):
            return await respond(JSONResponse({"detail": "Submit this action from the BoardCast demo page."}, status_code=403))

        # Keep old bookmarks and already-open forms usable after removing the gates.
        if path in {"/judge/login", "/judge/owner", "/judge/logout"}:
            if method not in {"GET", "HEAD", "POST"}:
                return await respond(JSONResponse({"detail": "Method not allowed"}, status_code=405,
                                                  headers={"Allow": "GET, HEAD, POST"}))
            destination = "/admin?demo=student-affairs" if path == "/judge/owner" else "/demo"
            return await respond(RedirectResponse(destination, status_code=303))
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
                if not self.settings.email_preview:
                    return await respond(JSONResponse({"detail": "Email testing is disabled."}, status_code=404))
                # The recipient is fixed by server configuration. Cookies or role
                # switching cannot reset this shared limit.
                retry = self.limiter.take([(("email-preview-global",), 3, 3600)])
                if retry:
                    return await respond(JSONResponse({"detail": "The email test limit has been reached. Wait before sending again."},
                                                      status_code=429, headers={"Retry-After": str(retry)}))
        if _expensive(method, path):
            # Trust the ASGI peer, never a caller-supplied forwarding header.
            peer = scope.get("client") or ("unknown", 0)
            retry = self.limiter.take([(("ai-burst", peer[0]), 12, 300), (("ai-client", peer[0]), 40, 3600),
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
