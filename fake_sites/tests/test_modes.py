"""Failure modes of both sources, switching and reset, the JSON API session."""

import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest

from fake_sites.app import create_app
from fake_sites.catalog import LISTINGS, SPIKE_ARTICLE, Site
from fake_sites.html_catalog import NBSP

type Scenario = Callable[[httpx.AsyncClient], Awaitable[None]]

A_ARTICLE = "0986452041"
NORMAL_MODES = {
    "html_catalog": {"mode": "normal", "retry_after": 60},
    "json_api": {"mode": "normal", "retry_after": 60},
}


def _run(scenario: Scenario) -> None:
    """Run ``scenario`` against one fresh application instance."""

    async def call() -> None:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://fake.test") as client:
            await scenario(client)

    asyncio.run(call())


async def _set_mode(client: httpx.AsyncClient, source: str, mode: str, **extra: Any) -> None:
    response = await client.put(f"/control/modes/{source}", json={"mode": mode, **extra})
    assert response.status_code == 200, response.text


async def _open_session(client: httpx.AsyncClient) -> httpx.Response:
    return await client.get("/json/")


async def _offers(client: httpx.AsyncClient, article: str) -> httpx.Response:
    return await client.get("/json/api/offers", params={"article": article})


# --- Control endpoints ----------------------------------------------------------


def test_both_sources_start_in_normal() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        response = await client.get("/control/modes")
        assert response.status_code == 200
        assert response.json() == NORMAL_MODES

    _run(scenario)


def test_mode_is_set_per_source_and_reset_returns_normal() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        response = await client.put(
            "/control/modes/json_api", json={"mode": "blocked", "retry_after": 5}
        )
        assert response.json() == {"mode": "blocked", "retry_after": 5}
        await _set_mode(client, "html_catalog", "markup_changed")
        assert (await client.get("/control/modes")).json() == {
            "html_catalog": {"mode": "markup_changed", "retry_after": 60},
            "json_api": {"mode": "blocked", "retry_after": 5},
        }

        response = await client.post("/control/reset")
        assert response.status_code == 200
        assert response.json() == NORMAL_MODES
        assert (await client.get("/control/modes")).json() == NORMAL_MODES
        # After reset the sites answer normally again.
        assert (await client.get("/html/search", params={"article": A_ARTICLE})).status_code == 200
        assert (await _open_session(client)).status_code == 200

    _run(scenario)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/control/modes/ftp_site", {"mode": "normal"}),
        ("/control/modes/html_catalog", {"mode": "on_fire"}),
        ("/control/modes/json_api", {"mode": "blocked", "retry_after": -1}),
        ("/control/modes/json_api", {}),
    ],
)
def test_invalid_mode_change_is_rejected(path: str, body: dict[str, Any]) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        assert (await client.put(path, json=body)).status_code == 422
        assert (await client.get("/control/modes")).json() == NORMAL_MODES

    _run(scenario)


def test_mode_switch_applies_to_the_next_request_without_restart() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        params = {"article": A_ARTICLE}
        assert (await client.get("/html/search", params=params)).status_code == 200
        await _set_mode(client, "html_catalog", "unavailable")
        assert (await client.get("/html/search", params=params)).status_code == 503
        await _set_mode(client, "html_catalog", "blocked")
        assert (await client.get("/html/search", params=params)).status_code == 403
        await _set_mode(client, "html_catalog", "normal")
        assert (await client.get("/html/search", params=params)).status_code == 200

        assert (await _open_session(client)).status_code == 200
        assert (await _offers(client, A_ARTICLE)).status_code == 200
        await _set_mode(client, "json_api", "unavailable")
        assert (await _offers(client, A_ARTICLE)).status_code == 503
        await _set_mode(client, "json_api", "normal")
        # The session survives a mode switch.
        assert (await _offers(client, A_ARTICLE)).status_code == 200

    _run(scenario)


# --- HTML catalog ---------------------------------------------------------------


def test_html_markup_changed_has_no_required_fields() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        await _set_mode(client, "html_catalog", "markup_changed")
        for article in (A_ARTICLE, "E340HD247"):
            response = await client.get("/html/search", params={"article": article})
            assert response.status_code == 200
            page = response.text
            assert 'id="offers"' not in page
            assert "no-results" not in page
            for css_class in ('class="offer"', 'class="price"', 'class="stock"'):
                assert css_class not in page
        changed = (await client.get("/html/search", params={"article": A_ARTICLE})).text
        assert 'class="product-card"' in changed
        assert f"1{NBSP}250,00{NBSP}₽" in changed  # the data is there, the markup is not

    _run(scenario)


def test_html_unavailable_is_503() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        await _set_mode(client, "html_catalog", "unavailable")
        response = await client.get("/html/search", params={"article": A_ARTICLE})
        assert response.status_code == 503

    _run(scenario)


