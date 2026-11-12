"""The "why is the price like this" formula: Russian lines with the numbers filled in.

Pure functions over the stored snapshot of a calculation (no database, no
I/O), so the server builds the text once and the UI only shows it. Numbers
are formatted the Russian way: thousands separated by spaces, a decimal comma,
``−`` (U+2212) for minus and subtraction, a space before ``%``.

Money is never rounded for display: an amount is shown with at least two
decimals and with every further digit it has.
"""

from dataclasses import dataclass
from decimal import Context, Decimal, localcontext

from app.pricing.models import LABELS, Constraint, ResultReason, ResultState
from app.pricing.money import HUNDRED, half_up_tenth

MINUS = "−"
_CONTEXT = Context(prec=28)


def _plain(value: Decimal) -> str:
    """Digits of a finite Decimal in fixed-point notation, never in exponent form."""
    return format(value, "f")


def format_number(value: Decimal, *, min_places: int = 0) -> str:
    """``1234567.5`` -> ``1 234 567,5``; at least ``min_places`` decimals, none dropped."""
    text = _plain(abs(value))
    whole, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0").ljust(min_places, "0")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    result = " ".join(groups)
    if fraction:
        result += "," + fraction
    return (MINUS if value < 0 else "") + result


def format_money(value: Decimal) -> str:
    """``1240`` -> ``1 240,00``; extra digits are shown, never rounded away."""
    return format_number(value, min_places=2)


def format_percent(value: Decimal, *, places: int | None = None) -> str:
    """``20.00`` -> ``20 %``; with ``places`` exactly that many decimals: ``1,6 %``."""
    if places is not None:
        return f"{format_number(value, min_places=places)} %"
    return f"{format_number(value)} %"


@dataclass(frozen=True, slots=True)
class FormulaInput:
    """What a stored calculation knows; money and percentages are exact Decimals."""

    purchase_price: Decimal
    min_markup_percent: Decimal
    step: Decimal
    # The ceiling setting and the ceiling rounded down to a kopeck (``upper_bound``).
    ceiling: Decimal | None
    upper_bound: Decimal | None
    quarantine_threshold_percent: Decimal
    suspicious_price_factor: Decimal
    accepted_offers: int
    rejected_offers: int
    min_competitor_price: Decimal | None
    target_price_raw: Decimal | None
    target_price: Decimal | None
    lower_bound: Decimal
    applied_constraints: tuple[Constraint, ...]
    recommended_price: Decimal | None
    previous_price: Decimal | None
    change_percent: Decimal | None
    state: ResultState
    reason: ResultReason | None
    partial_data: bool


def _shown_change(change: Decimal, threshold: Decimal) -> str:
    """|change| to 0.1 %, with more digits when 0.1 % would look equal to the threshold."""
    absolute = abs(change)
    shown = half_up_tenth(absolute)
    if shown == threshold and absolute != threshold:
        return format_percent(absolute)
    return format_percent(shown, places=1)


def _lower_bound_line(data: FormulaInput) -> str:
    with localcontext(_CONTEXT):
        exact = data.purchase_price * (1 + data.min_markup_percent / HUNDRED)
    head = (
        f"Нижняя граница = {format_money(data.purchase_price)} × "
        f"(1 + {format_percent(data.min_markup_percent)})"
    )
    if exact == data.lower_bound:
        return f"{head} = {format_money(data.lower_bound)} (округление вверх до копейки)"
    return (
        f"{head} = {format_number(exact)} → {format_money(data.lower_bound)} "
        "(округление вверх до копейки)"
    )


def _suspicious_line(data: FormulaInput) -> str:
    with localcontext(_CONTEXT):
        limit = data.purchase_price * data.suspicious_price_factor
    return (
        f"Порог подозрительно низкой цены = {format_money(data.purchase_price)} × "
        f"{format_number(data.suspicious_price_factor)} = {format_money(limit)}: "
        "предложения дешевле отбрасываются"
    )


def _ceiling_line(data: FormulaInput) -> str | None:
    if data.ceiling is None or data.upper_bound is None:
        return None
    if data.ceiling == data.upper_bound:
        return f"Потолок = {format_money(data.upper_bound)}"
    return (
        f"Потолок = {format_number(data.ceiling)} → {format_money(data.upper_bound)} "
        "(округление вниз до копейки)"
    )


