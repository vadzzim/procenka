"""Retries, Retry-After and the time budget (``app.parsers.http``).

Driven through the HTML catalog source with a mock transport and a fake clock:
no test sleeps for real, and "without waiting" means ``clock.sleeps == []``.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest

from app.db.enums import SourceErrorKind
from app.parsers.base import SourceError
from app.parsers.html_catalog import HtmlCatalogSource
from app.parsers.http import RetryPolicy, retry_after_seconds
from tests.fakes import FakeClock

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "html"
OK_PAGE = (FIXTURES / "ok.html").read_text(encoding="utf-8")
MARKUP_CHANGED_PAGE = (FIXTURES / "markup_changed.html").read_text(encoding="utf-8")
ARTICLE = "0986452041"

type Handler = Callable[[httpx.Request], httpx.Response]


class Site:
    """Answers with the queued responses in order, then repeats the last one."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        index = min(len(self.requests), len(self._responses)) - 1
        response = self._responses[index]
        if isinstance(response, Exception):
            raise response
        return response


def _source(
    handler: Handler,
    clock: FakeClock,
    *,
    budget: float = 30.0,
    policy: RetryPolicy | None = None,
    timeout: float = 5.0,
) -> HtmlCatalogSource:
    return HtmlCatalogSource(
        "http://fake-sites.test",
        transport=httpx.MockTransport(handler),
        budget_seconds=budget,
        retry_policy=policy,
        clock=clock,
        timeout_seconds=timeout,
    )


async def _fail(source: HtmlCatalogSource) -> SourceError:
    with pytest.raises(SourceError) as excinfo:
        await source.fetch_offers([ARTICLE])
    return excinfo.value


def _ok() -> httpx.Response:
    return httpx.Response(200, text=OK_PAGE)


def _rate_limited(retry_after: str | None) -> httpx.Response:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return httpx.Response(429, headers=headers)


# --- No retries --------------------------------------------------------------------


async def test_changed_markup_is_a_structure_error_after_exactly_one_request() -> None:
    site, clock = Site(httpx.Response(200, text=MARKUP_CHANGED_PAGE)), FakeClock()

    error = await _fail(_source(site, clock))

    assert error.kind is SourceErrorKind.STRUCTURE
    assert len(site.requests) == 1
    assert clock.sleeps == []


async def test_403_is_blocked_after_exactly_one_request() -> None:
    site, clock = Site(httpx.Response(403, text="<form class='captcha'>")), FakeClock()

    error = await _fail(_source(site, clock))

    assert error.kind is SourceErrorKind.BLOCKED
    assert "HTTP 403" in error.message
    assert "/html/search" in error.message
    assert len(site.requests) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("status", [301, 400, 404])
async def test_other_statuses_are_structure_errors_without_retries(status: int) -> None:
    site, clock = Site(httpx.Response(status)), FakeClock()

    error = await _fail(_source(site, clock))

    assert error.kind is SourceErrorKind.STRUCTURE
    assert f"HTTP {status}" in error.message
    assert len(site.requests) == 1
    assert clock.sleeps == []


# --- Network failures: limited retries with a pause ------------------------------------


async def test_503_is_a_network_error_after_all_attempts_with_pauses() -> None:
    site, clock = Site(httpx.Response(503)), FakeClock()

    error = await _fail(_source(site, clock))

    assert error.kind is SourceErrorKind.NETWORK
    assert "HTTP 503" in error.message
    assert "/html/search" in error.message
    assert "попыток: 3" in error.message
    assert len(site.requests) == 3
    assert clock.sleeps == [1.0, 1.0]


async def test_attempts_and_pause_are_configurable() -> None:
    site, clock = Site(httpx.Response(502)), FakeClock()
    policy = RetryPolicy(attempts=5, pause_seconds=0.5)

    error = await _fail(_source(site, clock, policy=policy))

    assert error.kind is SourceErrorKind.NETWORK
    assert len(site.requests) == 5
    assert clock.sleeps == [0.5] * 4


