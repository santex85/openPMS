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
async def test_get_properties_id_from_attributes_only() -> None:
    """Some payloads omit resource-level id; use attributes.id."""
    client = ChannexClient("k", "production")
    mock_r = _resp(
        200,
        {"data": [{"attributes": {"id": "p2", "title": "Bay"}}]},
    )
    with patch.object(client, "_raw_request", new_callable=AsyncMock) as m:
        m.return_value = mock_r
        props = await client.get_properties()
    assert len(props) == 1
    assert props[0].id == "p2"
    assert props[0].title == "Bay"


@pytest.mark.asyncio
async def test_get_properties_data_null_returns_empty() -> None:
    client = ChannexClient("k", "production")
    mock_r = _resp(200, {"data": None})
    with patch.object(client, "_raw_request", new_callable=AsyncMock) as m:
        m.return_value = mock_r
        props = await client.get_properties()
    assert props == []


@pytest.mark.asyncio
async def test_create_property_success() -> None:
    client = ChannexClient("k", "production")
    mock_r = MagicMock(spec=httpx.Response)
    mock_r.status_code = 201
    mock_r.text = ""
    mock_r.json = MagicMock(
        return_value={
            "data": {
                "type": "property",
                "id": "new-id",
                "attributes": {
                    "id": "new-id",
                    "title": "From OpenPMS",
                    "currency": "EUR",
                },
            },
        },
    )
    mock_r.content = b"{}"
    with patch.object(client, "_raw_request", new_callable=AsyncMock) as m:
        m.return_value = mock_r
        prop = await client.create_property("From OpenPMS", "eur", "Europe/Berlin")
    assert prop.id == "new-id"
    assert prop.title == "From OpenPMS"
    m.assert_awaited_once()
    call_kw = m.await_args.kwargs
    assert call_kw["json_body"]["property"]["title"] == "From OpenPMS"
    assert call_kw["json_body"]["property"]["currency"] == "EUR"
    assert call_kw["json_body"]["property"]["timezone"] == "Europe/Berlin"
    assert call_kw["json_body"]["property"]["property_type"] == "hotel"


@pytest.mark.asyncio
async def test_create_room_type_success() -> None:
    client = ChannexClient("k", "production")
    mock_r = MagicMock(spec=httpx.Response)
    mock_r.status_code = 201
    mock_r.text = ""
    mock_r.json = MagicMock(
        return_value={
            "data": {
                "type": "room_type",
                "id": "rt-1",
                "attributes": {"id": "rt-1", "title": "Deluxe"},
            },
        },
    )
    mock_r.content = b"{}"
    with patch.object(client, "_raw_request", new_callable=AsyncMock) as m:
        m.return_value = mock_r
        row = await client.create_room_type(
            property_id="p1",
            title="Deluxe",
            count_of_rooms=2,
            occ_adults=2,
            occ_children=0,
            occ_infants=0,
            default_occupancy=2,
        )
    assert row.id == "rt-1"
    assert row.title == "Deluxe"
    call_kw = m.await_args.kwargs
    assert call_kw["json_body"]["room_type"]["property_id"] == "p1"
    assert call_kw["json_body"]["room_type"]["count_of_rooms"] == 2


@pytest.mark.asyncio
async def test_create_rate_plan_success() -> None:
    client = ChannexClient("k", "sandbox")
    mock_r = MagicMock(spec=httpx.Response)
    mock_r.status_code = 201
    mock_r.text = ""
    mock_r.json = MagicMock(
        return_value={
            "data": {
                "type": "rate_plan",
                "id": "rp-9",
                "attributes": {"id": "rp-9", "title": "BAR / Deluxe"},
            },
        },
    )
    mock_r.content = b"{}"
    with patch.object(client, "_raw_request", new_callable=AsyncMock) as m:
        m.return_value = mock_r
        row = await client.create_rate_plan(
            property_id="p1",
            room_type_id="rt-1",
            title="BAR / Deluxe",
            currency="THB",
            primary_occupancy=2,
        )
    assert row.id == "rp-9"
    call_kw = m.await_args.kwargs
    rp = call_kw["json_body"]["rate_plan"]
    assert rp["room_type_id"] == "rt-1"
    assert rp["options"] == [{"occupancy": 2, "is_primary": True, "rate": 0}]
    assert rp["currency"] == "THB"


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
