"""The formula lines of «Почему цена такая» (``app.pricing.explain``)."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.pricing.calculate import calculate_price
from app.pricing.explain import (
    FormulaInput,
    format_money,
    format_number,
    format_percent,
    formula_lines,
)
from app.pricing.models import (
    Offer,
    PricingResult,
    Product,
    ProductSettings,
    ResultState,
)

D = Decimal


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (D("0"), "0,00"),
        (D("1"), "1,00"),
        (D("999.5"), "999,50"),
        (D("1000"), "1 000,00"),
        (D("1240.00"), "1 240,00"),
        (D("12900.00"), "12 900,00"),
        (D("1234567.89"), "1 234 567,89"),
        (D("1199.995"), "1 199,995"),
        (D("-10.00"), "−10,00"),
    ],
)
def test_format_money(value: Decimal, expected: str) -> None:
    assert format_money(value) == expected


def test_format_number_and_percent() -> None:
    assert format_number(D("0.5000")) == "0,5"
    assert format_number(D("1E+3")) == "1 000"
    assert format_percent(D("20.00")) == "20 %"
    assert format_percent(D("12.50")) == "12,5 %"
    assert format_percent(D("1.6"), places=1) == "1,6 %"
    assert format_percent(D("20"), places=1) == "20,0 %"
    assert format_percent(D("-25.1")) == "−25,1 %"


def _input(result: PricingResult) -> FormulaInput:
    """What the API passes: the stored snapshot of a calculation."""
    return FormulaInput(
        purchase_price=result.product.purchase_price,
        min_markup_percent=result.settings.min_markup_percent,
        step=result.settings.step,
        ceiling=result.settings.ceiling,
        upper_bound=result.ceiling,
        quarantine_threshold_percent=result.settings.quarantine_threshold_percent,
        suspicious_price_factor=result.global_settings.suspicious_price_factor,
        accepted_offers=len(result.accepted_offers),
        rejected_offers=len(result.rejected_offers),
        min_competitor_price=result.min_competitor_price,
        target_price_raw=result.target_price_raw,
        target_price=result.target_price,
        lower_bound=result.lower_bound,
        applied_constraints=result.applied_constraints,
        recommended_price=result.recommended_price,
        previous_price=result.product.published_price,
        change_percent=result.change_percent,
        state=result.state,
        reason=result.reason,
        partial_data=result.partial_data,
    )


def _result(
    *,
    purchase: str = "1000.00",
    published: str | None = "1260.00",
    offers: tuple[str, ...] = ("1250.00",),
    markup: str = "20",
    step: str = "10.00",
    ceiling: str | None = None,
    threshold: str = "20",
    partial: bool = False,
) -> PricingResult:
    product = Product(
        brand="Bosch",
        article="0986452041",
        purchase_price=D(purchase),
        published_price=None if published is None else D(published),
    )
    settings = ProductSettings(
        min_markup_percent=D(markup),
        step=D(step),
        ceiling=None if ceiling is None else D(ceiling),
        quarantine_threshold_percent=D(threshold),
    )
    return calculate_price(
        product,
        settings,
        [Offer("html_catalog", "Bosch", "0986452041", D(p), 5, 2) for p in offers],
        partial_data=partial,
    )


def _lines(**kwargs: object) -> list[str]:
    return formula_lines(_input(_result(**kwargs)))  # type: ignore[arg-type]


def test_control_example_published_at_1240() -> None:
    assert _lines() == [
        "Нижняя граница = 1 000,00 × (1 + 20 %) = 1 200,00 (округление вверх до копейки)",
        "Порог подозрительно низкой цены = 1 000,00 × 0,5 = 500,00: "
        "предложения дешевле отбрасываются",
        "Минимальная цена конкурента = 1 250,00 (пригодных предложений: 1)",
        "Целевая = 1 250,00 − 10,00 = 1 240,00",
        "Итог 1 240,00: ограничений нет",
        "Изменение = |1 240,00 − 1 260,00| / 1 260,00 × 100 = 1,6 % ≤ 20 % → опубликована",
    ]


def test_control_example_clamped_to_the_lower_bound_at_1200() -> None:
    lines = _lines(offers=("1100.00",), published="1210.00")
    assert "Целевая = 1 100,00 − 10,00 = 1 090,00" in lines
    assert (
        "Итог 1 200,00: целевая 1 090,00 ниже нижней границы 1 200,00 → ограничено снизу" in lines
    )
    assert lines[-1] == (
        "Изменение = |1 200,00 − 1 210,00| / 1 210,00 × 100 = 0,8 % ≤ 20 % → опубликована"
    )


def test_price_spike_is_quarantined() -> None:
    lines = _lines(offers=("1500.00",), published="1990.00")
    assert "Целевая = 1 500,00 − 10,00 = 1 490,00" in lines
    assert lines[-1] == (
        "Изменение = |1 490,00 − 1 990,00| / 1 990,00 × 100 = 25,1 % > 20 % → карантин"
    )


def test_ceiling() -> None:
    lines = _lines(
        purchase="400.00", markup="25", ceiling="600.00", offers=("650.00",), published="590.00"
    )
    assert "Нижняя граница = 400,00 × (1 + 25 %) = 500,00 (округление вверх до копейки)" in lines
    assert "Потолок = 600,00" in lines
    assert "Итог 600,00: целевая 640,00 выше потолка 600,00 → ограничено сверху" in lines


def test_ceiling_with_more_decimals_is_rounded_down() -> None:
    lines = _lines(purchase="400.00", ceiling="600.009", offers=("650.00",), published="590.00")
    assert "Потолок = 600,009 → 600,00 (округление вниз до копейки)" in lines


def test_lower_bound_rounded_up_shows_the_exact_value() -> None:
    lines = _lines(purchase="999.99", markup="12.5", offers=("1250.00",), published="1240.00")
    assert lines[0] == (
        "Нижняя граница = 999,99 × (1 + 12,5 %) = 1 124,98875 → 1 124,99 "
        "(округление вверх до копейки)"
    )


def test_target_rounded_half_up_shows_the_exact_value() -> None:
    lines = _lines(step="10.005", offers=("1250.00",))
    assert "Целевая = 1 250,00 − 10,005 = 1 239,995 → 1 240,00 " in lines[3] + " "
    assert lines[3].endswith("(округление до копейки, половина вверх)")


def test_unchanged() -> None:
    lines = _lines(published="1240.00")
    assert lines[-1] == "Итог равен опубликованной цене 1 240,00 → без изменений"


def test_no_previous_price() -> None:
    lines = _lines(published=None)
    assert lines[-1] == (
        "Опубликованной цены нет → карантин: первая рекомендация сама не публикуется"
    )


def test_no_eligible_offers() -> None:
    lines = _lines(offers=())
    assert "Предложений этого товара нет" in lines
    assert lines[-1] == (
        "Новая цена не публикуется (нет пригодных предложений): остаётся опубликованная 1 260,00"
    )
    assert not any(line.startswith(("Целевая", "Итог")) for line in lines)
    only_rejected = _lines(offers=("1.00",), published=None)
    assert "Пригодных предложений нет (отброшено: 1)" in only_rejected
    assert only_rejected[-1] == (
        "Новая цена не публикуется (нет пригодных предложений): опубликованной цены нет"
    )


def test_rules_conflict() -> None:
    assert _lines(ceiling="1100.00") == [
        "Нижняя граница = 1 000,00 × (1 + 20 %) = 1 200,00 (округление вверх до копейки)",
        "Потолок = 1 100,00",
        "Потолок 1 100,00 ниже нижней границы 1 200,00 → "
        "конфликт правил: потолок ниже нижней границы",
        "Новая цена не публикуется (конфликт правил: потолок ниже нижней границы): "
        "остаётся опубликованная 1 260,00",
    ]


def test_invalid_purchase() -> None:
    assert _lines(purchase="0.00") == [
        "Закупка 0,00 не больше нуля",
        "Новая цена не публикуется (некорректная закупка): остаётся опубликованная 1 260,00",
    ]


def test_partial_data_is_the_first_line() -> None:
    lines = _lines(partial=True)
    assert lines[0] == "Расчёт по частичным данным: один из источников в этом прогоне не ответил"
    assert lines[1].startswith("Нижняя граница")


def test_change_close_to_the_threshold_shows_enough_digits() -> None:
    # 1 000 -> 1 200,40 is +20,04 %, above 20 %: "20,0 % > 20 %" would read as a contradiction.
    above = _lines(offers=("1210.40",), published="1000.00")
    assert above[-1] == (
        "Изменение = |1 200,40 − 1 000,00| / 1 000,00 × 100 = 20,04 % > 20 % → карантин"
    )
    exact = _lines(offers=("1210.00",), published="1000.00")
    assert exact[-1] == (
        "Изменение = |1 200,00 − 1 000,00| / 1 000,00 × 100 = 20,0 % ≤ 20 % → опубликована"
    )


def test_formula_without_the_stored_change_computes_it() -> None:
    base = _input(_result())
    assert base.state is ResultState.PUBLISHED
    assert formula_lines(replace(base, change_percent=None)) == formula_lines(base)
