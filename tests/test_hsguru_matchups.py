from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from app import hsguru_matchups as matchups
from app.storage import load_dataset, load_status, save_dataset

ROOT = Path(__file__).resolve().parent.parent
# Reads judge staleness by the wall clock, so runs happen "now".
NOW = datetime.now(UTC).replace(microsecond=0)
PERIODS = [
    "past_3_hours",
    "past_6_hours",
    "past_day",
    "past_3_days",
    "past_week",
    "past_2_weeks",
    "past_30_days",
    "patch_36.6.3",
    "black_empire",
    "black_empire_ban",
]

# name, class, games, winrate; cells: (row, col) -> (winrate, games)
ARCHETYPES = [
    ("Aggro Warlock", "warlock", 1000, 56.5),
    ("C'Thun DH", "demonhunter", 400, 51.2),
    ("Quest Priest", "priest", 30, 47.0),
]
CELLS = {
    (0, 0): (50.0, 300),
    (0, 1): (54.2, 120),
    (0, 2): (61.0, 9),
    (1, 0): (45.8, 120),
    (1, 1): (50.0, 50),
    (2, 0): (39.0, 9),
}


def matchups_html(
    archetypes: list[tuple[str, str, int, float]] = ARCHETYPES,
    cells: dict[tuple[int, int], tuple[float, int]] = CELLS,
    total: int = 1430,
) -> str:
    """A trimmed HSGuru /matchups page with its real cell markup."""

    def esc(value: str) -> str:
        return value.replace("'", "&#39;")

    head = "".join(
        f'<th class="class-background {klass}"><button>{esc(name)}</button></th>'
        for name, klass, _, _ in archetypes
    )
    body = []
    for row, (name, klass, games, winrate) in enumerate(archetypes):
        tds = [
            f'<td aria-label="{esc(name)} - {games} games"><div><span><span>{winrate}</span></span></div></td>',
            (
                f'<td class="tw-border sticky-column class-background {klass}">'
                f'<button phx-value-archetype="{esc(name)}"><span>{esc(name)}</span></button></td>'
            ),
        ]
        for col, (opponent, _, _, _) in enumerate(archetypes):
            cell_winrate, cell_games = cells.get((row, col), (0, 0))
            label = f"{esc(name)} versus {esc(opponent)} - {cell_winrate:g}% {cell_games} games"
            inner = f"<div><span><span>{cell_winrate}</span></span></div>" if cell_games else ""
            tds.append(f'<td aria-label="{label}">{inner}</td>')
        body.append(f"<tr>{''.join(tds)}</tr>")
    return (
        "<html><body><table><thead><tr><th>Winrate</th><th>"
        f'<button phx-value-total_games="{total}">Seed Weights</button></th>{head}</tr>'
        f"</thead><tbody>{''.join(body)}</tbody></table></body></html>"
    )


PAGE = matchups_html()
PREMIUM = "<div>You do not have access to these filters. Join the appropriate tier</div>"
OOPS = "<html><body>Oops, looks like something went wrong...</body></html>"


# --- parsing ---------------------------------------------------------------------


def test_parse_keeps_rows_games_and_only_played_cells() -> None:
    parsed = matchups.parse_matchups_page(PAGE)
    assert parsed["total_games"] == 1430
    assert parsed["archetypes"][1] == {
        "archetype": "C'Thun DH",
        "class": "DEMONHUNTER",
        "games": 400,
        "winrate": 51.2,
    }
    # Empty "0% 0 games" cells are not stored; indices point at archetype rows.
    assert len(parsed["cells"]) == len(CELLS)
    assert [0, 1, 54.2, 120] in parsed["cells"] and [2, 0, 39.0, 9] in parsed["cells"]


def test_empty_window_is_valid_and_broken_pages_are_rejected() -> None:
    empty = matchups.parse_matchups_page(matchups_html([], {}, total=0))
    assert empty == {"total_games": 0, "archetypes": [], "cells": []}
    with pytest.raises(matchups.PageUnavailable):
        matchups.parse_matchups_page(PREMIUM)
    with pytest.raises(RuntimeError, match="upstream_error_page"):
        matchups.parse_matchups_page(OOPS)
    with pytest.raises(ValueError):
        matchups.parse_matchups_page("<html><table><tr><td>x</td></tr></table></html>")
    broken = PAGE.replace("54.2% 120 games", "54.2% games")
    with pytest.raises(ValueError):
        matchups.parse_matchups_page(broken)
    # The provider keeps a premium page (recorded as unavailable), retries the rest.
    assert matchups.page_is_valid(PREMIUM) and matchups.page_is_valid(PAGE)
    assert not matchups.page_is_valid(OOPS) and not matchups.page_is_valid(broken)


