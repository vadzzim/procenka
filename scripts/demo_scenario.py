"""Acceptance script: the 7 steps of the 5-minute demo scenario through the API.

Run from the repository root with the whole stack up (``docker compose up --build -d --wait``)::

    uv run --directory backend python ../scripts/demo_scenario.py [--base-url http://127.0.0.1:8080]

It talks to the stack only through nginx, like the screen does, and never
touches the database. Before step 2 it resets the demo (waiting for an active
run to finish), so it can be run again and again. Each step prints
``ШАГ N ... OK`` with its key numbers; the first mismatch prints ``FAIL`` and
exits with code 1, leaving the demo as it is for a look. After step 7 the demo
is reset again: database as after seed, both sources normal.
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
CONTROL_CASES_TEST = "tests/unit/pricing/test_control_cases.py"
RUNBOOK = "docs/runbook.md"

# Normalised brand and article of the scenario products (seed: PRODUCT_A, PRODUCT_SPIKE).
PRODUCT_A = ("Bosch", "0986452041")
PRODUCT_S = ("MANN-FILTER", "W71275")
SEED_PRODUCT_COUNT = 25

ACTIVE_STATUSES = {"queued", "running"}
RUN_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.5

Json = dict[str, Any]


class ScenarioError(Exception):
    """A mismatch with the scenario; the message says what was expected."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioError(message)


def plain(text: str) -> str:
    """Formula strings use non-breaking spaces in numbers: compare them as plain spaces."""
    return text.replace(" ", " ").replace(" ", " ")


class Demo:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def request(self, method: str, path: str, expected: set[int], **kwargs: Any) -> httpx.Response:
        response = self.client.request(method, path, **kwargs)
        check(
            response.status_code in expected,
            f"{method} {path}: HTTP {response.status_code}, ожидалось {sorted(expected)}: "
            f"{response.text[:300]}",
        )
        return response

    def get(self, path: str) -> Json:
        data: Json = self.request("GET", path, {200}).json()
        return data

    def wait_idle(self) -> None:
        deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
        while self.get("/api/status")["active_run"] is not None:
            check(time.monotonic() < deadline, "активный прогон не завершился вовремя")
            time.sleep(POLL_SECONDS)

    def reset(self) -> Json:
        """POST /api/demo/reset; on 409 (a run is active) wait for it and try again."""
        deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
        while True:
            response = self.request("POST", "/api/demo/reset", {200, 409})
            if response.status_code == 200:
                data: Json = response.json()
                return data
            check(time.monotonic() < deadline, f"сброс не удался: {response.json()['detail']}")
            self.wait_idle()

    def set_mode(self, source: str, mode: str) -> None:
        data = self.request("PUT", f"/api/demo/modes/{source}", {200}, json={"mode": mode}).json()
        check(data["mode"] == mode, f"{source}: режим {data['mode']}, ожидался {mode}")

    def run(self) -> Json:
        """Start a manual run and wait until it finishes."""
        created = self.request("POST", "/api/runs", {201}).json()
        check(created["created"], f"прогон {created['id']} уже существовал")
        deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
        while True:
            run = self.get(f"/api/runs/{created['id']}")
            if run["status"] not in ACTIVE_STATUSES:
                return run
            check(time.monotonic() < deadline, f"прогон {run['id']} не завершился вовремя")
            time.sleep(POLL_SECONDS)

    def products(self) -> dict[tuple[str, str], Json]:
        rows = self.get("/api/products")["products"]
        return {(row["product"]["brand"], row["product"]["article"]): row for row in rows}

    def product(self, key: tuple[str, str]) -> Json:
        rows = self.products()
        check(key in rows, f"товар {key} не найден в /api/products")
        return rows[key]


def published(row: Json) -> str | None:
    return None if row["published"] is None else row["published"]["price"]


def source_of(run: Json, source: str) -> Json:
    results: list[Json] = run["sources"]
    for result in results:
        if result["source"] == source:
            return result
    raise ScenarioError(f"прогон {run['id']}: нет результата источника {source}")


def check_run(run: Json, status: str) -> None:
    check(
        run["status"] == status,
        f"прогон {run['id']}: статус {run['status']} ({run['status_label']}), ожидался {status}",
    )


