"""Run the isolated, access-controlled judge demo. Never reads a local .env file."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from typing import MutableMapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class HostedSettings:
    public_url: str
    data_dir: Path
    port: int


def _public_url(value: str, *, local_test: bool) -> str:
    try:
        url = urlsplit(value.strip())
        host = url.hostname or ""
        port = url.port
    except ValueError:
        raise ValueError("PUBLIC_BASE_URL must be a valid HTTPS origin.") from None
    if (not host or url.username is not None or url.password is not None
            or url.path not in ("", "/") or url.query or url.fragment
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("PUBLIC_BASE_URL must be an origin without a path, credentials, query or fragment.")
    loopback = host.lower() in {"localhost", "127.0.0.1", "::1"}
    if local_test and loopback and url.scheme in {"http", "https"}:
        return value.strip().rstrip("/")
    if url.scheme != "https" or loopback:
        raise ValueError("The hosted demo requires a public HTTPS URL; localhost is only allowed with --local-test.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if ("." not in host or not host.isascii()
                or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                       for part in host.split("."))):
            raise ValueError("PUBLIC_BASE_URL must have a valid public hostname.") from None
    else:
        if not address.is_global:
            raise ValueError("PUBLIC_BASE_URL must not use a private network address.")
    return value.strip().rstrip("/")


def configure_environment(environ: MutableMapping[str, str] | None = None, *,
                          local_test: bool = False, test_data_dir: Path | None = None) -> HostedSettings:
    """Validate before importing backend modules, which normally load dotenv/config."""
    env = os.environ if environ is None else environ
    env["PYTHON_DOTENV_DISABLED"] = "1"
    if test_data_dir is not None and not local_test:
        raise ValueError("A custom data folder is only allowed with --local-test.")
    for key, minimum in (("DEMO_ACCESS_CODE", 16), ("DEMO_SESSION_SECRET", 32), ("LINK_SECRET", 32)):
        if len(env.get(key, "").strip()) < minimum:
            raise ValueError(f"Set {key} to a private value of at least {minimum} characters.")
    public_url = _public_url(env.get("PUBLIC_BASE_URL") or env.get("RENDER_EXTERNAL_URL", ""),
                             local_test=local_test)
    try:
        port = int(env.get("PORT", "10000"))
    except ValueError:
        raise ValueError("PORT must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be an integer from 1 to 65535.")

    preview_flag = env.get("HOSTED_EMAIL_PREVIEW", "false").strip().lower()
    if preview_flag not in {"", "false", "0", "true", "1"}:
        raise ValueError("HOSTED_EMAIL_PREVIEW must be true or false.")
    email_preview = preview_flag in {"true", "1"}
    if email_preview:
        owner_code = env.get("DEMO_OWNER_CODE", "").strip()
        if (not 32 <= len(owner_code) <= 256
                or owner_code == env.get("DEMO_ACCESS_CODE", "").strip()):
            raise ValueError("DEMO_OWNER_CODE must be 32–256 characters and different from the judge access code.")
        recipient = env.get("HOSTED_EMAIL_RECIPIENT", "").strip()
        local, separator, domain = recipient.partition("@")
        if (not separator or not recipient.isascii() or len(recipient) > 254 or len(local) > 64
                or local.startswith(".") or local.endswith(".") or ".." in local
                or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
                or "." not in domain or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                                           for part in domain.split("."))):
            raise ValueError("HOSTED_EMAIL_RECIPIENT must be one valid email address without a display name.")
        env["HOSTED_EMAIL_RECIPIENT"] = recipient.lower()
        # If provided, only a complete JSON OAuth credential may survive the cleanup.
        raw_token = env.get("GMAIL_TOKEN_JSON", "").strip()
        if raw_token:
            try:
                token = json.loads(raw_token)
                if not isinstance(token, dict) or not all(isinstance(token.get(key), str) and token[key].strip()
                        for key in ("refresh_token", "client_id", "client_secret")):
                    raise ValueError("Incomplete sender")
            except (ValueError, TypeError):
                raise ValueError("GMAIL_TOKEN_JSON must contain complete Gmail OAuth credentials.") from None

    # Only the explicit owner-test opt-in may retain Gmail JSON credentials.
    # ADC, token files and campus/cloud service identities are always cleared.
    for key in list(env):
        if key.upper().startswith(("GMAIL_", "CLOUDSDK_", "GOOGLE_APPLICATION_CREDENTIALS")):
            if email_preview and key == "GMAIL_TOKEN_JSON":
                continue
            env.pop(key, None)
    for key in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GOOGLE_CLOUD_QUOTA_PROJECT",
                "GOOGLE_CLOUD_LOCATION", "POSTER_BUCKET", "BQ_AUDIT_TABLE",
                "AGENT_SERVICE_ACCOUNT", "SCHEDULER_SERVICE_ACCOUNT", "OAUTH_CLIENT_ID"):
        env[key] = ""
    data_dir = (test_data_dir.resolve() if test_data_dir is not None else
                Path(tempfile.gettempdir()) / "boardcast-demo" / "data" if local_test else
                Path("/tmp/boardcast-demo/data"))
    env.update({
        "HOSTED_DEMO": "true", "LOCAL_MODE": "true", "DEV_MODE": "true",
        "LOCAL_DATA_DIR": str(data_dir), "PUBLIC_BASE_URL": public_url,
        "BOARDCAST_API_URL": public_url, "API_AUDIENCE": public_url,
        "PREREVIEW_AUTO": "false", "WEB_CONCURRENCY": "1",
        "HOSTED_EMAIL_PREVIEW": "true" if email_preview else "false",
        # Do not fall back to a token.json that happens to exist in the working tree.
        "GMAIL_TOKEN_FILE": str(data_dir / ".real-email-disabled"),
    })
    # An AI Studio key is the simple optional path. Explicit TRUE supports an Agent
    # Platform API key; the cloud-project/ADC path is disabled above in both cases.
    env.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "false")
    return HostedSettings(public_url=public_url, data_dir=data_dir, port=port)


def seed_demo() -> None:
    """Add missing labelled samples only; never replace a judge's existing changes."""
    from backend import checks, config, editor, issue_builder, mailer, store
    from scripts.seed_roles import ROLES, _next_monday, attach_sample_poster, sample_items

    for email, role in ROLES.items():
        if not store.get_role(email):
            store.db().collection("roles").document(email).set(role)
    demo_email = "student.demo@mail.apu.edu.my"
    if not store.get_subscriber(demo_email):
        store.upsert_subscriber({"email": demo_email, "active": True})
    marker = store.db().collection("settings").document("hosted_demo_seed")
    if marker.get().exists:
        return
    today = datetime.now(ZoneInfo(config.TIMEZONE)).date()
    monday = _next_monday(today)
    for item in sample_items(monday):
        if store.get_item(item["id"]):
            continue
        item["issues"] = checks.run_all(item["type"], item["data"])
        if item["type"] != "announcement":
            attach_sample_poster(item)
        store.create_item(item)

    # Deterministic sample output, explicitly labelled. Do not call Gemini during boot.
    issue_id = "sample-edition"
    if not store.get_issue(issue_id):
        # Use the review desk's current edition date. A future week_start would
        # outrank every preview judges build today when selecting the latest issue.
        edition = issue_builder.collect(today)
        items = issue_builder.all_items(edition)
        plan = editor.fallback_plan(items)
        settings = issue_builder.publication_settings()
        store.save_issue({
            "id": issue_id, "week_start": today.isoformat(), "status": "preview",
            "item_ids": [item["id"] for item in items], "items": items,
            "editor_plan": plan.model_dump(),
            "editor_report": {"source": "sample", "copy_source": "approved_fields",
                              "note": "Prepared sample; no AI call was made during startup."},
            "newsletter_template": settings["template"],
            "newsletter_design": issue_builder.newsletter_design(settings["design"], settings["template"]),
        })
    edition, plan = issue_builder.from_saved(store.get_issue(issue_id))
    html = issue_builder.render_email(edition, plan, {"email": demo_email}, issue_id=issue_id)
    # Call the local outbox writer directly: initialization cannot use real mail.
    mailer._to_outbox(demo_email, "[SAMPLE] Campus Weekly — judge demo", html)
    marker.set({"completed_at": store.now(), "edition_id": issue_id})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-test", action="store_true", help="Allow loopback HTTP for local QA only.")
    parser.add_argument("--data-dir", type=Path, help="Isolated QA directory; requires --local-test.")
    args = parser.parse_args(argv)
    try:
        settings = configure_environment(local_test=args.local_test, test_data_dir=args.data_dir)
    except ValueError as exc:
        parser.error(str(exc))
    # Gate setup must succeed before sample initialization or opening a port.
    from backend.main import app
    seed_demo()
    import uvicorn
    print("BoardCast judge sandbox: access code required; sample data; ordinary delivery stays in the outbox.", flush=True)
    print("Free-host data may reset when the service sleeps, restarts or redeploys.", flush=True)
    uvicorn.run(app, host="127.0.0.1" if args.local_test else "0.0.0.0", port=settings.port, workers=1)


if __name__ == "__main__":
    main()
