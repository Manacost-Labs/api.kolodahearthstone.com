#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_ROOT="$PROJECT_ROOT/platform"
TARGET_ROOT="${HS_DATA_PLATFORM_TARGET_ROOT:-/srv/hs-data-platform}"

die() {
  echo "deploy-platform: $*" >&2
  exit 1
}

[[ "$EUID" -eq 0 ]] || die "run as root"
[[ "$TARGET_ROOT" = /* ]] || die "target root must be absolute"
[[ "$TARGET_ROOT" != "/" && "$TARGET_ROOT" != "/srv" ]] \
  || die "target root is too broad"
[[ -d "$SOURCE_ROOT" ]] || die "platform source is missing"

install -d -o debian -g debian -m 0750 "$TARGET_ROOT" "$TARGET_ROOT/postgres"

for entry in design docs scripts sql systemd tests; do
  install -d -o debian -g debian -m 0750 "$TARGET_ROOT/$entry"
  rsync -a --delete "$SOURCE_ROOT/$entry/" "$TARGET_ROOT/$entry/"
done

install -o debian -g debian -m 0640 "$SOURCE_ROOT/README.md" "$TARGET_ROOT/README.md"
install -o debian -g debian -m 0640 \
  "$SOURCE_ROOT/postgres/docker-compose.yml" \
  "$TARGET_ROOT/postgres/docker-compose.yml"
install -o debian -g debian -m 0640 \
  "$SOURCE_ROOT/postgres/.gitignore" \
  "$TARGET_ROOT/postgres/.gitignore"

find "$TARGET_ROOT/scripts" "$TARGET_ROOT/tests" -type f -exec chmod 0750 {} +
find "$TARGET_ROOT/design" "$TARGET_ROOT/docs" "$TARGET_ROOT/sql" \
  "$TARGET_ROOT/systemd" -type f -exec chmod 0640 {} +

for unit in "$SOURCE_ROOT"/systemd/*.{service,timer}; do
  [[ -f "$unit" ]] || continue
  install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload

echo "platform_target=$TARGET_ROOT"
