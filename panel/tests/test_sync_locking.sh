#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$ROOT/scripts/run_sync_job.sh"
OVERRIDE="$ROOT/systemd/kolodahs-sync@constructed-wiki-refresh.service.d/override.conf"

bash -n "$RUNNER"
grep -Fq 'flock -w "$GLOBAL_LOCK_WAIT_SECONDS" 8' "$RUNNER"
grep -Fq 'write_status "lock_timeout" 75' "$RUNNER"
if grep -Fq 'flock -n 8' "$RUNNER"; then
  echo "global lock contention must wait instead of being skipped" >&2
  exit 1
fi
grep -Fq 'Restart=on-failure' "$OVERRIDE"
grep -Fq 'RestartSec=45m' "$OVERRIDE"
