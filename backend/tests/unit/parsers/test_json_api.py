"""JSON API parser: payload validation, the session cookie, HTTP outcomes."""

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.db.enums import Source, SourceErrorKind
from app.parsers.base import ParsedOffer, SourceError, collect
from app.parsers.json_api import (
    OFFERS_PATH,
    SESSION_COOKIE,
    SESSION_PAGE_PATH,
    JsonApiSource,
    parse_offers_payload,
)
from tests.fakes import FakeClock

BASE_URL = "http://fake-sites.test"
ARTICLE = "0986452041"
GOOD_OFFER: dict[str, Any] = {
    "brand": "Bosch",
    "article": "0986452041",
    "price": "1250.00",
    "stock": 7,
    "delivery_days": 3,
}


def _payload(**changes: Any) -> bytes:
    offer = {key: value for key, value in {**GOOD_OFFER, **changes}.items() if value is not ...}
    return json.dumps({"article": ARTICLE, "offers": [offer]}).encode()


def _structure_message(content: bytes) -> str:
    with pytest.raises(SourceError) as excinfo:
        parse_offers_payload(content)
    assert excinfo.value.kind is SourceErrorKind.STRUCTURE
    return excinfo.value.message


# --- Payload ----------------------------------------------------------------------------


def test_correct_payload() -> None:
    assert parse_offers_payload(_payload()) == [
        ParsedOffer("Bosch", "0986452041", Decimal("1250.00"), 7, 3)
    ]


def test_whole_and_one_decimal_prices_are_exact() -> None:
    [offer] = parse_offers_payload(_payload(price="1500"))
    assert offer.price == Decimal("1500")
    [offer] = parse_offers_payload(_payload(price="1500.5"))
    assert offer.price == Decimal("1500.5")


def test_nothing_found_is_an_empty_list() -> None:
    assert parse_offers_payload(b'{"article": "X", "offers": []}') == []


@pytest.mark.parametrize(
    ("raw_price", "message_part"),
    [
        # Numbers are rejected, never turned into a price: the contract sends strings.
        ("1500.5", "должно быть строкой, получено число"),
        ("1500.123", "должно быть строкой, получено число"),
        ("1500", "должно быть строкой, получено число"),
        ("1e3", "должно быть строкой, получено число"),
        ("null", "нет обязательного поля «цена»"),
        ("true", "должно быть строкой, получено логическое значение"),
        ('{"amount": 1500}', "должно быть строкой, получено объект"),
        ('"1500.123"', "больше двух знаков"),
        ('""', "не удалось разобрать поле «цена»"),
        ('"-5.00"', "не удалось разобрать поле «цена»"),
        ('"1 500.00"', "не удалось разобрать поле «цена»"),
        ('"1500,00"', "не удалось разобрать поле «цена»"),
        ('"1e3"', "не удалось разобрать поле «цена»"),
        ('"NaN"', "не удалось разобрать поле «цена»"),
        ('"по запросу"', "не удалось разобрать поле «цена»"),
        ('"10000000000.00"', "слишком большое значение"),
    ],
)
def test_bad_price_is_a_structure_error_not_zero(raw_price: str, message_part: str) -> None:
    content = (
        '{"offers": [{"brand": "Bosch", "article": "0986452041", '
        f'"price": {raw_price}, "stock": 7, "delivery_days": 3}}]}}'
    ).encode()
    assert message_part in _structure_message(content)


@pytest.mark.parametrize(
    ("changes", "message_part"),
    [
        ({"price": ...}, "нет обязательного поля «цена»"),
        ({"brand": ...}, "нет обязательного поля «бренд»"),
        ({"brand": ""}, "нет обязательного поля «бренд»"),
        ({"brand": 42}, "поле «бренд» (brand) должно быть строкой"),
        ({"article": None}, "нет обязательного поля «артикул»"),
        ({"stock": ...}, "нет обязательного поля «остаток»"),
        ({"stock": "7"}, "поле «остаток» должно быть целым числом, получено строка"),
        ({"stock": True}, "должно быть целым числом, получено логическое значение"),
        ({"stock": -1}, "отрицательное значение в поле «остаток»"),
        ({"stock": 2_147_483_648}, "слишком большое значение в поле «остаток»"),
        ({"delivery_days": 1.5}, "поле «срок поставки» должно быть целым числом"),
        ({"delivery_days": ...}, "нет обязательного поля «срок поставки»"),
    ],
)
def test_missing_or_wrong_field_is_a_structure_error(
    changes: dict[str, Any], message_part: str
) -> None:
    assert message_part in _structure_message(_payload(**changes))


