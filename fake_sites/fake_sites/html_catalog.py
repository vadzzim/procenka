"""The HTML catalog: a server-rendered search page.

Markup contract (normal mode) that the backend parser relies on:

* ``<section id="offers">`` always wraps the result;
* each offer is ``<tr class="offer">`` with cells ``.brand``, ``.article``,
  ``.price`` (``1 250,00 ₽``: non-breaking-space thousands, comma decimals),
  ``.stock`` (``12 шт.``) and ``.delivery`` (``3 дн.``);
* an empty result is ``<p class="no-results">`` inside ``#offers`` and no rows,
  so "nothing found" is distinguishable from broken markup.
"""

from decimal import Decimal
from html import escape

from fake_sites.catalog import Listing

NBSP = chr(0xA0)  # non-breaking space


def format_price(price: Decimal) -> str:
    """``Decimal("1250.00")`` -> ``"1 250,00 ₽"`` with non-breaking spaces."""
    whole, _, fraction = f"{price:.2f}".partition(".")
    groups: list[str] = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{NBSP.join(groups)},{fraction}{NBSP}₽"


def _row(item: Listing) -> str:
    return (
        '<tr class="offer">'
        f'<td class="brand">{escape(item.brand)}</td>'
        f'<td class="article">{escape(item.article)}</td>'
        f'<td class="price">{format_price(item.price)}</td>'
        f'<td class="stock">{item.stock} шт.</td>'
        f'<td class="delivery">{item.delivery_days} дн.</td>'
        "</tr>"
    )


def render_search_page(query: str, listings: list[Listing]) -> str:
    if listings:
        rows = "\n".join(_row(item) for item in listings)
        body = (
            '<table class="offers-table">\n'
            "<thead><tr><th>Бренд</th><th>Артикул</th><th>Цена</th>"
            "<th>Наличие</th><th>Срок</th></tr></thead>\n"
            f"<tbody>\n{rows}\n</tbody>\n</table>"
        )
    else:
        body = '<p class="no-results">По вашему запросу ничего не найдено</p>'
    return f"""<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Каталог запчастей — поиск</title></head>
<body>
<h1>Результаты поиска: {escape(query)}</h1>
<section id="offers">
{body}
</section>
</body>
</html>
"""


def render_changed_search_page(query: str, listings: list[Listing]) -> str:
    """``markup_changed``: a redesigned page with product cards instead of the table.

    There is no ``#offers`` block and no ``.price``/``.stock``/``.delivery``
    cells, so a parser bound to the old markup cannot find its required fields.
    """
    cards = "\n".join(
        '<div class="product-card">'
        f'<span class="product-card__title">{escape(item.brand)} {escape(item.article)}</span>'
        f'<span class="product-card__cost">{format_price(item.price)}</span>'
        "</div>"
        for item in listings
    )
    return f"""<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Каталог запчастей — новый дизайн</title></head>
<body>
<h1>Нашли по запросу «{escape(query)}»</h1>
<div class="catalog-results">
{cards}
</div>
</body>
</html>
"""


def render_captcha_page() -> str:
    """``blocked``: what the site shows instead of the result (served with HTTP 403)."""
    return """<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><title>Проверка: вы не робот?</title></head>
<body>
<form class="captcha" method="post" action="/html/captcha">
<p>Подтвердите, что вы не робот</p>
<img src="/html/captcha.png" alt="captcha">
<input name="answer"><button type="submit">Отправить</button>
</form>
</body>
</html>
"""
