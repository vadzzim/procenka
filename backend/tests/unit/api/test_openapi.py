"""The committed OpenAPI schema (``backend/openapi.json``) and its money types."""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest

from app.api.openapi import OPENAPI_PATH, current_schema, render
from app.api.schemas import MONEY_PATTERN, decimal_string, money_string

# Every money field of the API: (schema, property).
MONEY_FIELDS = {
    ("PublishedPriceOut", "price"),
    ("PublishedPriceInfo", "price"),
    ("ProductRowOut", "purchase_price"),
    ("LatestCalculationOut", "recommended_price"),
    ("CalculationInputsOut", "purchase_price"),
    ("CalculationInputsOut", "step"),
    ("CalculationInputsOut", "ceiling"),
    ("OfferOut", "price"),
    ("RejectedOfferOut", "price"),
    ("CalculationOut", "min_competitor_price"),
    ("CalculationOut", "lower_bound"),
    ("CalculationOut", "upper_bound"),
    ("CalculationOut", "target_price_raw"),
    ("CalculationOut", "target_price"),
    ("CalculationOut", "recommended_price"),
    ("CalculationOut", "previous_price"),
}


def test_committed_schema_matches_the_application() -> None:
    """Regenerate with ``uv run --directory backend python -m app.api.openapi``."""
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render(current_schema())


def test_rendering_is_deterministic() -> None:
    assert render(current_schema()) == render(current_schema())


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_no_number_anywhere_in_the_schema() -> None:
    """Money and percentages are strings; the only numbers are integer ids and counts."""
    assert [node for node in _walk(current_schema()) if node.get("type") == "number"] == []


def _variants(prop: dict[str, Any]) -> list[dict[str, Any]]:
    return [v for v in prop.get("anyOf", [prop]) if v.get("type") != "null"]


def test_money_fields_are_strings_with_two_decimals() -> None:
    schemas = current_schema()["components"]["schemas"]
    for schema, field in MONEY_FIELDS:
        (variant,) = _variants(schemas[schema]["properties"][field])
        assert variant["type"] == "string", (schema, field)
        assert variant["pattern"] == MONEY_PATTERN, (schema, field)
    # The list above is complete: no other field uses the money type.
    found = {
        (name, field)
        for name, schema in schemas.items()
        for field, prop in schema.get("properties", {}).items()
        if any(v.get("pattern") == MONEY_PATTERN for v in _variants(prop))
    }
    assert found == MONEY_FIELDS


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("1240"), "1240.00"), (Decimal("1240.5"), "1240.50"), (Decimal("0.00"), "0.00")],
)
def test_money_string(value: Decimal, expected: str) -> None:
    assert money_string(value) == expected


def test_money_string_never_rounds() -> None:
    with pytest.raises(ValueError, match="more than two decimals"):
        money_string(Decimal("1240.005"))


def test_decimal_string_is_plain() -> None:
    assert decimal_string(Decimal("-25.1")) == "-25.1"
    assert decimal_string(Decimal("1E+1")) == "10"
