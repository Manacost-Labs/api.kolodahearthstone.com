from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import source_operationally_enabled
from .parser_control_schedule import (
    _SCHEDULES,
    SCHEDULE_INVENTORY_VERSION,
    SCHEDULE_TIMEZONE,
    _ScheduleSpec,
)
from .sources import SOURCE_BY_ID
from .storage import root_dir

TRACKED_SCHEDULE_IDS = frozenset({"refresh-all-daily", "refresh-api-daily"})
FUTURE_HORIZON = timedelta(hours=48)
OCCURRENCE_DEADLINE = timedelta(hours=2)
MAX_EXCLUSION_REASON_LENGTH = 96


@dataclass(frozen=True)
class SourceEligibility:
    eligible: bool
    exclusion_reason: str = ""


@dataclass(frozen=True)
class MaterializationResult:
    coverage_started_at: datetime
    materialized_through: datetime
    cohort_hash: str
    inserted_source_windows: int


class SourceSetMismatchError(ValueError):
    """The launched source set differs from the persisted obligation."""


class OccurrenceNotFoundError(LookupError):
    """No already-materialized occurrence can be claimed."""


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("schedule ledger datetimes must be timezone-aware")
    return moment.astimezone(UTC)


def deterministic_occurrence_id(schedule_id: str, scheduled_for: datetime) -> str:
    moment = _utc(scheduled_for).replace(microsecond=0)
    return f"{schedule_id}:{moment.strftime('%Y%m%dT%H%M%SZ')}"


def tracked_schedule_specs() -> tuple[_ScheduleSpec, ...]:
    specs = tuple(spec for spec in _SCHEDULES if spec.id in TRACKED_SCHEDULE_IDS)
    if frozenset(spec.id for spec in specs) != TRACKED_SCHEDULE_IDS:
        raise RuntimeError("tracked schedule inventory is incomplete")
    if any(spec.purpose != "primary" for spec in specs):
        raise RuntimeError("recovery schedules cannot create ledger obligations")
    return specs


def _valid_local_candidate(
    local_day: date,
    local_time: time,
    *,
    timezone: ZoneInfo,
) -> datetime | None:
    naive = datetime.combine(local_day, local_time)
    candidate = naive.replace(tzinfo=timezone, fold=0)
    round_trip = candidate.astimezone(UTC).astimezone(timezone)
    if round_trip.replace(tzinfo=None) != naive:
        return None
    return candidate.astimezone(UTC)