def _target_line(data: FormulaInput) -> str | None:
    if data.min_competitor_price is None or data.target_price is None:
        return None
    head = f"Целевая = {format_money(data.min_competitor_price)} {MINUS} {format_money(data.step)}"
    if data.target_price_raw is None or data.target_price_raw == data.target_price:
        return f"{head} = {format_money(data.target_price)}"
    return (
        f"{head} = {format_number(data.target_price_raw)} → {format_money(data.target_price)} "
        "(округление до копейки, половина вверх)"
    )


def _result_line(data: FormulaInput) -> str | None:
    if data.recommended_price is None or data.target_price is None:
        return None
    total = f"Итог {format_money(data.recommended_price)}"
    if not data.applied_constraints:
        return f"{total}: ограничений нет"
    parts = []
    for constraint in data.applied_constraints:
        if constraint is Constraint.LOWER_BOUND:
            parts.append(
                f"целевая {format_money(data.target_price)} ниже нижней границы "
                f"{format_money(data.lower_bound)} → {LABELS[constraint]}"
            )
        elif data.upper_bound is not None:
            parts.append(
                f"целевая {format_money(data.target_price)} выше потолка "
                f"{format_money(data.upper_bound)} → {LABELS[constraint]}"
            )
    return f"{total}: {'; '.join(parts)}"


def _kept(data: FormulaInput) -> str:
    if data.previous_price is None:
        return "опубликованной цены нет"
    return f"остаётся опубликованная {format_money(data.previous_price)}"


def _decision_line(data: FormulaInput) -> str:
    state = LABELS[data.state]
    if data.state is ResultState.RETAINED:
        reason = "" if data.reason is None else f" ({LABELS[data.reason]})"
        return f"Новая цена не публикуется{reason}: {_kept(data)}"
    if data.previous_price is None or data.recommended_price is None:
        return f"Опубликованной цены нет → {state}: первая рекомендация сама не публикуется"
    if data.state is ResultState.UNCHANGED:
        return f"Итог равен опубликованной цене {format_money(data.previous_price)} → {state}"
    change = data.change_percent
    if change is None:
        with localcontext(_CONTEXT):
            change = (data.recommended_price - data.previous_price) / data.previous_price * HUNDRED
    operator = ">" if data.state is ResultState.QUARANTINE else "≤"
    return (
        f"Изменение = |{format_money(data.recommended_price)} {MINUS} "
        f"{format_money(data.previous_price)}| / {format_money(data.previous_price)} × 100 = "
        f"{_shown_change(change, data.quarantine_threshold_percent)} {operator} "
        f"{format_percent(data.quarantine_threshold_percent)} → {state}"
    )


def formula_lines(data: FormulaInput) -> list[str]:
    """The calculation step by step, as the manager reads it."""
    lines: list[str] = []
    if data.partial_data:
        lines.append("Расчёт по частичным данным: один из источников в этом прогоне не ответил")
    if data.reason is ResultReason.INVALID_PURCHASE_PRICE:
        lines.append(f"Закупка {format_money(data.purchase_price)} не больше нуля")
        lines.append(_decision_line(data))
        return lines

    lines.append(_lower_bound_line(data))
    ceiling = _ceiling_line(data)
    if ceiling is not None:
        lines.append(ceiling)
    if data.reason is ResultReason.RULES_CONFLICT and data.upper_bound is not None:
        lines.append(
            f"Потолок {format_money(data.upper_bound)} ниже нижней границы "
            f"{format_money(data.lower_bound)} → {LABELS[ResultReason.RULES_CONFLICT]}"
        )
        lines.append(_decision_line(data))
        return lines

    lines.append(_suspicious_line(data))
    if data.min_competitor_price is None:
        lines.append(
            f"Пригодных предложений нет (отброшено: {data.rejected_offers})"
            if data.rejected_offers
            else "Предложений этого товара нет"
        )
    else:
        lines.append(
            f"Минимальная цена конкурента = {format_money(data.min_competitor_price)} "
            f"(пригодных предложений: {data.accepted_offers})"
        )
    for line in (_target_line(data), _result_line(data)):
        if line is not None:
            lines.append(line)
    lines.append(_decision_line(data))
    return lines
