"""Stripe Connect OAuth routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from urllib.parse import quote

from app.api.deps import SessionDep, TenantIdDep
from app.api.routes.properties import PropertyReadRolesDep, PropertyTaxOwnerWriteDep
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.schemas.stripe_connect import StripeConnectUrlResponse, StripeStatusRead
from app.services import property_service
from app.services.stripe_connect_service import (
    StripeConnectError,
    build_connect_authorize_url,
    decode_oauth_state,
    disconnect_stripe_connection,
    exchange_code_for_connection,
    get_stripe_status,
)

router = APIRouter()


def _stripe_http_error(exc: StripeConnectError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get(
    "/properties/{property_id}/stripe/status",
    response_model=StripeStatusRead,
    response_model_exclude_none=True,
)
async def get_property_stripe_status(
    _: PropertyReadRolesDep,
    property_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> StripeStatusRead:
    if await property_service.get_property(session, tenant_id, property_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    return await get_stripe_status(session, tenant_id, property_id)


@router.get(
    "/properties/{property_id}/stripe/connect-url",
    response_model=StripeConnectUrlResponse,
)
async def get_property_stripe_connect_url(
    _: PropertyTaxOwnerWriteDep,
    property_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> StripeConnectUrlResponse:
    if await property_service.get_property(session, tenant_id, property_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    settings = get_settings()
    try:
        url = build_connect_authorize_url(settings, tenant_id, property_id)
    except StripeConnectError as exc:
        raise _stripe_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
    return StripeConnectUrlResponse(url=url)


@router.delete(
    "/properties/{property_id}/stripe/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_property_stripe_connection(
    _: PropertyTaxOwnerWriteDep,
    property_id: UUID,
    session: SessionDep,
    tenant_id: TenantIdDep,
) -> None:
    if await property_service.get_property(session, tenant_id, property_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found",
        )
    settings = get_settings()
    try:
        await disconnect_stripe_connection(settings, session, tenant_id, property_id)
    except StripeConnectError as exc:
        raise _stripe_http_error(exc) from exc


@router.api_route(
    "/stripe/oauth/callback",
    methods=["GET", "POST"],
)
@limiter.exempt
async def stripe_oauth_callback(
    request: Request,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    settings = get_settings()
    success_base = (settings.stripe_connect_success_url or "").strip()
    if not success_base:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="STRIPE_CONNECT_SUCCESS_URL is not configured",
        )

    def _redirect_success(property_id: UUID) -> RedirectResponse:
        sep = "&" if "?" in success_base else "?"
        url = f"{success_base}{sep}property_id={property_id}&connected=1"
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    def _redirect_denied() -> RedirectResponse:
        sep = "&" if "?" in success_base else "?"
        err = error or "access_denied"
        desc = error_description or ""
        url = (
            f"{success_base}{sep}stripe_error={quote(err, safe='')}"
            f"&stripe_error_description={quote(desc, safe='')}"
        )
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    if error:
        return _redirect_denied()
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state",
        )

    try:
        tenant_id, property_id = decode_oauth_state(settings, state)
    except StripeConnectError as exc:
        raise _stripe_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    factory = request.app.state.async_session_factory
    async with factory() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', CAST(:tid AS text), true)"),
            {"tid": str(tenant_id)},
        )
        try:
            await exchange_code_for_connection(settings, session, code, state)
        except StripeConnectError:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()

    return _redirect_success(property_id)
