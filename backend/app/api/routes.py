"""HTTP endpoints of runs and published prices."""

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from app.api.deps import SessionFactory
from app.api.runs import load_run_out
from app.api.schemas import (
    ErrorOut,
    PublishedPriceOut,
    PublishedPricesOut,
    RunCreated,
    RunOut,
)
from app.db.enums import RunTrigger
from app.db.models import Product, PublishedPrice, Run
from app.db.runs import enqueue_run

router = APIRouter(prefix="/api")


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


@router.get("/runs/{run_id}", responses={404: {"model": ErrorOut}})
async def get_run(run_id: int, session_factory: SessionFactory) -> RunOut:
    """Статус прогона и результаты источников."""
    async with session_factory() as session:
        run = await load_run_out(session, await session.get(Run, run_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Прогон не найден")
    return run


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
