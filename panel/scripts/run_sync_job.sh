#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="${KOLODAHS_PANEL_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON="/opt/wiki-hs-parser/.venv/bin/python"
PHP="/usr/bin/php"
JOB="${1:-}"
LOG_DIR="/var/log/kolodahs-sync"
STATUS_FILE="$APP_ROOT/var/sync/status.json"
LOCK_DIR="/run/lock/kolodahs-sync"
API_BASE="https://api.kolodahearthstone.com/api/v1"
GLOBAL_LOCK_WAIT_SECONDS="${KOLODAHS_SYNC_GLOBAL_LOCK_WAIT_SECONDS:-21600}"
AFTER_CMD=()
SECOND_AFTER_CMD=()
THIRD_AFTER_CMD=()

usage() {
  echo "Usage: $0 {cards|libraries|hero-skins|hero-skins-refresh|pets|coins|heroes|heroes-refresh|timewarped|timewarped-refresh|wiki-meta-missing|wiki-meta-full|constructed-cards|constructed-images|constructed-related|constructed-related-wiki-art|constructed-wiki-missing|constructed-wiki-refresh|diamond-cards|horizontal-art}" >&2
}

if [[ -z "$JOB" ]]; then
  usage
  exit 2
fi

mkdir -p "$LOG_DIR" "$LOCK_DIR" "$(dirname "$STATUS_FILE")"

case "$JOB" in
  cards)
    CMD=("$PHP" "$APP_ROOT/scripts/scan_cards.php" "--source=all")
    AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_battlegrounds_images.py")
    SECOND_AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/audit_battlegrounds_framed.py" "--scope" "all")
    THIRD_AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/audit_battlegrounds_variants.py")
    SMOKE_URLS=("$API_BASE/meta" "$API_BASE/cards?per_page=1")
    ;;
  libraries)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_library_cards.py" "--library" "all")
    AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_library_full_art.py" "--library" "trinket")
    SMOKE_URLS=("$API_BASE/anomalies?per_page=1" "$API_BASE/dark-gifts?per_page=1" "$API_BASE/quests?per_page=1" "$API_BASE/darkmoon-prizes?per_page=1" "$API_BASE/rewards?per_page=1" "$API_BASE/trinkets?per_page=1")
    ;;
  hero-skins)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_hero_skins.py" "--refresh-index")
    SMOKE_URLS=("$API_BASE/hero-skins?per_page=1" "$API_BASE/meta")
    ;;
  hero-skins-refresh)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_hero_skins.py" "--refresh-index" "--refresh-pages")
    SMOKE_URLS=("$API_BASE/hero-skins?per_page=1" "$API_BASE/meta")
    ;;
  pets)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_pets.py")
    SMOKE_URLS=("$API_BASE/pets?per_page=1" "$API_BASE/meta")
    ;;
  coins)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_wiki_meta.py" "--card-type" "spell" "--limit" "0" "--refresh-index" "--delay-seconds" "0" "--jitter-seconds" "0")
    AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_coins.py")
    SMOKE_URLS=("$API_BASE/coins?per_page=1" "$API_BASE/meta")
    ;;
  heroes)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_wiki_heroes.py" "--delay-seconds" "1.2" "--jitter-seconds" "0.4")
    AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_hsj_missing_heroes.py")
    SMOKE_URLS=("$API_BASE/heroes?per_page=1")
    ;;
  heroes-refresh)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_wiki_heroes.py" "--refresh-index" "--refresh-pages" "--delay-seconds" "1.2" "--jitter-seconds" "0.4")
    AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_hsj_missing_heroes.py")
    SMOKE_URLS=("$API_BASE/heroes?per_page=1")
    ;;
  timewarped)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_timewarped_cards.py" "--delay-seconds" "0.8" "--jitter-seconds" "0.25")
    SMOKE_URLS=("$API_BASE/timewarped-cards?per_page=1")
    ;;
  timewarped-refresh)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_timewarped_cards.py" "--refresh-index" "--refresh-pages" "--delay-seconds" "0.8" "--jitter-seconds" "0.25")
    SMOKE_URLS=("$API_BASE/timewarped-cards?per_page=1")
    ;;
  wiki-meta-missing)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_wiki_meta.py" "--card-type" "all" "--missing-only" "--in-pool-only" "--delay-seconds" "1.2" "--jitter-seconds" "0.4")
    SMOKE_URLS=("$API_BASE/cards?per_page=1&include=wiki")
    ;;
  wiki-meta-full)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_wiki_meta.py" "--card-type" "all" "--refresh-index" "--refresh-pages" "--delay-seconds" "1.2" "--jitter-seconds" "0.4")
    SMOKE_URLS=("$API_BASE/cards?per_page=1&include=wiki")
    ;;
  constructed-cards)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_cards.py" "--format" "all")
    AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/backfill_constructed_images.py" "--golden-only")
    SECOND_AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_related_cards.py" "--format" "all")
    THIRD_AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_related_wiki_art.py" "--format" "all")
    SMOKE_URLS=("$API_BASE/meta")
    ;;
  constructed-mirror)
    CMD=("$PYTHON" "$APP_ROOT/scripts/mirror_constructed_images.py")
    SMOKE_URLS=("$API_BASE/constructed-cards?per_page=1")
    ;;
  constructed-images)
    CMD=("$PYTHON" "$APP_ROOT/scripts/backfill_constructed_images.py")
    SMOKE_URLS=("$API_BASE/constructed-cards?per_page=1")
    ;;
  constructed-related)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_related_cards.py" "--format" "all")
    AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_related_wiki_art.py" "--format" "all")
    SMOKE_URLS=("$API_BASE/constructed-cards/DINO_410" "$API_BASE/constructed-cards/TLC_817")
    ;;
  constructed-related-wiki-art)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_related_wiki_art.py" "--format" "all")
    SMOKE_URLS=("$API_BASE/constructed-cards/TIME_005" "$API_BASE/constructed-cards/TIME_005t1")
    ;;
  constructed-wiki-missing)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_wiki_meta.py" "--format" "all" "--missing-only" "--limit" "100" "--delay-seconds" "1.2" "--jitter-seconds" "0.4")
    AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_related_cards.py" "--format" "all")
    SMOKE_URLS=("$API_BASE/meta")
    ;;
  constructed-wiki-refresh)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_wiki_meta.py" "--format" "all" "--limit" "500" "--oldest-first" "--refresh-pages" "--skip-ban-list-refresh" "--delay-seconds" "0.8" "--jitter-seconds" "0.4")
    AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_related_cards.py" "--format" "all")
    SECOND_AFTER_CMD=("$PYTHON" "$APP_ROOT/scripts/sync_constructed_related_wiki_art.py" "--format" "all")
    SMOKE_URLS=("$API_BASE/meta")
    ;;
  diamond-cards)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_diamond_cards.py")
    SMOKE_URLS=("$API_BASE/diamond-cards?per_page=1" "$API_BASE/constructed-cards?media=diamond&per_page=1" "$API_BASE/meta")
    ;;
  horizontal-art)
    CMD=("$PYTHON" "$APP_ROOT/scripts/sync_horizontal_art.py")
    SMOKE_URLS=("$API_BASE/cards?per_page=1" "$API_BASE/constructed-cards?per_page=1" "$API_BASE/heroes?per_page=1")
    ;;
  *)
    usage
    exit 2
    ;;
