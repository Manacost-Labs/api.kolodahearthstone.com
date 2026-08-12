# Phase 11 verification — 2026-07-12

## Completed on the stabilization branch

- `python -m pytest -q`: **292 passed**, 4 subtests passed, 0 failed.
- `python -m compileall -q app`: passed.
- `python scripts/generate-source-catalog.py --check`: passed; 46 sources (44 scrape + 2 pipeline).
- `docker compose config --quiet` with a temporary non-secret env file: passed.
- Anti-pattern checks: no `os.environ.get` outside `app/config.py`; no duplicate `_parse_percent_value` / `_is_percent` helpers.
- Real Uvicorn smoke on isolated storage: `/health` 200, `/openapi.json` 200 with all eight v1 routes, `/v1/system/sources` returned 46 rows, conditional request returned 304 with an empty body.
- GitHub review threads: all resolved; PR is mergeable. GitHub pytest and the clean Docker build are green on final verification commit `d22442a` ([run 29187899529](https://github.com/Manacost-Labs/api.kolodahearthstone.com/actions/runs/29187899529)).
- The production image is built from scratch in CI with browser installation failures treated as fatal. The image also has a Docker healthcheck against `/health`.

## Current production baseline (before merge/deploy)

Read-only audit of `https://api.kolodahearthstone.com`:

- `/health`: healthy.
- `/datasets`: 46/46 have a dataset and status `ok`.
- Local contract + semantic replay over every public dataset: 44 pass both layers.
- `vicious_syndicate_radars`: contract passes, semantic validation rejects missing latest-report issue/date metadata.
- `vicious_syndicate_live_beta`: contract passes, semantic validation rejects 11/11 placeholder `Other <Class>` decks and zero named archetypes.
- `hsreplay_battlegrounds_hero_details`: approximately 251 hours old versus its 192-hour source limit.
- `/v1/system/sources`: 404, expected because this branch has not been deployed.

The three production data findings are exactly the kind of silent-success/staleness that the new health and publish gates surface; they require refresh after deployment, not weakening validators.

## Post-baseline remediation added to the branch

- Live Firebase recon confirmed every Vicious interval (`last6Hours` through `last2Weeks`) currently contains only 11 `Other <Class>` buckets. Extraction now reports `upstream_unclassified`, removes those buckets from deck/tier output, and fails the publish gate instead of presenting them as archetypes.
- Live radar recon found 24 parsed radars at issue 349 while the latest report is issue 352. Radar output now reports `upstream_stale` and retains latest-report issue/date provenance.
- BG hero detail refresh now runs Mon/Thu rather than only Monday; its stale limit is 120 hours (96-hour maximum schedule gap + 24-hour slack), preventing one missed weekly run from silently aging for 8–11 days.
- Quality diagnostics are retained in failure statuses so `/sources/{id}` and operations tooling can distinguish upstream warm-up/staleness from transport or parser failures.
- Final local suite after these changes: **281 passed**, 4 subtests passed, 0 failed; GitHub pytest is green on commit `a30686a`.

## New-expansion card coverage

- Current HearthstoneJSON lists 135 collectible cards in `ESCAPEFROM_VIOLET_HOLD`; the full local card index contains 254 records for the set including non-collectible/token forms.
- Production `hsreplay_cards_legend_included_winrate` and `...included_popularity` each resolve all 135/135 collectible IDs. The union of all four ranked card datasets also covers 135/135; the one-day Legend sample contains 132/135, which is expected usage sampling rather than an index miss.
- The in-process HearthstoneJSON index previously remained frozen until process restart even after its file TTL elapsed. It now refreshes and rebuilds derived ID/name/dbf indexes after 24 hours, writes atomically, rejects payload truncation, serves stale data with a retry backoff during upstream failure, and falls back from unavailable locale data to enUS.
- Final local suite after card-index hardening: **286 passed**, 4 subtests passed, 0 failed.

## Final Vicious Syndicate verification

- Rechecked live upstream on 2026-07-12: Firebase reports 392,274 games and 11
  classes, but all 11 archetype buckets are still `Other <Class>`; the parser
  reports `upstream_unclassified` and publishes zero placeholder decks.
- The latest report index is issue 352, published 2026-06-18. Radar issue 349
  remains an upstream-stale condition until Vicious publishes matching radars.
- Optional Vicious browser cookies can now be imported with
  `vicious-import-storage`. The storage file is written atomically with mode
  `0600`, unrelated-domain cookies are discarded, and malformed storage never
  breaks an otherwise public fetch.
- Final suite after Vicious session and documentation completion: **296 passed**,
  4 subtests passed, 0 failed.

## External steps still required

1. Observe every Docker timer for 24 hours and confirm no new contract,
   semantic, freshness, or systemd failures.

## Production rollout — 2026-07-12

- PR #2 was merged as `4149615`; the fingerprint-runtime and pipeline-audit
  follow-ups were merged as `e3418b1` and `90af02f`.
- The production checkout was advanced to `main` without deleting its previous
  local history. The old line is retained as
  `production-pre-stabilization-20260712`, and its uncommitted files are retained
  in stash `pre-stabilization-deploy-2026-07-12T10:28Z`.
- A clean production image was built and deployed. The running API container is
  healthy, Chromium is installed, and the Node fingerprint generator was
  executed successfully inside the container.
- All 46 source status files were updated during the rollout: 44 sources are
  `ok`; only the two Vicious sources are `quality_error` for confirmed upstream
  states. `freshness-check --since-hours 48` passes with zero stale and zero
  cached-after-failure sources.
- Both dedicated pipelines completed successfully:
  `hsreplay_battlegrounds_hero_details` and `hsreplay_archetypes` are `ok`.
- Public smoke passed: `/health` is healthy, `/datasets` exposes 46 legacy
  sources, `/v1/system/sources` exposes 46 sources, and conditional GET returns
  HTTP 304 with an empty body.
- Production concurrency was normalized to light/medium `3/2`; duplicate legacy
  env keys were removed and per-backend timeout was normalized to 240 seconds.
- `quality-check` now handles dedicated pipelines by their status and structured
  payload rather than applying an HTML-title test. Its only two bad sources are
  the expected Vicious upstream failures; four valid datasets remain in the
  warning score band for observation.
- Vicious authenticated cookies were imported into the production data volume
  with mode `0600`. The latest complete radar issue (349, 24 validated graphs)
  is intentionally publishable with `upstream_stale` metadata while report 352
  has no matching radar. A dedicated two-hour timer will switch to the next
  complete issue automatically; unclassified Live placeholders remain blocked.
