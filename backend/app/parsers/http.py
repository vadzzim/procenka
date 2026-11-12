"""HTTP requests of a source: the retry policy and the time budget.

Every response or failure maps to one of three outcomes (``plan.md``,
"Источники и парсеры"):

* timeout, connection or proxy error, HTTP 5xx -> network: limited retries
  with a pause (``RetryPolicy.attempts`` in total), then a network error;
* HTTP 403 -> blocked at once, never retried;
* HTTP 429 -> retried after ``Retry-After`` (at most
  ``RetryPolicy.rate_limit_retries`` times) if the pause fits into what is
  left of the budget; otherwise blocked at once, without waiting;
* any other response is returned to the parser, which decides (200 is data,
  401 is a session matter, the rest is a structure error).

The budget covers the whole collection of a source, pauses included: no
request is sent and no pause is started after it is spent, and a request may
not take longer than what is left of it. Running out of budget is a network
error after network failures and a blocked error after 429.

Retries are an explicit loop rather than tenacity: the decision depends on
the response (403 vs 429 vs 5xx), the pause on ``Retry-After`` and on the
budget left, and the clock and sleep are injected so tests never sleep. The
deadline of a request in flight is the real event-loop clock (``asyncio.timeout``).
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.db.enums import SourceErrorKind
from app.logs import get_logger
from app.parsers.base import SourceError
from app.parsers.fields import structure_error

logger = get_logger("app.parsers")


class Clock(Protocol):
    def monotonic(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


SYSTEM_CLOCK = SystemClock()


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    # Attempts in total (the first one included) on network failures.
    attempts: int = 3
    # Pause between attempts after a network failure; also used after a 429
    # without a usable Retry-After.
    pause_seconds: float = 1.0
    # Retries after HTTP 429.
    rate_limit_retries: int = 2

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")
        if self.pause_seconds < 0:
            raise ValueError("pause_seconds must not be negative")
        if self.rate_limit_retries < 0:
            raise ValueError("rate_limit_retries must not be negative")


class Budget:
    """Time left for collecting one source; starts when created."""

    def __init__(self, seconds: float, clock: Clock) -> None:
        self.seconds = seconds
        self._clock = clock
        self._deadline = clock.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self._deadline - self._clock.monotonic())


def _seconds(value: float) -> str:
    number = f"{value:g}" if value == int(value) else f"{value:.1f}"
    return f"{number} сек."


def retry_after_seconds(response: httpx.Response) -> float | None:
    """``Retry-After`` in seconds; ``None`` if absent or not a delay in seconds.

    The HTTP-date form is not supported: it is treated as absent.
    """
    value = response.headers.get("Retry-After", "").strip()
    if not value.isascii() or not value.isdigit():
        return None
    return float(int(value))


def blocked_error(message: str) -> SourceError:
    return SourceError(SourceErrorKind.BLOCKED, message)


def network_error(message: str) -> SourceError:
    return SourceError(SourceErrorKind.NETWORK, message)


class SourceHttp:
    """GET requests of one source collection with retries within one budget."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        budget: Budget,
        policy: RetryPolicy,
        clock: Clock,
        request_timeout_seconds: float,
    ) -> None:
        self.client = client
        self._budget = budget
        self._policy = policy
        self._clock = clock
        self._request_timeout = request_timeout_seconds

    async def get(
        self, path: str, *, params: dict[str, str] | None = None, what: str
    ) -> httpx.Response:
        """Send ``GET path`` until it yields a response the parser handles itself.

        ``what`` names the request in error messages, e.g. ``"артикул W71275"``.
        Raises ``SourceError`` (network or blocked) per the module rules.
        """
        where = f"{path} ({what})"
        network_failures = 0
        rate_limit_retries = 0
        last_kind: SourceErrorKind | None = None
        last_failure = ""
        while True:
            remaining = self._budget.remaining()
            if remaining <= 0:
                raise self._budget_spent(last_kind, last_failure or "запрос не отправлен", where)
            failure: str
            # httpx timeouts limit each network operation, not the whole response (a
            # slow trickle of chunks never trips them), so the budget is also a hard
            # deadline on the request as a whole.
            deadline = asyncio.timeout(remaining)
            try:
                async with deadline:
                    response = await self.client.get(
                        path, params=params, timeout=min(self._request_timeout, remaining)
                    )
            except TimeoutError:
                if not deadline.expired():
                    raise
                raise self._budget_spent(
                    last_kind, "ответ не получен до конца бюджета", where
                ) from None
            except httpx.TimeoutException:
                failure = "таймаут запроса"
            except httpx.DecodingError as exc:
                raise structure_error(f"не удалось декодировать ответ: {where}") from exc
            except httpx.RequestError as exc:
                failure = f"ошибка соединения {type(exc).__name__}"
            else:
                status = response.status_code
                if status == 403:
                    raise blocked_error(f"доступ запрещён (страница капчи): HTTP 403 на {where}")
                if status == 429:
                    await self._wait_after_rate_limit(response, rate_limit_retries, where)
                    rate_limit_retries += 1
                    last_kind, last_failure = SourceErrorKind.BLOCKED, "HTTP 429"
                    continue
                if status < 500:
                    return response
                failure = f"HTTP {status}"

            network_failures += 1
            last_kind, last_failure = SourceErrorKind.NETWORK, failure
            if network_failures >= self._policy.attempts:
                raise network_error(
                    f"площадка недоступна: {failure} на {where}, попыток: {network_failures}"
                )
            pause = self._policy.pause_seconds
            remaining = self._budget.remaining()
            if pause >= remaining:
                raise network_error(
                    f"площадка недоступна: {failure} на {where}, попыток: {network_failures}; "
                    f"пауза {_seconds(pause)} не помещается в остаток бюджета "
                    f"{_seconds(remaining)} из {_seconds(self._budget.seconds)}"
                )
            logger.warning(
                "network failure, retrying",
                failure=failure,
                path=path,
                attempt=network_failures,
                pause_seconds=pause,
            )
            await self._clock.sleep(pause)

    async def _wait_after_rate_limit(
        self, response: httpx.Response, retries_done: int, where: str
    ) -> None:
        """Sleep before retrying a 429, or raise blocked if the retry is not allowed."""
        if retries_done >= self._policy.rate_limit_retries:
            raise blocked_error(
                f"слишком много запросов: HTTP 429 на {where}, "
                f"повторов после Retry-After: {retries_done}"
            )
        header = retry_after_seconds(response)
        pause = self._policy.pause_seconds if header is None else header
        remaining = self._budget.remaining()
        if pause >= remaining:
            shown = "без Retry-After" if header is None else f"Retry-After {_seconds(pause)}"
            raise blocked_error(
                f"слишком много запросов: HTTP 429 на {where}, {shown} не помещается "
                f"в остаток бюджета {_seconds(remaining)} из {_seconds(self._budget.seconds)}"
            )
        logger.warning(
            "rate limited (HTTP 429), retrying after a pause",
            pause_seconds=pause,
            retry_after=header,
            retry=retries_done + 1,
        )
        await self._clock.sleep(pause)

    def _budget_spent(
        self, last_kind: SourceErrorKind | None, failure: str, where: str
    ) -> SourceError:
        """The budget ran out: blocked after a 429, network otherwise."""
        spent = f"бюджет времени {_seconds(self._budget.seconds)} исчерпан"
        if last_kind is SourceErrorKind.BLOCKED:
            return blocked_error(f"слишком много запросов: {failure} на {where}; {spent}")
        return network_error(f"площадка недоступна: {failure} на {where}; {spent}")
