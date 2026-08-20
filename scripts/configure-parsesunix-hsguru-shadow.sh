#!/usr/bin/env bash
# Enable a zero-cost ParsesUnix shadow canary for the three HSGuru matchup feeds.
set -Eeuo pipefail

ENV_FILE="${1:-/srv/hs-data-api/.env.docker}"

die() {
  echo "configure-parsesunix-hsguru-shadow: $*" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || die "run as root"
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

upsert HS_PARSESUNIX_ENABLED true
upsert HS_PARSESUNIX_SHADOW_SOURCE_IDS \
  hsguru_matchups_legend,hsguru_matchups_wild_legend,hsguru_matchups_diamond_4to1
upsert HS_PARSESUNIX_ACTIVE_SOURCE_IDS ""
upsert HS_PARSESUNIX_ALLOWED_PROVIDERS ""
upsert HS_PARSESUNIX_SCRAPE_DO_DAILY_CREDIT_LIMIT 0
upsert HS_PARSESUNIX_SCRAPE_DO_MAX_REQUESTS_PER_REFRESH 0
upsert HS_PARSESUNIX_SCRAPE_DO_STRATEGIES normal

chmod 0600 "${ENV_FILE}"
echo "Configured zero-cost ParsesUnix shadow canary for HSGuru matchups."
