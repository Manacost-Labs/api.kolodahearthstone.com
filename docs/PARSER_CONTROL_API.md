# Parser Control API

The parser control plane is exposed only through admin-key protected endpoints.
All responses are private and must not be cached.

## Publication policy

`PATCH /admin/parser-control/policy` switches between `stable` and a bounded
`early` publication mode. An early mutation must include a future
`earlyUntil`; expiry returns the effective mode to `stable` automatically.
The early policy is intentionally global but its reduced thresholds apply only
to the explicit patch-sensitive source allowlist: Arena early feeds, registered
HSGuru meta/matchups, and HSReplay card-period IDs ending in `_patch`. Rolling
HSReplay `1d`/`3d`/`7d`/`14d` datasets and unrelated sources remain stable.

An accepted early sample is published as `provisional` while retaining the
stable baseline/LKG. Each refresh captures one immutable policy token, so a
concurrent policy update makes the candidate stale rather than mixing early
and stable gates within one run.

## Snapshot and schedule contract

`GET /admin/parser-control` returns the current publication policy, section
switches, source health, recent runs, and a read-only schedule inventory.

The schedule contract is additive and versioned independently:

```json
{
  "generatedAt": "2026-07-21T00:00:00+00:00",
  "scheduleInventory": {
    "schemaVersion": 1,
    "inventoryVersion": "2026-07-21.1",
    "timeSemantics": "nominal",
    "runtimeTimerStateIncluded": false,
    "schedules": []
  }
}
```

- `nextRunAt` is an ISO-8601 UTC timestamp calculated from the
  version-controlled Docker timer schedule.
- `timeSemantics: nominal` means random delay, host downtime, and the live
  `systemd` enabled/active state are not included.
- Every section and source has `scheduleIds`, a short `schedule` label, and an
  effective `nextRunAt`.
- A disabled section keeps its schedule metadata but its own and its sources'
  effective `nextRunAt` values are `null`, because scheduled commands honour
  the section switch.
- A bounded schedule remains in the inventory after it expires with
  `isActive: false` and `nextRunAt: null`.

When a timer is added or changed, update `app/parser_control_schedule.py` in the
same change and increment `SCHEDULE_INVENTORY_VERSION`. Increment
`SCHEDULE_INVENTORY_SCHEMA_VERSION` only for a breaking shape or semantic
change. Contract tests enforce complete source and section coverage.

## Mutation durability and audit warning

Policy and section updates first persist the revisioned control state and then
write a structured operational audit event. Audit logging is a secondary side
effect: if it fails after the control file was committed, the endpoint still
returns `200` with the new revision and an additive machine-readable warning:

```json
{
  "revision": 3,
  "warnings": [
    {
      "code": "AUDIT_WRITE_FAILED",
      "message": "Настройка сохранена, но запись в журнал аудита не удалась. Проверьте журнал сервиса.",
      "auditAction": "parser_control.sections.update"
    }
  ]
}
```

This prevents a caller from retrying an operation that actually succeeded. The
service also writes the audit failure to its standard error logger so the
condition remains observable when the structured audit sink is unavailable.
