#!/usr/bin/env bash
set -Eeuo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
artifact="${KOLODAHS_NEXT_ARTIFACT:-$project_root/panel-next/.next/standalone}"
target_root=/srv/api-kolodahearthstone/panel-next
release_id="${KOLODAHS_NEXT_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$EUID" -eq 0 ]] || { echo 'deploy-panel-next: run as root' >&2; exit 1; }
[[ "$release_id" =~ ^[A-Za-z0-9._-]+$ ]] || exit 1
[[ -f "$artifact/server.js" && -d "$artifact/.next/static" && -f "$artifact/public/fonts/inter.ttf" ]] || {
  echo 'deploy-panel-next: build the standalone artifact first' >&2; exit 1;
}
release_root="$target_root/releases/$release_id"
[[ ! -e "$release_root" ]] || { echo 'release already exists' >&2; exit 1; }
previous="$(readlink -f "$target_root/current" || true)"
install -d -o koloda -g koloda -m 0755 "$target_root" "$target_root/releases" "$release_root"
rsync -a --exclude='.env*' "$artifact/" "$release_root/"
chown -R koloda:koloda "$release_root"
chmod -R u=rwX,g=rX,o= "$release_root"
install -m 0644 "$project_root/panel-next/systemd/koloda-panel-next.service" /etc/systemd/system/koloda-panel-next.service
systemd-analyze verify /etc/systemd/system/koloda-panel-next.service
systemctl daemon-reload
ln -sfn "$release_root" "$target_root/current.next"
mv -Tf "$target_root/current.next" "$target_root/current"
systemctl enable koloda-panel-next.service >/dev/null
systemctl restart koloda-panel-next.service
healthy=false
for attempt in {1..15}; do
  status="$(curl --silent --max-time 2 --output /dev/null --write-out '%{http_code}' http://127.0.0.1:4181/ || true)"
  if [[ "$status" == 307 ]]; then healthy=true; break; fi
  sleep 1
done
if [[ "$healthy" != true ]]; then
  if [[ -n "$previous" && -f "$previous/server.js" ]]; then
    ln -sfn "$previous" "$target_root/current.next"
    mv -Tf "$target_root/current.next" "$target_root/current"
    systemctl restart koloda-panel-next.service
  else
    systemctl stop koloda-panel-next.service
  fi
  echo 'deploy-panel-next: health check failed; prior service restored if available' >&2
  exit 1
fi
printf 'next_release=%s\n' "$release_id"
