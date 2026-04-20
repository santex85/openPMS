"""Per-tenant folio charge category catalog (settings)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    chain_dependency_runners,
    require_roles,
    require_scopes,
)
from app.core.api_scopes import BOOKINGS_READ, BOOKINGS_WRITE
from app.schemas.folio_category import (
    FolioChargeCategoryCreate,
    FolioChargeCategoryRead,
    FolioChargeCategoryUpdate,
)
from app.services.audit_service import record_audit
from app.services.folio_category_service import (
    FolioCategoryError,
    create_category,
    delete_category,
    get_category_by_code,
    list_categories,
    update_category,
)

router = APIRouter()

FolioCategoriesReadDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles(
                "owner",
                "manager",
                "viewer",
                "housekeeper",
                "receptionist",
            ),
            require_scopes(BOOKINGS_READ),
        ),
    ),
]
FolioCategoriesWriteDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager"),
            require_scopes(BOOKINGS_WRITE),
        ),
    ),
]


@router.get("", response_model=list[FolioChargeCategoryRead])
async def list_folio_charge_categories(
    _: FolioCategoriesReadDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> list[FolioChargeCategoryRead]:
    rows = await list_categories(session, tenant_id)
    return [FolioChargeCategoryRead.model_validate(r) for r in rows]


@router.post(
    "",
    response_model=FolioChargeCategoryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_folio_charge_category(
    _: FolioCategoriesWriteDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    body: FolioChargeCategoryCreate,
) -> FolioChargeCategoryRead:
    try:
        row = await create_category(session, tenant_id, body)
    except FolioCategoryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    payload = FolioChargeCategoryRead.model_validate(row).model_dump(mode="json")
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="folio_category.create",
        entity_type="folio_charge_category",
        entity_id=row.id,
        new_values=payload,
    )
    return FolioChargeCategoryRead.model_validate(row)


@router.patch("/{code}", response_model=FolioChargeCategoryRead)
async def patch_folio_charge_category(
    _: FolioCategoriesWriteDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    code: str,
    body: FolioChargeCategoryUpdate,
) -> FolioChargeCategoryRead:
    row_before = await get_category_by_code(session, tenant_id, code)
    if row_before is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="category not found",
        )
    old_values = FolioChargeCategoryRead.model_validate(
        row_before,
    ).model_dump(mode="json")
    try:
        row = await update_category(session, tenant_id, code, body)
    except FolioCategoryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    new_values = FolioChargeCategoryRead.model_validate(row).model_dump(mode="json")
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="folio_category.update",
        entity_type="folio_charge_category",
        entity_id=row.id,
        old_values=old_values,
        new_values=new_values,
    )
    return FolioChargeCategoryRead.model_validate(row)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_folio_charge_category(
    _: FolioCategoriesWriteDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    code: str,
) -> Response:
    row_before = await get_category_by_code(session, tenant_id, code)
    if row_before is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="category not found",
        )
    old_values = FolioChargeCategoryRead.model_validate(
        row_before,
    ).model_dump(mode="json")
    try:
        await delete_category(session, tenant_id, code)
    except FolioCategoryError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
        ) from None
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="folio_category.delete",
        entity_type="folio_charge_category",
        entity_id=row_before.id,
        old_values=old_values,
        new_values=None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
