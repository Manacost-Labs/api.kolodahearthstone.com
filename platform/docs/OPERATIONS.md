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

The parser JSON import and the normalized statistics import run every 15 minutes.
For a safe manual refresh, run `scripts/import-datasets.php` followed by
`scripts/import-statistics.php`. PostgreSQL is backed up daily;
validated backups are kept for 14 days. The MariaDB and SQLite bootstrap remains
manual because it recreates shadow tables and must be followed by
`verify-data.php`.

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
