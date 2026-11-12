"""Dependencies shared by the routers: the database and the fake-sites client."""

from datetime import timedelta
from typing import Annotated

import httpx
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


def get_fake_sites(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.fake_sites
    return client


def get_confirm_max_age(request: Request) -> timedelta:
    """QUARANTINE_CONFIRM_MAX_AGE_MINUTES of the application (see app.db.confirm)."""
    max_age: timedelta = request.app.state.confirm_max_age
    return max_age


SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
FakeSites = Annotated[httpx.AsyncClient, Depends(get_fake_sites)]
ConfirmMaxAge = Annotated[timedelta, Depends(get_confirm_max_age)]
