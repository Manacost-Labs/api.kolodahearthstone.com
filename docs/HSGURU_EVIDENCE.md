# HSGuru collection evidence and offline replay

This additive first slice separates **when we downloaded a dataset** from
**the age of the statistics at HSGuru**. It does not change publication gates,
last-known-good selection, health policy, database schema, or provider requests.

## API contract

HSGuru source entries expose `data_evidence` in the source list, source detail,
`/demo/overview`, and `/demo/view/{source_id}`. Existing fields remain unchanged.
The source routes are `/sources` and `/sources/{source_id}`. These are existing
authenticated diagnostic routes, not new GraphQL fields. Use the canonical
host `https://api.kolodahearthstone.com`; normal route authentication still applies.

```json
{
  "schema_version": 1,
  "has_dataset": true,
  "collection": {"fetched_at": "2026-01-02T10:00:00Z"},
  "upstream": {
    "status": "unknown",
    "as_of": null,
    "reason": "adapter_has_no_verified_upstream_timestamp"
  },
  "coverage": {
    "status": "partial",
    "scope": "observed_archetype_components",
    "observed_archetypes": 2
  },
  "components": []
}
```

- `collection.fetched_at` is taken only from the resolved public dataset, never
  from a failed attempt's status. Missing, malformed, timezone-less, or future
  timestamps are `null`. Valid timestamps are normalized to UTC.
- `upstream` is intentionally `unknown`: current HSGuru adapters provide no
  verified upstream timestamp. Local `updated_at`, URL filters, row counts,
  and even a stored unverified freshness label are not proof.
- Archetype analysis summarizes `matchups` and `card_stats`, with `state_counts`,
  `entities_total`, oldest local `checked_at` / `updated_at`, and counts of
  missing timestamps. Output excludes raw errors, URLs and archetype content.
- Coverage `reported` means all **observed** components report `complete` or
  `sparse_valid` with valid local timestamps. `partial` means some observed
  components are cached, missing, erroneous or lack evidence. Neither means
  that the whole upstream catalogue was retrieved. Other HSGuru families and
  absent component evidence remain `unknown` / `not_measured`.

The overview panel displays collection age, unconfirmed upstream freshness,
and component coverage separately. “Подробнее” includes component dates and
missing evidence. Older API responses fail closed for HSGuru, without a green
freshness claim. This slice does not add fresh-only eligibility to HSGuru or
change other providers' existing freshness policy.

## Reproducible regression corpus

`tests/fixtures/hsguru_replay` contains small **synthetic**, non-secret examples,
not live captures or a complete 99-source corpus. The saved HTML covers a valid
50% diagonal and 0/100% boundaries, plus an invalid 999% cell amongst valid rows.
Both stable and early publication modes run the real HTML parser and quality
gate. A JSON example preserves old cached and missing archetype components.

Run `make hsguru-replay`. The same tests run in the canonical `make check` and
existing CI. To preserve a machine-readable report for a candidate checkout:

```sh
.venv/bin/python -m pytest tests/test_hsguru_replay.py tests/test_hsguru_evidence.py \
  --junitxml=/tmp/hsguru-replay.xml
```

Do not update approved expectations merely to make a changed gate pass.
Investigate new acceptances and rejections, add a minimized non-secret fixture,
then review the expectation change. No live capture or paid scraping is run by
these tests. ParsesUnix's separate `ws-profile compare` command compares two
profiles against one offline corpus; it does not compare two installed wheels.

## Boundaries and rollback

This is observability and regression tooling, not completion of the entire
parser roadmap. Verified upstream freshness, durable pagination, unified
publication versions and shared acquisition budgets remain separate tasks.
Reverting this code returns the old diagnostics/UI without data migration.
Implementation and push do not activate it in production; deployment and its
runtime checks remain a separate authorized step.
