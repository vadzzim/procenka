import pytest

from app.pricing import normalize_article, normalize_brand
from app.pricing.normalize import BRAND_SYNONYMS


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0 986-452 041", "0986452041"),  # plan.md example
        ("OC 90", "OC90"),  # plan.md example
        ("oc90", "OC90"),
        ("0.986.452.041", "0986452041"),
        ("A 000/180_26-09", "A0001802609"),
        ("  w\t712\\75 ", "W71275"),
        ("0986452041", "0986452041"),
        ("--", ""),
    ],
)
def test_normalize_article(raw: str, expected: str) -> None:
    assert normalize_article(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MB", "Mercedes-Benz"),  # plan.md example
        ("Mercedes-Benz", "Mercedes-Benz"),
        ("  mercedes   benz ", "Mercedes-Benz"),
        ("MANN", "MANN-FILTER"),  # plan.md example
        ("mann-filter", "MANN-FILTER"),
        ("Mann Filter", "MANN-FILTER"),
        ("bosch", "Bosch"),
        ("BOSCH", "Bosch"),
        ("Mahle", "MAHLE"),
        ("febi bilstein", "Febi"),
        ("FEBI", "Febi"),
    ],
)
def test_normalize_brand_synonyms(raw: str, expected: str) -> None:
    assert normalize_brand(raw) == expected


def test_unknown_brand_is_cleaned_upper_case() -> None:
    assert normalize_brand("  Some   Brand ") == "SOME BRAND"
    assert normalize_brand("Lemförder") == "LEMFÖRDER"


def test_canonical_brands_map_to_themselves() -> None:
    for canonical in set(BRAND_SYNONYMS.values()):
        assert normalize_brand(canonical) == canonical