def check_source_failed(run: Json, kind: str, label: str) -> Json:
    html = source_of(run, "html_catalog")
    check(
        html["status"] == "failed" and html["error_kind"] == kind and html["error_label"] == label,
        f"HTML-каталог: {html['status']}/{html['error_kind']} «{html['error_label']}», "
        f"ожидалось failed/{kind} «{label}»",
    )
    json_api = source_of(run, "json_api")
    check(json_api["status"] == "ok", f"JSON API: {json_api['status']}, ожидалось ok")
    return html


def check_published(row: Json, price: str, name: str) -> None:
    check(published(row) == price, f"{name}: опубликована {published(row)}, ожидалось {price}")


def step1(demo: Demo) -> str:
    health = demo.get("/health")
    check(health["status"] == "ok", f"/health: {health}")
    report = demo.reset()
    count = len(demo.products())
    check(count == SEED_PRODUCT_COUNT, f"товаров {count}, ожидалось {SEED_PRODUCT_COUNT}")
    modes = {m["source"]: m["mode"] for m in demo.get("/api/demo/modes")["modes"]}
    check(set(modes.values()) == {"normal"}, f"режимы {modes}, ожидалось normal")
    check(demo.get("/api/status")["latest_run"] is None, "после сброса остались прогоны")
    return (
        f"/health ok, товаров {count}, опубликованных цен {report['published_prices']}, "
        f"режимы {modes}, прогонов нет"
    )


def step2(demo: Demo) -> str:
    run = demo.run()
    check_run(run, "succeeded")
    row = demo.product(PRODUCT_A)
    check_published(row, "1240.00", "A")
    calc_info = row["latest_calculation"]
    check(calc_info["state"] == "published", f"A: состояние {calc_info['state']}")
    calc = demo.get(f"/api/calculations/{calc_info['id']}")
    accepted = sorted({o["price"] for o in calc["accepted_offers"]})
    check(accepted == ["1250.00"], f"A: учтены цены {accepted}, ожидалось 1250.00")
    rejected = {o["reason_label"] for o in calc["rejected_offers"]}
    expected_rejected = {"другой бренд", "подозрительно низкая цена"}
    check(rejected == expected_rejected, f"A: отброшены {rejected}, ожидалось {expected_rejected}")
    formula = [plain(line) for line in calc["formula"]]
    check(
        any("1 240,00" in line for line in formula),
        f"A: в формуле нет 1 240,00: {formula}",
    )
    target = next((line for line in formula if line.startswith("Целевая")), "")
    return (
        f"прогон {run['id']} {run['status']}; A опубликована {published(row)} "
        f"(было 1260.00); учтено {', '.join(accepted)}; отброшены: "
        f"{', '.join(sorted(rejected))}; «{target}»"
    )


def step3(demo: Demo) -> str:
    demo.set_mode("html_catalog", "markup_changed")
    run = demo.run()
    check_run(run, "partial")
    html = check_source_failed(run, "structure", "изменилась структура")
    rows = demo.products()
    a, s = rows[PRODUCT_A], rows[PRODUCT_S]
    check_published(a, "1240.00", "A")
    check(
        a["latest_calculation"]["run_id"] == run["id"] and a["latest_calculation"]["partial_data"],
        f"A: последний расчёт не по частичным данным прогона {run['id']}",
    )
    check(
        a["latest_calculation"]["state"] == "unchanged",
        f"A: состояние {a['latest_calculation']['state']}, ожидалось unchanged",
    )
    check_published(s, "1990.00", "S")
    return (
        f"прогон {run['id']} {run['status']}; HTML-каталог: «{html['error_label']}» "
        f"({html['error_message']}); JSON API ok; A {published(a)} без изменений "
        f"(по частичным данным); S {published(s)}"
    )


def step4(demo: Demo) -> str:
    demo.set_mode("html_catalog", "blocked")
    run = demo.run()
    check_run(run, "partial")
    html = check_source_failed(run, "blocked", "заблокирован")
    rows = demo.products()
    check_published(rows[PRODUCT_A], "1240.00", "A")
    check_published(rows[PRODUCT_S], "1990.00", "S")
    return (
        f"прогон {run['id']} {run['status']}; HTML-каталог: «{html['error_label']}» "
        f"({html['error_message']}); A {published(rows[PRODUCT_A])}, "
        f"S {published(rows[PRODUCT_S])} не изменились; runbook: {RUNBOOK}"
    )


