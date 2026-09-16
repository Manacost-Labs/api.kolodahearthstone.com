# Meta Intelligence Lab — MVP

Private research tools inside the existing Hearthstone Data API. The user selects
Standard/Wild, rank, period and patch, compares exact deck compositions, reviews
archetype-level card metrics and class matchups, and inspects immutable history.
No WordPress changes, public recommendations, notifications or automatic publishing.

## Entry points

- Panel: `/ui#meta-lab`, integrated into the existing API panel and source sidebar.
  The legacy `/ui/meta-intelligence` URL redirects to this view. Its public HTML
  shell contains no research data.
- Research API: `/v1/meta-intelligence/*`, protected by the existing `require_admin`
  dependency and `X-API-Key`. Missing server key fails closed.
- API responses, including auth errors, are `private, no-store`, excluded from the
  existing public cache/ETag middleware.
- The panel keeps the key in memory only, clears rendered data on logout, uses
  same-origin requests and renders upstream text with HTML escaping. No third-party assets.
- Disabled by default with `HS_META_LAB_ENABLED=0`.

## Implementation boundary

Canonical HSGuru parsers remain in `hsguru_meta_matrix.py`,
`hsguru_archetype_analysis.py`, and `parser.py`/`structured.py`. The Lab normalizes
their published output; it does not implement another HTML parser for their tables.
The only new HTML navigation logic is bounded, same-origin deck-list pagination.

Existing JSON publication feeds and schedules stay intact. New collectors publish
under `hsguru_lab_*` source IDs. The optional `save_status` hook ingests existing
HSGuru publications; errors in that secondary store do not break the main feed.
The legacy matrix found in this source tree uses rank=all and a patch period. It is
not assumed to cover every rank/period described by earlier requirements.

## Data contract

SQLite at `${HS_API_DATA_DIR}/meta-intelligence/lab.sqlite3`, schema version 1,
WAL, transactional writes, 15-second busy timeout, mode 0600. No new DB service.
GET endpoints open read-only connections and never migrate, collect or recompute.

| Table | Meaning |
|---|---|
| `attempts` | Every instrumented Scrape.do attempt, including failures and retries; run/source/page/slice, timestamps, duration, charge basis, reservation, outcome and validation |
| `observations` | Admission results; failures break confirmation sequences and mark previous good data cached |
| `snapshots` | Append-only normalized published facts, source/slice/patch/time/hash/version, field coverage and acquisition metadata |
| `snapshot_attempts` | References to validated acquisition attempts without duplicating ledger spend |
| `signals` | Current deterministic state per source/slice/patch/archetype/rule |
| `signal_events` | State and evidence history; earlier conclusions are retained |
| `jobs` | Persisted scheduled-slot deduplication and collector outcomes |
| `lab_meta` | Tracking start, explicit opening balances and optional synthetic-demo marker |

Snapshot identity includes source + format + rank + period + patch + fetched_at +
canonical payload hash. Re-importing one publication is idempotent. A new acquisition
with the same contents remains a separate observation. UPDATE/DELETE of snapshots
are blocked by SQLite triggers. Valid old snapshots can enter history; they cannot
serve as fresh evidence. No old LKG timestamp is replaced with ingestion time.

Deck identities use normalized main-deck cards, counts, heroes and sideboards,
including each sideboard's owner. A changed composition is a different variant.
Incomplete compositions remain identifiable but cannot support a reliable comparison.
Card names use the installed local Russian card index; missing names show IDs and
never trigger an external request from the API.

## Freshness and completeness

Key: source + format + rank + period. Each response identifies its patch as well.
Fresh requires age within its interval, admitted validation/completeness, sufficient
coverage, and no newer hard-failure/partial observation. LKG after an error is cached;
an expired interval is stale. Reasons are returned separately for each slice.

Coverage means coverage of required fields in the returned rows, not a claim to
cover every deck or game played on ladder. Existing source validators supply minimum
row checks; Lab adds required numeric metrics, unique archetypes, card identities,
card counts, matchup counts and exact deck compositions for detail publications.
Mixed retained/updated legacy analysis entries are conservatively rejected.

