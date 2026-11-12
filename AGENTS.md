# AGENTS.md

Инструкции для всех агентов, работающих в этом репозитории.

## Документы

- `plan.md` — спецификация демо. Источник истины по требованиям. Не меняй его; отступления записывай в `docs/progress.md`.
- `workflow.md` — процесс: шаги, цикл исполнителя, ревью, формат отчёта.
- `README.md` — что это, запуск, сценарий показа, API, архитектура, ограничения (для человека на чистом окружении).
- `docs/pricing.md` — правила расчёта цены, округление, карантин, сверка `control_cases.csv`.
- `docs/runbook.md` — «парсер сломался»: три типа ошибок источника, признаки, логи, что делать.
- `docs/screenshots/` — скриншоты экрана для README.
- `docs/progress.md` — журнал: что сделано на каждом шаге, принятые решения, отступления от плана, долги. Каждый шаг дописывает свой раздел.

## Окружение

- Windows 11, Docker Desktop. Оболочки: PowerShell и Git Bash.
- Всё, что запускается в контейнерах (shell-скрипты, entrypoint), должно иметь LF-окончания: это обеспечивает `.gitattributes`.
- Пути в коде и конфигурации — через `/`, без привязки к Windows.

## Команды проверки

Заполняются на шаге 1 и поддерживаются в актуальном виде. Шаг считается готовым, только если все они зелёные.

Все команды запускаются из корня репозитория (PowerShell и Git Bash одинаково). Python-проектов два: `backend/` и `fake_sites/` (синтетические площадки), поэтому uv вызывается с `--directory`.

```sh
docker compose up -d --wait postgres
uv run --directory backend ruff check . ../scripts
uv run --directory backend ruff format --check . ../scripts
uv run --directory backend mypy
uv run --directory backend pytest
uv run --directory fake_sites ruff check
uv run --directory fake_sites ruff format --check
uv run --directory fake_sites mypy
uv run --directory fake_sites pytest
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run gen:api:check
docker compose config --quiet
```

- CI: GitHub Actions (`.github/workflows/ci.yml`) на push и pull request в `main` гоняет эти же команды в джобах `backend` (PostgreSQL 16 — service container, `TEST_DATABASE_URL` указывает на него), `fake_sites`, `frontend` и, кроме них, `compose-e2e` — скрипт приёмки на поднятом стеке (см. «Приёмка»). Шаг готов, когда и локальные команды, и CI на `main` зелёные: `gh run list --branch main`, `gh run watch <id>`, `gh run view <id> --log-failed`. Версии actions — закреплённые теги (`astral-sh/setup-uv` мажорных тегов не публикует — полный тег); uv в CI той же минорной версии, что в Dockerfile.

- Фронтенд (`frontend/`): Vite + React + TypeScript (strict) + TanStack Query, npm с `package-lock.json`. Типы API — `frontend/src/api/schema.d.ts`, сгенерированный `openapi-typescript` из `backend/openapi.json` и закоммиченный; после регенерации `openapi.json` выполни `npm --prefix frontend run gen:api`, иначе `gen:api:check` падает. Тесты — Vitest + Testing Library (jsdom), фикстуры типизированы сгенерированными типами. Локальная разработка: `npm --prefix frontend run dev` (Vite проксирует `/api` и `/health` на `http://127.0.0.1:8000`, переопределяется `API_PROXY_TARGET`).

- `scripts/` (скрипт приёмки) работает в окружении backend: ruff проверяет его по конфигурации backend (`scripts/ruff.toml` расширяет `backend/pyproject.toml`), mypy — через `files` в `backend/pyproject.toml`.

- `fake_sites` подключён к backend как dev-зависимость по пути (editable): тесты backend ходят в настоящее приложение fake-sites через `httpx.ASGITransport`, без сети и без запущенного контейнера.

- `pytest` гоняет и unit-, и интеграционные тесты (маркер `integration`). Если PostgreSQL недоступен, интеграционные тесты падают, а не пропускаются.
- Интеграционные тесты берут URL из `TEST_DATABASE_URL`, по умолчанию `postgresql+asyncpg://procenka:procenka@localhost:54329/procenka_test`, и сами создают базу `procenka_test`, если её нет. Порт postgres на хосте — `POSTGRES_HOST_PORT` (по умолчанию 54329); пример переменных — `.env.example`.
- `.env` из корня читает только `docker compose`, pytest его не загружает. Если порт или учётные данные postgres изменены в `.env`, экспортируй `TEST_DATABASE_URL` в оболочке или запускай `uv run --directory backend --env-file ../.env pytest` (путь `--env-file` считается от `backend/`).
- Быстрый прогон без БД: `uv run --directory backend pytest -m "not integration"`. Готовность шага по нему не проверяется.
- OpenAPI-схема API зафиксирована в `backend/openapi.json` (из неё генерируются типы TS). После изменения API перегенерируй её: `uv run --directory backend python -m app.api.openapi` (`--check` — только сравнить). Тест `tests/unit/api/test_openapi.py` падает, если файл устарел или в схеме появилось поле `type: number`.
- Согласованность моделей и миграций (`alembic check`), а также `upgrade head` → `downgrade base` → `upgrade head` проверяет интеграционный тест `tests/integration/test_migrations.py` в отдельной базе `procenka_test_migrations`; отдельной команды проверки нет.

