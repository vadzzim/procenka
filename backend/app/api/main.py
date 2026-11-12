"""The FastAPI application: ``uvicorn app.api.main:app``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import confirm, demo, health, routes, screen
from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.logs import configure_logging

# Control requests to fake-sites are local and quick.
FAKE_SITES_TIMEOUT_SECONDS = 5.0


def create_app(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    fake_sites_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """With ``session_factory`` (tests) the app uses it; otherwise it owns an engine.

    ``fake_sites_transport`` (tests) replaces the network to fake-sites.
    """
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            if session_factory is not None:
                yield
                return
            configure_logging("api")
            engine = create_engine(settings.database_url)
            application.state.session_factory = create_session_factory(engine)
            try:
                yield
            finally:
                await engine.dispose()
        finally:
            await application.state.fake_sites.aclose()

    application = FastAPI(title="Проценка", version="0.1.0", lifespan=lifespan)
    if session_factory is not None:
        application.state.session_factory = session_factory
    application.state.confirm_max_age = timedelta(
        minutes=settings.quarantine_confirm_max_age_minutes
    )
    application.state.fake_sites = httpx.AsyncClient(
        base_url=settings.fake_sites_url,
        timeout=FAKE_SITES_TIMEOUT_SECONDS,
        transport=fake_sites_transport,
    )
    application.include_router(health.router)
    application.include_router(routes.router)
    application.include_router(screen.router)
    application.include_router(confirm.router)
    application.include_router(demo.router)
    return application


app = create_app()
