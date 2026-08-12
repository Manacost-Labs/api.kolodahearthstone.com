# ADR-002: PostgreSQL with the custom admin panel

## Status

Accepted

## Date

2026-08-07

## Context

PostgreSQL successfully consolidates catalogue, analytics and parser data, but
the generic NocoDB interface does not fit the daily editorial and statistics
workflow. The custom panel already has domain-specific card
filters, image previews, editing flows and an allowlisted statistics gateway.

## Decision

Keep PostgreSQL 17 as the central target data layer and develop the existing
custom PHP panel as the only internal user interface. Remove the NocoDB runtime,
Redis, proxy routes, metadata database and dedicated login. Preserve a final
backup of the retired interface for rollback.

The custom panel will evolve incrementally:

1. Keep current MariaDB and API behavior stable during shadow migration.
2. Add dashboard modules against stable `hub` views or compatible service APIs.
3. Move readers to PostgreSQL after count, latency and response-shape checks.
4. Move editing flows only after permissions, audit history and rollback exist.
5. Add new statistics as isolated, allowlisted modules with typed filters.

## Consequences

- There is one domain-specific interface at `api.kolodahearthstone.com`.
- PostgreSQL runs independently from the removed UI layer.
- UI work can prioritize cards, meta, maps and future statistics instead of a
  generic spreadsheet model.
- The panel remains dependency-light and protected by GitHub OAuth restricted to
  the allowlisted administrator.
- Historical NocoDB backups are retained temporarily but are not active services.
