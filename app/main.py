"""OpenPMS API entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import bookings, inventory, properties, room_types
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
    application = FastAPI(
        title="OpenPMS",
        description="API-first Property Management System",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(TenantJwtMiddleware)

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
        bookings.router,
        prefix="/bookings",
        tags=["bookings"],
    )

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
