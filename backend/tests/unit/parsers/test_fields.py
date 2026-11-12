from decimal import Decimal

import pytest

from app.db.enums import SourceErrorKind
from app.parsers.base import SourceError
from app.parsers.fields import parse_count, parse_price, require_text

NBSP = chr(0xA0)
NNBSP = chr(0x202F)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 250,00 ₽", "1250.00"),
        (f"1{NBSP}250,00{NBSP}₽", "1250.00"),
        (f"13{NNBSP}100,50 ₽", "13100.50"),
        ("1,00 ₽", "1.00"),
        ("650", "650"),
        ("650,5", "650.5"),
        ("  2 100,00 ₽  ", "2100.00"),
        ("0,00 ₽", "0.00"),
    ],
)
def test_parse_price(text: str, expected: str) -> None:
    price = parse_price(text)
    assert price == Decimal(expected)
    assert str(price) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "₽",
        "по запросу",
        "1.250,00 ₽",  # dot as a thousands separator is not this site's format
        "1 250,00 $",
        "1 250,00 ₽ ₽",
        "1e3",
        "NaN",
        "Infinity",
        "1 250,005 ₽",  # three decimals: never rounded
        "1250,001",
        "-1 250,00 ₽",
        "-0,01",
        "12345678901,00",  # does not fit NUMERIC(12,2)
        "١٢٥٠",  # non-ASCII digits
    ],
)
def test_unparsable_price_is_a_structure_error_never_zero(text: str) -> None:
    with pytest.raises(SourceError) as excinfo:
        parse_price(text)
    assert excinfo.value.kind is SourceErrorKind.STRUCTURE
    assert "цена" in excinfo.value.message


@pytest.mark.parametrize(
    ("text", "expected"),
    [("12 шт.", 12), ("12 шт", 12), ("3 дн.", 3), ("0 шт.", 0), ("7", 7), (f"40{NBSP}шт.", 40)],
)
def test_parse_count(text: str, expected: int) -> None:
    assert parse_count(text, field="остаток") == expected


def test_largest_count_still_fits_integer() -> None:
    assert parse_count("2147483647 шт.", field="остаток") == 2_147_483_647


@pytest.mark.parametrize(
    "text", ["", "много", "-3 шт.", "2,5 шт.", "3 недели", "٣", "2147483648 шт.", "9" * 30]
)
def test_unparsable_count_is_a_structure_error(text: str) -> None:
    with pytest.raises(SourceError) as excinfo:
        parse_count(text, field="остаток")
    assert excinfo.value.kind is SourceErrorKind.STRUCTURE


@pytest.mark.parametrize("text", [None, "", "  \n "])
def test_required_text_missing(text: str | None) -> None:
    with pytest.raises(SourceError) as excinfo:
        require_text(text, field="бренд")
    assert excinfo.value.kind is SourceErrorKind.STRUCTURE
    assert "бренд" in excinfo.value.message