# --- plan ------------------------------------------------------------------------


def test_slice_urls_use_the_lowest_hsguru_sample_filters() -> None:
    url = matchups.slice_url("wild", "top_legend", "black_empire_ban")
    assert url == (
        "https://www.hsguru.com/matchups?format=1&rank=top_legend&period=black_empire_ban"
        "&min_archetype_sample=1&min_matchup_sample=1"
    )
    plan = matchups.plan_slices(PERIODS)
    assert len(plan) == 2 * len(matchups.RANKS) * len(PERIODS) == 140
    assert {item["coin"] for item in plan} == {"any_player"}


def test_fresh_windows_refresh_more_often_and_wild_never_below_its_recompute() -> None:
    assert matchups.ttl_hours("standard", "past_3_hours") == 1
    assert matchups.ttl_hours("standard", "black_empire_ban") == 2
    assert matchups.ttl_hours("standard", "patch_36.6.3") == 12
    assert matchups.ttl_hours("standard", "black_empire") == 12
    assert matchups.ttl_hours("standard", "past_30_days") == 24
    assert min(matchups.ttl_hours("wild", period) for period in PERIODS) == 3
    estimate = matchups.estimate_plan(PERIODS)
    assert estimate["slices"] == 140
    # About 800 one-credit pages a day.
    assert 700 <= estimate["scrape_do_credits_per_day"] <= 900


def test_due_rules_slack_and_retry_backoff() -> None:
    target = {"format": "standard", "period": "past_3_hours"}
    assert matchups.due_score(target, NOW) == float("inf")
    # An hourly run meets the one-hour TTL a few seconds early and still reads it.
    almost = {**target, "last_success_at": (NOW - timedelta(minutes=59)).isoformat()}
    assert matchups.due_score(almost, NOW) == 1.0
    fresh = {**target, "last_success_at": (NOW - timedelta(minutes=20)).isoformat()}
    assert matchups.due_score(fresh, NOW) is None
    backoff = {**target, "next_attempt_after": (NOW + timedelta(minutes=5)).isoformat()}
    assert matchups.due_score(backoff, NOW) is None
    week = {"format": "standard", "period": "past_week"}
    assert matchups.due_score(
        {**week, "last_success_at": (NOW - timedelta(hours=3)).isoformat()}, NOW
    ) is None


def test_periods_come_from_the_meta_matrix_menu() -> None:
    structured = {
        "dimensions": {"periods": [*PERIODS, "bad period!"]},
        "current_catalog": {"criteria": {"period": "black_empire"}},
    }
    assert matchups.planned_periods(structured) == tuple(PERIODS)
    assert matchups.planned_periods({}) == matchups.ROLLING_PERIODS
    assert matchups.current_period(structured) == "black_empire"
    assert matchups.default_period(PERIODS, "black_empire") == "black_empire_ban"
    assert matchups.default_period(PERIODS[:9], "black_empire") == "black_empire"


def test_settings_cap_parallel_pages_at_five(monkeypatch) -> None:
    monkeypatch.setenv("HS_HSGURU_MATCHUPS_CONCURRENCY", "40")
    assert matchups.Settings.from_env().concurrency == 5
    assert matchups.Settings().concurrency == 5


