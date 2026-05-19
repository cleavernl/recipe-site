#!/usr/bin/env bash
# Import OneNote migration YAML into the local Django database.
#
# Usage:
#   ./scripts/import-onenote-recipes.sh              # migrate, dry-run, then prompt to import
#   ./scripts/import-onenote-recipes.sh --yes        # migrate, dry-run, import without prompting
#   ./scripts/import-onenote-recipes.sh --dry-run-only
#
# Options:
#   --yes, -y           Run the real import after dry-run (no confirmation prompt)
#   --dry-run-only      Migrate and dry-run only; do not import
#   --owner USERNAME    Recipe owner (default: cleavernl)
#   --dir PATH          Directory of *.recipe.yaml files (default: migration/onenote/recipes)
#   -h, --help          Show this help

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=project-env.sh
source "$SCRIPT_DIR/project-env.sh"

cd "$PROJECT_ROOT"

OWNER="${RECIPE_IMPORT_OWNER:-cleavernl}"
IMPORT_DIR="${PROJECT_ROOT}/migration/onenote/recipes"
DRY_RUN_ONLY=0
SKIP_CONFIRM=0

usage() {
  sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes | -y)
      SKIP_CONFIRM=1
      shift
      ;;
    --dry-run-only)
      DRY_RUN_ONLY=1
      shift
      ;;
    --owner)
      OWNER="${2:?--owner requires a username}"
      shift 2
      ;;
    --dir)
      IMPORT_DIR="${2:?--dir requires a path}"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

IMPORT_DIR="$(cd "$IMPORT_DIR" && pwd)"

echo "==> Project: $PROJECT_ROOT"
echo "==> Owner:   $OWNER"
echo "==> Recipes: $IMPORT_DIR"
echo

echo "==> Applying migrations"
uv run python manage.py migrate --noinput
echo

echo "==> Checking owner account exists"
if ! uv run python manage.py shell -c "
from django.contrib.auth import get_user_model
import sys
if not get_user_model().objects.filter(username='${OWNER}').exists():
    print('User ${OWNER} does not exist. Create one with:', file=sys.stderr)
    print('  uv run python manage.py createsuperuser --username ${OWNER}', file=sys.stderr)
    sys.exit(1)
"; then
  exit 1
fi
echo

echo "==> Dry run (no database writes)"
if ! uv run python manage.py import_recipes \
  --owner "$OWNER" \
  --dir "$IMPORT_DIR" \
  --dry-run; then
  echo "Dry run failed. Fix errors above and try again." >&2
  exit 1
fi
echo

if [[ "$DRY_RUN_ONLY" -eq 1 ]]; then
  echo "Dry run only (--dry-run-only). No recipes were imported."
  exit 0
fi

if [[ "$SKIP_CONFIRM" -ne 1 ]]; then
  read -r -p "Import recipes into the local database? [y/N] " reply
  case "${reply,,}" in
    y | yes) ;;
    *)
      echo "Cancelled. No recipes were imported."
      exit 0
      ;;
  esac
fi

echo "==> Importing recipes"
uv run python manage.py import_recipes \
  --owner "$OWNER" \
  --dir "$IMPORT_DIR"

echo
echo "Done. Log in as ${OWNER} and review recipes in the browser."
