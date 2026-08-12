from __future__ import annotations

import fcntl
import gzip
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import (
    build_id,
    log_retention_archives,
    log_retention_days,
    log_rotate_max_age_days,
    log_rotate_max_bytes,
    stale_dataset_hours,
)
from .hsreplay_auth_status import hsreplay_auth_status
from .source_state import ERROR_STATES, WARN_STATES, SourceState
from .storage import load_status, root_dir

_logger = logging.getLogger(__name__)
_write_lock = threading.Lock()
_MAX_ARCHIVE_PRUNES_PER_PASS = 100

_current_phase: ContextVar[str | None] = ContextVar("refresh_phase", default=None)
_current_run_id: ContextVar[str | None] = ContextVar("refresh_run_id", default=None)
_current_trace_id: ContextVar[str | None] = ContextVar("refresh_trace_id", default=None)
_current_source_id: ContextVar[str | None] = ContextVar("refresh_source_id", default=None)
_step_counter: ContextVar[int] = ContextVar("refresh_step", default=0)

# Human-readable action names grouped for UI filters.
ACTION_GROUPS: dict[str, str] = {
    "refresh.begin": "refresh",
    "refresh.end": "refresh",
    "phase.begin": "phase",
    "phase.end": "phase",
    "proxy.health.begin": "proxy",
    "proxy.health.ok": "proxy",
    "proxy.health.fail": "proxy",
    "routing.proxy_probe.fail": "proxy",
    "routing.proxy_circuit.skip": "proxy",
    "source.begin": "source",
    "source.end": "source",
    "source.complete": "source",
    "api.route.begin": "api",
    "api.route.ok": "api",
    "api.route.fail": "api",
    "api.route.skip": "api",
    "routing.channel.ok": "api",
    "routing.channel.fail": "api",
    "api.validate.ok": "quality",
    "api.validate.fail": "quality",
    "api.fallback.browser": "api",
    "api.fallback.blocked": "api",
    "http.request.begin": "http",
    "http.request.ok": "http",
    "http.request.fail": "http",
    "browser.fetch.begin": "browser",
    "browser.fetch.end": "browser",
    "browser.round.begin": "browser",
    "browser.backend.skip": "browser",
    "browser.backend.try": "browser",
    "browser.backend.ok": "browser",
    "browser.backend.fail": "browser",
    "browser.page.shell_reject": "browser",
    "browser.quality.fail": "browser",
    "parse.html": "parse",
    "quality.validate.ok": "quality",
    "quality.validate.fail": "quality",
    "quality.field_fill.warn": "quality",
    "quality.regression.warn": "quality",
    "ai.review.pass": "ai_review",
    "ai.review.fail": "ai_review",
    "ai.review.uncertain": "ai_review",
    "ai.review.quarantine": "ai_review",
    "ai.review.error": "ai_review",
    "ai.review.skipped": "ai_review",
    "ai.diagnosis.healthy": "ai_diagnosis",
    "ai.diagnosis.anomalous": "ai_diagnosis",
    "ai.diagnosis.inconclusive": "ai_diagnosis",
    "ai.diagnosis.error": "ai_diagnosis",
    "ai.diagnosis.skipped": "ai_diagnosis",
    "source_contract.validate.fail": "quality",
    "source_semantic.validate.fail": "quality",
    "dataset.save": "storage",
    "dataset.save.skip": "storage",
    "dataset.save.skip_regression": "storage",
    "dataset.preserve_previous_good": "storage",
    "dataset.db_store.fail": "storage",
    "dataset.cached.preserve": "cache",
    "dataset.cached.invalid": "cache",
    "dataset.cached_after_failure.alert": "cache",
    "dataset.stale.warn": "stale",
    "dataset.stale.alert": "stale",
    "protection.cloudflare": "protection",
    "http.status.error": "http",
    "auth.hsreplay.relogin": "auth",
    "alert.sent": "alert",
    "alert.skipped": "alert",
    "alert.failed": "alert",
    "firestone.static.try": "firestone",
    "firestone.static.ok": "firestone",
    "firestone.static.fail": "firestone",
    "flaresolverr.session.open": "flaresolverr",
    "flaresolverr.session.close": "flaresolverr",
    "preflight.begin": "preflight",
    "preflight.ok": "preflight",
    "preflight.fail": "preflight",
    "preflight.proxy.skip": "preflight",
    "preflight.proxy.fail": "preflight",
    "preflight.flaresolverr.ok": "preflight",
    "preflight.flaresolverr.fail": "preflight",
    "preflight.hsreplay.ok": "preflight",
    "preflight.hsreplay.warn": "preflight",
    "canary.begin": "canary",
    "canary.ok": "canary",
    "canary.fail": "canary",
    "premium_auth.endpoint.fail": "auth",
}


