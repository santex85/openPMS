# Security review — July 2026 (TZ-20 / F)

## RLS and tenant isolation

- Core tables use PostgreSQL RLS with `app.tenant_id` session variable.
- Extended negative tests: `tests/test_tenant_isolation_new_tables.py` covers `folio_charge_categories`, `email_settings`, `email_logs`, `stripe_connections`, `stripe_charges`, `channex_property_links`, and **booking notes** (column on `bookings`).
- `booking_notes` is a column on `bookings`, not a separate table — cross-tenant GET/PATCH return **404** (`test_tenant_b_cannot_get_tenant_a_booking_with_notes`, `test_tenant_b_cannot_patch_tenant_a_booking_notes`).
- Legacy `mt_rls_smoke` table was removed after initial RLS validation migration.

## Stripe

- **Inbound webhooks:** implemented (`POST /webhooks/stripe`). Signature verified via `stripe.Webhook.construct_event` against `STRIPE_WEBHOOK_SECRET`; invalid/missing signature returns 400, unconfigured secret returns 503.
  - Handled events: `charge.refunded` (reconcile `stripe_charges` status + negative folio line, idempotent on redelivery) and `charge.dispute.created` (audit + Sentry alert; status unchanged).
  - Tenant is resolved via `SECURITY DEFINER` function `lookup_stripe_charge_for_webhook(pi_id)` (the endpoint is unauthenticated; the connected-account id in `stripe_connections` is encrypted and not queryable).
  - Unknown PaymentIntents / other event types return 200 (no Stripe retries).
- **Charges:** `stripe_charges` rows are tenant-scoped; API lists charges only for bookings visible under RLS (`GET /bookings/{id}/stripe/charges`).
  - Standard Connect: `PaymentIntent.create` uses `stripe_account` only (no `on_behalf_of` — Stripe rejects `on_behalf_of` when set to the same Standard account).
- Stripe account IDs encrypted at rest via Fernet (`app/core/stripe_secrets.py`).
- **Startup:** `REQUIRE_STRIPE_WEBHOOK_SECRET=true` (staging/production env examples) fails fast if `STRIPE_WEBHOOK_SECRET` is empty.

## Password reset

- `POST /auth/forgot-password` (5/min) always returns 204 (no user enumeration); `POST /auth/reset-password` (10/min) consumes the token.
- Reset token is a short-lived JWT (`typ=password_reset`, `exp=1h`) carrying `tenant_id` and `pwd_fp` — a 12-hex-char digest of the current `password_hash`.
- **Design note (deviation from TZ-20 "store token hash"):** no DB table is used. Single-use is enforced by `pwd_fp`: changing the password rotates the hash so the token stops verifying. Expiry is enforced by `exp`. This avoids a migration and a cross-tenant token-lookup `SECURITY DEFINER` function on an unauthenticated endpoint (the tenant is already inside the signed JWT).
- On success all refresh tokens for the user are revoked and a `user.reset_password` audit row is written.
- Functional security tests: `tests/test_auth_password_reset.py` (anti-enumeration, single-use, rate limits). No RLS table tests (stateless).

## SECURITY DEFINER functions

| Function | Purpose | Why RLS bypass is acceptable |
|----------|---------|------------------------------|
| `lookup_stripe_charge_for_webhook(p_pi)` | Map PaymentIntent id → `(tenant_id, charge_id)` for unauthenticated Stripe webhooks | Returns **only** those two columns; mutations run inside `tenant_transaction_session` afterward. Covered by `tests/test_stripe_webhook_lookup.py` + integration webhook tests. |
| `lookup_active_users_by_email_login(p_email)` | Email → user rows for login / forgot-password without a tenant JWT | Minimal columns for auth; password verification and tenant session follow. Used by auth flows covered in `tests/test_auth_password_reset.py` / login tests. |

Both functions: `SECURITY DEFINER`, `SET row_security = off`, `REVOKE ALL FROM PUBLIC`, `GRANT EXECUTE` to the app role only.

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

## Test coverage (work item F)

| Surface | DB object | Negative / security test |
|---------|-----------|--------------------------|
| Booking notes | `bookings.notes` column | `test_tenant_b_cannot_get_tenant_a_booking_with_notes`, `test_tenant_b_cannot_patch_tenant_a_booking_notes` |
| Folio categories | `folio_charge_categories` | `test_tenant_b_cannot_see_tenant_a_folio_category` |
| Password reset | none (JWT) | `tests/test_auth_password_reset.py` (logic, not RLS) |
| Stripe webhook lookup | `lookup_stripe_charge_for_webhook()` | `tests/test_stripe_webhook_lookup.py` + `tests/test_stripe_webhook.py` |

## Findings / follow-ups

| ID | Severity | Item | Status |
|----|----------|------|--------|
| F-1 | Info | Stripe inbound webhook (`charge.refunded`, `charge.dispute.created`) | Done (TZ-20 G12) |
| F-2 | Low | `/health/deep` must stay off public edge | Done — blocked in `deploy/Caddyfile` + local `Caddyfile` (`respond 403`) |
| F-3 | Info | npm audit in separate frontend repo | Tracked in tz20_frontend_sentry.md |
| F-4 | Medium | Standard Connect charge rejected `on_behalf_of` | Done (G13) — removed from `PaymentIntent.create` |
| F-5 | Low | Empty `STRIPE_WEBHOOK_SECRET` soft-fail only | Done (G13) — `REQUIRE_STRIPE_WEBHOOK_SECRET` fail-fast |
