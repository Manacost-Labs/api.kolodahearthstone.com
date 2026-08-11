from __future__ import annotations

import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from .hsreplay_card_periods import HSREPLAY_CARD_PERIOD_SOURCE_IDS
from .trinket_slices import (
    LEGACY_DEFAULT_TRINKET_SOURCE_IDS,
    TRINKET_SLICE_SOURCE_IDS,
)

WINDOW_TIMEZONE = "Europe/Warsaw"
DEFAULT_WINDOW_START = date(2026, 7, 21)
DEFAULT_WINDOW_UNTIL = date(2026, 7, 28)
POST_PATCH_BASELINE_LABEL = f"arena-post-patch-{DEFAULT_WINDOW_START.isoformat()}"
STABLE_PUBLICATION_BASELINE_LABEL = "stable-publication"


@dataclass(frozen=True)
class PostPatchPolicy:
    source_id: str
    minimum_rows: int = 20
    minimum_classes: int = 1
    minimum_tier_fill_rate: float = 0.80
    minimum_sample: int = 10


@dataclass(frozen=True)
class CapturedPublicationPolicy:
    source_id: str
    effective_mode: str
    token: str
    revision: int | None
    captured_at: str
    window: dict[str, Any] | None


_CAPTURED_POLICY: ContextVar[CapturedPublicationPolicy | None] = ContextVar(
    "captured_publication_policy",
    default=None,
)


ARENA_EARLY_SOURCE_IDS = frozenset(
    {
        "hsreplay_arena_cards_advanced",
        "heartharena_tierlist",
        "firestone_arena_cards_normal",
    }
)

HSGURU_EARLY_SOURCE_IDS = frozenset(
    {
        "hsguru_meta_standard_legend",
        "hsguru_meta_standard_diamond_4to1",
        "hsguru_meta_wild_legend",
        "hsguru_meta_wild_diamond_4to1",
        "hsguru_meta_standard_top_5k",
        "hsguru_meta_standard_top_legend",
        "hsguru_meta_wild_top_legend",
        "hsguru_meta_wild_top_5k",
        "hsguru_matchups_legend",
        "hsguru_matchups_wild_legend",
        "hsguru_matchups_diamond_4to1",
    }
)

FIRESTONE_STANDARD_EARLY_SOURCE_IDS = frozenset({"firestone_standard"})

HSREPLAY_CURRENT_PATCH_EARLY_SOURCE_IDS = frozenset(
    source_id
    for source_id in HSREPLAY_CARD_PERIOD_SOURCE_IDS
    if source_id.endswith("_patch")
)

TRINKET_EARLY_SOURCE_IDS = frozenset(LEGACY_DEFAULT_TRINKET_SOURCE_IDS) | frozenset(
    TRINKET_SLICE_SOURCE_IDS
)

EARLY_SOURCE_IDS = frozenset(
    ARENA_EARLY_SOURCE_IDS
    | HSGURU_EARLY_SOURCE_IDS
    | FIRESTONE_STANDARD_EARLY_SOURCE_IDS
    | HSREPLAY_CURRENT_PATCH_EARLY_SOURCE_IDS
    | TRINKET_EARLY_SOURCE_IDS
)


def _policy_for_source(source_id: str) -> PostPatchPolicy:
    if source_id in HSGURU_EARLY_SOURCE_IDS:
        minimum_rows = 3
    elif source_id in LEGACY_DEFAULT_TRINKET_SOURCE_IDS:
        minimum_rows = 8
    elif source_id in TRINKET_SLICE_SOURCE_IDS:
        minimum_rows = 16
    else:
        minimum_rows = 20
    return PostPatchPolicy(source_id=source_id, minimum_rows=minimum_rows)


def current_time() -> datetime:
    return datetime.now(UTC)


