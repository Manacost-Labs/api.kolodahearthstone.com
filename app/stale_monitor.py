from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .config import stale_dataset_hours
from .parser_control import resolve_public_dataset
from .source_state import SourceState
from .sources import SOURCES
from .storage import load_dataset, load_status


def _age_hours(fetched_at: str | None) -> float | None:
    if not fetched_at:
        return None
    try:
        ts = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return (datetime.now(UTC) - ts).total_seconds() / 3600
    except ValueError:
        return None


def find_stale_sources(*, include_ok: bool = True) -> list[dict[str, Any]]:
    """Sources with dataset or status older than the per-source threshold.

    The threshold is ``source.stale_hours`` when set (slow-cadence pipeline
    sources) and the global ``HS_STALE_HOURS`` otherwise.
    """
    default_limit_h = stale_dataset_hours()
    now_label = datetime.now(UTC).isoformat()
    stale: list[dict[str, Any]] = []
    configured = {s.id for s in SOURCES}

    for source in SOURCES:
        limit_h = getattr(source, "stale_hours", None) or default_limit_h
        corrupt_parts: list[str] = []
        try:
            st = load_status(source.id) or {}
        except (OSError, UnicodeError, ValueError):
            st = {}
            corrupt_parts.append("status")
        state = st.get("state") or SourceState.NEVER_FETCHED
        if state == SourceState.OK and not include_ok:
            continue

        fetched_at = st.get("fetched_at")
        try:
            candidate = load_dataset(source.id)
        except (OSError, UnicodeError, ValueError):
            candidate = None
            corrupt_parts.append("dataset")
        ds = (
            resolve_public_dataset(source.id, candidate)
            if isinstance(candidate, dict)
            else None
        )
        public_dataset_unavailable = candidate is not None and ds is None
        ds_fetched = ds.get("fetched_at") if ds else None
        age_status = _age_hours(fetched_at)
        age_dataset = _age_hours(ds_fetched)
        age_h = age_status
        if age_dataset is not None and (age_h is None or age_dataset > age_h):
            age_h = age_dataset
            fetched_at = ds_fetched

        if corrupt_parts:
            stale.append(
                {
                    "source_id": source.id,
                    "state": state,
                    "dataset_age_hours": round(age_h, 1) if age_h is not None else None,
                    "fetched_at": fetched_at,
                    "checked_at": now_label,
                    "reason": f"{'_and_'.join(corrupt_parts)}_corrupt",
                }
            )
            continue

        if public_dataset_unavailable:
            stale.append(
                {
                    "source_id": source.id,
                    "state": state,
                    "dataset_age_hours": None,
                    "fetched_at": None,
                    "checked_at": now_label,
                    "reason": "public_dataset_unavailable",
                }
            )
            continue

        if age_h is None or age_h < limit_h:
            continue
        live_failed_cache = bool(st.get("serving_cached_dataset")) and st.get("last_refresh_state") not in (
            None,
            SourceState.OK,
        )
        if state == SourceState.OK and include_ok:
            stale.append(
                {
                    "source_id": source.id,
                    "state": state,
                    "last_refresh_state": st.get("last_refresh_state"),
                    "dataset_age_hours": round(age_h, 1),
                    "fetched_at": fetched_at,
                    "checked_at": now_label,
                    "reason": "live_failed_cached" if live_failed_cache else "ok_but_stale",
                }
            )
        elif state != SourceState.OK:
            stale.append(
                {
                    "source_id": source.id,
                    "state": state,
                    "dataset_age_hours": round(age_h, 1),
                    "fetched_at": fetched_at,
                    "checked_at": now_label,
                    "reason": "error_with_stale_cache",
                }
            )

    # Orphan status files (not in SOURCES) — reported for cleanup only
    from .config import data_dir

    status_dir = data_dir() / "statuses"
    if status_dir.is_dir():
        for path in status_dir.glob("*.json"):
            sid = path.stem
            if sid in configured:
                continue
            try:
                st = load_status(sid) or {}
            except (OSError, UnicodeError, ValueError):
                stale.append(
                    {
                        "source_id": sid,
                        "state": "orphan",
                        "dataset_age_hours": None,
                        "fetched_at": None,
                        "checked_at": now_label,
                        "reason": "orphan_status_corrupt",
                    }
                )
                continue
            age_h = _age_hours(st.get("fetched_at"))
            if age_h is not None and age_h >= default_limit_h:
                stale.append(
                    {
                        "source_id": sid,
                        "state": st.get("state", "orphan"),
                        "dataset_age_hours": round(age_h, 1),
                        "fetched_at": st.get("fetched_at"),
                        "checked_at": now_label,
                        "reason": "orphan_status",
                    }
                )
    return stale


def _stale_alert_state(item: dict[str, Any]) -> str:
    is_live_failed_cache = item.get("reason") == "live_failed_cached"
    base = "stale_ok" if item.get("state") == SourceState.OK and not is_live_failed_cache else "stale_data"
    try:
        age_h = float(item.get("dataset_age_hours") or 0)
    except (TypeError, ValueError):
        return base
    if age_h >= 48:
        return f"{base}_48h"
    if age_h >= 24:
        return f"{base}_24h"
    return base


async def alert_stale_sources() -> int:
    """Send Telegram for stale ok sources and error+cache; return alert count."""
    from .fetcher import send_telegram_alert
    from .refresh_log import log_action

    sent = 0
    for item in find_stale_sources(include_ok=True):
        source_id = item["source_id"]
        reason = item.get("reason", "stale")
        if reason == "orphan_status":
            continue
        source = next((s for s in SOURCES if s.id == source_id), None)
        if source is None:
            continue
        state = _stale_alert_state(item)
        detail = (
            f"Dataset stale ({item['dataset_age_hours']}h > {stale_dataset_hours()}h); "
            f"status={item.get('state')}; last={item.get('fetched_at')}"
        )
        log_action(
            "dataset.stale.alert",
            source_id=source_id,
            level="warn",
            detail=detail,
            extra=item,
        )
        await send_telegram_alert(source_id, state, detail, source.url)
        sent += 1
    return sent
