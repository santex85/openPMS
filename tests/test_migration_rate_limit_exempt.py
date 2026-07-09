"""Migration CLI rate-limit bypass header."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.core.config import get_settings
from app.core.rate_limit import (
    MIGRATION_RATE_LIMIT_HEADER,
    bind_rate_limit_request,
    migration_rate_limit_exempt,
    reset_rate_limit_request,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _request(headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/guests",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def test_migration_exempt_false_when_key_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIGRATION_RATE_LIMIT_KEY", "")
    get_settings.cache_clear()
    req = _request({MIGRATION_RATE_LIMIT_HEADER: "anything"})
    token = bind_rate_limit_request(req)
    try:
        assert migration_rate_limit_exempt() is False
    finally:
        reset_rate_limit_request(token)


def test_migration_exempt_true_when_header_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIGRATION_RATE_LIMIT_KEY", "import-secret")
    get_settings.cache_clear()
    req = _request({MIGRATION_RATE_LIMIT_HEADER: "import-secret"})
    token = bind_rate_limit_request(req)
    try:
        assert migration_rate_limit_exempt() is True
    finally:
        reset_rate_limit_request(token)


def test_migration_exempt_false_on_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIGRATION_RATE_LIMIT_KEY", "import-secret")
    get_settings.cache_clear()
    req = _request({MIGRATION_RATE_LIMIT_HEADER: "wrong"})
    token = bind_rate_limit_request(req)
    try:
        assert migration_rate_limit_exempt() is False
    finally:
        reset_rate_limit_request(token)
