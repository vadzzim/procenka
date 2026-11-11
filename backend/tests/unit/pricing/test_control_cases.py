"""Control cases from ``tests/data/control_cases.csv`` (the "Сверка" section of plan.md)."""

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from app.pricing import (
    Offer,
    OfferRejection,
    Product,
    ProductSettings,
    ResultReason,
    ResultState,
    calculate_price,
    money,
)

CONTROL_CASES_PATH = Path(__file__).resolve().parents[2] / "data" / "control_cases.csv"
MIN_CONTROL_CASES = 15


@dataclass(frozen=True)
class ControlCase:
    id: str
    raw: dict[str, str]
    product: Product
    settings: ProductSettings
    offers: tuple[Offer, ...]
    expected_price: Decimal | None
    expected_state: ResultState
    expected_reason: ResultReason | None
    expected_rejected: tuple[OfferRejection, ...]


def _optional_money(value: str) -> Decimal | None:
    return money(value) if value.strip() else None


def _parse_offers(value: str) -> tuple[Offer, ...]:
    offers = []
    for chunk in filter(None, (part.strip() for part in value.split(";"))):
        brand, article, price, stock, days = chunk.split("|")
        offers.append(
            Offer(
                source="control_cases",
                brand=brand,
                article=article,
                price=money(price),
                stock=int(stock),
                delivery_days=int(days),
            )
        )
    return tuple(offers)


def _parse_row(row: dict[str, str]) -> ControlCase:
    ceiling = _optional_money(row["ceiling"])
    return ControlCase(
        id=row["id"],
        raw=row,
        product=Product(
            brand=row["brand"],
            article=row["article"],
            purchase_price=money(row["purchase"]),
            published_price=_optional_money(row["published"]),
        ),
        settings=ProductSettings(
            min_markup_percent=money(row["markup_percent"]),
            step=money(row["step"]),
            ceiling=ceiling,
            quarantine_threshold_percent=money(row["threshold_percent"]),
        ),
        offers=_parse_offers(row["offers"]),
        expected_price=_optional_money(row["expected_price"]),
        expected_state=ResultState(row["expected_state"]),
        expected_reason=ResultReason(row["expected_reason"]) if row["expected_reason"] else None,
        expected_rejected=tuple(
            OfferRejection(code) for code in row["expected_rejected"].split(";") if code
        ),
    )


def load_control_cases() -> list[ControlCase]:
    with CONTROL_CASES_PATH.open(encoding="utf-8", newline="") as file:
        return [_parse_row(row) for row in csv.DictReader(file)]


CONTROL_CASES = load_control_cases()


def test_control_cases_file_is_substantial() -> None:
    ids = [case.id for case in CONTROL_CASES]
    assert len(ids) >= MIN_CONTROL_CASES
    assert len(ids) == len(set(ids)), "control case ids must be unique"


@pytest.mark.parametrize("case", CONTROL_CASES, ids=[case.id for case in CONTROL_CASES])
def test_control_case(case: ControlCase) -> None:
    result = calculate_price(case.product, case.settings, case.offers)

    expected = (
        case.expected_price,
        case.expected_state,
        case.expected_reason,
        case.expected_rejected,
    )
    actual = (
        result.recommended_price,
        result.state,
        result.reason,
        tuple(rejected.reason for rejected in result.rejected_offers),
    )
    if actual != expected:
        inputs = "\n".join(f"  {key}: {value}" for key, value in case.raw.items())
        pytest.fail(
            f"control case {case.id!r} mismatched\n"
            f"inputs:\n{inputs}\n"
            f"expected: price={expected[0]} state={expected[1]} reason={expected[2]} "
            f"rejected={[str(r) for r in expected[3]]}\n"
            f"actual:   price={actual[0]} state={actual[1]} reason={actual[2]} "
            f"rejected={[str(r) for r in actual[3]]}\n"
            f"details:  lower_bound={result.lower_bound} ceiling={result.ceiling} "
            f"min_competitor={result.min_competitor_price} "
            f"target_raw={result.target_price_raw} target={result.target_price} "
            f"change={result.change_percent_display}%",
            pytrace=False,
        )
    if result.recommended_price is not None:
        # Recommendations always carry exactly two decimal places.
        assert result.recommended_price.as_tuple().exponent == -2
