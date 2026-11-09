#!/usr/bin/env bash
# One-time Google Cloud setup for BoardCast. Run from the repo root:  bash infra/setup.sh
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-asia-southeast1}"
gcloud config set project "$PROJECT_ID"

echo "== Enable APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com firestore.googleapis.com \
  pubsub.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com \
  bigquery.googleapis.com storage.googleapis.com aiplatform.googleapis.com gmail.googleapis.com

echo "== Service accounts"
gcloud iam service-accounts create boardcast-api --display-name="BoardCast API" || true
gcloud iam service-accounts create boardcast-agent --display-name="BoardCast ADK agent" || true
gcloud iam service-accounts create boardcast-scheduler --display-name="BoardCast scheduler" || true
API_SA="boardcast-api@$PROJECT_ID.iam.gserviceaccount.com"
AGENT_SA="boardcast-agent@$PROJECT_ID.iam.gserviceaccount.com"
for role in roles/datastore.user roles/storage.objectAdmin roles/pubsub.publisher \
            roles/bigquery.dataEditor roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$API_SA" --role="$role" -q >/dev/null
done
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$AGENT_SA" \
  --role=roles/aiplatform.user -q >/dev/null

echo "== Firestore, bucket, Pub/Sub, BigQuery"
gcloud firestore databases create --location="$REGION" || true
gcloud storage buckets create "gs://$PROJECT_ID-posters" --location="$REGION" || true
gcloud storage buckets add-iam-policy-binding "gs://$PROJECT_ID-posters" \
  --member="serviceAccount:$AGENT_SA" --role=roles/storage.objectCreator -q >/dev/null
gcloud pubsub topics create item-submitted || true
bq --location="$REGION" mk -d boardcast || true
bq mk -t boardcast.audit_log ts:TIMESTAMP,actor:STRING,action:STRING,item_id:STRING,detail:STRING || true

echo "== Prepare secrets before deployment"
for secret_name in gmail-token link-secret; do
  if ! gcloud secrets describe "$secret_name" >/dev/null 2>&1; then
    gcloud secrets create "$secret_name" --replication-policy=automatic
  fi
  gcloud secrets add-iam-policy-binding "$secret_name" --member="serviceAccount:$API_SA" \
    --role=roles/secretmanager.secretAccessor -q >/dev/null
done

# Creating a secret without a version is insufficient: Cloud Run cannot start
# with gmail-token:latest until an enabled version exists. Do not print tokens.
GMAIL_VERSION=$(gcloud secrets versions list gmail-token --filter='state=ENABLED' \
  --sort-by='~createTime' --limit=1 --format='value(name)')
if [[ -z "$GMAIL_VERSION" ]]; then
  GMAIL_TOKEN_FILE="${GMAIL_TOKEN_FILE:-token.json}"
  if [[ ! -f "$GMAIL_TOKEN_FILE" ]]; then
    echo "Create a Gmail token with python -m scripts.get_gmail_token, then rerun with GMAIL_TOKEN_FILE=/path/to/token.json." >&2
    exit 1
  fi
  gcloud secrets versions add gmail-token --data-file="$GMAIL_TOKEN_FILE" >/dev/null
  GMAIL_VERSION=$(gcloud secrets versions list gmail-token --filter='state=ENABLED' \
    --sort-by='~createTime' --limit=1 --format='value(name)')
fi
LINK_VERSION=$(gcloud secrets versions list link-secret --filter='state=ENABLED' \
  --sort-by='~createTime' --limit=1 --format='value(name)')
if [[ -z "$LINK_VERSION" ]]; then
  python3 -c "import secrets; print(secrets.token_urlsafe(48), end='')" | \
    gcloud secrets versions add link-secret --data-file=- >/dev/null
  LINK_VERSION=$(gcloud secrets versions list link-secret --filter='state=ENABLED' \
    --sort-by='~createTime' --limit=1 --format='value(name)')
fi
# Reuse existing signing secrets so rerunning setup does not invalidate links.
: "${GMAIL_VERSION:?No enabled Gmail token version}" "${LINK_VERSION:?No enabled signing secret version}"
GMAIL_VERSION="${GMAIL_VERSION##*/}"
LINK_VERSION="${LINK_VERSION##*/}"

echo "== Deploy API to Cloud Run"
gcloud run deploy boardcast-api --source . --region "$REGION" \
  --service-account "$API_SA" --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,POSTER_BUCKET=$PROJECT_ID-posters,BQ_AUDIT_TABLE=$PROJECT_ID.boardcast.audit_log,AGENT_SERVICE_ACCOUNT=boardcast-agent@$PROJECT_ID.iam.gserviceaccount.com,SCHEDULER_SERVICE_ACCOUNT=boardcast-scheduler@$PROJECT_ID.iam.gserviceaccount.com" \
  --set-secrets "GMAIL_TOKEN_JSON=gmail-token:$GMAIL_VERSION,LINK_SECRET=link-secret:$LINK_VERSION"
# After creating an OAuth web client id for /admin sign-in (console > APIs & Services > Credentials),
# add it with: gcloud run services update boardcast-api --region "$REGION" --update-env-vars OAUTH_CLIENT_ID=...
# Public routes (/cal, /r, /subscribe) need --allow-unauthenticated; protected routes check
# a Google-signed ID token from the agent / scheduler service account in backend/auth.py.

API_URL=$(gcloud run services describe boardcast-api --region "$REGION" --format='value(status.url)')
gcloud run services update boardcast-api --region "$REGION" \
  --update-env-vars "PUBLIC_BASE_URL=$API_URL,API_AUDIENCE=$API_URL"

echo "== Scheduler: Sunday preview, Monday send, delivery retries and reminders (Malaysia time)"
SCHED_SA="boardcast-scheduler@$PROJECT_ID.iam.gserviceaccount.com"
gcloud scheduler jobs create http boardcast-preview --location "$REGION" \
  --schedule "0 18 * * 0" --time-zone "Asia/Kuala_Lumpur" \
  --uri "$API_URL/issues/preview" --http-method POST \
  --oidc-service-account-email "$SCHED_SA" --oidc-token-audience "$API_URL" || true
gcloud scheduler jobs create http boardcast-send --location "$REGION" \
  --schedule "0 8 * * 1" --time-zone "Asia/Kuala_Lumpur" \
  --uri "$API_URL/issues/send" --http-method POST \
  --oidc-service-account-email "$SCHED_SA" --oidc-token-audience "$API_URL" || true
# Recovery only: this endpoint resumes editions whose first send has started;
# it must never start delivery of a fresh Sunday preview before Monday.
gcloud scheduler jobs create http boardcast-send-retry --location "$REGION" \
  --schedule "*/10 * * * *" --time-zone "Asia/Kuala_Lumpur" \
  --uri "$API_URL/issues/retry" --http-method POST \
  --oidc-service-account-email "$SCHED_SA" --oidc-token-audience "$API_URL" || true
gcloud scheduler jobs create http boardcast-reminders --location "$REGION" \
  --schedule "0 * * * *" --time-zone "Asia/Kuala_Lumpur" \
  --uri "$API_URL/reminders/run" --http-method POST \
  --oidc-service-account-email "$SCHED_SA" --oidc-token-audience "$API_URL" || true

echo "Done. API: $API_URL"
