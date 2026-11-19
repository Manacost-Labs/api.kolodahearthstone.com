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

## Card text language (`locale`)

`GET /api/v1/card-catalog`, `/api/v1/card-catalog/{card_id}` and
`/api/v1/card-catalog/dbf/{dbf}` accept `?locale=ruRU|enUS|plPL|esES` (exact
values, case-sensitive). Without it the response is unchanged. With it, every
card (the detail object and each `data[]` item) gets an additive block:

```json
"localized": {
  "locale": "plPL", "requested_locale": "plPL", "fallback": false,
  "name": "...", "text": "...", "flavor": "...",
  "collection_text": null, "how_to_earn": null,
  "how_to_earn_golden": null, "targeting_arrow_text": null
}
```

The texts come from the `payload` of the card's row in
`hearthstone_card_localizations` for that locale (HSJ keys `collectionText`,
`howToEarn`, `howToEarnGolden`, `targetingArrowText` are returned in snake_case;
a field missing in HSJ is `null`). Other fields, including `images.card`
(a ruRU render), do not change.

Fallback: if the card has no row for the locale (or the table is not deployed
yet), the texts are taken from the enUS catalog record, `locale` is `"enUS"`,
`requested_locale` keeps the request and `fallback` is `true`; the status stays
200. An empty, array or unknown value (`esMX`, `pl`, `plpl`) is a 400:
`{"error": {"code": "invalid_parameter", "message": "Unsupported locale; expected one of ruRU, enUS, plPL, esES"}}`.

`q` together with `locale` also matches the card name in that locale (when the
table is missing it matches `card_id`, `name_ru` and `name_en` only).
`Last-Modified` is the later of the catalog row and the locale row `updated_at`,
so a changed translation is never answered with a stale 304. The listing reads
one page with one `LEFT JOIN`, no per-card queries. Mode endpoints
(`/constructed-cards`, BG, ...) do not accept `locale` yet.

### Card render in the requested language (`render_url`)

With `?locale=` the `localized` block also has two fields about the card image
in that locale (`images.card`, the top-level `media_verified` and the response
without `?locale=` do not change). The render locale is always the requested one:
it does not follow the enUS text fallback.

| Case | `render_url` | `render_media_verified` |
| --- | --- | --- |
| Render is downloaded (id is in `renders` of the locale index) | `https://api.kolodahearthstone.com/uploads/card-renders/{locale}/512x/{id}.webp?v={v}` | `true` |
| Not downloaded yet, type is one of `MINION`, `SPELL`, `WEAPON`, `HERO`, `HERO_POWER`, `LOCATION` | `https://art.hearthstonejson.com/v1/render/latest/{locale}/512x/{id}.png` | `false` |
| HSJ answered 404 (id is in `missing`), the type is in `skip_types`, or it is another type without a downloaded render | `null` | `false` |

`v` is the first 12 hex characters of the sha256 of the WebP file, so the URL
changes together with the file and the CDN cache is not stale. The source of
truth is the manifest written by the card-renders loader,
`panel/current/var/card-renders/{locale}.index.json` (`version` 1, `renders`,
`missing`, `skip_types`). It is read once per request; a missing, unreadable or
invalid file (or `version` other than 1) behaves as "nothing downloaded", never
as an error. A `v` that is not 12 lower-case hex is ignored; the id is
URL-encoded. The render files themselves are not checked (after the S3 offload
they are symlinks); the index is trusted.

`Last-Modified` of a `?locale=` response is also not older than the mtime of that
locale's index, and the ETag follows the body, so a newly downloaded render is
never answered with a stale 304.

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

Localized WebP renders of these cards are downloaded by a separate timer, see
[card-renders.md](card-renders.md).
