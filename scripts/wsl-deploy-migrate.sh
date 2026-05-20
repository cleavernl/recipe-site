#!/usr/bin/env bash
# Apply Django migrations in the production compose web service after deploy.
# Usage: run from the project root (same tree as compose.yaml).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

if command -v podman-compose >/dev/null 2>&1; then
  COMPOSE=(podman-compose)
else
  COMPOSE=(podman compose)
fi

echo "Applying database migrations in the web service..."
"${COMPOSE[@]}" exec -T web uv run python manage.py migrate --noinput
echo "Migrations applied."
