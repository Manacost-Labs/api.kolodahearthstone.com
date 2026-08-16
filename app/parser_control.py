from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from .config import data_dir, source_operationally_enabled
from .parser_control_registry import (
    EARLY_SOURCE_IDS,
    SECTION_BY_ID,
    SECTIONS,
    SOURCE_TO_SECTION,
    source_label,
)
from .sources import SOURCE_BY_ID

CONTROL_FILENAME = "parser-control.json"
CONTROL_LOCK_FILENAME = "parser-control.lock"
MAX_RECENT_RUNS = 50
MAX_PENDING_RUNS = 20
MAX_RUN_SOURCES = len(SOURCE_BY_ID)
MAX_ORCHESTRATOR_REQUESTS = 1000
ACTIVE_RUN_STATUSES = {"queued", "running"}
RUN_STATUSES = ACTIVE_RUN_STATUSES | {"succeeded", "partial", "failed"}
_logger = logging.getLogger(__name__)
_REQUEST_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)


AUDIT_WRITE_WARNING = {
    "code": "AUDIT_WRITE_FAILED",
    "message": (
        "Настройка сохранена, но запись в журнал аудита не удалась. "
        "Проверьте журнал сервиса."
    ),
}


class ParserControlError(RuntimeError):
    pass


class InvalidControlRequest(ParserControlError):
    pass


class RevisionConflict(ParserControlError):
    def __init__(self, expected: int, current: int) -> None:
        super().__init__(f"Expected revision {expected}, current revision is {current}")
        self.expected = expected
        self.current = current


class ParserControlStorageError(ParserControlError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime | None = None) -> str:
    value = moment or _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _monotonic_ms() -> float:
    """Return a monotonic timestamp in milliseconds for run duration accounting."""

    return time.monotonic_ns() / 1_000_000


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


MAX_PUBLIC_RUN_ERRORS = 50


def _normalise_run_errors_with_metadata(value: Any) -> tuple[list[str], int, bool]:
    """Convert heterogeneous upstream errors into safe, stable JSON strings."""

    if not isinstance(value, list):
        return [], 0, False
    normalised: list[str] = []
    seen: set[str] = set()
    errors_total = 0
    context_keys = {
        "archetype_id",
        "card_id",
        "cardId",
        "dbfId",
        "deck_id",
        "deckId",
        "index",
        "row",
        "source_id",
        "sourceId",
    }
    scalar_types = (str, int, float, bool)
    for item in value:
        text = ""
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            message = next(
                (
                    str(item[key]).strip()
                    for key in ("error", "message", "detail")
                    if isinstance(item.get(key), scalar_types)
                    and str(item[key]).strip()
                ),
                "",
            )
            safe_keys = sorted(
                (key for key in item if isinstance(key, str)), key=str
            )
            context = [
                f"{key}={item[key]}"
                for key in safe_keys
                if key in context_keys
                and isinstance(item[key], scalar_types)
                and str(item[key]).strip()
            ]
            if message:
                text = f"{', '.join(context)}: {message}" if context else message
            else:
                safe_item = {
                    key: item[key]
                    for key in safe_keys
                    if key in context_keys
                    and (isinstance(item[key], scalar_types) or item[key] is None)
                }
                text = (
                    json.dumps(safe_item, ensure_ascii=False, sort_keys=True)
                    if safe_item
                    else "Структурированная ошибка парсера"
                )
        elif isinstance(item, (int, float, bool)):
            text = str(item)
        elif item is not None:
            # Never serialize an arbitrary object's repr: it may contain secrets,
            # addresses, or otherwise unstable implementation details.
            text = "Неизвестная ошибка парсера"
        text = text.strip()[:1000]
        if text and text not in seen:
            seen.add(text)
            errors_total += 1
            if len(normalised) < MAX_PUBLIC_RUN_ERRORS:
                normalised.append(text)
    return normalised, errors_total, errors_total > len(normalised)


def _normalise_run_errors(value: Any) -> list[str]:
    normalised, _errors_total, _errors_truncated = (
        _normalise_run_errors_with_metadata(value)
    )
    return normalised


def _result_errors(result: dict[str, Any]) -> tuple[list[str], int, bool]:
    errors, derived_total, derived_truncated = _normalise_run_errors_with_metadata(
        result.get("errors")
    )
    supplied_total = next(
        (
            normalised
            for key in ("errorsTotal", "errors_total")
            if (normalised := _non_negative_int(result.get(key))) is not None
        ),
        None,
    )
    errors_total = max(derived_total, supplied_total or 0)
    supplied_truncated = any(
        result.get(key) is True for key in ("errorsTruncated", "errors_truncated")
    )
    return (
        errors,
        errors_total,
        derived_truncated or supplied_truncated or errors_total > len(errors),
    )


def _merge_result_errors(
    first: dict[str, Any], second: dict[str, Any]
) -> tuple[list[str], int, bool]:
    """Merge aggregate-source errors without discarding truncation metadata."""

    first_errors, first_total, first_truncated = _result_errors(first)
    second_errors, second_total, second_truncated = _result_errors(second)
    errors, visible_total, visible_truncated = _normalise_run_errors_with_metadata(
        [*first_errors, *second_errors]
    )
    first_visible = set(first_errors)
    second_visible = set(second_errors)
    # Hidden errors from a truncated input may overlap the other input. Report
    # the smallest total guaranteed by the visible values and supplied totals.
    errors_total = max(
        visible_total,
        first_total + len(second_visible - first_visible),
        second_total + len(first_visible - second_visible),
    )
    return (
        errors,
        errors_total,
        first_truncated
        or second_truncated
        or visible_truncated
        or errors_total > len(errors),
    )


