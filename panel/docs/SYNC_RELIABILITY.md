# Kolodahs sync reliability

All scheduled sync jobs share one global lock because they update common JSON
and image indexes. `scripts/run_sync_job.sh` waits up to six hours for that lock
instead of silently dropping a timer event. Override the bound with
`KOLODAHS_SYNC_GLOBAL_LOCK_WAIT_SECONDS` in `/etc/kolodahs-sync.env`.

If the wait expires, the job records `lock_timeout`, exits with temporary-error
code `75`, and remains visible as failed in systemd. The rolling constructed
Wiki refresh has an instance override that retries a failed run after 45
minutes. Each retry is a new process, so it does not hold the global lock while
waiting. The parser's oldest-first order makes completed pages recent and lets
the next run continue with older pages after a wiki.gg rate limit.

## Battlegrounds framed portraits

### Base and golden variants

Normal and golden/tripled cards remain separate physical rows because Blizzard
can give the golden form different stats and rules text. They are not separate
catalogue entities. `scan_cards.php` links them with HearthstoneJSON's official
`battlegroundsNormalDbfId` and `battlegroundsPremiumDbfId` fields and stores the
relationship in `base_dbf`, `base_card_id`, and `premium_dbf`.

The admin catalogue and `GET /api/v1/cards` show base cards by default. The API
adds the complete golden form under `golden_variant`, including its own card ID,
DBF, stats, text, and local images. Raw maintenance clients can request every
physical row with `include_variants=1`; direct lookup by a golden `card_id` or
DBF remains supported.

Every `cards` sync runs `audit_battlegrounds_variants.py`. It rejects duplicate
card IDs/DBFs, broken reciprocal links, missing linked bases, and multiple gold
variants for one base. A small set of historical Hearthstone fixtures whose
counterpart is absent from the current Battlegrounds dataset is reported as a
warning, not silently treated as a live duplicate.

`scan_cards.php` materializes three distinct assets for every card:
the compact `256x388` render, square `512x512` art, and a transparent
`300x350` portrait in the Hearthstone frame. A regular full-card render must
never be written to `framed_image` as a fallback. If portrait generation
fails, the field remains empty so monitoring and the admin table report the
missing derivative honestly. New minions, tokens and archived cards are
backfilled during the regular `cards` sync.

Golden Battlegrounds records whose IDs end in `_G` or the token form `_Gt`
use HearthstoneJSON's
`<card_id>_triple.png` source. The plain `<card_id>.png` path does not exist;
the sync must still materialize the result locally under
`/uploads/cards/<card_id>.png`.

Framed portraits use an aspect-preserving `1.0` cover crop anchored at the
upper quarter. A recipe refresh always fetches the canonical original artwork;
it must never use the previously saved `512x512` derivative as its source.
Older revisions resized border-trimmed portrait art to an exact square, which
widened characters and made repeated runs non-idempotent. The sync repairs
those legacy square derivatives when the upstream artwork is demonstrably
portrait, while retaining genuine square and high-resolution Wiki art.

Run `php scripts/scan_cards.php --self-test-framing=1` before deployment. It
checks representative square and portrait inputs, rejects non-uniform scaling,
and prevents accidental zoom from returning. The recipe marker in
`uploads/framed/` automatically refreshes older derivatives when this
algorithm changes. Frames are written to a temporary file and atomically
renamed, so a visitor never receives a partially rendered PNG during a full
archive refresh.

Verify after a Battlegrounds patch:

```bash
identify -format '%wx%h' uploads/framed/BG36_921.png
curl -fsS https://api.kolodahearthstone.com/api/v1/cards/BG36_921 \
  | jq -r '.data.images.framed'
```

The expected geometry is `300x350`, and the API URL must point to
`/uploads/framed/` rather than the normal HearthstoneJSON card render.
`run_sync_job.sh cards` audits the compact render and portrait across the full
archive after every image refresh and marks the scheduled job as failed when
either local asset is missing or has the wrong geometry.
Five non-user-facing Blizzard `SKIN`/`Test` fixtures currently have no render
in either upstream image repository. A full-archive audit reports them as
`skipped_technical`; every real card remains mandatory.

The Nginx `/uploads/` location disables inherited negative `open_file_cache`.
Missing files return `Cache-Control: no-store`, while successful immutable
assets keep the seven-day cache policy. This prevents a request made during
generation from turning into a long-lived CDN 404 after the file appears.

Verify after installation:

```bash
tests/test_sync_locking.sh
sudo systemctl daemon-reload
sudo systemctl cat kolodahs-sync@constructed-wiki-refresh.service
sudo systemctl start kolodahs-sync@constructed-wiki-refresh.service
sudo systemctl show kolodahs-sync@constructed-wiki-refresh.service \
  -p Result -p ExecMainStatus -p NRestarts
```