@pytest.mark.parametrize(
    ("content", "message_part"),
    [
        (b"<html>maintenance</html>", "не JSON"),
        (b"", "не JSON"),
        (b"\xff\xfe", "не JSON"),
        (b'{"offers": [{"price": NaN}]}', "не JSON"),
        (b'{"offers": [{"price": Infinity}]}', "не JSON"),
        (b"[]", "не объект"),
        (b'{"data": {"items": []}}', "нет обязательного поля «offers»"),
        (b'{"offers": null}', "нет обязательного поля «offers»"),
        (b'{"offers": {"brand": "Bosch"}}', "не список"),
        (b'{"offers": ["Bosch"]}', "не объект"),
    ],
)
def test_broken_payload_is_a_structure_error(content: bytes, message_part: str) -> None:
    assert message_part in _structure_message(content)


def test_changed_schema_is_a_structure_error() -> None:
    changed = {
        "query": ARTICLE,
        "data": {"items": [{"manufacturer": "Bosch", "cost": {"amount": 125000}}]},
    }
    assert "offers" in _structure_message(json.dumps(changed).encode())


# --- Session and HTTP --------------------------------------------------------------------


class Site:
    """A scripted JSON site: the storefront issues tokens, the API checks them."""

    def __init__(
        self,
        *,
        api: list[httpx.Response] | None = None,
        page: httpx.Response | None = None,
        rejected_tokens: set[str] | None = None,
        set_cookie: bool = True,
    ) -> None:
        self._api = api or []
        self._page = page
        self._rejected = rejected_tokens if rejected_tokens is not None else set()
        self._set_cookie = set_cookie
        self.page_requests = 0
        self.api_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == SESSION_PAGE_PATH:
            self.page_requests += 1
            if self._page is not None:
                return self._page
            headers = {}
            if self._set_cookie:
                headers["Set-Cookie"] = f"{SESSION_COOKIE}=t{self.page_requests}; Path=/json"
            return httpx.Response(200, text="<html>storefront</html>", headers=headers)
        assert request.url.path == OFFERS_PATH
        self.api_requests.append(request)
        cookie = request.headers.get("cookie", "")
        session = dict(part.split("=", 1) for part in cookie.split("; ") if "=" in part).get(
            SESSION_COOKIE
        )
        if session is None or session in self._rejected:
            return httpx.Response(401)
        if self._api:
            index = min(len(self.api_requests), len(self._api)) - 1
            return self._api[index]
        article = request.url.params["article"]
        offers = [GOOD_OFFER] if article == ARTICLE else []
        return httpx.Response(200, json={"article": article, "offers": offers})


def _source(site: Site, clock: FakeClock | None = None, **kwargs: Any) -> JsonApiSource:
    return JsonApiSource(
        BASE_URL, transport=httpx.MockTransport(site), clock=clock or FakeClock(), **kwargs
    )


async def _error(source: JsonApiSource, articles: list[str] | None = None) -> SourceError:
    collection = await collect(source, articles or [ARTICLE])
    assert collection.source is Source.JSON_API
    assert collection.offers == ()
    assert collection.error is not None
    return collection.error


async def test_one_session_per_collection_then_the_api_directly() -> None:
    site = Site()

    offers = await _source(site).fetch_offers([ARTICLE, "W71275", "OC90"])

    assert offers == [ParsedOffer("Bosch", "0986452041", Decimal("1250.00"), 7, 3)]
    assert site.page_requests == 1
    assert len(site.api_requests) == 3
    assert {r.headers["cookie"] for r in site.api_requests} == {f"{SESSION_COOKIE}=t1"}


async def test_no_articles_no_requests() -> None:
    site = Site()
    assert await _source(site).fetch_offers([]) == []
    assert site.page_requests == 0


async def test_storefront_without_session_cookie_is_a_structure_error() -> None:
    site = Site(set_cookie=False)

    error = await _error(_source(site))

    assert error.kind is SourceErrorKind.STRUCTURE
    assert "сессионную cookie" in error.message
    assert site.api_requests == []


async def test_401_reopens_the_session_once_and_succeeds() -> None:
    site = Site(rejected_tokens={"t1"})

    offers = await _source(site).fetch_offers([ARTICLE])

    assert len(offers) == 1
    assert site.page_requests == 2
    assert [r.headers["cookie"] for r in site.api_requests] == [
        f"{SESSION_COOKIE}=t1",
        f"{SESSION_COOKIE}=t2",
    ]


async def test_401_twice_is_blocked() -> None:
    site, clock = Site(rejected_tokens={"t1", "t2", "t3"}), FakeClock()

    error = await _error(_source(site, clock))

    assert error.kind is SourceErrorKind.BLOCKED
    assert "доступ отклонён: HTTP 401" in error.message
    assert OFFERS_PATH in error.message
    assert site.page_requests == 2
    assert len(site.api_requests) == 2
    assert clock.sleeps == []


async def test_concurrent_401s_reopen_the_session_only_once() -> None:
    site = Site(rejected_tokens={"t1"})
    articles = [ARTICLE, *(f"A{i}" for i in range(7))]

    offers = await _source(site, concurrency=4).fetch_offers(articles)

    assert len(offers) == 1
    assert site.page_requests == 2


