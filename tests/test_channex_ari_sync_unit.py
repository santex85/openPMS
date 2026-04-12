"""Unit tests for channex_ari_sync helpers and _run_channex_full_ari_sync branches."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.channex.client import ChannexApiError
from app.models.integrations.channex_property_link import ChannexPropertyLink
from app.tasks.channex_ari_sync import _restriction_row, _run_channex_full_ari_sync

from tests.test_channex_webhook_sync import _database_url, _seed_channex_property


def test_restriction_row_skips_non_positive_price() -> None:
    r = SimpleNamespace(
        price=Decimal("0"),
        stop_sell=False,
        min_stay_arrival=None,
        max_stay=None,
    )
    assert _restriction_row("p1", "rp1", date(2026, 1, 1), r, "USD") is None


def test_restriction_row_includes_min_max_stay_and_stop_sell() -> None:
    r = SimpleNamespace(
        price=Decimal("50.00"),
        stop_sell=True,
        min_stay_arrival=2,
        max_stay=14,
    )
    out = _restriction_row("prop-cx", "rp-cx", date(2026, 6, 1), r, "EUR")
    assert out is not None
    assert out["stop_sell"] is True
    assert out["min_stay_arrival"] == 2
    assert out["max_stay"] == 14
    assert "50" in str(out["rate"])


@pytest.mark.asyncio
async def test_run_full_ari_sync_skips_when_link_not_active(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_channex_property(
        db_engine,
        status="pending",
        channex_webhook_id=None,
    )
    await _run_channex_full_ari_sync(ctx["tenant_id"], ctx["property_id"])


@pytest.mark.asyncio
async def test_run_full_ari_sync_sets_error_on_channex_api_error(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=None,
    )
    mock = AsyncMock()
    mock.push_availability = AsyncMock(side_effect=ChannexApiError("upstream failed"))
    with patch("app.tasks.channex_ari_sync._client_for_link", return_value=mock):
        await _run_channex_full_ari_sync(ctx["tenant_id"], ctx["property_id"])

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        link = await session.scalar(
            select(ChannexPropertyLink).where(ChannexPropertyLink.id == ctx["link_id"])
        )
    assert link is not None
    assert link.error_message is not None
    assert "upstream" in link.error_message


@pytest.mark.asyncio
async def test_run_full_ari_sync_sets_error_on_unexpected_exception(
    db_engine: object,
    channex_encrypt_env: None,
) -> None:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL required")
    ctx = await _seed_channex_property(
        db_engine,
        status="active",
        channex_webhook_id=None,
    )
    mock = AsyncMock()
    mock.push_availability = AsyncMock(side_effect=RuntimeError("network blew up"))
    with patch("app.tasks.channex_ari_sync._client_for_link", return_value=mock):
        await _run_channex_full_ari_sync(ctx["tenant_id"], ctx["property_id"])

    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        link = await session.scalar(
            select(ChannexPropertyLink).where(ChannexPropertyLink.id == ctx["link_id"])
        )
    assert link is not None
    assert "network" in (link.error_message or "")
