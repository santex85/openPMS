"""CRUD and seeding for folio charge categories (per tenant)."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bookings.folio_charge_category import FolioChargeCategory
from app.schemas.folio_category import (
    FolioChargeCategoryCreate,
    FolioChargeCategoryUpdate,
)

BUILTIN_CHARGE_ROWS: tuple[tuple[str, str, int], ...] = (
    ("room_charge", "Room charge", 0),
    ("food_beverage", "Food & beverage", 10),
    ("spa", "Spa", 20),
    ("minibar", "Minibar", 30),
    ("tax", "Tax", 40),
    ("discount", "Discount", 50),
    ("misc", "Miscellaneous", 60),
    ("service", "Service", 70),
)


class FolioCategoryError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


async def ensure_builtin_categories(
    session: AsyncSession,
    tenant_id: UUID,
) -> None:
    """Idempotently insert built-in charge category rows for a tenant."""
    for code, label, sort_order in BUILTIN_CHARGE_ROWS:
        exists = await session.scalar(
            select(FolioChargeCategory.id).where(
                FolioChargeCategory.tenant_id == tenant_id,
                FolioChargeCategory.code == code,
            ),
        )
        if exists is not None:
            continue
        session.add(
            FolioChargeCategory(
                tenant_id=tenant_id,
                code=code,
                label=label,
                is_builtin=True,
                is_active=True,
                sort_order=sort_order,
            ),
        )
    await session.flush()


async def list_categories(
    session: AsyncSession,
    tenant_id: UUID,
) -> list[FolioChargeCategory]:
    await ensure_builtin_categories(session, tenant_id)
    stmt = (
        select(FolioChargeCategory)
        .where(FolioChargeCategory.tenant_id == tenant_id)
        .order_by(FolioChargeCategory.sort_order.asc(), FolioChargeCategory.code.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def active_category_codes(
    session: AsyncSession,
    tenant_id: UUID,
) -> set[str]:
    await ensure_builtin_categories(session, tenant_id)
    stmt = select(FolioChargeCategory.code).where(
        FolioChargeCategory.tenant_id == tenant_id,
        FolioChargeCategory.is_active.is_(True),
    )
    result = await session.execute(stmt)
    return {str(row[0]) for row in result.all()}


async def get_category_by_code(
    session: AsyncSession,
    tenant_id: UUID,
    code: str,
) -> FolioChargeCategory | None:
    await ensure_builtin_categories(session, tenant_id)
    return await session.scalar(
        select(FolioChargeCategory).where(
            FolioChargeCategory.tenant_id == tenant_id,
            FolioChargeCategory.code == code,
        ),
    )


async def create_category(
    session: AsyncSession,
    tenant_id: UUID,
    body: FolioChargeCategoryCreate,
) -> FolioChargeCategory:
    await ensure_builtin_categories(session, tenant_id)
    row = FolioChargeCategory(
        tenant_id=tenant_id,
        code=body.code,
        label=body.label,
        is_builtin=False,
        is_active=body.is_active,
        sort_order=body.sort_order,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise FolioCategoryError(
            "category code already exists",
            status_code=409,
        ) from exc
    return row


async def update_category(
    session: AsyncSession,
    tenant_id: UUID,
    code: str,
    body: FolioChargeCategoryUpdate,
) -> FolioChargeCategory:
    await ensure_builtin_categories(session, tenant_id)
    row = await get_category_by_code(session, tenant_id, code)
    if row is None:
        raise FolioCategoryError("category not found", status_code=404)
    if body.label is not None:
        row.label = body.label
    if body.sort_order is not None:
        row.sort_order = body.sort_order
    if body.is_active is not None:
        row.is_active = body.is_active
    await session.flush()
    await session.refresh(row)
    return row


async def delete_category(
    session: AsyncSession,
    tenant_id: UUID,
    code: str,
) -> None:
    await ensure_builtin_categories(session, tenant_id)
    row = await get_category_by_code(session, tenant_id, code)
    if row is None:
        raise FolioCategoryError("category not found", status_code=404)
    if row.is_builtin:
        raise FolioCategoryError(
            "cannot delete built-in category",
            status_code=400,
        )
    await session.delete(row)
    await session.flush()
