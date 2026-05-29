#!/usr/bin/env bash
# CI / deploy gate: verbose pytest output for pasting into agent chat on failure.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

print_context() {
  echo "=== CI test context ==="
  echo "time_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if git rev-parse --is-inside-work-tree &>/dev/null; then
    echo "git_ref: ${GITHUB_REF_NAME:-$(git symbolic-ref -q --short HEAD 2>/dev/null || git describe --tags --always)}"
    echo "git_sha: ${GITHUB_SHA:-$(git rev-parse HEAD)}"
  fi
  if [[ -n "${GITHUB_RUN_ID:-}" && -n "${GITHUB_SERVER_URL:-}" && -n "${GITHUB_REPOSITORY:-}" ]]; then
    echo "actions_run: ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
  fi
  echo "runner_os: ${RUNNER_OS:-$(uname -s)}"
  uv run python --version
  uv run python -c "import django; print('django:', django.get_version())"
  uv run pytest --version
  echo 'pytest_flags: -vv --tb=long --color=no -ra --junitxml=pytest-results.xml'
  echo "========================"
}

print_failure_hints() {
  echo ""
  echo "=== CI test failure hints (paste this log into chat) ==="
  echo "1. Search for FAILED / ERROR lines and the traceback block above each one."
  echo "2. Read the innermost exception message first, then work outward."
  echo "3. Reproduce locally:"
  echo "     source scripts/project-env.sh"
  echo "     uv run pytest -vv --tb=long --color=no <file>::<TestClass>::<test_name>"
  echo "     Or: bash scripts/ci-test.sh"
  if [[ -f pytest-results.xml ]]; then
    echo "4. JUnit XML: pytest-results.xml (workflow artifact on failure)."
  fi
  echo "5. Tag deploy: CI deletes the pushed v* tag from origin; git fetch --tags --prune, then git tag -d <tag> if local only."
  echo "======================================================="
}

print_context

set +e
uv run pytest \
  -vv \
  --tb=long \
  --color=no \
  -ra \
  --junitxml=pytest-results.xml \
  "$@"
status=$?
set -e

if [[ "$status" -ne 0 ]]; then
  print_failure_hints
  exit "$status"
fi