async def test_503_then_recovery_returns_offers() -> None:
    site, clock = Site(httpx.Response(503), httpx.Response(503), _ok()), FakeClock()

    offers = await _source(site, clock).fetch_offers([ARTICLE])

    assert len(offers) == 3
    assert len(site.requests) == 3
    assert clock.sleeps == [1.0, 1.0]


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
async def test_timeout_and_connection_errors_are_retried_then_network(
    exception: Exception,
) -> None:
    site, clock = Site(exception), FakeClock()

    error = await _fail(_source(site, clock))

    assert error.kind is SourceErrorKind.NETWORK
    assert "попыток: 3" in error.message
    assert len(site.requests) == 3
    assert clock.sleeps == [1.0, 1.0]


async def test_timeout_then_recovery_returns_offers() -> None:
    site, clock = Site(httpx.ReadTimeout("timed out"), _ok()), FakeClock()

    offers = await _source(site, clock).fetch_offers([ARTICLE])

    assert len(offers) == 3
    assert clock.sleeps == [1.0]


async def test_budget_spent_on_503_retries_is_a_network_error() -> None:
    site, clock = Site(httpx.Response(503)), FakeClock()
    policy = RetryPolicy(attempts=10, pause_seconds=1.0)

    error = await _fail(_source(site, clock, budget=2.5, policy=policy))

    # Requests at 0, 1 and 2 s; the next pause does not fit into the 0.5 s left.
    assert error.kind is SourceErrorKind.NETWORK
    assert "не помещается в остаток бюджета" in error.message
    assert len(site.requests) == 3
    assert clock.sleeps == [1.0, 1.0]
    assert clock.now <= 2.5


async def test_slow_timeouts_spend_the_budget_and_give_a_network_error() -> None:
    clock = FakeClock()
    timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeout = request.extensions["timeout"]["read"]
        timeouts.append(timeout)
        clock.now += timeout  # the request hangs until its timeout
        raise httpx.ReadTimeout("timed out")

    policy = RetryPolicy(attempts=10, pause_seconds=1.0)
    error = await _fail(_source(handler, clock, budget=12.5, policy=policy))

    # 5 s, pause, 5 s, pause, then the last request may only take the 0.5 s left.
    assert error.kind is SourceErrorKind.NETWORK
    assert "таймаут запроса" in error.message
    assert "остаток бюджета 0 сек. из 12.5 сек." in error.message
    assert timeouts == [5.0, 5.0, 0.5]
    assert clock.sleeps == [1.0, 1.0]
    assert clock.now == 12.5


# --- 429 and Retry-After -------------------------------------------------------------


async def test_429_within_budget_is_retried_after_retry_after() -> None:
    site, clock = Site(_rate_limited("2"), _ok()), FakeClock()

    offers = await _source(site, clock).fetch_offers([ARTICLE])

    assert len(offers) == 3
    assert len(site.requests) == 2
    assert clock.sleeps == [2.0]


async def test_429_with_retry_after_beyond_the_budget_is_blocked_without_waiting() -> None:
    site, clock = Site(_rate_limited("60"), _ok()), FakeClock()

    error = await _fail(_source(site, clock, budget=30.0))

    assert error.kind is SourceErrorKind.BLOCKED
    assert "HTTP 429" in error.message
    assert "Retry-After 60 сек." in error.message
    assert "остаток бюджета 30 сек. из 30 сек." in error.message
    assert len(site.requests) == 1
    assert clock.sleeps == []


async def test_429_with_retry_after_beyond_what_is_left_of_the_budget_is_blocked() -> None:
    clock = FakeClock()
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            clock.now += 22.0  # a slow first request spent most of the budget
            return httpx.Response(503)
        return _rate_limited("10")

    error = await _fail(_source(handler, clock, budget=30.0))

    assert error.kind is SourceErrorKind.BLOCKED
    assert "остаток бюджета 7 сек." in error.message
    assert calls == 2
    assert clock.sleeps == [1.0]  # only the pause after the 503


async def test_repeated_429_is_blocked_after_the_allowed_retries() -> None:
    site, clock = Site(_rate_limited("2")), FakeClock()

    error = await _fail(_source(site, clock))

    assert error.kind is SourceErrorKind.BLOCKED
    assert "повторов после Retry-After: 2" in error.message
    assert len(site.requests) == 3
    assert clock.sleeps == [2.0, 2.0]