async def test_session_is_reopened_at_most_once_per_collection() -> None:
    # t2 works for the first article only, then the site rejects it as well.
    rejected = {"t1"}
    site = Site(rejected_tokens=rejected)

    def handler(request: httpx.Request) -> httpx.Response:
        if len(site.api_requests) >= 2:
            rejected.add("t2")
        return site(request)

    source = JsonApiSource(
        BASE_URL, transport=httpx.MockTransport(handler), clock=FakeClock(), concurrency=1
    )
    error = await _error(source, [ARTICLE, "W71275"])

    assert error.kind is SourceErrorKind.BLOCKED
    assert site.page_requests == 2


async def test_changed_schema_is_a_structure_error_after_one_api_request() -> None:
    changed = {"query": ARTICLE, "data": {"items": []}}
    site, clock = Site(api=[httpx.Response(200, json=changed)]), FakeClock()

    error = await _error(_source(site, clock))

    assert error.kind is SourceErrorKind.STRUCTURE
    assert "offers" in error.message
    assert ARTICLE in error.message
    assert len(site.api_requests) == 1
    assert clock.sleeps == []


async def test_float_price_from_the_site_is_a_structure_error() -> None:
    content = (
        b'{"offers": [{"brand": "Bosch", "article": "0986452041", "price": 1500.5,'
        b' "stock": 7, "delivery_days": 3}]}'
    )
    site = Site(api=[httpx.Response(200, content=content)])

    error = await _error(_source(site))

    assert error.kind is SourceErrorKind.STRUCTURE
    assert "должно быть строкой" in error.message


async def test_api_503_is_network_after_retries() -> None:
    site, clock = Site(api=[httpx.Response(503)]), FakeClock()

    error = await _error(_source(site, clock))

    assert error.kind is SourceErrorKind.NETWORK
    assert f"HTTP 503 на {OFFERS_PATH}" in error.message
    assert "попыток: 3" in error.message
    assert len(site.api_requests) == 3
    assert clock.sleeps == [1.0, 1.0]


async def test_storefront_503_is_network_after_retries() -> None:
    site, clock = Site(page=httpx.Response(503)), FakeClock()

    error = await _error(_source(site, clock))

    assert error.kind is SourceErrorKind.NETWORK
    assert SESSION_PAGE_PATH in error.message
    assert site.page_requests == 3
    assert clock.sleeps == [1.0, 1.0]


async def test_api_timeout_is_network_after_retries() -> None:
    clock = FakeClock()
    site = Site()
    api_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls
        if request.url.path == OFFERS_PATH:
            api_calls += 1
            raise httpx.ReadTimeout("timed out")
        return site(request)

    source = JsonApiSource(BASE_URL, transport=httpx.MockTransport(handler), clock=clock)
    error = await _error(source)

    assert error.kind is SourceErrorKind.NETWORK
    assert "таймаут" in error.message
    assert api_calls == 3
    assert clock.sleeps == [1.0, 1.0]


async def test_api_429_beyond_the_budget_is_blocked_without_waiting() -> None:
    limited = httpx.Response(429, headers={"Retry-After": "60"})
    site, clock = Site(api=[limited]), FakeClock()

    error = await _error(_source(site, clock, budget_seconds=30.0))

    assert error.kind is SourceErrorKind.BLOCKED
    assert "Retry-After 60 сек." in error.message
    assert len(site.api_requests) == 1
    assert clock.sleeps == []


async def test_api_429_within_the_budget_is_retried() -> None:
    limited = httpx.Response(429, headers={"Retry-After": "3"})
    ok = httpx.Response(200, json={"offers": [GOOD_OFFER]})
    site, clock = Site(api=[limited, ok]), FakeClock()

    offers = await _source(site, clock).fetch_offers([ARTICLE])

    assert len(offers) == 1
    assert clock.sleeps == [3.0]


async def test_storefront_403_is_blocked() -> None:
    site = Site(page=httpx.Response(403))

    error = await _error(_source(site))

    assert error.kind is SourceErrorKind.BLOCKED
    assert site.page_requests == 1


@pytest.mark.parametrize("status", [302, 404])
async def test_other_statuses_are_structure_errors(status: int) -> None:
    site = Site(api=[httpx.Response(status)])

    error = await _error(_source(site))

    assert error.kind is SourceErrorKind.STRUCTURE
    assert f"HTTP {status}" in error.message
    assert len(site.api_requests) == 1


async def test_proxy_and_timeout_are_passed_to_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    original_init = httpx.AsyncClient.__init__

    def spy(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        seen.update(kwargs)
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy)
    source = JsonApiSource(BASE_URL, proxy="http://proxy.local:3128", timeout_seconds=2.5)
    site = Site()
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport,
        "handle_async_request",
        lambda self, request: _async(site(request)),
    )

    assert len(await source.fetch_offers([ARTICLE])) == 1
    assert seen["proxy"] == "http://proxy.local:3128"
    assert seen["timeout"] == httpx.Timeout(2.5)


async def _async(response: httpx.Response) -> httpx.Response:
    return response
