# BoardCast

Clubs and APU offices submit a poster once. BoardCast extracts its details, checks them, and routes the submission to Student Affairs for approval. Campus Weekly brings the approved submissions together in one campus edition, by email and as a web magazine.

The prototype runs locally through a submission portal and ADK chat. Gemini Enterprise access is pending; the Enterprise connector and cloud deployment still require end-to-end verification. The deployment scripts schedule Campus Weekly for Monday at 8am Malaysia time.

APU Cloud Innovation Hackathon 2026 · Track 2: Intelligent Automation & Digital Transformation · full spec in `BOARDCAST_SPEC.md`

---

## Agentic workflow

```
Club rep / Student Affairs ──► ADK / Teams adapter ─────────────┐
                           └─► Gemini Enterprise (pending) ──┴─► Coordinator agent (routes only)
                                                       ├── Intake agent  → read_attached_poster · submit_item · fix_item
                                                       └── Review agent  → list_pending · decide_item · hold_issue
                                                                │  (every tool calls the API; the API re-checks the user's role)
Cloud Run API ◄─────────────────────────────────────────────────┘
  1. Extract   Gemini + response_schema → Pydantic model; QR links decoded in code
  2. Check     code: missing fields, past dates, unsafe links, consent
  3. Approve   Student Affairs (human), in chat or on /admin
  4. Edit      Gemini selects a spotlight id; code renders headline, teaser and subject from approved fields
  5. Publish   code (Monday 8am): one edition, recipient delivery ledger, Gmail API (local mode: HTML outbox)
  6. Track     Register / Add-to-calendar clicks, audit log in BigQuery
```

| Agent | File | Prompt | Tools / output |
| --- | --- | --- | --- |
| Coordinator | `agent/boardcast_agent/agent.py` | `prompts/coordinator.md` | Routes to the two sub-agents; no tools |
| Intake | same | `prompts/intake.md` | `read_attached_poster`, `submit_item`, `fix_item`, `list_pending` |
| Review | same | `prompts/review.md` | `list_pending`, `decide_item`, `hold_issue` |
| Extractor | `backend/extract.py` | `prompts/extract.md` | `EventExtraction` / `AnnouncementExtraction` / `AchievementExtraction` |
| Editor | `backend/editor.py` | `prompts/editor.md` | `SpotlightChoice`; code renders the `EditorPlan` |

The Intake agent has no approval tools. The API checks the caller's role independently, so requesting an approval through chat does not grant permission. `/submit` uses the same submission checks through the web UI; its manual-entry path is clearly labelled and works without an AI connection.

## Microsoft Teams

The Teams adapter (`teams/`) runs the same ADK agents. Local tests cover its message and attachment handling; a real tenant deployment still needs verification. Its current in-memory sessions and artifacts do not survive instance restarts, so persistent storage is required before a campus pilot.

| Step | Where |
| --- | --- |
| Teams message → Microsoft-signed request, checked by the Microsoft 365 Agents SDK | `teams/bot.py` `/api/messages` |
| User = their APU email from Teams (Teams member lookup), never typed by them | `teams/bot.py` `user_email()` |
| Poster image attached in chat → downloaded → passed to the agent as a file | `teams/bot.py`, `teams/bridge.py` |
| ADK runner: coordinator → intake / review, same prompts and tools | `teams/bridge.py` |
| Tools call the API as the agent service account; the API checks the user's role | `agent/boardcast_agent/tools.py`, `backend/auth.py` |

Poster uploads are images only. For a PDF poster, export it as a PNG or JPG before attaching it.
PDFs and other documents are rejected with a message asking for a poster image.

**Try it on a laptop (no bot registration):** Microsoft 365 Agents Playground simulates Teams.
```bash
# .env: TEAMS_LOCAL=true, BOARDCAST_API_URL=http://localhost:8081, DEV_USER_EMAIL=clubrep.demo@mail.apu.edu.my
uvicorn backend.main:app --port 8081       # the API
uvicorn teams.bot:app --port 3978          # the bot
npx @microsoft/m365agentsplayground         # opens a Teams-like chat on http://localhost:56150
```

