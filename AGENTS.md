# AGENTS.md

Инструкции для всех агентов, работающих в этом репозитории.

## Документы

- `plan.md` — спецификация демо. Источник истины по требованиям. Не меняй его; отступления записывай в `docs/progress.md`.
- `workflow.md` — процесс: шаги, цикл исполнителя, ревью, формат отчёта.
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
uv run --directory backend ruff check
uv run --directory backend ruff format --check
uv run --directory backend mypy
uv run --directory backend pytest
uv run --directory fake_sites ruff check
uv run --directory fake_sites ruff format --check
uv run --directory fake_sites mypy
uv run --directory fake_sites pytest
docker compose config --quiet
```

- `fake_sites` подключён к backend как dev-зависимость по пути (editable): тесты backend ходят в настоящее приложение fake-sites через `httpx.ASGITransport`, без сети и без запущенного контейнера.

- `pytest` гоняет и unit-, и интеграционные тесты (маркер `integration`). Если PostgreSQL недоступен, интеграционные тесты падают, а не пропускаются.
- Интеграционные тесты берут URL из `TEST_DATABASE_URL`, по умолчанию `postgresql+asyncpg://procenka:procenka@localhost:54329/procenka_test`, и сами создают базу `procenka_test`, если её нет. Порт postgres на хосте — `POSTGRES_HOST_PORT` (по умолчанию 54329); пример переменных — `.env.example`.
- `.env` из корня читает только `docker compose`, pytest его не загружает. Если порт или учётные данные postgres изменены в `.env`, экспортируй `TEST_DATABASE_URL` в оболочке или запускай `uv run --directory backend --env-file ../.env pytest` (путь `--env-file` считается от `backend/`).
- Быстрый прогон без БД: `uv run --directory backend pytest -m "not integration"`. Готовность шага по нему не проверяется.
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
docker compose up --build -d                       # postgres, fake-sites, api (миграции и seed при старте), worker
curl -X POST http://127.0.0.1:8000/api/runs         # поставить прогон; повторный запрос вернёт тот же id
curl http://127.0.0.1:8000/api/runs/<id>            # статус прогона и источников
curl http://127.0.0.1:8000/api/published-prices     # опубликованные цены
docker compose exec api python -m app.db.reset      # вернуть демо в состояние seed
```

Без контейнеров api и worker: `uv run --directory backend uvicorn app.api.main:app`, `uv run --directory fake_sites uvicorn fake_sites.app:app --port 8001` и `uv run --directory backend python -m app.worker.once` (поставить и выполнить один прогон без воркера). Новая миграция: `uv run --directory backend alembic revision --autogenerate -m "..."`, затем проверить и поправить файл (типы enum в `downgrade` удаляются вручную).

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
