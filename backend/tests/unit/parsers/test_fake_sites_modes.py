"""Both real parsers against the fake-sites application in every failure mode.

Modes are switched over HTTP on the same application instance the parsers
talk to, as in the demo; the clock is fake, so nothing sleeps for real.
"""

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fake_sites.app import create_app as create_fake_sites_app
from fake_sites.catalog import LISTINGS, SPIKE_ARTICLE, Site, normalize_article

from app.db.enums import Source, SourceErrorKind
from app.parsers.base import OfferSource, ParsedOffer, SourceCollection, collect
from app.parsers.html_catalog import HtmlCatalogSource
from app.parsers.json_api import JsonApiSource
from tests.fakes import FakeClock

BASE_URL = "http://fake-sites.test"
A_ARTICLE = "0986452041"
ARTICLES = [A_ARTICLE, SPIKE_ARTICLE, "E340HD247"]
SITE_OF = {Source.HTML_CATALOG: Site.HTML, Source.JSON_API: Site.JSON}


class FakeSites:
    def __init__(self) -> None:
        self.app = create_fake_sites_app()
        self.transport = httpx.ASGITransport(app=self.app)
        self.requests: list[str] = []
        self.control = httpx.AsyncClient(transport=self.transport, base_url=BASE_URL)

    async def set_mode(self, source: Source, mode: str, **extra: Any) -> None:
        response = await self.control.put(
            f"/control/modes/{source.value}", json={"mode": mode, **extra}
        )
        assert response.status_code == 200, response.text

    def source(self, source: Source, clock: FakeClock) -> OfferSource:
        transport = _CountingTransport(self.transport, self.requests)
        if source is Source.HTML_CATALOG:
            return HtmlCatalogSource(BASE_URL, transport=transport, clock=clock)
        return JsonApiSource(BASE_URL, transport=transport, clock=clock)


class _CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, log: list[str]) -> None:
        self._inner = inner
        self._log = log

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._log.append(request.url.path)
        return await self._inner.handle_async_request(request)


@pytest.fixture
async def sites() -> AsyncIterator[FakeSites]:
    fake = FakeSites()
    try:
        yield fake
    finally:
        await fake.control.aclose()


def _expected(source: Source) -> set[ParsedOffer]:
    return {
        ParsedOffer(item.brand, item.article, item.price, item.stock, item.delivery_days)
        for item in LISTINGS
        if item.site is SITE_OF[source]
    }


async def _collect(sites: FakeSites, source: Source, clock: FakeClock) -> SourceCollection:
    return await collect(sites.source(source, clock), ARTICLES)


def _error(collection: SourceCollection) -> tuple[SourceErrorKind, str]:
    assert collection.offers == ()
    assert collection.error is not None
    return collection.error.kind, collection.error.message


@pytest.mark.parametrize("source", list(Source))
async def test_normal_mode_parses_every_listing_exactly(sites: FakeSites, source: Source) -> None:
    articles = sorted(
        {normalize_article(item.article) for item in LISTINGS if item.site is SITE_OF[source]}
    )

    offers = await sites.source(source, FakeClock()).fetch_offers(articles)

    assert set(offers) == _expected(source)
    assert len(offers) == len(_expected(source))


@pytest.mark.parametrize("source", list(Source))
async def test_markup_changed_is_a_structure_error_without_retries(
    sites: FakeSites, source: Source
) -> None:
    await sites.set_mode(source, "markup_changed")
    clock = FakeClock()

    collection = await collect(sites.source(source, clock), [A_ARTICLE])

    kind, _ = _error(collection)
    assert kind is SourceErrorKind.STRUCTURE
    assert clock.sleeps == []
    data_requests = [p for p in sites.requests if p != "/json/"]
    assert len(data_requests) == 1


@pytest.mark.parametrize("source", list(Source))
async def test_unavailable_is_a_network_error_after_retries(
    sites: FakeSites, source: Source
) -> None:
    await sites.set_mode(source, "unavailable")
    clock = FakeClock()

    kind, message = _error(await collect(sites.source(source, clock), [A_ARTICLE]))

    assert kind is SourceErrorKind.NETWORK
    assert "HTTP 503" in message
    assert "попыток: 3" in message
    assert clock.sleeps == [1.0, 1.0]


