# BoardCast judge demo on Render

This deployment gives judges a public browser link with no access code. They can switch directly between Club, Student Affairs and Outbox. It is a shared sandbox with labelled sample content, not the campus production service.

## Deploy the prepared version

1. Push the reviewed files, including `render.yaml` and `Dockerfile.render`, to the repository branch you want Render to use. Do not commit `.env`, Gmail tokens or personal demo data.
2. In Render, create a **Blueprint**, connect that GitHub repository, and select the reviewed branch. The Blueprint creates one **Free** Docker web service in **Singapore**, with automatic deployment off and `/healthz` for health checks. It creates no paid disk or database.
3. Let the build and health check finish. Render supplies `RENDER_EXTERNAL_URL`; the launcher uses it for poster, newsletter and calendar URLs. If using a custom domain, set `PUBLIC_BASE_URL` to its HTTPS origin and redeploy.
4. Share the Render URL directly. No judge or owner access code is needed. Keep server credentials and `LINK_SECRET` private.
5. Verify the judge journey below before submitting the link. After changes, deploy manually from the reviewed commit.

The Blueprint generates `LINK_SECRET` for signed newsletter preference and unsubscribe links. It is an internal signing secret, not a code anyone enters. The old `DEMO_ACCESS_CODE`, `DEMO_OWNER_CODE` and `DEMO_SESSION_SECRET` settings are unused and can be removed from existing services.

## Optional live Gemini

The initial deployment works without a model key: sample content and manual entry are available. To demonstrate real poster reading and agent review, add a permitted Gemini API key to Render's environment as `GEMINI_API_KEY` and set `GEMINI_MODEL` to a model your account can access.

- For an AI Studio key, use `GOOGLE_GENAI_USE_VERTEXAI=false` (the Blueprint default).
- For an Agent Platform API key, explicitly use `GOOGLE_GENAI_USE_VERTEXAI=true`. This demo clears cloud project and service-account credentials; it does not use ADC.

Gemini Enterprise access is not required for either API-key path. The hosting free tier does not cover Gemini usage. Automatic pre-review is off: Student Affairs can request it from the review desk. Poster extraction and newsletter generation may still call Gemini when a key is configured. Startup creates the sample preview without any AI calls and labels it as prepared sample output.

## Judge journey

1. Open the HTTPS URL. No login or code is required.
2. In **Club**, use a non-sensitive poster or type a notice. Without a key, complete the form manually.
3. In **Student Affairs**, compare source and copy, run pre-review if Gemini is configured, and approve or return the submission.
4. Preview the edition, try its newsletter layouts, then publish into **Outbox**.
5. Open the saved email and web edition. Ordinary publication creates demonstration messages in the outbox. The optional email test below is a separate, explicit real-email action.

All reviewers share the same demo data. Do not upload confidential documents, real recipient lists or credentials. Prepared items carry `[SAMPLE]` labels. Initial startup creates sample posters, a preview edition and one saved email so judges can explore immediately.

## Optional real email test

Real email is disabled by default. The service owner can enable Gmail previews to one fixed address. Once enabled, anyone using Student Affairs in the public demo can send a preview to that address without a code. Configure these runtime environment values privately on the Render service, then deploy the change:

- `HOSTED_EMAIL_PREVIEW=true`
- `HOSTED_EMAIL_RECIPIENT`: one test email address controlled or approved by the owner. This is the only recipient the API accepts.
- `GMAIL_TOKEN_JSON`: complete server-side Gmail OAuth credentials with the sender's granted send scope. Transfer it privately; never commit or paste it into a page, browser storage or frontend code.

Open **Student Affairs → Send test email**, select the edition and confirm the displayed recipient, then click **Send test email**. No unlock step or timed session is required. Old `/judge/login` and `/judge/owner` links redirect directly to the demo and Student Affairs respectively.

The server fixes the recipient and checks that the request comes from the Student Affairs demo role and the same website. Requests are limited to three send attempts per hour across the demo. This is a process-local cap: a service restart resets it. Delivery receipts use the same disposable demo storage, so disk loss can also remove duplicate-send protection. Confirm the recipient's inbox before retrying after a reset. This test does not subscribe that recipient or change the normal outbox-only publication behavior.

To turn the feature off, set `HOSTED_EMAIL_PREVIEW=false`, remove `GMAIL_TOKEN_JSON`, and deploy. The launcher always removes token-file and cloud service-account settings; it retains Gmail JSON only for an explicitly enabled, valid email-test configuration.

## Free-host limits

- Render Free services can sleep while idle and take time to wake up. Open the URL before a scheduled judging session and keep the local recording as a backup.
- Data is stored under `/tmp/boardcast-demo/data`. There is **no persistent disk** in this Blueprint. Data may disappear when Render spins the service down, restarts or redeploys it. A fresh filesystem recreates the sample demo; it does not recover prior judge uploads.
- If the same filesystem remains available, initialization preserves existing items, decisions and subscriptions. It does not reset the demo on every process start.
- One process and one instance are intentional. The local JSON store is not suitable for multiple workers or replicas.
- Real email and Gmail credentials are disabled by default. The optional email test uses the Gmail HTTPS API; ordinary delivery uses the saved HTML outbox. Render Free's outbound SMTP restrictions do not affect either path.
- Demo pages, posters and newsletter artifacts are public. Use sample content only.

For a durable campus pilot, use the existing Google Cloud plan with verified OAuth roles, Firestore, poster storage, real mail delivery and background job handling. This Render sandbox does not claim those integrations are verified.

## What the hosted launcher changes

`scripts.run_hosted_demo` disables dotenv loading before importing the backend, clears inherited cloud credentials and unapproved Gmail configuration, chooses isolated demo storage, validates the public HTTPS origin, seeds missing demo content and starts one Uvicorn worker. The only Gmail exception is the explicit email-preview JSON credential configuration described above. `Dockerfile.render` copies application files and the synthetic seed script only. It does not include real data or local secrets. The existing local launcher and production Dockerfile are separate.
