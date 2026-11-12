"""Executing one run: collect, record, calculate, publish.

Order of work (``plan.md``, "Прогоны и устойчивость"):

1. A short read transaction loads the articles to search for and is closed.
2. Every source is collected over HTTP with no database session at all.
3. Transaction 1 writes source results, offers (one batch insert) and one
   calculation per product, made from the offers of this run only.
4. Transaction 2, short, takes the publication lock (``app.db.publication``),
   finishes the run and publishes the calculations in state ``published`` with
   ``INSERT ... ON CONFLICT (product_id) DO UPDATE``. A product whose published
   price changed after transaction 1 read it (a quarantined recommendation was
   confirmed in between) is calculated again from the same offers against the
   price published now, and its calculation row is updated first.

Run status: every source ok -> succeeded; some failed -> partial (calculations
carry ``partial_data``); all failed -> failed, no offers or calculations are
written and published prices do not change.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.enums import RunFinishReason, RunStatus, SourceStatus
from app.db.models import Calculation, Offer, Product, PublishedPrice, Run, SourceResult
from app.db.publication import Publication, lock_publication, upsert_published_prices
from app.logs import bind_product_id, get_logger
from app.parsers.base import OfferSource, SourceCollection, collect_all
from app.pricing import models as pricing
from app.pricing.calculate import calculate_price
from app.pricing.normalize import normalize_article, normalize_brand

logger = get_logger("app.worker")


class RunNotRunningError(RuntimeError):
    """The run is not in ``running`` any more; nothing of it is published."""


@dataclass(frozen=True, slots=True)
class RunOutcome:
    run_id: int
    status: RunStatus
    calculations: int
    published: int


@dataclass(frozen=True, slots=True)
class _ProductInput:
    id: int
    product: pricing.Product
    settings: pricing.ProductSettings


def run_status(collections: Sequence[SourceCollection]) -> RunStatus:
    failed = sum(not collection.ok for collection in collections)
    if failed == 0:
        return RunStatus.SUCCEEDED
    if failed < len(collections):
        return RunStatus.PARTIAL
    return RunStatus.FAILED


async def load_articles(session_factory: async_sessionmaker[AsyncSession]) -> list[str]:
    """Distinct normalized articles of all products; the transaction is closed on return."""
    async with session_factory() as session, session.begin():
        articles = await session.scalars(
            select(Product.article).distinct().order_by(Product.article)
        )
        return list(articles)


async def execute_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: int,
    sources: Sequence[OfferSource],
    *,
    global_settings: pricing.GlobalSettings,
) -> RunOutcome:
    """Execute a run that is already ``running`` (claimed by the worker)."""
    articles = await load_articles(session_factory)
    # No session exists while the sources are collected over HTTP.
    collections = await collect_all(sources, articles)
    return await record_run(session_factory, run_id, collections, global_settings=global_settings)


async def record_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: int,
    collections: Sequence[SourceCollection],
    *,
    global_settings: pricing.GlobalSettings,
) -> RunOutcome:
    status = run_status(collections)
    calculated: list[_Calculated] = []
    async with session_factory() as session:
        async with session.begin():
            await _insert_source_results(session, run_id, collections)
            if status is not RunStatus.FAILED:
                products = await _load_products(session)
                offers = await _insert_offers(session, run_id, collections, products)
                calculated = await _calculate(
                    session,
                    run_id,
                    products,
                    offers,
                    global_settings=global_settings,
                    partial_data=status is RunStatus.PARTIAL,
                )
        async with session.begin():
            calculated = await finish_and_publish(session, run_id, status, calculated)
        publications = _publications(calculated)
    return RunOutcome(
        run_id=run_id, status=status, calculations=len(calculated), published=len(publications)
    )


async def finish_and_publish(
    session: AsyncSession, run_id: int, status: RunStatus, calculated: Sequence["_Calculated"]
) -> list["_Calculated"]:
    """Transaction 2 of a run (the caller commits): lock, finish, refresh, publish."""
    await lock_publication(session)
    await _finish_run(session, run_id, status)
    refreshed = await _recalculate_stale(session, calculated)
    await upsert_published_prices(session, _publications(refreshed))
    return refreshed


async def _insert_source_results(
    session: AsyncSession, run_id: int, collections: Sequence[SourceCollection]
) -> None:
    rows = [
        {
            "run_id": run_id,
            "source": collection.source,
            "status": SourceStatus.OK if collection.ok else SourceStatus.FAILED,
            "error_kind": collection.error.kind if collection.error else None,
            "error_message": collection.error.message if collection.error else None,
            "offer_count": len(collection.offers),
            "started_at": collection.started_at,
            "finished_at": collection.finished_at,
        }
        for collection in collections
    ]
    if rows:
        await session.execute(insert(SourceResult).values(rows))


async def _load_products(session: AsyncSession) -> list[_ProductInput]:
    rows = await session.execute(
        select(Product, PublishedPrice.price)
        .outerjoin(PublishedPrice, PublishedPrice.product_id == Product.id)
        .order_by(Product.id)
    )
    return [
        _ProductInput(
            id=product.id,
            product=pricing.Product(
                brand=product.brand_raw,
                article=product.article_raw,
                purchase_price=product.purchase_price,
                published_price=published_price,
            ),
            settings=pricing.ProductSettings(
                min_markup_percent=product.min_markup_percent,
                step=product.step,
                ceiling=product.ceiling,
                quarantine_threshold_percent=product.quarantine_threshold_percent,
            ),
        )
        for product, published_price in rows
    ]


async def _insert_offers(
    session: AsyncSession,
    run_id: int,
    collections: Sequence[SourceCollection],
    products: Sequence[_ProductInput],
) -> list[pricing.Offer]:
    """Write all offers of the run in one batch; return them as pricing inputs."""
    product_ids = {
        (item.product.normalized_brand, item.product.normalized_article): item.id
        for item in products
    }
    rows: list[dict[str, Any]] = []
    offers: list[pricing.Offer] = []
    for collection in collections:
        for parsed in collection.offers:
            brand, article = normalize_brand(parsed.brand), normalize_article(parsed.article)
            rows.append(
                {
                    "run_id": run_id,
                    "source": collection.source,
                    "brand_raw": parsed.brand,
                    "article_raw": parsed.article,
                    "brand": brand,
                    "article": article,
                    "price": parsed.price,
                    "stock": parsed.stock,
                    "delivery_days": parsed.delivery_days,
                    "product_id": product_ids.get((brand, article)),
                }
            )
            offers.append(
                pricing.Offer(
                    source=collection.source.value,
                    brand=parsed.brand,
                    article=parsed.article,
                    price=parsed.price,
                    stock=parsed.stock,
                    delivery_days=parsed.delivery_days,
                )
            )
    if rows:
        await session.execute(insert(Offer).values(rows))
    return offers


def _offer_json(offer: pricing.Offer) -> dict[str, Any]:
    return {
        "source": offer.source,
        "brand": offer.brand,
        "article": offer.article,
        "normalized_brand": normalize_brand(offer.brand),
        "normalized_article": normalize_article(offer.article),
        "price": str(offer.price),
        "stock": offer.stock,
        "delivery_days": offer.delivery_days,
    }


def calculation_row(run_id: int, product_id: int, result: pricing.PricingResult) -> dict[str, Any]:
    """A ``calculations`` row; JSON money is strings (see ``app.db.models``)."""
    return {
        "run_id": run_id,
        "product_id": product_id,
        "rules_version": result.rules_version,
        "purchase_price": result.product.purchase_price,
        "min_markup_percent": result.settings.min_markup_percent,
        "step": result.settings.step,
        "ceiling": result.settings.ceiling,
        "quarantine_threshold_percent": result.settings.quarantine_threshold_percent,
        "suspicious_price_factor": result.global_settings.suspicious_price_factor,
        "max_delivery_days": result.global_settings.max_delivery_days,
        "previous_price": result.product.published_price,
        "partial_data": result.partial_data,
        "accepted_offers": [_offer_json(offer) for offer in result.accepted_offers],
        "rejected_offers": [
            {**_offer_json(item.offer), "reason": item.reason.value}
            for item in result.rejected_offers
        ],
        "min_competitor_price": result.min_competitor_price,
        "target_price_raw": result.target_price_raw,
        "target_price": result.target_price,
        "lower_bound": result.lower_bound,
        "upper_bound": result.ceiling,
        "applied_constraints": [constraint.value for constraint in result.applied_constraints],
        "recommended_price": result.recommended_price,
        "state": result.state,
        "reason": result.reason,
        "change_percent": result.change_percent,
        "change_percent_display": result.change_percent_display,
    }


@dataclass(frozen=True, slots=True)
class _Calculated:
    """A calculation written in transaction 1, with what is needed to redo it."""

    calculation_id: int
    item: _ProductInput
    offers: tuple[pricing.Offer, ...]
    result: pricing.PricingResult


def _price(
    item: _ProductInput,
    offers: Sequence[pricing.Offer],
    *,
    global_settings: pricing.GlobalSettings,
    partial_data: bool,
) -> pricing.PricingResult:
    with bind_product_id(item.id):
        result = calculate_price(
            item.product,
            item.settings,
            offers,
            global_settings=global_settings,
            partial_data=partial_data,
        )
        logger.info(
            "price calculated",
            state=result.state.value,
            reason=None if result.reason is None else result.reason.value,
            recommended_price=None
            if result.recommended_price is None
            else str(result.recommended_price),
            accepted_offers=len(result.accepted_offers),
            rejected_offers=len(result.rejected_offers),
            partial_data=partial_data,
        )
    return result


async def _calculate(
    session: AsyncSession,
    run_id: int,
    products: Sequence[_ProductInput],
    offers: Sequence[pricing.Offer],
    *,
    global_settings: pricing.GlobalSettings,
    partial_data: bool,
) -> list[_Calculated]:
    """Calculate every product from this run's offers with its article; the core
    then accepts or rejects each offer (other brand, suspicious price, ...)."""
    by_article: defaultdict[str, list[pricing.Offer]] = defaultdict(list)
    for offer in offers:
        by_article[normalize_article(offer.article)].append(offer)

    pending: dict[int, tuple[_ProductInput, tuple[pricing.Offer, ...], pricing.PricingResult]] = {}
    for item in products:
        product_offers = tuple(by_article.get(item.product.normalized_article, ()))
        result = _price(
            item, product_offers, global_settings=global_settings, partial_data=partial_data
        )
        pending[item.id] = (item, product_offers, result)
    if not pending:
        return []
    inserted = await session.execute(
        insert(Calculation)
        .values([calculation_row(run_id, pid, result) for pid, (_, _, result) in pending.items()])
        .returning(Calculation.id, Calculation.product_id)
    )
    calculated = [
        _Calculated(calculation_id, *pending[product_id]) for calculation_id, product_id in inserted
    ]
    calculated.sort(key=lambda calc: calc.item.id)
    return calculated


async def _recalculate_stale(
    session: AsyncSession, calculated: Sequence[_Calculated]
) -> list[_Calculated]:
    """Redo the calculations whose published price changed since transaction 1.

    Called under the publication lock, so the published prices read here stay
    as they are until the commit. Outside a run only a confirmed quarantine
    changes a published price; the new calculation compares the recommendation
    with the price that is actually published now (same offers, same settings).
    """
    if not calculated:
        return []
    rows = await session.execute(select(PublishedPrice.product_id, PublishedPrice.price))
    current: dict[int, Decimal] = {product_id: price for product_id, price in rows}
    refreshed: list[_Calculated] = []
    for calc in calculated:
        read_price = calc.item.product.published_price
        published_now = current.get(calc.item.id)
        if published_now == read_price:
            refreshed.append(calc)
            continue
        item = replace(calc.item, product=replace(calc.item.product, published_price=published_now))
        with bind_product_id(item.id):
            logger.warning(
                "published price changed during the run; calculating again",
                read_price=None if read_price is None else str(read_price),
                published_price=None if published_now is None else str(published_now),
            )
        result = _price(
            item,
            calc.offers,
            global_settings=calc.result.global_settings,
            partial_data=calc.result.partial_data,
        )
        row = calculation_row(0, item.id, result)
        del row["run_id"], row["product_id"]
        await session.execute(
            update(Calculation).where(Calculation.id == calc.calculation_id).values(**row)
        )
        refreshed.append(_Calculated(calc.calculation_id, item, calc.offers, result))
    return refreshed


def _publications(calculated: Sequence[_Calculated]) -> list[Publication]:
    publications = []
    for calc in calculated:
        if calc.result.state is pricing.ResultState.PUBLISHED:
            assert calc.result.recommended_price is not None
            publications.append(
                Publication(calc.item.id, calc.calculation_id, calc.result.recommended_price)
            )
    return publications


async def _finish_run(session: AsyncSession, run_id: int, status: RunStatus) -> None:
    finish_reason = RunFinishReason.ALL_SOURCES_FAILED if status is RunStatus.FAILED else None
    finished = await session.scalar(
        update(Run)
        .where(Run.id == run_id, Run.status == RunStatus.RUNNING)
        .values(status=status, finished_at=func.now(), finish_reason=finish_reason)
        .returning(Run.id)
    )
    if finished is None:
        raise RunNotRunningError(f"run {run_id} is not running; nothing is published")


async def fail_run(
    session_factory: async_sessionmaker[AsyncSession], run_id: int, reason: RunFinishReason
) -> None:
    """Mark a running run failed (used after an unexpected error in the worker)."""
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == RunStatus.RUNNING)
            .values(status=RunStatus.FAILED, finished_at=func.now(), finish_reason=reason)
        )
