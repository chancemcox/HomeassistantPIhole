#!/usr/bin/env bash
set -euo pipefail

URL="${1:-http://127.0.0.1:8123}"
TIMEOUT_SECONDS="${2:-180}"
deadline=$((SECONDS + TIMEOUT_SECONDS))

echo "Waiting for Home Assistant at ${URL} (timeout=${TIMEOUT_SECONDS}s)"

while (( SECONDS < deadline )); do
  # Fresh installs often return 401 from /api until auth is configured.
  status="$(curl -sS -o /tmp/ha-wait.json -w '%{http_code}' "${URL}/api/" || true)"
  if [[ "${status}" == "200" || "${status}" == "401" ]]; then
    echo "Home Assistant API is up (HTTP ${status}):"
    cat /tmp/ha-wait.json || true
    echo
    exit 0
  fi

  # During onboarding HA may respond on / before /api is ready.
  if curl -fsS -o /dev/null "${URL}/" 2>/dev/null; then
    echo "Home Assistant HTTP is responding; waiting for /api (last=${status})..."
  else
    echo "Still waiting... (last=/api status=${status})"
  fi
  sleep 5
done

echo "Timed out waiting for Home Assistant" >&2
docker logs ha-ci 2>/dev/null || true
exit 1
