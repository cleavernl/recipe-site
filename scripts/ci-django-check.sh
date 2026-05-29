#!/usr/bin/env bash
# CI / deploy gate: verbose Django system check for agent-friendly logs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=== Django system check context ==="
echo "time_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
if git rev-parse --is-inside-work-tree &>/dev/null; then
  echo "git_ref: ${GITHUB_REF_NAME:-$(git symbolic-ref -q --short HEAD 2>/dev/null || git describe --tags --always)}"
  echo "git_sha: ${GITHUB_SHA:-$(git rev-parse HEAD)}"
fi
echo "DJANGO_SETTINGS_MODULE: ${DJANGO_SETTINGS_MODULE:-config.settings}"
uv run python --version
echo "==================================="

set +e
uv run python manage.py check --verbosity 2 --traceback
status=$?
set -e

if [[ "$status" -ne 0 ]]; then
  echo ""
  echo "=== Django check failure hints (paste this log into chat) ==="
  echo "Reproduce: source scripts/project-env.sh && uv run python manage.py check --verbosity 2 --traceback"
  echo "============================================================="
  exit "$status"
fi
