#!/usr/bin/env bash
# Production deploy for a release tag: sync git, rebuild the web image, recreate the
# container, apply migrations, and wait until the app answers on localhost.
#
# Usage (from RECIPE_SITE_DEPLOY_PATH):
#   ./scripts/wsl-deploy-release.sh v0.2.6
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

TAG="${1:-}"
if [[ -z "$TAG" ]]; then
  echo "Usage: $0 <git-tag>" >&2
  exit 1
fi

if command -v podman-compose >/dev/null 2>&1; then
  COMPOSE=(podman-compose)
else
  COMPOSE=(podman compose)
fi

echo "Checking out ${TAG}..."
git fetch --tags origin
git checkout -f "$TAG"
git reset --hard "$TAG"
echo "Deploy tree at $(git rev-parse --short HEAD) (${TAG})"

echo "Building web image (no cache)..."
"${COMPOSE[@]}" build --no-cache web

echo "Recreating web container..."
"${COMPOSE[@]}" up -d --force-recreate --no-build web

"${SCRIPT_DIR}/wsl-deploy-migrate.sh"
"${SCRIPT_DIR}/wsl-wait-for-web.sh" 8000 90 2

echo "Deploy finished for ${TAG}."
