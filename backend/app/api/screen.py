"""Endpoints of the screen: status header, products table and «Почему цена такая»."""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import aliased

from app.api.deps import SessionFactory
from app.api.runs import load_run_out
from app.api.schemas import (
    PRICE_ORIGIN_LABELS,
    CalculationInputsOut,
    CalculationOut,
    ConstraintOut,
    ErrorOut,
    LatestCalculationOut,
    OfferOut,
    PriceOrigin,
    ProductOut,
    ProductRowOut,
    ProductsOut,
    PublishedPriceInfo,
    RejectedOfferOut,
    StatusOut,
)
from app.db.enums import ACTIVE_RUN_STATUSES, SOURCE_LABELS, RunStatus, Source
from app.db.models import Calculation, Product, PublishedPrice, Run
from app.pricing.explain import FormulaInput, formula_lines
from app.pricing.models import LABELS, Constraint, OfferRejection

router = APIRouter(prefix="/api")

# Runs whose calculations the screen shows: an interrupted or failed run may have
# recorded calculations, but they were never published and are not the latest word.
SHOWN_RUN_STATUSES = (RunStatus.SUCCEEDED, RunStatus.PARTIAL)


@router.get("/status")
async def get_status(session_factory: SessionFactory) -> StatusOut:
    """Последний и активный прогон; источники последнего завершённого прогона."""
    async with session_factory() as session:
        latest = await session.scalar(select(Run).order_by(Run.id.desc()).limit(1))
        active = await session.scalar(
            select(Run).where(Run.status.in_(ACTIVE_RUN_STATUSES)).order_by(Run.id).limit(1)
        )
        finished = await session.scalar(
            select(Run)
            .where(Run.status.not_in(ACTIVE_RUN_STATUSES))
            .order_by(Run.id.desc())
            .limit(1)
        )
        return StatusOut(
            latest_run=await load_run_out(session, latest),
            active_run=await load_run_out(session, active),
            last_finished_run=await load_run_out(session, finished),
        )


def product_out(product: Product) -> ProductOut:
    return ProductOut(
        id=product.id,
        brand=product.brand,
        article=product.article,
        brand_raw=product.brand_raw,
        article_raw=product.article_raw,
        name=product.name,
    )


@router.get("/products")
async def get_products(session_factory: SessionFactory) -> ProductsOut:
    """Таблица товаров: закупка, опубликованная цена и последний расчёт.

    Последний расчёт берётся только из прогонов succeeded и partial.
    """
    latest_ids = (
        select(Calculation.id, Calculation.product_id)
        .join(Run, Run.id == Calculation.run_id)
        .where(Run.status.in_(SHOWN_RUN_STATUSES))
        .order_by(Calculation.product_id, Calculation.run_id.desc(), Calculation.id.desc())
        .ext(distinct_on(Calculation.product_id))
        .subquery()
    )
    latest = aliased(Calculation, name="latest")
    published_by = aliased(Calculation, name="published_by")
    async with session_factory() as session:
        rows = await session.execute(
            select(Product, PublishedPrice, published_by, latest)
            .outerjoin(PublishedPrice, PublishedPrice.product_id == Product.id)
            .outerjoin(published_by, published_by.id == PublishedPrice.calculation_id)
            .outerjoin(latest_ids, latest_ids.c.product_id == Product.id)
            .outerjoin(latest, latest.id == latest_ids.c.id)
            .order_by(Product.id)
        )
        products = [
            ProductRowOut(
                product=product_out(product),
                purchase_price=product.purchase_price,
                published=_published_info(published, source_calc),
                latest_calculation=_latest_out(calc),
            )
            for product, published, source_calc, calc in rows
        ]
    return ProductsOut(products=products)


def _published_info(
    published: PublishedPrice | None, calculation: Calculation | None
) -> PublishedPriceInfo | None:
    if published is None:
        return None
    origin = PriceOrigin.SEED if calculation is None else PriceOrigin.CALCULATION
    return PublishedPriceInfo(
        price=published.price,
        published_at=published.published_at,
        origin=origin,
        origin_label=PRICE_ORIGIN_LABELS[origin],
        calculation_id=None if calculation is None else calculation.id,
        calculation_run_id=None if calculation is None else calculation.run_id,
        calculated_at=None if calculation is None else calculation.calculated_at,
    )


