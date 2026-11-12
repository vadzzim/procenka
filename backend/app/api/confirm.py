"""Confirmation of a quarantined recommendation: POST /api/calculations/{id}/confirm."""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.deps import ConfirmMaxAge, SessionFactory
from app.api.schemas import ConfirmRefusalOut, ErrorOut, PublishedPriceOut
from app.db.confirm import CalculationNotFoundError, ConfirmRefusedError, confirm_calculation
from app.logs import bind_product_id, get_logger

router = APIRouter(prefix="/api")
logger = get_logger("app.api")


@router.post(
    "/calculations/{calculation_id}/confirm",
    response_model=PublishedPriceOut,
    responses={
        404: {"model": ErrorOut, "description": "Расчёт не найден"},
        409: {
            "model": ConfirmRefusalOut,
            "description": "Подтвердить нельзя: код и причина; ничего не опубликовано",
        },
    },
)
async def confirm(
    calculation_id: int, session_factory: SessionFactory, max_age: ConfirmMaxAge
) -> PublishedPriceOut | JSONResponse:
    """Подтвердить рекомендацию из карантина и опубликовать её.

    Можно только рекомендацию последнего завершённого прогона, не старше
    допустимого возраста, если закупка, версия правил, настройки товара и
    опубликованная цена не изменились с момента расчёта. Проверка и запись —
    в одной транзакции.
    """
    try:
        confirmed = await confirm_calculation(session_factory, calculation_id, max_age=max_age)
    except CalculationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Расчёт не найден") from exc
    except ConfirmRefusedError as exc:
        logger.info(
            "quarantine confirmation refused",
            calculation_id=calculation_id,
            refusal=exc.refusal.value,
        )
        body = ConfirmRefusalOut(code=exc.refusal, detail=exc.label)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content=body.model_dump(mode="json")
        )
    with bind_product_id(confirmed.product_id):
        logger.info(
            "quarantined recommendation confirmed and published",
            calculation_id=confirmed.calculation_id,
            price=str(confirmed.price),
        )
    return PublishedPriceOut(
        product_id=confirmed.product_id,
        brand=confirmed.brand,
        article=confirmed.article,
        name=confirmed.name,
        price=confirmed.price,
        published_at=confirmed.published_at,
        calculation_id=confirmed.calculation_id,
    )
