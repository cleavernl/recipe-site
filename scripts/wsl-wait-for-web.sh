#!/usr/bin/env bash
# Wait until the compose web service accepts HTTP on localhost (WSL view).
# Usage: ./scripts/wsl-wait-for-web.sh [PORT] [MAX_ATTEMPTS] [SLEEP_SECONDS]
set -euo pipefail

PORT="${1:-8000}"
MAX_ATTEMPTS="${2:-90}"
SLEEP_SEC="${3:-2}"
BASE_URL="http://127.0.0.1:${PORT}"

http_ok() {
  local code
  code="$(curl -gSs --connect-timeout 2 --max-time 8 -o /dev/null -w '%{http_code}' "${BASE_URL}/accounts/login/" 2>/dev/null || echo 000)"
  [[ "$code" =~ ^(200|30[123])$ ]]
}

for i in $(seq 1 "$MAX_ATTEMPTS"); do
  if http_ok; then
    echo "Web responded on ${BASE_URL} (attempt ${i}/${MAX_ATTEMPTS}, HTTP login page)."
    exit 0
  fi
  echo "Waiting for web on :${PORT} (${i}/${MAX_ATTEMPTS})..."
  sleep "$SLEEP_SEC"
done

echo "Timed out after $((MAX_ATTEMPTS * SLEEP_SEC))s waiting for ${BASE_URL}/accounts/login/"
exit 1
