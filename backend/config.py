"""Settings read from environment variables (see .env.example)."""
import os
from pathlib import Path

try:  # local runs: read .env from the repo root (Cloud Run has no .env and sets real env vars)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-southeast1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")  # set to the newest Flash model you have access to

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8080")
POSTER_BUCKET = os.getenv("POSTER_BUCKET", "")
PUBSUB_TOPIC_SUBMITTED = os.getenv("PUBSUB_TOPIC_SUBMITTED", "item-submitted")
BQ_AUDIT_TABLE = os.getenv("BQ_AUDIT_TABLE", "")  # project.dataset.audit_log; empty = log to Firestore only

# Only the ADK agent's service account may call the protected API.
AGENT_SERVICE_ACCOUNT = os.getenv("AGENT_SERVICE_ACCOUNT", "")
# Audience the agent uses when it mints an ID token (= this service's URL).
API_AUDIENCE = os.getenv("API_AUDIENCE", PUBLIC_BASE_URL)
# Scheduler jobs call /issues/* with an ID token from this service account.
SCHEDULER_SERVICE_ACCOUNT = os.getenv("SCHEDULER_SERVICE_ACCOUNT", "")

DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"  # skips token checks locally; never true in Cloud Run

# LOCAL_MODE: run on a laptop with no Google Cloud project. Firestore -> LOCAL_DATA_DIR/db.json,
# posters -> LOCAL_DATA_DIR/posters, emails -> LOCAL_DATA_DIR/outbox/*.html. Implies DEV_MODE.
LOCAL_MODE = os.getenv("LOCAL_MODE", "false").lower() == "true"
DEV_MODE = DEV_MODE or LOCAL_MODE
LOCAL_DATA_DIR = Path(os.getenv("LOCAL_DATA_DIR", Path(__file__).resolve().parent.parent / "local_data"))
# Isolated, access-code-protected judge workspace. Never enabled for a normal local run.
HOSTED_DEMO = os.getenv("HOSTED_DEMO", "false").lower() == "true"

# Gemini, three ways:
#   Cloud Run:  GOOGLE_GENAI_USE_VERTEXAI=TRUE + GOOGLE_CLOUD_PROJECT (service account, no key)
#   Laptop:     GOOGLE_GENAI_USE_VERTEXAI=TRUE + GEMINI_API_KEY = an Agent Platform key ("AQ." prefix),
#               and NO GOOGLE_CLOUD_PROJECT / LOCATION in .env (the key wins only without them)
#   Laptop:     GOOGLE_GENAI_USE_VERTEXAI=FALSE + GEMINI_API_KEY = an AI Studio Gemini API key
USE_VERTEX = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE").lower() in ("true", "1")
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or None

ALLOWED_LINK_DOMAINS = [
    d.strip() for d in os.getenv(
        "ALLOWED_LINK_DOMAINS",
        "forms.gle,docs.google.com,forms.office.com,apu.edu.my,www.instagram.com,instagram.com",
    ).split(",") if d.strip()
]

TIMEZONE = "Asia/Kuala_Lumpur"


def _optional_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else None
    except ValueError:
        return None
    return value if value is not None and value >= 0 else None


# Pre-review agent (backend/prereview.py): briefs Student Affairs on each pending submission.
# It never changes a submission's status. PREREVIEW_AUTO=false leaves only the manual button.
PREREVIEW_AUTO = os.getenv("PREREVIEW_AUTO", "true").lower() == "true"
PREREVIEW_TEMPERATURE = _optional_float("PREREVIEW_TEMPERATURE")   # unset = the model's default

# Cost estimates in the run trace. Copy the prices for GEMINI_MODEL from the official pricing
# page (US$ per one million tokens). Unset = the trace shows token counts without a cost.
GEMINI_PRICE_INPUT_PER_M = _optional_float("GEMINI_PRICE_INPUT_PER_M")
GEMINI_PRICE_OUTPUT_PER_M = _optional_float("GEMINI_PRICE_OUTPUT_PER_M")

# Google Identity Services client id for the Student Affairs / club web pages (/admin)
OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
APU_EMAIL_DOMAINS = ("@mail.apu.edu.my", "@apu.edu.my")