**Real Teams:**
1. Teams Developer Portal (https://dev.teams.microsoft.com) → Tools → Bot management → New bot. Copy the bot id, create a client secret, note your tenant id. (No Azure subscription needed. If APU blocks app registrations, ask APU IT or use an Azure Bot resource.)
2. `TEAMS_BOT_ID=... TEAMS_BOT_SECRET=... TEAMS_TENANT_ID=... bash infra/deploy_teams.sh` → set the printed messaging endpoint on the bot.
3. `python -m teams.build_package` → upload `teams/build/boardcast-teams.zip` in Teams (Apps → Manage your apps → Upload a custom app).

## Prompt design

- Prompts are Markdown files in `prompts/` with `name`, `version` and `changelog` headers.
- Successful extractor and editor operations record prompt versions in the audit trail. Full chat tracing, token usage and cost accounting remain follow-up work.
- QR decoding, validation, counts and layout run in code. Gemini chooses only a valid spotlight event id; headline, teaser and subject are rendered from approved titles and dates. Model-written prose is not published, including prose from older saved previews.
- Chat tools select an exact attachment filename or the current message's upload. They never infer upload order from alphabetically sorted artifact names. Production identity comes from the authenticated adapter; local demo identity fallbacks cannot override it.

## Prompt accuracy eval

`eval/` measures how well `prompts/extract.md` reads **real APU posters**, field by field. See `eval/README.md`.

```bash
python -m eval.run_eval      # → eval/results/extract_<version>.md
```

Change the prompt → bump its version → run again → keep every results file. The before/after table is our evidence for "prompting design" and "app output".

Archived results show v1 at 99/103 scored fields and v2 at 103/103 across the same 14 posters. This is development-set accuracy after prompt tuning, not a held-out accuracy estimate. Some fields are unscored and link results include deterministic QR decoding. Freeze a separate unseen set before making general accuracy claims.

## Newsletter format

Campus Weekly reads like a newspaper: clubs submit, Student Affairs approves, BoardCast prints everything approved since the last issue (events on any upcoming date, each printed once). One
edition for every student, in fixed numbered sections (empty ones are left out):

| Part | Content | Written by |
| --- | --- | --- |
| Headline + teaser | The spotlight event and what else is on | Code from approved fields; Gemini chooses the spotlight |
| 1. Coming up | Table: date, time, event, venue, fee (any upcoming date) | Code |
| 2. Events | "When · where · fee – title by club." + the club's own 1-2 sentences + optional Register link + poster | The club |
| 3. Campus wins | Result, team, competition + Instagram link + poster | The club / APU |
| 4. Opportunities | "Closes ... – title by office." + their words + link + poster | The club / office |

Student Affairs can open **Newsletter template** on `/admin`, choose **Classic** (bordered
sections) or **Newspaper** (a lead story beside its poster, supporting story columns, and a
schedule below the news), and preview the email with
approved content. **Use this template** saves the default for future editions and also updates
the current edition if it is still an editable preview. The saved choice controls both email
and web layouts. Editions that have begun delivery keep their original layout, including retries.
Template previews are read-only and do not call Gemini or send email.
The newspaper rearranges the same approved copy; it does not add filler or rewrite submissions.
Email uses two supporting columns, the web edition uses up to three, and both stack on mobile.

Enable **Customize this template** to choose headline/body fonts, background/text/accent colours,
one to three story columns, and the lead poster's position. Drag stories into the lead area or
a column; each story also has a destination menu and move-up/down buttons for keyboard and
mobile use. **Preview newsletter** shows the candidate email before **Use this template** saves it.
The web edition supports three columns; email folds the third into the second for readability.
Appearance settings become the default for future editions. Story placement is saved to the
current editable edition only. A sent edition can be used to try a design without changing it.
Resetting the design restores the selected preset. This editor runs locally without Gemini.

## UI

| Screen | Route | Who |
| --- | --- | --- |
| Guided local walkthrough | `/demo` | Local demo users |
| Poster submission or explicit manual entry; all three content types | `/submit` | Club reps, Student Affairs |
| Subscribe (APU email + 6-digit code) | `/subscribe` | Students |
| Preferences / unsubscribe (signed links) | `/preferences?token=`, `/unsubscribe?token=` | Students |
| Web magazine | `/m/{issue_id}` | Everyone |
| Review queue with source poster, full fields, student-copy preview, inline rejection reason and stale-version checks | `/admin` (Google sign-in; demo roles locally) | Student Affairs, club reps within their permissions |
| Preview/publish controls and rendered email outbox | `/admin`, `/demo/outbox` | Local demo users; production publishing remains scheduled |
| Club report: items, status, rejection reasons, Register / calendar clicks, reminders set | `/admin/club` (Google sign-in) | Club reps (own club), Student Affairs (all) |
| Rejection email: title, reason, and how to correct and resubmit | `templates/rejection_email.html` | The original uploader |
| 1-hour event reminder | `templates/reminder_email.html`, hourly `POST /reminders/run` | Students who clicked Register / Add to calendar in their email |
| Weekly email | `templates/weekly_email.html` | Students |
| Chat | ADK locally; Gemini Enterprise pending access/verification | Club reps, Student Affairs |

## Repo map

| Folder | What |
| --- | --- |
| `agent/` | ADK agents (coordinator + 2 sub-agents) and their tools |
| `backend/` | FastAPI on Cloud Run: API, checks, extract, editor, issue builder, mailer, web UI |
| `prompts/` | Versioned prompt files |
| `eval/` | Real-poster accuracy eval |
| `shared/` | Pydantic schemas + prompt loader |
| `templates/`, `static/` | Email, magazine, student and admin pages |
| `infra/` | `setup.sh` (all Google Cloud resources), `deploy_agent.sh` |
| `scripts/` | Local launcher, seed demo roles, create Gmail token |
| `tests/` | `pytest -q` for Python; `node tests/frontend_state.cjs` and `node tests/newsletter_state.cjs` for browser-independent UI state regressions; no cloud needed |

## Data (Firestore)

`roles/{email}` role + club · `items/{id}` type, status, data, issues · `subscribers/{email}` active · `issues/{id}` week, status, item ids, editor plan, frozen delivery snapshot and recipient ledger · `clicks` · `saved/{email|item}` events a student registered for or added, reminded flag · `audit` (fallback when BigQuery is off)

Item status: `draft` (problems to fix) → `pending` → `approved` → `published`, or `rejected`.

Clubs obtain event and venue approval through the campus application system before promoting
their posters in BoardCast. BoardCast reviews poster content and publication; it does not compare
events for venue/time overlaps or block submissions on that basis. Venue, date and time are still
needed so students know where and when to attend. Editing/resubmitting an older draft clears any
previous venue-clash warning while keeping other content checks.

A separate registration URL is optional, including events that require registration. Clubs may
use their poster's QR code or instructions in the description; a missing URL never blocks review.
Register / More info links appear only when a URL is supplied or decoded. Optional HTTPS URLs in
descriptions become clickable in the email and web edition, using the existing allowed-domain
checks; digits in URLs do not count as extra event facts. QR scans and these direct description
links bypass BoardCast's click counts and automatic reminders. Editing/resubmitting an older
draft rechecks it under the current rules.

Signed subscription, reminder and staff-poster links retain their existing format. Verification
reads the fixed-length signature so binary separator characters cannot break valid links, and
malformed or modified links are rejected without an application error. Each event has Add to calendar; calendar links in a student's own email carry a signed token so the event can be saved for that subscriber's reminder.

Editing a submission clears its approval and sends it back for review. Immediately before a
newsletter send, BoardCast checks the preview's items again: only items still approved are included.
Copy is rebuilt from the remaining approved content. If none remain, no email is sent. Once delivery
starts, a frozen snapshot and recipient ledger keep retries consistent. Successful recipients are
not sent the edition again. Known rejected requests can be retried; timeouts or interrupted sends
with an uncertain provider outcome become `unknown` and require reconciliation with the sender's
Sent mailbox. BoardCast does not blindly resend them or claim exactly-once email delivery.
`POST /issues/retry`, scheduled every ten minutes, resumes only editions whose delivery already
started; it cannot send a fresh Sunday preview early. Partial delivery is shown explicitly, and
unresolved uncertain deliveries become `needs_attention`. Later item edits do not rewrite the
sent edition or get overwritten by its final publication update.

Student Affairs must give a reason when rejecting a submission, whether in chat or on the review
page. BoardCast saves that reason in the club report and automatically emails the original uploader
with the title, submission ID, reason, and correction/resubmission instructions. Repeating the same
rejection does not send another email. A delivery failure keeps the rejection and reason saved,
shows a warning to the reviewer, and offers Student Affairs a **Retry rejection email** button in
the club report. Correcting/resubmitting clears the old rejection details; a later rejection gets
its own notification. In LOCAL_MODE every email goes to the HTML outbox, even when Gmail credentials exist.

---

## Verify when Gemini Enterprise access arrives

1. **Identity and permissions:** verify the signed-in user's email reaches the trusted ADK context; prove a club rep cannot approve and Student Affairs can. Missing production identity must fail closed.
2. **Attachments:** upload an image, then a corrected image with a differently sorted filename; verify the selected artifact and extracted facts are the corrected version.
3. **Runtime:** verify the configured model/location, service-account token, API authorization and poster storage on the deployed runtime. Local test success does not establish cloud integration success.

## Run locally with no Google Cloud (LOCAL_MODE)

Use Python 3.12 and the installed requirements. The local launcher binds only to loopback,
uses the existing `local_data/`, and seeds missing demo roles/subscriber without replacing
existing submissions. Firestore becomes `local_data/db.json`, posters stay in `local_data/posters/`,
and **every email is an HTML outbox file**, regardless of configured Gmail credentials.

```bash
python -m venv .venv
.venv\Scripts\activate                       # macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements.txt -r agent/requirements.txt -r teams/requirements.txt
pytest -q
python -m scripts.run_local --port 8081 --offline
```

On Windows, `start-local.ps1` provides a shortcut. Open `http://127.0.0.1:8081/demo` and follow
Submit → Review → Preview → Publish → Outbox. The segmented switch at the top opens **Club**,
**Student Affairs** or **Outbox preview**. The first two select the matching demo account
automatically, and Outbox preview displays the latest saved email. This shortcut appears only
in `LOCAL_MODE`; normal backend role checks still apply. Offline mode uses explicit manual entry and
deterministic copy; it does not pretend to run AI extraction. To use Gemini extraction, omit
`--offline` and configure your Gemini API credentials (for AI Studio, `GOOGLE_GENAI_USE_VERTEXAI=FALSE`
and `GEMINI_API_KEY` or `GOOGLE_API_KEY`). Gemini Enterprise access is not required for this local API path.

| Try | How |
| --- | --- |
| Guided walkthrough | http://127.0.0.1:8081/demo |
| Poster or manual submission | http://127.0.0.1:8081/submit; select event, opportunity or achievement |
| Review queue | http://localhost:8081/admin, act as `studentaffairs.demo@apu.edu.my` |
| Club report | http://localhost:8081/admin/club, act as `clubrep.demo@mail.apu.edu.my` |
| Subscribe | http://localhost:8081/subscribe (the code email lands in `local_data/outbox/`) |
| Sunday preview | `curl -X POST localhost:8081/issues/preview`, then open `/m/<issue_id>` |
| Monday send | `curl -X POST localhost:8081/issues/send` → one email per subscriber in the outbox |
| Retry started delivery | `curl -X POST localhost:8081/issues/retry` → only eligible unsent recipients |
| Read local emails | http://127.0.0.1:8081/demo/outbox |
| Reminders | `curl -X POST localhost:8081/reminders/run` |
| Chat agent | `agent/boardcast_agent/.env`: `GOOGLE_GENAI_USE_VERTEXAI=FALSE`, `GOOGLE_API_KEY=...`, `BOARDCAST_API_URL=http://localhost:8081`, `DEV_USER_EMAIL=clubrep.demo@mail.apu.edu.my`; then `cd agent && adk web` |

Without a Gemini key, use the manual submission path. The editor uses a deterministic spotlight
fallback and source-based copy. Publishing changes item and delivery statuses; keep your existing
`local_data/` intact unless you deliberately want to discard your demo data.

## Run locally against Google Cloud

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt -r agent/requirements.txt -r teams/requirements.txt
cp .env.example .env              # set your project; DEV_MODE=true locally
gcloud auth application-default login
pytest -q                         # unit tests use mocked cloud services
python -m scripts.seed_roles      # demo club rep, Student Affairs, student
uvicorn backend.main:app --reload --port 8080
#   http://localhost:8080/subscribe   (DEV_MODE prints the code in the server log)
#   http://localhost:8080/admin       (DEV_MODE lets you type a demo email)
cd agent && adk web               # chat with the agents
```

QR decoding needs the `zbar` library (`brew install zbar` / `sudo apt install libzbar0`).

## Deploy

1. If no enabled Gmail token secret exists, create a sender token with `python -m scripts.get_gmail_token` after preparing the OAuth desktop client described in that script. Keep `token.json` private.
2. `GMAIL_TOKEN_FILE=token.json bash infra/setup.sh` (APIs, service accounts, Firestore, bucket, Pub/Sub, BigQuery, secrets, Cloud Run, Scheduler). Setup stops before deployment if a required secret has no usable version. Existing signing secrets are reused; enabled versions are pinned on deployment.
3. Create an OAuth **web** client id for `/admin` sign-in and set `OAUTH_CLIENT_ID` on the Cloud Run service
4. `BOARDCAST_API_URL=<Cloud Run URL> bash infra/deploy_agent.sh`. The script explicitly selects the `boardcast-agent` runtime account trusted by the API. The deployer needs permission to act as that account.
5. Gemini Enterprise console: add the Agent Engine agent, share it only with the Club reps and Student Affairs groups
6. The first `/issues/send` may ask Firestore for a composite index; open the link in the error to create it

## Judging criteria → evidence

| Criterion | Where |
| --- | --- |
| Problem statement value | Deck: poster count vs APSpace Notifications, survey, club interviews |
| UI | `/demo`, `/submit`, `/subscribe`, `/m/{id}`, `/admin`, weekly email; Enterprise chat once verified |
| Agentic workflow | Diagram above; `agent/`, `backend/editor.py` |
| Prompting + orchestration | `prompts/`, development-set `eval/results/`, ADK sub-agents, deterministic approved-field rendering |
| App output | A real issue from real posters; extraction accuracy table |
| Business value | Measure channels per event, promotion time saved and student reach in a pilot; token/cost accounting is not implemented yet |
| Pitch video | Storyboard in `BOARDCAST_SPEC.md` |

## Known gaps

- Gemini Enterprise access is pending; real identity, attachment handling and cloud deployment have not been verified end to end.
- Teams currently uses in-memory sessions/artifacts and needs durable storage before a campus pilot.
- The archived 14-poster evaluation is a development set. Held-out accuracy, broader difficult-poster coverage, workflow latency and measured cost remain to be collected.
- An ambiguous email-provider outcome requires human reconciliation. The system intentionally avoids automatic resends that could create duplicates.

## Production at APU

Proposed rollout: official APU sender with SPF, DKIM, DMARC (BoardCast never asks for passwords), an approved APSpace "Campus activities" integration, a formal achievement-consent process, and semesterly club-rep access reviews. These institutional integrations are not implemented in this prototype.
