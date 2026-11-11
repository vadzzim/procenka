"""Seed and reset are repeatable and return the demo to exactly the seed state."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import RunStatus, RunTrigger, Source, SourceStatus
from app.db.models import Base, Calculation, Offer, Product, PublishedPrice, Run, SourceResult
from app.db.reset import reset
from app.db.seed import SeedReport, seed
from app.db.seed_data import (
    PRODUCT_A,
    PRODUCT_SPIKE,
    SEED_PRODUCTS,
    SEED_PUBLISHED_AT,
    SeedProduct,
)
from app.pricing.models import RULES_VERSION, ResultState
from app.pricing.normalize import normalize_article, normalize_brand

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
SEEDED_PRICES = sum(item.published_price is not None for item in SEED_PRODUCTS)
FULL_SEED = SeedReport(
    products_inserted=len(SEED_PRODUCTS), published_prices_inserted=SEEDED_PRICES
)

type Snapshot = dict[str, list[tuple[Any, ...]]]


async def _snapshot(session: AsyncSession) -> Snapshot:
    """Every row of every table, ordered by primary key."""
    result: Snapshot = {}
    for table in Base.metadata.sorted_tables:
        rows = await session.execute(select(table).order_by(*table.primary_key.columns))
        result[table.name] = [tuple(row) for row in rows]
    return result


async def _product(session: AsyncSession, item: SeedProduct) -> Product:
    result = await session.execute(
        select(Product).where(
            Product.brand == normalize_brand(item.brand),
            Product.article == normalize_article(item.article),
        )
    )
    return result.scalar_one()


async def _published_price(session: AsyncSession, product_id: int) -> PublishedPrice | None:
    return await session.get(PublishedPrice, product_id, populate_existing=True)


async def _assert_fixed_products(session: AsyncSession) -> None:
    product_a = await _product(session, PRODUCT_A)
    assert (product_a.brand_raw, product_a.article_raw) == ("BOSCH", "0 986-452 041")
    assert (product_a.brand, product_a.article) == ("Bosch", "0986452041")
    assert product_a.purchase_price == Decimal("1000.00")
    assert product_a.min_markup_percent == Decimal("20")
    assert product_a.step == Decimal("10.00")
    assert product_a.ceiling is None
    assert product_a.quarantine_threshold_percent == Decimal("20")
    price_a = await _published_price(session, product_a.id)
    assert price_a is not None
    assert (price_a.price, price_a.published_at, price_a.calculation_id) == (
        Decimal("1260.00"),
        SEED_PUBLISHED_AT,
        None,
    )

    spike = await _product(session, PRODUCT_SPIKE)
    assert (spike.brand_raw, spike.article_raw) == ("MANN", "W 712/75")
    assert (spike.brand, spike.article) == ("MANN-FILTER", "W71275")
    assert spike.purchase_price == Decimal("1000.00")
    assert spike.min_markup_percent == Decimal("20")
    assert spike.step == Decimal("10.00")
    assert spike.ceiling is None
    assert spike.quarantine_threshold_percent == Decimal("20")
    price_spike = await _published_price(session, spike.id)
    assert price_spike is not None
    assert price_spike.price == Decimal("1990.00")


async def _make_demo_mess(session: AsyncSession) -> None:
    """What a demo leaves behind: runs, results, offers, calculations, new prices."""
    product_a = await _product(session, PRODUCT_A)
    run = Run(
        status=RunStatus.SUCCEEDED, trigger=RunTrigger.MANUAL, started_at=NOW, finished_at=NOW
    )
    session.add(run)
    await session.flush()
    session.add(
        SourceResult(
            run_id=run.id,
            source=Source.HTML_CATALOG,
            status=SourceStatus.OK,
            offer_count=1,
            started_at=NOW,
            finished_at=NOW,
        )
    )
    session.add(
        Offer(
            run_id=run.id,
            source=Source.HTML_CATALOG,
            brand_raw="BOSCH",
            article_raw="0 986-452 041",
            brand="Bosch",
            article="0986452041",
            price=Decimal("1250.00"),
            stock=3,
            delivery_days=2,
            product_id=product_a.id,
        )
    )
    calculation = Calculation(
        run_id=run.id,
        product_id=product_a.id,
        rules_version=RULES_VERSION,
        purchase_price=Decimal("1000.00"),
        min_markup_percent=Decimal("20"),
        step=Decimal("10.00"),
        quarantine_threshold_percent=Decimal("20"),
        suspicious_price_factor=Decimal("0.5"),
        max_delivery_days=14,
        previous_price=Decimal("1260.00"),
        partial_data=False,
        accepted_offers=[],
        rejected_offers=[],
        lower_bound=Decimal("1200.00"),
        applied_constraints=[],
        recommended_price=Decimal("1240.00"),
        state=ResultState.PUBLISHED,
    )
    session.add(calculation)
    await session.flush()
    await session.execute(
        update(PublishedPrice)
        .where(PublishedPrice.product_id == product_a.id)
        .values(price=Decimal("1240.00"), published_at=NOW, calculation_id=calculation.id)
    )
    # Settings edited by hand, a price removed and a product added outside the seed.
    await session.execute(
        update(Product).where(Product.id == product_a.id).values(purchase_price=Decimal("1.00"))
    )
    spike = await _product(session, PRODUCT_SPIKE)
    await session.execute(delete(PublishedPrice).where(PublishedPrice.product_id == spike.id))
    session.add(
        Product(
            brand_raw="X",
            article_raw="1",
            brand="X",
            article="1",
            name="extra",
            purchase_price=Decimal("1.00"),
            min_markup_percent=Decimal("0"),
            step=Decimal("0"),
            quarantine_threshold_percent=Decimal("0"),
        )
    )
    await session.commit()


async def test_seed_loads_the_demo_set(db_session: AsyncSession) -> None:
    assert await seed(db_session) == FULL_SEED
    await db_session.commit()

    assert await db_session.scalar(select(func.count()).select_from(Product)) == len(SEED_PRODUCTS)
    assert await db_session.scalar(select(func.count()).select_from(PublishedPrice)) == (
        SEEDED_PRICES
    )
    await _assert_fixed_products(db_session)


async def test_seed_twice_gives_the_same_state(db_session: AsyncSession) -> None:
    await seed(db_session)
    await db_session.commit()
    first = await _snapshot(db_session)

    assert await seed(db_session) == SeedReport(products_inserted=0, published_prices_inserted=0)
    await db_session.commit()

    assert await _snapshot(db_session) == first
    await _assert_fixed_products(db_session)


async def test_seed_does_not_undo_a_demo_in_progress(db_session: AsyncSession) -> None:
    await seed(db_session)
    await db_session.commit()
    await _make_demo_mess(db_session)
    before = await _snapshot(db_session)

    await seed(db_session)
    await db_session.commit()

    assert await _snapshot(db_session) == before


async def test_reset_returns_exactly_the_seed_state(db_session: AsyncSession) -> None:
    await seed(db_session)
    await db_session.commit()
    seeded = await _snapshot(db_session)

    await _make_demo_mess(db_session)
    assert await _snapshot(db_session) != seeded

    assert await reset(db_session) == FULL_SEED
    await db_session.commit()

    assert await _snapshot(db_session) == seeded
    await _assert_fixed_products(db_session)


async def test_reset_is_idempotent_and_works_on_an_empty_database(
    db_session: AsyncSession,
) -> None:
    await reset(db_session)
    await db_session.commit()
    first = await _snapshot(db_session)

    await reset(db_session)
    await db_session.commit()

    assert await _snapshot(db_session) == first
    assert len(first["products"]) == len(SEED_PRODUCTS)
    assert all(
        not rows for name, rows in first.items() if name not in {"products", "published_prices"}
    )