def enumerate_nominal_occurrences(
    spec: _ScheduleSpec,
    *,
    start: datetime,
    end: datetime,
) -> tuple[datetime, ...]:
    lower = _utc(start)
    upper = _utc(end)
    if upper <= lower:
        return ()
    if spec.recurrence == "explicit":
        return tuple(
            moment
            for value in spec.explicit_local_datetimes
            if lower <= (moment := value.astimezone(UTC)) < upper
        )

    timezone = ZoneInfo(SCHEDULE_TIMEZONE)
    local_day = lower.astimezone(timezone).date()
    last_day = upper.astimezone(timezone).date()
    occurrences: list[datetime] = []
    while local_day <= last_day:
        day_allowed = not (
            (spec.recurrence == "weekly" and local_day.weekday() not in spec.weekdays)
            or (spec.recurrence == "odd-month-days" and local_day.day % 2 == 0)
        )
        if day_allowed:
            for local_time in spec.local_times:
                candidate = _valid_local_candidate(
                    local_day, local_time, timezone=timezone
                )
                if candidate is not None and lower <= candidate < upper:
                    occurrences.append(candidate)
        local_day += timedelta(days=1)
    return tuple(sorted(occurrences))


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_source_windows (
            occurrence_id TEXT NOT NULL,
            schedule_id TEXT NOT NULL,
            scheduled_for REAL NOT NULL,
            deadline_at REAL NOT NULL,
            source_id TEXT NOT NULL,
            inventory_version TEXT NOT NULL,
            cohort_hash TEXT NOT NULL,
            eligible INTEGER NOT NULL CHECK (eligible IN (0, 1)),
            exclusion_reason TEXT NOT NULL DEFAULT '',
            recorded_at REAL NOT NULL,
            PRIMARY KEY (occurrence_id, source_id),
            UNIQUE (schedule_id, scheduled_for, source_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_ledger_state (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            coverage_started_at REAL NOT NULL,
            materialized_through REAL NOT NULL,
            inventory_version TEXT NOT NULL,
            cohort_hash TEXT NOT NULL,
            tracked_schedule_count INTEGER NOT NULL,
            catalog_schedule_count INTEGER NOT NULL,
            catalog_source_count INTEGER NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS schedule_source_windows_due_idx
        ON schedule_source_windows (cohort_hash, deadline_at, eligible)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS schedule_source_windows_claim_idx
        ON schedule_source_windows (schedule_id, scheduled_for)
        """
    )


def _normalize_eligibility(value: SourceEligibility) -> SourceEligibility:
    if value.eligible:
        return SourceEligibility(eligible=True)
    reason = " ".join(str(value.exclusion_reason).split()) or "disabled"
    return SourceEligibility(
        eligible=False,
        exclusion_reason=reason[:MAX_EXCLUSION_REASON_LENGTH],
    )


def build_current_eligibility() -> dict[str, SourceEligibility]:
    """Snapshot operational authorization and parser-control section switches."""

    from .parser_control import filter_scheduled_source_ids

    source_ids = sorted(
        {
            source_id
            for spec in tracked_schedule_specs()
            for source_id in spec.source_ids
        }
    )
    operational = [
        source_id for source_id in source_ids if source_operationally_enabled(source_id)
    ]
    section_enabled = frozenset(filter_scheduled_source_ids(operational))
    decisions: dict[str, SourceEligibility] = {}
    for source_id in source_ids:
        if source_id not in operational:
            decisions[source_id] = SourceEligibility(
                eligible=False,
                exclusion_reason="operationally-disabled",
            )
        elif source_id not in section_enabled:
            decisions[source_id] = SourceEligibility(
                eligible=False,
                exclusion_reason="section-disabled",
            )
        else:
            decisions[source_id] = SourceEligibility(eligible=True)
    return decisions


def _source_decisions(
    specs: tuple[_ScheduleSpec, ...],
    overrides: Mapping[str, SourceEligibility],
) -> dict[str, SourceEligibility]:
    source_ids = {source_id for spec in specs for source_id in spec.source_ids}
    current = build_current_eligibility()
    decisions: dict[str, SourceEligibility] = {}
    for source_id in sorted(source_ids):
        override = overrides.get(source_id, current[source_id])
        decisions[source_id] = _normalize_eligibility(override)
    return decisions


def _cohort_hash(
    specs: tuple[_ScheduleSpec, ...],
    decisions: Mapping[str, SourceEligibility],
) -> str:
    payload = {
        "inventoryVersion": SCHEDULE_INVENTORY_VERSION,
        "schedules": [
            {
                "id": spec.id,
                "sources": [
                    {
                        "id": source_id,
                        "eligible": decisions[source_id].eligible,
                        "reason": decisions[source_id].exclusion_reason,
                    }
                    for source_id in sorted(spec.source_ids)
                ],
            }
            for spec in sorted(specs, key=lambda value: value.id)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _coverage_cohort_hash(configuration_hash: str, started_at: datetime) -> str:
    value = f"{configuration_hash}:{_utc(started_at).isoformat()}".encode()
    return hashlib.sha256(value).hexdigest()


def _active_primary_schedule_count(now: datetime) -> int:
    count = 0
    for spec in _SCHEDULES:
        if spec.purpose != "primary":
            continue
        if spec.recurrence != "explicit" or any(
            value.astimezone(UTC) >= now for value in spec.explicit_local_datetimes
        ):
            count += 1
    return count


def materialize_schedule_windows(
    connection: sqlite3.Connection,
    *,
    now: datetime,
    horizon: timedelta = FUTURE_HORIZON,
    eligibility_by_source: Mapping[str, SourceEligibility] | None = None,
) -> MaterializationResult:
    """Persist the next 48 hours without inventing obligations before activation."""

    if horizon <= timedelta(0) or horizon > FUTURE_HORIZON:
        raise ValueError("schedule ledger horizon must be between 0 and 48 hours")
    moment = _utc(now)
    materialized_through = moment + horizon
    specs = tracked_schedule_specs()
    decisions = _source_decisions(specs, eligibility_by_source or {})
    configuration_hash = _cohort_hash(specs, decisions)
    ensure_schema(connection)

    connection.execute("SAVEPOINT schedule_ledger_materialize")
    try:
        state = connection.execute(
            """
            SELECT coverage_started_at, materialized_through, cohort_hash
            FROM schedule_ledger_state
            WHERE singleton = 1
            """
        ).fetchone()
        coverage_gap = state is not None and float(state[1]) < moment.timestamp()
        configuration_changed = state is not None and str(state[2]) != (
            _coverage_cohort_hash(
                configuration_hash,
                datetime.fromtimestamp(float(state[0]), tz=UTC),
            )
        )
        coverage_started_at = (
            moment
            if state is None or configuration_changed or coverage_gap
            else datetime.fromtimestamp(float(state[0]), tz=UTC)
        )
        cohort_hash = _coverage_cohort_hash(
            configuration_hash,
            coverage_started_at,
        )
        cohort_changed = state is not None and str(state[2]) != cohort_hash

        if cohort_changed:
            placeholders = ",".join("?" for _ in specs)
            connection.execute(
                f"""
                DELETE FROM schedule_source_windows
                WHERE scheduled_for > ? AND schedule_id IN ({placeholders})
                """,
                (moment.timestamp(), *(spec.id for spec in specs)),
            )

        inserted = 0
        for spec in specs:
            occurrences = enumerate_nominal_occurrences(
                spec,
                start=moment,
                end=materialized_through,
            )
            for scheduled_for in occurrences:
                occurrence_id = deterministic_occurrence_id(spec.id, scheduled_for)
                for source_id in sorted(spec.source_ids):
                    decision = decisions[source_id]
                    cursor = connection.execute(
                        """
                        INSERT INTO schedule_source_windows (
                            occurrence_id, schedule_id, scheduled_for, deadline_at,
                            source_id, inventory_version, cohort_hash, eligible,
                            exclusion_reason, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(occurrence_id, source_id) DO NOTHING
                        """,
                        (
                            occurrence_id,
                            spec.id,
                            scheduled_for.timestamp(),
                            (scheduled_for + OCCURRENCE_DEADLINE).timestamp(),
                            source_id,
                            SCHEDULE_INVENTORY_VERSION,
                            cohort_hash,
                            int(decision.eligible),
                            decision.exclusion_reason,
                            moment.timestamp(),
                        ),
                    )
                    inserted += max(0, cursor.rowcount)

        final_through = max(
            materialized_through,
            datetime.fromtimestamp(float(state[1]), tz=UTC)
            if state is not None and not cohort_changed
            else materialized_through,
        )
        connection.execute(
            """
            INSERT INTO schedule_ledger_state (
                singleton, coverage_started_at, materialized_through,
                inventory_version, cohort_hash, tracked_schedule_count,
                catalog_schedule_count, catalog_source_count, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                coverage_started_at = excluded.coverage_started_at,
                materialized_through = excluded.materialized_through,
                inventory_version = excluded.inventory_version,
                cohort_hash = excluded.cohort_hash,
                tracked_schedule_count = excluded.tracked_schedule_count,
                catalog_schedule_count = excluded.catalog_schedule_count,
                catalog_source_count = excluded.catalog_source_count,
                updated_at = excluded.updated_at
            """,
            (
                coverage_started_at.timestamp(),
                final_through.timestamp(),
                SCHEDULE_INVENTORY_VERSION,
                cohort_hash,
                len(specs),
                _active_primary_schedule_count(moment),
                len(SOURCE_BY_ID),
                moment.timestamp(),
            ),
        )
        connection.execute("RELEASE SAVEPOINT schedule_ledger_materialize")
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT schedule_ledger_materialize")
        connection.execute("RELEASE SAVEPOINT schedule_ledger_materialize")
        raise

    return MaterializationResult(
        coverage_started_at=coverage_started_at,
        materialized_through=final_through,
        cohort_hash=cohort_hash,
        inserted_source_windows=inserted,
    )


def _ledger_path(path: Path | None) -> Path:
    return path if path is not None else root_dir() / "parser-telemetry.sqlite3"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def reconcile_schedule_ledger(
    *,
    now: datetime | None = None,
    horizon: timedelta = FUTURE_HORIZON,
    path: Path | None = None,
) -> dict[str, object]:
    """Atomically extend ledger coverage and return privacy-safe aggregates."""

    moment = _utc(now or datetime.now(UTC))
    connection = _connect(_ledger_path(path))
    try:
        ensure_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            result = materialize_schedule_windows(
                connection,
                now=moment,
                horizon=horizon,
            )
            state = connection.execute(
                """
                SELECT inventory_version, tracked_schedule_count,
                       catalog_schedule_count, catalog_source_count
                FROM schedule_ledger_state
                WHERE singleton = 1
                """
            ).fetchone()
            counts = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(eligible), 0)
                FROM schedule_source_windows
                WHERE cohort_hash = ? AND scheduled_for >= ? AND scheduled_for < ?
                """,
                (
                    result.cohort_hash,
                    moment.timestamp(),
                    result.materialized_through.timestamp(),
                ),
            ).fetchone()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.close()

    expected = int(counts[0])
    eligible = int(counts[1])
    return {
        "schema_version": 1,
        "inventory_version": str(state[0]),
        "coverage_started_at": result.coverage_started_at.isoformat(),
        "materialized_through": result.materialized_through.isoformat(),
        "tracked_schedule_count": int(state[1]),
        "catalog_schedule_count": int(state[2]),
        "catalog_source_count": int(state[3]),
        "expected_source_windows": expected,
        "eligible_source_windows": eligible,
        "excluded_source_windows": expected - eligible,
        "inserted_source_windows": result.inserted_source_windows,
    }


def claim_occurrence(
    schedule_id: str,
    requested_source_ids: Collection[str],
    *,
    now: datetime | None = None,
    path: Path | None = None,
) -> str:
    """Return a due persisted occurrence after validating its static scope."""

    specs = {spec.id: spec for spec in tracked_schedule_specs()}
    if schedule_id not in specs:
        raise ValueError(f"schedule is not tracked: {schedule_id}")
    if isinstance(requested_source_ids, (str, bytes)):
        raise SourceSetMismatchError("requested source scope must be a collection")
    requested = tuple(requested_source_ids)
    requested_set = frozenset(requested)
    if len(requested) != len(requested_set):
        raise SourceSetMismatchError("requested source scope contains duplicates")

    moment = _utc(now or datetime.now(UTC))
    connection = _connect(_ledger_path(path))
    try:
        ensure_schema(connection)
        occurrence = connection.execute(
            """
            SELECT occurrence_id
            FROM schedule_source_windows
            WHERE schedule_id = ? AND scheduled_for <= ? AND deadline_at >= ?
            ORDER BY scheduled_for DESC
            LIMIT 1
            """,
            (schedule_id, moment.timestamp(), moment.timestamp()),
        ).fetchone()
        if occurrence is None:
            raise OccurrenceNotFoundError(
                f"no materialized occurrence is claimable for {schedule_id}"
            )
        occurrence_id = str(occurrence[0])
        persisted_set = frozenset(
            str(row[0])
            for row in connection.execute(
                """
                SELECT source_id
                FROM schedule_source_windows
                WHERE occurrence_id = ?
                """,
                (occurrence_id,),
            )
        )
        if requested_set != persisted_set:
            missing = len(persisted_set - requested_set)
            extra = len(requested_set - persisted_set)
            raise SourceSetMismatchError(
                f"requested source scope mismatch: missing={missing}, extra={extra}"
            )
        return occurrence_id
    finally:
        connection.close()
