# Operations runbook

## On-call questions

1. Is the custom admin panel responding?
   Run `scripts/verify-platform.sh`, then inspect the Nginx and PHP-FPM logs.
2. Is a data source stale or failing?
   Read `hub.integration_status` and the latest rows in `platform.sync_runs`.
3. Did the scheduled parser import finish?
   Run `systemctl status hs-data-platform-import.service` and inspect its JSON
   events in `/var/log/hs-data-platform/jobs.jsonl`.
4. Is there a usable recent backup?
   Run `systemctl status hs-data-platform-backup.service` and list the
   root-restricted `backups/` directory.

## Routine checks

```bash
/srv/hs-data-platform/scripts/verify-platform.sh
/srv/hs-data-platform/scripts/verify-data.php
/srv/hs-data-platform/tests/statistics-normalizer.test.php
/srv/hs-data-platform/tests/admin-ui-contract.sh
systemctl list-timers 'hs-data-platform-*'
sudo tail -n 50 /var/log/hs-data-platform/jobs.jsonl
sudo docker compose --project-directory /srv/hs-data-platform/postgres \
  -f /srv/hs-data-platform/postgres/docker-compose.yml ps
```

The parser JSON/statistics/media import and the atomic shadow synchronization
are scheduled every 15 minutes. Both scheduled services use one shared lock in
the root-owned `0700` directory `/run/hs-data-platform-sync`, including all three import steps. A job
waits up to 15 minutes for the lock and fails visibly if it cannot acquire it.
The original execution budgets remain 20 minutes for import and 30 minutes for
shadow; service timeouts also allow for lock waiting and termination cleanup.
Each scheduled run tags all writer connections, including pgloader, with a
unique `hsdp-sync-UUID` application name. On timeout or TERM, the supervisor
stops its host process group, terminates only that run's database sessions,
and checks that they have finished before releasing the lock. Under the lock,
the next run also cleans up scheduled sessions left by a hard kill. If database
cleanup cannot be verified, no new producer starts. Manual and unrelated
database sessions are not selected. Services use `KillMode=mixed` and allow
90 seconds for shutdown cleanup; a subsequent start recovers after SIGKILL.
The supervisor persists `host-state.json` before launching each producer. If a
host process cannot be stopped, later jobs remain blocked while that group is
present. An interrupted launch with no confirmed process identity also remains
blocked for inspection. Recovery never kills a process merely by a saved PID;
absence is required, and possible process-group id reuse fails conservatively.
For a safe manual refresh, use `sudo scripts/run-scheduled-sync import` or
`sudo scripts/run-scheduled-sync shadow`. Calling the underlying scripts
directly bypasses the shared scheduling lock. PostgreSQL is backed up daily;
validated backups are kept for 14 days. Direct MariaDB/SQLite bootstrap into
the target schemas remains manual; the scheduled shadow job instead loads
staging schemas and merges them transactionally, followed by `verify-data.php`.
The catalogue merge removes only obsolete card identities that collide on dbf
before inserting canonical identities. It retains the existing authoritative
snapshot and append-only history rules and rolls back on inconsistent input.

## Incremental SQLite shadow transfer

Scheduled shadow synchronization uses `bootstrap-shadow.php catalog-stage`
for MariaDB and `stage-analytics.py` for the 17 public SQLite analytics tables.
The SQLite reader opens `/srv/hs-data-api/data/hs_parses.db` in read-only mode
and holds one read transaction while hashing and copying. It creates no complete
SQLite backup, runs no SQLite VACUUM, and does not use pgloader for analytics.
The original manual `catalog`, `analytics`, `all` and `stage` bootstrap modes
remain available and retain their existing full-bootstrap behavior.

Every source row is still read to detect corrections anywhere in history,
including corrections with unchanged IDs, timestamps or row counts. Rows are
fingerprinted in fixed ranges of the first integer primary-key column, 8192
key values per range; composite keys retain their full identity. String-key
tables use a whole-table fingerprint. The second read copies only changed
ranges in the same SQLite snapshot. This reduces transfer and staging writes;
it does not remove the full source comparison read. Copy conversion preserves
the destination real-number precision, NULL/empty strings and JSON numeric
precision. Explicit public-table allowlisting excludes authentication tables.

`analytics_stage` contains the changed public rows. `analytics_sync_stage`
contains the complete manifest, changed ranges and expected checkpoint
generation. Both use UNLOGGED tables because they are disposable; the merge
checks the whitelist, table shape, generation and row counts before writing
canonical data. Missing/truncated stages after a crash fail instead of being
interpreted as deletions. The existing catalogue merge and the analytics merge
still commit together. `platform.analytics_shadow_state` and
`platform.analytics_shadow_chunks` advance inside that same transaction.
A failed stage or merge does not advance the checkpoints.

The merge updates only different canonical rows. Authoritative tables reconcile
missing keys within changed or removed ranges; the ten existing history tables
retain rows no longer present in SQLite. Identical history row counts no longer
hide source corrections. Existing `verify-data.php` count/lag checks remain
in place; they can still report a target-ahead history mismatch after a source
history deletion, as before. Investigate that condition without deleting history.

The first run copies all public source rows to establish checkpoints. A missing
or inconsistent manifest, source-file replacement, schema change, target-table
replacement/truncation, or unexpected target row count forces a full table
reconciliation. Checkpoints assume canonical shadow tables have no other writers;
normal API consumers only read them. An administrative target edit that keeps
the row count and storage identity unchanged requires an explicit full refresh:

