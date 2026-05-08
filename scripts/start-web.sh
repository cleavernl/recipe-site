#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/project-env.sh"

cd "$PROJECT_ROOT"

APP_MODULE="${DJANGO_WSGI_MODULE:-config.wsgi:application}"
BIND_HOST="${WEB_BIND_HOST:-0.0.0.0}"
BIND_PORT="${WEB_BIND_PORT:-8000}"
WORKERS="${WEB_CONCURRENCY:-2}"

mkdir -p "${DJANGO_DATA_DIR:-data}" "${DJANGO_MEDIA_ROOT:-media}" staticfiles

uv run python manage.py migrate --noinput
uv run python manage.py collectstatic --noinput

exec uv run gunicorn "$APP_MODULE" \
  --bind "${BIND_HOST}:${BIND_PORT}" \
  --workers "$WORKERS" \
  --access-logfile - \
  --error-logfile -
