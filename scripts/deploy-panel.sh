#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ROOT="$PROJECT_ROOT/panel"
TARGET_ROOT="${KOLODAHS_PANEL_TARGET_ROOT:-/srv/api-kolodahearthstone/panel}"
DATA_ROOT="${KOLODAHS_PANEL_DATA_ROOT:-/srv/api-kolodahearthstone/panel-data}"
CONFIG_PATH="${KOLODAHS_PANEL_CONFIG_PATH:-/etc/api-kolodahearthstone/panel-config.php}"
RELEASE_ID="${KOLODAHS_PANEL_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RELEASE_ROOT="$TARGET_ROOT/releases/$RELEASE_ID"

[[ "$EUID" -eq 0 ]] || {
  echo "deploy-panel: run as root" >&2
  exit 1
}

die() {
  echo "deploy-panel: $*" >&2
  exit 1
}

validate_absolute_path() {
  local label="$1"
  local path="$2"
  [[ "$path" = /* ]] || die "$label must be absolute"
  [[ "$path" != "/" && "$path" != "/srv" && "$path" != "/etc" ]] \
    || die "$label is too broad"
}

validate_absolute_path "target root" "$TARGET_ROOT"
validate_absolute_path "data root" "$DATA_ROOT"
validate_absolute_path "config path" "$CONFIG_PATH"
[[ "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid release id"
[[ -d "$SOURCE_ROOT" ]] || die "panel source is missing"
[[ -f "$CONFIG_PATH" ]] || die "private config is missing: $CONFIG_PATH"
[[ ! -e "$RELEASE_ROOT" ]] || die "release already exists: $RELEASE_ID"

install -d -o koloda -g koloda -m 0755 \
  "$TARGET_ROOT" "$TARGET_ROOT/releases" "$DATA_ROOT" \
  "$DATA_ROOT/uploads" "$DATA_ROOT/var"
install -d -m 0755 "$RELEASE_ROOT"

rsync -a --delete \
  --exclude='config.php' \
  --exclude='uploads/' \
  --exclude='var/' \
  --exclude='tests/' \
  --exclude='systemd/' \
  "$SOURCE_ROOT/" "$RELEASE_ROOT/"

ln -s "$CONFIG_PATH" "$RELEASE_ROOT/config.php"
ln -s "$DATA_ROOT/uploads" "$RELEASE_ROOT/uploads"
ln -s "$DATA_ROOT/var" "$RELEASE_ROOT/var"

chown -R koloda:koloda "$RELEASE_ROOT"
find "$RELEASE_ROOT" -type d -exec chmod 0755 {} +
find "$RELEASE_ROOT" -type f -exec chmod 0640 {} +
find "$RELEASE_ROOT/assets" -type f -exec chmod 0644 {} +
find "$RELEASE_ROOT/scripts" -type f \( -name '*.py' -o -name '*.sh' \) -exec chmod 0750 {} +
chmod 0640 "$CONFIG_PATH"
chown root:koloda "$CONFIG_PATH"

ln -sfn "$RELEASE_ROOT" "$TARGET_ROOT/current.next"
mv -Tf "$TARGET_ROOT/current.next" "$TARGET_ROOT/current"

echo "panel_release=$RELEASE_ID"
echo "panel_current=$RELEASE_ROOT"
