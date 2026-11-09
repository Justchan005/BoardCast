FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libzbar0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
COPY agent/requirements.txt agent/requirements.txt
COPY teams/requirements.txt teams/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt -r agent/requirements.txt -r teams/requirements.txt
COPY shared shared
COPY backend backend
COPY templates templates
COPY prompts prompts
COPY static static
COPY agent agent
COPY teams teams
# One image, two Cloud Run services:
#   boardcast-api    APP_MODULE=backend.main:app (default)
#   boardcast-teams  APP_MODULE=teams.bot:app    (infra/deploy_teams.sh)
ENV PORT=8080 APP_MODULE=backend.main:app
CMD exec uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT}
