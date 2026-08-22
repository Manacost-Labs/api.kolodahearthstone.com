#!/usr/bin/env bash
# Safely configure the bounded convergence worker without printing its secret.
set -Eeuo pipefail

MODE="${1:-active}"
ENV_FILE="${2:-/srv/hs-data-api/.env.docker}"

die() {
  echo "configure-convergence-worker: $*" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || die "run as root"
[[ "${MODE}" =~ ^(active|disable)$ ]] || die "mode must be active or disable"
[[ "${ENV_FILE}" = /* ]] || die "environment file path must be absolute"
[[ "${ENV_FILE}" != "/" && "${ENV_FILE}" != "/srv" ]] \
  || die "environment file path is too broad"
[[ -f "${ENV_FILE}" && ! -L "${ENV_FILE}" ]] \
  || die "environment file must be an existing regular file"

read_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "${ENV_FILE}" | tail -n 1
}

upsert() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
  else
    printf '%s=%s\n' "${key}" "${value}" >>"${ENV_FILE}"
  fi
}

if [[ "${MODE}" == "active" ]]; then
  command -v openssl >/dev/null 2>&1 || die "openssl is required"
  api_key_value="$(read_value HS_API_KEY)"
  orchestrator_key_value="$(read_value HS_ORCHESTRATOR_API_KEY)"
  if [[ "${#orchestrator_key_value}" -lt 32 \
    || "${orchestrator_key_value}" == "${api_key_value}" ]]; then
    orchestrator_key_value="$(openssl rand -hex 32)"
  fi
  [[ "${#orchestrator_key_value}" -ge 32 ]] \
    || die "could not create a safe orchestrator key"
  upsert HS_ORCHESTRATOR_API_KEY "${orchestrator_key_value}"
  upsert HS_CONVERGENCE_API_BASE_URL "http://api:8000"
  upsert HS_CONVERGENCE_WORKER_MODE "active"
else
  upsert HS_CONVERGENCE_WORKER_MODE "off"
fi

chmod 0600 "${ENV_FILE}"
echo "Configured bounded convergence worker: ${MODE}."
