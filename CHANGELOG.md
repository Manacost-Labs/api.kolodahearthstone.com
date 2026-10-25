# Changelog

## Unreleased

- HSGuru coverage: Tavern Brawl and esports. `hsguru_brawl` reads the current
  Brawl meta (7 ranks × 3 coin options and 11 opponent classes, from 10
  games) and Brawl decks every three hours. `hsguru_esports_tournaments`
  collects /esports upcoming tournaments and every official tour stop
  (2019–2026) plus recently started community tournaments with stage
  standings, player lineups (deck codes) and lineup archetype popularity;
  finished tournaments are reused from the previous snapshot.
  `hsguru_esports_points` holds HSEsports points of every season for
  Standard and Battlegrounds; `hsguru_esports_legacy` the Masters Tour
  archive (weekly). Plain Scrape.do pages, at most 5 at once, as `detail`
  jobs of the shared HSGuru budget. REST `/v1/hsguru/brawl/*`,
  `/v1/hsguru/esports/*` and GraphQL `hsguruBrawl*`/`hsguruEsports*`. The
  HSGuru coverage audit is in `docs/DATA_CATALOG.md`.


- HSGuru mulligan no longer grows to gigabytes during a run (5.2 GiB RSS in
  production). The parse cache keyed by page HTML kept about 130 pages
  (~230 MB) alive; the parsed result now travels with the page from
  validation to storage and the HTML is released right after parsing. The
  rest was glibc arena fragmentation from 40 Scrape.do threads: the unit runs
  the container with `MALLOC_ARENA_MAX=2` and a run calls `malloc_trim(0)`
  every 200 pages. Local benchmark with 3,000 distinct pages: peak RSS
  1,262 → 465 MB, flat at 320–340 MB, throughput unchanged.