def step5(demo: Demo) -> str:
    demo.set_mode("html_catalog", "normal")
    demo.set_mode("json_api", "price_spike")
    run = demo.run()
    check_run(run, "succeeded")
    s = demo.product(PRODUCT_S)
    calc = s["latest_calculation"]
    check(
        calc["run_id"] == run["id"] and calc["state"] == "quarantine",
        f"S: состояние {calc['state']} в прогоне {calc['run_id']}, ожидался карантин",
    )
    check(calc["recommended_price"] == "1490.00", f"S: рекомендация {calc['recommended_price']}")
    check(
        calc["change_percent_display"] == "-25.1", f"S: изменение {calc['change_percent_display']}"
    )
    check_published(s, "1990.00", "S")
    return (
        f"прогон {run['id']} {run['status']}; S «{calc['state_label']}», рекомендация "
        f"{calc['recommended_price']}, изменение {calc['change_percent_display']} %, "
        f"опубликована {published(s)}"
    )


def step6(demo: Demo) -> str:
    prices = demo.get("/api/published-prices")["prices"]
    by_key = {(p["brand"], p["article"]): p for p in prices}
    check(PRODUCT_S in by_key and PRODUCT_A in by_key, "в выдаче нет A или S")
    s, a = by_key[PRODUCT_S], by_key[PRODUCT_A]
    check(s["price"] == "1990.00", f"S в выдаче: {s['price']}, ожидалось 1990.00")
    s_prices = [p["price"] for p in prices if (p["brand"], p["article"]) == PRODUCT_S]
    check("1490.00" not in s_prices, "рекомендация S из карантина попала в выдачу")
    quarantined = demo.product(PRODUCT_S)["latest_calculation"]["id"]
    check(s["calculation_id"] != quarantined, "в выдаче расчёт S из карантина")
    check(a["price"] == "1240.00", f"A в выдаче: {a['price']}, ожидалось 1240.00")
    return f"цен в выдаче {len(prices)}; S {s['price']} (1490.00 нет); A {a['price']}"


def step7(_: Demo) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", CONTROL_CASES_TEST, "-q", "-p", "no:cacheprovider"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    match = re.search(r"control cases: (\d+) matched, (\d+) mismatched", completed.stdout)
    check(match is not None, f"pytest не напечатал сводку:\n{completed.stdout}{completed.stderr}")
    assert match is not None
    matched, mismatched = int(match[1]), int(match[2])
    check(
        completed.returncode == 0 and matched > 0 and mismatched == 0,
        f"{match[0]}, код pytest {completed.returncode}:\n{completed.stdout[-2000:]}",
    )
    return match[0]


STEPS = (
    (1, "запуск: /health, seed, режимы normal", step1),
    (2, "ручной прогон: A = 1 240 ₽, расчёт объяснён", step2),
    (3, "HTML markup_changed: «изменилась структура», partial", step3),
    (4, "HTML blocked: «заблокирован», partial", step4),
    (5, "JSON price_spike: S в карантине", step5),
    (6, "JSON API: рекомендации из карантина нет", step6),
    (7, "контрольные примеры", step7),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8080", help="адрес nginx")
    args = parser.parse_args()
    # Windows consoles may not be UTF-8; the output is Russian.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
        demo = Demo(client)
        for number, title, step in STEPS:
            try:
                details = step(demo)
            except (ScenarioError, httpx.HTTPError) as exc:
                print(f"ШАГ {number}. {title} ... FAIL\n    {exc}", flush=True)
                print("Демо оставлено как есть; сброс: POST /api/demo/reset", flush=True)
                return 1
            print(f"ШАГ {number}. {title} ... OK\n    {details}", flush=True)
        try:
            demo.reset()
        except (ScenarioError, httpx.HTTPError) as exc:
            print(f"Сброс после сценария ... FAIL\n    {exc}", flush=True)
            return 1
    print("Сценарий пройден; демо сброшено, оба источника в normal.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