```bash
sudo env HS_DATA_ANALYTICS_FULL=1 /srv/hs-data-platform/scripts/run-scheduled-sync shadow
```

This flag applies only to that supervised run, including lock and timeout cleanup.
Do not invoke the staging helper alone against production. Avoid direct target edits;
make ordinary corrections in the SQLite source. A full PostgreSQL restore
normally restores canonical rows and their checkpoint tables together. For a
partial table restore, request a full refresh. The shared supervisor and its
connection tags/timeout cleanup are unchanged.

Logs add `analytics_stage_completed` and `analytics_delta_completed` events
with source rows read, rows staged, changed ranges and actual canonical writes
and deletes. Distinguish the initial full synchronization from steady-state
runs when comparing resource use. The MariaDB catalogue still uses pgloader;
optimizing that smaller workload is a separate task.

## Imports that avoid unchanged content writes

The raw importer compares `fetched_at`, `state` and the actual JSONB payload
for an existing `(source_id, dataset_version)`. A corrected payload is imported
even if its version is unchanged; JSON object key order alone is not a change.
For timestamped, unchanged datasets, `imported_at` remains the last content
write or version re-selection time. `parser-json.last_synced_at` and a successful
`platform.sync_runs` record still advance on every check. Sources without
`fetched_at` retain the previous `imported_at` freshness behavior. Re-selecting
an older stored version with a tied source timestamp still promotes it for
consumers that order by `fetched_at, imported_at`; other versions remain stored.

The statistics importer retains snapshot IDs and refreshes their timestamps,
source URL, patch and metadata. Normalized rows use the existing
`(snapshot_id, entity_key)` primary key: only new or changed rows are written,
and only missing entities within the imported snapshots are removed. Historical
and skipped-source snapshots are untouched. Comparison includes all projected
fields and JSONB metrics, using the destination numeric precision. Duplicate
entity keys choose the first occurrence in COPY order. The entire import remains
one transaction under the existing shared supervisor lock and connection tag.

Successful JSON events and `platform.sync_runs` now report actual
`rows_written` (inserted or updated), `rows_unchanged`, and, for statistics,
`rows_deleted` and `rows_deduplicated`. Snapshot freshness writes are deliberately
separate from normalized-row counts. There is no schema migration or new cache
to invalidate; reverting the two importer scripts restores full-write behavior.

`tests/incremental-import.test.py --container hs-incremental-fixture-20260906`
is a destructive fixture-only integration test: it requires the exact disposable
container name and label, disabled network, tmpfs PGDATA, memory limit, and no
volumes or published ports. It rejects ordinary/production containers. It tests
real importer SQL, rollback, correction without a version change, history and
deletion boundaries, numeric precision, and complete PHP imports with private
test adapters for the database and external source boundaries.

## Shared sync lock and media offload

`/run/hs-data-platform-sync/sync.lock` serialises `run-scheduled-sync`
(import, shadow), the panel catalogue publishers, `kolodahs-card-history-media`
and the exchange step of `api-uploads-offload`. Keep every holder short:

- `index-media-assets.php` never enters `uploads/.s3-storage` and reuses the
  indexed size/mtime of offloaded links; only unseen links are stat()ed
  (at most 2000 per run).
- `api-uploads-offload` copies and verifies without the lock and waits up to
  600 s for it only to swap files for links. A busy lock ends the run with
  `deferred_exchange`; local files stay untouched.
- `kolodahs-card-history-media` exits 75 (`SuccessExitStatus`) when the lock
  stays busy for 900 s; the timer retries a minute later.

`platform/scripts/api-uploads-offload` is installed outside this tree:

```bash
sudo install -o root -g root -m 0755 platform/scripts/api-uploads-offload /usr/local/sbin/api-uploads-offload
```

## Custom panel

- Document root: `/srv/api-kolodahearthstone/panel/current`
- Persistent media and state: `/srv/api-kolodahearthstone/panel-data`
- Main catalogue: `index.php`
- Statistics gateway: `analytics.php` and `lib/analytics.php`
- Browser statistics client: `assets/analytics.js`
- Public API: `api/index.php`

The panel exposes real meta, the HSGuru archetype list, Arena card slices,
Standard/Wild card slices and Battlegrounds hero rating brackets. PostgreSQL
stores the same source snapshots in `analytics.game_stat_snapshots` and their
normalized rows in `analytics.game_stat_rows`; consumers should prefer the
stable `hub.game_stat_latest` view.

The `/data` and `/data/` legacy bookmarks redirect to `/`. Stale `/_nuxt/`
requests return `410 Gone` and must not be proxied to another application.

## Restore outline

1. Stop services that write to PostgreSQL, leaving the current dump available.
2. Create a separate empty restore database; do not overwrite `hs_data` first.
3. Restore the selected `hs_data.dump` with `pg_restore`.
4. Run both verification scripts against the restored state.
5. Switch consumers only after count and API checks pass.

## Rollback

The legacy MariaDB, SQLite, PHP panel and FastAPI service remain the production
read/write path during shadow migration. PostgreSQL can be stopped without
changing those source stores, although the parser shadow import will pause.

Do not put passwords, tokens, data payloads or authentication headers in logs.
Platform job logs use stable JSON event names and a per-run identifier. Log
rotation keeps eight compressed weekly files.
