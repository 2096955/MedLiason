#!/usr/bin/env bash
# deploy-langfuse.sh — Deploy self-hosted Langfuse on GCP Cloud Run
#
# Architecture (all internal, same VPC):
#   langfuse-clickhouse  — ClickHouse analytics (Cloud Run, internal only)
#   langfuse-db          — Cloud SQL Postgres 17 (metadata)
#   langfuse-redis       — existing MedExpert Redis (shared, separate DB)
#   langfuse-web         — Langfuse UI + API + OTel endpoint (Cloud Run)
#   langfuse-worker      — Background event processing (Cloud Run)
#   GCS bucket           — Media/event blob storage (replaces MinIO)
#
# The script is idempotent — safe to re-run.
#
# Usage:
#   export PROJECT_ID=gbg-neuro
#   export REGION=us-central1
#   bash medexpert/infra/langfuse/deploy-langfuse.sh
#
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-gbg-neuro}"
REGION="${REGION:-us-central1}"
ACCOUNT="${GCLOUD_ACCOUNT:-anthony.lui@cognizant.com}"

# ── Secrets (generate once, reuse on re-runs) ──────────────────────────────
SALT="${LANGFUSE_SALT:-$(openssl rand -hex 16)}"
ENCRYPTION_KEY="${LANGFUSE_ENCRYPTION_KEY:-$(openssl rand -hex 32)}"
NEXTAUTH_SECRET="${LANGFUSE_NEXTAUTH_SECRET:-$(openssl rand -hex 32)}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-$(openssl rand -hex 16)}"

# Pre-configured project keys (auto-provisioned on first start)
INIT_PROJECT_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY:-pk-lf-medexpert-$(openssl rand -hex 8)}"
INIT_PROJECT_SECRET_KEY="${LANGFUSE_SECRET_KEY:-sk-lf-medexpert-$(openssl rand -hex 8)}"

GCS_BUCKET="${PROJECT_ID}-langfuse"

echo "╔══════════════════════════════════════════════╗"
echo "║  Deploying Langfuse to GCP Cloud Run         ║"
echo "║  Project: ${PROJECT_ID}                       "
echo "║  Region:  ${REGION}                           "
echo "╚══════════════════════════════════════════════╝"

# ── Step 1: GCS Bucket ────────────────────────────────────────────────────
echo "[1/5] Creating GCS bucket ${GCS_BUCKET}..."
gcloud storage buckets describe "gs://${GCS_BUCKET}" --project "${PROJECT_ID}" 2>/dev/null \
  || gcloud storage buckets create "gs://${GCS_BUCKET}" \
       --project "${PROJECT_ID}" \
       --location "${REGION}" \
       --uniform-bucket-level-access

# ── Step 2: Cloud SQL Postgres ────────────────────────────────────────────
echo "[2/5] Creating Cloud SQL Postgres instance (langfuse-db)..."
if ! gcloud sql instances describe langfuse-db --project "${PROJECT_ID}" 2>/dev/null; then
  gcloud sql instances create langfuse-db \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --database-version POSTGRES_17 \
    --tier db-f1-micro \
    --storage-size 10GB \
    --storage-auto-increase \
    --database-flags max_connections=100 \
    --no-assign-ip \
    --network default

  gcloud sql databases create langfuse \
    --instance langfuse-db \
    --project "${PROJECT_ID}"

  gcloud sql users set-password postgres \
    --instance langfuse-db \
    --project "${PROJECT_ID}" \
    --password "${CLICKHOUSE_PASSWORD}"
fi

CLOUD_SQL_CONNECTION="${PROJECT_ID}:${REGION}:langfuse-db"
DATABASE_URL="postgresql://postgres:${CLICKHOUSE_PASSWORD}@localhost:5432/langfuse"

# ── Step 3: ClickHouse on Cloud Run (internal) ───────────────────────────
echo "[3/5] Deploying ClickHouse..."
gcloud run deploy langfuse-clickhouse \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image docker.io/clickhouse/clickhouse-server:latest \
  --port 8123 \
  --cpu 2 --memory 4Gi \
  --min-instances 1 --max-instances 1 \
  --no-allow-unauthenticated \
  --ingress internal \
  --set-env-vars "CLICKHOUSE_USER=clickhouse,CLICKHOUSE_PASSWORD=${CLICKHOUSE_PASSWORD}" \
  --account "${ACCOUNT}" \
  --quiet

CLICKHOUSE_URL=$(gcloud run services describe langfuse-clickhouse \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --format 'value(status.url)' --account "${ACCOUNT}")

echo "  ClickHouse URL: ${CLICKHOUSE_URL}"

# ── Step 4: Langfuse Worker (internal) ───────────────────────────────────
echo "[4/5] Deploying Langfuse Worker..."

