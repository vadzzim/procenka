"""Load the demo product set: ``uv run python -m app.db.seed``.

Idempotent: products are matched by normalized (brand, article); only missing
products are inserted, together with their seed published price. Existing
rows, runs and published prices are left untouched, so running the seed on
every start never undoes a demo in progress. To return to the exact seed
state use ``python -m app.db.reset``. Migrations are not applied here: run
``alembic upgrade head`` first.
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Product, PublishedPrice
from app.db.seed_data import SEED_PRODUCTS, SEED_PUBLISHED_AT
from app.db.session import create_engine, create_session_factory
from app.pricing.normalize import normalize_article, normalize_brand


@dataclass(frozen=True, slots=True)
class SeedReport:
    products_inserted: int
    published_prices_inserted: int


async def seed(session: AsyncSession) -> SeedReport:
    """Insert missing demo products and their published prices; the caller commits."""
    product_rows = [
        {
            "brand_raw": item.brand,
            "article_raw": item.article,
            "brand": normalize_brand(item.brand),
            "article": normalize_article(item.article),
            "name": item.name,
            "purchase_price": item.purchase_price,
            "min_markup_percent": item.min_markup_percent,
            "step": item.step,
            "ceiling": item.ceiling,
            "quarantine_threshold_percent": item.quarantine_threshold_percent,
        }
        for item in SEED_PRODUCTS
    ]
    inserted = (
        await session.execute(
            insert(Product)
            .values(product_rows)
            .on_conflict_do_nothing(index_elements=[Product.brand, Product.article])
            .returning(Product.id, Product.brand, Product.article)
        )
    ).all()
    new_ids = {(row.brand, row.article): row.id for row in inserted}

    price_rows = [
        {
            "product_id": new_ids[key],
            "price": item.published_price,
            "published_at": SEED_PUBLISHED_AT,
            "calculation_id": None,
        }
        for item in SEED_PRODUCTS
        if item.published_price is not None
        and (key := (normalize_brand(item.brand), normalize_article(item.article))) in new_ids
    ]
    if price_rows:
        await session.execute(
            insert(PublishedPrice)
            .values(price_rows)
            .on_conflict_do_nothing(index_elements=[PublishedPrice.product_id])
        )
    return SeedReport(products_inserted=len(new_ids), published_prices_inserted=len(price_rows))


async def _main() -> None:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            report = await seed(session)
    finally:
        await engine.dispose()
    print(
        f"seed: {report.products_inserted} products and "
        f"{report.published_prices_inserted} published prices inserted "
        f"({len(SEED_PRODUCTS)} products in the demo set)"
    )


if __name__ == "__main__":
    asyncio.run(_main())
