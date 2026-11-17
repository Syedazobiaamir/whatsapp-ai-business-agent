#!/bin/sh
# Runs before the API starts: bring the database schema up to date, then (optionally) seed.
set -e

echo "[entrypoint] applying database migrations..."
alembic upgrade head

if [ "${SEED_ON_START:-true}" = "true" ]; then
  echo "[entrypoint] seeding missing sample data (never overwrites edits)..."
  python -m app.seed
fi

echo "[entrypoint] starting: $*"
exec "$@"
