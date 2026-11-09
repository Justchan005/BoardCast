#!/usr/bin/env bash
# Deploy the ADK agents to Agent Engine. Run from the repo root:  bash infra/deploy_agent.sh
set -euo pipefail
: "${GOOGLE_CLOUD_PROJECT:?}" "${BOARDCAST_API_URL:?}"
REGION="${GOOGLE_CLOUD_LOCATION:-asia-southeast1}"
AGENT_SA="boardcast-agent@$GOOGLE_CLOUD_PROJECT.iam.gserviceaccount.com"

# Agent Engine packages only the agent folder, so ship a copy of the prompt files with it.
mkdir -p agent/boardcast_agent/prompts
cp prompts/*.md agent/boardcast_agent/prompts/
# Current ADK packages dependencies from the deployed agent folder.
cp agent/requirements.txt agent/boardcast_agent/requirements.txt

# setup.sh grants model access and poster uploads to this account. The deployer
# must have iam.serviceAccounts.actAs (Service Account User) on it.
gcloud iam service-accounts describe "$AGENT_SA" --project "$GOOGLE_CLOUD_PROJECT" >/dev/null

cat > agent/boardcast_agent/.env <<ENV
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT
GOOGLE_CLOUD_LOCATION=$REGION
BOARDCAST_API_URL=$BOARDCAST_API_URL
POSTER_BUCKET=$GOOGLE_CLOUD_PROJECT-posters
GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.5-flash}
DEV_MODE=false
LOCAL_MODE=false
TEAMS_LOCAL=false
ENV

# The same explicit runtime identity is allow-listed by backend/auth.py.
cd agent
adk deploy agent_engine --project "$GOOGLE_CLOUD_PROJECT" --region "$REGION" \
  --service_account "$AGENT_SA" \
  --staging_bucket "gs://$GOOGLE_CLOUD_PROJECT-posters" boardcast_agent

echo "Next: Gemini Enterprise console > Agents > add this Agent Engine agent,"
echo "share it only with the Club reps and Student Affairs groups,"
echo "Runtime identity: $AGENT_SA (the API checks this account)."
echo "Verify signed-in user identity, attachment selection and role denial on the real chat surface."
