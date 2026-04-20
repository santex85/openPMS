"""OpenPMSClient retries HTTP 429 with Retry-After (MIG-14)."""

from __future__ import annotations

import httpx

from scripts.migrate.core.client import APIRateLimitError, OpenPMSClient


def _client_with_mock_transport(transport: httpx.BaseTransport) -> OpenPMSClient:
    oc = OpenPMSClient("http://test.invalid", "token")
    oc.close()
    oc._client = httpx.Client(
        base_url="http://test.invalid",
        headers={
            "Authorization": "Bearer token",
            "Content-Type": "application/json",
        },
        transport=transport,
        timeout=60.0,
    )
    return oc


def test_429_retry_after_zero_then_200() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(
            200,
            json={"items": [], "total": 0, "limit": 100, "offset": 0},
        )

    oc = _client_with_mock_transport(httpx.MockTransport(handler))
    try:
        data = oc.list_guests(q="a@b.c", limit=10)
        assert data["total"] == 0
        assert calls["n"] == 2
    finally:
        oc.close()


def test_429_five_times_raises() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"})

    oc = _client_with_mock_transport(httpx.MockTransport(handler))
    try:
        try:
            oc.list_guests()
        except APIRateLimitError:
            pass
        else:
            raise AssertionError("expected APIRateLimitError")
        assert calls["n"] == 5
    finally:
        oc.close()
