# TZ-20 / B — Frontend Sentry (external SPA repo)

This backend repository does not contain the Vite/React SPA. Complete these items in the **frontend** repository.

## Checklist

- [ ] Add `@sentry/react` and initialize with `VITE_SENTRY_DSN` (optional — omit DSN in local dev).
- [ ] Wrap the router in `Sentry.ErrorBoundary` (fallback UI for render errors).
- [ ] Upload source maps to Sentry during `vite build` in CI/production only (`@sentry/vite-plugin` or equivalent).
- [ ] `tracesSampleRate` ≤ `0.1`.
- [ ] `beforeSend` filter: ignore TanStack Query cancellations / aborted `fetch` (`AbortError`, status 0).
- [ ] Tag events with `environment` (`staging` / `production`) and `release` (git SHA).
- [ ] CI job: `npm audit --omit=dev` — critical/high vulnerabilities block merge.

## Acceptance

- Throw a test error on staging → Sentry shows readable stack (not minified one-liner).
- No access tokens or refresh cookies in Sentry request extras.

## Backend coordination

- Backend Sentry uses the same `APP_RELEASE` git SHA when both are deployed from one pipeline.
- CORS: ensure staging/prod SPA origins are listed in backend `CORS_ORIGINS`.
