"""fake-sites serves exactly the planned offers (docs/progress.md, step 3)."""

from decimal import Decimal

import pytest
from fake_sites.catalog import LISTINGS, Listing, Site

from app.db.enums import Source
from app.db.seed_data import PRODUCT_A, PRODUCT_NO_OFFERS, PRODUCT_SPIKE, SEED_PRODUCTS, SeedProduct
from app.pricing.calculate import calculate_price
from app.pricing.models import Offer, OfferRejection, PricingResult, ResultState
from app.pricing.normalize import normalize_article
from tests.unit.db.test_seed_consistency import GLOBAL, OTHER_PLAN

SITE_SOURCE = {Site.HTML: Source.HTML_CATALOG.value, Site.JSON: Source.JSON_API.value}


def _offers(item: SeedProduct, sites: tuple[Site, ...]) -> list[Offer]:
    article = normalize_article(item.article)
    return [
        Offer(
            source=SITE_SOURCE[listing.site],
            brand=listing.brand,
            article=listing.article,
            price=listing.price,
            stock=listing.stock,
            delivery_days=listing.delivery_days,
        )
        for listing in LISTINGS
        if listing.site in sites and normalize_article(listing.article) == article
    ]


def _calculate(
    item: SeedProduct, sites: tuple[Site, ...] = (Site.HTML, Site.JSON)
) -> PricingResult:
    return calculate_price(
        item.pricing_product(),
        item.pricing_settings(),
        _offers(item, sites),
        global_settings=GLOBAL,
        partial_data=len(sites) == 1,
    )


def _plain(listing: Listing) -> tuple[str, str, str, Decimal]:
    return (listing.site.value, listing.brand, listing.article, listing.price)


def test_product_a_listings_match_the_plan() -> None:
    article = normalize_article(PRODUCT_A.article)
    listings = [_plain(i) for i in LISTINGS if normalize_article(i.article) == article]
    assert listings == [
        ("html", "BOSCH", "0 986-452 041", Decimal("1250.00")),
        ("html", "MANN", "0986452041", Decimal("1150.00")),
        ("html", "Bosch", "0 986 452 041", Decimal("1.00")),
        ("json", "Bosch", "0986452041", Decimal("1250.00")),
    ]


def test_product_a_publishes_1240_from_the_html_source_alone() -> None:
    result = _calculate(PRODUCT_A, (Site.HTML,))

    assert result.recommended_price == Decimal("1240.00")
    assert result.state is ResultState.PUBLISHED
    assert [r.reason for r in result.rejected_offers] == [
        OfferRejection.OTHER_BRAND,
        OfferRejection.SUSPICIOUSLY_LOW_PRICE,
    ]


def test_spike_product_minimum_is_in_the_json_source() -> None:
    assert _calculate(PRODUCT_SPIKE).recommended_price == Decimal("1990.00")
    assert _calculate(PRODUCT_SPIKE).state is ResultState.UNCHANGED
    assert _calculate(PRODUCT_SPIKE, (Site.JSON,)).state is ResultState.UNCHANGED


def test_product_without_offers_is_sold_nowhere() -> None:
    assert _offers(PRODUCT_NO_OFFERS, (Site.HTML, Site.JSON)) == []


def test_every_listing_belongs_to_a_seed_product() -> None:
    articles = {normalize_article(item.article) for item in SEED_PRODUCTS}
    assert {normalize_article(listing.article) for listing in LISTINGS} <= articles


@pytest.mark.parametrize(
    "item",
    [item for item in SEED_PRODUCTS if item not in (PRODUCT_A, PRODUCT_SPIKE, PRODUCT_NO_OFFERS)],
    ids=lambda item: normalize_article(item.article),
)
@pytest.mark.parametrize("sites", [(Site.HTML, Site.JSON), (Site.HTML,), (Site.JSON,)])
def test_other_products_follow_the_plan_in_each_source(
    item: SeedProduct, sites: tuple[Site, ...]
) -> None:
    minimum, state, recommended = OTHER_PLAN[normalize_article(item.article)]

    result = _calculate(item, sites)

    assert result.min_competitor_price == Decimal(minimum)
    assert result.recommended_price == Decimal(recommended)
    assert result.state is state
    # Dirty spellings normalize to the product: nothing is rejected.
    assert result.rejected_offers == ()
