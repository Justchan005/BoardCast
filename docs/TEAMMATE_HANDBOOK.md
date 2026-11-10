# 📘 BoardCast Developer & Teammate Handbook
> **A Comprehensive Guide to Understanding, Running, and Developing BoardCast**  
> *APU Cloud Innovation Hackathon 2026 · Track 2: Intelligent Automation & Digital Transformation*

---

## 🌟 1. Welcome to BoardCast

Welcome to the team! This handbook is designed to get you from zero to fully operational with **BoardCast**.

### The Problem We Are Solving
At Asia Pacific University (APU), student activities and club initiatives are fragmented across physical lift-lobby posters and disparate club Instagram accounts. Meanwhile, official university notifications (APSpace) are reserved for administrative broadcasts. Consequently, students miss out on activities they would have loved, and clubs spend excessive time cross-promoting with limited reach.

### What BoardCast Does
Clubs and university offices submit a poster once. BoardCast automatically:
1. **Extracts & checks** the details with multimodal AI and code guardrails.
2. **Routes** the submission to Student Affairs for moderation.
3. **Curates & Publishes** **"Campus Weekly"**—a unified campus edition sent every Monday at 8:00 AM via email and an online web magazine.

### Core Engineering Philosophy
> **"AI reads and writes words. Code does everything that must be exact. People make the decisions."**
* **AI (Gemini)**: Reads poster images and selects the weekly Spotlight.
* **Code (Deterministic Python/FastAPI)**: Decodes QR codes, enforces validations, checks for conflicting dates/times, tracks deliveries, and renders final published copy.
* **People (Student Affairs & Club Reps)**: Moderate submissions, approve/reject content, and retain publication hold controls.

---

## 🏗️ 2. High-Level Architecture & Tech Stack

```
   ┌─────────────────────────────────────────────────────────┐
   │                    INTAKE SURFACES                      │
   │   Web Portal (/submit)  ·  Teams Bot  ·  ADK Chat       │
   └────────────────────────────┬────────────────────────────┘
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────┐
   │                  AI & PARSING ENGINE                    │
   │   Gemini 3.5 Flash (Extraction) + pyzbar (QR Link Decode)│
   └────────────────────────────┬────────────────────────────┘
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────┐
   │               DETERMINISTIC GUARDRAILS                  │
   │   checks.py (Required fields, past dates, safe URLs,    │
   │              description numbers vs fields, poster sync) │
   └────────────────────────────┬────────────────────────────┘
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────┐
   │                  HUMAN MODERATION                       │
   │   Student Affairs Review Queue (/admin or Chat Agent)   │
   │   - Approval Gate  ·  Rejection with Automated Notice   │
   └────────────────────────────┬────────────────────────────┘
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────┐
   │             EDITORIAL & CURATION (Sunday 6pm)           │
   │   - Editor Agent selects Spotlight ID                   │
   │   - Deterministic Copy Generator (Zero Hallucination)   │
   │   - Student Affairs Preview & Hold Controls             │
   └────────────────────────────┬────────────────────────────┘
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────┐
   │               DISTRIBUTION (Monday 8am)                 │
   │   - Frozen Delivery Snapshot & Recipient Ledger         │
   │   - Table-Based HTML Email (Gmail API / Local Outbox)   │
   │   - Interactive Web Magazine (/m/{issue_id})            │
   └────────────────────────────┬────────────────────────────┘
                                │
                                ▼
   ┌─────────────────────────────────────────────────────────┐
   │              STUDENT ENGAGEMENT & ANALYTICS             │
   │   - One-Click .ics Calendar Downloads                   │
   │   - 1-Hour Pre-Event Reminder Emails (/reminders/run)   │
   │   - Real-Time Club Analytics Dashboard (/admin/club)    │
   └─────────────────────────────────────────────────────────┘
```

### Technology Stack
* **Backend API & Web**: FastAPI, Uvicorn, Jinja2, Pydantic v2
* **Agentic AI**: Google Agent Development Kit (ADK), Gemini 3.5 Flash
* **Computer Vision & Decoding**: `pyzbar` / `libzbar0` (QR decoding), Pillow (image sanitization & EXIF stripping)
* **Cloud Architecture (Production)**: Cloud Run, Firestore, BigQuery audit log, Cloud Storage, Cloud Scheduler, Secret Manager, Gmail API
* **Local Development (Zero-Cloud)**: In-memory/JSON filesystem stand-in (`backend/localdb.py`), local disk poster storage, HTML outbox

