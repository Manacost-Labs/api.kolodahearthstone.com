#!/usr/bin/env bash
set -euo pipefail

platform_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_dir="${platform_dir}/postgres"
admin_root="/srv/api-kolodahearthstone/panel/current"
nginx_config="/etc/nginx/vhosts/koloda/api.kolodahearthstone.com.conf"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"

log_event() {
  local level="$1"
  local event="$2"
  local detail="${3:-}"
  printf '{"timestamp":"%s","level":"%s","event":"%s","run_id":"%s","detail":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$event" "$run_id" "$detail"
}
on_error() {
  local exit_code="$?"
  log_event error platform_health_failed "exit_${exit_code}"
  exit "${exit_code}"
}
trap on_error ERR

for php_file in \
  "${admin_root}/index.php" \
  "${admin_root}/analytics.php" \
  "${admin_root}/lib/analytics.php" \
  "${admin_root}/api/index.php"
do
  sudo -n -u koloda php -l "${php_file}" >/dev/null
done

grep -Fq 'root /srv/api-kolodahearthstone/panel/current;' "${nginx_config}"
if grep -Fq '127.0.0.1:18084' "${nginx_config}"; then
  log_event error platform_health_failed nocodb_proxy_still_configured
  exit 1
fi

read -r public_status public_redirect <<<"$(curl --silent --show-error --max-time 10 \
  --output /dev/null --write-out '%{http_code} %{redirect_url}' \
  https://api.kolodahearthstone.com/)"
test "${public_status}" = '302'
test "${public_redirect}" = 'https://api.kolodahearthstone.com/auth/github'

read -r github_status github_redirect <<<"$(curl --silent --show-error --max-time 10 \
  --output /dev/null --write-out '%{http_code} %{redirect_url}' \
  https://api.kolodahearthstone.com/auth/github)"
test "${github_status}" = '302'
[[ "${github_redirect}" == 'https://github.com/login/oauth/authorize?'* ]]

legacy_api_status="$(curl --silent --show-error --max-time 10 \
  --output /dev/null --write-out '%{http_code}' \
  https://api.kolodahearthstone.com/api/v1)"
test "${legacy_api_status}" = '200'

graphql_body="$(curl --silent --show-error --fail --max-time 15 \
  -H 'Content-Type: application/json' \
  --data '{"query":"{ health { status } }"}' \
  https://api.kolodahearthstone.com/v1/)"
grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' <<<"${graphql_body}"

sudo -n docker compose --project-directory "${compose_dir}" -f "${compose_dir}/docker-compose.yml" exec -T postgres \
  sh -eu -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data -tAc \
    "SELECT count(*) = 5
       FROM information_schema.schemata
      WHERE schema_name IN ('\''analytics'\'', '\''catalog'\'', '\''hub'\'', '\''platform'\'', '\''raw'\'')"' \
  | grep -qx 't'

sudo -n docker compose --project-directory "${compose_dir}" -f "${compose_dir}/docker-compose.yml" exec -T postgres \
  sh -eu -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data -tAc \
    "SELECT count(*) > 0 FROM hub.integration_status"' \
  | grep -qx 't'

if sudo -n docker ps --format '{{.Image}}' | grep -q '^nocodb/nocodb:'; then
  log_event error platform_health_failed nocodb_container_still_running
  exit 1
fi

trap - ERR
log_event info platform_health_ok
