"""Bounded diagnostics suitable for API/UI; never return exception text."""

from __future__ import annotations

import re
from typing import Any


def failure_diagnostics(source_id: str, status: dict[str, Any]) -> dict[str, Any]:
    from .reliability_telemetry import classify_failure_reason

    reason = classify_failure_reason(status)
    result: dict[str, Any] = {
        "reason": reason,
        "issue": "unclassified_failure" if reason == "unknown" else None,
    }
    if reason == "none":
        return result
    signal = " ".join(
        str(status.get(k) or "")
        for k in ("error", "detail", "last_refresh_error", "last_refresh_detail")
    ).lower()
    drop = re.search(
        r"(?:metric count|filled metric count) dropped (\d{1,7})\s*->\s*(\d{1,7})",
        signal,
    )
    if drop:
        result.update(
            issue="metric_population_drop",
            previous_rows=int(drop[1]),
            received_rows=int(drop[2]),
        )
        if source_id in {
            "hsreplay_cards_wild_legend_1d",
            "hsreplay_cards_wild_diamond_4_1_1d",
        }:
            result["confirmation"] = "two_complete_matching_upstream_observations"
    low = re.search(r"too few rows \((\d{1,7}) < (\d{1,7})\)", signal)
    if low:
        result.update(
            issue="below_row_floor",
            received_rows=int(low[1]),
            required_rows=int(low[2]),
        )
        if source_id == "hsguru_streamer_decks_legend_1000":
            result["issue"] = (
                "empty_rolling_window"
                if int(low[1]) == 0
                else "rolling_window_needs_validation"
            )
    return result
