"""Strict parsing of values shown on competitor sites.

Anything that does not parse exactly is a structure error: a price that could
not be read never becomes 0, and a missing field never becomes "no offer".
"""

import re
from decimal import Decimal, InvalidOperation

from app.db.enums import SourceErrorKind
from app.parsers.base import SourceError

# Regular, non-breaking, narrow non-breaking and thin spaces used as thousands separators.
_SPACES = re.compile("[ " + chr(0xA0) + chr(0x202F) + chr(0x2009) + "]+")
RUBLE_SIGN = chr(0x20BD)
_NUMBER = re.compile(r"-?\d+(\.\d+)?", re.ASCII)
# Counts are stored as PostgreSQL INTEGER; a larger value is not a real stock or delivery time.
MAX_COUNT = 2_147_483_647
_COUNT = re.compile(r"(-?\d+)\s*(шт\.?|дн\.?)?", re.ASCII)


def structure_error(message: str) -> SourceError:
    return SourceError(SourceErrorKind.STRUCTURE, message)


def parse_price(text: str, *, field: str = "цена") -> Decimal:
    """``"1 250,00 ₽"`` -> ``Decimal("1250.00")``.

    Rejects empty values, other formats, negative prices and more than two
    decimals (prices are stored as NUMERIC(12,2) and are never rounded silently).
    """
    value = _SPACES.sub("", text.strip())
    value = value.removesuffix(RUBLE_SIGN).replace(",", ".")
    if not _NUMBER.fullmatch(value):
        raise structure_error(f"не удалось разобрать поле «{field}»: {text!r}")
    try:
        price = Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - the regex admits only valid numbers
        raise structure_error(f"не удалось разобрать поле «{field}»: {text!r}") from exc
    if price < 0:
        raise structure_error(f"отрицательное значение в поле «{field}»: {text!r}")
    exponent = price.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        raise structure_error(f"больше двух знаков после запятой в поле «{field}»: {text!r}")
    if price >= Decimal("10000000000"):
        raise structure_error(f"слишком большое значение в поле «{field}»: {text!r}")
    return price


def parse_count(text: str, *, field: str) -> int:
    """``"12 шт."`` -> ``12``, ``"3 дн."`` -> ``3``; negative or other formats are errors."""
    match = _COUNT.fullmatch(_SPACES.sub(" ", text.strip()))
    if match is None:
        raise structure_error(f"не удалось разобрать поле «{field}»: {text!r}")
    count = int(match.group(1))
    if count < 0:
        raise structure_error(f"отрицательное значение в поле «{field}»: {text!r}")
    if count > MAX_COUNT:
        raise structure_error(f"слишком большое значение в поле «{field}»: {text!r}")
    return count


def require_text(text: str | None, *, field: str) -> str:
    """A required text field: present and not blank."""
    if text is None or not text.strip():
        raise structure_error(f"нет обязательного поля «{field}»")
    return text.strip()
