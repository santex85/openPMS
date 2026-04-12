"""Tests for tenant JWT middleware helpers."""

from app.middleware.tenant_jwt import _client_ip, _is_auth_exempt_path


def test_is_auth_exempt_paths() -> None:
    assert _is_auth_exempt_path("/health") is True
    assert _is_auth_exempt_path("/docs") is True
    assert _is_auth_exempt_path("/webhooks/channex") is True
    assert _is_auth_exempt_path("/bookings") is False


def test_client_ip_from_forwarded_for() -> None:
    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/x",
        "raw_path": b"/x",
        "root_path": "",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("10.0.0.1", 0),
        "headers": [(b"x-forwarded-for", b"203.0.113.1, 10.0.0.2")],
    }

    req = Request(scope)
    assert _client_ip(req) == "203.0.113.1"