esac

started_epoch="$(date +%s)"
started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
log_file="$LOG_DIR/${JOB}-$(date -u +"%Y%m%dT%H%M%SZ").log"

write_status() {
  local status="$1"
  local exit_code="$2"
  local finished_at="${3:-}"
  local duration="${4:-0}"
  "$PYTHON" - "$STATUS_FILE" "$JOB" "$status" "$started_at" "$finished_at" "$exit_code" "$duration" "$log_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
job, status, started_at, finished_at, exit_code, duration, log_file = sys.argv[2:9]
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        data = {}
except Exception:
    data = {}

jobs = data.setdefault("jobs", {})
jobs[job] = {
    "job": job,
    "status": status,
    "started_at": started_at,
    "finished_at": finished_at or None,
    "exit_code": int(exit_code),
    "duration_seconds": int(float(duration)),
    "log_file": log_file,
}
data["updated_at"] = finished_at or started_at
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
tmp.replace(path)
PY
}

exec 8>"$LOCK_DIR/global.lock"
if ! flock -w "$GLOBAL_LOCK_WAIT_SECONDS" 8; then
  finished_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  duration="$(( $(date +%s) - started_epoch ))"
  write_status "lock_timeout" 75 "$finished_at" "$duration"
  echo "$finished_at $JOB failed: global lock was busy for ${GLOBAL_LOCK_WAIT_SECONDS}s" >> "$log_file"
  exit 75
fi

exec 9>"$LOCK_DIR/${JOB}.lock"
if ! flock -n 9; then
  finished_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  duration="$(( $(date +%s) - started_epoch ))"
  write_status "skipped_locked" 0 "$finished_at" "$duration"
  echo "$finished_at $JOB skipped: same job is already running" >> "$log_file"
  exit 0
fi

write_status "running" 0 "" 0

handle_termination() {
  local finished_at
  local duration
  finished_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  duration="$(( $(date +%s) - started_epoch ))"
  write_status "canceled" 143 "$finished_at" "$duration"
  exit 143
}

trap handle_termination TERM INT

set +e
{
  echo "started_at=$started_at"
  echo "job=$JOB"
  printf 'command='
  printf '%q ' "${CMD[@]}"
  echo
  echo
  "${CMD[@]}"
  command_rc=$?
  if [[ "$command_rc" -eq 0 && "${#AFTER_CMD[@]}" -gt 0 ]]; then
    echo
    printf 'after_command='
    printf '%q ' "${AFTER_CMD[@]}"
    echo
    echo
    "${AFTER_CMD[@]}"
    command_rc=$?
  fi
  if [[ "$command_rc" -eq 0 && "${#SECOND_AFTER_CMD[@]}" -gt 0 ]]; then
    echo
    printf 'second_after_command='
    printf '%q ' "${SECOND_AFTER_CMD[@]}"
    echo
    echo
    "${SECOND_AFTER_CMD[@]}"
    command_rc=$?
  fi
  if [[ "$command_rc" -eq 0 && "${#THIRD_AFTER_CMD[@]}" -gt 0 ]]; then
    echo
    printf 'third_after_command='
    printf '%q ' "${THIRD_AFTER_CMD[@]}"
    echo
    echo
    "${THIRD_AFTER_CMD[@]}"
    command_rc=$?
  fi
  (exit "$command_rc")
} >> "$log_file" 2>&1
rc=$?

if [[ "$rc" -eq 0 ]]; then
  {
    echo
    echo "post_sync_smoke_tests"
    for url in "${SMOKE_URLS[@]}"; do
      echo "GET $url"
      curl -fsS "$url" >/dev/null
    done
    if [[ "$JOB" == "libraries" ]]; then
      "$PYTHON" "$APP_ROOT/scripts/audit_library_images.py" --library all
    fi
  } >> "$log_file" 2>&1
  rc=$?
fi
set -e

finished_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
duration="$(( $(date +%s) - started_epoch ))"
if [[ "$rc" -eq 0 ]]; then
  write_status "ok" 0 "$finished_at" "$duration"
else
  write_status "error" "$rc" "$finished_at" "$duration"
fi

exit "$rc"
