import asyncio
from decimal import Decimal

import httpx
import pytest

from fake_sites.app import create_app
from fake_sites.html_catalog import NBSP, format_price


@pytest.mark.parametrize(
    ("price", "text"),
    [
        ("1.00", f"1,00{NBSP}₽"),
        ("650.00", f"650,00{NBSP}₽"),
        ("1250.00", f"1{NBSP}250,00{NBSP}₽"),
        ("13100.5", f"13{NBSP}100,50{NBSP}₽"),
        ("1234567.89", f"1{NBSP}234{NBSP}567,89{NBSP}₽"),
    ],
)
def test_format_price(price: str, text: str) -> None:
    assert format_price(Decimal(price)) == text


def _get(path: str) -> httpx.Response:

    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://fake") as client:
            return await client.get(path)

    return asyncio.run(call())


def test_search_page_lists_offers() -> None:
    response = _get("/html/search?article=0986452041")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    assert '<section id="offers">' in page
    assert page.count('<tr class="offer">') == 3
    assert f'<td class="price">1{NBSP}250,00{NBSP}₽</td>' in page
    assert '<td class="brand">MANN</td>' in page
    assert '<td class="stock">12 шт.</td>' in page
    assert '<td class="delivery">1 дн.</td>' in page
    assert "no-results" not in page


def test_search_page_without_results_has_explicit_marker() -> None:
    response = _get("/html/search?article=E340HD247")

    assert response.status_code == 200
    page = response.text
    assert '<section id="offers">' in page
    assert '<p class="no-results">' in page
    assert '<tr class="offer">' not in page


def test_search_query_is_escaped() -> None:
    page = _get("/html/search?article=%3Cscript%3E").text

    assert "<script>" not in page
    assert "&lt;script&gt;" in page
