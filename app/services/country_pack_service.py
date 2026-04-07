"""Country pack CRUD and apply-to-property."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core.country_pack import CountryPack
from app.models.core.property import Property
from app.schemas.country_pack import (
    CountryPackApplyResponse,
    CountryPackCreate,
    CountryPackListItem,
    CountryPackPatch,
    CountryPackRead,
    TaxRuleSchema,
)


class CountryPackServiceError(Exception):
    def __init__(self, detail: str, *, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _pack_to_read(row: CountryPack) -> CountryPackRead:
    return CountryPackRead.model_validate(row)


async def list_country_packs(
    session: AsyncSession,
    tenant_id: UUID,
) -> list[CountryPackListItem]:
    stmt = (
        select(CountryPack)
        .where(
            (CountryPack.is_builtin.is_(True))
            | (CountryPack.tenant_id == tenant_id),
        )
        .order_by(CountryPack.is_builtin.desc(), CountryPack.code)
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    return [CountryPackListItem.model_validate(r) for r in rows]


async def get_country_pack(
    session: AsyncSession,
    tenant_id: UUID,
    code: str,
) -> CountryPackRead | None:
    c = code.strip()
    stmt = select(CountryPack).where(
        CountryPack.code == c,
        (CountryPack.is_builtin.is_(True)) | (CountryPack.tenant_id == tenant_id),
    )
    row = await session.scalar(stmt)
    if row is None:
        return None
    return _pack_to_read(row)


async def create_country_pack(
    session: AsyncSession,
    tenant_id: UUID,
    data: CountryPackCreate,
) -> CountryPackRead:
    code = data.code.strip()
    existing = await session.scalar(select(CountryPack).where(CountryPack.code == code))
    if existing is not None:
        raise CountryPackServiceError("country pack code already exists", status_code=409)

    taxes_dump = [r.model_dump(mode="json") for r in data.taxes]
    row = CountryPack(
        code=code,
        tenant_id=tenant_id,
        name=data.name.strip(),
        currency_code=data.currency_code,
        currency_symbol=data.currency_symbol.strip(),
        currency_symbol_position=data.currency_symbol_position,
        currency_decimal_places=data.currency_decimal_places,
        timezone=data.timezone.strip(),
        date_format=data.date_format.strip(),
        locale=data.locale.strip(),
        default_checkin_time=data.default_checkin_time,
        default_checkout_time=data.default_checkout_time,
        taxes=taxes_dump,
        payment_methods=data.payment_methods,
        fiscal_year_start=data.fiscal_year_start.strip() if data.fiscal_year_start else None,
        is_builtin=False,
    )
    session.add(row)
    await session.flush()
    return _pack_to_read(row)


async def update_country_pack(
    session: AsyncSession,
    tenant_id: UUID,
    code: str,
    data: CountryPackPatch,
) -> CountryPackRead:
    c = code.strip()
    row = await session.scalar(
        select(CountryPack).where(
            CountryPack.code == c,
            (CountryPack.is_builtin.is_(True)) | (CountryPack.tenant_id == tenant_id),
        ),
    )
    if row is None:
        raise CountryPackServiceError("country pack not found", status_code=404)
    if row.is_builtin:
        raise CountryPackServiceError("cannot modify builtin country pack", status_code=403)
    if row.tenant_id != tenant_id:
        raise CountryPackServiceError("country pack not found", status_code=404)

    patch = data.model_dump(exclude_unset=True)
    if "name" in patch:
        row.name = str(patch["name"]).strip()
    if "currency_code" in patch:
        row.currency_code = patch["currency_code"]
    if "currency_symbol" in patch:
        row.currency_symbol = str(patch["currency_symbol"]).strip()
    if "currency_symbol_position" in patch:
        row.currency_symbol_position = patch["currency_symbol_position"]
    if "currency_decimal_places" in patch:
        row.currency_decimal_places = int(patch["currency_decimal_places"])
    if "timezone" in patch:
        row.timezone = str(patch["timezone"]).strip()
    if "date_format" in patch:
        row.date_format = str(patch["date_format"]).strip()
    if "locale" in patch:
        row.locale = str(patch["locale"]).strip()
    if "default_checkin_time" in patch:
        row.default_checkin_time = patch["default_checkin_time"]
    if "default_checkout_time" in patch:
        row.default_checkout_time = patch["default_checkout_time"]
    if "taxes" in patch and patch["taxes"] is not None:
        taxes_val = patch["taxes"]
        row.taxes = [
            t.model_dump(mode="json") if isinstance(t, TaxRuleSchema) else dict(t)
            for t in taxes_val
        ]
    if "payment_methods" in patch:
        row.payment_methods = patch["payment_methods"] or []
    if "fiscal_year_start" in patch:
        fy = patch["fiscal_year_start"]
        row.fiscal_year_start = str(fy).strip() if fy else None
    await session.flush()
    return _pack_to_read(row)


async def delete_country_pack(
    session: AsyncSession,
    tenant_id: UUID,
    code: str,
) -> None:
    c = code.strip()
    row = await session.scalar(
        select(CountryPack).where(
            CountryPack.code == c,
            (CountryPack.is_builtin.is_(True)) | (CountryPack.tenant_id == tenant_id),
        ),
    )
    if row is None:
        raise CountryPackServiceError("country pack not found", status_code=404)
    if row.is_builtin:
        raise CountryPackServiceError("cannot delete builtin country pack", status_code=403)
    if row.tenant_id != tenant_id:
        raise CountryPackServiceError("country pack not found", status_code=404)

    n_props = await session.scalar(
        select(func.count())
        .select_from(Property)
        .where(
            Property.tenant_id == tenant_id,
            Property.country_pack_code == c,
        ),
    )
    if n_props and int(n_props) > 0:
        raise CountryPackServiceError(
            "country pack is attached to one or more properties",
            status_code=409,
        )

    session.delete(row)
    await session.flush()


async def apply_country_pack(
    session: AsyncSession,
    tenant_id: UUID,
    code: str,
    property_id: UUID,
) -> CountryPackApplyResponse:
    c = code.strip()
    pack = await session.scalar(
        select(CountryPack).where(
            CountryPack.code == c,
            (CountryPack.is_builtin.is_(True)) | (CountryPack.tenant_id == tenant_id),
        ),
    )
    if pack is None:
        raise CountryPackServiceError("country pack not found", status_code=404)

    prop = await session.scalar(
        select(Property).where(
            Property.tenant_id == tenant_id,
            Property.id == property_id,
        ),
    )
    if prop is None:
        raise CountryPackServiceError("property not found", status_code=404)

    prop.country_pack_code = pack.code
    prop.currency = pack.currency_code
    prop.timezone = pack.timezone
    prop.checkin_time = pack.default_checkin_time
    prop.checkout_time = pack.default_checkout_time
    await session.flush()

    pms = pack.payment_methods if isinstance(pack.payment_methods, list) else []
    pm_out = [str(x) for x in pms]

    return CountryPackApplyResponse(
        property_id=prop.id,
        country_pack_code=pack.code,
        currency=prop.currency,
        timezone=prop.timezone,
        checkin_time=prop.checkin_time,
        checkout_time=prop.checkout_time,
        payment_methods=pm_out,
    )
