"""Guest profiles CRUD."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import SessionDep, TenantIdDep, require_roles, require_scopes
from app.core.api_scopes import GUESTS_READ, GUESTS_WRITE
from app.schemas.guest import GuestCreate, GuestDetailRead, GuestPatch, GuestRead
from app.services.audit_service import record_audit
from app.services.guest_service import GuestServiceError, create_guest, get_guest_with_booking_summaries, list_guests, patch_guest

router = APIRouter()

GuestReadRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "viewer", "receptionist")),
    Depends(require_scopes(GUESTS_READ)),
]
GuestWriteRolesDep = Annotated[
    None,
    Depends(require_roles("owner", "manager", "receptionist")),
    Depends(require_scopes(GUESTS_WRITE)),
]


@router.get("", response_model=list[GuestRead])
async def get_guests(
    _: GuestReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    q: str | None = Query(None, description="Search first name, last name, email, phone"),
) -> list[GuestRead]:
    rows = await list_guests(session, tenant_id, q=q)
    return [GuestRead.model_validate(r) for r in rows]


@router.get("/{guest_id}", response_model=GuestDetailRead)
async def get_guest_detail(
    guest_id: UUID,
    _: GuestReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> GuestDetailRead:
    guest, summaries = await get_guest_with_booking_summaries(
        session,
        tenant_id,
        guest_id,
    )
    if guest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="guest not found",
        )
    core = GuestRead.model_validate(guest).model_dump()
    return GuestDetailRead(**core, bookings=summaries)


@router.post("", response_model=GuestRead, status_code=status.HTTP_201_CREATED)
async def post_guest(
    _: GuestWriteRolesDep,
    body: GuestCreate,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> GuestRead:
    try:
        row = await create_guest(session, tenant_id, body)
    except GuestServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="guest.create",
        entity_type="guest",
        entity_id=row.id,
        new_values=GuestRead.model_validate(row).model_dump(mode="json"),
    )
    return GuestRead.model_validate(row)


@router.patch("/{guest_id}", response_model=GuestRead)
async def patch_guest_by_id(
    guest_id: UUID,
    _: GuestWriteRolesDep,
    body: GuestPatch,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> GuestRead:
    try:
        row = await patch_guest(session, tenant_id, guest_id, body)
    except GuestServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await record_audit(
        session,
        tenant_id=tenant_id,
        action="guest.patch",
        entity_type="guest",
        entity_id=guest_id,
        new_values=body.model_dump(exclude_unset=True, mode="json"),
    )
    return GuestRead.model_validate(row)
