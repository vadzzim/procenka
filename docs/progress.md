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
