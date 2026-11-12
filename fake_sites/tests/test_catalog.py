from decimal import Decimal

import pytest

from fake_sites.catalog import LISTINGS, Site, normalize_article, search


def test_listings_are_in_stock_and_ship_within_14_days() -> None:
    for item in LISTINGS:
        assert item.stock > 0, item
        assert 0 <= item.delivery_days <= 14, item
        assert item.price > 0, item
        assert item.price == item.price.quantize(Decimal("0.01")), item


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("0 986-452 041", "0986452041"), ("W 712/75", "W71275"), ("bkr6e-11", "BKR6E11")],
)
def test_normalize_article(raw: str, normalized: str) -> None:
    assert normalize_article(raw) == normalized


def test_search_matches_normalized_article_on_one_site() -> None:
    found = search(Site.HTML, "0986452041")

    assert [(item.brand, item.article, item.price) for item in found] == [
        ("BOSCH", "0 986-452 041", Decimal("1250.00")),
        ("MANN", "0986452041", Decimal("1150.00")),
        ("Bosch", "0 986 452 041", Decimal("1.00")),
    ]
    assert search(Site.HTML, "0 986.452/041") == found


@pytest.mark.parametrize("query", ["", "  ", "--", "E340HD247", "NOSUCHPART"])
def test_search_without_matches_is_empty(query: str) -> None:
    assert search(Site.HTML, query) == []
