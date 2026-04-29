"""X-API-Key authentication: scoped access via require_scopes."""

from __future__ import annotations


def test_api_key_allows_scoped_route(
    client,
    properties_only_api_key: tuple[str, str],
) -> None:
    _tid, plain = properties_only_api_key
    r = client.get("/properties", headers={"X-API-Key": plain})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_key_forbidden_without_scope(
    client,
    properties_only_api_key: tuple[str, str],
) -> None:
    """Key has only properties:read; guests list requires guests:read."""
    _tid, plain = properties_only_api_key
    r = client.get("/guests", headers={"X-API-Key": plain})
    assert r.status_code == 403
    assert "scope" in r.json()["detail"].lower()


def test_api_key_unknown_returns_401(client) -> None:
    r = client.get(
        "/properties",
        headers={"X-API-Key": "opms_invalid_key_that_does_not_exist"},
    )
    assert r.status_code == 401