def test_scrape_do_asks_for_the_plain_page_with_the_budget_scope(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def scrape(source, **options):
        from app.meta_intelligence.budget import _scope

        seen.update(options, source_id=source.id, scope=dict(_scope.get() or {}))
        return type("Result", (), {"html": PAGE, "request_credits": 1, "backend": "scrape_do"})()

    monkeypatch.setattr("app.firecrawl_backend._scrape_sync", scrape)
    acquirer = matchups.Acquirer()
    html, meta = asyncio.run(acquirer.fetch(matchups.slice_url("standard", "all", "past_day")))
    assert html == PAGE and meta == {"backend": "scrape_do", "request_credits": 1}
    assert seen["scrape_do_profiles"] == ((False, False),)
    assert seen["headers"] is None and seen["source_id"] == "hsguru_matchups:page"
    assert seen["scope"]["source_id"] == "hsguru_matchups"
    assert seen["scope"]["job_class"] == "detail"
    assert acquirer.scrape_do_credits == 1 and acquirer.pages_scrape_do == 1


# --- refresh ---------------------------------------------------------------------


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    save_dataset(
        "hsguru_meta_matrix",
        {
            "state": "ok",
            "fetched_at": NOW.isoformat(),
            "data": {
                "structured": {
                    "dimensions": {"periods": PERIODS},
                    "current_catalog": {"criteria": {"period": "black_empire"}},
                }
            },
        },
    )
    return tmp_path


def _fetcher(pages: dict[str, Any] | None = None, calls: list[str] | None = None):
    async def fetch(url: str):
        if calls is not None:
            calls.append(url)
        for marker, page in (pages or {}).items():
            if marker in url:
                if isinstance(page, Exception):
                    raise page
                return page, {"backend": "scrape_do", "request_credits": 1}
        return PAGE, {"backend": "scrape_do", "request_credits": 1}

    return fetch


def _refresh(*, now=NOW, pages=None, calls=None, **kwargs) -> dict[str, Any]:
    return asyncio.run(
        matchups.refresh_hsguru_matchups(
            fetch_page=_fetcher(pages, calls), now=now, **kwargs
        )
    )


def _rows(path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path / "hs_parses.db")
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {matchups.SLICES}").fetchall()
    finally:
        conn.close()


def test_first_run_reads_every_slice_and_later_runs_only_due_ones(isolated) -> None:
    result = _refresh()
    assert result["state"] == "ok" and result["published"] is True
    assert result["attempted"] == result["changed"] == result["slices_with_data"] == 140
    assert result["scrape_do_credits"] == 0  # the fake fetcher bypasses the acquirer
    dataset = load_dataset("hsguru_matchups")["data"]["structured"]
    assert dataset["dimensions"]["periods"] == PERIODS
    assert dataset["current_period"] == "black_empire"
    assert dataset["slices_with_data"] == 140 and len(dataset["slices"]) == 140
    assert load_status("hsguru_matchups")["state"] == "ok"

    calls: list[str] = []
    assert _refresh(now=NOW + timedelta(minutes=5), calls=calls)["attempted"] == 0
    # An hour later only Standard 3/6-hour windows are due (Wild waits three hours).
    later = _refresh(now=NOW + timedelta(hours=1), calls=calls)
    assert later["attempted"] == 2 * len(matchups.RANKS)
    assert later["unchanged"] == later["attempted"]
    assert all("format=2" in url and "past_" in url and "hours" in url for url in calls)


def test_failures_back_off_and_unavailable_slices_wait_a_day(isolated) -> None:
    pages = {
        "rank=bronze_plat&period=past_3_hours": PREMIUM,
        "format=1&rank=top_legend&period=past_3_hours": RuntimeError("scrape_do=502"),
    }
    result = _refresh(pages=pages)
    assert result["state"] == "partial" and result["published"] is True
    assert (result["failed"], result["unavailable"]) == (1, 2)
    states = {row["slice_key"]: row for row in _rows(isolated)}
    failed = states["wild|top_legend|past_3_hours|any_player"]
    assert failed["state"] == "failed" and failed["payload_json"] is None
    assert failed["next_attempt_after"] == (NOW + timedelta(minutes=30)).isoformat()
    unavailable = states["standard|bronze_plat|past_3_hours|any_player"]
    assert unavailable["state"] == "unavailable"
    assert unavailable["next_attempt_after"] == (NOW + timedelta(hours=24)).isoformat()

    # A later failure keeps the last good matrix readable.
    _refresh(
        now=NOW + timedelta(hours=1),
        pages={"format=2&rank=all&period=past_3_hours": RuntimeError("boom")},
    )
    kept = {row["slice_key"]: row for row in _rows(isolated)}["standard|all|past_3_hours|any_player"]
    assert kept["state"] == "failed" and kept["payload_json"] is not None


def test_run_stops_when_the_provider_keeps_failing(isolated) -> None:
    result = _refresh(pages={"matchups": RuntimeError("down")})
    assert result["state"] == "fetch_error" and result["published"] is False
    assert result["stop_reason"] == "provider_circuit_open"
    assert result["failed"] < 140
    assert load_dataset("hsguru_matchups") is None


def test_periods_that_leave_the_menu_stay_readable_but_inactive(isolated) -> None:
    _refresh()
    save_dataset(
        "hsguru_meta_matrix",
        {"data": {"structured": {"dimensions": {"periods": PERIODS[:-1]}}}},
    )
    result = _refresh(now=NOW + timedelta(minutes=5))
    assert result["slices"] == 2 * len(matchups.RANKS) * (len(PERIODS) - 1)
    bans = [row for row in _rows(isolated) if row["period"] == "black_empire_ban"]
    assert bans and all(row["active"] == 0 and row["payload_json"] for row in bans)


# --- read API --------------------------------------------------------------------


@pytest.fixture()
def client(isolated):
    _refresh()
    from app.main import app

    return TestClient(app)


def test_matchups_endpoint_defaults_to_the_bans_window(client) -> None:
    response = client.get("/v1/hsguru/matchups")
    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    assert (data["format"], data["rank"], data["period"], data["coin"]) == (
        "standard",
        "all",
        "black_empire_ban",
        "any_player",
    )
    assert data["total_games"] == 1430 and data["min_games"] == 1
    assert [row["archetype"] for row in data["archetypes"]] == [
        "Aggro Warlock",
        "C'Thun DH",
        "Quest Priest",
    ]
    assert data["archetypes"][0]["popularity_pct"] == 69.93
    assert {
        "archetype": "Aggro Warlock",
        "opponent": "C'Thun DH",
        "winrate": 54.2,
        "games": 120,
    } in data["matchups"]
    available = body["meta"]["available"]
    assert available["periods"] == PERIODS and available["coins"] == ["any_player"]
    assert available["default_period"] == "black_empire_ban"
    assert available["ranks"][:2] == ["all", "top_legend"]
    assert body["meta"]["source_id"] == "hsguru_matchups"
    assert body["meta"]["stale"] is False


def test_matchups_filters_thresholds_and_top_archetypes(client) -> None:
    data = client.get(
        "/v1/hsguru/matchups",
        params={"format": "wild", "rank": "legend", "period": "past_week", "min_games": 10},
    ).json()["data"]
    assert all(cell["games"] >= 10 for cell in data["matchups"]) and len(data["matchups"]) == 4
    data = client.get(
        "/v1/hsguru/matchups", params={"min_archetype_games": 100}
    ).json()["data"]
    assert [row["archetype"] for row in data["archetypes"]] == ["Aggro Warlock", "C'Thun DH"]
    assert all(cell["opponent"] != "Quest Priest" for cell in data["matchups"])
    data = client.get("/v1/hsguru/matchups", params={"limit": 1}).json()["data"]
    assert len(data["archetypes"]) == 1 and len(data["matchups"]) == 1


def test_invalid_coin_period_and_missing_slices(client, isolated) -> None:
    coin = client.get("/v1/hsguru/matchups", params={"coin": "on_coin"})
    assert coin.status_code == 422
    assert coin.json()["detail"]["allowed_coins"] == ["any_player"]
    period = client.get("/v1/hsguru/matchups", params={"period": "past_year"})
    assert period.status_code == 422
    assert period.json()["detail"]["allowed_periods"] == PERIODS
    assert client.get("/v1/hsguru/matchups", params={"rank": "top_100"}).status_code == 422
    conn = sqlite3.connect(isolated / "hs_parses.db")
    with conn:
        conn.execute(f"UPDATE {matchups.SLICES} SET payload_json = NULL WHERE rank = 'legend'")
    conn.close()
    assert client.get("/v1/hsguru/matchups", params={"rank": "legend"}).status_code == 404


def test_slices_endpoint_lists_every_slice_with_its_state(client) -> None:
    body = client.get("/v1/hsguru/matchups/slices").json()
    assert body["meta"]["count"] == 140 and body["meta"]["stale"] is False
    first = body["data"][0]
    assert {"key", "format", "rank", "period", "state", "checked_at", "cells"} <= set(first)
    assert body["meta"]["last_run"]["attempted"] == 140
    assert body["meta"]["estimate"]["slices"] == 140


def test_graphql_matches_rest(client, monkeypatch) -> None:
    from app.redis_cache import TieredCache
    from tests.test_graphql_api import FakeRepository
    from tests.test_redis_cache import FakeBackend

    repository = FakeRepository()
    cache = TieredCache(FakeBackend(), max_local_entries=32)
    monkeypatch.setattr("app.graphql_api.router.get_graphql_repository", lambda: repository)
    monkeypatch.setattr("app.graphql_api.governance.get_tiered_cache", lambda: cache)
    query = (
        '{ hsguruMatchups(format: "standard", rank: "all", minGames: 100) }'
    )
    response = client.post("/v1/graphql", json={"query": query})
    assert response.status_code == 200, response.text
    data = response.json()["data"]["hsguruMatchups"]
    rest = client.get("/v1/hsguru/matchups", params={"min_games": 100}).json()["data"]
    assert data == rest
    invalid = client.post(
        "/v1/graphql", json={"query": '{ hsguruMatchups(coin: "on_coin") }'}
    ).json()
    assert invalid["errors"][0]["extensions"]["code"] == "VALIDATION_ERROR"


def test_systemd_timer_matches_the_schedule_inventory() -> None:
    from app.parser_control_schedule import _SCHEDULES

    spec = next(spec for spec in _SCHEDULES if spec.id == "refresh-hsguru-matchups")
    timer = (ROOT / "systemd" / spec.systemd_unit).read_text()
    service = (ROOT / "systemd" / spec.systemd_unit.replace(".timer", ".service")).read_text()
    assert all(f"OnCalendar={value}" in timer for value in spec.on_calendar)
    assert "refresh-hsguru-matchups --scheduled --schedule-id refresh-hsguru-matchups" in service
