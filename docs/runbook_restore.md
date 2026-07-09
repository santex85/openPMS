# Runbook: PostgreSQL restore

## Scope

Restore OpenPMS from an S3 `pg_dump` custom-format backup created by `scripts/backup_pg_to_s3.sh`.

## Secrets **not** in the backup

Store these in your secrets manager **before** any restore drill. They are required for a working system after `pg_restore`:

| Secret | Why |
|--------|-----|
| `JWT_SECRET` | User sessions and HS256 tokens |
| `WEBHOOK_SECRET_FERNET_KEY` | Decrypt webhook subscription secrets, Channex API keys, Stripe account IDs at rest |
| `STRIPE_SECRET_KEY`, `STRIPE_CLIENT_ID` | Payments (if used) |
| `RESEND_API_KEY` | Outbound email |
| `CHANNEX_WEBHOOK_SECRET` | Inbound webhook HMAC (if configured) |

## Prerequisites

- Clean host with Docker Compose and this repository.
- `.env.production` with secrets above + `DATABASE_URL` pointing at the new DB.
- AWS credentials with read access to `S3_BUCKET`.
- Backup object key, e.g. `openpms/backup-20260709-030001.dump`.

## Steps

1. **Stop writers** (api, worker, beat) to avoid concurrent writes during restore.

   ```bash
   docker compose -f docker-compose.production.yml stop api worker
   ```

2. **Download backup**

   ```bash
   aws s3 cp "s3://${S3_BUCKET}/openpms/backup-YYYYMMDD-HHMMSS.dump" /tmp/restore.dump
   ```

3. **Restore** (into empty or replacement database; `--clean` drops existing objects)

   ```bash
   docker compose -f docker-compose.production.yml exec -T db \
     pg_restore -U openpms -d openpms --clean --if-exists < /tmp/restore.dump
   ```

   Or from host with `pg_restore` if client tools are installed.

4. **Migration check** (should be no-op if backup was from same schema version)

   ```bash
   docker compose -f docker-compose.production.yml run --rm api alembic upgrade head
   ```

5. **Start stack**

   ```bash
   docker compose -f docker-compose.production.yml up -d
   ```

6. **Smoke**

   - `curl -s https://<domain>/health`
   - Login with a known user JWT flow
   - `GET /bookings?property_id=...` for a property with data

## Restore test log

| Field | Value |
|-------|-------|
| Date (UTC) | _fill after staging drill_ |
| Backup object | _s3 key_ |
| RTO (wall clock) | _minutes_ |
| Operator | _name_ |
| Notes | _issues found_ |

## Retention

Daily backups at **03:00 UTC**. Configure S3 lifecycle to expire objects after **30 days** (`BACKUP_RETAIN_DAYS` documents intent; enforcement is on the bucket policy).
