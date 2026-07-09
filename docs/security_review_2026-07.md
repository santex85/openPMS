# Security review — July 2026 (TZ-20 / F)

## RLS and tenant isolation

- Core tables use PostgreSQL RLS with `app.tenant_id` session variable.
- Extended negative tests: `tests/test_tenant_isolation_new_tables.py` covers `folio_charge_categories`, `email_settings`, `email_logs`, `stripe_connections`, `stripe_charges`, `channex_property_links`.
- `booking_notes` is a column on `bookings`, not a separate table — covered by existing booking isolation tests.
- Legacy `mt_rls_smoke` table was removed after initial RLS validation migration.

## Stripe

- **Inbound webhooks:** implemented (`POST /webhooks/stripe`). Signature verified via `stripe.Webhook.construct_event` against `STRIPE_WEBHOOK_SECRET`; invalid/missing signature returns 400, unconfigured secret returns 503.
  - Handled events: `charge.refunded` (reconcile `stripe_charges` status + negative folio line, idempotent on redelivery) and `charge.dispute.created` (audit + Sentry alert; status unchanged).
  - Tenant is resolved via `SECURITY DEFINER` function `lookup_stripe_charge_for_webhook(pi_id)` (the endpoint is unauthenticated; the connected-account id in `stripe_connections` is encrypted and not queryable).
  - Unknown PaymentIntents / other event types return 200 (no Stripe retries).
- **Charges:** `stripe_charges` rows are tenant-scoped; API lists charges only for bookings visible under RLS (`GET /bookings/{id}/stripe/charges`).
- Stripe account IDs encrypted at rest via Fernet (`app/core/stripe_secrets.py`).

## Password reset

- `POST /auth/forgot-password` (5/min) always returns 204 (no user enumeration); `POST /auth/reset-password` (10/min) consumes the token.
- Reset token is a short-lived JWT (`typ=password_reset`, `exp=1h`) carrying `tenant_id` and `pwd_fp` — a 12-hex-char digest of the current `password_hash`.
- **Design note (deviation from TZ-20 "store token hash"):** no DB table is used. Single-use is enforced by `pwd_fp`: changing the password rotates the hash so the token stops verifying. Expiry is enforced by `exp`. This avoids a migration and a cross-tenant token-lookup `SECURITY DEFINER` function on an unauthenticated endpoint (the tenant is already inside the signed JWT).
- On success all refresh tokens for the user are revoked and a `user.reset_password` audit row is written.

## Channex inbound

- `POST /webhooks/channex`: optional HMAC-SHA256 (`CHANNEX_WEBHOOK_SECRET` + `X-Channex-Signature`).
- Fallback: IP allowlist `34.76.12.0/24` when secret unset and `CHANNEX_WEBHOOK_VERIFY_CHANNEX_IPS=true`.
- **Replay / duplicate revisions:** `channex_booking_revisions` uses `ON CONFLICT DO NOTHING` on `channex_revision_id`; skips rows already `done` or `processing`.

## Auth hardening

- Stricter SlowAPI limits on `/auth/login`, `/auth/register`, `/auth/refresh`, `/auth/invite`, `/auth/change-password`.
- `temporary_password` removed from `POST /auth/invite` response (password sent only via Resend email).
- Password-reset flow added (see "Password reset" above).

## Dependency audit (CI)

- `pip-audit -r requirements.txt` in GitHub Actions job `security`.
- **Frontend:** `npm audit --omit=dev` must run in the SPA repository — see [tz20_frontend_sentry.md](tz20_frontend_sentry.md).

## Findings / follow-ups

| ID | Severity | Item | Status |
|----|----------|------|--------|
| F-1 | Info | Stripe inbound webhook (`charge.refunded`, `charge.dispute.created`) | Implemented (TZ-20 G12) |
| F-2 | Low | `/health/deep` must stay off public edge | Documented in production checklist |
| F-3 | Info | npm audit in separate frontend repo | Tracked in tz20_frontend_sentry.md |
