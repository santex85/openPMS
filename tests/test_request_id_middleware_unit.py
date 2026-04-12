"""Tests for RequestIdASGIMiddleware."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_request_id_echoes_incoming_header() -> None:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from app.middleware.request_id import RequestIdASGIMiddleware

    async def home(req: Request) -> PlainTextResponse:
        return PlainTextResponse(req.state.request_id)

    app = Starlette(routes=[Route("/", home)])
    wrapped = RequestIdASGIMiddleware(app)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 0),
        "headers": [(b"x-request-id", b"client-rid-1")],
    }
    messages: list[dict] = []

    async def receive() -> dict:  # pragma: no cover
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg: dict) -> None:
        messages.append(msg)

    await wrapped(scope, receive, send)

    assert any(
        m.get("type") == "http.response.start"
        and any(
            k == b"x-request-id" and v == b"client-rid-1"
            for k, v in m.get("headers", [])
        )
        for m in messages
    )
