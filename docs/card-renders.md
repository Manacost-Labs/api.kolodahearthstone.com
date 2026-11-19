# Localized card renders (E24, phase 2)

`panel/scripts/sync_card_renders.py` keeps its own copy of the HearthstoneJSON
card renders for `ruRU`, `enUS`, `plPL`, `esES` as WebP, so the API does not
depend only on a third-party CDN. The loader only writes files and a manifest;
the API field (`render_url`) is api#122 and the coverage report is api#123.

## Source and storage

- Source: `https://art.hearthstonejson.com/v1/render/latest/{locale}/512x/{card_id}.png`
  (free CDN, no paid providers, one thread, `User-Agent: KolodaCardRenders/1.0`).
- Result: WebP, Pillow `quality=85, method=6`, alpha kept, about 50 KB per file,
  about 1.4 GB per locale, 5.7 GB for four.
- Files: `panel-data/uploads/card-renders/{locale}/512x/{card_id}.webp`, served as
  `https://api.kolodahearthstone.com/uploads/card-renders/...`. Each file is written
  as `.tmp-<uuid>.webp`, `chmod 0644`, `fsync`, `os.replace`.
  `api-uploads-offload.timer` moves them to S3 and leaves a symlink in
  `uploads/.s3-storage`; replacing such a symlink is fine.
- Old versions are never deleted.
- **Never `stat`/`exists`/`is_file` rendered files** (loader, PHP, reports): after the
  offload each call is an S3 request. The index is the source of truth.
- Not requested: `ENCHANTMENT`, `PET`, `MOVE_MINION_HOVER_TARGET`,
  `BATTLEGROUND_HERO_BUDDY` and an empty type (`SKIP_TYPES`, also written to the
  index); ids not matching `^[A-Za-z0-9_-]{1,64}$` are counted as
  `skipped_invalid_id`.

## Manifest (contract for api#122 and api#123)

Directory `panel-data/var/card-renders/` (`panel/current/var/card-renders/`), all
files written atomically (`.tmp-` + rename, 0644):

- `{locale}.index.json`:
  `{"version":1,"locale":"plPL","size":"512x","updated_at":"<UTC ISO>","skip_types":[...],"renders":{"<card_id>":"<v>"},"missing":["<card_id>",...]}`.
  `v` is the first 12 hex of the sha256 of the WebP bytes; `missing` are ids that
  HSJ answered with 404 at the last check. The index only lists files already
  written.
- `{locale}.state.json`: loader internals. Per id either
  `{source_hash, v, bytes, fetched_at}` or `{status: 404, checked_at, source_hash}`.
- `last-run.json`: `started_at`, `finished_at`, `requests`, `downloaded`,
  `not_found`, `errors`, `skipped_invalid_id`, `bytes_written`, `avg_webp_bytes`,
  `planned`, `stopped_reason` (`done|budget|upstream_errors|low_disk`), and
  `retry_after_until` after a 429 with `Retry-After`.

## Plan and limits

Input: `hearthstone_card_localizations` (`card_id`, `locale`, `source_hash`) JOIN
`hearthstone_card_catalog` (`collectible`, `card_type`), read-only; `--rows-file`
takes the same rows as JSON. The plan uses only the state files:

| State | Action |
| --- | --- |
| id not in state | download |
| `source_hash` changed | download again |
| 404 recorded | check again only if `source_hash` changed or the last check is at least `--recheck-missing-days` (30) old |
| otherwise | nothing |

Order: collectible cards first, then the rest; by `card_id`, all locales of one card
in a row. Known limit: if HSJ redraws an image without a payload change we do not
notice (no ETag rechecks).

Responses: 200 + PNG 512 px wide becomes WebP; 404 is remembered; 429, 5xx,
timeout (30 s) and invalid images are errors, they are not 404 and are retried
next run. A 429 or 20 errors in a row stop the run with exit code 75
(`upstream_errors`, the unit treats 75 as success); `Retry-After` (up to 24 h) is
honoured by the following runs.

Flags: `--apply` (without it a dry-run plan, no network, no writes), `--rows-file`,
`--rate` (default 3 req/s, allowed 0.5-5), `--max-requests` (6000, about 35 min),
`--min-free-gb` (20), `--recheck-missing-days` (30), `--locales`, `--uploads-dir`,
`--var-dir`. Free space of the `uploads` filesystem is checked before the start and
every 500 files; below the threshold the run stops with exit code 1 and
`low_disk`. Index and state are saved every 500 files and at the end (index
first, so a crash costs at most a repeated download). A non-blocking `flock` on
`var/card-renders/.lock` forbids parallel runs (second run exits 0). The global
`kolodahs-sync/global.lock` and the platform `sync.lock` are not taken.

Expected backfill: collectible cards (about 33k requests) in about 6 h, everything
(about 118k) in about a day, 6000 requests per hourly run.

## Deploy runbook (for the Reviewer)

Only after phase 1 (#95, #116) is on production.

1. Check that #116 is deployed (`hearthstone_card_localizations` filled for 4 locales).
2. Clean worktree on the merge commit. `/srv/hs-data-api` is not on main: do not deploy from it.
3. `sudo diff -rq` of production `panel/` against the worktree (exclusions as in A2.1).
4. `sudo ./scripts/deploy-panel.sh`.
5. `df -h /srv/api-kolodahearthstone/panel-data`: at least 30 GB free.
   `systemctl is-active api-uploads-offload.timer` must be `active`.
6. `sudo install -o root -g root -m 0644 panel/systemd/kolodahs-card-renders.{service,timer} /etc/systemd/system/`,
   `sudo systemctl daemon-reload`, `systemd-analyze verify /etc/systemd/system/kolodahs-card-renders.{service,timer}`.
7. Test run as `koloda`:
   `sudo -u koloda bash -c 'cd /srv/api-kolodahearthstone/panel/current && /opt/wiki-hs-parser/.venv/bin/python scripts/sync_card_renders.py --apply --max-requests 40'`.
   Expect 40 files in `uploads/card-renders/`, symlinks into `.s3-storage` within
   5 minutes, HTTP 200 `image/webp` from
   `https://api.kolodahearthstone.com/uploads/card-renders/plPL/512x/<id>.webp`,
   `avg_webp_bytes` in `last-run.json` between 35 000 and 65 000.
8. `sudo systemctl enable --now kolodahs-card-renders.timer`.
9. After a day check `last-run.json` and the number of keys in the four indexes.

## Rollback

`sudo systemctl disable --now kolodahs-card-renders.timer`. Files and manifest stay;
until api#122 is deployed nobody reads them, afterwards the API keeps serving what
was downloaded and falls back to HSJ links for the rest.
