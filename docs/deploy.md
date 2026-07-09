# Deploy procedure (staging and production)

Same steps for **staging** and **production**; only env file and compose project name differ.

## Files

| Environment | Env file | Compose |
|-------------|----------|---------|
| Staging | `.env.staging` (from `.env.staging.example`) | `docker-compose.production.yml` |
| Production | `.env.production` | `docker-compose.production.yml` |

## Deploy

```bash
git pull origin main

# Staging example:
export COMPOSE_FILE=docker-compose.production.yml
export ENV_FILE=.env.staging

docker compose --env-file "$ENV_FILE" build api worker
docker compose --env-file "$ENV_FILE" run --rm api alembic upgrade head
docker compose --env-file "$ENV_FILE" up -d
```

Production: replace `.env.staging` with `.env.production`.

## Post-deploy smoke

```bash
curl -sf "https://<host>/health"
# Deep probe (internal/VPN only):
curl -sf "http://api:8000/health/deep"
```

Optional demo data on **staging** only:

```bash
docker compose --env-file .env.staging run --rm api \
  python scripts/seed_demo_data.py
```

## Rollback

1. Re-deploy previous git tag (same steps with `git checkout <tag>`).
2. If schema migrated forward, run `alembic downgrade -1` only when the release notes require it.
3. For data corruption, use [runbook_restore.md](runbook_restore.md).

## GitHub Actions (optional)

Mirror the shell steps above in a workflow: build image → `alembic upgrade head` → `compose up -d` on the target host via SSH or a CD runner.