Comparisons require the same format, rank, period and patch. Different providers'
game counts are not added: samples can overlap. Card impact remains source-reported
archetype-level impact, never relabeled as a per-deck or causal effect.

An acquisition's patch label does not prove that a rolling weekly sample contains
only that patch. `patch_scope=single_patch` requires a patch-specific source period
or a verified `HS_META_LAB_PATCH_STARTED_AT` at least seven days before collection,
matching the configured patch. Otherwise coverage is `unverified`, the panel labels
the limitation, and the signal engine cannot confirm that evidence. Updating the
verified patch and its start time is part of release operations.

## Collection and budget

- Existing matrix: unchanged. Existing streamer timer: unchanged.
- Hot: six meta slices (Legend, Diamond 4–1, Top Legend × two formats) and two
  Legend matchup slices every four hours; explicitly `past_week` in MVP.
- Daily details: up to 40 priority archetypes, balancing Standard/Wild opportunities,
  selected from fresh Legend/past_week data. Cards + class matchups + deck listings.
- Strong early/confirmed Legend popularity growth queues event-driven details after
  hot collection: at most four jobs per run, deduplicated by evidence, with a 24-hour
  detail cooldown. Patch-obsolete jobs are retired. Daily collection skips details
  already refreshed by an event. This queue cannot bypass the detail budget gate.
- Deck pages: follow explicit next links, at most `HS_META_LAB_DECK_PAGES` (default 5).
  Coverage distinguishes the observed listing from a page-budget cutoff. Never
  promises all variants on ladder. Revisits listing statistics even for known decks;
  deckstrings already encode composition, so no per-deck detail calls are needed.
- Collectors require an explicitly verified `HS_META_LAB_PATCH` and retain the fixed
  Scrape.do → Firecrawl → Scrapfly order. Budget denial cannot fall through to another
  provider. A process lock and durable slot IDs prevent duplicate scheduled runs.
- Interrupted/failed slots are retained for diagnosis; automatic same-slot retries
  are intentionally bounded by skipping the already-attempted slot.

| Exposure | State | Policy |
|---|---|---|
| <560,000 | normal | Hot and details allowed |
| >=560,000 | soft_alert | Visible in panel, no user notifications |
| >=620,000 | deep_analysis_throttled | Details/confirmation disabled |
| >=700,000 | hard_limit_reached | New non-critical requests disabled |

Critical base jobs continue, so 700,000 is not an absolute subscription-spend cap.
Reservations are taken transactionally before each attempt (30 credits by default).
Actual response headers replace reservations; absent headers are explicitly estimated,
transport errors retain unknown-cost reservations. A provider charge above the reserve
can exceed a threshold; the next non-critical request is blocked. Keys, request URLs,
authorization headers and provider error bodies are not stored in the ledger.

Existing usage before deployment is not fabricated. Record a verified opening balance
once for the current month before allowing non-critical collection. Critical calls
are still accounted while that figure is unavailable. Monthly totals include the opening
balance; provider/page breakdowns cover instrumented calls. Forecast is unavailable
until at least 24 hours of observed time. Missing denominators return null.

## Analysis and signal lifecycle

Deck views show sample size and approximate descriptive Wilson intervals computed
from reported rounded winrates. They do not automatically declare a statistically
superior variant or a causal card replacement. Global card-power scores are absent.

Rules are versioned `lab-v1`. Confidence is a rule score, not a calibrated probability.
Popularity rise/drop requires absolute and relative change; confirmed requires >=3
valid observations, >=8 hours span, no missed interval, adequate samples, known patch,
fresh latest evidence and consistent direction. Defaults: >=500 games, >=1 percentage
point and >=25% relative change. All thresholds are configurable and tested.

