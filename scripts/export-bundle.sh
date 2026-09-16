#!/usr/bin/env bash
# Export config + cache for migration to another server (keep archive private!).
# Usage: ./scripts/export-bundle.sh [output.tar.gz]
set -euo pipefail

DATA_DIR="${HS_API_DATA_DIR:-/var/lib/hs-data-api}"
ENV_FILE="${HS_ENV_FILE:-/etc/hs-data-api.env}"
OUT="${1:-hs-data-api-bundle-$(date +%Y%m%d).tar.gz}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bundle/datasets" "$TMP/bundle/statuses"

if [[ -f "$ENV_FILE" ]]; then
  cp "$ENV_FILE" "$TMP/bundle/hs-data-api.env"
else
  echo "Warning: $ENV_FILE not found" >&2
fi

for sub in datasets statuses; do
  if [[ -d "$DATA_DIR/$sub" ]]; then
    cp -a "$DATA_DIR/$sub/." "$TMP/bundle/$sub/" 2>/dev/null || true
  fi
done

# SQLite WAL cannot be backed up by copying only the main file.
if [[ -f "$DATA_DIR/meta-intelligence/lab.sqlite3" ]]; then
  mkdir -p "$TMP/bundle/meta-intelligence"
  python3 - "$DATA_DIR/meta-intelligence/lab.sqlite3" "$TMP/bundle/meta-intelligence/lab.sqlite3" <<'PY'
import sqlite3
import sys
from pathlib import Path
source = sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise SystemExit('Lab backup failed integrity check')
    Path(sys.argv[2]).chmod(0o600)
finally:
    target.close()
    source.close()
PY
fi

for f in hsreplay-auth.json hearthstonejson.cards.enUS.json hearthstonejson.cards.ruRU.json; do
  [[ -f "$DATA_DIR/$f" ]] && cp "$DATA_DIR/$f" "$TMP/bundle/"
done

cat > "$TMP/bundle/MANIFEST.txt" <<EOF
exported_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
hostname=$(hostname)
data_dir=$DATA_DIR
EOF

tar -czf "$OUT" -C "$TMP" bundle
echo "Created: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Transfer securely, then on new server: ./scripts/import-bundle.sh $OUT"