async def test_html_blocked_is_403_blocked_without_retries(sites: FakeSites) -> None:
    await sites.set_mode(Source.HTML_CATALOG, "blocked")
    clock = FakeClock()

    collection = await collect(sites.source(Source.HTML_CATALOG, clock), [A_ARTICLE])

    kind, message = _error(collection)
    assert kind is SourceErrorKind.BLOCKED
    assert "HTTP 403" in message
    assert sites.requests == ["/html/search"]
    assert clock.sleeps == []


async def test_json_blocked_with_default_retry_after_is_blocked_without_waiting(
    sites: FakeSites,
) -> None:
    await sites.set_mode(Source.JSON_API, "blocked")
    clock = FakeClock()

    kind, message = _error(await _collect(sites, Source.JSON_API, clock))

    # Retry-After 60 s does not fit into the 30 s budget.
    assert kind is SourceErrorKind.BLOCKED
    assert "HTTP 429" in message
    assert "Retry-After 60 сек." in message
    assert len(sites.requests) == 1
    assert clock.sleeps == []


async def test_json_blocked_mid_collection_with_retry_after_beyond_budget(
    sites: FakeSites,
) -> None:
    # The session is open, then the API starts answering 429 for 60 s.
    clock = FakeClock()
    transport = sites.transport

    class BlockAfterSession(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path != "/json/":
                await sites.set_mode(Source.JSON_API, "blocked")
            return await transport.handle_async_request(request)

    source = JsonApiSource(BASE_URL, transport=BlockAfterSession(), clock=clock)

    kind, message = _error(await collect(source, [A_ARTICLE]))

    assert kind is SourceErrorKind.BLOCKED
    assert "/json/api/offers" in message
    assert clock.sleeps == []


async def test_json_blocked_within_budget_is_retried_and_succeeds_once_the_site_recovers(
    sites: FakeSites,
) -> None:
    await sites.set_mode(Source.JSON_API, "blocked", retry_after=2)

    async def site_recovers(seconds: float) -> None:
        await sites.set_mode(Source.JSON_API, "normal")

    clock = FakeClock(on_sleep=site_recovers)

    offers = await sites.source(Source.JSON_API, clock).fetch_offers([A_ARTICLE])

    assert offers == [ParsedOffer("Bosch", "0986452041", Decimal("1250.00"), 7, 3)]
    assert clock.sleeps == [2.0]


async def test_json_blocked_within_budget_that_persists_is_blocked(sites: FakeSites) -> None:
    await sites.set_mode(Source.JSON_API, "blocked", retry_after=2)
    clock = FakeClock()

    kind, message = _error(await _collect(sites, Source.JSON_API, clock))

    assert kind is SourceErrorKind.BLOCKED
    assert "повторов после Retry-After: 2" in message
    assert clock.sleeps == [2.0, 2.0]


@pytest.mark.parametrize("source", list(Source))
async def test_price_spike_changes_only_the_control_product(
    sites: FakeSites, source: Source
) -> None:
    await sites.set_mode(source, "price_spike")

    offers = await sites.source(source, FakeClock()).fetch_offers(ARTICLES)

    by_article: dict[str, list[Decimal]] = {}
    for offer in offers:
        by_article.setdefault(normalize_article(offer.article), []).append(offer.price)
    assert by_article[SPIKE_ARTICLE] == [Decimal("1500.00")]
    normal = {
        normalize_article(item.article): item.price
        for item in LISTINGS
        if item.site is SITE_OF[source] and normalize_article(item.article) == A_ARTICLE
    }
    assert normal[A_ARTICLE] in by_article[A_ARTICLE]
    assert len(offers) == sum(
        item.site is SITE_OF[source] and normalize_article(item.article) in ARTICLES
        for item in LISTINGS
    )


async def test_modes_are_per_source(sites: FakeSites) -> None:
    await sites.set_mode(Source.HTML_CATALOG, "unavailable")

    html = await _collect(sites, Source.HTML_CATALOG, FakeClock())
    json_ = await _collect(sites, Source.JSON_API, FakeClock())

    assert html.error is not None
    assert json_.ok