## База демо

Приложение берёт URL из `DATABASE_URL`, по умолчанию `postgresql+asyncpg://procenka:procenka@localhost:54329/procenka` (основная база compose-postgres).

```sh
uv run --directory backend alembic upgrade head      # применить миграции
uv run --directory backend python -m app.db.seed     # загрузить демо-набор (только недостающие товары)
uv run --directory backend python -m app.db.reset    # очистить всё и вернуть точное состояние seed
```

Команды seed и reset миграции не применяют.

Запуск всего демо и ручной прогон:

```sh
docker compose up --build -d                       # postgres, fake-sites, api (миграции и seed при старте), worker, nginx
# экран: http://127.0.0.1:8080 (nginx: статика фронтенда, /api/ и /health проксируются на api:8000)
curl http://127.0.0.1:8000/health                   # 200 {"status":"ok"} или 503, если БД не отвечает
curl -X POST http://127.0.0.1:8000/api/runs         # поставить прогон; повторный запрос вернёт тот же id
curl http://127.0.0.1:8000/api/runs/<id>            # статус прогона и источников
curl http://127.0.0.1:8000/api/status               # последний, активный и последний завершённый прогон с источниками
curl http://127.0.0.1:8000/api/products             # таблица экрана: закупка, опубликованная цена, последний расчёт
curl http://127.0.0.1:8000/api/calculations/<id>    # «Почему цена такая»: предложения, границы, формула, confirmation
curl -X POST http://127.0.0.1:8000/api/calculations/<id>/confirm   # подтвердить карантин: 200 цена, 409 {code, detail}, 404
curl http://127.0.0.1:8000/api/published-prices     # опубликованные цены
curl http://127.0.0.1:8000/api/demo/modes           # режимы источников через API (прокси к fake-sites)
curl -X PUT http://127.0.0.1:8000/api/demo/modes/html_catalog -H 'content-type: application/json' -d '{"mode":"markup_changed"}'
curl -X POST http://127.0.0.1:8000/api/demo/reset   # БД в состояние seed и оба источника в normal; 409, если есть активный прогон
curl http://127.0.0.1:8001/control/modes            # режимы источников fake-sites
curl -X PUT http://127.0.0.1:8001/control/modes/html_catalog -H 'content-type: application/json' -d '{"mode":"markup_changed"}'
curl -X PUT http://127.0.0.1:8001/control/modes/json_api -H 'content-type: application/json' -d '{"mode":"blocked","retry_after":5}'
curl -X POST http://127.0.0.1:8001/control/reset    # оба источника в normal
docker compose exec api python -m app.db.reset      # вернуть демо в состояние seed
docker compose restart worker                       # прогон, оставшийся в running, станет failed/interrupted
SCHEDULE_INTERVAL_SECONDS=60 docker compose up -d worker   # показать расписание (в compose по умолчанию выключено)
```

Воркер (`python -m app.worker`): при старте переводит прогоны в running в failed/`interrupted`, затем в цикле — проверка расписания, claim, execute, пауза `WORKER_POLL_SECONDS` (2). Расписание `SCHEDULE_INTERVAL_SECONDS` (в приложении 600, в compose 0 — выключено): прогон ставится, если нет активного и последний прогон любого источника создан не позже интервала назад; решение — по БД. SIGTERM/SIGINT останавливают воркер между итерациями.

Логи api и worker — structlog, одна JSON-строка на запись в stdout (`timestamp`, `level`, `logger`, `event`, `service` и контекст). Каждая строка воркера содержит `run_id` (`null` вне прогона); строки сбора источника — ещё `source`, строки расчёта товара — `product_id`. Трассировка исключения — поле `exception` той же строки. Например: `docker compose logs worker | grep '"run_id": 3'`.

