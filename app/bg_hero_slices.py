"""On-demand Battlegrounds hero slices with a last-known-good copy.

Only the Top 50% hero list is collected on a schedule. The narrower MMR slices
of ``/api/bg/heroes`` are read from HSReplay when a client asks for them. This
module keeps the last good answer of every such slice on disk, so that:

* a fresh copy answers without an upstream request;
* a failed upstream read serves the previous copy marked stale;
* without a copy the caller gets :class:`HeroSliceUnavailable` (HTTP 503),
  never an unhandled exception;
* a failure is never stored as data, and is not retried on every request.

The copies are not registered sources: they have no schedule, so they are not
part of the source inventory or of the health diagnostics.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .storage import _assert_not_test_prod_write, root_dir

logger = logging.getLogger(__name__)

ON_DEMAND_MMRS: frozenset[str] = frozenset(
    {"TOP_20_PERCENT", "TOP_5_PERCENT", "TOP_1_PERCENT"}
)
MODES: frozenset[str] = frozenset({"solo", "duos"})
# A copy younger than this answers without asking HSReplay: the upstream list
# is a daily aggregate, and every read may spend a paid provider request.
FRESH_SECONDS = 3 * 60 * 60
# After a failed read the same slice is not requested again for this long.
FAILURE_COOLDOWN_SECONDS = 10 * 60
# Same floors as the publication checks of the scheduled hero lists.
MIN_HEROES = {"solo": 30, "duos": 20}

_locks: dict[tuple[str, str], asyncio.Lock] = {}
_retry_not_before: dict[tuple[str, str], float] = {}


class HeroSliceUnavailable(Exception):
    """The slice could not be read and no stored copy exists."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("Battlegrounds hero slice is temporarily unavailable")
        self.retry_after = max(1, int(retry_after))


def slice_path(mode: str, mmr: str) -> Path:
    if mode not in MODES or mmr not in ON_DEMAND_MMRS:
        raise ValueError("Unknown Battlegrounds hero slice")
    return root_dir() / "on_demand" / f"bg_heroes.{mode}.{mmr}.json"


def _read_copy(path: Path) -> dict[str, Any] | None:
    try:
        copy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if (
        not isinstance(copy, dict)
        or not isinstance(copy.get("payload"), dict)
        or not isinstance(copy["payload"].get("heroes"), list)
        or _age_seconds(copy.get("fetched_at")) is None
    ):
        return None
    return copy


def _write_copy(path: Path, copy: dict[str, Any]) -> None:
    _assert_not_test_prod_write(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(copy, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _age_seconds(fetched_at: Any) -> float | None:
    if not isinstance(fetched_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return (datetime.now(UTC) - parsed).total_seconds()


def validate_hero_index(payload: Any, *, mode: str) -> None:
    """Reject an answer that is not a usable hero list of this mode."""

    heroes = payload.get("heroes") if isinstance(payload, dict) else None
    count = len(heroes) if isinstance(heroes, list) else 0
    minimum = MIN_HEROES[mode]
    if count < minimum:
        raise ValueError(f"hero index too small ({count} < {minimum})")


def _response(copy: dict[str, Any], *, stale: bool) -> dict[str, Any]:
    payload = copy["payload"]
    return {
        "mode": payload.get("mode"),
        "fetched_at": copy["fetched_at"],
        "stale": stale,
        "filters": payload.get("filters"),
        "source": payload.get("source"),
        "heroes": list(payload.get("heroes") or []),
    }


async def load_hero_slice(*, mode: str, mmr: str) -> dict[str, Any]:
    """Return the hero list of one on-demand slice.

    ``stale`` is true when the answer is the stored copy served because
    HSReplay could not be read. Raises :class:`HeroSliceUnavailable` when the
    read failed and nothing is stored.
    """

    from .hsreplay_bg_hero_details import fetch_hero_index

    path = slice_path(mode, mmr)
    key = (mode, mmr)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        copy = await asyncio.to_thread(_read_copy, path)
        if copy is not None:
            age = _age_seconds(copy["fetched_at"])
            if age is not None and 0 <= age < FRESH_SECONDS:
                return _response(copy, stale=False)
        cooldown = _retry_not_before.get(key, 0.0) - time.monotonic()
        if cooldown <= 0:
            try:
                payload = await fetch_hero_index(duos=mode == "duos", mmr=mmr)
                validate_hero_index(payload, mode=mode)
            except Exception as exc:  # noqa: BLE001 - any upstream failure degrades
                # Upstream errors can carry request data: log the type only.
                logger.warning(
                    "BG hero slice %s/%s refresh failed (%s)",
                    mode,
                    mmr,
                    type(exc).__name__,
                )
                _retry_not_before[key] = time.monotonic() + FAILURE_COOLDOWN_SECONDS
                cooldown = FAILURE_COOLDOWN_SECONDS
            else:
                _retry_not_before.pop(key, None)
                fresh = {
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "payload": payload,
                }
                try:
                    await asyncio.to_thread(_write_copy, path, fresh)
                except OSError as exc:
                    logger.warning(
                        "BG hero slice %s/%s copy was not stored (%s)",
                        mode,
                        mmr,
                        type(exc).__name__,
                    )
                return _response(fresh, stale=False)
        if copy is not None:
            return _response(copy, stale=True)
        raise HeroSliceUnavailable(retry_after=int(cooldown) or 1)


def _reset_for_tests() -> None:
    _locks.clear()
    _retry_not_before.clear()
