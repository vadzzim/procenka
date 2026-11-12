#!/bin/sh
# api: apply migrations, load missing demo products, serve HTTP.
set -eu
alembic upgrade head
python -m app.db.seed
exec python -m app.api
