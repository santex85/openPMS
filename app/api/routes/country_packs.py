"""Country packs and extension registry REST API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import (
    SessionDep,
    TenantIdDep,
    chain_dependency_runners,
    require_jwt_user,
    require_roles,
    require_scopes,
)
from app.core.api_scopes import COUNTRY_PACKS_READ, COUNTRY_PACKS_WRITE
from app.schemas.country_pack import (
    CountryPackApplyRequest,
    CountryPackApplyResponse,
    CountryPackCreate,
    CountryPackListItem,
    CountryPackPatch,
    CountryPackRead,
    ExtensionCreate,
    ExtensionRead,
    PropertyExtensionRead,
    PropertyExtensionUpsert,
)
from app.services import country_pack_service as pack_svc
from app.services import extension_service as ext_svc
from app.services.country_pack_service import CountryPackServiceError
from app.services.extension_service import ExtensionServiceError

router = APIRouter(dependencies=[Depends(require_jwt_user())])

PackReadRolesDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner", "manager"),
            require_scopes(COUNTRY_PACKS_READ),
        ),
    ),
]
PackOwnerRolesDep = Annotated[
    None,
    Depends(
        chain_dependency_runners(
            require_roles("owner"),
            require_scopes(COUNTRY_PACKS_WRITE),
        ),
    ),
]


@router.get("/extensions", response_model=list[ExtensionRead])
async def list_extensions_route(
    _: PackReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    country_code: str | None = Query(None, max_length=2),
) -> list[ExtensionRead]:
    return await ext_svc.list_extensions(
        session,
        tenant_id,
        country_code=country_code,
    )


@router.post("/extensions", response_model=ExtensionRead, status_code=status.HTTP_201_CREATED)
async def register_extension_route(
    _: PackOwnerRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    body: ExtensionCreate,
) -> ExtensionRead:
    try:
        return await ext_svc.register_extension(session, tenant_id, body)
    except ExtensionServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/property-extensions", response_model=list[PropertyExtensionRead])
async def list_property_extensions_route(
    _: PackReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    property_id: UUID = Query(...),
) -> list[PropertyExtensionRead]:
    return await ext_svc.list_property_extensions(session, tenant_id, property_id)


@router.post(
    "/property-extensions",
    response_model=PropertyExtensionRead,
    status_code=status.HTTP_201_CREATED,
)
async def upsert_property_extension_route(
    _: PackOwnerRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    body: PropertyExtensionUpsert,
) -> PropertyExtensionRead:
    try:
        return await ext_svc.upsert_property_extension(
            session,
            tenant_id,
            body.property_id,
            body.extension_id,
            config=body.config,
            is_active=body.is_active,
        )
    except ExtensionServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("", response_model=list[CountryPackListItem])
async def list_country_packs_route(
    _: PackReadRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> list[CountryPackListItem]:
    return await pack_svc.list_country_packs(session, tenant_id)


@router.post("", response_model=CountryPackRead, status_code=status.HTTP_201_CREATED)
async def create_country_pack_route(
    _: PackOwnerRolesDep,
    session: SessionDep,
    tenant_id: TenantIdDep,
    body: CountryPackCreate,
) -> CountryPackRead:
    try:
        return await pack_svc.create_country_pack(session, tenant_id, body)
    except CountryPackServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/{code}", response_model=CountryPackRead)
async def get_country_pack_route(
    _: PackReadRolesDep,
    code: str,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> CountryPackRead:
    row = await pack_svc.get_country_pack(session, tenant_id, code)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return row


@router.patch("/{code}", response_model=CountryPackRead)
async def patch_country_pack_route(
    _: PackOwnerRolesDep,
    code: str,
    session: SessionDep,
    tenant_id: TenantIdDep,
    body: CountryPackPatch,
) -> CountryPackRead:
    try:
        return await pack_svc.update_country_pack(session, tenant_id, code, body)
    except CountryPackServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_country_pack_route(
    _: PackOwnerRolesDep,
    code: str,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> None:
    try:
        await pack_svc.delete_country_pack(session, tenant_id, code)
    except CountryPackServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/{code}/apply", response_model=CountryPackApplyResponse)
async def apply_country_pack_route(
    _: PackOwnerRolesDep,
    code: str,
    session: SessionDep,
    tenant_id: TenantIdDep,
    body: CountryPackApplyRequest,
) -> CountryPackApplyResponse:
    try:
        return await pack_svc.apply_country_pack(
            session,
            tenant_id,
            code,
            body.property_id,
        )
    except CountryPackServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