def events_path() -> Path:
    return root_dir() / "logs" / "refresh-events.jsonl"


def _rotation_started_at_path(path: Path | None = None) -> Path:
    active_path = path or events_path()
    return active_path.with_name(f".{active_path.name}.started-at")


@contextmanager
def _event_log_process_lock() -> Iterator[None]:
    """Serialize rotation and append across API and scheduled-job processes."""
    lock_path = events_path().with_name(".refresh-events.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)


def _event_log_paths() -> list[Path]:
    path = events_path()
    log_dir = path.parent
    rotated = (
        sorted(log_dir.glob("refresh-events.*.jsonl.gz"))[
            -log_retention_archives():
        ]
        if log_dir.exists()
        else []
    )
    return [*rotated, path]


def _prune_rotated_events_logs(*, now: float | None = None) -> int:
    """Delete a bounded batch of expired/surplus compressed archives."""
    log_dir = events_path().parent
    if not log_dir.exists():
        return 0
    archives_with_mtime: list[tuple[float, Path]] = []
    for candidate in log_dir.glob("refresh-events.*.jsonl.gz"):
        try:
            archives_with_mtime.append((candidate.stat().st_mtime, candidate))
        except OSError:
            continue
    archives = sorted(archives_with_mtime, reverse=True)
    keep_count = log_retention_archives()
    cutoff = (time.time() if now is None else now) - log_retention_days() * 86400
    candidates: list[tuple[float, Path]] = []
    for index, (mtime, archive) in enumerate(archives):
        expired = mtime < cutoff
        surplus = index >= keep_count
        if expired or surplus:
            candidates.append((mtime, archive))
    removed = 0
    for _mtime, archive in sorted(candidates)[:_MAX_ARCHIVE_PRUNES_PER_PASS]:
        try:
            archive.unlink()
            removed += 1
        except OSError as exc:
            _logger.warning("Failed to prune refresh log archive %s: %s", archive, exc)
    return removed


def _write_rotation_started_at(path: Path, started_at: float) -> None:
    """Atomically persist when the current active-log period began."""
    marker = _rotation_started_at_path(path)
    temporary = marker.with_name(f"{marker.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="ascii") as stream:
            stream.write(f"{started_at:.6f}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _rotation_started_at(path: Path, *, now: float) -> float:
    marker = _rotation_started_at_path(path)
    if not path.exists():
        try:
            _write_rotation_started_at(path, now)
        except OSError as exc:
            _logger.warning(
                "Failed to persist refresh log rotation state: %s",
                type(exc).__name__,
            )
        return now

    try:
        started_at = float(marker.read_text(encoding="ascii").strip())
        if 0 < started_at <= now:
            return started_at
    except (OSError, ValueError):
        pass

    try:
        started_at = min(path.stat().st_mtime, now) if path.exists() else now
        _write_rotation_started_at(path, started_at)
    except OSError as exc:
        _logger.warning(
            "Failed to persist refresh log rotation state: %s",
            type(exc).__name__,
        )
    return started_at


def _maintain_events_log(*, now: float | None = None) -> None:
    """Prune archives and rotate the active JSONL when its period expires."""
    current_time = time.time() if now is None else now
    removed = _prune_rotated_events_logs(now=current_time)
    if removed:
        _logger.info("Pruned %s expired refresh log archives", removed)

    path = events_path()
    if not path.exists():
        _rotation_started_at(path, now=current_time)
        return
    try:
        stat = path.stat()
    except OSError:
        return
    age_days = max(0.0, current_time - _rotation_started_at(path, now=current_time)) / 86400
    if stat.st_size < log_rotate_max_bytes() and age_days < log_rotate_max_age_days():
        return
    stamp = datetime.fromtimestamp(current_time, tz=UTC).strftime("%Y%m%dT%H%M%S%f")
    rotated = path.with_name(f"refresh-events.{stamp}.jsonl.gz")
    temporary = rotated.with_name(f".{rotated.name}.{uuid.uuid4().hex}.tmp")
    try:
        with path.open("rb") as src, gzip.open(temporary, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(temporary, rotated)
        path.unlink()
        _rotation_started_at(path, now=current_time)
        removed = _prune_rotated_events_logs(now=current_time)
        _logger.info("Rotated refresh log to %s", rotated)
        if removed:
            _logger.info("Pruned %s expired refresh log archives", removed)
    except OSError as exc:
        _logger.warning("Failed to rotate refresh log: %s", type(exc).__name__)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def maybe_rotate_events_log(*, now: float | None = None) -> None:
    """Process-safe, fail-open structured-log maintenance entry point."""
    try:
        with _write_lock, _event_log_process_lock():
            _maintain_events_log(now=now)
    except (OSError, TypeError, ValueError) as exc:
        _logger.warning(
            "Structured refresh log maintenance failed: %s",
            type(exc).__name__,
        )


def set_refresh_context(*, phase: str | None = None, run_id: str | None = None) -> None:
    if phase is not None:
        _current_phase.set(phase)
    if run_id is not None:
        _current_run_id.set(run_id)


def new_run_id() -> str:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + f"-{uuid.uuid4().hex[:12]}"
    _current_run_id.set(run_id)
    return run_id


def new_trace_id(source_id: str) -> str:
    short = uuid.uuid4().hex[:8]
    return f"{source_id[:24]}-{short}"


@lru_cache(maxsize=1)
def runtime_version_info() -> dict[str, Any]:
    app_root = Path(__file__).resolve().parents[1]
    info: dict[str, Any] = {
        "app": app_root.name,
        "build_id": build_id(),
        "git_commit": None,
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(app_root), "rev-parse", "--short", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            info["git_commit"] = result.stdout.strip() or None
    except Exception:
        pass
    return info


def _next_step() -> int:
    value = _step_counter.get() + 1
    _step_counter.set(value)
    return value


def _level_for(state: str | None, level: str | None, error_type: str | None) -> str:
    if level:
        return level
    if error_type or state in ERROR_STATES:
        return "error"
    if state in WARN_STATES:
        return "warn"
    return "info"


def log_action(
    action: str,
    *,
    level: str | None = None,
    source_id: str | None = None,
    trace_id: str | None = None,
    state: str | None = None,
    backend: str | None = None,
    duration_ms: float | None = None,
    detail: str | None = None,
    error_type: str | None = None,
    tier: str | None = None,
    step: int | None = None,
    http_status: int | None = None,
    url: str | None = None,
    bytes_out: int | None = None,
    attempt: int | None = None,
    event: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Structured action log (JSONL). Prefer this over log_event for new code.

    `action` — dotted name, e.g. browser.backend.try
    `event` — legacy short name; defaults to last segment of action
    """
    resolved_source = source_id or _current_source_id.get()
    resolved_trace = trace_id or _current_trace_id.get()
    resolved_step = step if step is not None else (_next_step() if resolved_trace else None)
    resolved_level = _level_for(state, level, error_type)
    legacy_event = event or action.split(".")[-1] if action else "unknown"

    row: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "level": resolved_level,
        "action": action,
        "action_group": ACTION_GROUPS.get(action, action.split(".")[0] if action else "other"),
        "event": legacy_event,
        "run_id": _current_run_id.get(),
        "trace_id": resolved_trace,
        "phase": _current_phase.get(),
        "step": resolved_step,
        "source_id": resolved_source,
        "tier": tier,
        "state": state,
        "backend": backend,
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "error_type": error_type,
        "http_status": http_status,
        "url": (url or "")[:500] or None,
        "bytes": bytes_out,
        "attempt": attempt,
        "detail": (detail or "")[:4000] or None,
    }
    if extra:
        row["extra"] = extra

    try:
        path = events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with _write_lock, _event_log_process_lock():
            _maintain_events_log()
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except (OSError, TypeError, ValueError) as exc:
        # Observability must never turn an otherwise successful refresh into a
        # failed one. Log only the bounded exception type; payloads may contain
        # URLs or provider details that must not leak to the fallback channel.
        _logger.warning(
            "Structured refresh log write failed: %s",
            type(exc).__name__,
        )

    msg = f"[{resolved_level}] {action} source={resolved_source} {detail or ''}"[:500]
    if resolved_level == "error":
        _logger.error(msg)
    elif resolved_level == "warn":
        _logger.warning(msg)
    else:
        _logger.info(msg)

    return row


def log_event(
    event: str,
    *,
    source_id: str | None = None,
    state: str | None = None,
    backend: str | None = None,
    duration_ms: float | None = None,
    detail: str | None = None,
    error_type: str | None = None,
    tier: str | None = None,
    extra: dict[str, Any] | None = None,
    level: str | None = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper — maps legacy event names to actions."""
    action_map = {
        "refresh_start": "refresh.begin",
        "refresh_end": "refresh.end",
        "phase_start": "phase.begin",
        "phase_end": "phase.end",
        "source_start": "source.begin",
        "source_end": "source.complete",
        "api_start": "api.route.begin",
        "api_ok": "api.route.ok",
        "api_fail": "api.route.fail",
        "api_quality_fail": "api.validate.fail",
        "browser_fallback": "api.fallback.browser",
        "browser_fallback_blocked": "api.fallback.blocked",
        "backend_ok": "browser.backend.ok",
        "backend_fail": "browser.backend.fail",
    }
    action = action_map.get(event, f"legacy.{event}")
    return log_action(
        action,
        event=event,
        level=level,
        source_id=source_id,
        state=state,
        backend=backend,
        duration_ms=duration_ms,
        detail=detail,
        error_type=error_type,
        tier=tier,
        extra=extra,
    )


def activate_source_trace(
    source_id: str,
    *,
    tier: str | None = None,
    url: str | None = None,
) -> tuple[str, Token, Token, Token]:
    """Start per-source trace; returns (trace_id, tokens for reset)."""
    trace_id = new_trace_id(source_id)
    tok_trace = _current_trace_id.set(trace_id)
    tok_source = _current_source_id.set(source_id)
    tok_step = _step_counter.set(0)
    try:
        log_action(
            "source.begin",
            level="info",
            source_id=source_id,
            trace_id=trace_id,
            tier=tier,
            url=url,
            step=1,
        )
    except Exception as exc:
        # Context tokens have already been installed. Source-begin telemetry is
        # best effort so callers can always receive and reset those tokens.
        _logger.debug("Source trace begin telemetry failed for %s: %s", source_id, exc)
    return trace_id, tok_trace, tok_source, tok_step


def deactivate_source_trace(tokens: tuple[Token, Token, Token]) -> None:
    tok_trace, tok_source, tok_step = tokens
    _current_trace_id.reset(tok_trace)
    _current_source_id.reset(tok_source)
    _step_counter.reset(tok_step)


@contextmanager
def source_trace(
    source_id: str,
    *,
    tier: str | None = None,
    url: str | None = None,
) -> Iterator[str]:
    """Per-source correlation ID and step counter for a full fetch lifecycle."""
    trace_id, tok_trace, tok_source, tok_step = activate_source_trace(
        source_id, tier=tier, url=url
    )
    try:
        yield trace_id
    finally:
        deactivate_source_trace((tok_trace, tok_source, tok_step))


def complete_source_trace(
    source_id: str,
    status: dict[str, Any],
    *,
    tier: str | None = None,
    started_monotonic: float,
    trace_id: str | None = None,
) -> None:
    log_action(
        "source.complete",
        level="info" if status.get("state") == SourceState.OK else "error",
        source_id=source_id,
        trace_id=trace_id or _current_trace_id.get(),
        state=status.get("state"),
        backend=status.get("backend"),
        duration_ms=(time.monotonic() - started_monotonic) * 1000,
        error_type=status.get("error"),
        detail=status.get("detail"),
        tier=tier,
        http_status=status.get("http_status"),
        url=status.get("final_url"),
        bytes_out=status.get("content_length"),
    )


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_events(
    *,
    limit: int = 200,
    source_id: str | None = None,
    event: str | None = None,
    action: str | None = None,
    action_group: str | None = None,
    level: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    since_hours: float | None = None,
) -> list[dict[str, Any]]:
    paths = [item for item in _event_log_paths() if item.exists()]
    if not paths:
        return []

    cutoff: datetime | None = None
    if since_hours is not None:
        cutoff = datetime.now(UTC) - timedelta(hours=since_hours)

    rows: list[dict[str, Any]] = []
    try:
        for path in paths:
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if source_id and row.get("source_id") != source_id:
                        continue
                    if event and row.get("event") != event:
                        continue
                    if action and row.get("action") != action:
                        continue
                    if action_group and row.get("action_group") != action_group:
                        continue
                    if level and row.get("level") != level:
                        continue
                    if trace_id and row.get("trace_id") != trace_id:
                        continue
                    if run_id and row.get("run_id") != run_id:
                        continue
                    if cutoff is not None:
                        ts = _parse_ts(str(row.get("ts", "")))
                        if ts is None or ts < cutoff:
                            continue
                    rows.append(row)
    except OSError:
        return []

    return rows[-limit:]


def build_trace_timeline(trace_id: str) -> dict[str, Any]:
    events = read_events(limit=500, trace_id=trace_id)
    if not events:
        return {"trace_id": trace_id, "events": [], "found": False}
    return {
        "trace_id": trace_id,
        "found": True,
        "source_id": events[0].get("source_id"),
        "run_id": events[0].get("run_id"),
        "events": events,
        "final_state": next(
            (e.get("state") for e in reversed(events) if e.get("action") == "source.complete"),
            None,
        ),
    }


def build_run_timeline(run_id: str) -> dict[str, Any]:
    events = read_events(limit=5000, run_id=run_id)
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    phases: list[dict[str, Any]] = []
    for row in events:
        if row.get("action", "").startswith("phase."):
            phases.append(row)
        sid = row.get("source_id")
        if sid:
            by_source[sid].append(row)
    failed = [
        sid
        for sid, evs in by_source.items()
        if any(e.get("action") == "source.complete" and e.get("state") != SourceState.OK for e in evs)
    ]
    return {
        "run_id": run_id,
        "found": bool(events),
        "events_total": len(events),
        "phases": phases,
        "sources_touched": len(by_source),
        "failed_sources": failed,
        "events": events[-500:],
    }


def _stale_sources() -> list[dict[str, Any]]:
    from .stale_monitor import find_stale_sources

    return find_stale_sources(include_ok=True)


def build_summary(*, since_hours: float = 24.0) -> dict[str, Any]:
    from .source_contracts import get_contract
    from .sources import SOURCES

    events = read_events(limit=10000, since_hours=since_hours)
    by_event = Counter(e.get("event") for e in events)
    by_action = Counter(e.get("action") for e in events)
    by_level = Counter(e.get("level") for e in events)
    by_group = Counter(e.get("action_group") for e in events)
    by_state = Counter(e.get("state") for e in events if e.get("state"))

    last_end_by_source: dict[str, dict[str, Any]] = {}
    last_trace_by_source: dict[str, str] = {}
    failure_counts: Counter[str] = Counter()
    preserved_counts: Counter[str] = Counter()
    backend_failures: Counter[str] = Counter()
    action_errors: Counter[str] = Counter()
    db_store_failures: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []
    last_traffic: dict[str, Any] | None = None

    for row in events:
        if row.get("action") == "refresh.end":
            traffic = (row.get("extra") or {}).get("traffic")
            if isinstance(traffic, dict):
                last_traffic = traffic
        sid = row.get("source_id")
        if not sid:
            continue
        if row.get("trace_id"):
            last_trace_by_source[sid] = str(row["trace_id"])
        if row.get("action") == "source.complete":
            last_end_by_source[sid] = row
        if row.get("level") == "error" or row.get("state") not in (None, SourceState.OK):
            failure_counts[sid] += 1
        if row.get("action") == "dataset.preserve_previous_good":
            preserved_counts[sid] += 1
        if row.get("action") == "browser.backend.fail" and row.get("backend"):
            backend_failures[str(row["backend"])] += 1
        if row.get("level") == "error" and row.get("action"):
            action_errors[str(row["action"])] += 1
        if row.get("action") == "dataset.db_store.fail" and sid:
            db_store_failures[sid] += 1
        if row.get("action") == "source.complete" and row.get("state") not in (None, SourceState.OK):
            failures.append(row)

    statuses = [load_status(s.id) for s in SOURCES]
    state_now: Counter[str] = Counter()
    cached_now: list[str] = []
    live_failed_cached_now: list[str] = []
    for st in statuses:
        state_now[st.get("state") if st else SourceState.NEVER_FETCHED] += 1
        if st and st.get("serving_cached_dataset"):
            source_id = str(st.get("source_id") or "")
            if source_id:
                cached_now.append(source_id)
            if st.get("last_refresh_state") not in (None, SourceState.OK):
                live_failed_cached_now.append(source_id)

    stale_sources = _stale_sources()
    stale_ids = {str(item.get("source_id")) for item in stale_sources}

    vulnerabilities: list[dict[str, Any]] = []
    weak_sources: list[dict[str, Any]] = []
    for source in SOURCES:
        st = load_status(source.id) or {}
        last_log = last_end_by_source.get(source.id)
        quality_score = st.get("quality_score")
        failures_24h = failure_counts.get(source.id, 0)
        preserved_24h = preserved_counts.get(source.id, 0)
        contract = get_contract(source.id)
        risk = "low"
        if st.get("state") != SourceState.OK or st.get("serving_cached_dataset") or source.id in stale_ids:
            risk = "high"
        elif (
            failures_24h >= 3
            or preserved_24h
            or isinstance(quality_score, (int, float))
            and quality_score < 0.85
        ):
            risk = "medium"
        vulnerabilities.append(
            {
                "source_id": source.id,
                "site": source.site,
                "state": st.get("state", SourceState.NEVER_FETCHED),
                "fetched_at": st.get("fetched_at"),
                "backend": st.get("backend"),
                "serving_cached_dataset": bool(st.get("serving_cached_dataset")),
                "last_refresh_state": st.get("last_refresh_state"),
                "last_refresh_at": st.get("last_refresh_at"),
                "is_stale": source.id in stale_ids,
                "detail_preview": (st.get("detail") or "")[:240] or None,
                "failures_24h": failures_24h,
                "last_trace_id": last_trace_by_source.get(source.id),
                "last_run_state": last_log.get("state") if last_log else None,
                "last_run_ts": last_log.get("ts") if last_log else None,
                "last_duration_ms": last_log.get("duration_ms") if last_log else None,
            }
        )
        if risk != "low" or contract is not None:
            weak_sources.append(
                {
                    "source_id": source.id,
                    "site": source.site,
                    "risk": risk,
                    "failures_24h": failures_24h,
                    "last_backend": st.get("backend"),
                    "quality_score": quality_score,
                    "rows_total": st.get("rows_total"),
                    "preserved_count_24h": preserved_24h,
                    "serving_cached_dataset": bool(st.get("serving_cached_dataset")),
                    "is_stale": source.id in stale_ids,
                    "state": st.get("state", SourceState.NEVER_FETCHED),
                    "recommendation": (contract.recommendation if contract else None)
                    or ("Investigate recent errors in trace timeline" if failures_24h else "Monitor source"),
                }
            )

    vulnerabilities.sort(
        key=lambda v: (
            0 if v["state"] != SourceState.OK else 1,
            -v["failures_24h"],
            v["source_id"],
        )
    )

    recent_failures = sorted(failures, key=lambda r: str(r.get("ts", "")), reverse=True)[:50]
    weak_sources.sort(
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}.get(str(item.get("risk")), 3),
            -int(item.get("failures_24h") or 0),
            str(item.get("source_id")),
        )
    )

    runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        rid = row.get("run_id")
        if rid and row.get("action") == "phase.end":
            runs[rid].append(row)
    last_runs = sorted(
        (
            {
                "run_id": rid,
                "phases": sorted(phase_rows, key=lambda r: str(r.get("ts", ""))),
            }
            for rid, phase_rows in runs.items()
        ),
        key=lambda item: str(item["phases"][-1].get("ts", "")) if item["phases"] else "",
        reverse=True,
    )[:10]

    return {
        "since_hours": since_hours,
        "hsreplay_auth": hsreplay_auth_status(),
        "stale_datasets": stale_sources,
        "stale_hours_threshold": stale_dataset_hours(),
        "cached_sources": cached_now,
        "cached_after_failure_sources": live_failed_cached_now,
        "freshness": {
            # Phase 5: the former orphan pipelines (hsreplay_archetypes,
            # hsreplay_battlegrounds_hero_details) are registered in SOURCES
            # with their own stale_hours, so orphan statuses no longer need a
            # freshness carve-out; any remaining orphan is a real problem.
            "ok": not stale_sources and not cached_now,
            "stale_count": len(stale_sources),
            "cached_count": len(cached_now),
            "cached_after_failure_count": len(live_failed_cached_now),
        },
        "events_total": len(events),
        "events_by_type": dict(by_event),
        "events_by_action": dict(by_action.most_common(30)),
        "events_by_level": dict(by_level),
        "events_by_group": dict(by_group.most_common(20)),
        "terminal_states": dict(by_state),
        "sources_by_state": dict(state_now),
        "backend_failures": dict(backend_failures.most_common(15)),
        "action_errors": dict(action_errors.most_common(20)),
        "db_store_failures": dict(db_store_failures.most_common(20)),
        "vulnerabilities": vulnerabilities,
        "weak_sources": weak_sources,
        "recent_failures": recent_failures,
        "last_runs": last_runs,
        "last_traffic": last_traffic,
        "log_path": str(events_path()),
        "action_groups": sorted(set(ACTION_GROUPS.values())),
    }