---

## 📁 3. Repo Directory Map

Here is where the key components live:

```text
boardcast/
├── agent/                       # Google ADK multi-agent system
│   └── boardcast_agent/         # Coordinator, Intake, and Review agents + tools
├── backend/                     # FastAPI core backend service
│   ├── checks.py                # Deterministic validation rules & guardrails
│   ├── delivery.py              # Durable email dispatcher & recipient ledger
│   ├── editor.py                # Gemini Spotlight selection & copy validation
│   ├── extract.py               # Multimodal Gemini poster extraction
│   ├── issue_builder.py         # Newsletter layout engine & template dispatcher
│   ├── localdb.py               # Offline local Firestore stand-in (JSON file)
│   ├── main.py                  # API routes, lifespan, and dependency injection
│   ├── qr.py                    # Deterministic QR code link reader
│   └── web.py                   # Frontend web routes & admin REST APIs
├── docs/                        # Architecture specs, diagrams, and video assets
├── eval/                        # Prompt evaluation harness with real APU posters
├── infra/                       # Google Cloud deployment shell scripts
├── prompts/                     # Versioned Markdown prompt files
├── scripts/                     # Local launcher (run_local.py), demo seeders
├── shared/                      # Single-source-of-truth Pydantic schemas
├── static/                      # CSS styling and frontend client JavaScript
├── teams/                       # Microsoft Teams bot adapter (M365 Agents SDK)
├── templates/                   # Jinja2 HTML templates for email, web, & admin
└── tests/                       # Complete Python & Node.js test suites
```

---

## ⚙️ 4. Quickstart: Running the Code Locally

You can run BoardCast **completely offline** without Google Cloud credentials or API credits.

### Step 1: Clone and Enter the Repository
```bash
git clone https://github.com/Justchan005/BoardCast.git
cd BoardCast
```

### Step 2: Set Up Python Virtual Environment (Python 3.11 or 3.12)

**On Windows (PowerShell):**
```powershell
python -m venv .venv
# If script execution is blocked on your machine, run:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt -r agent/requirements.txt
```

