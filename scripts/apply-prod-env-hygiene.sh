#!/usr/bin/env bash
# Update /etc/hs-data-api.env keys from plan (never prints secrets).
set -euo pipefail
ENV_FILE="${1:-/etc/hs-data-api.env}"
EXAMPLE="$(dirname "$0")/../.env.example"

upsert() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    echo "${key}=${value}" >>"$ENV_FILE"
  fi
}

# Remove duplicate HS_CLOAKBROWSER_HUMANIZE lines (keep last true)
if grep -q '^HS_CLOAKBROWSER_HUMANIZE=' "$ENV_FILE" 2>/dev/null; then
  grep -v '^HS_CLOAKBROWSER_HUMANIZE=' "$ENV_FILE" >"${ENV_FILE}.tmp" || true
  echo 'HS_CLOAKBROWSER_HUMANIZE=true' >>"${ENV_FILE}.tmp"
  mv "${ENV_FILE}.tmp" "$ENV_FILE"
fi

upsert HS_FETCH_BACKENDS "flaresolverr,patchright,scrapling,curl_cffi,cloudscraper"
upsert HS_HSGURU_FETCH_BACKENDS "flaresolverr,patchright,scrapling,curl_cffi,cloudscraper"
upsert HS_FETCH_BACKENDS_LAB "cloakbrowser,flaresolverr,scrapling,patchright,curl_cffi,cloudscraper"
upsert HS_HSREPLAY_JSON_CHANNELS "flaresolverr,scrape_do,curl_cffi"
upsert HS_HSREPLAY_MARKDOWN_CHANNELS "flaresolverr,curl_cffi"
upsert HS_REFRESH_PREFLIGHT_STRICT "true"

# Merge any missing keys from .env.example without overwriting values
if [[ -x "$(dirname "$0")/merge-env-example.sh" ]]; then
  "$(dirname "$0")/merge-env-example.sh" "$ENV_FILE"
fi

echo "Updated $ENV_FILE (backends, channels, preflight strict, cloak dedup)"
