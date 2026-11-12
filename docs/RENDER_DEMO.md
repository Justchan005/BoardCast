# BoardCast judge demo on Render

This deployment gives judges a browser link and one access code. They can switch between Club, Student Affairs and Outbox. It is a shared sandbox with labelled sample content, not the campus production service.

## Deploy the prepared version

1. Push the reviewed files, including `render.yaml` and `Dockerfile.render`, to the repository branch you want Render to use. Do not commit `.env`, Gmail tokens or personal demo data.
2. In Render, create a **Blueprint**, connect that GitHub repository, and select the reviewed branch. The Blueprint creates one **Free** Docker web service in **Singapore**, with automatic deployment off and `/healthz` for health checks. It creates no paid disk or database.
3. Let the build and health check finish. Render supplies `RENDER_EXTERNAL_URL`; the launcher uses it for poster, newsletter and calendar URLs. If using a custom domain, set `PUBLIC_BASE_URL` to its HTTPS origin and redeploy.
4. In the service's **Environment** settings, retrieve the generated `DEMO_ACCESS_CODE`. Give judges the Render URL and this code privately. Keep `DEMO_SESSION_SECRET` and `LINK_SECRET` private; judges do not need them. Do not paste any secret into a GitHub issue or public submission field.
5. Verify the judge journey below before submitting the link. After changes, deploy manually from the reviewed commit.

The Blueprint generates three random secrets. Startup requires an access code of at least 16 characters, a session secret of at least 32 characters and a signing secret of at least 32 characters. Missing or invalid settings stop startup rather than opening an unprotected demo.

## Optional live Gemini

The initial deployment works without a model key: sample content and manual entry are available. To demonstrate real poster reading and agent review, add a permitted Gemini API key to Render's environment as `GEMINI_API_KEY` and set `GEMINI_MODEL` to a model your account can access.

- For an AI Studio key, use `GOOGLE_GENAI_USE_VERTEXAI=false` (the Blueprint default).
- For an Agent Platform API key, explicitly use `GOOGLE_GENAI_USE_VERTEXAI=true`. This demo clears cloud project and service-account credentials; it does not use ADC.

Gemini Enterprise access is not required for either API-key path. The hosting free tier does not cover Gemini usage. Automatic pre-review is off: Student Affairs can request it from the review desk. Poster extraction and newsletter generation may still call Gemini when a key is configured. Startup creates the sample preview without any AI calls and labels it as prepared sample output.

## Judge journey

1. Open the HTTPS URL and enter the access code.
2. In **Club**, use a non-sensitive poster or type a notice. Without a key, complete the form manually.
3. In **Student Affairs**, compare source and copy, run pre-review if Gemini is configured, and approve or return the submission.
4. Preview the edition, try its newsletter layouts, then publish into **Outbox**.
5. Open the saved email and web edition. These are demonstration messages, not delivered emails.

All reviewers share the same demo data. Do not upload confidential documents, real recipient lists or credentials. Prepared items carry `[SAMPLE]` labels. Initial startup creates sample posters, a preview edition and one saved email so judges can explore immediately.

## Free-host limits

- Render Free services can sleep while idle and take time to wake up. Open the URL before a scheduled judging session and keep the local recording as a backup.
- Data is stored under `/tmp/boardcast-demo/data`. There is **no persistent disk** in this Blueprint. Data may disappear when Render spins the service down, restarts or redeploys it. A fresh filesystem recreates the sample demo; it does not recover prior judge uploads.
- If the same filesystem remains available, initialization preserves existing items, decisions and subscriptions. It does not reset the demo on every process start.
- One process and one instance are intentional. The local JSON store is not suitable for multiple workers or replicas.
- Real email sending and Gmail credentials are disabled in this hosted sandbox. Render Free also restricts outbound SMTP ports; this app uses its saved HTML outbox, so the demo does not require SMTP.
- The site and its artifacts require the judge access code. Newsletter links are not an unrestricted public publication.

For a durable campus pilot, use the existing Google Cloud plan with verified OAuth roles, Firestore, poster storage, real mail delivery and background job handling. This Render sandbox does not claim those integrations are verified.

## What the hosted launcher changes

`scripts.run_hosted_demo` disables dotenv loading before importing the backend, clears inherited Gmail/cloud credentials, chooses isolated demo storage, validates the public HTTPS origin, seeds missing demo content and starts one Uvicorn worker. `Dockerfile.render` copies application files and the synthetic seed script only. It does not include real data or local secrets. The existing local launcher and production Dockerfile are separate.
