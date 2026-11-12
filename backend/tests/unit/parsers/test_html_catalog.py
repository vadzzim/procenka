"""HTML catalog parser: page fixtures, HTTP outcome mapping, the real fake-sites app."""

import asyncio
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fake_sites.app import create_app as create_fake_sites_app
from fake_sites.catalog import LISTINGS, Site

from app.db.enums import Source, SourceErrorKind
from app.parsers.base import ParsedOffer, SourceError, collect
from app.parsers.html_catalog import SEARCH_PATH, HtmlCatalogSource, parse_search_page
from tests.fakes import FakeClock

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "html"
BASE_URL = "http://fake-sites.test"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _source(
    handler: Callable[[httpx.Request], httpx.Response], *, concurrency: int = 4
) -> HtmlCatalogSource:
    return HtmlCatalogSource(
        BASE_URL,
        transport=httpx.MockTransport(handler),
        concurrency=concurrency,
        clock=FakeClock(),
    )


# --- Page parsing -----------------------------------------------------------


def test_correct_page() -> None:
    offers = parse_search_page(_page("ok.html"))

    assert offers == [
        ParsedOffer("BOSCH", "0 986-452 041", Decimal("1250.00"), 12, 1),
        ParsedOffer("MANN", "0986452041", Decimal("1150.00"), 3, 4),
        ParsedOffer("Bosch", "0 986 452 041", Decimal("1.00"), 8, 2),
    ]


def test_nothing_found_page_is_an_empty_list() -> None:
    assert parse_search_page(_page("no_results.html")) == []


@pytest.mark.parametrize(
    ("fixture", "message_part"),
    [
        ("missing_price.html", "нет обязательного поля «цена»"),
        ("empty_brand.html", "нет обязательного поля «бренд»"),
        ("unparsable_price.html", "не удалось разобрать поле «цена»"),
        ("three_decimals.html", "больше двух знаков"),
        ("negative_price.html", "отрицательное значение в поле «цена»"),
        ("negative_stock.html", "отрицательное значение в поле «остаток»"),
        ("foreign_price_format.html", "не удалось разобрать поле «цена»"),
        ("markup_changed.html", "нет блока результатов"),
        ("empty_container.html", "ни предложений, ни «ничего не найдено»"),
    ],
)
def test_broken_page_is_a_structure_error(fixture: str, message_part: str) -> None:
    with pytest.raises(SourceError) as excinfo:
        parse_search_page(_page(fixture))

    assert excinfo.value.kind is SourceErrorKind.STRUCTURE
    assert message_part in excinfo.value.message


def test_rows_together_with_nothing_found_marker_is_a_structure_error() -> None:
    html = _page("ok.html").replace(
        "</section>", '<p class="no-results">Ничего не найдено</p></section>'
    )
    with pytest.raises(SourceError) as excinfo:
        parse_search_page(html)
    assert excinfo.value.kind is SourceErrorKind.STRUCTURE


# --- HTTP outcomes ------------------------------------------------------------


async def test_source_requests_every_article_and_returns_all_offers() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == SEARCH_PATH
        article = request.url.params["article"]
        requested.append(article)
        page = "ok.html" if article == "0986452041" else "no_results.html"
        return httpx.Response(200, text=_page(page))

    offers = await _source(handler).fetch_offers(["0986452041", "W71275", "E340HD247"])

    assert sorted(requested) == ["0986452041", "E340HD247", "W71275"]
    assert [offer.price for offer in offers] == [
        Decimal("1250.00"),
        Decimal("1150.00"),
        Decimal("1.00"),
    ]


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (403, SourceErrorKind.BLOCKED),
        (429, SourceErrorKind.BLOCKED),
        (500, SourceErrorKind.NETWORK),
        (502, SourceErrorKind.NETWORK),
        (503, SourceErrorKind.NETWORK),
        (404, SourceErrorKind.STRUCTURE),
        (301, SourceErrorKind.STRUCTURE),
        (401, SourceErrorKind.STRUCTURE),
    ],
)
async def test_http_status_maps_to_error_kind(status: int, kind: SourceErrorKind) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="<html>капча</html>")

    collection = await collect(_source(handler), ["0986452041"])

    assert collection.source is Source.HTML_CATALOG
    assert collection.error is not None
    assert collection.error.kind is kind
    assert str(status) in collection.error.message
    assert collection.offers == ()


@pytest.mark.parametrize(
    "exception",
    [
        httpx.ReadTimeout("timed out"),
        httpx.ConnectTimeout("timed out"),
        httpx.ConnectError("connection refused"),
        httpx.RemoteProtocolError("server disconnected"),
        httpx.ProxyError("proxy refused"),
    ],
)
async def test_timeout_and_connection_errors_are_network_errors(exception: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception

    collection = await collect(_source(handler), ["0986452041"])

    assert collection.error is not None
    assert collection.error.kind is SourceErrorKind.NETWORK
    assert collection.offers == ()


async def test_one_broken_article_fails_the_whole_source() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["article"] == "W71275":
            return httpx.Response(200, text=_page("unparsable_price.html"))
        return httpx.Response(200, text=_page("ok.html"))

    collection = await collect(_source(handler), ["0986452041", "W71275", "OC90"])

    assert not collection.ok
    assert collection.error is not None
    assert collection.error.kind is SourceErrorKind.STRUCTURE
    assert "W71275" in collection.error.message
    # No partial results of a source.
    assert collection.offers == ()


async def test_concurrency_is_limited() -> None:
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, text=_page("no_results.html"))

    source = HtmlCatalogSource(BASE_URL, transport=httpx.MockTransport(handler), concurrency=2)
    await source.fetch_offers([f"A{i}" for i in range(10)])

    assert peak == 2


async def test_unexpected_exception_is_not_turned_into_a_source_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise ZeroDivisionError

    with pytest.raises(ZeroDivisionError):
        await collect(_source(handler), ["0986452041"])


async def test_proxy_is_passed_to_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    original_init = httpx.AsyncClient.__init__

    def spy(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        seen.update(kwargs)
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy)
    source = HtmlCatalogSource(BASE_URL, proxy="http://proxy.local:3128", timeout_seconds=2.5)

    assert await source.fetch_offers([]) == []
    assert seen["proxy"] == "http://proxy.local:3128"
    assert seen["timeout"] == httpx.Timeout(2.5)


# --- Against the real fake-sites application ---------------------------------


async def test_parses_the_fake_sites_html_catalog() -> None:
    transport = httpx.ASGITransport(app=create_fake_sites_app())
    source = HtmlCatalogSource(BASE_URL, transport=transport)

    offers = await source.fetch_offers(["0986452041", "E340HD247"])

    assert offers == [
        ParsedOffer("BOSCH", "0 986-452 041", Decimal("1250.00"), 12, 1),
        ParsedOffer("MANN", "0986452041", Decimal("1150.00"), 3, 4),
        ParsedOffer("Bosch", "0 986 452 041", Decimal("1.00"), 8, 2),
    ]


async def test_every_fake_sites_html_listing_parses_exactly() -> None:
    html_listings = [item for item in LISTINGS if item.site is Site.HTML]
    articles = sorted({item.article for item in html_listings})
    transport = httpx.ASGITransport(app=create_fake_sites_app())

    offers = await HtmlCatalogSource(BASE_URL, transport=transport).fetch_offers(articles)

    expected = {
        ParsedOffer(item.brand, item.article, item.price, item.stock, item.delivery_days)
        for item in html_listings
    }
    assert set(offers) == expected