def test_html_blocked_is_403_with_captcha() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        await _set_mode(client, "html_catalog", "blocked", retry_after=1)
        response = await client.get("/html/search", params={"article": A_ARTICLE})
        assert response.status_code == 403
        assert 'class="captcha"' in response.text
        assert "Retry-After" not in response.headers

    _run(scenario)


def test_html_price_spike_changes_only_the_control_product() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        await _set_mode(client, "html_catalog", "price_spike")
        spike = (await client.get("/html/search", params={"article": SPIKE_ARTICLE})).text
        assert f"1{NBSP}500,00{NBSP}₽" in spike
        assert f"2{NBSP}100,00{NBSP}₽" not in spike
        other = (await client.get("/html/search", params={"article": A_ARTICLE})).text
        assert f"1{NBSP}250,00{NBSP}₽" in other

    _run(scenario)


# --- JSON API -------------------------------------------------------------------


def test_storefront_page_sets_the_session_cookie_and_loads_offers_by_xhr() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        response = await _open_session(client)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert 'fetch("/json/api/offers?article="' in response.text
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("session=")
        assert "Path=/json" in cookie
        assert "HttpOnly" in cookie
        assert client.cookies.get("session")

    _run(scenario)


def test_api_without_session_cookie_is_401() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        response = await _offers(client, A_ARTICLE)
        assert response.status_code == 401
        client.cookies.set("session", "forged", domain="fake.test", path="/json")
        assert (await _offers(client, A_ARTICLE)).status_code == 401

    _run(scenario)


def test_api_returns_offers_with_string_prices() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        await _open_session(client)
        response = await _offers(client, "0 986-452 041")
        assert response.status_code == 200
        assert response.json() == {
            "article": "0 986-452 041",
            "offers": [
                {
                    "brand": "Bosch",
                    "article": "0986452041",
                    "price": "1250.00",
                    "stock": 7,
                    "delivery_days": 3,
                }
            ],
        }
        # Nothing found is an explicit empty list.
        assert (await _offers(client, "E340HD247")).json() == {
            "article": "E340HD247",
            "offers": [],
        }

    _run(scenario)


def test_api_serves_every_json_listing() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        await _open_session(client)
        served = set()
        for item in LISTINGS:
            if item.site is not Site.JSON:
                continue
            for offer in (await _offers(client, item.article)).json()["offers"]:
                assert isinstance(offer["price"], str)
                served.add((offer["brand"], offer["article"], Decimal(offer["price"])))
        expected = {(i.brand, i.article, i.price) for i in LISTINGS if i.site is Site.JSON}
        assert served == expected

    _run(scenario)


def test_api_markup_changed_renames_the_fields() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        await _open_session(client)
        await _set_mode(client, "json_api", "markup_changed")
        for article in (A_ARTICLE, "E340HD247"):
            response = await _offers(client, article)
            assert response.status_code == 200
            payload = response.json()
            assert "offers" not in payload
            assert "items" in payload["data"]
        item = (await _offers(client, A_ARTICLE)).json()["data"]["items"][0]
        assert not {"brand", "article", "price", "stock", "delivery_days"} & item.keys()

    _run(scenario)


def test_api_unavailable_is_503_on_every_page() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        await _open_session(client)
        await _set_mode(client, "json_api", "unavailable")
        assert (await _offers(client, A_ARTICLE)).status_code == 503
        assert (await _open_session(client)).status_code == 503

    _run(scenario)


def test_api_blocked_is_429_with_retry_after() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        await _open_session(client)
        await _set_mode(client, "json_api", "blocked")
        response = await _offers(client, A_ARTICLE)
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        session_page = await _open_session(client)
        assert session_page.status_code == 429

        await _set_mode(client, "json_api", "blocked", retry_after=3)
        assert (await _offers(client, A_ARTICLE)).headers["retry-after"] == "3"

    _run(scenario)


def test_api_price_spike_changes_only_the_control_product() -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        await _open_session(client)
        before = (await _offers(client, SPIKE_ARTICLE)).json()["offers"]
        assert [offer["price"] for offer in before] == ["2000.00"]
        await _set_mode(client, "json_api", "price_spike")
        spike = (await _offers(client, SPIKE_ARTICLE)).json()["offers"]
        assert [(o["brand"], o["price"]) for o in spike] == [("MANN-FILTER", "1500.00")]
        other = (await _offers(client, A_ARTICLE)).json()["offers"]
        assert [offer["price"] for offer in other] == ["1250.00"]
        # The HTML catalog is not affected by the JSON source's mode.
        html = (await client.get("/html/search", params={"article": SPIKE_ARTICLE})).text
        assert f"2{NBSP}100,00{NBSP}₽" in html

    _run(scenario)


def test_modes_are_independent_between_application_instances() -> None:
    async def first(client: httpx.AsyncClient) -> None:
        await _set_mode(client, "html_catalog", "blocked")

    async def second(client: httpx.AsyncClient) -> None:
        assert (await client.get("/control/modes")).json() == NORMAL_MODES

    _run(first)
    _run(second)
