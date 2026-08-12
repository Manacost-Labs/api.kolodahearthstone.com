#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if rg -n '/var/www/koloda/data/www/db\.kolodahs\.ru' "$ROOT" \
  --glob '!docs/**' \
  --glob '!tests/runtime_layout_test.sh'; then
  echo "panel code still depends on the retired runtime path" >&2
  exit 1
fi

if find "$ROOT" -type f \( -name 'config.php' -o -path '*/uploads/*' -o -path '*/var/*' \) -print -quit | grep -q .; then
  echo "runtime data or config.php must not be committed with panel source" >&2
  exit 1
fi

bash -n "$ROOT/scripts/run_sync_job.sh"
grep -Fq 'KOLODAHS_PANEL_ROOT' "$ROOT/scripts/run_sync_job.sh"