async def test_429_retries_are_configurable() -> None:
    site, clock = Site(_rate_limited("1")), FakeClock()

    error = await _fail(_source(site, clock, policy=RetryPolicy(rate_limit_retries=0)))

    assert error.kind is SourceErrorKind.BLOCKED
    assert len(site.requests) == 1
    assert clock.sleeps == []


async def test_429_without_retry_after_waits_the_default_pause() -> None:
    site, clock = Site(_rate_limited(None), _ok()), FakeClock()

    await _source(site, clock).fetch_offers([ARTICLE])

    assert clock.sleeps == [1.0]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("60", 60.0),
        ("0", 0.0),
        (" 5 ", 5.0),
        (None, None),
        ("", None),
        ("-1", None),
        ("1.5", None),
        ("soon", None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
    ],
)
def test_retry_after_parsing(value: str | None, expected: float | None) -> None:
    headers = {} if value is None else {"Retry-After": value}
    assert retry_after_seconds(httpx.Response(429, headers=headers)) == expected


@pytest.mark.parametrize(
    "kwargs",
    [{"attempts": 0}, {"pause_seconds": -1.0}, {"rate_limit_retries": -1}],
)
def test_invalid_retry_policy_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - the message differs per field
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


# --- The budget is shared by the whole source ---------------------------------------------


async def test_request_timeout_is_capped_by_the_budget_left() -> None:
    clock = FakeClock()
    timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"]["read"])
        clock.now += 3.0
        return httpx.Response(200, text=OK_PAGE)

    source = HtmlCatalogSource(
        "http://fake-sites.test",
        transport=httpx.MockTransport(handler),
        budget_seconds=8.0,
        clock=clock,
        concurrency=1,
    )
    with pytest.raises(SourceError) as excinfo:
        await source.fetch_offers(["A1", "A2", "A3", "A4"])

    # 8 s for the whole source: 5, 5 (capped at 5), then 2 s left, then nothing.
    assert timeouts == [5.0, 5.0, 2.0]
    assert excinfo.value.kind is SourceErrorKind.NETWORK
    assert "запрос не отправлен" in excinfo.value.message
    assert "бюджет времени 8 сек. исчерпан" in excinfo.value.message


async def test_slow_trickling_response_is_cut_at_the_end_of_the_budget() -> None:
    """httpx timeouts apply per read; the budget bounds the whole response (real clock)."""
    chunks_sent = 0

    async def trickle() -> AsyncIterator[bytes]:
        nonlocal chunks_sent
        for _ in range(50):
            chunks_sent += 1
            yield b"<p>"
            await asyncio.sleep(0.05)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=trickle())

    source = HtmlCatalogSource(
        "http://fake-sites.test",
        transport=httpx.MockTransport(handler),
        budget_seconds=0.3,
        timeout_seconds=0.5,
    )
    started = time.monotonic()
    with pytest.raises(SourceError) as excinfo:
        await source.fetch_offers([ARTICLE])
    elapsed = time.monotonic() - started

    assert excinfo.value.kind is SourceErrorKind.NETWORK
    assert "ответ не получен до конца бюджета" in excinfo.value.message
    assert elapsed < 1.0
    assert chunks_sent < 50


async def test_budget_spent_after_429_while_waiting_for_a_response_is_blocked() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _rate_limited("0")
        await asyncio.sleep(5)
        return httpx.Response(200, text=OK_PAGE)

    source = HtmlCatalogSource(
        "http://fake-sites.test", transport=httpx.MockTransport(handler), budget_seconds=0.2
    )
    with pytest.raises(SourceError) as excinfo:
        await source.fetch_offers([ARTICLE])

    assert excinfo.value.kind is SourceErrorKind.BLOCKED
    assert calls == 2


async def test_unrelated_timeout_error_is_not_masked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise TimeoutError

    with pytest.raises(TimeoutError):
        await _source(handler, FakeClock()).fetch_offers([ARTICLE])
