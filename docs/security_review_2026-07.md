# Security review — July 2026 (TZ-20 / F)

## RLS and tenant isolation

- Core tables use PostgreSQL RLS with `app.tenant_id` session variable.
- Extended negative tests: `tests/test_tenant_isolation_new_tables.py` covers `folio_charge_categories`, `email_settings`, `email_logs`, `stripe_connections`, `stripe_charges`, `channex_property_links`.
- `booking_notes` is a column on `bookings`, not a separate table — covered by existing booking isolation tests.
- Legacy `mt_rls_smoke` table was removed after initial RLS validation migration.

## Stripe

- **Inbound webhooks:** not implemented in this repository (Connect OAuth + payment intents only). Webhook signature verification is **N/A** until an inbound endpoint is added.
- **Charges:** `stripe_charges` rows are tenant-scoped; API lists charges only for bookings visible under RLS (`GET /bookings/{id}/stripe/charges`).
- Stripe account IDs encrypted at rest via Fernet (`app/core/stripe_secrets.py`).

## Channex inbound

- `POST /webhooks/channex`: optional HMAC-SHA256 (`CHANNEX_WEBHOOK_SECRET` + `X-Channex-Signature`).
- Fallback: IP allowlist `34.76.12.0/24` when secret unset and `CHANNEX_WEBHOOK_VERIFY_CHANNEX_IPS=true`.
- **Replay / duplicate revisions:** `channex_booking_revisions` uses `ON CONFLICT DO NOTHING` on `channex_revision_id`; skips rows already `done` or `processing`.

## Auth hardening

- Stricter SlowAPI limits on `/auth/login`, `/auth/register`, `/auth/refresh`, `/auth/invite`, `/auth/change-password`.
- `temporary_password` removed from `POST /auth/invite` response (password sent only via Resend email).

## Dependency audit (CI)

- `pip-audit -r requirements.txt` in GitHub Actions job `security`.
- **Frontend:** `npm audit --omit=dev` must run in the SPA repository — see [tz20_frontend_sentry.md](tz20_frontend_sentry.md).

## Findings / follow-ups

| ID | Severity | Item | Status |
|----|----------|------|--------|
| F-1 | Info | No Stripe inbound webhook endpoint | Deferred |
| F-2 | Low | `/health/deep` must stay off public edge | Documented in production checklist |
| F-3 | Info | npm audit in separate frontend repo | Tracked in tz20_frontend_sentry.md |
