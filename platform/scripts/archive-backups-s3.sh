#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

backup_dir="${HS_DATA_S3_BACKUP_DIR:-/srv/hs-data-platform/backups}"
compose_dir="${HS_DATA_S3_COMPOSE_DIR:-/srv/hs-data-platform/postgres}"
config_file="${HS_DATA_S3_ARCHIVE_CONFIG:-/etc/hs-data-platform/s3-archive.env}"
lock_file="${HS_DATA_S3_ARCHIVE_LOCK:-${backup_dir}/.backup.lock}"
rclone_bin="${HS_DATA_S3_RCLONE_BIN:-/usr/bin/rclone}"
docker_bin="${HS_DATA_S3_DOCKER_BIN:-/usr/bin/docker}"
test_mode="${HS_DATA_S3_ARCHIVE_TEST_MODE:-0}"

mode=archive

log_event() {
  local level=$1
  local event=$2
  local detail=${3:-}
  jq -cn \
    --arg timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg level "$level" \
    --arg event "$event" \
    --arg detail "$detail" \
    '{timestamp:$timestamp,level:$level,event:$event,detail:$detail}'
}

die() {
  log_event error archive_failed "$1" >&2
  exit 1
}

require_safe_paths() {
  if [[ "$test_mode" != 1 ]]; then
    [[ "$(realpath -- "$backup_dir")" == /srv/hs-data-platform/backups ]] \
      || die unsafe_backup_directory
    [[ "$(realpath -- "$compose_dir")" == /srv/hs-data-platform/postgres ]] \
      || die unsafe_compose_directory
  fi
  [[ -d "$backup_dir" ]] || die missing_backup_directory
  [[ -x "$rclone_bin" ]] || die missing_rclone
  [[ -x "$docker_bin" ]] || die missing_docker
}

