"""Normalization of articles and brands.

Functions return only the normalized value; callers keep the original next to it.
"""

import re
from types import MappingProxyType

_WHITESPACE = re.compile(r"\s+")

# Brand synonyms of the demo data set only. Keys are cleaned upper-case
# spellings (see ``_brand_key``), values are canonical names. Every canonical
# name also maps to itself.
_CANONICAL_BRANDS: dict[str, tuple[str, ...]] = {
    "Bosch": ("BOSCH", "ROBERT BOSCH"),
    "MANN-FILTER": ("MANN", "MANN FILTER", "MANNFILTER"),
    "Mercedes-Benz": ("MB", "MERCEDES", "MERCEDES BENZ", "MERCEDESBENZ"),
    "MAHLE": ("MAHLE ORIGINAL", "KNECHT"),
    "Febi": ("FEBI BILSTEIN", "FEBI-BILSTEIN"),
    "Hengst": ("HENGST FILTER",),
    "Valeo": (),
    "Toyota": (),
    "Volkswagen": ("VW",),
}


def _brand_key(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip()).upper()


def _build_synonyms() -> dict[str, str]:
    table: dict[str, str] = {}
    for canonical, synonyms in _CANONICAL_BRANDS.items():
        for spelling in (canonical, *synonyms):
            key = _brand_key(spelling)
            if key in table and table[key] != canonical:
                raise ValueError(f"brand synonym {spelling!r} maps to two brands")
            table[key] = canonical
    return table


BRAND_SYNONYMS = MappingProxyType(_build_synonyms())


def normalize_article(value: str) -> str:
    """Keep only letters and digits, upper-cased.

    ``0 986-452 041`` -> ``0986452041``, ``OC 90`` -> ``OC90``. Spaces and
    separators (``-``, ``.``, ``/``, ``_``, ``\\`` and the like) are removed.
    """
    return "".join(ch for ch in value if ch.isalnum()).upper()


def normalize_brand(value: str) -> str:
    """Trim, collapse whitespace, upper-case and resolve demo synonyms.

    ``MB`` -> ``Mercedes-Benz``, ``mann`` -> ``MANN-FILTER``. An unknown brand
    becomes its cleaned upper-case form, never an error.
    """
    key = _brand_key(value)
    return BRAND_SYNONYMS.get(key, key)
