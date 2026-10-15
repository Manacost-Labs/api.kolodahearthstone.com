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

Prepare `panel/scripts/full_card_catalog.sql` separately from the data transaction.
Run `panel/scripts/sync_full_card_catalog.py --hsj-ru RU.json --hsj-en EN.json`
for a read-only plan, then repeat with `--apply --expect-total COUNT` using the
same files and the existing panel global synchronization lock. The default
network source is the free HSJSON catalog; no premium providers are called.

Before production, back up the catalog table and verify its restoration in a
disposable database. Initial installation creates a separate table; it never
updates existing cards, format associations, images, or wiki metadata. Publish
in batches of 200 within one transaction; a failed batch rolls back everything.
Reject incomplete locale snapshots, duplicate IDs/dbfs and conflicting published
identities. Repeating identical snapshots keeps data and timestamps unchanged.
Historical entries are retained if later upstream snapshots omit them.

Deploy the new library and the small API dispatcher/fallback diff only after the
data is ready. Preserve any existing runtime changes when constructing the
release. Roll back by atomically returning the panel `current` symlink to its
previous release; the separate catalog table can remain for recovery.

The supplied `kolodahs-full-card-catalog.timer` runs a daily free-source refresh
under the same global sync lock. Its `--apply --refresh` mode requires an already
published catalog and at least 95% identity coverage; a truncated upstream
snapshot leaves the last good data available. Install the service/timer after
the initial exact-count publication. Failures are visible in systemd/journald.