**On macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r agent/requirements.txt
```

### Step 3: Copy Configuration File
* Windows: `Copy-Item .env.example .env`
* macOS/Linux: `cp .env.example .env`

> [!NOTE]
> * **Offline Mode (Default)**: Leave `.env` as is! You can enter event data manually and test all features without making external AI calls.
> * **AI Mode**: To test automated poster extraction, add your free Google AI Studio key in `.env`:
>   ```env
>   GEMINI_API_KEY=your_key_here
>   GOOGLE_GENAI_USE_VERTEXAI=FALSE
>   ```

### Step 4: Start the Local Development Server
Use the built-in launcher script:

**Windows PowerShell Shortcut:**
```powershell
.\start-local.ps1 -SampleItems
```

**Universal Command (Windows / macOS / Linux):**
```bash
python -m scripts.run_local --port 8081 --offline --sample-items
```

*(The `--sample-items` flag pre-loads sample campus events so your review queue and newsletter have immediate content).*

### Step 5: Open the Interactive Demo
Open your browser to: **[http://127.0.0.1:8081/demo](http://127.0.0.1:8081/demo)**

---

## 🧭 5. Interactive Demo Tour (The 5-Step Flow)

Once the local server is running, use the **role switcher bar at the top** to test each perspective:

```
[ 🎓 Club Rep ]  [ 🛡️ Student Affairs ]  [ 📬 Outbox Preview ]
```

### 1. Submit an Item (Club Rep Role)
* Click **🎓 Club Rep** or visit [`/submit`](http://127.0.0.1:8081/submit).
* Choose **Event**, **Announcement**, or **Achievement**.
* Upload a poster image (PNG/JPG) or fill in details manually.
* Notice the real-time preview card at the bottom. Click **Submit**.

### 2. Moderate Submissions (Student Affairs Role)
* Click **🛡️ Student Affairs** or visit [`/admin`](http://127.0.0.1:8081/admin).
* The queue displays pending items, the uploaded poster image, and detected issues.
* Click **Approve** on items to qualify them for next Monday's issue.
* Test the **Reject** button: enter a rejection reason. The system logs the explanation in the Club Report and sends a rejection email to the uploader.

### 3. Customize Newsletter Design & Layout
* On the `/admin` page, click **Newsletter template**.
* Select between **Classic** (boxed cards) and **Newspaper** (editorial multi-column layout).
* Check **Customize this template** to:
  * Pick typography (`Georgia`, `Times New Roman`, `Arial`, `Verdana`).
  * Pick custom background, text, and accent colors.
  * Drag and drop stories into different columns or set a custom Lead article.
* Click **Preview newsletter** to inspect a live sandboxed render.
* Click **Use this template** to save.

### 4. Build Sunday Preview & Publish Monday Issue
* On `/admin` (or `/demo`), click **Build Sunday preview**.
* Click **Read web magazine** ([`/m/{issue_id}`](http://127.0.0.1:8081/m/...)) to view the student web layout.
* Click **Publish Monday edition** to trigger distribution.

### 5. Inspect Sent Emails (Local Outbox)
* Click **📬 Outbox** or visit [`/demo/outbox`](http://127.0.0.1:8081/demo/outbox).
* In local mode, emails are never dispatched externally. Instead, every rendered email is saved to `local_data/outbox/` and previewable inside the interactive iframe viewer.

---

## 🧪 6. Testing & Quality Assurance

BoardCast maintains a comprehensive regression suite:

### 1. Python Unit & Flow Tests
```bash
pytest -q
```
* Covers deterministic checks (`test_checks.py`), durable delivery retry logic (`test_delivery.py`), role enforcement (`test_role_assignment.py`), and layout engines (`test_newspaper_layout.py`).

### 2. Client-Side DOM & State Regression Tests (Requires Node.js)
```bash
node tests/frontend_state.cjs
node tests/newsletter_state.cjs
```
* Verifies UI state machines, role switching, keyboard accessibility, modal sandboxing, and drag-and-drop story arrangements without needing a browser.

### 3. Prompt Accuracy Evaluation Harness
```bash
python -m eval.run_eval
```
* Compares `prompts/extract.md` against real APU posters and outputs field-by-field accuracy scorecards in `eval/results/`.

---

## 🔒 7. Security & Contribution Rules

When contributing code, please follow these strict guidelines:

1. **NEVER commit `.env` or credentials**:
   * API keys, OAuth tokens (`token.json`), and virtual environments (`.venv/`) are gitignored. Always verify with `git status` before committing.
2. **Deterministic Copy Rule**:
   * AI must **never** write free-form published newsletter text. Headings, dates, teasers, and subjects must be rendered strictly from verified fields via Jinja2 templates.
3. **Dual Authorization Pattern**:
   * Every protected endpoint verifies both the service caller token and the user's role in Firestore (`Role.CLUB_REP` vs `Role.STUDENT_AFFAIRS`).
4. **Input Sanitization**:
   * Uploaded posters are parsed, stripped of EXIF data, resized to 600px width, and re-encoded as clean JPEGs before storage.

---

## ❓ 8. FAQ & Troubleshooting

* **Q: PowerShell says "running scripts is disabled on this system".**  
  * **A**: Open PowerShell and run:  
    `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
* **Q: Error: `Address already in use` on port 8081.**  
  * **A**: Another instance is running. Launch on a different port:  
    `python -m scripts.run_local --port 8082 --offline`
* **Q: Why does QR code decoding fail locally?**  
  * **A**: QR decoding requires the underlying system library `zbar`. On Ubuntu/Debian: `sudo apt install libzbar0`. On macOS: `brew install zbar`. On Windows, standard wheels usually include it; if missing, QR decoding gracefully falls back to text URLs.
* **Q: Where is data stored locally?**  
  * **A**: All local records reside in `boardcast/local_data/db.json` and posters in `boardcast/local_data/posters/`. You can delete `local_data/` at any time to reset to a blank slate.
