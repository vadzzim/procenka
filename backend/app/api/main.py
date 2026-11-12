"""The FastAPI application: ``uvicorn app.api.main:app``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes import router
from app.config import get_settings
from app.db.session import create_engine, create_session_factory


def create_app(session_factory: async_sessionmaker[AsyncSession] | None = None) -> FastAPI:
    """With ``session_factory`` (tests) the app uses it; otherwise it owns an engine."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if session_factory is not None:
            yield
            return
        engine = create_engine(get_settings().database_url)
        application.state.session_factory = create_session_factory(engine)
        try:
            yield
        finally:
            await engine.dispose()

    application = FastAPI(title="Проценка", version="0.1.0", lifespan=lifespan)
    if session_factory is not None:
        application.state.session_factory = session_factory
    application.include_router(router)
    return application


app = create_app()
