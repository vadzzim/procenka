"""Money helpers: Decimal only, explicit rounding to kopecks.

Floats are rejected everywhere money or percentages enter the pricing core:
a float such as ``0.1`` cannot represent a price exactly, so accepting one
would silently change the result of rounding.
"""

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
TENTH = Decimal("0.1")
HUNDRED = Decimal(100)


def require_decimal(value: object, name: str) -> Decimal:
    """Return ``value`` if it is a finite Decimal; otherwise raise.

    ``float`` and anything that is not a ``Decimal`` raise ``TypeError``;
    NaN and infinities raise ``ValueError``.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal, got {value}")
    return value


def money(value: Decimal | int | str) -> Decimal:
    """Build an exact Decimal amount from a Decimal, an int or a decimal string.

    The value is not rounded: rounding is a pricing rule and happens only
    where ``plan.md`` prescribes it.
    """
    if isinstance(value, bool) or not isinstance(value, Decimal | int | str):
        raise TypeError(f"money accepts Decimal, int or str, got {type(value).__name__}")
    if isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal number: {value!r}") from exc
    else:
        result = Decimal(value)
    return require_decimal(result, "money")


def ceil_cents(value: Decimal) -> Decimal:
    """Round up to a kopeck (ROUND_CEILING): used for the lower bound."""
    return require_decimal(value, "value").quantize(CENT, rounding=ROUND_CEILING)


def floor_cents(value: Decimal) -> Decimal:
    """Round down to a kopeck (ROUND_FLOOR): used for the ceiling."""
    return require_decimal(value, "value").quantize(CENT, rounding=ROUND_FLOOR)


def half_up_cents(value: Decimal) -> Decimal:
    """Round to the nearest kopeck, halves away from zero: used for the target price."""
    return require_decimal(value, "value").quantize(CENT, rounding=ROUND_HALF_UP)


def half_up_tenth(value: Decimal) -> Decimal:
    """Round to 0.1 with ROUND_HALF_UP: used only to display a percentage."""
    return require_decimal(value, "value").quantize(TENTH, rounding=ROUND_HALF_UP)
