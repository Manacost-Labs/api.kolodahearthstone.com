"""Off-by-default planner that turns non-fresh primary outcomes into chains."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from .convergence_policy import SOURCE_TO_RECOVERY_COHORT, decide_recovery
from .convergence_store import ConvergenceStore

PlannerMode = Literal["off", "shadow"]
_BATCH_LIMIT = 500
_FIRST_RUN_LOOKBACK = timedelta(hours=24)


@dataclass(frozen=True)
class PlannerSummary:
    mode: PlannerMode
    scanned_terminal_events: int
    scanned_missing_slots: int
    planned_chains: int
    planned_sources: int
    skipped_events: int
    cursor_advanced: bool


def convergence_mode() -> PlannerMode:
    value = os.environ.get("HS_CONVERGENCE_MODE", "off").strip().lower()
    return cast(PlannerMode, value) if value in {"off", "shadow"} else "off"


def _deadline_for(action: str, observed_at: datetime) -> datetime:
    duration = {
        "retry_transport": timedelta(hours=4),
        "retry_publication": timedelta(hours=2),
        "retry_scheduler": timedelta(hours=2),
        "retry_local": timedelta(hours=2),
        "retry_candidate": timedelta(hours=24),
        "probe_upstream": timedelta(hours=48),
    }.get(action, timedelta(days=7))
    return observed_at + duration


def _terminal_events(
    path: Path,
    *,
    cursor: tuple[float, str] | None,
    now: datetime,
) -> list[sqlite3.Row]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        if cursor is None:
            lower_epoch = (now - _FIRST_RUN_LOOKBACK).timestamp()
            lower_id = ""
        else:
            lower_epoch, lower_id = cursor
        try:
            return list(
                connection.execute(
                    """
                    SELECT
                        attempt_id, refresh_window_id, source_id, finished_at,
                        outcome, reason_code, independently_ineligible_reason
                    FROM source_attempts
                    WHERE attempt_purpose = 'primary'
                      AND (
                          finished_at > ?
                          OR (finished_at = ? AND attempt_id > ?)
                      )
                      AND finished_at <= ?
                    ORDER BY finished_at, attempt_id
                    LIMIT ?
                    """,
                    (
                        lower_epoch,
                        lower_epoch,
                        lower_id,
                        now.timestamp(),
                        _BATCH_LIMIT,
                    ),
                )
            )
        except sqlite3.OperationalError as exc:
            if "no such table: source_attempts" not in str(exc):
                raise
            return []
    finally:
        connection.close()


def _missing_schedule_slots(path: Path, *, now: datetime) -> list[sqlite3.Row]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        try:
            return list(
                connection.execute(
                    """
                    SELECT
                        slots.occurrence_id,
                        slots.source_id,
                        slots.deadline_at
                    FROM schedule_source_windows AS slots
                    WHERE slots.eligible = 1
                      AND slots.deadline_at > ?
                      AND slots.deadline_at <= ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM source_attempts AS attempts
                          WHERE attempts.refresh_window_id = slots.occurrence_id
                            AND attempts.source_id = slots.source_id
                            AND (
                                attempts.outcome != 'skipped'
                                OR attempts.independently_ineligible_reason =
                                   'upstream_not_published'
                            )
                      )
                    ORDER BY slots.deadline_at, slots.occurrence_id, slots.source_id
                    LIMIT ?
                    """,
                    (
                        (now - _FIRST_RUN_LOOKBACK).timestamp(),
                        now.timestamp(),
                        _BATCH_LIMIT,
                    ),
                )
            )
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return []
    finally:
        connection.close()


def plan_once(
    *,
    path: Path,
    now: datetime | None = None,
    mode: PlannerMode | None = None,
) -> PlannerSummary:
    effective_mode = mode or convergence_mode()
    if effective_mode == "off":
        return PlannerSummary("off", 0, 0, 0, 0, 0, False)
    moment_value = now or datetime.now(UTC)
    if moment_value.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    moment = moment_value.astimezone(UTC)
    store = ConvergenceStore(path)
    store.initialize()
    cursor = store.planner_cursor()
    rows = _terminal_events(path, cursor=cursor, now=moment)
    missing_slots = _missing_schedule_slots(path, now=moment)
    planned_chain_ids: set[str] = set()
    planned_sources: set[str] = set()
    skipped = 0
    for row in rows:
        outcome = str(row["outcome"])
        upstream_pending = (
            str(row["independently_ineligible_reason"])
            == "upstream_not_published"
        )
        if outcome == "fresh_published" or (outcome == "skipped" and not upstream_pending):
            skipped += 1
            continue
        source_id = str(row["source_id"])
        cohort_id = SOURCE_TO_RECOVERY_COHORT.get(source_id)
        if cohort_id is None:
            skipped += 1
            continue
        reason_code = str(row["reason_code"] or "unknown")
        decision = decide_recovery(
            outcome=outcome,
            reason_code=reason_code,
            upstream_pending=upstream_pending,
        )
        if decision.action == "complete":
            skipped += 1
            continue
        observed_at = datetime.fromtimestamp(float(row["finished_at"]), tz=UTC)
        origin_occurrence_id = str(row["refresh_window_id"] or "").strip()
        if not origin_occurrence_id:
            origin_occurrence_id = f"attempt:{row['attempt_id']}"
        chain = store.create_or_get_chain(
            cohort_id=cohort_id,
            source_ids=[source_id],
            origin_occurrence_id=origin_occurrence_id,
            decision=decision,
            outcome=outcome,
            reason_code=reason_code,
            observed_at=observed_at,
            deadline_at=_deadline_for(decision.action, observed_at),
        )
        planned_chain_ids.add(chain.chain_id)
        planned_sources.add(source_id)

    for row in missing_slots:
        source_id = str(row["source_id"])
        cohort_id = SOURCE_TO_RECOVERY_COHORT.get(source_id)
        if cohort_id is None:
            skipped += 1
            continue
        observed_at = datetime.fromtimestamp(float(row["deadline_at"]), tz=UTC)
        decision = decide_recovery(outcome="missing", reason_code="unknown")
        chain = store.create_or_get_chain(
            cohort_id=cohort_id,
            source_ids=[source_id],
            origin_occurrence_id=str(row["occurrence_id"]),
            decision=decision,
            outcome="missing",
            reason_code="unknown",
            observed_at=observed_at,
            deadline_at=_deadline_for(decision.action, observed_at),
        )
        planned_chain_ids.add(chain.chain_id)
        planned_sources.add(source_id)

    cursor_advanced = bool(rows)
    if rows:
        last = rows[-1]
        store.advance_planner_cursor(
            finished_at=float(last["finished_at"]),
            attempt_id=str(last["attempt_id"]),
            updated_at=moment,
        )
    return PlannerSummary(
        effective_mode,
        len(rows),
        len(missing_slots),
        len(planned_chain_ids),
        len(planned_sources),
        skipped,
        cursor_advanced,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path)
    parser.add_argument("--mode", choices=("off", "shadow"))
    args = parser.parse_args()
    store = ConvergenceStore(args.path)
    summary = plan_once(path=store.path, mode=args.mode)
    print(json.dumps(asdict(summary), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
