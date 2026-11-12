"""Response and request models of the API; their names become TypeScript types.

Money is a ``Decimal`` serialized as a JSON string with exactly two decimals
(``"1240.00"``), never a JSON number; the OpenAPI schema says ``type: string``
with a pattern. Percentages and factors are decimal strings too (``"-25.1"``).
Times are ISO 8601 with a time zone. Every code (status, state, reason, error
kind) comes with its Russian label, so the UI keeps no label table of its own.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer, WithJsonSchema

from app.db.confirm import ConfirmRefusal
from app.db.enums import (
    RunFinishReason,
    RunStatus,
    RunTrigger,
    Source,
    SourceErrorKind,
    SourceStatus,
)
from app.pricing.models import Constraint, OfferRejection, ResultReason, ResultState
from app.pricing.money import CENT

MONEY_PATTERN = r"^-?\d+\.\d{2}$"
DECIMAL_PATTERN = r"^-?\d+(\.\d+)?$"


def money_string(value: Decimal) -> str:
    """``Decimal("1240")`` -> ``"1240.00"``; a value with more than two decimals is a bug."""
    quantized = value.quantize(CENT)
    if quantized != value:
        raise ValueError(f"money with more than two decimals: {value}")
    return format(quantized, "f")


def decimal_string(value: Decimal) -> str:
    return format(value, "f")


# Always a string such as "1240.00" in JSON, never a JSON number.
Money = Annotated[
    Decimal,
    PlainSerializer(money_string, return_type=str, when_used="always"),
    WithJsonSchema({"type": "string", "pattern": MONEY_PATTERN, "examples": ["1240.00"]}),
]
# A percentage or factor as a decimal string: "20.00", "-25.1", "0.5000".
DecimalString = Annotated[
    Decimal,
    PlainSerializer(decimal_string, return_type=str, when_used="always"),
    WithJsonSchema({"type": "string", "pattern": DECIMAL_PATTERN, "examples": ["-25.1"]}),
]


class ErrorOut(BaseModel):
    detail: str


# --- Runs -------------------------------------------------------------------


class RunCreated(BaseModel):
    id: int
    status: RunStatus
    # False when a queued or running run already existed and is returned instead.
    created: bool


class SourceResultOut(BaseModel):
    """How one source went in a run. ``ok`` with zero offers is a successful
    collection that found nothing; ``failed`` is a technical failure with a kind."""

    source: Source
    source_label: str
    status: SourceStatus
    error_kind: SourceErrorKind | None
    error_label: str | None
    error_message: str | None
    offer_count: int
    started_at: datetime
    finished_at: datetime


class RunOut(BaseModel):
    id: int
    status: RunStatus
    status_label: str
    trigger: RunTrigger
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    finish_reason: RunFinishReason | None
    finish_reason_label: str | None
    # Written when a run finishes; empty while it is queued or running and for
    # a run interrupted before it recorded anything.
    sources: list[SourceResultOut]


class StatusOut(BaseModel):
    """The header of the screen."""

    # The newest run of any status (it is the active run if there is one).
    latest_run: RunOut | None
    # The queued or running run, if any.
    active_run: RunOut | None
    # The newest finished run (succeeded, partial or failed) with its sources.
    last_finished_run: RunOut | None


# --- Products ---------------------------------------------------------------


class ProductOut(BaseModel):
    id: int
    # Normalized brand and article (used for matching) and the raw spellings.
    brand: str
    article: str
    brand_raw: str
    article_raw: str
    name: str


class PriceOrigin(StrEnum):
    CALCULATION = "calculation"
    # A quarantined recommendation published by the manager's confirmation.
    CONFIRMATION = "confirmation"
    SEED = "seed"


PRICE_ORIGIN_LABELS = {
    PriceOrigin.CALCULATION: "по расчёту",
    PriceOrigin.CONFIRMATION: "подтверждена из карантина",
    PriceOrigin.SEED: "цена из исходных данных",
}


class PublishedPriceInfo(BaseModel):
    """The current published price and where it came from."""

    price: Money
    published_at: datetime
    origin: PriceOrigin
    origin_label: str
    # The calculation the price was published by; null for a seed price.
    calculation_id: int | None
    calculation_run_id: int | None
    calculated_at: datetime | None


class ConfirmationOut(BaseModel):
    """Confirmation of a quarantined recommendation (extension 1 of plan.md).

    ``can_confirm`` says whether POST /api/calculations/{id}/confirm would publish
    now; otherwise ``refusal`` is the code it would answer with (409) and
    ``refusal_label`` its Russian reason, e.g. for a disabled button.
    """

    confirmed: bool
    # When the recommendation was confirmed and published; the state stays "quarantine".
    confirmed_at: datetime | None
    can_confirm: bool
    refusal: ConfirmRefusal | None
    refusal_label: str | None


class ConfirmRefusalOut(BaseModel):
    """409 of a confirmation: the code and its Russian reason."""

    code: ConfirmRefusal
    detail: str


class LatestCalculationOut(BaseModel):
    """The calculation of the product in the newest succeeded or partial run."""

    id: int
    run_id: int
    calculated_at: datetime
    # Null when the state is "retained".
    recommended_price: Money | None
    state: ResultState
    state_label: str
    reason: ResultReason | None
    reason_label: str | None
    # Signed change against the published price, rounded to 0.1 for display.
    change_percent_display: DecimalString | None
    partial_data: bool
    confirmation: ConfirmationOut


class ProductRowOut(BaseModel):
    product: ProductOut
    purchase_price: Money
    published: PublishedPriceInfo | None
    latest_calculation: LatestCalculationOut | None


class ProductsOut(BaseModel):
    products: list[ProductRowOut]


# --- Calculation ------------------------------------------------------------


class CalculationInputsOut(BaseModel):
    """Purchase and settings as they were at the time of the calculation."""

    purchase_price: Money
    min_markup_percent: DecimalString
    step: Money
    ceiling: Money | None
    quarantine_threshold_percent: DecimalString
    suspicious_price_factor: DecimalString
    max_delivery_days: int


class OfferOut(BaseModel):
    source: Source
    source_label: str
    brand_raw: str
    article_raw: str
    brand: str
    article: str
    price: Money
    stock: int
    delivery_days: int


class RejectedOfferOut(OfferOut):
    reason: OfferRejection
    reason_label: str


class ConstraintOut(BaseModel):
    code: Constraint
    label: str


class CalculationOut(BaseModel):
    """Everything behind one price: «Почему цена такая»."""

    id: int
    run_id: int
    run_status: RunStatus
    product: ProductOut
    calculated_at: datetime
    rules_version: str
    partial_data: bool
    inputs: CalculationInputsOut
    accepted_offers: list[OfferOut]
    rejected_offers: list[RejectedOfferOut]
    min_competitor_price: Money | None
    lower_bound: Money
    # The ceiling rounded down to a kopeck; null without a ceiling.
    upper_bound: Money | None
    target_price_raw: Money | None
    target_price: Money | None
    applied_constraints: list[ConstraintOut]
    recommended_price: Money | None
    # The published price at the time of the calculation.
    previous_price: Money | None
    # Exact signed change in percent and the same rounded to 0.1 for display.
    change_percent: DecimalString | None
    change_percent_display: DecimalString | None
    state: ResultState
    state_label: str
    reason: ResultReason | None
    reason_label: str | None
    # The calculation step by step in Russian with the numbers filled in.
    formula: list[str]
    confirmation: ConfirmationOut


# --- Published prices -------------------------------------------------------


class PublishedPriceOut(BaseModel):
    product_id: int
    brand: str
    article: str
    name: str
    price: Money
    published_at: datetime
    # The calculation that produced the price; null for a price loaded by the seed.
    calculation_id: int | None


class PublishedPricesOut(BaseModel):
    prices: list[PublishedPriceOut]


# --- Demo panel -------------------------------------------------------------


class DemoMode(StrEnum):
    """Failure modes of the synthetic sources (the same codes as fake-sites)."""

    NORMAL = "normal"
    MARKUP_CHANGED = "markup_changed"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    PRICE_SPIKE = "price_spike"


DEMO_MODE_LABELS = {
    DemoMode.NORMAL: "норма",
    DemoMode.MARKUP_CHANGED: "изменилась разметка",
    DemoMode.UNAVAILABLE: "недоступен",
    DemoMode.BLOCKED: "заблокирован",
    DemoMode.PRICE_SPIKE: "скачок цены",
}


class SourceModeOut(BaseModel):
    source: Source
    source_label: str
    mode: DemoMode
    mode_label: str
    # Seconds of Retry-After the JSON source sends in "blocked".
    retry_after: int


class DemoModesOut(BaseModel):
    modes: list[SourceModeOut]


class ModeChange(BaseModel):
    mode: DemoMode
    # Retry-After of the JSON source in "blocked"; omitted -> the fake-sites default.
    retry_after: int | None = Field(default=None, ge=0, le=3600)


class DemoResetOut(BaseModel):
    """What the reset did: the database is back to the seed, both sources are normal."""

    products: int
    published_prices: int
    modes: list[SourceModeOut]


class HealthOut(BaseModel):
    status: Literal["ok", "unavailable"]
