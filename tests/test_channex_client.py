"""Channex HTTP client (mocked httpx)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.integrations.channex.client import ChannexApiError, ChannexClient


def _resp(status: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.text = text or ""
    if json_data is not None:
        r.json = MagicMock(return_value=json_data)
    else:
        r.json = MagicMock(return_value={})
    r.content = b"{}" if json_data is not None else b""
    return r


@pytest.mark.asyncio
async def test_get_properties_success() -> None:
    client = ChannexClient("k", "production")
    mock_r = _resp(
        200,
        {"data": [{"id": "p1", "attributes": {"title": "Sea"}}]},
    )
    with patch.object(client, "_raw_request", new_callable=AsyncMock) as m:
        m.return_value = mock_r
        props = await client.get_properties()
    assert len(props) == 1
    assert props[0].id == "p1"
    assert props[0].title == "Sea"


@pytest.mark.asyncio
async def test_get_properties_401_raises() -> None:
    client = ChannexClient("k", "production")
    mock_r = _resp(401, text="Unauthorized")
    with patch.object(client, "_raw_request", new_callable=AsyncMock) as m:
        m.return_value = mock_r
        with pytest.raises(ChannexApiError) as exc_info:
            await client.get_properties()
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_properties_404_raises() -> None:
    client = ChannexClient("k", "production")
    mock_r = _resp(404, text="missing")
    with patch.object(client, "_raw_request", new_callable=AsyncMock) as m:
        m.return_value = mock_r
        with pytest.raises(ChannexApiError) as exc_info:
            await client.get_properties()
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_push_ari_429_retries_then_success() -> None:
    client = ChannexClient("k", "production")
    r429 = _resp(429)
    r200 = _resp(200, {"ok": True})
    with patch.object(client, "_raw_request", new_callable=AsyncMock) as m:
        m.side_effect = [r429, r429, r200]
        out = await client.push_ari([{"x": 1}])
    assert m.await_count == 3
    assert out == {"ok": True}


@pytest.mark.asyncio
async def test_push_ari_5xx_retries_then_success() -> None:
    client = ChannexClient("k", "production")
    r500 = _resp(500)
    r200 = _resp(200, {"ok": True})
    with (
        patch.object(client, "_raw_request", new_callable=AsyncMock) as m,
        patch(
            "app.integrations.channex.client.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        m.side_effect = [r500, r200]
        out = await client.push_ari([{"x": 1}])
    assert m.await_count == 2
    assert out == {"ok": True}


@pytest.mark.asyncio
async def test_push_ari_5xx_exhausted_raises() -> None:
    client = ChannexClient("k", "production")
    r500 = _resp(500, text="err")
    with (
        patch.object(client, "_raw_request", new_callable=AsyncMock) as m,
        patch(
            "app.integrations.channex.client.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        m.return_value = r500
        with pytest.raises(ChannexApiError) as exc_info:
            await client.push_ari([{"x": 1}])
    assert exc_info.value.status_code == 500
    assert m.await_count == 3
