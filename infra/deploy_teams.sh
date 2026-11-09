#!/usr/bin/env bash
# Deploy the BoardCast Teams bot to Cloud Run. Run from the repo root after infra/setup.sh:
#   TEAMS_BOT_ID=... TEAMS_BOT_SECRET=... TEAMS_TENANT_ID=... bash infra/deploy_teams.sh
# Then set the bot's messaging endpoint (Teams Developer Portal > Bot management) to
#   <printed URL>/api/messages, and run: python -m teams.build_package
set -euo pipefail
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-asia-southeast1}"
: "${TEAMS_BOT_ID:?}" "${TEAMS_BOT_SECRET:?}" "${TEAMS_TENANT_ID:?}"

AGENT_SA="boardcast-agent@$PROJECT_ID.iam.gserviceaccount.com"
API_URL=$(gcloud run services describe boardcast-api --region "$REGION" --format='value(status.url)')

# The bot runs the ADK agents itself, as the agent service account, so the API's
# ID-token check (backend/auth.py) accepts its calls exactly like Agent Engine's.
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$AGENT_SA" \
  --role=roles/aiplatform.user -q >/dev/null

printf '%s' "$TEAMS_BOT_SECRET" | gcloud secrets create teams-bot-secret --data-file=- 2>/dev/null \
  || printf '%s' "$TEAMS_BOT_SECRET" | gcloud secrets versions add teams-bot-secret --data-file=-
gcloud secrets add-iam-policy-binding teams-bot-secret --member="serviceAccount:$AGENT_SA" \
  --role=roles/secretmanager.secretAccessor -q >/dev/null

# max-instances=1: chat sessions are kept in memory (teams/bridge.py).
# no-cpu-throttling: replies are sent after Teams' request returns (long-running messages).
gcloud run deploy boardcast-teams --source . --region "$REGION" \
  --service-account "$AGENT_SA" --allow-unauthenticated \
  --max-instances 1 --min-instances 0 --no-cpu-throttling --timeout 300 \
  --set-env-vars "APP_MODULE=teams.bot:app,GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,BOARDCAST_API_URL=$API_URL,POSTER_BUCKET=$PROJECT_ID-posters,GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.5-flash},TEAMS_BOT_ID=$TEAMS_BOT_ID,TEAMS_TENANT_ID=$TEAMS_TENANT_ID" \
  --set-secrets "TEAMS_BOT_SECRET=teams-bot-secret:latest"

BOT_URL=$(gcloud run services describe boardcast-teams --region "$REGION" --format='value(status.url)')
echo "Messaging endpoint: $BOT_URL/api/messages"
