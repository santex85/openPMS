#!/usr/bin/env bash
# Dump PostgreSQL to a timestamped file and upload to S3-compatible storage (AWS S3 or DigitalOcean Spaces).
#
# Required env:
#   DATABASE_URL  — sync URL for pg_dump, e.g. postgresql://user:pass@host:5432/dbname
#   S3_BUCKET
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
# Optional:
#   AWS_ENDPOINT_URL — for non-AWS S3 (e.g. Spaces)
#   AWS_DEFAULT_REGION
#   BACKUP_RETAIN_DAYS — local pruning only if you also keep files on disk (not implemented here)
#
# Cron example (host with docker):
#   0 3 * * * docker compose -f docker-compose.production.yml exec -T db \
#     sh -c 'pg_dump -U openpms -Fc openpms' | aws s3 cp - "s3://$S3_BUCKET/pg/dump-$(date -u +%Y%m%dT%H%M%SZ).dump"
#
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]] || [[ -z "${S3_BUCKET:-}" ]]; then
  echo "DATABASE_URL and S3_BUCKET are required." >&2
  exit 1
fi

# pg_dump expects postgresql:// not +asyncpg
SYNC_URL="${DATABASE_URL//+asyncpg/}"
STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
KEY="pg/openpms-${STAMP}.dump"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

pg_dump "$SYNC_URL" -Fc -f "$TMP"

aws s3 cp "$TMP" "s3://${S3_BUCKET}/${KEY}" ${AWS_ENDPOINT_URL:+--endpoint-url "$AWS_ENDPOINT_URL"}

echo "Uploaded s3://${S3_BUCKET}/${KEY}"
