# Current patch statistics refresh

HSReplay card-period sources request `TimeRange=CURRENT_PATCH` for Standard and
Wild across every configured rank. HSReplay resets this sample at patch launch,
so a new patch can legitimately contain far fewer rows than the mature previous
patch.

Outside an explicitly enabled early window, current-patch contracts combine two
safeguards:

- an absolute minimum of 450 Standard rows or 700 Wild rows, plus the normal
  identity and metric-fill validation;
- an 85% count-regression allowance between consecutive current-patch
  snapshots.

Rolling one-, three-, seven- and fourteen-day datasets keep their stricter
regression limits. This lets the first valid snapshot of a new patch replace
the old patch without allowing empty or malformed payloads onto the site.

## Bounded early-patch publication

Immediately after a game patch, even the normal current-patch floors can be
legitimately unavailable. The parser-control `early` mode therefore applies a
smaller contract to an explicit allowlist only:

- the three Arena early-patch feeds;
- the registered HSGuru meta and matchup feeds;
- HSReplay card-period source IDs ending in `_patch`.

It never relaxes HSReplay rolling `1d`, `3d`, `7d`, or `14d` datasets. During
the bounded window, HSGuru requires at least three complete rows and HSReplay
current-patch cards require at least 20 unique cards with valid metrics. A
smaller HSGuru document may pass the raw-size gate, but Cloudflare challenges,
wrong-site pages, incomplete rows, duplicate card identities, impossible
percentages, and malformed JSON remain rejected.

Every accepted early dataset is marked `provisional`. The stable publication
baseline/LKG is retained instead of being replaced by the small sample. The
effective policy is captured at the beginning of a refresh; a policy change
while the request is running cannot publish against a different gate. When
`earlyUntil` expires, the normal row and regression thresholds are restored
automatically.

The HSGuru current patch is discovered from the combined Blizzard and wiki.gg
catalog. Build versions such as `36.2.0.248348` are normalized to `36.2.0` for
HSGuru queries and site labels.

## Battlegrounds: do not use a rolling window for the live minion pool

The HSReplay Battlegrounds minion tier list must request:

```text
BattlegroundsMMRPercentile=TOP_50_PERCENT
BattlegroundsTimeRange=CURRENT_BATTLEGROUNDS_PATCH
```

Do not replace `CURRENT_BATTLEGROUNDS_PATCH` with `LAST_7_DAYS`. During a pool
rotation the rolling window can contain mostly cards from the previous patch
while still carrying a fresh HTTP response and `fetched_at` timestamp. On patch
36.2 this produced 264 apparently fresh rows but only three `BG36_*` cards; the
patch-scoped query produced 229 rows and all 62 `BG36_*` cards.

Freshness checks for this dataset must therefore validate both dimensions:

1. **Temporal freshness:** `fetched_at` is within the scheduled refresh window.
2. **Content freshness:** the returned card identities overlap the active pool
   from the current Hearthstone card index. A new timestamp alone is not proof
   that the tier list belongs to the current patch.

After a Battlegrounds patch, run the targeted refresh before the normal daily
schedule if the public tier list still contains the previous pool:

```bash
docker compose run --rm api \
  python -m app.cli refresh \
  --source hsreplay_battlegrounds_minions \
  --source firestone_battlegrounds_comps \
  --source firestone_battlegrounds_spells \
  --require-all-ok
```

Then verify `/api/bg/tier-lists?list=minions`, `strategies`, and `spells` by
checking the source timestamp, row count, unique card IDs, duplicate IDs,
missing images/metrics, and current-patch card coverage.

## Battlegrounds strategies immediately after a patch

Firestone can initially publish fewer than ten compositions. The source
contract accepts five or more complete rows, including the seven strategies
seen at the start of patch 36.2. Responses with fewer than five rows, missing
strategy names, or missing core cards remain blocked. This keeps the first
useful post-patch list visible without allowing an empty or malformed response
to overwrite the last-known-good dataset.

The regression guard allows up to an 85% row-count contraction for this source
because a verified patch reset can reduce the curated list from about 30 rows
to seven. The five-row contract remains the hard lower bound, so a truncated
four-row response is still rejected. When changing either value, test both the
first valid post-patch sample and a deliberately incomplete sample.

## HSReplay strategies use the embedded live guide catalog

The strategies page must refresh **both** independent sources:
`hsreplay_battlegrounds_comps` and `firestone_battlegrounds_comps`. A successful
Firestone refresh does not prove that the HSReplay tab is current.

Since August 2026 HSReplay embeds its guide catalog as JSON in the
`#react_context` script. Ordinary HTML links and Firecrawl Markdown may be
empty even while that JSON contains the current guides. Parse only entries
where `comp_hidden` is false, resolve `comp_core_cards` DBF IDs through the
current HearthstoneJSON index, and retain `comp_last_updated` for diagnostics.
Never publish hidden historical guides merely to preserve the old row count.
When FlareSolverr returns the rendered HTML fallback instead, carry the
authoritative `s`/`a`/`b`/`c`/`d` marker from each listing group into its comp
rows; do not leave the tier blank and rely on a consumer's default (which
renders every strategy as D).
The HSReplay contract permits an 85% contraction because removing hidden
retired guides reduced the live catalog from 27 rows to eight on patch 36.2;
the five-row minimum continues to reject incomplete responses.

HSGuru Standard/Wild Legend can also contract immediately after a patch because
only archetypes above `min_games=100` are returned. On patch 36.2 the complete
tables changed from 65 to 23 Standard rows and from 105 to 29 Wild rows. In
stable mode these two feeds allow a 75% contraction only when at least ten
unique visible rows remain and at least 95% contain `Archetype`, `Winrate↓`,
and `Popularity`. The smaller three-row rule is available only through the
bounded provisional mode described above.

If Firecrawl returns fewer than three live guides, the fetcher must continue to
the authenticated HTML fallback instead of accepting the empty result. Verify
HSReplay and Firestone separately through `list=strategies&source=hsreplay` and
`list=strategies&source=firestone`, including timestamps, unique strategy IDs,
current-patch core-card IDs and absence of hidden guides.
