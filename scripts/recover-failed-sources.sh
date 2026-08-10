#!/usr/bin/env bash
# Re-fetch all sources that are not ok (one at a time to avoid lock contention).
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/hs-data-api}"
VENV="$INSTALL_DIR/venv/bin/python"
export HS_API_DATA_DIR="${HS_API_DATA_DIR:-/var/lib/hs-data-api}"

cd "$INSTALL_DIR"

mapfile -t FAILED < <(
  "$VENV" <<'PY'
from app.cli import load_env_file
load_env_file()
from app.sources import SOURCES
from app.storage import load_status
for s in SOURCES:
    st = (load_status(s.id) or {}).get("state", "never_fetched")
    if st != "ok":
        print(s.id)
PY
)

echo "Recovering ${#FAILED[@]} sources..."
for sid in "${FAILED[@]}"; do
  echo "=== $sid ==="
  if "$VENV" -m app.cli refresh --source "$sid"; then
    echo "OK $sid"
  else
    echo "FAIL $sid" >&2
  fi
  sleep 3
done

echo "=== Final health ==="
curl -sf http://127.0.0.1:8000/health | "$VENV" -m json.tool
