# ADR-001: PostgreSQL with NocoDB as the central data platform

## Status

Superseded by ADR-002

## Date

2026-08-07

## Context

Hearthstone data is currently split between three storage styles:

- MariaDB contains catalogue and editorial data.
- SQLite contains more than a million historical analytics rows.
- JSON files contain parser snapshots and source-specific payloads.

The split works for ingestion but makes discovery, cross-source analytics,
access control and integration with new services unnecessarily difficult. The
current PHP and FastAPI contracts must continue working while storage changes.

## Decision

Use PostgreSQL 17 as the target system of record and NocoDB as the internal
table-management interface. Migrate with a strangler approach:

1. Copy data into PostgreSQL without changing existing consumers.
2. Verify counts, keys and representative API responses.
3. Move readers one service at a time behind compatible adapters.
4. Move writers only after PostgreSQL has been proven in production.
5. Retire MariaDB/SQLite only after measured usage reaches zero.

PostgreSQL is divided into explicit schemas:

- `catalog` — cards, heroes, cosmetics, translations and editorial relations.
- `analytics` — observations, snapshots, decks, archetypes and time series.
- `raw` — validated source payloads stored as JSONB.
- `platform` — migrations, data catalogue and synchronization runs.

## Alternatives considered

### Keep MariaDB and move SQLite tables into it

This is the smallest migration, but the current catalogue already stores many
JSON documents as `LONGTEXT`, several legacy tables still use MyISAM, and the
combined analytics model benefits from PostgreSQL JSONB, partial indexes and
partitioning. MariaDB remains a supported source during transition.

### Use NocoDB as the database

NocoDB is an interface and automation layer, not the durable database engine.
Making PostgreSQL the source of truth avoids lock-in and lets services integrate
through stable APIs or normal database clients.

### Replace everything in one cutover

Rejected because both public APIs already have consumers. A parallel shadow
database provides comparison and rollback without downtime.

## Consequences

- New integrations should target stable service APIs first and PostgreSQL
  read-only roles when direct SQL is justified.
- NocoDB is internal and remains behind authentication.
- Synchronization and verification tooling becomes part of the production
  platform and must be monitored.
- The old stores remain operational until every consumer is migrated.
- PostgreSQL is selected for consolidation and data capabilities; it is not
  assumed to be faster without query-level measurements and indexes.
