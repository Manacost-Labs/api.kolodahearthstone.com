from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

RULE_VERSION = "lab-v1"
SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed.astimezone(UTC)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).strip().replace("%", "").replace(",", "."))
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    soft: int = 560_000
    throttle: int = 620_000
    hard: int = 700_000
    reserve: int = 30
    coverage_min: float = 0.9
    min_games: int = 500
    min_card_observations: int = 200
    popularity_pp: float = 1.0
    popularity_relative: float = 0.25
    winrate_pp: float = 2.0
    confirmed_snapshots: int = 3
    min_signal_hours: float = 8.0
    deep_limit: int = 40
    deck_pages: int = 5
    event_limit: int = 4

    @classmethod
    def from_env(cls) -> Settings:
        base = cls()
        values: dict[str, Any] = {"enabled": os.getenv("HS_META_LAB_ENABLED", "0").lower() in {"1", "true"}}
        for name in cls.__dataclass_fields__:
            if name == "enabled":
                continue
            default = getattr(base, name)
            values[name] = type(default)(os.getenv(f"HS_META_LAB_{name.upper()}", str(default)))
        result = cls(**values)
        if not (0 < result.soft < result.throttle < result.hard):
            raise ValueError("Lab budget thresholds must be positive and ordered")
        if not (0 < result.coverage_min <= 1) or result.confirmed_snapshots < 3:
            raise ValueError("Invalid Lab coverage or confirmation configuration")
        if any(getattr(result, n) <= 0 for n in ("reserve", "min_games", "min_card_observations", "deep_limit", "deck_pages", "event_limit", "min_signal_hours", "popularity_pp", "popularity_relative", "winrate_pp")):
            raise ValueError("Lab limits and signal thresholds must be positive")
        return result
