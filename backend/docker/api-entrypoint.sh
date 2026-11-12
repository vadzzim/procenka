#!/bin/sh
# api: apply migrations, load missing demo products, serve HTTP.
set -eu
alembic upgrade head
python -m app.db.seed
exec uvicorn app.api.main:app --host 0.0.0.0 --port 8000
