"""Helpers for direct ORM inserts in tests when Postgres uses FORCE RLS on tenant tables."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def disable_row_security_for_test_seed(session: AsyncSession) -> None:
    """Allow FK checks to see just-inserted parent rows during test transaction seeding."""
    await session.execute(text("SET LOCAL row_security = off"))
