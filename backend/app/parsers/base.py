"""The common interface of competitor sources.

A source either returns every offer it found for the requested articles or
fails with exactly one error of one of three kinds (``SourceErrorKind``):
network, structure or blocked. There are no partial results of a source: an
error on any article fails the whole source for the run, because a source that
answered for some products only is not a source we trust in that run.

Collection performs HTTP requests only; it never touches the database, so no
transaction can be open while it runs.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from app.db.enums import Source, SourceErrorKind
from app.logs import bind_source, get_logger

logger = get_logger("app.parsers")


@dataclass(frozen=True, slots=True)
class ParsedOffer:
    """One offer as the site showed it; brand and article are raw, numbers are validated."""

    brand: str
    article: str
    price: Decimal
    stock: int
    delivery_days: int


class SourceError(Exception):
    """The source failed; ``message`` is shown to the manager (Russian)."""

    def __init__(self, kind: SourceErrorKind, message: str) -> None:
        super().__init__(f"{kind.value}: {message}")
        self.kind = kind
        self.message = message


@dataclass(frozen=True, slots=True)
class SourceCollection:
    """The outcome of collecting one source: offers, or an error, never both."""

    source: Source
    started_at: datetime
    finished_at: datetime
    offers: tuple[ParsedOffer, ...]
    error: SourceError | None

    @property
    def ok(self) -> bool:
        return self.error is None


class OfferSource(Protocol):
    source: Source

    async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
        """Offers for the articles; raises ``SourceError`` on any failure."""
        ...


async def collect(source: OfferSource, articles: Sequence[str]) -> SourceCollection:
    """Collect one source and turn a ``SourceError`` into a failed collection.

    Every line logged meanwhile (retries, httpx requests) carries ``source``.
    """
    with bind_source(source.source.value):
        started_at = datetime.now(UTC)
        logger.info("source collection started", articles=len(articles))
        try:
            offers = tuple(await source.fetch_offers(articles))
        except SourceError as error:
            logger.warning(
                "source failed", error_kind=error.kind.value, error_message=error.message
            )
            return SourceCollection(source.source, started_at, datetime.now(UTC), (), error)
        logger.info("source collected", offers=len(offers))
        return SourceCollection(source.source, started_at, datetime.now(UTC), offers, None)


async def collect_all(
    sources: Sequence[OfferSource], articles: Sequence[str]
) -> list[SourceCollection]:
    """Collect all sources concurrently; the result keeps the order of ``sources``."""
    return list(await asyncio.gather(*(collect(source, articles) for source in sources)))


async def gather_per_article[T](
    articles: Sequence[str],
    fetch_one: "FetchOne[T]",
    *,
    concurrency: int,
) -> list[T]:
    """Run ``fetch_one`` for every article with at most ``concurrency`` at once.

    The first ``SourceError`` cancels the remaining requests and is raised as is;
    any other exception propagates (it is a bug, not a source error).
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(article: str) -> list[T]:
        async with semaphore:
            return await fetch_one(article)

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(guarded(article)) for article in articles]
    except BaseExceptionGroup as exc_group:
        matched, rest = exc_group.split(SourceError)
        if rest is not None:
            # A bug, not a source error: re-raise it alone when it is the only one.
            if len(rest.exceptions) == 1 and not isinstance(rest.exceptions[0], BaseExceptionGroup):
                raise rest.exceptions[0] from None
            raise
        assert matched is not None
        raise _first_leaf(matched) from None
    return [item for task in tasks for item in task.result()]


class FetchOne[T](Protocol):
    async def __call__(self, article: str, /) -> list[T]: ...


def _first_leaf(group: BaseExceptionGroup[SourceError]) -> SourceError:
    first = group.exceptions[0]
    while isinstance(first, BaseExceptionGroup):
        first = first.exceptions[0]
    return first
