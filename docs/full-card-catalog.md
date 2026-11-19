# Complete Hearthstone card catalog

`GET /api/v1/card-catalog` exposes every identity from the full Russian and
English HearthstoneJSON `cards.json` snapshots, including non-collectible tokens,
enchantments and cards from other modes. Pagination uses `page` and `per_page`
(1–200). Filters: `q`, `collectible=0|1`, `set`, `card_type`.

`GET /api/v1/card-catalog/BE_222t` and
`GET /api/v1/card-catalog/dbf/130406` resolve the same non-collectible card.
The existing individual `/api/v1/constructed-cards/{card_id}` and `/dbf/{dbf}`
routes fall back to this catalog when their primary dataset has no match (the
legacy numeric-ID path is `/api/v1/constructed-cards/by-dbf/{dbf}`).
Existing Standard/Wild lists and their format membership remain unchanged.

Catalog responses include localized names/text, common card fields and the
original `hearthstonejson.ruRU` / `enUS` records. `formats: []` and
`catalog_only: true` explicitly avoid claiming current format eligibility.
HSJSON render URLs are provider fallbacks, not downloaded or verified media;
`media_verified: false` records this distinction. Effects without a renderable
card type have no image URL. Existing curated card/media records take precedence
on the legacy individual routes.

## Publication and rollback

The catalog is stored in two tables written by one loader in one transaction:

- `hearthstone_card_catalog` (`panel/scripts/full_card_catalog.sql`): ruRU/enUS
  columns, unchanged.
- `hearthstone_card_localizations` (`panel/sql/card_localizations.sql`): one row
  per `(card_id, locale)` for `ruRU`, `enUS`, `plPL`, `esES`; `dbf`, `name`,
  `payload` (the full HSJ object of that locale in canonical JSON, so ruRU/enUS
  equal `payload_ru`/`payload_en` byte for byte), `source_hash = sha256(payload)`
  and `updated_at`, which changes only when the hash changes. Keys:
  `PRIMARY KEY (card_id, locale)`, `UNIQUE (dbf, locale)`,
  `KEY (locale, name)`. The locale list is an allow-list in the loader (no
  `CHECK`/`ENUM`), so esMX needs no `ALTER`. The history triggers `cv1_*` and
  `catalog_card_versions` do not touch these tables. The seven translatable HSJ
  fields are `name`, `text`, `flavor`, `collectionText`, `howToEarn`,
  `howToEarnGolden`, `targetingArrowText`; empty ones are normal.

`panel/scripts/sync_full_card_catalog.py` downloads the four
`https://api.hearthstonejson.com/v1/latest/{locale}/cards.json` files (or takes
`--hsj-ru/--hsj-en/--hsj-pl/--hsj-es`) and is a read-only plan by default. The
JSON output has the catalog counters plus
`"localizations": {"ruRU": {"insert", "update", "unchanged"}, ...}`. Run it
under the panel's global sync lock. No premium providers are called.

Publication gate. Every locale must be a plain list of 10 000–100 000 cards
under 32 MiB with valid, unique ids and dbfs, and all four locales must have
exactly the same `id -> dbfId` set as enUS. Otherwise the loader stops before
writing anything; a failed batch rolls back the catalog and all locales
together. A locale with noticeably fewer ids than enUS is therefore rejected,
not published. Rows for ids that vanish from HSJ are kept. Only new or changed
locale rows are written: repeating an unchanged snapshot writes nothing
(`unchanged` everywhere) and keeps every `updated_at`.

`--apply --expect-total COUNT` is the initial publication. `--apply --refresh`
(the daily `kolodahs-full-card-catalog.timer`, flags unchanged) additionally
needs a non-empty `hearthstone_card_localizations` with at least 10 000 rows per
locale and at least 95% of each locale's published ids present in the new
snapshot; otherwise it fails and the last good data stays. If the table does not
exist the loader reports it and creates nothing.

### Deploy order (production, risk:high)

1. Merge only after the panel-scripts sync (api#94) is deployed.
2. `sudo ./scripts/deploy-server.sh` installs `platform/sql/021_card_localizations.sql`.
   `platform/scripts/sync-shadow.php` loads every `generator` table and fails
   when a table has no mirror, so check that
   `catalog.hearthstone_card_localizations` exists **before** creating the
   MariaDB table.
3. Back up: `mysqldump --single-transaction generator hearthstone_card_catalog |
   gzip` into `backups/e24-locales-<UTC>/`, and restore it into a throwaway
   MariaDB to verify.
4. `sudo mysql generator < panel/sql/card_localizations.sql` from the merge commit.
5. `sudo ./scripts/deploy-panel.sh`.
6. As `koloda` from `panel/current`, under `flock -w 300
   /run/lock/kolodahs-sync/global.lock`: dry-run, then `--apply --expect-total N`,
   then dry-run again (all `unchanged`).
7. Wait for the next `hs-data-platform-shadow-sync.service` (`Result=success`),
   watch the logs for 10 minutes and check the next
   `kolodahs-full-card-catalog.service` run against `TimeoutStartSec=10min`.

Check: `SELECT locale, COUNT(*), SUM(name IS NULL OR name='') FROM
hearthstone_card_localizations GROUP BY locale` gives four rows with N ids and 0
empty names.

Rollback: revert the PR and run `deploy-panel.sh` again; the old loader never
reads the new table. The MariaDB and PostgreSQL tables may stay (they are
harmless); nothing is dropped without the owner. The catalog table is untouched,
so no restore is needed for the catalog itself. The earlier rule for the
catalog table still holds: a failed batch rolls back, and the panel `current`
symlink can be returned atomically to the previous release.
