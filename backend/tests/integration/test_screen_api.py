"""The screen API on PostgreSQL: ``/api/status``, ``/api/products``, ``/api/calculations/{id}``
(``plan.md``, "UI и обмен без Excel", "Правила расчёта" 9, "Прогоны и устойчивость").

Real parsers against fake-sites, runs executed by the worker's code (``tests.integration.demo``).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import insert, select

from app.db.enums import RunFinishReason, RunStatus, Source
from app.db.models import Calculation
from app.db.runs import claim_next_run, recover_interrupted_runs
from app.db.seed_data import (
    PRODUCT_A,
    PRODUCT_CEILING,
    PRODUCT_NO_OFFERS,
    PRODUCT_SPIKE,
    PRODUCT_UNPUBLISHED,
    SEED_PUBLISHED_AT,
    SeedProduct,
)
from app.parsers.base import OfferSource, ParsedOffer
from app.pricing.normalize import normalize_article
from app.worker.loop import process_next_run
from tests.integration.demo import SETTINGS, Demo

pytestmark = pytest.mark.integration


async def _products(demo: Demo) -> dict[str, dict[str, Any]]:
    """``GET /api/products`` keyed by the normalized article."""
    response = await demo.api.get("/api/products")
    assert response.status_code == 200, response.text
    return {row["product"]["article"]: row for row in response.json()["products"]}


def _row(rows: dict[str, dict[str, Any]], item: SeedProduct) -> dict[str, Any]:
    return rows[normalize_article(item.article)]


async def _calculation(demo: Demo, calculation_id: int) -> dict[str, Any]:
    response = await demo.api.get(f"/api/calculations/{calculation_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    return parsed


# --- /api/products -----------------------------------------------------------


async def test_products_before_any_run_show_the_seed(demo: Demo) -> None:
    rows = await _products(demo)
    assert len(rows) == 25
    a = _row(rows, PRODUCT_A)
    assert a["product"] == {
        "id": a["product"]["id"],
        "brand": "Bosch",
        "article": "0986452041",
        "brand_raw": "BOSCH",
        "article_raw": "0 986-452 041",
        "name": "Фильтр масляный",
    }
    assert a["purchase_price"] == "1000.00"
    assert a["published"] == {
        "price": "1260.00",
        "published_at": a["published"]["published_at"],
        "origin": "seed",
        "origin_label": "цена из исходных данных",
        "calculation_id": None,
        "calculation_run_id": None,
        "calculated_at": None,
    }
    assert _time(a["published"]["published_at"]) == SEED_PUBLISHED_AT
    assert a["latest_calculation"] is None
    assert _row(rows, PRODUCT_UNPUBLISHED)["published"] is None
    # Ordered by product id.
    ids = [row["product"]["id"] for row in rows.values()]
    assert ids == sorted(ids)


async def test_products_after_a_normal_run(demo: Demo) -> None:
    run = await demo.run()
    assert run["status"] == "succeeded"
    rows = await _products(demo)

    a = _row(rows, PRODUCT_A)
    a_calc = a["latest_calculation"]
    assert a_calc["run_id"] == run["id"]
    assert (a_calc["state"], a_calc["state_label"]) == ("published", "опубликована")
    assert (a_calc["reason"], a_calc["reason_label"]) == (None, None)
    assert a_calc["recommended_price"] == "1240.00"
    assert a_calc["change_percent_display"] == "-1.6"
    assert a_calc["partial_data"] is False
    _time(a_calc["calculated_at"])
    assert a["published"]["price"] == "1240.00"
    assert a["published"]["origin"] == "calculation"
    assert a["published"]["origin_label"] == "по расчёту"
    assert a["published"]["calculation_id"] == a_calc["id"]
    assert a["published"]["calculation_run_id"] == run["id"]
    assert a["published"]["calculated_at"] == a_calc["calculated_at"]

    s = _row(rows, PRODUCT_SPIKE)
    assert (s["latest_calculation"]["state"], s["latest_calculation"]["state_label"]) == (
        "unchanged",
        "без изменений",
    )
    assert s["latest_calculation"]["recommended_price"] == "1990.00"
    assert s["published"]["price"] == "1990.00"
    assert s["published"]["origin"] == "seed"

    febi = _row(rows, PRODUCT_UNPUBLISHED)
    assert febi["published"] is None
    assert febi["latest_calculation"]["state"] == "quarantine"
    assert febi["latest_calculation"]["state_label"] == "карантин"
    assert febi["latest_calculation"]["reason"] == "no_previous_price"
    assert febi["latest_calculation"]["reason_label"] == "нет предыдущей цены"
    assert febi["latest_calculation"]["recommended_price"] == "1090.00"

    # Retained: the reason, and the price with the date of the calculation behind it
    # (here the seed: "цена из исходных данных", no calculation).
    hengst = _row(rows, PRODUCT_NO_OFFERS)
    assert hengst["product"]["article"] == "E340HD247"
    h_calc = hengst["latest_calculation"]
    assert (h_calc["state"], h_calc["state_label"]) == ("retained", "оставлена предыдущая")
    assert (h_calc["reason"], h_calc["reason_label"]) == (
        "no_eligible_offers",
        "нет пригодных предложений",
    )
    assert h_calc["recommended_price"] is None
    assert h_calc["change_percent_display"] is None
    assert hengst["published"]["price"] == "690.00"
    assert hengst["published"]["origin"] == "seed"
    assert hengst["published"]["origin_label"] == "цена из исходных данных"
    assert hengst["published"]["calculated_at"] is None


class _WithoutArticles:
    """The real source, but the site no longer lists some articles."""

    def __init__(self, inner: OfferSource, articles: set[str]) -> None:
        self.source = inner.source
        self._inner = inner
        self._articles = articles

    async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
        return await self._inner.fetch_offers([a for a in articles if a not in self._articles])


async def test_retained_price_shows_the_date_of_the_calculation_that_published_it(
    demo: Demo,
) -> None:
    first = await demo.run()
    published_by = _row(await _products(demo), PRODUCT_A)["latest_calculation"]

    gone = {normalize_article(PRODUCT_A.article)}
    second_id = (await demo.api.post("/api/runs")).json()["id"]
    sources: list[OfferSource] = [_WithoutArticles(s, gone) for s in demo.sources]
    await process_next_run(demo.session_factory, sources, SETTINGS)

    a = _row(await _products(demo), PRODUCT_A)
    assert a["latest_calculation"]["run_id"] == second_id
    assert a["latest_calculation"]["state"] == "retained"
    assert a["latest_calculation"]["reason_label"] == "нет пригодных предложений"
    assert a["published"]["price"] == "1240.00"
    assert a["published"]["origin"] == "calculation"
    assert a["published"]["calculation_id"] == published_by["id"]
    assert a["published"]["calculation_run_id"] == first["id"]
    assert a["published"]["calculated_at"] == published_by["calculated_at"]


async def test_calculations_of_unfinished_and_interrupted_runs_are_not_shown(demo: Demo) -> None:
    first = await demo.run()
    shown = {row["product"]["id"]: row for row in (await _products(demo)).values()}

    # A run that recorded calculations and then stopped (worker killed before it finished).
    stale = (await demo.api.post("/api/runs")).json()["id"]
    assert await claim_next_run(demo.session_factory) == stale
    async with demo.session_factory() as session, session.begin():
        rows = (
            await session.scalars(select(Calculation).where(Calculation.run_id == first["id"]))
        ).all()
        columns = [c.key for c in Calculation.__table__.columns if c.key not in {"id", "run_id"}]
        await session.execute(
            insert(Calculation),
            [
                {
                    **{key: getattr(row, key) for key in columns},
                    "run_id": stale,
                    "state": "retained",
                    "reason": "no_eligible_offers",
                    "recommended_price": None,
                    "change_percent": None,
                    "change_percent_display": None,
                }
                for row in rows
            ],
        )

    async def latest() -> dict[int, dict[str, Any] | None]:
        rows = await _products(demo)
        return {row["product"]["id"]: row["latest_calculation"] for row in rows.values()}

    # While the run is running, its calculations are not the latest word.
    assert await latest() == {pid: row["latest_calculation"] for pid, row in shown.items()}
    assert await recover_interrupted_runs(demo.session_factory) == [stale]
    status = (await demo.api.get("/api/status")).json()
    assert status["last_finished_run"]["finish_reason"] == RunFinishReason.INTERRUPTED.value
    # Interrupted: still not shown; every latest calculation is from the first run.
    after = await latest()
    assert after == {pid: row["latest_calculation"] for pid, row in shown.items()}
    assert {calc["run_id"] for calc in after.values() if calc is not None} == {first["id"]}


async def test_calculations_of_a_failed_run_are_not_shown(demo: Demo) -> None:
    first = await demo.run()
    await demo.set_mode(Source.HTML_CATALOG, "unavailable")
    await demo.set_mode(Source.JSON_API, "unavailable")
    failed = await demo.run()
    assert failed["status"] == "failed"
    a = _row(await _products(demo), PRODUCT_A)
    assert a["latest_calculation"]["run_id"] == first["id"]


# --- /api/calculations/{id} ----------------------------------------------------


async def test_calculation_of_a_explains_the_published_price(demo: Demo) -> None:
    run = await demo.run()
    a_id = _row(await _products(demo), PRODUCT_A)["latest_calculation"]["id"]
    calc = await _calculation(demo, a_id)

    assert calc["id"] == a_id
    assert calc["run_id"] == run["id"]
    assert calc["run_status"] == "succeeded"
    assert calc["product"]["article_raw"] == "0 986-452 041"
    assert calc["rules_version"]
    assert calc["partial_data"] is False
    assert calc["inputs"] == {
        "purchase_price": "1000.00",
        "min_markup_percent": "20.00",
        "step": "10.00",
        "ceiling": None,
        "quarantine_threshold_percent": "20.00",
        "suspicious_price_factor": "0.5000",
        "max_delivery_days": 14,
    }
    accepted = sorted((o["source"], o["brand_raw"], o["price"]) for o in calc["accepted_offers"])
    assert accepted == [
        ("html_catalog", "BOSCH", "1250.00"),
        ("json_api", "Bosch", "1250.00"),
    ]
    html = next(o for o in calc["accepted_offers"] if o["source"] == "html_catalog")
    assert html == {
        "source": "html_catalog",
        "source_label": "HTML-каталог",
        "brand_raw": "BOSCH",
        "article_raw": "0 986-452 041",
        "brand": "Bosch",
        "article": "0986452041",
        "price": "1250.00",
        "stock": html["stock"],
        "delivery_days": html["delivery_days"],
    }
    rejected = sorted(
        (o["brand_raw"], o["price"], o["reason"], o["reason_label"])
        for o in calc["rejected_offers"]
    )
    assert rejected == [
        ("Bosch", "1.00", "suspiciously_low_price", "подозрительно низкая цена"),
        ("MANN", "1150.00", "other_brand", "другой бренд"),
    ]
    assert calc["min_competitor_price"] == "1250.00"
    assert calc["lower_bound"] == "1200.00"
    assert calc["upper_bound"] is None
    assert calc["target_price_raw"] == calc["target_price"] == "1240.00"
    assert calc["applied_constraints"] == []
    assert calc["recommended_price"] == "1240.00"
    assert calc["previous_price"] == "1260.00"
    assert calc["change_percent_display"] == "-1.6"
    assert calc["change_percent"].startswith("-1.587")
    assert (calc["state"], calc["state_label"]) == ("published", "опубликована")
    assert calc["reason"] is None

    formula = calc["formula"]
    assert (
        "Нижняя граница = 1 000,00 × (1 + 20 %) = 1 200,00 (округление вверх до копейки)" in formula
    )
    assert "Целевая = 1 250,00 − 10,00 = 1 240,00" in formula
    assert "Итог 1 240,00: ограничений нет" in formula
    assert (
        "Изменение = |1 240,00 − 1 260,00| / 1 260,00 × 100 = 1,6 % ≤ 20 % → опубликована"
        in formula
    )
    assert formula[-1].endswith("→ опубликована")


async def test_calculation_of_s_in_price_spike_is_quarantined(demo: Demo) -> None:
    await demo.set_mode(Source.JSON_API, "price_spike")
    run = await demo.run()
    s = _row(await _products(demo), PRODUCT_SPIKE)
    assert s["latest_calculation"]["state"] == "quarantine"
    assert s["latest_calculation"]["change_percent_display"] == "-25.1"
    assert s["published"]["price"] == "1990.00"

    calc = await _calculation(demo, s["latest_calculation"]["id"])
    assert calc["run_id"] == run["id"]
    assert calc["min_competitor_price"] == "1500.00"
    assert calc["recommended_price"] == "1490.00"
    assert calc["previous_price"] == "1990.00"
    assert calc["change_percent_display"] == "-25.1"
    assert (calc["state"], calc["state_label"]) == ("quarantine", "карантин")
    assert (calc["reason"], calc["reason_label"]) == ("price_spike", "скачок цены")
    assert "Целевая = 1 500,00 − 10,00 = 1 490,00" in calc["formula"]
    assert (
        "Изменение = |1 490,00 − 1 990,00| / 1 990,00 × 100 = 25,1 % > 20 % → карантин"
        in calc["formula"]
    )


async def test_calculation_with_a_ceiling_names_the_constraint(demo: Demo) -> None:
    await demo.run()
    row = _row(await _products(demo), PRODUCT_CEILING)
    calc = await _calculation(demo, row["latest_calculation"]["id"])
    assert calc["upper_bound"] == "600.00"
    assert calc["inputs"]["ceiling"] == "600.00"
    assert calc["applied_constraints"] == [{"code": "ceiling", "label": "ограничено сверху"}]
    assert calc["recommended_price"] == "600.00"
    assert "Потолок = 600,00" in calc["formula"]
    assert "Итог 600,00: целевая 640,00 выше потолка 600,00 → ограничено сверху" in calc["formula"]


async def test_calculation_of_a_retained_price_says_why(demo: Demo) -> None:
    await demo.run()
    row = _row(await _products(demo), PRODUCT_NO_OFFERS)
    calc = await _calculation(demo, row["latest_calculation"]["id"])
    assert calc["accepted_offers"] == calc["rejected_offers"] == []
    assert calc["min_competitor_price"] is None
    assert calc["target_price"] is None
    assert calc["recommended_price"] is None
    assert calc["lower_bound"] == "625.00"
    assert calc["formula"][-1] == (
        "Новая цена не публикуется (нет пригодных предложений): остаётся опубликованная 690,00"
    )


async def test_partial_calculation_is_marked(demo: Demo) -> None:
    await demo.set_mode(Source.HTML_CATALOG, "markup_changed")
    await demo.run()
    a = _row(await _products(demo), PRODUCT_A)
    assert a["latest_calculation"]["partial_data"] is True
    calc = await _calculation(demo, a["latest_calculation"]["id"])
    assert calc["partial_data"] is True
    assert calc["run_status"] == "partial"
    assert calc["formula"][0].startswith("Расчёт по частичным данным")


async def test_unknown_calculation_is_404(demo: Demo) -> None:
    response = await demo.api.get("/api/calculations/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Расчёт не найден"}


# --- /api/status ---------------------------------------------------------------


async def test_status_without_runs(demo: Demo) -> None:
    response = await demo.api.get("/api/status")
    assert response.status_code == 200
    assert response.json() == {"latest_run": None, "active_run": None, "last_finished_run": None}


async def test_status_after_markup_changed_on_html(demo: Demo) -> None:
    await demo.set_mode(Source.HTML_CATALOG, "markup_changed")
    run = await demo.run()
    status = (await demo.api.get("/api/status")).json()

    assert status["active_run"] is None
    assert status["latest_run"] == status["last_finished_run"]
    last = status["last_finished_run"]
    assert last["id"] == run["id"]
    assert (last["status"], last["status_label"]) == (
        "partial",
        "частично: часть источников с ошибкой",
    )
    assert last["trigger"] == "manual"
    for key in ("created_at", "started_at", "finished_at"):
        _time(last[key])
    assert last["finish_reason"] is None
    html, json_api = last["sources"]
    assert (html["source"], html["source_label"], html["status"]) == (
        "html_catalog",
        "HTML-каталог",
        "failed",
    )
    assert (html["error_kind"], html["error_label"]) == ("structure", "изменилась структура")
    assert html["error_message"]
    assert html["offer_count"] == 0
    assert (json_api["source"], json_api["status"], json_api["error_kind"]) == (
        "json_api",
        "ok",
        None,
    )
    assert json_api["error_label"] is None
    assert json_api["offer_count"] > 0


async def test_status_tells_ok_with_no_offers_from_a_failure(demo: Demo) -> None:
    everything = {normalize_article(item.article) for item in [PRODUCT_A, PRODUCT_SPIKE]}
    run_id = (await demo.api.post("/api/runs")).json()["id"]
    sources: list[OfferSource] = [
        _NothingFound(demo.sources[0]),
        _WithoutArticles(demo.sources[1], everything),
    ]
    await process_next_run(demo.session_factory, sources, SETTINGS)
    await demo.set_mode(Source.JSON_API, "blocked")

    last = (await demo.api.get("/api/status")).json()["last_finished_run"]
    assert last["id"] == run_id
    assert last["status"] == "succeeded"
    html = last["sources"][0]
    assert (html["status"], html["error_kind"], html["offer_count"]) == ("ok", None, 0)

    second = await demo.run()
    last = (await demo.api.get("/api/status")).json()["last_finished_run"]
    assert last["id"] == second["id"]
    json_api = last["sources"][1]
    assert (json_api["status"], json_api["error_kind"], json_api["error_label"]) == (
        "failed",
        "blocked",
        "заблокирован",
    )


class _NothingFound:
    """The site answers, but lists nothing for any article."""

    def __init__(self, inner: OfferSource) -> None:
        self.source = inner.source

    async def fetch_offers(self, articles: Sequence[str]) -> list[ParsedOffer]:
        return []


async def test_status_shows_the_active_run_and_the_last_finished_one(demo: Demo) -> None:
    first = await demo.run()
    active = (await demo.api.post("/api/runs")).json()["id"]
    status = (await demo.api.get("/api/status")).json()
    assert status["active_run"]["id"] == status["latest_run"]["id"] == active
    assert (status["active_run"]["status"], status["active_run"]["status_label"]) == (
        "queued",
        "в очереди",
    )
    assert status["active_run"]["sources"] == []
    assert status["active_run"]["finished_at"] is None
    assert status["last_finished_run"]["id"] == first["id"]
    assert len(status["last_finished_run"]["sources"]) == 2

    await claim_next_run(demo.session_factory)
    await recover_interrupted_runs(demo.session_factory)
    status = (await demo.api.get("/api/status")).json()
    assert status["active_run"] is None
    last = status["last_finished_run"]
    assert (last["id"], last["status"], last["finish_reason"]) == (active, "failed", "interrupted")
    assert last["finish_reason_label"] == "прерван: воркер остановился во время прогона"
    assert last["sources"] == []
    assert RunStatus(last["status"]) is RunStatus.FAILED


async def test_run_endpoint_has_labels(demo: Demo) -> None:
    await demo.set_mode(Source.HTML_CATALOG, "unavailable")
    await demo.set_mode(Source.JSON_API, "unavailable")
    run = await demo.run()
    assert (run["status"], run["finish_reason"]) == ("failed", "all_sources_failed")
    assert run["finish_reason_label"] == "все источники завершились ошибкой"
    assert {s["error_label"] for s in run["sources"]} == {"недоступен"}
    response = await demo.api.get("/api/runs/12345")
    assert response.status_code == 404
