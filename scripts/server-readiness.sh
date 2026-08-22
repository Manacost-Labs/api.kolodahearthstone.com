#!/usr/bin/env bash
# Verify that a migrated/new HS Data API server is ready for production traffic.
set -euo pipefail

STRICT=0
RUN_REFRESH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict) STRICT=1; shift ;;
    --refresh-all) RUN_REFRESH=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="${VENV_PYTHON:-$ROOT/venv/bin/python}"
ENV_FILE="${HS_ENV_FILE:-/etc/hs-data-api.env}"
HEALTH_JSON="$(mktemp)"
OPS_JSON="$(mktemp)"

cleanup() {
  rm -f "$HEALTH_JSON" "$OPS_JSON"
}
trap cleanup EXIT

failures=0

check() {
  local name="$1"
  shift
  echo "=== $name ==="
  if "$@"; then
    echo "OK: $name"
  else
    local code=$?
    echo "FAIL: $name (exit=$code)" >&2
    failures=$((failures + 1))
  fi
  echo
}

warn() {
  echo "WARN: $*" >&2
}

require_file() {
  [[ -f "$1" ]]
}

require_executable() {
  [[ -x "$1" ]]
}

env_configured() {
  require_file "$ENV_FILE" || return 1
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  [[ "${HS_API_KEY:-}" != "" && "${HS_API_KEY:-}" != "change-me" ]] || return 1
  [[ "${HS_API_DATA_DIR:-}" != "" ]] || return 1
  if [[ "${HS_FETCH_REQUIRE_PROXY:-false}" == "true" ]]; then
    [[ "${HS_FETCH_PROXY_URL:-}" != "" && "${HS_FETCH_PROXY_URL:-}" != *"USER:PASS"* ]] || return 1
  fi
  if [[ "${HS_CONVERGENCE_WORKER_MODE:-off}" == "active" ]]; then
    local orchestrator_key="${HS_ORCHESTRATOR_API_KEY:-}"
    [[ "${#orchestrator_key}" -ge 32 ]] || return 1
    [[ "${orchestrator_key}" != "${HS_API_KEY}" ]] || return 1
    case "${HS_CONVERGENCE_API_BASE_URL:-}" in
      http://api:* | http://localhost:* | http://127.0.0.1:* | https://*) ;;
      *) return 1 ;;
    esac
  fi
}

python_imports() {
  "$VENV" - <<'PY'
from app.cli import load_env_file
load_env_file()
from app.sources import SOURCES
from app.source_tiers import validate_tier_registry
validate_tier_registry()
print(f"sources={len(SOURCES)}")
PY
}

systemd_units() {
  systemctl daemon-reload
  systemctl is-enabled hs-data-api.service >/dev/null
  systemctl is-enabled hs-data-api-refresh.timer >/dev/null
  systemctl is-enabled hs-data-api-refresh-api.timer >/dev/null
  systemctl is-enabled hs-data-api-freshness-check.timer >/dev/null
  if systemctl is-enabled hs-data-api-refresh-protected.timer >/dev/null 2>&1; then
    echo "hs-data-api-refresh-protected.timer must stay disabled" >&2
    return 1
  fi
  systemctl list-timers --all 'hs-data-api*' --no-pager
}

api_health() {
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  curl -fsS "http://127.0.0.1:${HS_API_PORT:-8000}/health" >"$HEALTH_JSON"
  export HEALTH_JSON
  "$VENV" - <<'PY'
import json
import os
payload = json.loads(open(os.environ["HEALTH_JSON"], encoding="utf-8").read())
assert payload.get("ok") is True, payload
print(payload)
PY
}

ops_health() {
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  curl -fsS -H "X-API-Key: ${HS_API_KEY}" "http://127.0.0.1:${HS_API_PORT:-8000}/ops/summary?since_hours=48" >"$OPS_JSON"
  export OPS_JSON
  "$VENV" - <<'PY'
import json
import os
payload = json.loads(open(os.environ["OPS_JSON"], encoding="utf-8").read())
freshness = payload.get("freshness") or {}
assert freshness.get("ok") is True, freshness
assert freshness.get("stale_count") == 0, freshness
assert freshness.get("cached_after_failure_count") == 0, freshness
print(json.dumps({"freshness": freshness, "states": payload.get("sources_by_state")}, indent=2))
PY
}

check "repo and venv" require_executable "$VENV"
check "env configured" env_configured
check "python imports and tier registry" python_imports
check "systemd units and timers" systemd_units

if command -v docker >/dev/null 2>&1; then
  check "flaresolverr service" systemctl is-active --quiet hs-flaresolverr.service
else
  warn "docker is not installed; FlareSolverr service may be unavailable"
  failures=$((failures + 1))
fi

check "proxy-check" "$VENV" -m app.cli proxy-check
if [[ "$STRICT" -eq 1 ]]; then
  check "preflight strict" "$VENV" -m app.cli preflight --strict
else
  check "preflight" "$VENV" -m app.cli preflight
fi

if [[ "$RUN_REFRESH" -eq 1 ]]; then
  check "refresh all sources" "$VENV" -m app.cli refresh --all
fi

check "API /health" api_health
check "ops freshness summary" ops_health
check "freshness-check" "$VENV" -m app.cli freshness-check --since-hours 48
check "quality-check" "$VENV" -m app.cli quality-check

if [[ "$failures" -ne 0 ]]; then
  echo "Server readiness: FAILED ($failures checks failed)" >&2
  exit 1
fi

echo "Server readiness: OK"