# Common env vars shared by web + worker
LANGFUSE_ENV="DATABASE_URL=${DATABASE_URL}"
LANGFUSE_ENV="${LANGFUSE_ENV},SALT=${SALT}"
LANGFUSE_ENV="${LANGFUSE_ENV},ENCRYPTION_KEY=${ENCRYPTION_KEY}"
LANGFUSE_ENV="${LANGFUSE_ENV},CLICKHOUSE_URL=${CLICKHOUSE_URL}"
LANGFUSE_ENV="${LANGFUSE_ENV},CLICKHOUSE_USER=clickhouse"
LANGFUSE_ENV="${LANGFUSE_ENV},CLICKHOUSE_PASSWORD=${CLICKHOUSE_PASSWORD}"
LANGFUSE_ENV="${LANGFUSE_ENV},CLICKHOUSE_MIGRATION_URL=clickhouse://clickhouse:${CLICKHOUSE_PASSWORD}@${CLICKHOUSE_URL#https://}:9000"
LANGFUSE_ENV="${LANGFUSE_ENV},REDIS_HOST=${REDIS_HOST:-localhost}"
LANGFUSE_ENV="${LANGFUSE_ENV},REDIS_PORT=${REDIS_PORT:-6379}"
LANGFUSE_ENV="${LANGFUSE_ENV},LANGFUSE_S3_EVENT_UPLOAD_BUCKET=${GCS_BUCKET}"
LANGFUSE_ENV="${LANGFUSE_ENV},LANGFUSE_S3_MEDIA_UPLOAD_BUCKET=${GCS_BUCKET}"
LANGFUSE_ENV="${LANGFUSE_ENV},TELEMETRY_ENABLED=false"

gcloud run deploy langfuse-worker \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image docker.io/langfuse/langfuse-worker:3 \
  --port 3030 \
  --cpu 1 --memory 1Gi \
  --min-instances 1 --max-instances 2 \
  --no-allow-unauthenticated \
  --ingress internal \
  --add-cloudsql-instances "${CLOUD_SQL_CONNECTION}" \
  --set-env-vars "${LANGFUSE_ENV}" \
  --account "${ACCOUNT}" \
  --quiet

# ── Step 5: Langfuse Web (public) ────────────────────────────────────────
echo "[5/5] Deploying Langfuse Web..."

LANGFUSE_WEB_URL="https://langfuse-web-$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)').${REGION}.run.app"

gcloud run deploy langfuse-web \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image docker.io/langfuse/langfuse:3 \
  --port 3000 \
  --cpu 1 --memory 1Gi \
  --min-instances 1 --max-instances 3 \
  --allow-unauthenticated \
  --add-cloudsql-instances "${CLOUD_SQL_CONNECTION}" \
  --set-env-vars "${LANGFUSE_ENV},NEXTAUTH_SECRET=${NEXTAUTH_SECRET},NEXTAUTH_URL=${LANGFUSE_WEB_URL},LANGFUSE_INIT_ORG_NAME=MedExpert,LANGFUSE_INIT_PROJECT_NAME=medexpert,LANGFUSE_INIT_PROJECT_PUBLIC_KEY=${INIT_PROJECT_PUBLIC_KEY},LANGFUSE_INIT_PROJECT_SECRET_KEY=${INIT_PROJECT_SECRET_KEY},LANGFUSE_INIT_USER_EMAIL=admin@medexpert.local,LANGFUSE_INIT_USER_NAME=Admin,LANGFUSE_INIT_USER_PASSWORD=changeme123" \
  --account "${ACCOUNT}" \
  --quiet

LANGFUSE_WEB_URL=$(gcloud run services describe langfuse-web \
  --project "${PROJECT_ID}" --region "${REGION}" \
  --format 'value(status.url)' --account "${ACCOUNT}")

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Langfuse deployed successfully!                        ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║  UI:         ${LANGFUSE_WEB_URL}"
echo "║  OTel endpoint: ${LANGFUSE_WEB_URL}/api/public/otel/v1/traces"
echo "║                                                         ║"
echo "║  Login:      admin@medexpert.local / changeme123        ║"
echo "║                                                         ║"
echo "║  Project keys (add to MedExpert .env):                  ║"
echo "║  LANGFUSE_PUBLIC_KEY=${INIT_PROJECT_PUBLIC_KEY}"
echo "║  LANGFUSE_SECRET_KEY=${INIT_PROJECT_SECRET_KEY}"
echo "║  LANGFUSE_BASE_URL=${LANGFUSE_WEB_URL}"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "To wire MedExpert → Langfuse, add these env vars to medexpert-v3:"
echo ""
echo "  gcloud run services update medexpert-v3 \\"
echo "    --set-env-vars LANGFUSE_SECRET_KEY=${INIT_PROJECT_SECRET_KEY},LANGFUSE_PUBLIC_KEY=${INIT_PROJECT_PUBLIC_KEY},LANGFUSE_BASE_URL=${LANGFUSE_WEB_URL} \\"
echo "    --region ${REGION} --project ${PROJECT_ID} --account ${ACCOUNT}"
