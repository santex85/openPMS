"""Unit tests for Sentry PII scrubbing and safe no-op behavior (no DSN)."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.sentry import (
    _scrub_event,
    _scrub_headers,
    capture_message_with_tags,
    capture_task_exception,
    init_sentry,
)


def test_scrub_headers_filters_sensitive_case_insensitive() -> None:
    out = _scrub_headers(
        {
            "Authorization": "Bearer secret",
            "X-Api-Key": "k",
            "Cookie": "s=1",
            "Accept": "application/json",
        }
    )
    assert out is not None
    assert out["Authorization"] == "[Filtered]"
    assert out["X-Api-Key"] == "[Filtered]"
    assert out["Cookie"] == "[Filtered]"
    assert out["Accept"] == "application/json"


def test_scrub_headers_empty_or_none_passthrough() -> None:
    assert _scrub_headers(None) is None
    assert _scrub_headers({}) == {}


def test_scrub_event_filters_auth_path_body_and_headers() -> None:
    event = {
        "request": {
            "url": "https://api.example.com/auth/login",
            "headers": {"Cookie": "s=1", "Accept": "json"},
            "data": "password=secret",
            "body": "password=secret",
        }
    }
    out = _scrub_event(event, {})
    assert out is not None
    assert out["request"]["headers"]["Cookie"] == "[Filtered]"
    assert out["request"]["headers"]["Accept"] == "json"
    assert out["request"]["data"] == "[Filtered]"
    assert out["request"]["body"] == "[Filtered]"


def test_scrub_event_filters_stripe_path_body() -> None:
    event = {"request": {"url": "https://api.example.com/stripe/charge", "data": "x"}}
    out = _scrub_event(event, {})
    assert out["request"]["data"] == "[Filtered]"


def test_scrub_event_keeps_non_sensitive_path_body() -> None:
    event = {"request": {"url": "https://api.example.com/bookings", "data": "keep-me"}}
    out = _scrub_event(event, {})
    assert out["request"]["data"] == "keep-me"


def test_scrub_event_without_request_is_unchanged() -> None:
    event = {"level": "error", "message": "boom"}
    assert _scrub_event(event, {}) == {"level": "error", "message": "boom"}


def test_init_sentry_noop_without_dsn() -> None:
    # Test settings have no SENTRY_DSN -> init returns early (no raise, no client).
    init_sentry(get_settings())


def test_capture_helpers_noop_when_not_initialized() -> None:
    # No DSN configured in tests, so these are safe no-ops.
    capture_message_with_tags("hi", level="warning", tags={"tenant_id": "t1"})
    capture_task_exception(RuntimeError("boom"), task_name="unit.test", tenant_id="t1")