load_config() {
  [[ -r "$config_file" ]] || die missing_archive_configuration
  # The root-owned configuration contains only S3 archive settings and an
  # rclone-obscured crypt password. It must never be printed or logged.
  # shellcheck disable=SC1090
  source "$config_file"

  : "${S3_CREDENTIALS_FILE:?missing S3_CREDENTIALS_FILE}"
  : "${S3_BUCKET:?missing S3_BUCKET}"
  : "${S3_PREFIX:?missing S3_PREFIX}"
  : "${S3_CRYPT_PASSWORD:?missing S3_CRYPT_PASSWORD}"
  : "${S3_CRYPT_PASSWORD2:?missing S3_CRYPT_PASSWORD2}"
  : "${LOCAL_KEEP_COUNT:?missing LOCAL_KEEP_COUNT}"
  : "${S3_REMOTE_KEEP_DAYS:?missing S3_REMOTE_KEEP_DAYS}"
  : "${S3_BWLIMIT:?missing S3_BWLIMIT}"

  [[ -r "$S3_CREDENTIALS_FILE" ]] || die unreadable_s3_credentials
  [[ "$S3_BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || die unsafe_s3_bucket
  [[ "$S3_PREFIX" == hs-data-platform ]] || die unsafe_s3_prefix
  [[ "$LOCAL_KEEP_COUNT" =~ ^[1-9][0-9]*$ ]] || die invalid_local_keep_count
  [[ "$S3_REMOTE_KEEP_DAYS" =~ ^[1-9][0-9]*$ ]] || die invalid_remote_keep_days
  (( S3_REMOTE_KEEP_DAYS >= 35 )) || die remote_keep_days_too_short
}

configure_remotes() {
  export RCLONE_CONFIG_OVH_TYPE=s3
  export RCLONE_CONFIG_OVH_PROVIDER=Other
  export RCLONE_CONFIG_OVH_REGION=eu-west-par
  export RCLONE_CONFIG_OVH_FORCE_PATH_STYLE=true
  export RCLONE_CONFIG_OVH_ACCESS_KEY_ID
  export RCLONE_CONFIG_OVH_SECRET_ACCESS_KEY
  export RCLONE_CONFIG_OVH_ENDPOINT
  RCLONE_CONFIG_OVH_ACCESS_KEY_ID=$(jq -er '.access_key' "$S3_CREDENTIALS_FILE")
  RCLONE_CONFIG_OVH_SECRET_ACCESS_KEY=$(jq -er '.secret_key' "$S3_CREDENTIALS_FILE")
  RCLONE_CONFIG_OVH_ENDPOINT=$(jq -er '.endpoint' "$S3_CREDENTIALS_FILE")

  export RCLONE_CONFIG_HSDATA_TYPE=crypt
  export RCLONE_CONFIG_HSDATA_REMOTE="ovh:${S3_BUCKET}/${S3_PREFIX}"
  export RCLONE_CONFIG_HSDATA_FILENAME_ENCRYPTION=standard
  export RCLONE_CONFIG_HSDATA_DIRECTORY_NAME_ENCRYPTION=true
  export RCLONE_CONFIG_HSDATA_PASSWORD="$S3_CRYPT_PASSWORD"
  export RCLONE_CONFIG_HSDATA_PASSWORD2="$S3_CRYPT_PASSWORD2"
}

rclone_args=()

configure_rclone_args() {
  rclone_args=(
    --s3-no-check-bucket
    --s3-acl private
    --transfers 2
    --checkers 4
    --s3-upload-concurrency 1
    --s3-chunk-size 16M
    --retries 5
    --low-level-retries 10
    --bwlimit "$S3_BWLIMIT"
    --log-level NOTICE
  )
}

archive_remote() {
  printf 'hsdata:'
}

remote_object() {
  local name=$1
  printf '%s%s' "$(archive_remote)" "$name"
}

make_manifest() {
  local dump=$1
  local manifest=$2
  local name
  name=$(basename -- "$dump")
  printf '%s  %s\n' "$(sha256sum -- "$dump" | awk '{print $1}')" "$name" >"$manifest"
}

manifest_hash() {
  local manifest=$1
  awk 'NR == 1 {print $1; exit}' "$manifest"
}

validate_manifest() {
  local manifest=$1
  local expected_name=$2
  local hash name
  read -r hash name <"$manifest" || return 1
  [[ "$hash" =~ ^[a-f0-9]{64}$ && "$name" == "$expected_name" ]]
}

remote_size_matches() {
  local remote=$1
  local expected_size=$2
  local item size
  item=$("$rclone_bin" lsjson --stat "$remote" "${rclone_args[@]}") || return 1
  size=$(jq -er 'if type == "array" then .[0] else . end | select(.IsDir == false) | .Size' <<<"$item") || return 1
  [[ "$size" == "$expected_size" ]]
}

remote_manifest_matches() {
  local remote_manifest=$1
  local local_manifest=$2
  local received
  received=$(mktemp "${TMPDIR:-/var/tmp}/hs-data-platform-s3-manifest.XXXXXX")
  if ! "$rclone_bin" cat "$remote_manifest" "${rclone_args[@]}" >"$received"; then
    rm -f -- "$received"
    return 1
  fi
  cmp -s "$local_manifest" "$received"
  local result=$?
  rm -f -- "$received"
  return "$result"
}

upload_if_needed() {
  local dump=$1
  local manifest=$2
  local name remote_dump remote_manifest size
  name=$(basename -- "$dump")
  remote_dump=$(remote_object "$name")
  remote_manifest=$(remote_object "${name}.sha256")
  size=$(stat -c %s -- "$dump")

  if remote_size_matches "$remote_dump" "$size" \
    && remote_manifest_matches "$remote_manifest" "$manifest"; then
    log_event info archive_present "$name"
    return 0
  fi

  log_event info archive_upload_started "$name"
  "$rclone_bin" copyto "$dump" "$remote_dump" --immutable "${rclone_args[@]}"
  "$rclone_bin" copyto "$manifest" "$remote_manifest" --immutable "${rclone_args[@]}"

  remote_size_matches "$remote_dump" "$size" || die "remote_size_mismatch:${name}"
  remote_manifest_matches "$remote_manifest" "$manifest" \
    || die "remote_manifest_mismatch:${name}"
  log_event info archive_upload_verified "$name"
}

check_restore_capacity() {
  local dump_size=$1
  local available required
  available=$(df -B1 --output=avail "${TMPDIR:-/var/tmp}" | awk 'NR == 2 {print $1}')
  required=$((dump_size + 1024 * 1024 * 1024))
  [[ "$available" =~ ^[0-9]+$ ]] || die cannot_measure_restore_capacity
  (( available >= required )) || die insufficient_restore_capacity
}

validate_restored_dump() {
  local dump=$1
  "$docker_bin" compose --project-directory "$compose_dir" \
    -f "$compose_dir/docker-compose.yml" exec -T postgres pg_restore --list <"$dump" >/dev/null
}

verify_remote_dump() {
  local name=$1
  local expected_manifest=${2:-}
  local work_dir manifest dump expected actual remote_manifest remote_dump
  remote_manifest=$(remote_object "${name}.sha256")
  remote_dump=$(remote_object "$name")
  work_dir=$(mktemp -d "${TMPDIR:-/var/tmp}/hs-data-platform-s3-restore.XXXXXX")
  manifest="$work_dir/${name}.sha256"
  dump="$work_dir/$name"
  cleanup_verify() { rm -rf --one-file-system "$work_dir"; }

  if ! "$rclone_bin" cat "$remote_manifest" "${rclone_args[@]}" >"$manifest"; then
    cleanup_verify
    die "remote_manifest_unreadable:${name}"
  fi
  validate_manifest "$manifest" "$name" || { cleanup_verify; die "invalid_remote_manifest:${name}"; }
  if [[ -n "$expected_manifest" ]] && ! cmp -s "$expected_manifest" "$manifest"; then
    cleanup_verify
    die "remote_manifest_changed:${name}"
  fi

  check_restore_capacity "$(remote_size "$remote_dump")"
  log_event info remote_restore_validation_started "$name"
  if ! "$rclone_bin" copyto "$remote_dump" "$dump" "${rclone_args[@]}"; then
    cleanup_verify
    die "remote_download_failed:${name}"
  fi
  expected=$(manifest_hash "$manifest")
  actual=$(sha256sum -- "$dump" | awk '{print $1}')
  if [[ "$expected" != "$actual" ]]; then
    cleanup_verify
    die "remote_sha256_mismatch:${name}"
  fi
  if ! validate_restored_dump "$dump"; then
    cleanup_verify
    die "remote_pg_restore_validation_failed:${name}"
  fi
  cleanup_verify
  log_event info remote_restore_validation_passed "$name"
}

remote_size() {
  local remote=$1
  local item
  item=$("$rclone_bin" lsjson --stat "$remote" "${rclone_args[@]}")
  jq -er 'if type == "array" then .[0] else . end | select(.IsDir == false) | .Size' <<<"$item"
}

backup_names() {
  find "$backup_dir" -maxdepth 1 -type f \
    -name 'hs-data-platform-*-hs_data.dump' -printf '%T@\t%p\n' \
    | LC_ALL=C sort -n | cut -f2-
}

remote_names() {
  "$rclone_bin" lsf "$(archive_remote)" --files-only \
    --include 'hs-data-platform-*-hs_data.dump' "${rclone_args[@]}" | LC_ALL=C sort
}

backup_epoch() {
  local name=$1
  local stamp year month day hour minute second
  stamp=${name#hs-data-platform-}
  stamp=${stamp:0:16}
  [[ "$stamp" =~ ^([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z$ ]] \
    || return 1
  year=${BASH_REMATCH[1]}
  month=${BASH_REMATCH[2]}
  day=${BASH_REMATCH[3]}
  hour=${BASH_REMATCH[4]}
  minute=${BASH_REMATCH[5]}
  second=${BASH_REMATCH[6]}
  date -u -d "${year}-${month}-${day} ${hour}:${minute}:${second} UTC" +%s
}

prune_remote_backups() {
  local -a names=()
  local name now cutoff epoch
  mapfile -t names < <(remote_names)
  (( ${#names[@]} > 7 )) || return 0
  now=$(date -u +%s)
  cutoff=$((now - S3_REMOTE_KEEP_DAYS * 86400))
  for name in "${names[@]}"; do
    epoch=$(backup_epoch "$name") || die "invalid_remote_backup_name:${name}"
    if (( epoch >= cutoff )); then
      continue
    fi
    "$rclone_bin" deletefile "$(remote_object "${name}.sha256")" "${rclone_args[@]}"
    "$rclone_bin" deletefile "$(remote_object "$name")" "${rclone_args[@]}"
    log_event info remote_archive_pruned "$name"
  done
}

service_guard() {
  [[ "$test_mode" == 1 ]] && return 0
  local unit
  for unit in hs-data-platform-backup.service server-backup-core.service; do
    if systemctl is-active --quiet "$unit"; then
      log_event info archive_deferred "$unit"
      exit 0
    fi
  done
}

plan() {
  local -a dumps=()
  local prune_count=0 i
  mapfile -t dumps < <(backup_names)
  if (( ${#dumps[@]} > LOCAL_KEEP_COUNT )); then
    prune_count=$((${#dumps[@]} - LOCAL_KEEP_COUNT))
  fi
  for ((i = 0; i < ${#dumps[@]}; i++)); do
    if (( i < prune_count )); then
      printf 'would_prune %s\n' "$(basename -- "${dumps[$i]}")"
    else
      printf 'would_keep %s\n' "$(basename -- "${dumps[$i]}")"
    fi
  done
}

archive() (
  local -a dumps=()
  local prune_count=0 i dump manifest work_dir
  service_guard
  mapfile -t dumps < <(backup_names)
  (( ${#dumps[@]} > 0 )) || { log_event info archive_nothing_to_do; return 0; }
  work_dir=$(mktemp -d "${TMPDIR:-/var/tmp}/hs-data-platform-s3-archive.XXXXXX")
  trap 'rm -rf --one-file-system "$work_dir"' EXIT

  for dump in "${dumps[@]}"; do
    manifest="$work_dir/$(basename -- "$dump").sha256"
    make_manifest "$dump" "$manifest"
    upload_if_needed "$dump" "$manifest"
  done

  if (( ${#dumps[@]} > LOCAL_KEEP_COUNT )); then
    prune_count=$((${#dumps[@]} - LOCAL_KEEP_COUNT))
  fi
  for ((i = 0; i < prune_count; i++)); do
    dump=${dumps[$i]}
    manifest="$work_dir/$(basename -- "$dump").sha256"
    verify_remote_dump "$(basename -- "$dump")" "$manifest"
    rm -f -- "$dump"
    log_event info local_backup_pruned "$(basename -- "$dump")"
  done
  prune_remote_backups
)

verify_one_remote() {
  local -a names=()
  mapfile -t names < <(remote_names)
  (( ${#names[@]} > 0 )) || die no_remote_archives
  verify_remote_dump "${names[0]}"
}

usage() {
  printf 'Usage: %s [--archive|--plan|--verify-remote]\n' "$(basename -- "$0")"
}

main() {
  case "${1:---archive}" in
    --archive) mode=archive ;;
    --plan) mode=plan ;;
    --verify-remote) mode=verify-remote ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac

  require_safe_paths
  if [[ "$mode" == plan ]]; then
    : "${LOCAL_KEEP_COUNT:=3}"
    [[ "$LOCAL_KEEP_COUNT" =~ ^[1-9][0-9]*$ ]] || die invalid_local_keep_count
    plan
    exit 0
  fi

  [[ ${EUID} -eq 0 ]] || die must_run_as_root
  load_config
  configure_remotes
  configure_rclone_args
  mkdir -p "$(dirname -- "$lock_file")"
  exec 9>"$lock_file"
  if ! flock -n 9; then
    log_event info archive_skipped already_running
    exit 0
  fi

  case "$mode" in
    archive) archive ;;
    verify-remote) verify_one_remote ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
