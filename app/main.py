"""OpenPMS API entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    api_keys,
    auth,
    bookings,
    guests,
    housekeeping,
    inventory,
    nightly_rates,
    properties,
    rate_plans,
    room_types,
    rooms,
    webhooks,
)
from app.core.config import get_settings
from app.db.session import create_async_engine_and_sessionmaker
from app.middleware.tenant_jwt import TenantJwtMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine, session_factory = create_async_engine_and_sessionmaker(
        settings.database_url,
    )
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
            {"name": "auth", "description": "Tenant registration, login, token refresh, user invite."},
            {"name": "bookings", "description": "Bookings, folio, triggers webhooks on create/update."},
            {"name": "guests", "description": "Guest CRM and profiles."},
            {"name": "housekeeping", "description": "Room housekeeping board and status updates."},
            {"name": "inventory", "description": "Availability grid and blocked-room overrides."},
            {"name": "rates", "description": "Nightly rate read/write."},
            {"name": "properties", "description": "Properties CRUD."},
            {"name": "room-types", "description": "Room types per property."},
            {"name": "rooms", "description": "Physical rooms."},
            {"name": "rate-plans", "description": "Rate plans per property."},
            {"name": "api-keys", "description": "Integration API keys (JWT-only management)."},
            {"name": "webhooks", "description": "HTTPS webhook subscriptions and delivery logs (JWT-only)."},
        ],
    )
    application.add_middleware(TenantJwtMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

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
    application.include_router(housekeeping.router)
    application.include_router(
        api_keys.router,
        prefix="/api-keys",
        tags=["api-keys"],
    )
    application.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