def _result_rows_total(result: dict[str, Any]) -> int | None:
    for value in (result.get("rows_total"), result.get("rowsTotal")):
        rows_total = _non_negative_int(value)
        if rows_total is not None:
            return rows_total
    quality = result.get("quality")
    if isinstance(quality, dict):
        return _non_negative_int(quality.get("rows_total"))
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise InvalidControlRequest("earlyUntil must be a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise InvalidControlRequest("earlyUntil must include a timezone")
    return parsed.astimezone(UTC)


def _log_storage_fallback(
    operation: str,
    exc: Exception,
    *,
    fallback: str,
) -> None:
    try:
        from .refresh_log import log_action

        log_action(
            "parser_control.storage_fallback",
            level="error",
            detail=f"{type(exc).__name__}: {str(exc)[:700]}",
            extra={"operation": operation, "fallback": fallback},
        )
    except Exception:
        # A control-file failure must never become a public-site outage merely
        # because the secondary operational logger is also unavailable.
        return


def _record_control_audit(
    action: str,
    *,
    actor: str,
    revision: int,
    details: dict[str, Any],
) -> dict[str, str] | None:
    """Persist a mutation audit without invalidating an already committed write."""

    try:
        from .refresh_log import log_action

        log_action(
            action,
            extra={
                "actor": actor,
                "revision": revision,
                **details,
            },
        )
    except Exception:
        # The policy file is already durable at this point. Returning an error
        # would invite the caller to retry a mutation that actually succeeded.
        _logger.exception(
            "Parser control audit write failed after committed mutation "
            "action=%s revision=%s actor=%s",
            action,
            revision,
            actor,
        )
        return {**AUDIT_WRITE_WARNING, "auditAction": action}
    return None


def _with_warning(
    payload: dict[str, Any], warning: dict[str, str] | None
) -> dict[str, Any]:
    if warning is None:
        return payload
    existing = payload.get("warnings")
    warnings = list(existing) if isinstance(existing, list) else []
    return {**payload, "warnings": [*warnings, warning]}


def _default_state() -> dict[str, Any]:
    return {
        "schemaVersion": 3,
        "revision": 1,
        "policyConfigured": False,
        "policy": {
            "mode": "stable",
            "earlyUntil": None,
            "reason": None,
            "updatedAt": None,
            "updatedBy": None,
        },
        "sections": {section.id: True for section in SECTIONS},
        "updatedAt": None,
        "updatedBy": None,
        "runs": [],
        "orchestratorRequests": [],
    }


def _normalise_state(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ParserControlStorageError("Parser control state must be a JSON object")
    state = _default_state()
    revision = raw.get("revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ParserControlStorageError("Parser control revision is invalid")
    state["revision"] = revision
    # Normalising an older file upgrades the in-memory representation; the
    # next mutation persists the current schema version.
    state["schemaVersion"] = 3

    policy = raw.get("policy") or {}
    if not isinstance(policy, dict):
        raise ParserControlStorageError("Parser control policy is invalid")
    mode = policy.get("mode", "stable")
    if mode not in {"stable", "early"}:
        raise ParserControlStorageError("Parser control publication mode is invalid")
    state["policy"] = {
        "mode": mode,
        "earlyUntil": policy.get("earlyUntil"),
        "reason": policy.get("reason"),
        "updatedAt": policy.get("updatedAt"),
        "updatedBy": policy.get("updatedBy"),
    }
    # Files written by the first control-plane release did not have an
    # explicit provenance bit. A policy with its own audit timestamp is an
    # intentional admin choice; a default policy persisted only by a section
    # edit or run enqueue is not.
    inferred_policy_configured = bool(policy.get("updatedAt") or policy.get("updatedBy"))
    configured = raw.get("policyConfigured", inferred_policy_configured)
    if not isinstance(configured, bool):
        raise ParserControlStorageError("Parser control policy provenance is invalid")
    state["policyConfigured"] = configured

    sections = raw.get("sections") or {}
    if not isinstance(sections, dict):
        raise ParserControlStorageError("Parser control sections are invalid")
    invalid_section_flags = [
        section.id
        for section in SECTIONS
        if section.id in sections and not isinstance(sections[section.id], bool)
    ]
    if invalid_section_flags:
        raise ParserControlStorageError(
            "Parser control section flags are invalid: " + ", ".join(invalid_section_flags)
        )
    state["sections"] = {
        section.id: sections.get(section.id, True)
        for section in SECTIONS
    }
    state["updatedAt"] = raw.get("updatedAt")
    state["updatedBy"] = raw.get("updatedBy")

    runs = raw.get("runs") or []
    if not isinstance(runs, list):
        raise ParserControlStorageError("Parser control runs are invalid")
    state["runs"] = [run for run in runs if isinstance(run, dict)][-MAX_RECENT_RUNS:]

    requests = raw.get("orchestratorRequests") or []
    if not isinstance(requests, list):
        raise ParserControlStorageError("Parser orchestrator request ledger is invalid")
    normalised_requests: list[dict[str, Any]] = []
    for entry in requests:
        if not isinstance(entry, dict):
            continue
        request_id = entry.get("requestId")
        run_id = entry.get("runId")
        source_ids = entry.get("requestedSourceIds")
        if (
            not isinstance(request_id, str)
            or not request_id
            or len(request_id) > 160
            or any(character not in _REQUEST_ID_CHARACTERS for character in request_id)
            or not isinstance(run_id, str)
            or not run_id
            or not isinstance(source_ids, list)
            or any(not isinstance(source_id, str) for source_id in source_ids)
        ):
            continue
        normalised_requests.append(
            {
                "requestId": request_id,
                "runId": run_id,
                "requestedSourceIds": sorted(set(source_ids)),
                "createdAt": entry.get("createdAt"),
            }
        )
    known_request_ids = {entry["requestId"] for entry in normalised_requests}
    for run in state["runs"]:
        request_id = run.get("requestId")
        if not isinstance(request_id, str) or not request_id or request_id in known_request_ids:
            continue
        normalised_requests.append(
            {
                "requestId": request_id,
                "runId": str(run.get("id") or ""),
                "requestedSourceIds": sorted(
                    set(run.get("requestedSourceIds") or run.get("sourceIds") or [])
                ),
                "createdAt": run.get("createdAt"),
            }
        )
        known_request_ids.add(request_id)
    state["orchestratorRequests"] = normalised_requests[-MAX_ORCHESTRATOR_REQUESTS:]
    return state


def _record_orchestrator_request(
    state: dict[str, Any],
    *,
    request_id: str,
    run_id: str,
    requested_source_ids: list[str],
) -> None:
    requests = [
        entry
        for entry in state.get("orchestratorRequests") or []
        if isinstance(entry, dict) and entry.get("requestId") != request_id
    ]
    requests.append(
        {
            "requestId": request_id,
            "runId": run_id,
            "requestedSourceIds": list(requested_source_ids),
            "createdAt": _iso(),
        }
    )
    state["orchestratorRequests"] = requests[-MAX_ORCHESTRATOR_REQUESTS:]


def _environment_policy(at: datetime) -> dict[str, Any] | None:
    enabled = os.environ.get("HS_ARENA_POST_PATCH_ENABLED", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None
    timezone = ZoneInfo("Europe/Warsaw")
    local_day = at.astimezone(timezone).date()
    try:
        start = date.fromisoformat(
            os.environ.get("HS_ARENA_POST_PATCH_FROM", "2026-07-21").strip()
        )
        until = date.fromisoformat(
            os.environ.get("HS_ARENA_POST_PATCH_UNTIL", "2026-07-28").strip()
        )
    except ValueError:
        return None
    if not start <= local_day < until:
        return None
    until_at = datetime.combine(until, datetime_time.min, tzinfo=timezone).astimezone(UTC)
    return {
        "mode": "early",
        "effectiveMode": "early",
        "earlyUntil": _iso(until_at),
        "reason": "Послепатчевый режим из настроек окружения",
        "updatedAt": None,
        "updatedBy": "environment",
        "managedBy": "environment",
    }


def _policy_view(
    state: dict[str, Any],
    *,
    persisted: bool,
    at: datetime | None = None,
) -> dict[str, Any]:
    moment = at or _now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    policy_configured = bool(state.get("policyConfigured"))
    if not policy_configured:
        environment = _environment_policy(moment)
        if environment:
            return {**environment, "policyConfigured": False}

    policy = state["policy"]
    mode = policy["mode"]
    effective = mode
    if mode == "early":
        try:
            until = _parse_datetime(policy.get("earlyUntil"))
        except InvalidControlRequest:
            until = None
        if until is None or moment.astimezone(UTC) >= until:
            effective = "stable"
    return {
        **policy,
        "effectiveMode": effective,
        "managedBy": "control" if policy_configured else "default",
        "policyConfigured": policy_configured,
    }


def _run_public_view(run: dict[str, Any]) -> dict[str, Any]:
    view = dict(run)
    source_ids = [source_id for source_id in (run.get("sourceIds") or []) if source_id]
    results: list[dict[str, Any]] = []
    for row in run.get("results") or []:
        if not isinstance(row, dict):
            continue
        result = dict(row)
        errors, errors_total, errors_truncated = _result_errors(row)
        result["errors"] = errors
        result["errorsTotal"] = errors_total
        result["errorsTruncated"] = errors_truncated
        result.pop("errors_total", None)
        result.pop("errors_truncated", None)
        for key, aliases in {
            "rowsTotal": ("rowsTotal", "rows_total"),
            "durationMs": ("durationMs", "duration_ms"),
        }.items():
            value = next(
                (
                    normalised
                    for alias in aliases
                    if (normalised := _non_negative_int(row.get(alias))) is not None
                ),
                None,
            )
            for alias in aliases:
                if alias != key:
                    result.pop(alias, None)
            if value is None:
                result.pop(key, None)
            else:
                result[key] = value
        results.append(result)
    view["results"] = results
    total = int(run.get("totalSources") or len(source_ids))
    completed = int(run.get("completedSources") or len(results))
    failed = run.get("failedSources")
    if not isinstance(failed, int) or isinstance(failed, bool):
        failed = sum(
            row.get("state") != "ok" or bool(row.get("servingCachedDataset"))
            for row in results
        )
    if run.get("status") == "failed" and not results and total:
        completed = total
        failed = total
    view["totalSources"] = total
    view["completedSources"] = min(completed, total)
    view["failedSources"] = min(int(failed), total)
    return view


class ParserControlStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root_override = Path(root) if root is not None else None
        self._thread_lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self._root_override or data_dir()

    @property
    def state_path(self) -> Path:
        return self.root / "control" / CONTROL_FILENAME

    @property
    def lock_path(self) -> Path:
        return self.root / "control" / CONTROL_LOCK_FILENAME

    @contextmanager
    def _locked(self, *, exclusive: bool) -> Iterator[tuple[dict[str, Any], bool]]:
        try:
            directory = self.state_path.parent
            directory.mkdir(parents=True, exist_ok=True)
            with self._thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock_handle:
                fcntl.flock(
                    lock_handle.fileno(),
                    fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
                )
                try:
                    persisted = self.state_path.exists()
                    if persisted:
                        try:
                            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError) as exc:
                            raise ParserControlStorageError(
                                "Parser control state cannot be read safely"
                            ) from exc
                        state = _normalise_state(raw)
                    else:
                        state = _default_state()
                    yield state, persisted
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        except ParserControlError:
            raise
        except OSError as exc:
            raise ParserControlStorageError(
                "Parser control storage is unavailable"
            ) from exc

    def _write_locked(self, state: dict[str, Any]) -> None:
        path = self.state_path
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.chmod(0o640)
        os.replace(temporary, path)

    def read_state(self) -> tuple[dict[str, Any], bool]:
        with self._locked(exclusive=False) as (state, persisted):
            return state, persisted

    def _read_source_json(self, directory: str, source_id: str) -> dict[str, Any] | None:
        path = self.root / directory / f"{source_id}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _read_baseline_json(self, source_id: str, label: str) -> dict[str, Any] | None:
        path = self.root / "baselines" / f"{source_id}.{label}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def stable_baseline(self, source_id: str) -> dict[str, Any] | None:
        from .post_patch_policy import (
            POST_PATCH_BASELINE_LABEL,
            STABLE_PUBLICATION_BASELINE_LABEL,
        )

        return self._read_baseline_json(
            source_id, STABLE_PUBLICATION_BASELINE_LABEL
        ) or self._read_baseline_json(source_id, POST_PATCH_BASELINE_LABEL)

    def snapshot(self, *, at: datetime | None = None) -> dict[str, Any]:
        state, persisted = self.read_state()
        moment = at or _now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        moment = moment.astimezone(UTC)
        policy_view = _policy_view(state, persisted=persisted, at=moment)
        from .parser_control_schedule import build_schedule_inventory

        schedule_inventory = build_schedule_inventory(at=moment)
        sections: list[dict[str, Any]] = []
        for section in SECTIONS:
            section_enabled = state["sections"].get(section.id, True)
            section_schedule = schedule_inventory["sections"][section.id]
            sources: list[dict[str, Any]] = []
            for source_id in section.source_ids:
                source = SOURCE_BY_ID[source_id]
                source_schedule = schedule_inventory["sources"][source_id]
                status = self._read_source_json("statuses", source_id) or {}
                dataset = self._read_source_json("datasets", source_id) or {}
                quality = status.get("quality") or {}
                candidate_rows_total = status.get("rows_total")
                if candidate_rows_total is None and isinstance(quality, dict):
                    candidate_rows_total = quality.get("rows_total")
                candidate_fetched_at = dataset.get("fetched_at")
                stable_baseline = self.stable_baseline(source_id)
                effective_mode = (
                    str(policy_view["effectiveMode"])
                    if source_id in EARLY_SOURCE_IDS
                    else "stable"
                )
                published = resolve_public_dataset(
                    source_id,
                    dataset,
                    at=at,
                    store=self,
                    effective_mode=effective_mode,
                ) if dataset else None
                published_fetched_at = (published or {}).get("fetched_at")
                if published is None:
                    publication_channel = "unavailable"
                elif _is_provisional_dataset(published):
                    publication_channel = "early"
                elif published is dataset:
                    publication_channel = "stable"
                else:
                    publication_channel = "stable_baseline"
                published_rows_total: int | None = None
                if published:
                    try:
                        from .dataset_regression import estimate_metric_count

                        published_rows_total = estimate_metric_count(
                            source,
                            (published.get("data") or {}),
                        )
                    except Exception:
                        published_rows_total = None
                    if not published_rows_total:
                        public_data = published.get("data") or {}
                        structured = public_data.get("structured") or {}
                        for container in (structured, public_data):
                            if not isinstance(container, dict):
                                continue
                            row_list = next(
                                (
                                    container[key]
                                    for key in (
                                        "cards",
                                        "rows",
                                        "items",
                                        "minions",
                                        "heroes",
                                        "decks",
                                        "archetypes",
                                    )
                                    if isinstance(container.get(key), list)
                                ),
                                None,
                            )
                            if row_list is not None:
                                published_rows_total = len(row_list)
                                break
                if published_rows_total is None and published is dataset:
                    published_rows_total = candidate_rows_total

                source_state = str(
                    status.get("state") or ("ready" if dataset else "missing")
                )
                serving_cached = bool(status.get("serving_cached_dataset"))
                upstream_state = str(
                    status.get("last_refresh_upstream_state")
                    or status.get("upstream_state")
                    or ""
                )
                upstream_publication_pending = (
                    source_id == "vicious_syndicate_radars"
                    and serving_cached
                    and status.get("cached_content_temporally_grandfathered") is True
                    and upstream_state == "upstream_publication_pending"
                )
                if upstream_publication_pending:
                    health = "upstream_pending"
                elif serving_cached or source_state == "partial":
                    health = "warning"
                elif source_state in {"ok", "ready"}:
                    health = "ok"
                elif source_state == "missing":
                    health = "missing"
                else:
                    health = "error"
                last_attempt_at = (
                    status.get("last_refresh_at")
                    if serving_cached
                    else status.get("fetched_at")
                )
                last_error = None
                if serving_cached:
                    last_error = status.get("last_refresh_error")
                elif health == "error":
                    last_error = status.get("error") or status.get("detail")
                sources.append(
                    {
                        "id": source_id,
                        "label": source_label(source_id),
                        "kind": source.kind,
                        "state": health,
                        "health": health,
                        "sourceState": source_state,
                        "upstreamState": upstream_state or None,
                        "servingCachedDataset": serving_cached,
                        "lastSuccessAt": published_fetched_at,
                        "lastAttemptAt": last_attempt_at,
                        "lastError": last_error,
                        "datasetFetchedAt": candidate_fetched_at,
                        "candidateFetchedAt": candidate_fetched_at,
                        "publishedFetchedAt": published_fetched_at,
                        "publicationChannel": publication_channel,
                        "stableBaselineAvailable": stable_baseline is not None,
                        "rowsTotal": published_rows_total,
                        "candidateRowsTotal": candidate_rows_total,
                        "supportsEarly": source_id in EARLY_SOURCE_IDS,
                        "canRunManually": True,
                        "enabled": section_enabled,
                        "scheduleIds": source_schedule["scheduleIds"],
                        "schedule": source_schedule["schedule"],
                        "nextRunAt": (
                            source_schedule["nextRunAt"] if section_enabled else None
                        ),
                    }
                )
            sections.append(
                {
                    "id": section.id,
                    "label": section.label,
                    "group": section.group,
                    "description": section.description,
                    "enabled": section_enabled,
                    "supportsEarly": section.supports_early,
                    "sourceCount": len(sources),
                    "scheduleIds": section_schedule["scheduleIds"],
                    "schedule": section_schedule["schedule"],
                    "nextRunAt": (
                        section_schedule["nextRunAt"] if section_enabled else None
                    ),
                    "sources": sources,
                }
            )
        runs = [
            _run_public_view(run)
            for run in sorted(
                state["runs"], key=lambda run: str(run.get("createdAt") or ""), reverse=True
            )
        ]
        active_run = next(
            (run for run in runs if run.get("status") in ACTIVE_RUN_STATUSES), None
        )
        return {
            "generatedAt": _iso(moment),
            "revision": state["revision"],
            "policy": policy_view,
            "policyConfigured": bool(state.get("policyConfigured")),
            "scheduleInventory": schedule_inventory,
            "sections": sections,
            "activeRun": active_run,
            "recentRuns": runs[:10],
            "updatedAt": state.get("updatedAt"),
            "updatedBy": state.get("updatedBy"),
        }

    def update_policy(
        self,
        *,
        expected_revision: int,
        mode: str,
        early_until: str | None,
        reason: str | None,
        updated_by: str,
    ) -> dict[str, Any]:
        if mode not in {"stable", "early"}:
            raise InvalidControlRequest("mode must be stable or early")
        actor = str(updated_by or "admin-api").strip()[:120] or "admin-api"
        clean_reason = str(reason or "").strip()[:500] or None
        until: datetime | None = None
        if mode == "early":
            if not clean_reason:
                raise InvalidControlRequest("reason is required for early mode")
            until = _parse_datetime(early_until)
            if until is None:
                raise InvalidControlRequest("earlyUntil is required for early mode")
            if until <= _now():
                raise InvalidControlRequest("earlyUntil must be in the future")
        timestamp = _iso()
        committed_revision: int
        with self._locked(exclusive=True) as (state, _persisted):
            if state["revision"] != expected_revision:
                raise RevisionConflict(expected_revision, state["revision"])
            state["revision"] += 1
            committed_revision = state["revision"]
            state["policyConfigured"] = True
            state["policy"] = {
                "mode": mode,
                "earlyUntil": _iso(until) if until else None,
                "reason": clean_reason,
                "updatedAt": timestamp,
                "updatedBy": actor,
            }
            state["updatedAt"] = timestamp
            state["updatedBy"] = actor
            self._write_locked(state)
        warning = _record_control_audit(
            "parser_control.policy.update",
            actor=actor,
            revision=committed_revision,
            details={
                "mode": mode,
                "earlyUntil": _iso(until) if until else None,
                "reason": clean_reason,
            },
        )
        return _with_warning(self.snapshot(), warning)

    def update_sections(
        self,
        *,
        expected_revision: int,
        changes: dict[str, bool],
        updated_by: str,
    ) -> dict[str, Any]:
        if not changes:
            raise InvalidControlRequest("At least one section change is required")
        unknown = sorted(set(changes) - set(SECTION_BY_ID))
        if unknown:
            raise InvalidControlRequest("Unknown sections: " + ", ".join(unknown))
        if any(not isinstance(value, bool) for value in changes.values()):
            raise InvalidControlRequest("Section enabled values must be boolean")
        actor = str(updated_by or "admin-api").strip()[:120] or "admin-api"
        timestamp = _iso()
        committed_revision: int
        with self._locked(exclusive=True) as (state, _persisted):
            if state["revision"] != expected_revision:
                raise RevisionConflict(expected_revision, state["revision"])
            state["revision"] += 1
            committed_revision = state["revision"]
            state["sections"].update(changes)
            state["updatedAt"] = timestamp
            state["updatedBy"] = actor
            self._write_locked(state)
        warning = _record_control_audit(
            "parser_control.sections.update",
            actor=actor,
            revision=committed_revision,
            details={"sections": dict(sorted(changes.items()))},
        )
        return _with_warning(self.snapshot(), warning)

    def enqueue_run(
        self,
        *,
        source_ids: list[str],
        requested_by: str,
        reason: str | None,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        normalised = sorted(set(source_ids))
        if not normalised:
            raise InvalidControlRequest("At least one source is required")
        if len(normalised) > MAX_RUN_SOURCES:
            raise InvalidControlRequest("Too many sources in one run")
        unknown = sorted(set(normalised) - set(SOURCE_BY_ID))
        if unknown:
            raise InvalidControlRequest("Unknown sources: " + ", ".join(unknown))
        actor = str(requested_by or "admin-api").strip()[:120] or "admin-api"
        clean_reason = str(reason or "").strip()[:500] or None
        clean_request_id = str(request_id or "").strip() or None
        if clean_request_id and (
            len(clean_request_id) > 160
            or any(character not in _REQUEST_ID_CHARACTERS for character in clean_request_id)
        ):
            raise InvalidControlRequest("Invalid orchestrator request ID")
        with self._locked(exclusive=True) as (state, _persisted):
            if clean_request_id:
                request_entry = next(
                    (
                        entry
                        for entry in state["orchestratorRequests"]
                        if entry.get("requestId") == clean_request_id
                    ),
                    None,
                )
                if request_entry is not None:
                    existing_selection = request_entry["requestedSourceIds"]
                    if existing_selection != normalised:
                        raise InvalidControlRequest(
                            "Orchestrator request ID was already used for another selection"
                        )
                    existing = next(
                        (
                            run
                            for run in state["runs"]
                            if run.get("id") == request_entry.get("runId")
                        ),
                        None,
                    )
                    if existing is None:
                        raise InvalidControlRequest(
                            "Orchestrator request ID refers to an expired parser run"
                        )
                    return _run_public_view(existing), True
            active = [
                run for run in state["runs"]
                if run.get("status") in ACTIVE_RUN_STATUSES
            ]
            active_sources: set[str] = set()
            active_source_runs: dict[str, str] = {}
            for run in active:
                completed = {
                    str(result.get("sourceId") or result.get("source_id") or "")
                    for result in (run.get("results") or [])
                    if isinstance(result, dict)
                }
                for source_id in run.get("sourceIds") or []:
                    active_source_runs.setdefault(source_id, str(run.get("id") or ""))
                active_sources.update(
                    source_id
                    for source_id in (run.get("sourceIds") or [])
                    if source_id not in completed
                )
            if clean_request_id:
                deduplicated_source_ids = sorted(
                    set(normalised) & set(active_source_runs)
                )
                uncovered_source_ids = sorted(
                    set(normalised) - set(active_source_runs)
                )
            else:
                deduplicated_source_ids = sorted(set(normalised) & active_sources)
                uncovered_source_ids = sorted(set(normalised) - active_sources)
            if not uncovered_source_ids:
                dependency_run_ids = {
                    active_source_runs[source_id] for source_id in normalised
                    if active_source_runs.get(source_id)
                }
                exact_covering = None
                if clean_request_id and len(dependency_run_ids) == 1:
                    dependency_run_id = next(iter(dependency_run_ids))
                    exact_covering = next(
                        (
                            run
                            for run in active
                            if run.get("id") == dependency_run_id
                            and set(run.get("sourceIds") or []) == set(normalised)
                        ),
                        None,
                    )
                if clean_request_id and exact_covering is not None:
                    view = _run_public_view(exact_covering)
                    view["requestedSourceIds"] = normalised
                    view["deduplicatedSourceIds"] = deduplicated_source_ids
                    _record_orchestrator_request(
                        state,
                        request_id=clean_request_id,
                        run_id=str(exact_covering["id"]),
                        requested_source_ids=normalised,
                    )
                    self._write_locked(state)
                    return view, True
                if not clean_request_id:
                    covering = next(
                        (
                            run
                            for run in active
                            if set(normalised).issubset(
                                set(run.get("sourceIds") or [])
                            )
                        ),
                        active[0],
                    )
                    view = _run_public_view(covering)
                    view["requestedSourceIds"] = normalised
                    view["deduplicatedSourceIds"] = deduplicated_source_ids
                    return view, True
            if len(active) >= MAX_PENDING_RUNS:
                raise InvalidControlRequest("Parser run queue is full")
            run_source_ids = normalised if clean_request_id else uncovered_source_ids
            run = {
                "id": uuid4().hex,
                "status": "queued",
                "sourceIds": run_source_ids,
                "executionSourceIds": (
                    uncovered_source_ids if clean_request_id else run_source_ids
                ),
                "dependencySourceRuns": (
                    {
                        source_id: active_source_runs[source_id]
                        for source_id in deduplicated_source_ids
                        if active_source_runs.get(source_id)
                    }
                    if clean_request_id
                    else {}
                ),
                "requestedSourceIds": normalised,
                "deduplicatedSourceIds": deduplicated_source_ids,
                "requestedBy": actor,
                "requestId": clean_request_id,
                "reason": clean_reason,
                "createdAt": _iso(),
                "startedAt": None,
                "finishedAt": None,
                "results": [],
                "error": None,
                "totalSources": len(run_source_ids),
                "completedSources": 0,
                "failedSources": 0,
            }
            state["runs"].append(run)
            state["runs"] = state["runs"][-MAX_RECENT_RUNS:]
            if clean_request_id:
                _record_orchestrator_request(
                    state,
                    request_id=clean_request_id,
                    run_id=str(run["id"]),
                    requested_source_ids=normalised,
                )
            self._write_locked(state)
            return _run_public_view(run), bool(deduplicated_source_ids)

    def list_runs(self, *, limit: int = 20) -> dict[str, Any]:
        state, _persisted = self.read_state()
        runs = [
            _run_public_view(run)
            for run in sorted(
                state["runs"], key=lambda run: str(run.get("createdAt") or ""), reverse=True
            )[: max(1, min(limit, MAX_RECENT_RUNS))]
        ]
        active = next(
            (run for run in runs if run.get("status") in ACTIVE_RUN_STATUSES), None
        )
        return {"activeRun": active, "runs": runs}

    def recover_interrupted_runs(self) -> int:
        recovered = 0
        with self._locked(exclusive=True) as (state, persisted):
            if not persisted:
                return 0
            for run in state["runs"]:
                if run.get("status") == "running":
                    run["status"] = "queued"
                    run["startedAt"] = None
                    run["error"] = "Запуск восстановлен после перезапуска сервиса"
                    recovered += 1
            if recovered:
                self._write_locked(state)
        return recovered

    def claim_next_run(self) -> dict[str, Any] | None:
        with self._locked(exclusive=True) as (state, persisted):
            if not persisted:
                return None
            queued = sorted(
                (run for run in state["runs"] if run.get("status") == "queued"),
                key=lambda run: str(run.get("createdAt") or ""),
            )
            if not queued:
                return None
            runs_by_id = {
                str(candidate.get("id") or ""): candidate
                for candidate in state["runs"]
            }

            def dependencies_finished(candidate: dict[str, Any]) -> bool:
                dependencies = candidate.get("dependencySourceRuns") or {}
                if not isinstance(dependencies, dict):
                    return True
                return all(
                    runs_by_id.get(str(dependency_run_id), {}).get("status")
                    not in ACTIVE_RUN_STATUSES
                    for dependency_run_id in dependencies.values()
                )

            run = next(
                (
                    candidate
                    for candidate in queued
                    if dependencies_finished(candidate)
                ),
                None,
            )
            if run is None:
                return None
            run["status"] = "running"
            run["startedAt"] = _iso()
            run["error"] = None
            self._write_locked(state)
            return dict(run)

    def dependency_results(self, run_id: str) -> list[dict[str, Any]]:
        state, _persisted = self.read_state()
        run = next((row for row in state["runs"] if row.get("id") == run_id), None)
        if run is None:
            return []
        runs_by_id = {
            str(candidate.get("id") or ""): candidate
            for candidate in state["runs"]
        }
        summaries: list[dict[str, Any]] = []
        dependencies = run.get("dependencySourceRuns") or {}
        if not isinstance(dependencies, dict):
            return summaries
        for source_id in run.get("sourceIds") or []:
            dependency_run_id = dependencies.get(source_id)
            if not isinstance(dependency_run_id, str):
                continue
            dependency = runs_by_id.get(dependency_run_id)
            raw_result = next(
                (
                    result
                    for result in (dependency or {}).get("results") or []
                    if isinstance(result, dict)
                    and str(result.get("sourceId") or result.get("source_id") or "")
                    == source_id
                ),
                None,
            )
            if raw_result is None:
                detail = "Dependency parser run did not produce a source result"
                summaries.append(
                    {
                        "sourceId": source_id,
                        "label": source_label(source_id),
                        "state": "error",
                        "fetchedAt": None,
                        "detail": detail,
                        "errors": [detail],
                        "servingCachedDataset": False,
                    }
                )
                continue
            summary = _run_result_summary(raw_result)
            summary["sourceId"] = source_id
            summaries.append(summary)
        return summaries

    def record_run_result(
        self,
        run_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Checkpoint one source result so a restart can resume safely."""
        source_id = str(result.get("sourceId") or result.get("source_id") or "")
        if not source_id:
            raise ValueError("Parser run result must contain sourceId")
        with self._locked(exclusive=True) as (state, _persisted):
            run = next((row for row in state["runs"] if row.get("id") == run_id), None)
            if run is None:
                return None
            if source_id not in set(run.get("sourceIds") or []):
                raise ValueError("Parser run result source is not part of the run")
            results = [
                dict(row)
                for row in (run.get("results") or [])
                if isinstance(row, dict)
                and str(row.get("sourceId") or row.get("source_id") or "") != source_id
            ]
            persisted_result = dict(result)
            persisted_result["sourceId"] = source_id
            persisted_result.pop("source_id", None)
            results.append(persisted_result)
            source_order = {
                value: index for index, value in enumerate(run.get("sourceIds") or [])
            }
            results.sort(
                key=lambda row: source_order.get(str(row.get("sourceId")), 10**9)
            )
            run["results"] = results
            run["completedSources"] = len(results)
            run["failedSources"] = sum(
                row.get("state") != "ok" or bool(row.get("servingCachedDataset"))
                for row in results
            )
            run["lastProgressAt"] = _iso()
            self._write_locked(state)
            return _run_public_view(run)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        state, _persisted = self.read_state()
        run = next((row for row in state["runs"] if row.get("id") == run_id), None)
        return _run_public_view(run) if run is not None else None

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        results: list[dict[str, Any]],
        error: str | None = None,
    ) -> None:
        if status not in RUN_STATUSES - ACTIVE_RUN_STATUSES:
            raise ValueError("Invalid terminal parser run status")
        with self._locked(exclusive=True) as (state, _persisted):
            run = next((row for row in state["runs"] if row.get("id") == run_id), None)
            if run is None:
                return
            run["status"] = status
            run["finishedAt"] = _iso()
            run["results"] = results
            run["error"] = str(error)[:1000] if error else None
            total = len(run.get("sourceIds") or [])
            failed = sum(
                result.get("state") != "ok" or bool(result.get("servingCachedDataset"))
                for result in results
            )
            failed += max(0, total - len(results))
            if status == "failed" and not results:
                failed = total
            run["totalSources"] = total
            run["completedSources"] = total
            run["failedSources"] = failed
            self._write_locked(state)


_STORE = ParserControlStore()


def parser_control_store() -> ParserControlStore:
    return _STORE


def _state_policy(
    *, at: datetime | None = None, store: ParserControlStore | None = None
) -> dict[str, Any]:
    target = store or parser_control_store()
    try:
        state, persisted = target.read_state()
    except ParserControlStorageError as exc:
        # Publication remains operational if the control file is unreadable;
        # the legacy date-bounded environment policy is the explicit fallback.
        # The admin snapshot itself still surfaces the storage failure.
        _log_storage_fallback(
            "publication_policy",
            exc,
            fallback="environment_or_stable_default",
        )
        state, persisted = _default_state(), False
    return _policy_view(state, persisted=persisted, at=at)


def publication_policy_context(
    source_id: str,
    *,
    at: datetime | None = None,
    store: ParserControlStore | None = None,
) -> dict[str, Any]:
    target = store or parser_control_store()
    try:
        state, persisted = target.read_state()
    except ParserControlStorageError as exc:
        _log_storage_fallback(
            "capture_publication_policy",
            exc,
            fallback="environment_or_stable_default",
        )
        state, persisted = _default_state(), False
    view = _policy_view(state, persisted=persisted, at=at)
    effective_mode = (
        str(view.get("effectiveMode") or "stable")
        if source_id in EARLY_SOURCE_IDS
        else "stable"
    )
    managed_by = str(view.get("managedBy") or "default")
    window: dict[str, Any] | None = None
    if effective_mode == "early":
        if managed_by == "environment":
            window = {
                "from": os.environ.get("HS_ARENA_POST_PATCH_FROM", "2026-07-21"),
                "until": os.environ.get("HS_ARENA_POST_PATCH_UNTIL", "2026-07-28"),
                "timezone": "Europe/Warsaw",
            }
        else:
            window = {
                "from": view.get("updatedAt"),
                "until": view.get("earlyUntil"),
                "timezone": "UTC",
            }
    token_payload = {
        "sourceId": source_id,
        "effectiveMode": effective_mode,
        "managedBy": managed_by,
        "policyConfigured": bool(view.get("policyConfigured")),
        "earlyUntil": view.get("earlyUntil"),
        "updatedAt": view.get("updatedAt"),
    }
    token = hashlib.sha256(
        json.dumps(token_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return {
        **token_payload,
        "revision": state["revision"] if persisted else None,
        "token": token,
        "capturedAt": _iso(at),
        "window": window,
    }


def effective_publication_mode(
    source_id: str,
    *,
    at: datetime | None = None,
    store: ParserControlStore | None = None,
) -> str:
    if source_id not in EARLY_SOURCE_IDS:
        return "stable"
    return str(_state_policy(at=at, store=store)["effectiveMode"])


def effective_early_window(
    source_id: str,
    *,
    at: datetime | None = None,
    store: ParserControlStore | None = None,
) -> dict[str, Any] | None:
    if effective_publication_mode(source_id, at=at, store=store) != "early":
        return None
    policy = _state_policy(at=at, store=store)
    if policy.get("managedBy") == "environment":
        return {
            "from": os.environ.get("HS_ARENA_POST_PATCH_FROM", "2026-07-21"),
            "until": os.environ.get("HS_ARENA_POST_PATCH_UNTIL", "2026-07-28"),
            "timezone": "Europe/Warsaw",
            "managedBy": "environment",
        }
    return {
        "from": policy.get("updatedAt"),
        "until": policy.get("earlyUntil"),
        "timezone": "UTC" if policy.get("managedBy") == "control" else "Europe/Warsaw",
        "managedBy": policy.get("managedBy"),
    }


def publication_cache_token(source_id: str, *, at: datetime | None = None) -> str:
    """Return a cheap token that changes whenever public channel selection changes."""
    if source_id not in EARLY_SOURCE_IDS:
        return ""
    context = publication_policy_context(source_id, at=at)
    return f"{context.get('revision') or 'env'}:{context['token']}"


def enabled_section_ids(*, store: ParserControlStore | None = None) -> set[str]:
    target = store or parser_control_store()
    try:
        state, _persisted = target.read_state()
    except ParserControlStorageError as exc:
        _log_storage_fallback(
            "scheduled_sections",
            exc,
            fallback="all_sections_enabled",
        )
        return set(SECTION_BY_ID)
    return {
        section_id
        for section_id, enabled in state["sections"].items()
        if enabled
    }


def is_source_scheduled_enabled(
    source_id: str, *, store: ParserControlStore | None = None
) -> bool:
    section_id = SOURCE_TO_SECTION.get(source_id)
    return bool(
        source_operationally_enabled(source_id)
        and section_id
        and section_id in enabled_section_ids(store=store)
    )


def filter_scheduled_source_ids(
    source_ids: list[str] | None,
    *,
    store: ParserControlStore | None = None,
) -> list[str]:
    enabled = enabled_section_ids(store=store)
    candidates = list(source_ids) if source_ids is not None else list(SOURCE_BY_ID)
    return [
        source_id
        for source_id in candidates
        if source_operationally_enabled(source_id)
        and SOURCE_TO_SECTION.get(source_id) in enabled
    ]


def _is_provisional_dataset(dataset: dict[str, Any]) -> bool:
    data = dataset.get("data") or {}
    return any(
        isinstance(data.get(key), dict) and bool(data[key].get("provisional"))
        for key in ("structured", "hsreplay_extracted")
    )


def dataset_publication_mode(dataset: dict[str, Any]) -> str:
    """Return the immutable mode carried by an already selected publication."""
    return "early" if _is_provisional_dataset(dataset) else "stable"


def resolve_public_dataset(
    source_id: str,
    dataset: dict[str, Any],
    *,
    at: datetime | None = None,
    store: ParserControlStore | None = None,
    effective_mode: str | None = None,
) -> dict[str, Any] | None:
    if not source_operationally_enabled(source_id):
        return None
    if not _is_provisional_dataset(dataset):
        return dataset
    mode = effective_mode or effective_publication_mode(
        source_id, at=at, store=store
    )
    if mode == "early":
        return dataset
    baseline = (store or parser_control_store()).stable_baseline(source_id)
    if baseline is None or _is_provisional_dataset(baseline):
        return None
    return baseline


def load_resolved_public_dataset(
    source_id: str,
    *,
    at: datetime | None = None,
    store: ParserControlStore | None = None,
    effective_mode: str | None = None,
) -> dict[str, Any] | None:
    """Load the public publication channel instead of the mutable candidate."""
    from .storage import load_dataset

    dataset = load_dataset(source_id)
    if not isinstance(dataset, dict):
        return None
    return resolve_public_dataset(
        source_id,
        dataset,
        at=at,
        store=store,
        effective_mode=effective_mode,
    )


def expand_run_selection(
    *, source_ids: list[str] | None, section_ids: list[str] | None
) -> list[str]:
    selected = list(source_ids or [])
    unknown_sections = sorted(set(section_ids or []) - set(SECTION_BY_ID))
    if unknown_sections:
        raise InvalidControlRequest("Unknown sections: " + ", ".join(unknown_sections))
    for section_id in section_ids or []:
        selected.extend(SECTION_BY_ID[section_id].source_ids)
    return sorted(set(selected))


def _run_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    source_id = str(result.get("source_id") or result.get("sourceId") or "")
    errors, errors_total, errors_truncated = _result_errors(result)
    summary = {
        "sourceId": source_id,
        "label": source_label(source_id) if source_id in SOURCE_BY_ID else source_id,
        "state": result.get("state") or ("ok" if result.get("ok") else "error"),
        "fetchedAt": result.get("fetched_at") or result.get("fetchedAt"),
        "detail": result.get("detail") or result.get("error"),
        "errors": errors,
        "errorsTotal": errors_total,
        "errorsTruncated": errors_truncated,
        "servingCachedDataset": bool(
            result.get("serving_cached_dataset") or result.get("servingCachedDataset")
        ),
    }
    rows_total = _result_rows_total(result)
    if rows_total is not None:
        summary["rowsTotal"] = rows_total
    return summary


async def _run_pipeline_source(source_id: str) -> dict[str, Any]:
    if source_id == "hsguru_meta_matrix":
        from .hsguru_meta_matrix import refresh_hsguru_meta_matrix

        result = await refresh_hsguru_meta_matrix()
    elif source_id == "hsreplay_archetypes":
        from .hsreplay_archetypes_db import refresh_hsreplay_archetype_database

        result = await refresh_hsreplay_archetype_database()
    elif source_id == "hsreplay_battlegrounds_hero_details":
        from .hsreplay_bg_hero_details import refresh_bg_hero_details

        result = await refresh_bg_hero_details()
    elif source_id == "hsguru_fun_decks":
        from .fun_decks import refresh_fun_decks

        result = await asyncio.to_thread(refresh_fun_decks)
    elif source_id == "hsguru_archetype_analysis":
        from .hsguru_archetype_analysis import refresh_hsguru_archetype_analysis

        result = await refresh_hsguru_archetype_analysis()
    elif source_id == "hsreplay_battlegrounds_compositions_screenshot":
        from .hsreplay_bg_screenshots import capture_compositions_screenshot

        result = await capture_compositions_screenshot()
    elif source_id == "hsreplay_battlegrounds_minions":
        from .hsreplay_bg_minions_db import refresh_bg_minion_database_sync

        result = await asyncio.to_thread(refresh_bg_minion_database_sync)
    else:
        raise InvalidControlRequest(f"Unsupported pipeline source: {source_id}")
    upstream_state = str(
        result.get("state") or ("ok" if result.get("ok", True) else "error")
    )
    upstream_errors: list[Any] = []
    for key in ("errors", "quality_errors"):
        value = result.get(key)
        if isinstance(value, list):
            upstream_errors.extend(value)
    errors, errors_total, errors_truncated = _normalise_run_errors_with_metadata(
        upstream_errors
    )
    detail = result.get("error") or result.get("detail")
    if not detail and errors:
        detail = "; ".join(errors[:5])
    pipeline_result = {
        "source_id": source_id,
        "state": upstream_state,
        "detail": detail,
        "errors": errors,
        "errors_total": errors_total,
        "errors_truncated": errors_truncated,
        "serving_cached_dataset": bool(
            result.get("serving_cached_dataset")
            or result.get("servingCachedDataset")
        ),
        "fetched_at": result.get("fetched_at") or _iso(),
    }
    rows_total = _result_rows_total(result)
    if rows_total is None:
        pipeline_row_fields = {
            "hsguru_meta_matrix": ("logical_slices",),
            "hsreplay_archetypes": ("archetypes_ok",),
            "hsreplay_battlegrounds_hero_details": ("heroes",),
            "hsguru_fun_decks": ("fun_retained",),
            "hsguru_archetype_analysis": ("archetypes",),
            "hsreplay_battlegrounds_minions": ("minions_ok", "minions_total"),
        }
        rows_total = next(
            (
                value
                for field in pipeline_row_fields.get(source_id, ())
                if (value := _non_negative_int(result.get(field))) is not None
            ),
            None,
        )
    if rows_total is not None:
        pipeline_result["rows_total"] = rows_total
    return pipeline_result


async def execute_parser_run(source_ids: list[str]) -> list[dict[str, Any]]:
    from .fetcher import refresh_sources

    scrape_ids = [source_id for source_id in source_ids if SOURCE_BY_ID[source_id].kind == "scrape"]
    pipeline_ids = [source_id for source_id in source_ids if SOURCE_BY_ID[source_id].kind == "pipeline"]
    results: list[dict[str, Any]] = []
    if scrape_ids:
        results.extend(await refresh_sources(scrape_ids))
    for source_id in pipeline_ids:
        results.append(await _run_pipeline_source(source_id))
    # This source has two public products: the cached source dataset and the
    # SQLite-backed /v1/bg/minions API. One manual control-plane action updates
    # both and reports a single aggregate source result.
    bg_minions_id = "hsreplay_battlegrounds_minions"
    if bg_minions_id in source_ids:
        database_result = await _run_pipeline_source(bg_minions_id)
        base = next(
            (
                result
                for result in results
                if str(result.get("source_id") or result.get("sourceId") or "")
                == bg_minions_id
            ),
            None,
        )
        if base is None:
            results.append(database_result)
        else:
            base_ok = (
                base.get("state") == "ok"
                and not base.get("serving_cached_dataset")
            )
            database_ok = database_result.get("state") == "ok"
            base["state"] = (
                "ok" if base_ok and database_ok
                else "partial" if base_ok or database_ok
                else "error"
            )
            details = [
                str(detail)
                for detail in (base.get("detail"), database_result.get("detail"))
                if detail
            ]
            base["detail"] = "; ".join(details) or None
            base["database_state"] = database_result.get("state")
            errors, errors_total, errors_truncated = _merge_result_errors(
                base, database_result
            )
            base["errors"] = errors
            base["errors_total"] = errors_total
            base["errors_truncated"] = errors_truncated
            if _result_rows_total(base) is None:
                database_rows_total = _result_rows_total(database_result)
                if database_rows_total is not None:
                    base["rows_total"] = database_rows_total
    return results


class ParserRunWorker:
    def __init__(
        self,
        store: ParserControlStore,
        *,
        executor: Callable[[list[str]], Any] = execute_parser_run,
    ) -> None:
        self.store = store
        self.executor = executor
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._start_lock = threading.Lock()

    def start(self) -> None:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            try:
                self.store.recover_interrupted_runs()
            except ParserControlStorageError as exc:
                _log_storage_fallback(
                    "parser_run_worker_start",
                    exc,
                    fallback="worker_inactive",
                )
                return
            self._thread = threading.Thread(
                target=self._loop,
                name="parser-control-worker",
                daemon=True,
            )
            self._thread.start()

    def enqueue(
        self,
        *,
        source_ids: list[str],
        requested_by: str,
        reason: str | None,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        run, deduplicated = self.store.enqueue_run(
            source_ids=source_ids,
            requested_by=requested_by,
            reason=reason,
            request_id=request_id,
        )
        self.start()
        self._wake.set()
        return run, deduplicated

    def process_next(self) -> bool:
        """Process one durable run and persist progress after every source."""
        run = self.store.claim_next_run()
        if run is None:
            return False
        run_id = str(run["id"])
        for dependency_result in self.store.dependency_results(run_id):
            self.store.record_run_result(run_id, dependency_result)
        persisted_at_start = self.store.get_run(run_id) or _run_public_view(run)
        completed = {
            str(result.get("sourceId") or result.get("source_id") or "")
            for result in (persisted_at_start.get("results") or [])
            if isinstance(result, dict)
        }
        execution_source_ids = run.get("executionSourceIds")
        if not isinstance(execution_source_ids, list):
            execution_source_ids = run.get("sourceIds") or []
        for source_id in execution_source_ids:
            if source_id in completed:
                continue
            source_started_ms = _monotonic_ms()
            try:
                raw_results = asyncio.run(self.executor([source_id]))
                raw_result = next(
                    (
                        row
                        for row in raw_results
                        if str(row.get("source_id") or row.get("sourceId") or "")
                        == source_id
                    ),
                    raw_results[0] if raw_results else None,
                )
                if raw_result is None:
                    raise RuntimeError("Parser returned no result for the source")
                summary = _run_result_summary(raw_result)
                summary["sourceId"] = source_id
            except Exception as exc:
                detail = f"{type(exc).__name__}: {str(exc)[:900]}"
                summary = {
                    "sourceId": source_id,
                    "label": source_label(source_id),
                    "state": "error",
                    "fetchedAt": None,
                    "detail": detail,
                    "errors": [detail],
                    "servingCachedDataset": False,
                }
            summary["durationMs"] = max(
                0, round(_monotonic_ms() - source_started_ms)
            )
            self.store.record_run_result(run_id, summary)

        persisted = self.store.get_run(run_id) or _run_public_view(run)
        summaries = list(persisted.get("results") or [])
        fresh_count = sum(
            result.get("state") == "ok" and not result.get("servingCachedDataset")
            for result in summaries
        )
        usable_count = sum(
            result.get("state") in {"ok", "partial"}
            or bool(result.get("servingCachedDataset"))
            for result in summaries
        )
        expected_count = len(run.get("sourceIds") or [])
        if (
            summaries
            and len(summaries) == expected_count
            and fresh_count == expected_count
        ):
            terminal = "succeeded"
        elif usable_count:
            terminal = "partial"
        else:
            terminal = "failed"
        self.store.finish_run(run_id, status=terminal, results=summaries)
        return True

    def _loop(self) -> None:
        while True:
            try:
                processed = self.process_next()
            except ParserControlStorageError as exc:
                _log_storage_fallback(
                    "parser_run_worker_loop",
                    exc,
                    fallback="retry_after_backoff",
                )
                processed = False
            if not processed:
                self._wake.wait(timeout=30.0)
                self._wake.clear()


_RUN_WORKER = ParserRunWorker(_STORE)


def parser_run_worker() -> ParserRunWorker:
    return _RUN_WORKER