Winrate/matchup shifts, new deck variants and streamer bursts are implemented as
observing/early evidence. They cannot become confirmed merely by repeating overlapping
rolling-window data. Matchup confirmation needs independent pair samples. Streamer
burst needs >=3 distinct author references; repeated posts from one author do not count.
New-variant detection means a changed composition, not evidence of greater strength.

Lifecycle: observing → early → confirmed when the rule supports it; failed/retracted
conditions → rejected, with state history preserved. GET also downgrades the displayed
state of stale evidence even if the scheduled signal recomputation has stopped.
No source failure, invalid/partial publication or stale evidence may create a confirmed
signal. Historical snapshots themselves remain available for research.

`social.py` defines separate SocialEvidence/SocialAdapter and draft-writer interfaces.
There are no X, Reddit or LLM calls, keys or background integrations. AI cannot collect,
raise confidence, or publish through the draft-writer interface.

## API

All paths below use `/v1/meta-intelligence`:

- `GET /health`, `/budget`
- `GET /archetypes` (search, limit/offset), `/archetypes/{archetype}`
- `GET /archetypes/{archetype}/compare?left=...&right=...`
- `GET /archetypes/{archetype}/timeline`
- `GET /cards` (search; metrics remain separated by archetype)
- `GET /signals`, `/signals/{signal_id}`
- `GET /snapshots/{snapshot_id}`

Research filters: format, rank, period, patch. Signals: format, rank, period, patch, state, kind,
archetype, timezone-aware since/until, limit/offset. Signal evidence references immutable
snapshots. No API endpoint starts a collection job or changes data.

## Migration, backup, retention and rollback

`python -m app.meta_intelligence migrate` creates only the separate Lab DB. Repeated
calls are safe; future schema versions are refused. Existing application tables are
not migrated. Disable the flag and new timers to roll back behavior; retain Lab data.
Reverting the source patch does not require deleting history.

`python -m app.meta_intelligence backup /secure/path/lab.sqlite3` uses SQLite's backup
API and verifies integrity. The existing export-bundle script now includes a consistent
Lab backup. Import verifies its integrity/version and refuses to overwrite existing Lab
history. Restore only into a fresh, stopped instance; run regression checks before use.
Existing export secrecy rules still apply: bundles may contain service credentials.

The old JSON policy keeps five revisions by default and is not sufficient analytical
retention. Lab MVP retains snapshots/evidence indefinitely, with no automatic pruning.
An evidence-aware archival/retention policy is required before a long-term rollout;
never delete referenced snapshots independently of signals. This is an explicit MVP
storage limitation, not an assumed inherited retention guarantee.

## Validation and rollout

Offline fixtures are clearly synthetic (`tests/fixtures/meta_lab.json`); no real keys,
network requests or production data are required. Unit/integration tests cover budget
boundaries/reservations/failures, immutability, backups, freshness, selectors, signal
rules, comparisons, auth, disabled mode, cache exclusion and API filtering.

```sh
python -m pytest tests/test_meta_intelligence*.py -q
python -m pytest tests/test_firecrawl_scrape_do_fallback.py tests/test_firecrawl_key_rotation.py tests/test_scrapfly_key_rotation.py tests/test_hsreplay_card_periods.py -q
python -m pytest -q
node --check web/meta-intelligence.js
```

Before promotion: verify the baseline source revision (the original working tree
contained pre-existing unpublished changes), deploy only to an isolated test instance,
configure the existing access key via protected environment, verify the patch and
opening spend, migrate and sync published data, inspect health/coverage, then enable
the two new timers only with explicit rollout authority. Do not change the existing
matrix or streamer timer cadence. No deployment is part of this change.

## Next stages

Independent sample/window data for defensible winrate and card-effect inference;
provider-level reconciliation of unknown costs; richer deck-specific matchup data;
additional independent confirmation sampling and outcome scoring at 24h/72h/7d; evidence-aware
archival; optional HSReplay adapter without sample double counting; allowlisted social
radar and editorial AI drafts. These limitations remain visible rather than inventing
statistics or presenting correlations as proof.
