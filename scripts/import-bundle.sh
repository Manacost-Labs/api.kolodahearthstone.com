#!/usr/bin/env bash
# Import migration bundle from export-bundle.sh
# Usage: sudo ./scripts/import-bundle.sh hs-data-api-bundle-YYYYMMDD.tar.gz
set -euo pipefail

ARCHIVE="${1:?Usage: import-bundle.sh <archive.tar.gz>}"
DATA_DIR="${HS_API_DATA_DIR:-/var/lib/hs-data-api}"
ENV_FILE="${HS_ENV_FILE:-/etc/hs-data-api.env}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

tar -xzf "$ARCHIVE" -C "$TMP"
B="$TMP/bundle"
[[ -d "$B" ]] || { echo "Invalid bundle layout"; exit 1; }

# Lab history is append-only. Restores target a fresh, stopped instance only.
if [[ -f "$B/meta-intelligence/lab.sqlite3" ]]; then
  if [[ -e "$DATA_DIR/meta-intelligence/lab.sqlite3" || -e "$DATA_DIR/meta-intelligence/lab.sqlite3-wal" ]]; then
    echo "Refusing to overwrite existing Lab history; use a fresh data directory."
    exit 1
  fi
  python3 - "$B/meta-intelligence/lab.sqlite3" <<'PY'
import sqlite3
import sys
from pathlib import Path
conn = sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True)
try:
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise SystemExit('Lab restore failed integrity check')
    if conn.execute('PRAGMA user_version').fetchone()[0] != 1:
        raise SystemExit('Unsupported Lab schema')
finally:
    conn.close()
PY
fi

mkdir -p "$DATA_DIR/datasets" "$DATA_DIR/statuses"

if [[ -f "$B/hs-data-api.env" ]]; then
  if [[ -f "$ENV_FILE" ]]; then
    cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%s)"
  fi
  cp "$B/hs-data-api.env" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Installed $ENV_FILE"
fi

for sub in datasets statuses; do
  if [[ -d "$B/$sub" ]]; then
    cp -a "$B/$sub/." "$DATA_DIR/$sub/"
    echo "Restored $DATA_DIR/$sub"
  fi
done

if [[ -f "$B/meta-intelligence/lab.sqlite3" ]]; then
  mkdir -p "$DATA_DIR/meta-intelligence"
  cp "$B/meta-intelligence/lab.sqlite3" "$DATA_DIR/meta-intelligence/lab.sqlite3"
  chmod 600 "$DATA_DIR/meta-intelligence/lab.sqlite3"
fi

for f in hsreplay-auth.json hearthstonejson.cards.enUS.json hearthstonejson.cards.ruRU.json; do
  [[ -f "$B/$f" ]] && cp "$B/$f" "$DATA_DIR/$f" && echo "Restored $DATA_DIR/$f"
done

echo "Import complete. Restart API and verify: python -m app.cli proxy-check && ./scripts/audit.sh"
