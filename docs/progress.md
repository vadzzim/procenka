# Журнал реализации

Каждый шаг из `workflow.md` дописывает свой раздел: что сделано, решения, отступления от `plan.md`, долги.

## Шаг 1. Каркас

### Что сделано

- `.gitignore` (сохранён `.temp`; добавлены `.env`, venv, кэши Python/mypy/ruff/pytest, артефакты сборки, `node_modules`) и `.gitattributes` (`* text=auto eol=lf` плюс явный LF для скриптов, Dockerfile, py, yaml, toml, md и т. п.).
- Python-проект в `backend/`: `pyproject.toml`, `uv.lock`, `.python-version` (3.13). Пакет `backend/app/` с пустыми подпакетами `api`, `pricing`, `parsers`, `worker`, `db`.
- Зависимости: `sqlalchemy[asyncio]` 2, `asyncpg`; dev — `ruff`, `mypy`, `pytest`, `pytest-asyncio`.
- ruff (lint + format), mypy `strict` для `app` и `tests`, pytest с `--strict-markers`, `asyncio_mode = auto`, маркер `integration`.
- Docker Compose: `deploy/compose.yaml` с сервисом `postgres` (`postgres:16`, healthcheck через `pg_isready`, именованный том `postgres-data`, порт `127.0.0.1:${POSTGRES_HOST_PORT:-54329}`); корневой `compose.yaml` подключает его через `include`, имя проекта `procenka`.
- `.env.example` с переменными postgres и `TEST_DATABASE_URL`.
- Тесты: unit-заглушка `tests/unit/test_smoke.py`; интеграционный `tests/integration/test_postgres.py` — через SQLAlchemy async + asyncpg выполняет `SELECT version()` и `SHOW server_version_num` и проверяет, что сервер — PostgreSQL 16 и подключение идёт к тестовой базе.
- Фикстуры в `tests/conftest.py`: база `procenka_test` создаётся при отсутствии (через служебную базу `postgres` в AUTOCOMMIT), таймаут подключения 5 с.
- Раздел «Команды проверки» в `AGENTS.md` заполнен; в раздел «Git» добавлено правило без строк атрибуции агентов.

### Решения

- uv-проект не в корне, а в `backend/`; команды из корня вызываются как `uv run --directory backend ...`, чтобы конфигурация ruff/mypy/pytest бралась из `backend/pyproject.toml`.
- `[tool.uv] package = false`: бэкенд — приложение, а не библиотека; `app` импортируется из рабочей директории `backend/`.
- Интеграционные тесты не пропускаются при недоступной БД: ошибка подключения в фикстуре даёт ERROR и ненулевой код выхода pytest. Проверено остановкой контейнера (`docker compose stop postgres` → `1 passed, 1 error`, exit code 1).
- Цикл событий pytest-asyncio — один на сессию (`asyncio_default_fixture_loop_scope` и `asyncio_default_test_loop_scope` = `session`), чтобы сессионные async-фикстуры и тесты работали в одном цикле.
- `.env` читает только `docker compose`; pytest берёт `TEST_DATABASE_URL` из окружения процесса. Для переопределения — экспорт переменной или `uv run --directory backend --env-file ../.env pytest` (описано в `AGENTS.md` и `.env.example`).
- Учётные данные postgres по умолчанию (`procenka/procenka`) — только для локального демо; порт опубликован только на `127.0.0.1`.

### Отступления от plan.md

- Нет.

### Долги

- FastAPI, httpx, Alembic и прочие зависимости из «Стека» добавят шаги, которым они нужны.
- Сервисы api, worker, fake-sites, nginx и Dockerfile для них — в следующих шагах.

## Шаг 2. Ядро расчёта и нормализация

### Что сделано

- `backend/app/pricing/` — чистые функции без БД, сети, времени и окружения:
  - `money.py` — `money()` (Decimal/int/str → Decimal без округления), `require_decimal()` и округление `ceil_cents` / `floor_cents` / `half_up_cents` / `half_up_tenth`. `float`, `bool` и прочие типы → `TypeError`, NaN и бесконечности → `ValueError`;
  - `normalize.py` — `normalize_article()`, `normalize_brand()` и таблица синонимов демо-набора `BRAND_SYNONYMS`;
  - `models.py` — входные dataclass-ы `Product`, `ProductSettings`, `GlobalSettings`, `Offer`; результат `PricingResult`; enum-ы `OfferRejection`, `ResultState`, `ResultReason`, `Constraint` с английскими кодами и одна таблица русских подписей `LABELS`; константа `RULES_VERSION`;
  - `calculate.py` — `calculate_price(product, settings, offers, *, global_settings, partial_data)`.
