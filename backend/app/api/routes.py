"""HTTP endpoints of the demo core."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas import (
    PublishedPriceOut,
    PublishedPricesOut,
    RunCreated,
    RunOut,
    SourceResultOut,
)
from app.db.enums import RunTrigger
from app.db.models import Product, PublishedPrice, Run, SourceResult
from app.db.runs import enqueue_run

router = APIRouter(prefix="/api")


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]


@router.post(
    "/runs",
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": RunCreated, "description": "Прогон уже идёт или ждёт запуска"}},
)
async def create_run(session_factory: SessionFactory, response: Response) -> RunCreated:
    """Поставить ручной прогон в очередь; если прогон уже есть — вернуть существующий."""
    enqueued = await enqueue_run(session_factory, RunTrigger.MANUAL)
    if not enqueued.created:
        response.status_code = status.HTTP_200_OK
    return RunCreated(id=enqueued.id, status=enqueued.status, created=enqueued.created)


@router.get("/runs/{run_id}")
async def get_run(run_id: int, session_factory: SessionFactory) -> RunOut:
    """Статус прогона и результаты источников."""
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Прогон не найден")
        results = await session.scalars(
            select(SourceResult).where(SourceResult.run_id == run_id).order_by(SourceResult.source)
        )
        return RunOut(
            id=run.id,
            status=run.status,
            trigger=run.trigger,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            finish_reason=run.finish_reason,
            sources=[
                SourceResultOut(
                    source=r.source,
                    status=r.status,
                    error_kind=r.error_kind,
                    error_message=r.error_message,
                    offer_count=r.offer_count,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                )
                for r in results
            ],
        )


@router.get("/published-prices")
async def published_prices(session_factory: SessionFactory) -> PublishedPricesOut:
    """Только опубликованные цены; рекомендации из карантина сюда не попадают."""
    async with session_factory() as session:
        rows = await session.execute(
            select(Product, PublishedPrice)
            .join(PublishedPrice, PublishedPrice.product_id == Product.id)
            .order_by(Product.id)
        )
        return PublishedPricesOut(
            prices=[
                PublishedPriceOut(
                    product_id=product.id,
                    brand=product.brand,
                    article=product.article,
                    name=product.name,
                    price=price.price,
                    published_at=price.published_at,
                    calculation_id=price.calculation_id,
                )
                for product, price in rows
            ]
        )
