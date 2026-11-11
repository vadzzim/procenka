"""The demo product set: 25 products with settings and seed published prices.

Brands and articles are raw, deliberately "dirty" spellings; the seed stores
them next to their normalized form. The planned competitor offers that pair
with this set (what fake-sites will serve) are listed in ``docs/progress.md``
("Шаг 3") and checked against the pricing core in
``tests/unit/db/test_seed_consistency.py``.

Named products are the fixed points of the demo script (``plan.md``,
"Сценарий показа"):

* ``PRODUCT_A`` — step 2: published 1 260, the first run publishes 1 240;
* ``PRODUCT_SPIKE`` — the price_spike control product: published 1 990,
  quarantine at 1 490 (-25.1 %) when the JSON source drops 2 000 -> 1 500;
* ``PRODUCT_CEILING`` — the recommendation is capped by the ceiling;
* ``PRODUCT_UNPUBLISHED`` — no published price: the first calculation is quarantined;
* ``PRODUCT_NO_OFFERS`` — no competitor offers: the previous price is retained.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.pricing.models import Product, ProductSettings

# Fixed so that seed and reset always produce exactly the same rows.
SEED_PUBLISHED_AT = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SeedProduct:
    brand: str
    article: str
    name: str
    purchase_price: Decimal
    min_markup_percent: Decimal
    step: Decimal
    ceiling: Decimal | None
    quarantine_threshold_percent: Decimal
    published_price: Decimal | None

    def pricing_product(self) -> Product:
        return Product(
            brand=self.brand,
            article=self.article,
            purchase_price=self.purchase_price,
            published_price=self.published_price,
        )

    def pricing_settings(self) -> ProductSettings:
        return ProductSettings(
            min_markup_percent=self.min_markup_percent,
            step=self.step,
            ceiling=self.ceiling,
            quarantine_threshold_percent=self.quarantine_threshold_percent,
        )


def _product(
    brand: str,
    article: str,
    name: str,
    *,
    purchase: str,
    published: str | None,
    markup: str = "20",
    step: str = "10",
    ceiling: str | None = None,
    threshold: str = "20",
) -> SeedProduct:
    return SeedProduct(
        brand=brand,
        article=article,
        name=name,
        purchase_price=Decimal(purchase),
        min_markup_percent=Decimal(markup),
        step=Decimal(step),
        ceiling=None if ceiling is None else Decimal(ceiling),
        quarantine_threshold_percent=Decimal(threshold),
        published_price=None if published is None else Decimal(published),
    )


PRODUCT_A = _product(
    "BOSCH", "0 986-452 041", "Фильтр масляный", purchase="1000.00", published="1260.00"
)
PRODUCT_SPIKE = _product(
    "MANN", "W 712/75", "Фильтр масляный", purchase="1000.00", published="1990.00"
)
PRODUCT_CEILING = _product(
    "Mahle Original",
    "OC 90",
    "Фильтр масляный",
    purchase="400.00",
    markup="25",
    ceiling="600.00",
    published="590.00",
)
PRODUCT_UNPUBLISHED = _product(
    "febi bilstein", "16 410", "Колодки тормозные передние", purchase="800.00", published=None
)
PRODUCT_NO_OFFERS = _product(
    "Hengst", "E340H D247", "Фильтр масляный", purchase="500.00", markup="25", published="690.00"
)

SEED_PRODUCTS: tuple[SeedProduct, ...] = (
    PRODUCT_A,
    PRODUCT_SPIKE,
    PRODUCT_CEILING,
    PRODUCT_UNPUBLISHED,
    PRODUCT_NO_OFFERS,
    _product("MB", "A 000 180 26 09", "Фильтр масляный", purchase="900.00", published="1180.00"),
    _product(
        "MERCEDES-BENZ",
        "A 271 180 03 09",
        "Фильтр масляный",
        purchase="1100.00",
        published="1450.00",
    ),
    _product("VW", "06J 115 403 Q", "Фильтр масляный", purchase="700.00", published="950.00"),
    _product(
        "Volkswagen", "1K0-698-151-A", "Колодки тормозные", purchase="2500.00", published="3350.00"
    ),
    _product(
        "TOYOTA",
        "90915-YZZE1",
        "Фильтр масляный",
        purchase="450.00",
        markup="25",
        step="5",
        published="640.00",
    ),
    _product("Toyota", "04465-33471", "Колодки тормозные", purchase="3200.00", published="4290.00"),
    _product(
        "Valeo",
        "574 105",
        "Щётка стеклоочистителя",
        purchase="600.00",
        markup="25",
        published="840.00",
    ),
    _product(
        "VALEO",
        "826 704",
        "Комплект сцепления",
        purchase="9800.00",
        markup="15",
        threshold="15",
        published="12900.00",
    ),
    _product("MANN-FILTER", "C 2029", "Фильтр воздушный", purchase="650.00", published="880.00"),
    _product(
        "Mann Filter",
        "CUK 2939",
        "Фильтр салонный угольный",
        purchase="900.00",
        published="1240.00",
    ),
    _product(
        "BOSCH",
        "0 242 236 562",
        "Свеча зажигания",
        purchase="450.00",
        markup="25",
        step="5",
        published="620.00",
    ),
    _product(
        "Robert Bosch",
        "3 397 007 297",
        "Щётки стеклоочистителя, комплект",
        purchase="1300.00",
        published="1720.00",
    ),
    _product("KNECHT", "LX 1780/3", "Фильтр воздушный", purchase="800.00", published="1090.00"),
    _product(
        "NGK",
        "BKR6E-11",
        "Свеча зажигания",
        purchase="250.00",
        markup="30",
        step="5",
        published="395.00",
    ),
    _product(
        "DENSO",
        "IK20TT",
        "Свеча зажигания иридиевая",
        purchase="900.00",
        markup="25",
        published="1290.00",
    ),
    # The competitor is below the lower bound 2 880: the result is clamped to it.
    _product("SKF", "VKBA 3525", "Подшипник ступицы", purchase="2400.00", published="2880.00"),
    _product("TRW", "GDB1550", "Колодки тормозные", purchase="1800.00", published="2390.00"),
    _product("SACHS", "313 410", "Амортизатор задний", purchase="3500.00", published="4700.00"),
    _product("LEMFORDER", "25957 01", "Рычаг подвески", purchase="4200.00", published="5690.00"),
    _product(
        "Hengst Filter",
        "H90W03",
        "Фильтр масляный",
        purchase="380.00",
        markup="25",
        step="5",
        published="520.00",
    ),
)
