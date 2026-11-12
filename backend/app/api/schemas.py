"""Response models. Money is a ``Decimal`` and is serialized as a JSON string."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

from app.db.enums import RunStatus, RunTrigger, Source, SourceErrorKind, SourceStatus

# Always a string such as "1240.00" in JSON, never a JSON number.
Money = Annotated[Decimal, PlainSerializer(str, return_type=str, when_used="always")]


class RunCreated(BaseModel):
    id: int
    status: RunStatus
    # False when a queued or running run already existed and is returned instead.
    created: bool


class SourceResultOut(BaseModel):
    source: Source
    status: SourceStatus
    error_kind: SourceErrorKind | None
    error_message: str | None
    offer_count: int
    started_at: datetime
    finished_at: datetime


class RunOut(BaseModel):
    id: int
    status: RunStatus
    trigger: RunTrigger
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    finish_reason: str | None
    sources: list[SourceResultOut]


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
