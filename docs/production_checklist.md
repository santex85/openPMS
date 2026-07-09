# Production checklist

Use this list before pointing real traffic at a new environment. Mark each item when verified on **staging**, then again on **production**.

## Secrets and configuration

- [ ] Run `scripts/generate-secrets.sh` and store `JWT_SECRET` + `WEBHOOK_SECRET_FERNET_KEY` in a secrets manager (not in git).
- [ ] Set `FERNET_KEY` / `WEBHOOK_SECRET_FERNET_KEY` — without it, encrypted Channex API keys and Stripe account IDs in the DB cannot be decrypted after restore.
- [ ] `CORS_ORIGINS` lists only known SPA origins (no `*`).
- [ ] `REFRESH_COOKIE_SECURE=true` (HTTPS only).
- [ ] Decide `ALLOW_PUBLIC_REGISTRATION`: `false` + invite flow for production, or document why open registration is acceptable.
- [ ] `RESEND_API_KEY` and `EMAIL_FROM_DEFAULT` set for transactional email.
- [ ] Optional: `SENTRY_DSN`, `APP_ENV=production`, `APP_RELEASE=<git-sha>`.

## TLS and edge (Caddy)

- [ ] `DOMAIN` and `ACME_EMAIL` set in `.env.production`.
- [ ] Caddy terminates TLS; API is not exposed on a public port (only `expose: 8000` on internal network).
- [ ] Security headers in `deploy/Caddyfile` (example):

```caddyfile
header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
    X-Content-Type-Options nosniff
    X-Frame-Options DENY
    Referrer-Policy strict-origin-when-cross-origin
}
```

- [ ] `/health/deep` is **not** routed through Caddy to the public internet (ops/VPN only).

## Rate limiting

- [ ] `POST /auth/login` — 10/minute per IP+tenant (verified: repeated failures return **429**).
- [ ] `POST /auth/register` — 10/minute.
- [ ] `POST /auth/refresh` — 30/minute.
- [ ] `POST /auth/invite`, `POST /auth/change-password` — 30/minute.
- [ ] `POST /auth/forgot-password` — **N/A** (route not implemented).
- [ ] Channex inbound `POST /webhooks/channex` — default 300/minute (burst-friendly).

## Observability

- [ ] `LOG_FORMAT=json` on api/worker containers.
- [ ] Sentry receives a test error from staging with `environment`, `release`, `tenant_id` tag; no `Authorization` / cookies in payload.
- [ ] External uptime monitor hits `GET /health` (not `/health/deep`).

## Backups

- [ ] `backup` compose service running; daily dump in S3 (03:00 UTC).
- [ ] S3 bucket lifecycle: 30-day retention.
- [ ] Restore runbook exercised — see [runbook_restore.md](runbook_restore.md).

## Smoke after deploy

- [ ] `GET /health` → `{"status":"ok"}`
- [ ] Login → `GET /bookings` with property filter
- [ ] Stripe Connect status (test mode on staging)
