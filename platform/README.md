# HS Data Platform

Central data layer for Hearthstone catalogues and analytics. PostgreSQL is the
target system of record, while the custom PHP panel at
`https://api.kolodahearthstone.com/` is the human-facing workspace.

## Current state

- Custom admin panel: `https://api.kolodahearthstone.com/` (protected by GitHub OAuth)
- Admin source: `../panel` in this repository
- Admin production document root: `/srv/api-kolodahearthstone/panel/current`
- PostgreSQL 17: `127.0.0.1:15434`, not exposed publicly
- PostgreSQL schemas: `catalog`, `analytics`, `raw`, `platform`, and `hub`
- Production readers and writers still use their original MariaDB/SQLite stores
- Synchronization into PostgreSQL is one-way until cutover verification finishes
- Parser JSON snapshots and normalized game statistics are imported every 15 minutes
- PostgreSQL backups run daily with 14-day retention

## Architecture

```text
MariaDB catalogues ─┐
                    ├─> PostgreSQL hs_data ─> custom panel / future services
SQLite analytics ───┤       ├─ catalog
JSON/API datasets ──┘       ├─ analytics (snapshots and normalized rows)
                            ├─ raw
                            ├─ platform
                            └─ hub (stable integration views)
```

The custom panel continues to read the proven MariaDB and API contracts during
the shadow migration. New panel modules should use `hub` views or stable APIs so
storage can be switched without changing the interface.

See [ADR-002](docs/decisions/002-custom-admin-panel.md) for the current decision.
The normalized statistics contract is documented in
[ADR-003](docs/decisions/003-unified-game-statistics.md).

## Commands

| Command | Purpose |
|---|---|
| `scripts/apply-migrations.sh` | Create/update the `hs_data` database |
| `scripts/bootstrap-shadow.php all` | Copy MariaDB and SQLite into PostgreSQL |
| `scripts/import-datasets.php` | Import validated parser JSON snapshots as JSONB |
| `scripts/import-statistics.php` | Normalize meta, HSGuru, Arena, Standard/Wild cards and BG rating slices |
| `scripts/verify-data.php` | Compare source/target counts, keys and JSONB types |
| `scripts/verify-platform.sh` | Check the custom panel, Nginx and PostgreSQL |
| `tests/statistics-normalizer.test.php` | Verify the normalized statistics contract |
| `tests/admin-ui-contract.sh` | Verify the panel modules and accessible lightbox hooks |
| `sudo scripts/backup.sh` | Back up and validate PostgreSQL |
| `cd postgres && sudo docker compose ps` | Inspect PostgreSQL |

## Safety rules

1. Do not point production services at PostgreSQL until verification passes.
2. Synchronization is source-to-target only; it must never update MariaDB or SQLite.
3. Keep public API response shapes backward compatible during cutover.
4. Store credentials only in restricted generated files; never commit or print them.
5. Back up PostgreSQL before upgrades or data migrations.

Operational checks and restore guidance are in [OPERATIONS.md](docs/OPERATIONS.md).
