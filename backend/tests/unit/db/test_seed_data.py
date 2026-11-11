"""The demo set is valid input for the pricing core and has the planned shape."""

from decimal import Decimal

from app.db.seed_data import (
    PRODUCT_A,
    PRODUCT_CEILING,
    PRODUCT_NO_OFFERS,
    PRODUCT_SPIKE,
    PRODUCT_UNPUBLISHED,
    SEED_PRODUCTS,
)
from app.pricing.models import Product, ProductSettings


def _key(brand: str, article: str) -> tuple[str, str]:
    product = Product(brand=brand, article=article, purchase_price=Decimal(1))
    return product.normalized_brand, product.normalized_article


def test_set_size_and_unique_normalized_keys() -> None:
    assert 20 <= len(SEED_PRODUCTS) <= 30
    keys = [_key(item.brand, item.article) for item in SEED_PRODUCTS]
    assert len(set(keys)) == len(keys)


def test_every_product_is_valid_core_input_with_kopeck_money() -> None:
    for item in SEED_PRODUCTS:
        assert isinstance(item.pricing_product(), Product)
        assert isinstance(item.pricing_settings(), ProductSettings)
        assert item.purchase_price > 0
        money = [item.purchase_price, item.step, item.ceiling, item.published_price]
        for value in money:
            # Fits NUMERIC(12,2) without rounding.
            assert value is None or value == value.quantize(Decimal("0.01"))
        for percent in (item.min_markup_percent, item.quarantine_threshold_percent):
            assert percent == percent.quantize(Decimal("0.01"))


def test_dirty_spellings_from_the_plan_are_present() -> None:
    raw_articles = {item.article for item in SEED_PRODUCTS}
    raw_brands = {item.brand for item in SEED_PRODUCTS}
    assert {"0 986-452 041", "OC 90"} <= raw_articles
    assert {"MB", "MANN"} <= raw_brands
    # Raw differs from normalized for most products: normalization is visible.
    changed = [
        item
        for item in SEED_PRODUCTS
        if _key(item.brand, item.article) != (item.brand, item.article)
    ]
    assert len(changed) >= len(SEED_PRODUCTS) // 2


def test_fixed_products() -> None:
    assert _key(PRODUCT_A.brand, PRODUCT_A.article) == ("Bosch", "0986452041")
    assert (
        PRODUCT_A.purchase_price,
        PRODUCT_A.min_markup_percent,
        PRODUCT_A.step,
        PRODUCT_A.ceiling,
        PRODUCT_A.quarantine_threshold_percent,
        PRODUCT_A.published_price,
    ) == (Decimal(1000), Decimal(20), Decimal(10), None, Decimal(20), Decimal(1260))

    assert _key(PRODUCT_SPIKE.brand, PRODUCT_SPIKE.article) == ("MANN-FILTER", "W71275")
    assert (
        PRODUCT_SPIKE.purchase_price,
        PRODUCT_SPIKE.min_markup_percent,
        PRODUCT_SPIKE.step,
        PRODUCT_SPIKE.ceiling,
        PRODUCT_SPIKE.quarantine_threshold_percent,
        PRODUCT_SPIKE.published_price,
    ) == (Decimal(1000), Decimal(20), Decimal(10), None, Decimal(20), Decimal(1990))

    assert PRODUCT_CEILING.ceiling is not None
    assert PRODUCT_UNPUBLISHED.published_price is None
    assert PRODUCT_NO_OFFERS.published_price is not None
    named = [PRODUCT_A, PRODUCT_SPIKE, PRODUCT_CEILING, PRODUCT_UNPUBLISHED, PRODUCT_NO_OFFERS]
    assert all(item in SEED_PRODUCTS for item in named)
    # Only the dedicated product starts without a published price.
    assert [item for item in SEED_PRODUCTS if item.published_price is None] == [PRODUCT_UNPUBLISHED]
