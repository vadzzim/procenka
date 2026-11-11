"""Inputs, outcome codes and the result of one price calculation.

Percentages (markup, quarantine threshold) are stored in percent: ``20`` means
20 %. The suspicious-low-price factor is a plain fraction: ``0.5``.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from app.pricing.money import require_decimal
from app.pricing.normalize import normalize_article, normalize_brand

RULES_VERSION = "2026-11-12.1"


class OfferRejection(StrEnum):
    """Why an offer was not used. Checked in this order; the first match wins."""

    OTHER_ARTICLE = "other_article"
    OTHER_BRAND = "other_brand"
    NON_POSITIVE_PRICE = "non_positive_price"
    OUT_OF_STOCK = "out_of_stock"
    DELIVERY_TOO_LONG = "delivery_too_long"
    SUSPICIOUSLY_LOW_PRICE = "suspiciously_low_price"


class ResultState(StrEnum):
    PUBLISHED = "published"
    UNCHANGED = "unchanged"
    QUARANTINE = "quarantine"
    RETAINED = "retained"


class ResultReason(StrEnum):
    NO_PREVIOUS_PRICE = "no_previous_price"
    PRICE_SPIKE = "price_spike"
    NO_ELIGIBLE_OFFERS = "no_eligible_offers"
    INVALID_PURCHASE_PRICE = "invalid_purchase_price"
    RULES_CONFLICT = "rules_conflict"


class Constraint(StrEnum):
    LOWER_BOUND = "lower_bound"
    CEILING = "ceiling"


type Code = OfferRejection | ResultState | ResultReason | Constraint

# The single table of Russian labels the UI shows for every code.
LABELS: MappingProxyType[Code, str] = MappingProxyType(
    {
        OfferRejection.OTHER_ARTICLE: "другой артикул",
        OfferRejection.OTHER_BRAND: "другой бренд",
        OfferRejection.NON_POSITIVE_PRICE: "цена не больше нуля",
        OfferRejection.OUT_OF_STOCK: "нет в наличии",
        OfferRejection.DELIVERY_TOO_LONG: "срок поставки больше допустимого",
        OfferRejection.SUSPICIOUSLY_LOW_PRICE: "подозрительно низкая цена",
        ResultState.PUBLISHED: "опубликована",
        ResultState.UNCHANGED: "без изменений",
        ResultState.QUARANTINE: "карантин",
        ResultState.RETAINED: "оставлена предыдущая",
        ResultReason.NO_PREVIOUS_PRICE: "нет предыдущей цены",
        ResultReason.PRICE_SPIKE: "скачок цены",
        ResultReason.NO_ELIGIBLE_OFFERS: "нет пригодных предложений",
        ResultReason.INVALID_PURCHASE_PRICE: "некорректная закупка",
        ResultReason.RULES_CONFLICT: "конфликт правил: потолок ниже нижней границы",
        Constraint.LOWER_BOUND: "ограничено снизу",
        Constraint.CEILING: "ограничено сверху",
    }
)


def label(code: Code) -> str:
    return LABELS[code]


def _require_int(value: object, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _require_non_negative(value: object, name: str) -> Decimal:
    decimal = require_decimal(value, name)
    if decimal < 0:
        raise ValueError(f"{name} must be >= 0, got {decimal}")
    return decimal


@dataclass(frozen=True, slots=True)
class Product:
    """A product as the calculation sees it.

    ``brand`` and ``article`` are the raw values; they are normalized for matching.
    ``purchase_price`` is not validated beyond being a finite Decimal: zero or a
    negative value is a data problem that yields a "retained" result, not an error.
    """

    brand: str
    article: str
    purchase_price: Decimal
    published_price: Decimal | None = None

    def __post_init__(self) -> None:
        require_decimal(self.purchase_price, "purchase_price")
        if self.published_price is not None:
            require_decimal(self.published_price, "published_price")
            if self.published_price <= 0:
                raise ValueError(f"published_price must be > 0, got {self.published_price}")
        if not normalize_article(self.article):
            raise ValueError(f"product article is empty after normalization: {self.article!r}")
        if not normalize_brand(self.brand):
            raise ValueError(f"product brand is empty after normalization: {self.brand!r}")

    @property
    def normalized_brand(self) -> str:
        return normalize_brand(self.brand)

    @property
    def normalized_article(self) -> str:
        return normalize_article(self.article)


@dataclass(frozen=True, slots=True)
class ProductSettings:
    """Per-product pricing settings. Percentages are in percent (20 = 20 %)."""

    min_markup_percent: Decimal
    step: Decimal
    ceiling: Decimal | None = None
    quarantine_threshold_percent: Decimal = Decimal(20)

    def __post_init__(self) -> None:
        _require_non_negative(self.min_markup_percent, "min_markup_percent")
        _require_non_negative(self.step, "step")
        _require_non_negative(self.quarantine_threshold_percent, "quarantine_threshold_percent")
        if self.ceiling is not None:
            require_decimal(self.ceiling, "ceiling")


@dataclass(frozen=True, slots=True)
class GlobalSettings:
    """Settings shared by all products."""

    suspicious_price_factor: Decimal = Decimal("0.5")
    max_delivery_days: int = 14

    def __post_init__(self) -> None:
        _require_non_negative(self.suspicious_price_factor, "suspicious_price_factor")
        _require_int(self.max_delivery_days, "max_delivery_days", minimum=0)


DEFAULT_GLOBAL_SETTINGS = GlobalSettings()


@dataclass(frozen=True, slots=True)
class Offer:
    """A competitor offer exactly as a source reported it (brand and article are raw)."""

    source: str
    brand: str
    article: str
    price: Decimal
    stock: int
    delivery_days: int

    def __post_init__(self) -> None:
        require_decimal(self.price, "price")
        _require_int(self.stock, "stock", minimum=0)
        _require_int(self.delivery_days, "delivery_days", minimum=0)


@dataclass(frozen=True, slots=True)
class RejectedOffer:
    offer: Offer
    reason: OfferRejection


@dataclass(frozen=True, slots=True)
class PricingResult:
    """Everything needed to explain one calculation.

    Money fields are exact Decimals. ``target_price_raw`` is the minimum eligible
    competitor price minus the step before rounding; ``target_price`` is it
    rounded half-up to a kopeck. ``recommended_price`` is ``None`` when the
    state is ``retained``. ``change_percent`` is signed and exact
    (``(new - old) / old * 100``); ``change_percent_display`` is it rounded to
    0.1 for display. The quarantine decision uses the exact absolute value.
    """

    rules_version: str
    product: Product
    settings: ProductSettings
    global_settings: GlobalSettings
    partial_data: bool
    accepted_offers: tuple[Offer, ...]
    rejected_offers: tuple[RejectedOffer, ...]
    suspicious_price_limit: Decimal
    min_competitor_price: Decimal | None
    target_price_raw: Decimal | None
    target_price: Decimal | None
    lower_bound: Decimal
    ceiling: Decimal | None
    applied_constraints: tuple[Constraint, ...]
    recommended_price: Decimal | None
    state: ResultState
    reason: ResultReason | None
    change_percent: Decimal | None
    change_percent_display: Decimal | None
