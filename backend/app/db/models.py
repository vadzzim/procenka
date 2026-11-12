"""SQLAlchemy models of the demo core.

Money is ``NUMERIC(12,2)`` everywhere, including competitor offer prices: a
source price with more than two decimals is a structure error of the parser,
so it never reaches the database. Percentages are ``NUMERIC(6,2)`` in percent
(20 = 20 %). The exact change percent is a ratio, not money, and is stored as
unconstrained ``NUMERIC``.

JSON columns of ``calculations`` keep money as strings, never as JSON numbers:

* ``accepted_offers``: ``[{"source", "brand", "article", "normalized_brand",
  "normalized_article", "price", "stock", "delivery_days"}]``;
* ``rejected_offers``: the same objects plus ``"reason"`` (an ``OfferRejection`` code);
* ``applied_constraints``: ``["lower_bound" | "ceiling", ...]`` in the order applied.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.enums import RunStatus, RunTrigger, Source, SourceErrorKind, SourceStatus
from app.pricing.models import ResultReason, ResultState

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MONEY = Numeric(12, 2)
PERCENT = Numeric(6, 2)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def pg_enum[E: StrEnum](enum_class: type[E], name: str) -> Enum:
    """A native PostgreSQL enum that stores the English code (``.value``)."""
    return Enum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


RUN_STATUS = pg_enum(RunStatus, "run_status")
RUN_TRIGGER = pg_enum(RunTrigger, "run_trigger")
SOURCE = pg_enum(Source, "source")
SOURCE_STATUS = pg_enum(SourceStatus, "source_status")
SOURCE_ERROR_KIND = pg_enum(SourceErrorKind, "source_error_kind")
RESULT_STATE = pg_enum(ResultState, "result_state")
RESULT_REASON = pg_enum(ResultReason, "result_reason")


class Product(Base):
    """A product of the demo set with its pricing settings."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("brand", "article"),
        CheckConstraint("brand <> ''", name="brand_not_empty"),
        CheckConstraint("article <> ''", name="article_not_empty"),
        CheckConstraint("min_markup_percent >= 0", name="min_markup_percent_non_negative"),
        CheckConstraint("step >= 0", name="step_non_negative"),
        CheckConstraint(
            "quarantine_threshold_percent >= 0", name="quarantine_threshold_percent_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Raw values as they came in; brand and article are normalized for matching.
    brand_raw: Mapped[str] = mapped_column(Text)
    article_raw: Mapped[str] = mapped_column(Text)
    brand: Mapped[str] = mapped_column(Text)
    article: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    # Zero or negative purchase is a data problem shown as "retained", not rejected here.
    purchase_price: Mapped[Decimal] = mapped_column(MONEY)
    min_markup_percent: Mapped[Decimal] = mapped_column(PERCENT)
    step: Mapped[Decimal] = mapped_column(MONEY)
    ceiling: Mapped[Decimal | None] = mapped_column(MONEY)
    quarantine_threshold_percent: Mapped[Decimal] = mapped_column(PERCENT)


class PublishedPrice(Base):
    """The current published price of a product; written with INSERT ... ON CONFLICT."""

    __tablename__ = "published_prices"
    __table_args__ = (CheckConstraint("price > 0", name="price_positive"),)

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    price: Mapped[Decimal] = mapped_column(MONEY)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # NULL for a price that came from the seed rather than from a calculation.
    calculation_id: Mapped[int | None] = mapped_column(
        ForeignKey("calculations.id", ondelete="SET NULL")
    )


class Run(Base):
    """One collection and pricing run."""

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "(status IN ('queued', 'running')) = (finished_at IS NULL)",
            name="finished_at_matches_status",
        ),
        # At most one queued or running run at any time.
        Index(
            "uq_runs_single_active",
            text("(true)"),
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    status: Mapped[RunStatus] = mapped_column(RUN_STATUS)
    trigger: Mapped[RunTrigger] = mapped_column(RUN_TRIGGER)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A RunFinishReason code such as "interrupted"; free text so later codes need no migration.
    finish_reason: Mapped[str | None] = mapped_column(String(64))


class SourceResult(Base):
    """How collection from one source went within a run."""

    __tablename__ = "source_results"
    __table_args__ = (
        UniqueConstraint("run_id", "source"),
        CheckConstraint("(status = 'ok') = (error_kind IS NULL)", name="error_kind_matches_status"),
        CheckConstraint("offer_count >= 0", name="offer_count_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    source: Mapped[Source] = mapped_column(SOURCE)
    status: Mapped[SourceStatus] = mapped_column(SOURCE_STATUS)
    error_kind: Mapped[SourceErrorKind | None] = mapped_column(SOURCE_ERROR_KIND)
    error_message: Mapped[str | None] = mapped_column(Text)
    offer_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Offer(Base):
    """A competitor offer collected in a run; inserted in batches.

    Offers of past runs stay for explaining past calculations and never feed a new one.
    """

    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint("stock >= 0", name="stock_non_negative"),
        CheckConstraint("delivery_days >= 0", name="delivery_days_non_negative"),
        Index("ix_offers_run_id_brand_article", "run_id", "brand", "article"),
        Index("ix_offers_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    source: Mapped[Source] = mapped_column(SOURCE)
    brand_raw: Mapped[str] = mapped_column(Text)
    article_raw: Mapped[str] = mapped_column(Text)
    brand: Mapped[str] = mapped_column(Text)
    article: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(MONEY)
    stock: Mapped[int] = mapped_column(Integer)
    delivery_days: Mapped[int] = mapped_column(Integer)
    # The matched product, if any; NULL for offers of products outside the demo set.
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))


class Calculation(Base):
    """One price calculation of one product in one run, with everything to explain it."""

    __tablename__ = "calculations"
    __table_args__ = (
        UniqueConstraint("run_id", "product_id"),
        CheckConstraint(
            "(state = 'retained') = (recommended_price IS NULL)",
            name="recommended_price_matches_state",
        ),
        CheckConstraint(
            "(state IN ('published', 'unchanged')) = (reason IS NULL)",
            name="reason_matches_state",
        ),
        # Only a quarantined recommendation is confirmed (extension 1 of plan.md).
        CheckConstraint(
            "confirmed_at IS NULL OR state = 'quarantine'",
            name="confirmed_only_quarantine",
        ),
        Index("ix_calculations_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))

    # Input snapshot at the time of calculation.
    rules_version: Mapped[str] = mapped_column(String(32))
    purchase_price: Mapped[Decimal] = mapped_column(MONEY)
    min_markup_percent: Mapped[Decimal] = mapped_column(PERCENT)
    step: Mapped[Decimal] = mapped_column(MONEY)
    ceiling: Mapped[Decimal | None] = mapped_column(MONEY)
    quarantine_threshold_percent: Mapped[Decimal] = mapped_column(PERCENT)
    suspicious_price_factor: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    max_delivery_days: Mapped[int] = mapped_column(Integer)
    previous_price: Mapped[Decimal | None] = mapped_column(MONEY)
    partial_data: Mapped[bool]

    # Offers with reasons; money as strings (see module docstring).
    accepted_offers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    rejected_offers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    # Intermediate numbers and the outcome.
    min_competitor_price: Mapped[Decimal | None] = mapped_column(MONEY)
    target_price_raw: Mapped[Decimal | None] = mapped_column(MONEY)
    target_price: Mapped[Decimal | None] = mapped_column(MONEY)
    lower_bound: Mapped[Decimal] = mapped_column(MONEY)
    upper_bound: Mapped[Decimal | None] = mapped_column(MONEY)
    applied_constraints: Mapped[list[str]] = mapped_column(JSONB)
    recommended_price: Mapped[Decimal | None] = mapped_column(MONEY)
    state: Mapped[ResultState] = mapped_column(RESULT_STATE)
    reason: Mapped[ResultReason | None] = mapped_column(RESULT_REASON)
    change_percent: Mapped[Decimal | None] = mapped_column(Numeric)
    change_percent_display: Mapped[Decimal | None] = mapped_column(Numeric)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # When the manager confirmed this quarantined recommendation and it was
    # published (app.db.confirm); the state stays "quarantine". Who confirmed is
    # not stored: the demo has no users.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