- `backend/tests/data/control_cases.csv` — 25 вручную рассчитанных примеров, формат описан в `backend/tests/data/README.md`. Параметризованный тест `tests/unit/pricing/test_control_cases.py`; `tests/conftest.py` печатает `control cases: N matched, M mismatched`, расхождение показывает входные данные строки, ожидаемое, фактическое и промежуточные числа.
- Unit-тесты: `test_money.py` (округление у границ, отказ от float и нефинитных значений во всех входных dataclass-ах и функциях округления), `test_normalize.py` (примеры из `plan.md`, синонимы, неизвестный бренд), `test_calculate.py` (каждое правило и граница, порядок причин отбраковки, решения, флаг частичных данных, независимость от `decimal.getcontext()`).

### Решения

- Наценка и порог карантина задаются в процентах (`20` = 20 %), коэффициент подозрительно низкой цены — долей (`0.5`). По умолчанию: порог 20 %, коэффициент 0,5, максимальный срок поставки 14 дней.
- Входные деньги не округляются и не обязаны иметь две цифры после запятой: округление — правило расчёта и применяется только по п. 7 (граница вверх, потолок вниз, целевая half-up). Поэтому цена конкурента вида `1250.005` даёт целевую `1240.01`.
- Порядок отбраковки предложения (первая сработавшая причина): другой артикул → другой бренд → цена ≤ 0 → нет в наличии (остаток ≤ 0) → срок поставки больше максимума → цена строго ниже закупка × коэффициент. Сравнение с коэффициентом идёт по точному произведению без округления.
- Приоритет причин «оставлена предыдущая»: некорректная закупка (≤ 0) → конфликт правил (потолок после округления строго меньше нижней границы после округления) → нет пригодных предложений. Промежуточные числа (граница, потолок, целевая) считаются и сохраняются и в этих случаях — для объяснения.
- Ограничения применяются к уже округлённой целевой цене: сначала нижняя граница, затем потолок; в `applied_constraints` попадает сработавшее.
- Решение о карантине: `abs(new − old) × 100 > порог × old` — то же, что `abs(new − old) / old × 100 > порог`, но без деления, то есть точно. Изменение ровно на пороге — не карантин. `change_percent` хранится точным (со знаком), `change_percent_display` — округлённым до 0,1 по ROUND_HALF_UP (−25,1 % для 1 990 → 1 490).
- Расчёт выполняется в собственном `decimal.Context` (28 знаков, ловушки на InvalidOperation/DivisionByZero/Overflow), результат не зависит от контекста вызывающего кода.
- Неверные входные данные конфигурации — `ValueError`/`TypeError` при создании dataclass-а: опубликованная цена ≤ 0, отрицательные наценка/шаг/порог/коэффициент, отрицательные остаток или срок, пустые после нормализации бренд или артикул товара. Нулевая или отрицательная закупка — не исключение, а результат «оставлена предыдущая».
- Нормализация: артикул — только буквы и цифры (`str.isalnum`) в верхнем регистре; бренд — trim, схлопывание пробелов, верхний регистр, затем таблица синонимов (Bosch, MANN-FILTER, Mercedes-Benz, MAHLE, Febi, Hengst, Valeo, Toyota, Volkswagen). Неизвестный бренд — очищенная форма в верхнем регистре.
- `RULES_VERSION = "2026-11-12.1"`; меняется при любом изменении правил.

### Отступления от plan.md

- Нет.

### Долги

- Цена конкурента может иметь больше двух знаков после запятой (сохраняется как пришла). Шагу 3 нужно хранить цену предложения с достаточной точностью (например, NUMERIC без жёсткого масштаба 2) или явно решить, что парсер отвергает такие цены как ошибку структуры.
- Текстовая формула на русском для UI — шаг 7; `docs/pricing.md` — шаг 10.
