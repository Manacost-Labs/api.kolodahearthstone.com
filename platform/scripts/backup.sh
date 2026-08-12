#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

platform_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_dir="${platform_dir}/postgres"
backup_dir="${platform_dir}/backups"
retention_days="${HS_DATA_BACKUP_RETENTION_DAYS:-14}"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
lock_file="${backup_dir}/.backup.lock"

log_event() {
  local level="$1"
  local event="$2"
  local detail="${3:-}"
  printf '{"timestamp":"%s","level":"%s","event":"%s","run_id":"%s","detail":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$event" "$run_id" "$detail"
}

if [[ ! "${retention_days}" =~ ^[0-9]+$ ]] || (( retention_days < 1 )); then
  log_event error backup_failed invalid_retention
  exit 1
fi

mkdir -p -- "${backup_dir}"
chmod 0700 "${backup_dir}"
if [[ "$(realpath -- "${backup_dir}")" != "/srv/hs-data-platform/backups" ]]; then
  log_event error backup_failed unsafe_backup_path
  exit 1
fi

exec 9>"${lock_file}"
if ! flock -n 9; then
  log_event warn backup_skipped already_running
  exit 0
fi

hs_data_partial="${backup_dir}/hs-data-platform-${run_id}-hs_data.dump.partial"
cleanup() {
  rm -f -- "${hs_data_partial}"
}
on_error() {
  local exit_code="$?"
  log_event error backup_failed "exit_${exit_code}"
  exit "${exit_code}"
}
trap cleanup EXIT
trap on_error ERR

log_event info backup_started

sudo -n docker compose --project-directory "${compose_dir}" -f "${compose_dir}/docker-compose.yml" \
  exec -T postgres sh -eu -c 'pg_dump -Fc -U "$POSTGRES_USER" -d hs_data' >"${hs_data_partial}"

sudo -n docker compose --project-directory "${compose_dir}" -f "${compose_dir}/docker-compose.yml" \
  exec -T postgres pg_restore --list <"${hs_data_partial}" >/dev/null

mv -- "${hs_data_partial}" "${hs_data_partial%.partial}"

find "${backup_dir}" -maxdepth 1 -type f \
  -name 'hs-data-platform-*-hs_data.dump' \
  -mtime "+${retention_days}" -delete

trap - ERR EXIT
log_event info backup_completed
