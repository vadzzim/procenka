"""What the synthetic sites sell: one listing table for both sources.

The listings follow the "Ожидаемые предложения" table of ``docs/progress.md``
(step 3). Brands and articles are deliberately spelled the way a real
marketplace would, not the way the price list does, so the backend's
normalization is visible. Every listing is in stock and ships within 14 days.

The HTML catalog serves the ``HTML`` rows, the JSON API the ``JSON`` rows.
In ``price_spike`` mode a source sells the control product S for 1 500.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum


class Site(StrEnum):
    HTML = "html"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class Listing:
    site: Site
    brand: str
    article: str
    price: Decimal
    stock: int
    delivery_days: int


def normalize_article(value: str) -> str:
    """Letters and digits only, upper-cased: how the sites match a search query."""
    return "".join(ch for ch in value if ch.isalnum()).upper()


def _listing(
    site: Site, brand: str, article: str, price: str, *, stock: int = 5, days: int = 2
) -> Listing:
    return Listing(
        site=site,
        brand=brand,
        article=article,
        price=Decimal(price),
        stock=stock,
        delivery_days=days,
    )


def _html(brand: str, article: str, price: str, *, stock: int = 5, days: int = 2) -> Listing:
    return _listing(Site.HTML, brand, article, price, stock=stock, days=days)


def _json(brand: str, article: str, price: str, *, stock: int = 5, days: int = 2) -> Listing:
    return _listing(Site.JSON, brand, article, price, stock=stock, days=days)


LISTINGS: tuple[Listing, ...] = (
    # Product A (Bosch 0986452041): 1 250 counts; the other two are discarded by the
    # pricing rules ("other brand" and "suspiciously low price": "1 250" -> 1).
    _html("BOSCH", "0 986-452 041", "1250.00", stock=12, days=1),
    _html("MANN", "0986452041", "1150.00", stock=3, days=4),
    _html("Bosch", "0 986 452 041", "1.00", stock=8, days=2),
    _json("Bosch", "0986452041", "1250.00", stock=7, days=3),
    # The price_spike control product (MANN-FILTER W71275): the minimum is in JSON.
    _html("MANN", "W 712/75", "2100.00", stock=4, days=2),
    _json("MANN-FILTER", "W712/75", "2000.00", stock=9, days=1),
    # Every other product: the same minimum in both sources.
    _html("MAHLE", "OC90", "650.00"),
    _json("Mahle Original", "OC 90", "650.00"),
    _html("Febi", "16410", "1100.00", days=5),
    _json("FEBI BILSTEIN", "16 410", "1100.00", days=6),
    _html("Mercedes-Benz", "A0001802609", "1190.00"),
    _json("MB", "A 000 180 26 09", "1190.00"),
    _html("MB", "A 271 180 03 09", "1470.00", days=7),
    _json("Mercedes", "A2711800309", "1470.00", days=7),
    _html("Volkswagen", "06J115403Q", "960.00"),
    _json("VW", "06J 115 403 Q", "960.00"),
    _html("VW", "1K0 698 151 A", "3390.00", stock=2),
    _json("Volkswagen", "1K0698151A", "3390.00", stock=2),
    _html("Toyota", "90915-YZZE1", "645.00", stock=30),
    _json("TOYOTA", "90915YZZE1", "645.00", stock=30),
    _html("TOYOTA", "0446533471", "4310.00", days=10),
    _json("Toyota", "04465-33471", "4310.00", days=10),
    _html("VALEO", "574105", "850.00"),
    _json("Valeo", "574 105", "850.00"),
    _html("Valeo", "826704", "13100.00", stock=1, days=14),
    _json("VALEO", "826-704", "13100.00", stock=1, days=14),
    _html("MANN", "C2029", "890.00"),
    _json("Mann-Filter", "C 2029", "890.00"),
    _html("MANN-FILTER", "CUK 2939", "1230.00"),
    _json("MANN", "CUK2939", "1230.00"),
    _html("Bosch", "0242236562", "625.00", stock=40),
    _json("BOSCH", "0 242 236 562", "625.00", stock=40),
    _html("BOSCH", "3 397 007 297", "1740.00"),
    _json("Bosch", "3397007297", "1740.00"),
    _html("Knecht", "LX1780/3", "1100.00"),
    _json("MAHLE", "LX 1780/3", "1100.00"),
    _html("NGK", "BKR6E11", "400.00", stock=50),
    _json("ngk", "BKR6E-11", "400.00", stock=50),
    _html("Denso", "IK20TT", "1310.00"),
    _json("DENSO", "IK 20TT", "1310.00"),
    _html("SKF", "VKBA3525", "2850.00", days=3),
    _json("Skf", "VKBA 3525", "2850.00", days=3),
    _html("TRW", "GDB 1550", "2380.00"),
    _json("trw", "GDB1550", "2380.00"),
    _html("Sachs", "313410", "4760.00", days=8),
    _json("SACHS", "313 410", "4760.00", days=8),
    _html("Lemforder", "2595701", "5700.00", days=9),
    _json("LEMFORDER", "25957 01", "5700.00", days=9),
    _html("HENGST", "H 90 W 03", "525.00"),
    _json("Hengst Filter", "H90W03", "525.00"),
    # Hengst E340HD247 is deliberately sold nowhere: "no eligible offers".
)


# The price_spike control product (MANN-FILTER W71275) and its price in that mode.
SPIKE_ARTICLE = "W71275"
SPIKE_PRICE = Decimal("1500.00")


def search(site: Site, article: str, *, price_spike: bool = False) -> list[Listing]:
    """Listings of ``site`` whose normalized article equals the normalized query.

    With ``price_spike`` the control product S costs ``SPIKE_PRICE``; nothing
    else changes.
    """
    wanted = normalize_article(article)
    if not wanted:
        return []
    found = [
        item for item in LISTINGS if item.site is site and normalize_article(item.article) == wanted
    ]
    if price_spike and wanted == SPIKE_ARTICLE:
        found = [replace(item, price=SPIKE_PRICE) for item in found]
    return found