def _enabled() -> bool:
    return os.environ.get("HS_ARENA_POST_PATCH_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _date_setting(name: str, default: date) -> date:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return default


def window_bounds() -> tuple[date, date]:
    return (
        _date_setting("HS_ARENA_POST_PATCH_FROM", DEFAULT_WINDOW_START),
        _date_setting("HS_ARENA_POST_PATCH_UNTIL", DEFAULT_WINDOW_UNTIL),
    )


@contextmanager
def capture_publication_policy(
    source_id: str,
    *,
    at: datetime | None = None,
) -> Iterator[CapturedPublicationPolicy]:
    from .parser_control import publication_policy_context

    raw = publication_policy_context(source_id, at=at)
    captured = CapturedPublicationPolicy(
        source_id=source_id,
        effective_mode=str(raw["effectiveMode"]),
        token=str(raw["token"]),
        revision=raw.get("revision"),
        captured_at=str(raw["capturedAt"]),
        window=raw.get("window"),
    )
    reset_token = _CAPTURED_POLICY.set(captured)
    try:
        yield captured
    finally:
        _CAPTURED_POLICY.reset(reset_token)


def captured_publication_policy(source_id: str) -> CapturedPublicationPolicy | None:
    captured = _CAPTURED_POLICY.get()
    if captured is None or captured.source_id != source_id:
        return None
    return captured


def early_policy_changed_since_capture(
    source_id: str,
) -> tuple[bool, CapturedPublicationPolicy | None, dict[str, Any] | None]:
    captured = captured_publication_policy(source_id)
    if captured is None or captured.effective_mode != "early":
        return False, captured, None
    from .parser_control import publication_policy_context

    current = publication_policy_context(source_id)
    changed = (
        current.get("effectiveMode") != "early"
        or current.get("token") != captured.token
    )
    return changed, captured, current


def policy_for(source_id: str, *, at: datetime | None = None) -> PostPatchPolicy | None:
    if source_id not in EARLY_SOURCE_IDS:
        return None
    captured = captured_publication_policy(source_id)
    if captured is not None:
        return (
            _policy_for_source(source_id)
            if captured.effective_mode == "early"
            else None
        )
    moment = at or current_time()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    # The persisted admin control plane is authoritative once it exists. Until
    # the first admin mutation, effective_publication_mode keeps the existing
    # date-bounded environment variables as a backwards-compatible fallback.
    from .parser_control import effective_publication_mode

    if effective_publication_mode(source_id, at=moment) != "early":
        return None
    return _policy_for_source(source_id)


def effective_contract_min_rows(
    source_id: str,
    default: int,
    *,
    at: datetime | None = None,
) -> int:
    policy = policy_for(source_id, at=at)
    return policy.minimum_rows if policy else default


def effective_arena_card_minimum(
    source_id: str,
    default: int,
    *,
    at: datetime | None = None,
) -> int:
    policy = policy_for(source_id, at=at)
    return policy.minimum_rows if policy else default


def effective_heartharena_thresholds(
    source_id: str,
    *,
    total_cards: int,
    at: datetime | None = None,
) -> tuple[int, int, int]:
    policy = policy_for(source_id, at=at)
    if not policy:
        return 5, 300, 200
    minimum_tier_ids = max(
        1,
        math.ceil(max(total_cards, policy.minimum_rows) * policy.minimum_tier_fill_rate),
    )
    return policy.minimum_classes, policy.minimum_rows, minimum_tier_ids


def effective_firestone_minimum_sample(
    source_id: str,
    default: int,
    *,
    at: datetime | None = None,
) -> int:
    policy = policy_for(source_id, at=at)
    if policy is None or source_id != "firestone_arena_cards_normal":
        return default
    return policy.minimum_sample


def build_provisional_metadata(
    source_id: str,
    *,
    accepted_rows: int,
    baseline_rows: int,
    at: datetime | None = None,
) -> dict[str, object]:
    policy = policy_for(source_id, at=at)
    if policy is None:
        return {}
    captured = captured_publication_policy(source_id)
    if captured is not None:
        central_window = captured.window
    else:
        from .parser_control import effective_early_window

        central_window = effective_early_window(source_id, at=at)
    start, until = window_bounds()
    coverage = accepted_rows / baseline_rows if baseline_rows > 0 else 1.0
    return {
        "data_phase": "post_patch_early",
        "provisional": True,
        "accepted_rows": accepted_rows,
        "baseline_rows": baseline_rows,
        "coverage_ratio": round(coverage, 4),
        "minimum_sample": policy.minimum_sample,
        "patch_window": {
            "from": (central_window or {}).get("from") or start.isoformat(),
            "until": (central_window or {}).get("until") or until.isoformat(),
            "timezone": (central_window or {}).get("timezone") or WINDOW_TIMEZONE,
        },
    }