Healthcheck воркера: воркер удаляет старый файл `WORKER_HEARTBEAT_FILE` при старте, создаёт его после восстановления и обновляет после каждой итерации, которая дошла до БД (при недоступной БД файл стареет, и через 120 с воркер становится unhealthy); healthcheck — `python -m app.worker.heartbeat /tmp/worker-heartbeat --max-age 120` (файл не старше 120 с). Поэтому `docker compose up --wait` ждёт, пока все 5 сервисов (postgres, fake-sites, api, worker, nginx) станут healthy.

Режимы: `normal`, `markup_changed`, `unavailable`, `blocked`, `price_spike`; источники `html_catalog`, `json_api`. Режимы живут в памяти fake-sites и сбрасываются при его перезапуске.

Прокси источников: `HTML_CATALOG_PROXY`, `JSON_API_PROXY` (compose передаёт их воркеру; пусто или не задано — без прокси).

Подтверждение карантина (расширение 1): `POST /api/calculations/{id}/confirm`, логика — `app/db/confirm.py`, коды отказа и подписи — там же (`CONFIRM_REFUSAL_LABELS`). Максимальный возраст — `QUARANTINE_CONFIRM_MAX_AGE_MINUTES` (по умолчанию 60, compose передаёт его api), по часам БД. Подтверждение и транзакция публикации прогона берут одну advisory-блокировку `app/db/publication.py` (`lock_publication`); любая новая запись в `published_prices` должна идти под ней. Правило — `docs/pricing.md`, «Подтверждение карантина».

Без контейнеров api и worker: `uv run --directory backend python -m app.api` (или `uvicorn app.api.main:app`), `uv run --directory fake_sites uvicorn fake_sites.app:app --port 8001` и `uv run --directory backend python -m app.worker.once` (поставить и выполнить один прогон без воркера). Новая миграция: `uv run --directory backend alembic revision --autogenerate -m "..."`, затем проверить и поправить файл (типы enum в `downgrade` удаляются вручную).

## Приёмка

Скрипт сценария показа (`plan.md`, «Сценарий показа — 5 минут», все 7 шагов, и шаг 8 — подтверждение карантина, расширение 1) через API, как экран, — только через nginx, БД не трогает. Нужен поднятый стек (`docker compose up --build -d --wait`), поэтому в «Команды проверки» не входит, но перед коммитом шага должен пройти:

```sh
uv run --directory backend python ../scripts/demo_scenario.py                      # по умолчанию http://127.0.0.1:8080
uv run --directory backend python ../scripts/demo_scenario.py --base-url http://127.0.0.1:8080
```

Печатает «ШАГ N … OK/FAIL» с ключевыми числами, в конце — «Сценарий пройден: 8/8 шагов OK»; при первом расхождении — FAIL и код 1, демо остаётся как есть. Перед шагом 2 сбрасывает демо (ждёт активный прогон), после шага 8 — снова сброс, поэтому повторяем. Шаг 8 подтверждает S (в выдаче 1490.00 с `calculation_id` этого расчёта), повторно — 409 `already_confirmed`, после нового прогона подтверждение старого расчёта Febi — 409 `superseded`. Шаг 7 запускает `tests/unit/pricing/test_control_cases.py` подпроцессом и проверяет строку `control cases: N matched, 0 mismatched`.

## Соглашения

- Деньги: только `Decimal` в Python, `NUMERIC(12,2)` в БД, строки в JSON. Никаких `float` в денежном коде и тестах.
- Округление и границы — строго по разделу «Правила расчёта» `plan.md`.
- Ошибка парсинга никогда не становится ценой 0 или «товара нет»: это ошибка структуры.
- Во время HTTP-запросов транзакция БД не открыта.
- Каждое требование шага покрыто тестом. Интеграционные тесты идут на настоящем PostgreSQL, не на SQLite.
- Код, идентификаторы и сообщения коммитов — на английском. Документация, UI и причины решений, которые видит менеджер, — на русском.
- Не добавляй то, что `plan.md` относит к «сознательно отложено» или к расширениям.

## Git

- Работа идёт в `main`, один шаг — один коммит. Без amend, без force push, без `--no-verify`.
- Сообщение коммита: `step N: <кратко>`, затем пустая строка и список изменений.
- Не коммить секреты, `.env`, артефакты сборки и `node_modules`.
- В сообщениях коммитов нет `Co-Authored-By` и иных строк атрибуции агентов.

## Ревью

Каждый шаг проходит независимое ревью в отдельном bb-треде Codex (`gpt-6-astra`) до коммита. Порядок и промпт — в `workflow.md`, раздел «Ревью». Ревьюер файлы не меняет.
