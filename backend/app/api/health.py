"""``GET /health``: 200 when the database answers ``SELECT 1``, otherwise 503."""

import asyncio

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import SessionFactory
from app.api.schemas import HealthOut
from app.logs import get_logger

router = APIRouter()
logger = get_logger("app.api")

HEALTH_TIMEOUT_SECONDS = 3.0


@router.get(
    "/health",
    responses={503: {"model": HealthOut, "description": "База данных не отвечает"}},
)
async def health(session_factory: SessionFactory, response: Response) -> HealthOut:
    """Живо ли приложение: база данных отвечает на ``SELECT 1``."""
    try:
        async with asyncio.timeout(HEALTH_TIMEOUT_SECONDS), session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("health check failed", error=repr(exc))
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthOut(status="unavailable")
    return HealthOut(status="ok")
