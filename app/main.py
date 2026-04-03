"""OpenPMS API entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.routes import (
    api_keys,
    assignable_stay,
    audit_log,
    auth,
    bookings,
    dashboard,
    guests,
    housekeeping,
    inventory,
    nightly_rates,
    properties,
    rate_plans,
    room_types,
    rooms,
    unpaid_folio_summary,
    webhooks,
)
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.core.rate_limit import limiter
from app.db.session import create_async_engine_and_sessionmaker
from app.middleware.request_id import RequestIdASGIMiddleware
from app.middleware.tenant_jwt import TenantJwtASGIMiddleware


@limiter.exempt
async def _health_check() -> dict[str, str]:
    return {"status": "ok"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    engine, session_factory = create_async_engine_and_sessionmaker(settings)
    app.state.db_engine = engine
    app.state.async_session_factory = session_factory
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="OpenPMS",
        description=(
            "API-first Property Management System. "
            "Authenticate with **Authorization: Bearer** plus a user JWT (UI) or **X-API-Key** "
            "(integrations; manage keys under /api-keys). "
            "Outbound webhooks POST compact JSON **event** + **data** with header "
            "**X-Webhook-Signature: sha256=...** (HMAC-SHA256 of the raw body bytes, subscription secret)."
        ),
        version="0.1.0",
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "auth",
                "description": "Tenant registration, login, token refresh, user invite, list users.",
            },
            {
                "name": "bookings",
                "description": "Bookings, folio, triggers webhooks on create/update.",
            },
            {"name": "guests", "description": "Guest CRM and profiles."},
            {
                "name": "dashboard",
                "description": "Property operational KPIs (arrivals, departures, occupancy, housekeeping).",
            },
            {
                "name": "housekeeping",
                "description": "Room housekeeping board and status updates.",
            },
            {
                "name": "inventory",
                "description": "Availability grid and blocked-room overrides.",
            },
            {"name": "rates", "description": "Nightly rate read/write."},
            {"name": "properties", "description": "Properties CRUD."},
            {"name": "room-types", "description": "Room types per property."},
            {"name": "rooms", "description": "Physical rooms."},
            {"name": "rate-plans", "description": "Rate plans per property."},
            {
                "name": "api-keys",
                "description": "Integration API keys (JWT-only management).",
            },
            {
                "name": "webhooks",
                "description": "HTTPS webhook subscriptions and delivery logs (JWT-only).",
            },
            {
                "name": "audit",
                "description": "Append-only audit log read API (owner / manager).",
            },
        ],
    )
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @application.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        if isinstance(exc, RequestValidationError):
            return await request_validation_exception_handler(request, exc)
        if isinstance(exc, StarletteHTTPException):
            return await http_exception_handler(request, exc)
        log = structlog.get_logger()
        rid = getattr(request.state, "request_id", None)
        log.exception(
            "unhandled_exception",
            request_id=str(rid) if rid is not None else None,
            path=request.url.path,
            method=request.method,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal Server Error",
                "request_id": str(rid) if rid is not None else None,
            },
        )

    application.add_middleware(TenantJwtASGIMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins(),
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "Cookie",
            # Browsers list these on preflight when a script sets cache-busting headers on XHR.
            "Cache-Control",
            "Pragma",
        ],
        allow_credentials=True,
    )
    application.add_middleware(SlowAPIMiddleware)
    application.add_middleware(RequestIdASGIMiddleware)

    application.include_router(
        auth.router,
        prefix="/auth",
        tags=["auth"],
    )
    application.include_router(
        properties.router,
        prefix="/properties",
        tags=["properties"],
    )
    application.include_router(
        room_types.router,
        prefix="/room-types",
        tags=["room-types"],
    )
    application.include_router(assignable_stay.router)
    application.include_router(unpaid_folio_summary.router)
    application.include_router(inventory.router)
    application.include_router(
        rate_plans.router,
        prefix="/rate-plans",
        tags=["rate-plans"],
    )
    application.include_router(nightly_rates.router)
    application.include_router(
        bookings.router,
        prefix="/bookings",
        tags=["bookings"],
    )
    application.include_router(
        rooms.router,
        prefix="/rooms",
        tags=["rooms"],
    )
    application.include_router(
        guests.router,
        prefix="/guests",
        tags=["guests"],
    )
    application.include_router(
        dashboard.router,
        prefix="/dashboard",
        tags=["dashboard"],
    )
    application.include_router(housekeeping.router)
    application.include_router(
        api_keys.router,
        prefix="/api-keys",
        tags=["api-keys"],
    )
    application.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
    application.include_router(
        audit_log.router,
        prefix="/audit-log",
        tags=["audit"],
    )

    application.add_api_route(
        "/health",
        _health_check,
        methods=["GET"],
        tags=["system"],
    )

    return application


app = create_app()
