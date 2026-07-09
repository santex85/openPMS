"""Optional Sentry initialization and PII scrubbing."""

from __future__ import annotations

from typing import Any

from app.core.config import Settings

_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
    },
)
_SENSITIVE_BODY_PREFIXES = ("/auth/", "/stripe/")


def _scrub_headers(headers: dict[str, Any] | None) -> dict[str, Any] | None:
    if not headers:
        return headers
    out: dict[str, Any] = {}
    for key, value in headers.items():
        if str(key).lower() in _SENSITIVE_HEADERS:
            out[key] = "[Filtered]"
        else:
            out[key] = value
    return out


def _scrub_event(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    request = event.get("request")
    if isinstance(request, dict):
        request["headers"] = _scrub_headers(request.get("headers"))
        url = str(request.get("url") or "")
        path = url
        if "://" in url:
            path = url.split("://", 1)[-1]
            if "/" in path:
                path = "/" + path.split("/", 1)[1]
        for prefix in _SENSITIVE_BODY_PREFIXES:
            if path.startswith(prefix):
                if "data" in request:
                    request["data"] = "[Filtered]"
                if "body" in request:
                    request["body"] = "[Filtered]"
                break
    return event


def init_sentry(settings: Settings) -> None:
    """Initialize Sentry when SENTRY_DSN is set; no-op otherwise."""
    dsn = (settings.sentry_dsn or "").strip()
    if not dsn:
        return

    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    release = (settings.app_release or "").strip() or None
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.app_env,
        release=release,
        send_default_pii=False,
        traces_sample_rate=0.0,
        integrations=[
            FastApiIntegration(),
            CeleryIntegration(),
            SqlalchemyIntegration(),
        ],
        before_send=_scrub_event,
    )


def capture_message_with_tags(
    message: str,
    *,
    level: str = "warning",
    tags: dict[str, str] | None = None,
) -> None:
    """Send a Sentry message with tags when Sentry is initialized; no-op otherwise."""
    try:
        import sentry_sdk
    except ImportError:
        return
    if not sentry_sdk.is_initialized():
        return
    with sentry_sdk.push_scope() as scope:
        for key, value in (tags or {}).items():
            scope.set_tag(key, value)
        sentry_sdk.capture_message(message, level=level)


def capture_task_exception(
    exc: BaseException,
    *,
    task_name: str,
    tenant_id: str | None = None,
) -> None:
    """Report a handled Celery task failure with task/tenant context."""
    try:
        import sentry_sdk
    except ImportError:
        return
    if not sentry_sdk.is_initialized():
        return
    with sentry_sdk.push_scope() as scope:
        scope.set_tag("task_name", task_name)
        if tenant_id:
            scope.set_tag("tenant_id", tenant_id)
        sentry_sdk.capture_exception(exc)
