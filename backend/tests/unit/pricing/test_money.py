from decimal import Decimal

import pytest

from app.pricing import GlobalSettings, Offer, Product, ProductSettings
from app.pricing.money import (
    ceil_cents,
    floor_cents,
    half_up_cents,
    half_up_tenth,
    money,
    require_decimal,
)

# The only float in the pricing tests: it exists to prove floats are rejected.
A_FLOAT = float("1250.5")


def test_money_accepts_decimal_int_and_string_exactly() -> None:
    assert money(Decimal("1250.50")) == Decimal("1250.50")
    assert money(1250) == Decimal(1250)
    assert money(" 0.1 ") == Decimal("0.1")
    assert money("1250.005") == Decimal("1250.005")  # never rounded on input


@pytest.mark.parametrize("value", [A_FLOAT, True, None, [1]])
def test_money_rejects_non_decimal_types(value: object) -> None:
    with pytest.raises(TypeError):
        money(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "sNaN", "abc", ""])
def test_money_rejects_non_finite_and_garbage(value: str) -> None:
    with pytest.raises(ValueError, match=r"finite|not a decimal"):
        money(value)


def test_require_decimal_rejects_float_int_and_non_finite() -> None:
    with pytest.raises(TypeError):
        require_decimal(A_FLOAT, "x")
    with pytest.raises(TypeError):
        require_decimal(10, "x")
    with pytest.raises(ValueError, match="finite"):
        require_decimal(Decimal("NaN"), "x")


@pytest.mark.parametrize("rounder", [ceil_cents, floor_cents, half_up_cents, half_up_tenth])
def test_rounders_reject_float(rounder: object) -> None:
    assert callable(rounder)
    with pytest.raises(TypeError):
        rounder(A_FLOAT)


@pytest.mark.parametrize(
    ("value", "ceil", "floor", "half_up"),
    [
        ("1169.9883", "1169.99", "1169.98", "1169.99"),
        ("113.0113", "113.02", "113.01", "113.01"),
        ("1240.005", "1240.01", "1240.00", "1240.01"),
        ("1240.0049", "1240.01", "1240.00", "1240.00"),
        ("1200", "1200.00", "1200.00", "1200.00"),
        ("1200.000", "1200.00", "1200.00", "1200.00"),
        ("-0.005", "0.00", "-0.01", "-0.01"),
    ],
)
def test_rounding_to_kopecks(value: str, ceil: str, floor: str, half_up: str) -> None:
    amount = Decimal(value)
    for rounder, expected in ((ceil_cents, ceil), (floor_cents, floor), (half_up_cents, half_up)):
        result = rounder(amount)
        assert result == Decimal(expected)
        assert result.as_tuple().exponent == -2


@pytest.mark.parametrize(
    ("value", "expected"),
    [("-25.1256", "-25.1"), ("-25.15", "-25.2"), ("20.0499", "20.0"), ("0.05", "0.1")],
)
def test_percent_display_rounding(value: str, expected: str) -> None:
    assert half_up_tenth(Decimal(value)) == Decimal(expected)


def test_input_dataclasses_reject_float() -> None:
    with pytest.raises(TypeError):
        Product(brand="Bosch", article="1", purchase_price=A_FLOAT)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Product(
            brand="Bosch",
            article="1",
            purchase_price=Decimal(1000),
            published_price=A_FLOAT,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        ProductSettings(min_markup_percent=A_FLOAT, step=Decimal(10))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ProductSettings(min_markup_percent=Decimal(20), step=A_FLOAT)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ProductSettings(
            min_markup_percent=Decimal(20),
            step=Decimal(10),
            ceiling=A_FLOAT,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        ProductSettings(
            min_markup_percent=Decimal(20),
            step=Decimal(10),
            quarantine_threshold_percent=A_FLOAT,  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        GlobalSettings(suspicious_price_factor=A_FLOAT)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Offer(
            source="s",
            brand="Bosch",
            article="1",
            price=A_FLOAT,  # type: ignore[arg-type]
            stock=1,
            delivery_days=1,
        )


def test_input_dataclasses_reject_non_finite_decimals() -> None:
    with pytest.raises(ValueError, match="finite"):
        Product(brand="Bosch", article="1", purchase_price=Decimal("Infinity"))
    with pytest.raises(ValueError, match="finite"):
        Offer(source="s", brand="B", article="1", price=Decimal("NaN"), stock=1, delivery_days=1)
