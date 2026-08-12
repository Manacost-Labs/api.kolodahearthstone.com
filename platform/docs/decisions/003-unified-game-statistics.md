# ADR-003: Normalized game statistics

## Status

Accepted on 2026-08-07.

## Context

The admin panel previously displayed only a subset of the statistics available
through the legacy API and the Arena data feeds. Each source uses a
different JSON shape, while future services need one durable integration layer.

## Decision

Store every successful source response as an immutable snapshot in
`analytics.game_stat_snapshots` and store its records in
`analytics.game_stat_rows`. Common dimensions are explicit columns:
`dataset_kind`, `format_name`, `mode_name`, `rank_name`, `period_name`,
`entity_id`, `entity_name`, and `source_id`. Source-specific fields remain in
`metrics` and `payload` JSONB so new statistics can be added without destructive
schema changes.

`hub.game_stat_latest` exposes the latest successful slice per complete set of
dimensions. The import is one-way and idempotent: it never writes back to the
upstream API, MariaDB, SQLite, Firestone, HSReplay or HSGuru.

The initial normalized datasets are:

- constructed meta and HSGuru archetypes for Standard and Wild;
- Standard and Wild card statistics by rank and period;
- normal and Underground Arena card statistics;
- Battlegrounds hero statistics for all players, Top 50%, Top 25%, Top 10% and
  Top 1%.

## Consequences

The PHP panel may keep using stable API/file adapters during the shadow cutover,
but other services can integrate through PostgreSQL without depending on those
source shapes. Adding a statistic normally requires a normalizer mapping and a
new source slice, not a new table. Raw source payloads remain auditable.
