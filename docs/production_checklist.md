# Production checklist

Use this list before pointing real traffic at a new environment. Mark each item when verified on **staging**, then again on **production**.

Legend: **[code]** satisfied by repo defaults/config in this release · **[manual]** ops/infra step · **[policy]** product decision recorded here.

## Secrets and configuration

- [ ] **[manual]** Run `scripts/generate-secrets.sh` and store `JWT_SECRET` + `WEBHOOK_SECRET_FERNET_KEY` in a secrets manager (not in git).
- [ ] **[manual]** Set `FERNET_KEY` / `WEBHOOK_SECRET_FERNET_KEY` — without it, encrypted Channex API keys and Stripe account IDs in the DB cannot be decrypted after restore.
- [ ] **[manual]** `CORS_ORIGINS` lists only known SPA origins (no `*`).
- [ ] **[manual]** `REFRESH_COOKIE_SECURE=true` (HTTPS only; set in `.env.staging.example` / `.env.production.example`).
- [x] **[policy]** `ALLOW_PUBLIC_REGISTRATION=true` for launch (open tenant self-registration). Documented in env examples; set `false` + use `/auth/invite` to lock down later.
- [ ] **[manual]** `RESEND_API_KEY` and `EMAIL_FROM_DEFAULT` set for transactional email.
- [ ] **[manual]** `FRONTEND_BASE_URL` set to the public SPA origin (password-reset links point here).
- [ ] **[manual]** `STRIPE_WEBHOOK_SECRET` set (`whsec_...`). With `REQUIRE_STRIPE_WEBHOOK_SECRET=true` (env examples), empty secret **fails API startup**; without the flag the endpoint returns 503.
- [ ] **[manual]** Optional: `SENTRY_DSN`, `APP_ENV=production`, `APP_RELEASE=<git-sha>`.

## TLS and edge (Caddy)

- [ ] **[manual]** `DOMAIN` and `ACME_EMAIL` set in `.env.production` / `.env.staging`.
- [ ] **[manual]** Caddy terminates TLS; API is not exposed on a public port (only `expose: 8000` on internal network).
- [x] **[code]** Security headers in [`deploy/Caddyfile`](../deploy/Caddyfile): HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`. Local [`Caddyfile`](../Caddyfile) has the same non-HSTS headers on `:443`.
- [x] **[code]** `/health/deep` blocked at public Caddy edge (`respond 403`); probe only from internal network / VPN (`http://api:8000/health/deep`).

## Rate limiting

- [x] **[code]** `POST /auth/login` — 10/minute per IP+tenant (verified: repeated failures return **429**).
- [x] **[code]** `POST /auth/register` — 10/minute.
- [x] **[code]** `POST /auth/refresh` — 30/minute.
- [x] **[code]** `POST /auth/invite`, `POST /auth/change-password` — 30/minute.
- [x] **[code]** `POST /auth/forgot-password` — 5/minute; `POST /auth/reset-password` — 10/minute.
- [x] **[code]** Channex inbound `POST /webhooks/channex` — default 300/minute (burst-friendly).
- [x] **[code]** Stripe inbound `POST /webhooks/stripe` — default 300/minute (burst-friendly).

## Stripe inbound webhook

- [ ] **[manual]** In Stripe Dashboard → Developers → Webhooks, add endpoint `https://<api-domain>/webhooks/stripe`.
- [ ] **[manual]** Subscribe to events: `charge.refunded`, `charge.dispute.created`.
- [ ] **[manual]** Copy the signing secret (`whsec_...`) into `STRIPE_WEBHOOK_SECRET`.
- [ ] **[manual]** Verify a test event is accepted (200) and a bad signature is rejected (400).

## Observability

- [ ] **[manual]** `LOG_FORMAT=json` on api/worker containers.
- [ ] **[manual]** Sentry receives a test error from staging with `environment`, `release`, `tenant_id` tag; no `Authorization` / cookies in payload.
- [ ] **[manual]** External uptime monitor hits `GET /health` (not `/health/deep`). Example: UptimeRobot / Better Stack / Pingdom → `https://<DOMAIN>/health` expecting HTTP 200 and body containing `"status":"ok"`.

## Backups

- [ ] **[manual]** `backup` compose service running; daily dump in S3 (03:00 UTC).
- [ ] **[manual]** S3 bucket lifecycle: 30-day retention.
- [ ] **[manual]** Restore runbook exercised — see [runbook_restore.md](runbook_restore.md).

## Smoke after deploy

- [ ] **[manual]** `GET /health` → `{"status":"ok"}`
- [ ] **[manual]** Login → `GET /bookings` with property filter
- [ ] **[manual]** Stripe Connect status (test mode on staging)
