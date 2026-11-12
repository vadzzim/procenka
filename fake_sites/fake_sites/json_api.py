"""The "hidden" JSON API: a page loads prices by XHR from an API behind a session.

Contract (normal mode) that the backend parser relies on:

* ``GET /json/`` is the storefront page; it sets the session cookie
  ``session`` (``Path=/json``) and its script fetches the offers by XHR;
* ``GET /json/api/offers?article=...`` answers 401 without a valid session
  cookie, otherwise ``{"article": ..., "offers": [...]}`` where every offer is
  ``{"brand": str, "article": str, "price": "1250.00", "stock": int,
  "delivery_days": int}``. Prices are strings with two decimals, as real
  APIs send money; ``"offers": []`` means nothing was found.

``markup_changed`` renames and nests the fields, so the required ones are gone.
"""

import secrets
from typing import Any

from fake_sites.catalog import Listing

SESSION_COOKIE = "session"
SESSION_COOKIE_PATH = "/json"


class SessionStore:
    """Sessions issued by the storefront page; one store per application instance."""

    def __init__(self) -> None:
        self._tokens: set[str] = set()

    def issue(self) -> str:
        token = secrets.token_urlsafe(16)
        self._tokens.add(token)
        return token

    def is_valid(self, token: str | None) -> bool:
        return token is not None and token in self._tokens


def offers_payload(query: str, listings: list[Listing]) -> dict[str, Any]:
    return {
        "article": query,
        "offers": [
            {
                "brand": item.brand,
                "article": item.article,
                "price": f"{item.price:.2f}",
                "stock": item.stock,
                "delivery_days": item.delivery_days,
            }
            for item in listings
        ],
    }


def changed_offers_payload(query: str, listings: list[Listing]) -> dict[str, Any]:
    """``markup_changed``: a new API version with another schema."""
    return {
        "query": query,
        "data": {
            "items": [
                {
                    "manufacturer": item.brand,
                    "partNumber": item.article,
                    "cost": {"amount": int(item.price * 100), "currency": "RUB"},
                    "availability": {"quantity": item.stock, "days": item.delivery_days},
                }
                for item in listings
            ]
        },
    }


STOREFRONT_PAGE = """<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Магазин запчастей</title></head>
<body>
<h1>Магазин запчастей</h1>
<form id="search"><input name="article" placeholder="Артикул"><button>Найти</button></form>
<table id="offers"><tbody></tbody></table>
<script>
document.getElementById("search").addEventListener("submit", async (event) => {
  event.preventDefault();
  const article = new FormData(event.target).get("article");
  const response = await fetch("/json/api/offers?article=" + encodeURIComponent(article), {
    credentials: "same-origin",
  });
  const data = await response.json();
  const body = document.querySelector("#offers tbody");
  body.replaceChildren(...data.offers.map((offer) => {
    const row = document.createElement("tr");
    for (const value of [offer.brand, offer.article, offer.price, offer.stock,
                         offer.delivery_days]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    return row;
  }));
});
</script>
</body>
</html>
"""
