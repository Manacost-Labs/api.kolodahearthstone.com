#!/usr/bin/env bash
# LEGACY. Ставит сборку без Docker: venv, hs-data-api.service и таймеры
# hs-data-api-refresh.*. Боевая установка работает иначе — контейнер через
# docker compose и юниты hs-data-api-docker-*, поэтому здешний сервис на ней
# просто отсутствует.
#
# Для нового сервера используйте scripts/bootstrap-server.sh, для обновления
# существующего — scripts/deploy-server.sh. Этот скрипт оставлен для старых
# установок в /opt/hs-data-api.
# Install Hearthstone Data API on a fresh Ubuntu/Debian server.
# Usage: sudo ./scripts/install.sh [--dir /opt/hs-data-api] [--no-systemd]
set -euo pipefail

INSTALL_DIR="/opt/hs-data-api"
DATA_DIR="/var/lib/hs-data-api"
ENV_FILE="/etc/hs-data-api.env"
USE_SYSTEMD=1
REPO_URL="${HS_REPO_URL:-https://github.com/Manacost-Labs/api.kolodahearthstone.com.git}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --no-systemd) USE_SYSTEMD=0; shift ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo)." >&2
  exit 1
fi

apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip curl

if ! getent group hs-data-api >/dev/null; then
  groupadd --system hs-data-api
fi
if ! id -u hs-data-api >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir "$DATA_DIR" --shell /usr/sbin/nologin --gid hs-data-api hs-data-api
fi

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  echo "Repo exists at $INSTALL_DIR — git pull"
  git -C "$INSTALL_DIR" pull --ff-only
fi

cd "$INSTALL_DIR"
chmod +x scripts/*.sh 2>/dev/null || true
python3 -m venv venv
./venv/bin/pip install -U pip
./venv/bin/pip install -r requirements.txt
./venv/bin/patchright install chromium

mkdir -p "$DATA_DIR/datasets" "$DATA_DIR/statuses" "$DATA_DIR/logs"
chown -R hs-data-api:hs-data-api "$DATA_DIR" 2>/dev/null || true

if [[ ! -f "$ENV_FILE" ]]; then
  cp .env.example "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE — edit proxy URL, API key, optional HSReplay/Telegram."
else
  ./scripts/merge-env-example.sh "$ENV_FILE"
fi

if command -v docker >/dev/null 2>&1 && [[ -f docker-compose.yml ]]; then
  docker compose up -d flaresolverr 2>/dev/null || true
fi

if [[ "$USE_SYSTEMD" -eq 1 ]]; then
  install_unit() {
    local unit="$1"
    if [[ -f "systemd/$unit" ]]; then
      sed "s|/opt/hs-data-api|$INSTALL_DIR|g" "systemd/$unit" > "/etc/systemd/system/$unit"
    fi
  }
  for unit in \
    hs-data-api.service \
    hs-data-api-refresh.service \
    hs-data-api-refresh.timer \
    hs-data-api-refresh-api.service \
    hs-data-api-refresh-api.timer \
    hs-data-api-refresh-protected.service \
    hs-data-api-refresh-protected.timer \
    hs-data-api-firecrawl-streamer.service \
    hs-data-api-firecrawl-streamer.timer \
    hs-data-api-protected-recovery.service \
    hs-data-api-freshness-check.service \
    hs-data-api-freshness-check.timer \
    hs-flaresolverr.service \
    hs-scrape-proxy.service; do
    install_unit "$unit"
  done
  systemctl daemon-reload
  systemctl enable hs-data-api.service hs-data-api-refresh.timer hs-data-api-refresh-api.timer hs-data-api-freshness-check.timer hs-data-api-firecrawl-streamer.timer
  systemctl disable --now hs-data-api-refresh-protected.timer 2>/dev/null || true
  systemctl enable hs-flaresolverr.service 2>/dev/null || true
  systemctl start hs-flaresolverr.service 2>/dev/null || true
  echo "Enabled systemd units. Start hs-data-api/timers after editing $ENV_FILE."
fi

echo ""
echo "Install done."
echo "  App:     $INSTALL_DIR"
echo "  Data:    $DATA_DIR"
echo "  Config:  $ENV_FILE"
echo ""
echo "Next steps:"
echo "  1. Edit $ENV_FILE (HS_FETCH_PROXY_URL, HS_API_KEY)"
echo "  2. systemctl restart hs-data-api hs-flaresolverr"
echo "  3. systemctl start hs-data-api-refresh.timer hs-data-api-refresh-api.timer hs-data-api-freshness-check.timer"
echo "  4. $INSTALL_DIR/scripts/server-readiness.sh --strict"
echo "  5. $INSTALL_DIR/scripts/server-readiness.sh --strict --refresh-all"
