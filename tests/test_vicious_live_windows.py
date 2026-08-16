from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.source_validators import validate_structured
from app.sources import Source
from app.vicious_live import ViciousLiveWindowUnavailable, fetch_vicious_live

ARCHETYPES = [
    ["DeathKnight", "Alpha"],
    ["DemonHunter", "Beta"],
    ["Druid", "Gamma"],
    ["Hunter", "Delta"],
]


def _ladder_interval(*, games_per_rank: int) -> dict[str, object]:
    rank_data = []
    class_rank_data = []
    for _ in range(51):
        rank_data.append([40, 30, 20, 10] if games_per_rank else [0, 0, 0, 0])
        class_rank_data.append(
            [40, 30, 20, 10, 0, 0, 0, 0, 0, 0, 0] if games_per_rank else [0] * 11
        )
    return {
        "archetypes": ARCHETYPES,
        "gamesPerRank": [games_per_rank] * 51,
        "rankData": rank_data,
        "classRankData": class_rank_data,
    }


def _table_interval(*, games_per_matchup: int) -> dict[str, object]:
    return {
        "ranks_all": {
            "archetypes": ARCHETYPES,
            "frequency": [0.4, 0.3, 0.2, 0.1] if games_per_matchup else [0, 0, 0, 0],
            "table": [
                [[games_per_matchup, games_per_matchup] for _ in ARCHETYPES]
                for _ in ARCHETYPES
            ],
        }
    }


def _fetch_with_payloads(
    ladder_payload: dict[str, object],
    table_payload: dict[str, object],
) -> dict[str, object]:
    source = Source(
        id="vicious_syndicate_live_beta",
        url="https://www.vicioussyndicate.com/data-reaper-live-beta/",
        site="vicious_syndicate",
        category="standard",
    )
    with (
        patch(
            "app.vicious_live._firebase_token",
            new=AsyncMock(return_value="test-token"),
        ),
        patch(
            "app.vicious_live._firebase_json",
            new=AsyncMock(side_effect=[ladder_payload, table_payload]),
        ),
    ):
        return asyncio.run(fetch_vicious_live(source))


def test_fetch_uses_first_aligned_non_empty_window_when_last_day_is_empty() -> None:
    ladder_payload = {
        "lastDay": _ladder_interval(games_per_rank=0),
        "last3Days": _ladder_interval(games_per_rank=100),
        "lastWeek": _ladder_interval(games_per_rank=100),
        "last2Weeks": _ladder_interval(games_per_rank=100),
    }
    table_payload = {
        "lastDay": _table_interval(games_per_matchup=0),
        "last3Days": _table_interval(games_per_matchup=100),
        "lastWeek": _table_interval(games_per_matchup=100),
        "last2Weeks": _table_interval(games_per_matchup=100),
    }

    result = _fetch_with_payloads(ladder_payload, table_payload)

    assert result["games"] == 5100
    assert result["pie_time_range"] == "last3Days"
    assert result["tier_ladder_time_range"] == "last3Days"
    assert result["tier_matchup_time_range"] == "last3Days"
    assert result["upstream_state"] == "ready"
    assert result["upstream_availability"]["window_state"] == "fallback_ready"
    assert result["upstream_availability"]["selected_time_range"] == "last3Days"
    assert result["upstream_availability"]["fallback_used"] is True
    diagnostics = result["upstream_availability"]["window_diagnostics"]
    assert diagnostics[0]["time_range"] == "lastDay"
    assert diagnostics[0]["ladder_games"] == 0
    assert diagnostics[0]["matchup_games"] == 0
    assert diagnostics[0]["usable"] is False
    assert diagnostics[1]["time_range"] == "last3Days"
    assert diagnostics[1]["usable"] is True
    assert validate_structured("vicious_syndicate_live_beta", result).ok


def test_fetch_keeps_last_day_when_both_aligned_windows_have_data() -> None:
    ladder_payload = {
        "lastDay": _ladder_interval(games_per_rank=25),
        "last3Days": _ladder_interval(games_per_rank=100),
        "lastWeek": _ladder_interval(games_per_rank=100),
        "last2Weeks": _ladder_interval(games_per_rank=100),
    }
    table_payload = {
        "lastDay": _table_interval(games_per_matchup=25),
        "last3Days": _table_interval(games_per_matchup=100),
        "lastWeek": _table_interval(games_per_matchup=100),
        "last2Weeks": _table_interval(games_per_matchup=100),
    }

    result = _fetch_with_payloads(ladder_payload, table_payload)

    assert result["games"] == 1275
    assert result["pie_time_range"] == "lastDay"
    assert result["tier_ladder_time_range"] == "lastDay"
    assert result["tier_matchup_time_range"] == "lastDay"
    assert result["upstream_availability"]["window_state"] == "preferred_ready"
    assert result["upstream_availability"]["fallback_used"] is False


def test_fetch_reports_temporarily_empty_when_no_aligned_window_has_samples() -> None:
    ladder_payload = {
        time_range: _ladder_interval(games_per_rank=0)
        for time_range in ("lastDay", "last3Days", "lastWeek", "last2Weeks")
    }
    table_payload = {
        time_range: _table_interval(games_per_matchup=0)
        for time_range in ("lastDay", "last3Days", "lastWeek", "last2Weeks")
    }

    with pytest.raises(ViciousLiveWindowUnavailable) as raised:
        _fetch_with_payloads(ladder_payload, table_payload)

    assert raised.value.upstream_state == "upstream_temporarily_empty"
    assert raised.value.failure_reason_code == "unavailable"
    assert raised.value.upstream_readiness["selected_time_range"] is None
    assert raised.value.upstream_readiness["window_state"] == (
        "upstream_temporarily_empty"
    )
    assert len(raised.value.upstream_readiness["window_diagnostics"]) == 4