def _latest_out(calculation: Calculation | None) -> LatestCalculationOut | None:
    if calculation is None:
        return None
    return LatestCalculationOut(
        id=calculation.id,
        run_id=calculation.run_id,
        calculated_at=calculation.calculated_at,
        recommended_price=calculation.recommended_price,
        state=calculation.state,
        state_label=LABELS[calculation.state],
        reason=calculation.reason,
        reason_label=None if calculation.reason is None else LABELS[calculation.reason],
        change_percent_display=calculation.change_percent_display,
        partial_data=calculation.partial_data,
    )


def _offer_fields(offer: dict[str, Any]) -> dict[str, Any]:
    source = Source(offer["source"])
    return {
        "source": source,
        "source_label": SOURCE_LABELS[source],
        "brand_raw": offer["brand"],
        "article_raw": offer["article"],
        "brand": offer["normalized_brand"],
        "article": offer["normalized_article"],
        "price": Decimal(offer["price"]),
        "stock": offer["stock"],
        "delivery_days": offer["delivery_days"],
    }


def _rejected_out(offer: dict[str, Any]) -> RejectedOfferOut:
    reason = OfferRejection(offer["reason"])
    return RejectedOfferOut(**_offer_fields(offer), reason=reason, reason_label=LABELS[reason])


def formula_input(calculation: Calculation) -> FormulaInput:
    return FormulaInput(
        purchase_price=calculation.purchase_price,
        min_markup_percent=calculation.min_markup_percent,
        step=calculation.step,
        ceiling=calculation.ceiling,
        upper_bound=calculation.upper_bound,
        quarantine_threshold_percent=calculation.quarantine_threshold_percent,
        suspicious_price_factor=calculation.suspicious_price_factor,
        accepted_offers=len(calculation.accepted_offers),
        rejected_offers=len(calculation.rejected_offers),
        min_competitor_price=calculation.min_competitor_price,
        target_price_raw=calculation.target_price_raw,
        target_price=calculation.target_price,
        lower_bound=calculation.lower_bound,
        applied_constraints=tuple(Constraint(code) for code in calculation.applied_constraints),
        recommended_price=calculation.recommended_price,
        previous_price=calculation.previous_price,
        change_percent=calculation.change_percent,
        state=calculation.state,
        reason=calculation.reason,
        partial_data=calculation.partial_data,
    )


@router.get("/calculations/{calculation_id}", responses={404: {"model": ErrorOut}})
async def get_calculation(calculation_id: int, session_factory: SessionFactory) -> CalculationOut:
    """«Почему цена такая»: вход, предложения, границы, ограничения, решение и формула."""
    async with session_factory() as session:
        row = (
            await session.execute(
                select(Calculation, Product, Run.status)
                .join(Product, Product.id == Calculation.product_id)
                .join(Run, Run.id == Calculation.run_id)
                .where(Calculation.id == calculation_id)
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Расчёт не найден")
    calculation, product, run_status = row
    constraints = [Constraint(code) for code in calculation.applied_constraints]
    return CalculationOut(
        id=calculation.id,
        run_id=calculation.run_id,
        run_status=run_status,
        product=product_out(product),
        calculated_at=calculation.calculated_at,
        rules_version=calculation.rules_version,
        partial_data=calculation.partial_data,
        inputs=CalculationInputsOut(
            purchase_price=calculation.purchase_price,
            min_markup_percent=calculation.min_markup_percent,
            step=calculation.step,
            ceiling=calculation.ceiling,
            quarantine_threshold_percent=calculation.quarantine_threshold_percent,
            suspicious_price_factor=calculation.suspicious_price_factor,
            max_delivery_days=calculation.max_delivery_days,
        ),
        accepted_offers=[OfferOut(**_offer_fields(o)) for o in calculation.accepted_offers],
        rejected_offers=[_rejected_out(o) for o in calculation.rejected_offers],
        min_competitor_price=calculation.min_competitor_price,
        lower_bound=calculation.lower_bound,
        upper_bound=calculation.upper_bound,
        target_price_raw=calculation.target_price_raw,
        target_price=calculation.target_price,
        applied_constraints=[ConstraintOut(code=c, label=LABELS[c]) for c in constraints],
        recommended_price=calculation.recommended_price,
        previous_price=calculation.previous_price,
        change_percent=calculation.change_percent,
        change_percent_display=calculation.change_percent_display,
        state=calculation.state,
        state_label=LABELS[calculation.state],
        reason=calculation.reason,
        reason_label=None if calculation.reason is None else LABELS[calculation.reason],
        formula=formula_lines(formula_input(calculation)),
    )
