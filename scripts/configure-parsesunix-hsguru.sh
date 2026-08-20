#!/usr/bin/env bash
# Configure the bounded ParsesUnix rollout for the three HSGuru matchup feeds.
set -Eeuo pipefail

MODE="${1:-shadow}"
ENV_FILE="${2:-/srv/hs-data-api/.env.docker}"
SOURCE_IDS="hsguru_matchups_legend,hsguru_matchups_wild_legend,hsguru_matchups_diamond_4to1"

die() {
  echo "configure-parsesunix-hsguru: $*" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || die "run as root"
[[ "${MODE}" =~ ^(shadow|active|disable)$ ]] \
  || die "mode must be shadow, active, or disable"
[[ "${ENV_FILE}" = /* ]] || die "environment file path must be absolute"
[[ "${ENV_FILE}" != "/" && "${ENV_FILE}" != "/srv" ]] \
  || die "environment file path is too broad"
[[ -f "${ENV_FILE}" && ! -L "${ENV_FILE}" ]] \
  || die "environment file must be an existing regular file"

upsert() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
  else
    printf '%s=%s\n' "${key}" "${value}" >>"${ENV_FILE}"
  fi
}

upsert HS_PARSESUNIX_MAX_CONCURRENCY 2
upsert HS_PARSESUNIX_TIMEOUT_SECONDS 150
upsert HS_PARSESUNIX_MAX_BODY_BYTES 8388608
upsert HS_PARSESUNIX_SCRAPE_DO_STRATEGIES normal

case "${MODE}" in
  shadow)
    upsert HS_PARSESUNIX_ENABLED true
    upsert HS_PARSESUNIX_SHADOW_SOURCE_IDS "${SOURCE_IDS}"
    upsert HS_PARSESUNIX_ACTIVE_SOURCE_IDS ""
    upsert HS_PARSESUNIX_ALLOWED_PROVIDERS ""
    upsert HS_PARSESUNIX_SCRAPE_DO_DAILY_CREDIT_LIMIT 0
    upsert HS_PARSESUNIX_SCRAPE_DO_MAX_REQUESTS_PER_REFRESH 0
    ;;
  active)
    upsert HS_PARSESUNIX_ENABLED true
    upsert HS_PARSESUNIX_SHADOW_SOURCE_IDS ""
    upsert HS_PARSESUNIX_ACTIVE_SOURCE_IDS "${SOURCE_IDS}"
    upsert HS_PARSESUNIX_ALLOWED_PROVIDERS scrape.do
    upsert HS_PARSESUNIX_SCRAPE_DO_DAILY_CREDIT_LIMIT 10
    upsert HS_PARSESUNIX_SCRAPE_DO_MAX_REQUESTS_PER_REFRESH 3
    ;;
  disable)
    upsert HS_PARSESUNIX_ENABLED false
    upsert HS_PARSESUNIX_SHADOW_SOURCE_IDS ""
    upsert HS_PARSESUNIX_ACTIVE_SOURCE_IDS ""
    upsert HS_PARSESUNIX_ALLOWED_PROVIDERS ""
    upsert HS_PARSESUNIX_SCRAPE_DO_DAILY_CREDIT_LIMIT 0
    upsert HS_PARSESUNIX_SCRAPE_DO_MAX_REQUESTS_PER_REFRESH 0
    ;;
esac

chmod 0600 "${ENV_FILE}"
echo "Configured bounded ParsesUnix HSGuru rollout: ${MODE}."
