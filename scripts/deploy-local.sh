#!/usr/bin/env bash
# Sync workspace to /opt/hs-data-api, merge env, reload systemd, restart API.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/hs-data-api}"
ENV_FILE="${ENV_FILE:-/etc/hs-data-api.env}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

echo "=== Sync $ROOT -> $INSTALL_DIR ==="
rsync -av --delete \
  --exclude data \
  --exclude '.env*' \
  --exclude '.credentials*' \
  --exclude venv \
  --exclude .git \
  --exclude .codegraph \
  --exclude .cursor \
  --exclude .pytest_cache \
  --exclude .ruff_cache \
  --exclude __pycache__ \
  --exclude scripts/fingerprint-node/node_modules \
  --exclude .venv \
  "$ROOT/" "$INSTALL_DIR/"

chmod +x "$INSTALL_DIR/scripts/"*.sh 2>/dev/null || true

if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
  python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install -U pip -q
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
if [[ "${INSTALL_SCRAPER_EXTRAS:-1}" == "1" ]]; then
  VENV="$INSTALL_DIR/venv" INSTALL_DIR="$INSTALL_DIR" "$INSTALL_DIR/scripts/install-scraper-extras.sh"
fi

echo "=== Merge env ==="
"$INSTALL_DIR/scripts/merge-env-example.sh" "$ENV_FILE"

echo "=== systemd ==="
install_systemd() {
  local unit="$1"
  sed "s|/opt/hs-data-api|$INSTALL_DIR|g" "$INSTALL_DIR/systemd/$unit" > "/etc/systemd/system/$unit"
}

for unit in \
  hs-data-api.service \
  hs-data-api-refresh.service \
  hs-data-api-refresh.timer \
  hs-data-api-refresh-protected.service \
  hs-data-api-refresh-protected.timer \
  hs-flaresolverr.service \
  hs-scrape-proxy.service; do
  if [[ -f "$INSTALL_DIR/systemd/$unit" ]]; then
    install_systemd "$unit"
  fi
done

systemctl daemon-reload
systemctl enable hs-data-api.service hs-data-api-refresh.timer hs-data-api-refresh-protected.timer 2>/dev/null || true
systemctl enable hs-flaresolverr.service 2>/dev/null || true

if command -v docker >/dev/null 2>&1 && [[ -f "$INSTALL_DIR/docker-compose.yml" ]]; then
  (cd "$INSTALL_DIR" && docker compose up -d flaresolverr 2>/dev/null) || true
  systemctl start hs-flaresolverr.service 2>/dev/null || true
fi

systemctl restart hs-data-api.service
echo "=== Deploy done ==="
systemctl is-active hs-data-api.service || true
