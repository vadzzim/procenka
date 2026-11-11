"""The seed and the pricing core agree on the demo script.

The planned competitor offers below are what fake-sites must serve (steps 4-5);
``docs/progress.md`` ("Шаг 3", "Ожидаемые предложения") mirrors this table.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.config import Settings
from app.db.enums import Source
from app.db.seed_data import (
    PRODUCT_A,
    PRODUCT_CEILING,
    PRODUCT_NO_OFFERS,
    PRODUCT_SPIKE,
    PRODUCT_UNPUBLISHED,
    SEED_PRODUCTS,
    SeedProduct,
)
from app.pricing.calculate import calculate_price
from app.pricing.models import (
    Constraint,
    Offer,
    OfferRejection,
    PricingResult,
    ResultReason,
    ResultState,
)

HTML = Source.HTML_CATALOG.value
JSON = Source.JSON_API.value
GLOBAL = Settings().global_settings()


def _offer(source: str, brand: str, article: str, price: str) -> Offer:
    return Offer(
        source=source, brand=brand, article=article, price=Decimal(price), stock=5, delivery_days=2
    )


# Product A (step 2 of the script): 1 250 in both sources, plus two offers to discard.
A_HTML = (
    _offer(HTML, "BOSCH", "0 986-452 041", "1250.00"),
    _offer(HTML, "MANN", "0986452041", "1150.00"),  # same article, other brand
    _offer(HTML, "Bosch", "0 986 452 041", "1.00"),  # "1 250" parsed as 1: suspiciously low
)
A_JSON = (_offer(JSON, "Bosch", "0986452041", "1250.00"),)

# The price_spike control product: its minimum lives in the JSON source.
SPIKE_HTML = (_offer(HTML, "MANN", "W 712/75", "2100.00"),)
SPIKE_JSON_NORMAL = (_offer(JSON, "MANN-FILTER", "W712/75", "2000.00"),)
SPIKE_JSON_SPIKE = (_offer(JSON, "MANN-FILTER", "W712/75", "1500.00"),)

# Every other product: the minimum competitor price in normal mode, the same in
# both sources, and the expected first-run outcome against the seed price.
OTHER_PLAN: dict[str, tuple[str, ResultState, str]] = {
    # normalized article: (minimum offer, state, recommended price)
    "OC90": ("650.00", ResultState.PUBLISHED, "600.00"),  # capped by the ceiling 600
    "16410": ("1100.00", ResultState.QUARANTINE, "1090.00"),  # no published price
    "A0001802609": ("1190.00", ResultState.UNCHANGED, "1180.00"),
    "A2711800309": ("1470.00", ResultState.PUBLISHED, "1460.00"),
    "06J115403Q": ("960.00", ResultState.UNCHANGED, "950.00"),
    "1K0698151A": ("3390.00", ResultState.PUBLISHED, "3380.00"),
    "90915YZZE1": ("645.00", ResultState.UNCHANGED, "640.00"),
    "0446533471": ("4310.00", ResultState.PUBLISHED, "4300.00"),
    "574105": ("850.00", ResultState.UNCHANGED, "840.00"),
    "826704": ("13100.00", ResultState.PUBLISHED, "13090.00"),
    "C2029": ("890.00", ResultState.UNCHANGED, "880.00"),
    "CUK2939": ("1230.00", ResultState.PUBLISHED, "1220.00"),
    "0242236562": ("625.00", ResultState.UNCHANGED, "620.00"),
    "3397007297": ("1740.00", ResultState.PUBLISHED, "1730.00"),
    "LX17803": ("1100.00", ResultState.UNCHANGED, "1090.00"),
    "BKR6E11": ("400.00", ResultState.UNCHANGED, "395.00"),
    "IK20TT": ("1310.00", ResultState.PUBLISHED, "1300.00"),
    "VKBA3525": ("2850.00", ResultState.UNCHANGED, "2880.00"),  # clamped to the lower bound
    "GDB1550": ("2380.00", ResultState.PUBLISHED, "2370.00"),
    "313410": ("4760.00", ResultState.PUBLISHED, "4750.00"),
    "2595701": ("5700.00", ResultState.UNCHANGED, "5690.00"),
    "H90W03": ("525.00", ResultState.UNCHANGED, "520.00"),
}


def _calculate(
    item: SeedProduct, offers: tuple[Offer, ...], *, partial: bool = False
) -> PricingResult:
    return calculate_price(
        item.pricing_product(),
        item.pricing_settings(),
        offers,
        global_settings=GLOBAL,
        partial_data=partial,
    )


def _with_published(item: SeedProduct, price: str) -> SeedProduct:
    return replace(item, published_price=Decimal(price))


def test_global_settings_match_the_plan() -> None:
    assert GLOBAL.suspicious_price_factor == Decimal("0.5")
    assert GLOBAL.max_delivery_days == 14


def test_product_a_first_run_publishes_1240() -> None:
    result = _calculate(PRODUCT_A, A_HTML + A_JSON)

    assert result.recommended_price == Decimal("1240.00")
    assert result.state is ResultState.PUBLISHED
    assert result.lower_bound == Decimal("1200.00")
    assert result.applied_constraints == ()
    assert [(r.offer.price, r.reason) for r in result.rejected_offers] == [
        (Decimal("1150.00"), OfferRejection.OTHER_BRAND),
        (Decimal("1.00"), OfferRejection.SUSPICIOUSLY_LOW_PRICE),
    ]


def test_product_a_with_html_source_broken_stays_1240() -> None:
    # Steps 3-4 of the script: after step 2 the published price is 1 240.
    result = _calculate(_with_published(PRODUCT_A, "1240.00"), A_JSON, partial=True)

    assert result.recommended_price == Decimal("1240.00")
    assert result.state is ResultState.UNCHANGED
    assert result.partial_data is True


def test_spike_product_normal_is_unchanged_at_1990() -> None:
    result = _calculate(PRODUCT_SPIKE, SPIKE_HTML + SPIKE_JSON_NORMAL)

    assert result.min_competitor_price == Decimal("2000.00")
    assert result.recommended_price == Decimal("1990.00")
    assert result.state is ResultState.UNCHANGED


def test_spike_product_with_html_source_broken_is_unchanged_at_1990() -> None:
    # Seed condition 3: the minimum is in the JSON source.
    result = _calculate(PRODUCT_SPIKE, SPIKE_JSON_NORMAL, partial=True)

    assert result.recommended_price == Decimal("1990.00")
    assert result.state is ResultState.UNCHANGED


def test_spike_product_in_price_spike_mode_goes_to_quarantine_at_1490() -> None:
    result = _calculate(PRODUCT_SPIKE, SPIKE_HTML + SPIKE_JSON_SPIKE)

    assert result.recommended_price == Decimal("1490.00")
    assert result.state is ResultState.QUARANTINE
    assert result.reason is ResultReason.PRICE_SPIKE
    assert result.change_percent_display == Decimal("-25.1")
    # Not clamped by the lower bound and not filtered as suspiciously low.
    assert result.lower_bound == Decimal("1200.00")
    assert result.applied_constraints == ()
    assert result.rejected_offers == ()
    assert result.product.published_price == Decimal("1990.00")


def test_product_without_offers_retains_the_previous_price() -> None:
    result = _calculate(PRODUCT_NO_OFFERS, ())

    assert result.state is ResultState.RETAINED
    assert result.reason is ResultReason.NO_ELIGIBLE_OFFERS
    assert result.recommended_price is None


def test_plan_covers_every_other_product() -> None:
    named = {PRODUCT_A, PRODUCT_SPIKE, PRODUCT_NO_OFFERS}
    articles = {item.pricing_product().normalized_article for item in SEED_PRODUCTS}
    named_articles = {item.pricing_product().normalized_article for item in named}
    assert set(OTHER_PLAN) == articles - named_articles


@pytest.mark.parametrize(
    "item",
    [item for item in SEED_PRODUCTS if item not in (PRODUCT_A, PRODUCT_SPIKE, PRODUCT_NO_OFFERS)],
    ids=lambda item: item.pricing_product().normalized_article,
)
def test_other_products_first_run(item: SeedProduct) -> None:
    minimum, state, recommended = OTHER_PLAN[item.pricing_product().normalized_article]
    offers = (
        _offer(HTML, item.brand, item.article, minimum),
        _offer(JSON, item.brand, item.article, minimum),
    )

    result = _calculate(item, offers)

    assert result.recommended_price == Decimal(recommended)
    assert result.state is state
    # Only the dedicated product lands in quarantine on the first normal run.
    if item is PRODUCT_UNPUBLISHED:
        assert result.reason is ResultReason.NO_PREVIOUS_PRICE
    else:
        assert result.state is not ResultState.QUARANTINE
    if item is PRODUCT_CEILING:
        assert result.applied_constraints == (Constraint.CEILING,)

    # With the HTML source broken, the JSON source alone gives the same answer.
    partial = _calculate(item, offers[1:], partial=True)
    assert partial.recommended_price == result.recommended_price
