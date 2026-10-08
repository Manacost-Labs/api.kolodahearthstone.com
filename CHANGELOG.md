# Changelog

## Unreleased

- Next.js panel design foundation: shared spacing, radius, type and z-index
  scales; separate warning and info colours; a darker light-theme accent so
  accent text passes 4.5:1; a "Как в системе" theme that follows the device.
- One formatting module for the Next.js panel: relative and exact UTC dates,
  Russian numbers and percents, status labels with tones, readable card types,
  tribes, scopes and statistics filters. Tables, record details, statistics,
  sources and tokens no longer print raw ISO dates, status codes or slugs.
- Next.js panel accessibility: deck tiles and catalogue cards announce tier,
  stats and price; the wide table area is a labelled region; icons replace
  text glyphs; empty results offer a reset.
- Inter now ships as a 206 KB woff2 subset instead of an 876 KB TTF, with its
  variable weight range declared.
- `npm run check` for the Next.js panel runs Prettier and Biome first.

- Kept manual Battlegrounds card edits across HearthstoneJSON imports. Edits
  are stored per field in `battlegrounds_card_overrides` with the upstream
  value they replaced, audited with their author, and released automatically
  when a newer game patch changes the field. Requires PostgreSQL migration
  `010_battlegrounds_card_overrides` before the panel deploy.
- Stopped constructed imports from silently removing cards: Blizzard
  pagination is validated, one run may remove at most `max(25, 5%)` of a
  format without `--allow-mass-removal`, and failed runs now keep their error
  row after the rollback.
- Hid legacy `catalog.jobs`, `catalog.options` and `catalog.migration` from
  GraphQL `collections` and `records`.
- Next.js panel: API token usage now reads `usage.request_count` instead of
  always showing 0; the editor no longer offers GIF uploads the backend
  rejects; deck tiles fit the window instead of requiring 1464 px; filled
  buttons keep 4.5:1 text contrast in the dark, tavern and arcane themes.
- Accepted graphql-core 3.3's wording for rejected mutations in the read-only
  GraphQL test.

- Added HSGuru Deck Radar beta: idempotent SQLite history over the published
  streamer-deck snapshot, candidate/confirmed exact-deck events, and the
  public `GET /v1/constructed/deck-radar` endpoint. Established catalog decks,
  matching archetype titles and ≥0.75-similar card variants are suppressed.
- Updated the Trigger.dev control-plane lockfile to patched `fast-uri` and
  `qs` releases after new upstream advisories began failing the CI audit.
- Added fail-closed freshness evidence for the four HSReplay daily meta
  datasets. Their public endpoints now require a verified `as_of` snapshot no
  older than 36 hours and return `503` instead of serving an unverified LKG;
  source diagnostics expose bounded `upstream_freshness` and
  `fresh_only_eligible` fields.
- Made Firestone legendary Arena pool rotations verifiable against the
  rarity-scoped upstream population, while retaining strict regression gates
  for truncated snapshots and every non-legendary Arena dataset.
- Added the authenticated database web panel, its sync jobs, tests, Nginx
  configuration and atomic release deployment to the canonical API repository.
- Added the PostgreSQL data platform, statistics normalizers, migrations and
  deployment contract to the same canonical repository.
- Moved the production panel to a domain-neutral runtime with persistent media
  and cache storage outside Git; the retired domain path is no longer used by
  active Nginx or systemd configuration.
- Excluded Python bytecode and test caches from immutable panel releases.
- Batched Wiki full-art page discovery at MediaWiki's 50-title limit, reducing
  Scrape.do requests for the same catalogue by roughly five times.
- Made Wiki full-art refresh validate existing local files by size and SHA-1
  before discovery, so Scrape.do is used only for missing or corrupt assets.
- Extended the full HSGuru archetype-analysis timeout to two hours so the
  checkpointed Scrape.do refresh can finish all rank/format targets.
- Restricted HSGuru archetype-analysis acquisition to Scrape.do; it no longer
  spends or probes Firecrawl, Bright Data or Scrapfly fallbacks.
- Made checkpoint retries combine independently successful matchup and card
  statistics components instead of requiring both requests to succeed in the
  same attempt.
- Accepted title-less structured API snapshots during last-known-good
  validation, so a temporary HSReplay premium-session failure no longer turns
  a valid Battlegrounds hero dataset into a hard parser error.
- Exposed Battlegrounds hero and detail row counts directly in parser status
  metadata after JSON refreshes.
- Calibrated the Standard Legend 24-hour card contract for its lower sample
  size while retaining field-fill and regression protection.
- Made the unified HSGuru matrix use the verified Scrape.do super-render
  profile before Firecrawl, and exposed logical/base/fresh/cached slice counts
  to parser monitoring.
- Made the four HSGuru deck catalogs refresh independently so one upstream
  failure no longer prevents the remaining format/rank datasets from updating.
- Allowed the unified HSGuru matrix to publish a clearly marked partial
  snapshot when one current-format catalog can be restored from the same
  patch's last-known-good data.
- Made a successful Battlegrounds hero-details JSON refresh update the
  compatible hero index too, removing its dependency on a separate premium
  HTML session for freshness.