- Balance Radar: the company Blizzard is never renamed after the Mage spell
  «Снежная буря» (brand words stay out of the name pass; «Снежная буря» used
  as the actor of a statement becomes Blizzard again) (#63).
- HSGuru mulligan: class matchup pages that map two labels to one class keep
  the larger sample, and a failed write of one page marks only that target
  failed instead of ending the run (#62).

- HSGuru deck catalogs and archetype analysis ask Scrape.do for a plain page
  first (1 credit instead of 5): deck, archetype and card-stats pages are
  LiveView dead renders, and on 2026-10-25 plain and rendered pages gave the
  same builds, games, winrates, matchups and card rows. A rejected plain page
  gets one rendered retry; replay pages, whose table loads after the LiveView
  connects, get one rendered request. Super proxies, Decodo and the paid
  remote reserves are no longer used for these pages; a page that every route
  failed is retried after 30 and 120 s instead of climbing the paid ladder.
  Provider usage keeps recording `render` and the cost of every call.
- HSGuru replays are asked once per window (the catalog window, plus the
  whole release for Bans) and their builds and empty lookups are reused for
  three days, instead of asking every fallback window every night.
- Every HSGuru archetype catalog row without a build of its own window gets
  builds from wider windows (Bans: the whole release, then the rolling month)
  or real deck codes from its public replays, and says where they come from
  (`decks_period`, `decks_source`, `decks_fallback`).

- Balance Radar: accurate, short, Russian-only texts. Archetypes use
  HearthPulse's own labels (the rest translated once by Claude and cached);
  cards use the ruRU localization. Every reader text is cleaned
  deterministically (English names, English brackets, rank slang, pipeline
  field names such as `history.patterns`). Mentions are marked up by `ref`
  (`textEntities`, `summaryEntities`). Every shown Reddit/X post and quote
  gets a full Russian translation (`titleRu`, `textRu`, cached). An optional
  Editor Team pass (`RADAR_EDITOR_URL`, docker network `editorteam-clients`)
  takes an edit only when it is accepted with all checks passed. Only real
  candidates are listed now (no quota). The backtest adds Brier score and a
  reliability table, and runs the Claude predictor automatically after a new
  patch; the result reaches every payload as `accuracy`.
- HSGuru mulligan parses a page with lxml into one tree, once: the provider
  cascade validates it twice and the run stores it, and all three now share
  one memoized result. BeautifulSoup built six trees per page (~140 ms of
  CPU), so the run was bound to one core at about 20 pages a minute in
  production. Measured on the real page path with the network mocked at 6 s:
  122 → 369 pages a minute at concurrency 40 (the network limit is 400),
  peak RSS 606 → 367 MB; output is identical on eight real HSGuru pages.

- Balance Radar v2 (schemaVersion 2, additive): 15–25 nerf and 15–25 buff
  candidates per format (cards, archetypes, classes) from wider pools (HSGuru
  all/legend/top_legend/diamond_4to1 in the Bans window, HSReplay Legend,
  Diamond 4-1 and Diamond). Confidence is anchored on a calibration of past
  patches (how often an archetype with this winrate/popularity was changed:
  389 archetype-weeks over 9 patches) and the odds of a patch within 7/14
  days. New inputs: HSGuru mulligan impacts, HSReplay core archetypes of each
  card, developer statements (Blizzard flair, official accounts). New fields:
  predicted changes with probabilities, meta impact, verified community
  quotes, key numbers, calibration, trend, confidence history, timing,
  changes since the previous run, counts. New `/backtest` (statistical
  nerf precision@5 0.28 / recall@10 0.59; Claude 0.33 / 0.77) and
  `/confidence-history` endpoints. About $1.14 per run for both formats.

- HSGuru deck catalogs fetch through Scrape.do first, 15 pages in parallel
  (`HS_HSGURU_DECK_CATALOG_CONCURRENCY`; the local FlareSolverr is the
  fallback, or goes first with `HS_HSGURU_DECK_CATALOG_LOCAL_FIRST=1`), with a
  90 s page timeout instead of 25 s. Every page asks for HSGuru's 50-game deck
  floor, so one pass verifies every archetype and the 200-target retry cap is
  gone; catalogs written under older rules are re-read in full. A live run
  took 28 minutes and 2,700 Scrape.do credits: Black Empire builds grew from
  458 to 2,634 (Standard) and 772 to 1,689 (Wild), and the Bans catalogs went
  from none to 1,826 and 840 builds. HSGuru lists no build below 50 games,
  also when logged in, so 88 of 243 Standard archetypes (0.3% of games) have
  none.
- One HSGuru deck-catalog run at a time: a flock makes a second run exit
  DEGRADED without writing, and the unit removes its named container when it
  stops. An orphaned container of the previous release had overwritten the
  Wild catalog of the next run. Death Knight builds that HSGuru names with
  their rune spread ("BUU Egg DK") now join their archetype ("Egg DK").

- HSGuru mulligan asks Scrape.do for a plain page first: HSGuru serves
  card-stats and archetype pages as LiveView dead renders, so a request
  without a browser has the full tables (8 s and 1 credit instead of 54 s
  and 5 credits). A rejected page still climbs the render and residential
  profiles; other HSGuru jobs keep their ladder. Concurrency defaults to 40
  and the timer fires hourly, so the whole plan loads in under an hour and
  costs about 8,800 credits; keeping it fresh about 1,300 a day.

- HSGuru mulligan reads pages through Scrape.do first, 25 in parallel
  (`HS_HSGURU_MULLIGAN_CONCURRENCY`, up to 50), so the shared server's
  FlareSolverr is only the fallback (`HS_HSGURU_MULLIGAN_TRANSPORT=scrape_do_only`
  turns it off, `flaresolverr` restores the old order). Blocking provider calls
  run in a dedicated thread pool instead of the default executor the API
  shares. Credit caps are off by default and stay available as safety limits.
  One run reads up to 10,000 pages in 150 minutes, so the whole plan
  (8,720 pages, about 51,000 credits) loads in about two hours instead of five
  days; keeping it fresh costs about 7,600 credits a day. Cumulative windows
  are re-read on 50 % growth at most every two days (was 30 % and one day) to
  keep re-reads of young windows affordable on the paid route.

- Balance Radar (`/v1/constructed/balance-radar`): nerf and buff candidates for
  Standard and Wild. A timer twice a day joins HSGuru archetypes by period
  (including the Bans window) and HSReplay card periods with Reddit
  (RedditAPIs) and X (GetXAPI) posts, and asks `claude-sonnet-5-5` for a
  structured verdict in Russian. Numbers and post links come from the data,
  never from the model. Runs and their cost are stored; the API keeps serving
  the last good run. About $0.86 per run for both formats.
- Balance Radar learns from Blizzard's past decisions: a history dataset of every
  card change since December 2023 (exact HearthstoneJSON build diffs, patch
  dates and Russian developer notes from `/api/patches`, HSReplay archetype
  winrate/popularity a week before and after since April 2026), computed
  patterns (thresholds, change types, timing after releases), a Claude-written
  overview, and up to three similar past cases per candidate (`precedents`).
  New endpoints `/v1/constructed/balance-radar/history` and `/patterns`.
- HSGuru coverage: matrix slices are requested with `min_games=10` instead of
  100 (same request count; Standard Bans ALL grows from 141 to about 226
  archetypes, Wild from 216 to about 628), add the Bronze–Platinum rank, and
  split On Coin / Going First for every rank instead of six. A run grows from
  423 to 603 Scrape.do pages (about 2,420 to 3,450 credits); its deadline grows
  to 150 minutes and the unit timeout to 160 minutes. `/v1/hsguru/meta` accepts
  `min_games` 10/25/50 and keeps its default of 100. The current archetype
  catalog lists archetypes from 10 games, and a Bans catalog
  (`/v1/hsguru/archetypes?period=black_empire_ban`) is built from the matrix
  slices with builds from its own deck catalogs. Deck catalogs ask for HSGuru's
  full 200-build page per archetype, re-read covered archetypes daily instead
  of keeping week-old builds, skip archetypes below HSGuru's 50-game deck
  floor, and the deck job's timeout grows to four hours. The paid archetype
  analysis keeps its 100-game Legend/past-week target set.
- New pipeline `hsguru_mulligan` collects the HSGuru mulligan guide
  (mulligan, drawn, not drawn and kept impact with counts per card) and class
  matchups for every archetype and catalog deck with enough games: six ranks ×
  eight periods including the Bans window, the coin split, opponent class for
  the most played archetypes, 8,289 pages on 25 October (about 1,200 a day by
  TTL). Before, only Legend past_week, without coin or decks, was collected.
  An incremental queue re-reads a page when its period TTL expires or a
  cumulative window grows by 30 %; it runs every three hours (200 pages, one
  hour) through the free local FlareSolverr. Scrape.do is a fallback capped at 300 credits per run and
  1,000 per UTC day inside the Meta-Lab HSGuru budget. SQLite `hsguru_*` is
  mirrored to PostgreSQL `analytics.hsguru_*` (migration 017). New REST
  `/v1/hsguru/mulligan`, `/class-matchups`, `/mulligan/slices`,
  `/mulligan/history`, `/mulligan/coverage` and GraphQL `hsguruMulligan`,
  `hsguruClassMatchups`. See docs/HSGURU_MULLIGAN.md.

- A HSGuru deck page whose cards all rendered but hold no usable deck (one
  "Highlander Warrior" card with a one-card code) now means "no builds" instead
  of failing the whole Wild all-rank catalog. Only cards without copy text count
  as an unrendered page.

- HSGuru meta matrix adds the "Bans" window (`period=black_empire_ban`) and
  the coin split back (`coin=on_coin` / `going_first`, HSGuru
  `player_has_coin=yes|no`) for the six ranks HearthPulse serves (ALL,
  Diamond, Diamond–Legend, Legend, Top 5K, Top Legend). A twelve-hour run grows
  from 164 to 422 Scrape.do pages (about 2,100 credits) and runs with
  concurrency 5. Coin and Bans slices may be empty except ALL over a full
  window.
- HSGuru all-rank deck catalogs no longer stall: a local-browser page whose deck
  cards cannot be read is rendered again through Scrape.do instead of failing
  the whole catalog (the Wild catalog had not refreshed since 21 October). A
  shared 20-archetype page that reaches its limit asks for each archetype on its
  own (10-game builds, up to 30 each), and the nightly exact retries cover up to
  200 build-less archetypes. The nightly job may run for up to two hours.

- Full HSReplay Battlegrounds minion statistics for every minion: new pipeline
  source `hsreplay_battlegrounds_minion_stats` reads the four analytics queries
  behind the HSReplay minion page (`battlegrounds_minion_list`, `_minion_position`,
  `_purchase_rates_by_turn`, `_minion_stats`) for six slices (TOP_50, TOP_20 and
  ALL × last 7 days and current patch; positions for TOP_50) once a
  day, validates each slice and keeps its previous snapshot on any failure. History
  is stored in SQLite `bg_minion_stat_*` and mirrored to PostgreSQL
  `analytics.bg_minion_stat_*` (migration 016). Header popularity and board
  positions now use HSReplay's own formulas, so 104551 shows 1.9 % / 0.2 %
  popularity and the site's position split instead of our earlier approximation.
  New REST `/v1/battlegrounds/minions/stats`, `/stats/slices`,
  `/{minion}/stats`, `/{minion}/stats/history` and GraphQL
  `battlegroundMinionStats`.
- HSGuru Scrape.do requests follow a measured ladder: datacenter render
  (5 credits) waiting for the LiveView root instead of a fixed delay, then the
  anonymous 10-credit residential profile with Scrape.do's free retries left
  on, then rendered residential (25). The 1-credit plain profile never passed
  HSGuru's Cloudflare (0/40) and is dropped. Cloudflare cookies are no longer
  forwarded and non-browser requests no longer carry the HSGuru session: both
  made Cloudflare block the residential profile, so pages that render missed
  went straight to 25 credits. Premium-filter paywall pages are rejected and
  escalate to the logged-in browser profile. A sticky Scrape.do session per
  worker cut median render time from 18.5 to 9.4 s in a 16-page benchmark.
- HSGuru meta matrix follows HSGuru's new period menu: seven rolling windows
  (`past_3_hours`, `past_6_hours`, `past_day`, `past_3_days`, `past_week`,
  `past_2_weeks`, `past_30_days`), the current patch and the «Власть Темной
  империи» expansion window `black_empire`, which is now the current-catalog
  period. The retired `violet_hold` and `most_wanted` windows are no longer
  scraped. 3/6-hour slices may be empty for every rank except ALL. The matrix
  grows from 126 to 162 Scrape.do pages per twelve-hour run.
- Constructed sync renames tokens and hero powers that were stored before release
  as `blizzard:<dbf>` to their HearthstoneJSON ID once it is published. Before,
  related-card sync kept updating the fallback row, so new-set tokens had no
  Russian names or local images in `related_cards_localized` (66 «Власть
  Темной империи» tokens on 2026-10-20).
- Public `/v1/*` responses no longer wait about a second for their ETag: the
  cache revision memoizes each dataset's `fetched_at` by file signature
  instead of parsing every published dataset (~260 MB) on each request inside
  the async middleware. `/v1/hsguru/meta` dropped from 1.1–2.8 s to tens of
  milliseconds; ETag values are unchanged. One revision now reads the
  parser-control state once instead of once per early source.
- `/v1/health` reuses its freshest public `fetched_at` for the same 15 s as
  its diagnostics: each monitoring probe resolved every public dataset (~6 s
  of CPU), which kept the API process near one full core.
- Next.js panel catalogue: artwork tiles with a loading shimmer, tier and pool
  badges and a golden marker; labelled filters, category pills, active-filter
  chips with a reset, and a filter sheet on phones.
- Next.js panel card inspector: opening a record writes `?card=<id>` to the
  URL, so it survives reloads, can be shared and closes with Back; links to
  cards beyond the current page resolve on the server. It shows curated text,
  mechanics, stats, badges, copyable IDs and the golden version; arrows flip
  through the page.
- Next.js panel tables use columns per category (heroes, skins, pets, coins,
  timewarped, Standard/Wild, libraries) with a pinned thumbnail and name.
- Ctrl+K in the Next.js panel searches cards of the current category as well
  as sections, with thumbnails and keyboard selection.

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
