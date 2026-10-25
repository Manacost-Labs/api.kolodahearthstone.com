#!/usr/bin/env bash
# Install and enable every Docker-backed API systemd unit shipped by the repo.
# deploy-server.sh runs this on every deploy, so a timer that was already
# installed and deliberately disabled stays disabled; new timers are enabled.
set -euo pipefail
umask 0022

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/srv/hs-data-api}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"

if [[ "$SYSTEMD_DIR" == "/etc/systemd/system" && "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo), or set SYSTEMD_DIR for a staged install." >&2
  exit 1
fi

mapfile -t units < <(
  find "$ROOT/systemd" -maxdepth 1 -type f \
    \( -name 'hs-data-api-docker.service' -o -name 'hs-data-api-docker-*.service' -o -name 'hs-data-api-docker-*.timer' \) \
    -printf '%f\n' | sort
)
mapfile -t timers < <(printf '%s\n' "${units[@]}" | grep '\.timer$' || true)

if [[ "${#units[@]}" -eq 0 || "${#timers[@]}" -eq 0 ]]; then
  echo "No Docker systemd units/timers found under $ROOT/systemd" >&2
  exit 1
fi

mkdir -p "$SYSTEMD_DIR"
declare -A preinstalled=()
for timer in "${timers[@]}"; do
  [[ -f "$SYSTEMD_DIR/$timer" ]] && preinstalled["$timer"]=1
done
for unit in "${units[@]}"; do
  sed "s|/srv/hs-data-api|$INSTALL_DIR|g" \
    "$ROOT/systemd/$unit" > "$SYSTEMD_DIR/$unit"
done

"$SYSTEMCTL_BIN" daemon-reload
enabled=0
for timer in "${timers[@]}"; do
  if [[ -n "${preinstalled[$timer]:-}" ]] \
    && [[ "$("$SYSTEMCTL_BIN" is-enabled "$timer" 2>/dev/null || true)" == "disabled" ]]; then
    echo "Keeping $timer disabled (disabled before this install)."
    continue
  fi
  "$SYSTEMCTL_BIN" enable --now "$timer"
  enabled=$((enabled + 1))
done

echo "Installed ${#units[@]} Docker units and enabled ${enabled}/${#timers[@]} timers."
