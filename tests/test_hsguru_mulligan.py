from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from app import hsguru_mulligan as mulligan
from app import hsguru_mulligan_store as store
from app.storage import dataset_path, save_dataset, write_json

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
FORGE_DBF = 129947


def _card_row(dbf_id: int, name: str, cost: int, values: list[str]) -> str:
    # Mirrors HSGuru's card cell: a screen-reader-only prefix inside .card-name.
    cells = "".join(f"<td>{value}</td>" for value in values)
    return (
        f"<tr><td><div><a aria-label='↑x {name}' href='/card/{dbf_id}'><div>"
        f"<span class='card-number deck-text'>{cost}</span>"
        f"<div class='card-name deck-text'><span class='tw-sr-only'># ↑x ({cost})</span>"
        f" {name}</div></div></a></div></td>{cells}</tr>"
    )


def card_stats_html(rows: list[str], *, games: int) -> str:
    return (
        "<html><body><div data-phx-session='x'><span>Games: "
        f"{games}</span><table><thead><tr><th>Card</th><th>Mulligan Impact↓</th>"
        "<th>Mulligan Count</th><th>Drawn Impact</th><th>Drawn Count</th>"
        "<th>Not Drawn Impact</th><th>Not Drawn Count</th><th>Kept Impact</th>"
        f"<th>Kept Count</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
        "</div></body></html>"
    )


FORGE = _card_row(
    FORGE_DBF,
    "Forge of Origination",
    3,
    ["3.5", "633", "-0.6", "1946", "0.6", "1242", "6.5", "314"],
)
DUCK = _card_row(
    2, "Mother Duck", 4, ["12.6", "121", "1.0", "342", "-4.1", "180", "15.8", "65"]
)
RARE = _card_row(3, "Rare Card", 1, ["40.0", "3", "1.0", "4", "", "0", "", "0"])


def matchups_html(rows: list[tuple[str, str, str]]) -> str:
    body = "".join(
        f"<tr><td>{label}</td><td>{winrate}</td><td>{games}</td></tr>"
        for label, winrate, games in rows
    )
    return (
        "<html><body><div data-phx-session='x'><table><thead><tr><th>Class</th>"
        f"<th>Winrate</th><th>Total Games</th></tr></thead><tbody>{body}</tbody>"
        "</table></div></body></html>"
    )


MATCHUPS = matchups_html(
    [
        ("Death Knight", "45.5", "829 (8.9%)"),
        ("Mage", "52.0", "1,000 (10.7%)"),
        ("Total", "50.1", "9352"),
    ]
)


def _matrix(
    rows_by_slice: dict[tuple[str, str, str, str], list[tuple[str, int]]],
) -> dict[str, Any]:
    slices = [
        {
            "key": "|".join(key),
            "format": key[0],
            "rank": key[1],
            "period": key[2],
            "coin": key[3],
            "rows": [{"archetype": name, "games": games} for name, games in rows],
        }
        for key, rows in rows_by_slice.items()
    ]
    return {
        "fetched_at": NOW.isoformat(),
        "data": {
            "structured": {
                "slices": slices,
                "current_catalog": {
                    "criteria": {"period": "black_empire", "rank": "all"}
                },
            }
        },
    }


def _deck_catalog(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"fetched_at": NOW.isoformat(), "data": rows}


def _deck(
    deck_id: int, games: int, *, rank: str = "all", period: str = "black_empire"
) -> dict:
    return {
        "archetype": "Dragon Warrior",
        "format": "Standard",
        "games": games,
        "url": f"https://www.hsguru.com/deck/{deck_id}",
        "sample_rank": rank,
        "sample_period": period,
    }


# --- parsing ---------------------------------------------------------------------


def test_card_stats_parse_every_column_and_the_visible_card_name(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cards_index.cards_by_dbfid",
        lambda: {FORGE_DBF: {"id": "BE_561", "name": "Forge of Origination"}},
    )
    parsed = mulligan.parse_mulligan_html(card_stats_html([FORGE, RARE], games=9352))
    assert parsed["games"] == 9352
    forge, rare = parsed["rows"]
    assert forge == {
        "card_id": "BE_561",
        "dbf_id": FORGE_DBF,
        "card_name": "Forge of Origination",
        "mulligan_impact": 3.5,
        "mulligan_count": 633,
        "drawn_impact": -0.6,
        "drawn_count": 1946,
        "not_drawn_impact": 0.6,
        "not_drawn_count": 1242,
        "kept_impact": 6.5,
        "kept_count": 314,
    }
    assert rare["not_drawn_impact"] is None and rare["kept_count"] == 0


def test_empty_window_is_valid_but_a_broken_or_paywalled_page_is_not() -> None:
    assert mulligan.parse_mulligan_html(card_stats_html([], games=0)) == {
        "games": 0,
        "winrate": None,
        "rows": [],
    }
    with pytest.raises(mulligan.PageRejected, match="content_validation"):
        mulligan.parse_mulligan_html("<html><title>Just a moment...</title></html>")
    with pytest.raises(mulligan.PageRejected, match="paywalled"):
        mulligan.parse_mulligan_html(
            card_stats_html([], games=0) + "You do not have access to these filters"
        )


def test_class_matchups_keep_the_total_row_and_class_keys() -> None:
    parsed = mulligan.parse_class_matchups_page(MATCHUPS)
    assert parsed["games"] == 9352 and parsed["winrate"] == 50.1
    assert parsed["rows"] == [
        {"class_key": "deathknight", "winrate": 45.5, "games": 829, "share_pct": 8.9},
        {"class_key": "mage", "winrate": 52.0, "games": 1000, "share_pct": 10.7},
    ]
    empty = mulligan.parse_class_matchups_page(matchups_html([("Total", "0", "0")]))
    assert empty == {"games": 0, "winrate": None, "rows": []}


# --- planning --------------------------------------------------------------------


def test_plan_uses_game_thresholds_core_periods_and_opponent_split() -> None:
    settings = mulligan.Settings()
    matrix = _matrix(
        {
            ("standard", "all", "black_empire", "any_player"): [
                ("Big", 12000),
                ("Small", 1999),
            ],
            ("standard", "all", "past_30_days", "any_player"): [("Big", 90000)],
            ("standard", "all", "past_3_hours", "any_player"): [("Big", 90000)],
            ("standard", "diamond", "black_empire", "any_player"): [("Big", 90000)],
            ("standard", "all", "black_empire", "on_coin"): [
                ("Big", 3000),
                ("Small", 2999),
            ],
            ("standard", "all", "past_30_days", "on_coin"): [("Big", 90000)],
        }
    )
    targets = {
        target["target_key"]: target
        for target in mulligan.plan_targets(matrix, [], settings)
    }
    keys = set(targets)
    big = "archetype:big|standard|all"
    assert f"cards|{big}|black_empire|any_player|any" in keys
    assert f"matchups|{big}|black_empire|any_player|any" in keys
    assert f"cards|{big}|past_30_days|any_player|any" in keys
    # Coin split and class matchups only for core windows.
    assert f"matchups|{big}|past_30_days|any_player|any" not in keys
    assert f"cards|{big}|past_30_days|on_coin|any" not in keys
    assert targets[f"cards|{big}|black_empire|on_coin|any"]["tier"] == 2
    # Opponent split for the current window of a widely played archetype.
    opponents = [
        key for key in keys if key.startswith(f"cards|{big}|black_empire|any_player|")
    ]
    assert len(opponents) == 1 + len(mulligan.OPPONENT_CLASSES)
    assert not any("archetype:small" in key for key in keys)
    assert not any("past_3_hours" in key or "|diamond|" in key for key in keys)


def test_deck_plan_keeps_the_best_sample_and_splits_coin_for_big_decks() -> None:
    catalogs = [
        _deck_catalog([_deck(1, 700), _deck(2, 99), _deck(3, 150)]),
        _deck_catalog([_deck(1, 650), _deck(1, 300, rank="legend")]),
    ]
    targets = mulligan.plan_deck_targets(catalogs, mulligan.Settings())
    keys = {target["target_key"]: target for target in targets}
    assert (
        keys["cards|deck:1|standard|all|black_empire|any_player|any"]["expected_games"]
        == 700
    )
    assert "cards|deck:1|standard|all|black_empire|on_coin|any" in keys
    assert "cards|deck:1|standard|legend|black_empire|any_player|any" in keys
    assert "cards|deck:3|standard|all|black_empire|on_coin|any" not in keys
    assert "matchups|deck:3|standard|all|black_empire|any_player|any" in keys
    assert not any("deck:2" in key for key in keys)


def test_target_urls_match_hsguru_filters() -> None:
    archetype = mulligan._target(
        kind="cards",
        subject_type="archetype",
        format_name="wild",
        archetype="Dragon Warrior",
        rank="legend",
        period="black_empire_ban",
        coin="on_coin",
        tier=2,
        expected_games=1,
        opponent_class="MAGE",
    )
    assert mulligan.target_url(archetype) == (
        "https://www.hsguru.com/card-stats?archetype=Dragon+Warrior&format=1&rank=legend"
        "&period=black_empire_ban&player_has_coin=yes&opponent_class=MAGE"
        "&min_mull_count=0&min_drawn_count=0&show_counts=yes"
    )
    deck = {
        **archetype,
        "subject_type": "deck",
        "deck_id": 41919872,
        "coin": "going_first",
    }
    deck["opponent_class"] = "any"
    assert mulligan.target_url(deck).startswith(
        "https://www.hsguru.com/card-stats?deck_id=41919872&format=1&rank=legend"
    )
    assert "player_has_coin=no" in mulligan.target_url(deck)
    assert mulligan.target_url({**archetype, "kind": "matchups"}) == (
        "https://www.hsguru.com/archetype/Dragon%20Warrior?format=1&rank=legend"
        "&period=black_empire_ban&player_has_coin=yes"
    )
    assert mulligan.target_url({**deck, "kind": "matchups", "coin": "any_player"}) == (
        "https://www.hsguru.com/deck/41919872?rank=legend&period=black_empire_ban"
    )


def test_due_rules_ttl_growth_and_retry_backoff() -> None:
    base = {"tier": 1, "period": "past_week", "expected_games": 1000}
    assert mulligan.due_score(base, NOW) == float("inf")
    fresh = {**base, "last_success_at": (NOW - timedelta(hours=71)).isoformat()}
    assert mulligan.due_score(fresh, NOW) is None
    # Tier 3 doubles the 72 h past_week TTL.
    assert mulligan.due_score({**fresh, "tier": 3}, NOW + timedelta(hours=60)) is None
    assert mulligan.due_score(fresh, NOW + timedelta(hours=2)) >= 1
    cumulative = {
        "tier": 1,
        "period": "black_empire",
        "last_success_at": (NOW - timedelta(hours=25)).isoformat(),
        "success_expected_games": 1000,
        "expected_games": 1299,
    }
    assert mulligan.due_score(cumulative, NOW) is None
    assert mulligan.due_score({**cumulative, "expected_games": 1300}, NOW) == 1.0
    # Growth alone never re-reads a page more than once a day.
    young = {**cumulative, "last_success_at": (NOW - timedelta(hours=23)).isoformat()}
    assert mulligan.due_score({**young, "expected_games": 5000}, NOW) is None
    retry = {**base, "next_attempt_after": (NOW + timedelta(minutes=5)).isoformat()}
    assert mulligan.due_score(retry, NOW) is None
    order = mulligan.due_targets(
        [
            {**base, "tier": 2, "expected_games": 99999, "target_key": "tier2"},
            {**base, "period": "past_30_days", "target_key": "month"},
            {**base, "period": "patch_36.6.3", "target_key": "patch"},
            {**base, "period": "black_empire_ban", "target_key": "bans"},
            {**base, "period": "black_empire", "target_key": "expansion"},
            {**base, "target_key": "week"},
        ],
        NOW,
    )
    assert [target["target_key"] for target in order] == [
        "expansion",
        "bans",
        "week",
        "patch",
        "month",
        "tier2",
    ]


def test_default_plan_estimate_fits_the_daily_page_capacity() -> None:
    matrix = _matrix(
        {
            ("standard", rank, period, "any_player"): [
                (f"A{index}", 2000) for index in range(10)
            ]
            for rank in mulligan.RANKS
            for period in ("past_day", "past_week", "black_empire")
        }
    )
    estimate = mulligan.estimate_plan(matrix, [], mulligan.Settings())
    assert estimate["targets"] == 6 * 3 * 10 + 6 * 2 * 10
    # Eight three-hourly runs of 200 pages are the scheduled capacity.
    assert estimate["pages_per_day_by_ttl"] < 8 * mulligan.Settings().max_pages


# --- acquisition -----------------------------------------------------------------


def _page(html: str, status: int = 200) -> SimpleNamespace:
    return SimpleNamespace(html=html, http_status=status)


def test_acquirer_prefers_the_free_route_and_caps_scrape_do(monkeypatch) -> None:
    good = card_stats_html([FORGE], games=10)
    paid_calls: list[str] = []

    async def free(source):
        return _page("<html>Just a moment...</html>")

    async def paid(source, **options):
        paid_calls.append(source.url)
        assert options["skip_providers"] == {
            "decodo",
            "firecrawl",
            "brightdata",
            "scrapfly",
        }
        return SimpleNamespace(html=good, request_credits=5, backend="scrape_do")

    acquirer = mulligan.Acquirer(
        scrape_do_credit_cap=29, fetch_free=free, fetch_paid=paid
    )
    html, meta = asyncio.run(
        acquirer.fetch("https://www.hsguru.com/card-stats?a=1", "cards")
    )
    assert html == good and meta == {"backend": "scrape_do", "request_credits": 5}
    # 0 + 25 reserved fitted the cap of 29; 5 spent + 25 reserved does not.
    assert acquirer.paid_allowed() is False
    with pytest.raises(RuntimeError, match="paid=budget"):
        asyncio.run(acquirer.fetch("https://www.hsguru.com/card-stats?a=2", "cards"))
    assert paid_calls == ["https://www.hsguru.com/card-stats?a=1"]

    async def free_ok(source):
        return _page(good)

    free_route = mulligan.Acquirer(
        scrape_do_credit_cap=0, fetch_free=free_ok, fetch_paid=paid
    )
    _, meta = asyncio.run(
        free_route.fetch("https://www.hsguru.com/card-stats?a=3", "cards")
    )
    assert meta["backend"] == "flaresolverr" and free_route.pages_free == 1


def test_free_circuit_opens_and_budget_exhaustion_stops_the_run() -> None:
    calls = {"free": 0}

    async def free(source):
        calls["free"] += 1
        raise RuntimeError("solver down")

    acquirer = mulligan.Acquirer(scrape_do_credit_cap=0, fetch_free=free)
    for index in range(mulligan.FREE_CIRCUIT_THRESHOLD + 2):
        with pytest.raises(RuntimeError):
            asyncio.run(
                acquirer.fetch(f"https://www.hsguru.com/card-stats?a={index}", "cards")
            )
    assert calls["free"] == mulligan.FREE_CIRCUIT_THRESHOLD
    assert acquirer.stop_reason == "scrape_do_budget_exhausted"


# --- refresh and storage ---------------------------------------------------------


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.cards_index.cards_by_dbfid", dict)
    save_dataset(
        "hsguru_meta_matrix",
        _matrix(
            {
                ("standard", "legend", "past_week", "any_player"): [
                    ("Dragon Warrior", 9352)
                ],
            }
        ),
    )
    _save_catalog([_deck(41919872, 461)])
    return tmp_path


def _save_catalog(rows: list[dict[str, Any]]) -> None:
    # Deck catalogs are plain JSON files written by app.hsguru_decks.
    write_json(dataset_path("hsguru_deck_catalog_standard_all"), _deck_catalog(rows))


def _fetcher(pages: dict[str, str], calls: list[str] | None = None):
    async def fetch(url: str, kind: str):
        if calls is not None:
            calls.append(url)
        for marker, html in pages.items():
            if marker in url:
                if isinstance(html, Exception):
                    raise html
                return html, {"backend": "flaresolverr", "request_credits": 0}
        raise AssertionError(f"unexpected url {url}")

    return fetch


PAGES = {
    "card-stats?archetype=": card_stats_html([FORGE, DUCK, RARE], games=9352),
    "card-stats?deck_id=": card_stats_html([DUCK], games=461),
    "/archetype/": MATCHUPS,
    "/deck/": MATCHUPS,
}


def _refresh(pages=PAGES, *, now=NOW, calls=None, **kwargs) -> dict[str, Any]:
    return asyncio.run(
        mulligan.refresh_hsguru_mulligan(
            fetch_page=_fetcher(pages, calls), now=now, **kwargs
        )
    )


def _count(path: Path, table: str) -> int:
    conn = sqlite3.connect(path / "hs_parses.db")
    try:
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_refresh_reads_due_targets_and_skips_fresh_ones(isolated) -> None:
    calls: list[str] = []
    result = _refresh(calls=calls)
    # Archetype cards + matchups (past_week is core) and deck cards + matchups.
    assert result["state"] == "ok" and result["published"] is True
    assert (result["attempted"], result["changed"], result["targets_with_data"]) == (
        4,
        4,
        4,
    )
    assert _count(isolated, store.CARDS) == 4 and _count(isolated, store.MATCHUPS) == 4
    assert result["stop_reason"] is None and result["scrape_do_credits"] == 0

    again = _refresh(now=NOW + timedelta(hours=1), calls=calls)
    assert again["attempted"] == 0 and len(calls) == 4

    later = _refresh(now=NOW + timedelta(hours=73))
    # Only the archetype past_week mulligan (tier 1, 72 h) is due again: the
    # deck's expansion window is cumulative (168 h unless it grows) and tier-3
    # matchups wait 144 h. An unchanged page adds no snapshot.
    assert (later["attempted"], later["unchanged"], later["changed"]) == (1, 1, 0)
    assert _count(isolated, store.SNAPSHOTS) == 4


def test_failures_back_off_and_page_budget_is_reported(isolated) -> None:
    pages = {**PAGES, "card-stats?archetype=": RuntimeError("free=x; paid=budget")}
    result = _refresh(pages, max_pages=3)
    assert result["state"] == "partial" and result["failed"] == 1
    assert (
        result["stop_reason"] == "page_budget" and result["targets_due_after_run"] == 1
    )
    conn = sqlite3.connect(isolated / "hs_parses.db")
    conn.row_factory = sqlite3.Row
    failed = dict(
        conn.execute(f"SELECT * FROM {store.TARGETS} WHERE state = 'failed'").fetchone()
    )
    conn.close()
    assert failed["consecutive_failures"] == 1 and failed["last_error"].startswith(
        "free=x"
    )
    assert failed["next_attempt_after"] == (NOW + timedelta(minutes=30)).isoformat()
    # Backoff: the failed target is not retried ten minutes later.
    retry = _refresh(now=NOW + timedelta(minutes=10))
    assert retry["attempted"] == 1


def test_targets_that_leave_the_plan_stay_readable_but_inactive(isolated) -> None:
    _refresh()
    _save_catalog([])
    _refresh(now=NOW + timedelta(hours=1))
    data = mulligan.get_statistics(
        mulligan.Selector(
            kind="cards",
            subject_type="deck",
            format_name=None,
            archetype=None,
            deck_id=41919872,
            rank="all",
            period="black_empire",
        )
    )
    assert data is not None and data["active"] is False and data["stale"] is True


def test_prune_keeps_the_latest_snapshot_of_every_target(isolated) -> None:
    _refresh()
    pages = {**PAGES, "card-stats?archetype=": card_stats_html([DUCK], games=9999)}
    _refresh(pages, now=NOW + timedelta(days=40))
    conn = sqlite3.connect(isolated / "hs_parses.db")
    try:
        pruned = store.prune(conn, now=NOW + timedelta(days=40), snapshot_days=30)
        latest = {
            row[0]
            for row in conn.execute(f"SELECT latest_snapshot_id FROM {store.TARGETS}")
        }
        remaining = {
            row[0] for row in conn.execute(f"SELECT id FROM {store.SNAPSHOTS}")
        }
    finally:
        conn.close()
    # The refresh already pruned the replaced archetype snapshot.
    assert pruned["snapshots"] == 0 and remaining == latest


# --- read API --------------------------------------------------------------------


@pytest.fixture()
def client(isolated):
    _refresh()
    from app.main import app

    return TestClient(app)


def test_mulligan_endpoint_contract(client) -> None:
    response = client.get(
        "/v1/hsguru/mulligan",
        params={
            "archetype": "dragon warrior",
            "format": "standard",
            "rank": "legend",
            "period": "past_week",
            "min_mull_count": 25,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["source_id"] == "hsguru_mulligan" and body["meta"]["count"] == 2
    data = body["data"]
    assert data["archetype"] == "Dragon Warrior" and data["games"] == 9352
    assert [row["card_name"] for row in data["rows"]] == [
        "Forge of Origination",
        "Mother Duck",
    ]
    assert (
        data["rows"][0]["not_drawn_count"] == 1242
        and "snapshot_id" not in data["rows"][0]
    )
    assert data["source_url"].startswith("https://www.hsguru.com/card-stats?archetype=")

    deck = client.get(
        "/v1/hsguru/mulligan", params={"deck_id": 41919872, "period": "current"}
    )
    assert deck.status_code == 200, deck.text
    assert deck.json()["data"]["period"] == "black_empire"


def test_class_matchups_slices_history_and_coverage(client) -> None:
    params = {
        "archetype": "Dragon Warrior",
        "format": "standard",
        "rank": "legend",
        "period": "past_week",
    }
    matchups = client.get("/v1/hsguru/class-matchups", params=params).json()["data"]
    assert matchups["winrate"] == 50.1 and [
        row["opponent_class"] for row in matchups["rows"]
    ] == [
        "deathknight",
        "mage",
    ]
    slices = client.get(
        "/v1/hsguru/mulligan/slices", params={"archetype": "Dragon Warrior"}
    ).json()
    assert {(row["kind"], row["rank"], row["period"]) for row in slices["data"]} == {
        ("cards", "legend", "past_week"),
        ("matchups", "legend", "past_week"),
    }
    history = client.get(
        "/v1/hsguru/mulligan/history", params={**params, "card": str(FORGE_DBF)}
    )
    points = history.json()["data"]["points"]
    assert len(points) == 1 and points[0]["card"]["mulligan_count"] == 633
    coverage = client.get("/v1/hsguru/mulligan/coverage")
    assert coverage.status_code == 200
    assert coverage.headers["cache-control"] == "no-store"
    assert coverage.json()["data"]["runs"][0]["changed"] == 4


def test_invalid_and_missing_slices(client) -> None:
    assert (
        client.get("/v1/hsguru/mulligan", params={"format": "standard"}).status_code
        == 422
    )
    both = {"archetype": "Dragon Warrior", "deck_id": 1, "format": "standard"}
    assert client.get("/v1/hsguru/mulligan", params=both).status_code == 422
    assert (
        client.get(
            "/v1/hsguru/mulligan", params={"archetype": "Dragon Warrior"}
        ).status_code
        == 422
    )
    missing = client.get(
        "/v1/hsguru/mulligan",
        params={"archetype": "Dragon Warrior", "format": "standard", "coin": "on_coin"},
    )
    assert missing.status_code == 404 and "/v1/hsguru/mulligan/slices" in missing.text
    bad_rank = {"archetype": "Dragon Warrior", "format": "standard", "rank": "top_100"}
    assert client.get("/v1/hsguru/mulligan", params=bad_rank).status_code == 422


def test_graphql_assembles_postgres_rows_like_rest(isolated) -> None:
    _refresh()
    selector = mulligan.Selector(
        kind="cards",
        subject_type="archetype",
        format_name="standard",
        archetype="Dragon Warrior",
        deck_id=None,
        rank="legend",
        period="past_week",
    )
    conn = sqlite3.connect(isolated / "hs_parses.db")
    conn.row_factory = sqlite3.Row
    try:
        target = mulligan._find(conn, selector)
        snapshot = store.snapshot(conn, target["latest_snapshot_id"])
        rows = [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM {store.CARDS} WHERE snapshot_id = ?", (snapshot["id"],)
            )
        ]
    finally:
        conn.close()
    assert mulligan.assemble_statistics(
        target, snapshot, rows
    ) == mulligan.get_statistics(selector)


# --- PostgreSQL mirror contract --------------------------------------------------


def test_postgres_migration_mirrors_sqlite_columns_and_types() -> None:
    sql = (ROOT / "platform/sql/017_hsguru_mulligan.sql").read_text()
    conn = sqlite3.connect(":memory:")
    store.ensure_hsguru_mulligan_schema(conn)
    expected_type = {"INTEGER": "bigint", "REAL": "real", "TEXT": "text"}
    for table in store.TABLES:
        block = re.search(
            rf"CREATE TABLE IF NOT EXISTS analytics\.{table} \((.*?)\n\);",
            sql,
            re.DOTALL,
        )
        assert block, table
        pg_columns = [
            line.strip().rstrip(",").split()[:2]
            for line in block.group(1).strip().splitlines()
        ]
        sqlite_columns = [
            (row[1], row[2]) for row in conn.execute(f"PRAGMA table_info({table})")
        ]
        assert [name for name, _ in pg_columns] == [
            name for name, _ in sqlite_columns
        ], table
        for (name, pg_type), (_name, sqlite_type) in zip(
            pg_columns, sqlite_columns, strict=True
        ):
            assert pg_type == (
                "jsonb" if name.endswith("_json") else expected_type[sqlite_type]
            ), (table, name)
        assert "id bigint PRIMARY KEY" in block.group(1)


def test_analytics_delta_contract_lists_every_table() -> None:
    stage = (ROOT / "platform/scripts/stage-analytics.py").read_text()
    merge = (ROOT / "platform/scripts/merge-analytics.sql").read_text()
    migrations = (ROOT / "platform/scripts/apply-migrations.sh").read_text()
    for table in store.TABLES:
        assert f"'{table}'" in stage and f"'{table}'" in merge
        # Targets update in place and snapshots age out: never append-only.
        assert f"'{table}'" not in stage.split("APPEND_ONLY")[1].split(")")[0]
    assert "017_hsguru_mulligan.sql" in migrations


def test_systemd_timer_matches_the_schedule_inventory() -> None:
    from app.parser_control_schedule import _SCHEDULES

    spec = next(spec for spec in _SCHEDULES if spec.id == "refresh-hsguru-mulligan")
    timer = (ROOT / "systemd" / spec.systemd_unit).read_text()
    service = (
        ROOT / "systemd" / spec.systemd_unit.replace(".timer", ".service")
    ).read_text()
    assert all(f"OnCalendar={value}" in timer for value in spec.on_calendar)
    assert (
        "refresh-hsguru-mulligan --scheduled --schedule-id refresh-hsguru-mulligan"
        in service
    )


def test_premium_filter_page_goes_to_the_logged_in_paid_profile() -> None:
    paywall = card_stats_html([], games=0) + "You do not have access to these filters"

    async def free(source):
        return _page(paywall)

    async def paid(source, **options):
        return SimpleNamespace(
            html=card_stats_html([FORGE], games=10), request_credits=5
        )

    acquirer = mulligan.Acquirer(
        scrape_do_credit_cap=100, fetch_free=free, fetch_paid=paid
    )
    html, meta = asyncio.run(
        acquirer.fetch("https://www.hsguru.com/card-stats?a=1", "cards")
    )
    assert "Forge of Origination" in html and meta["request_credits"] == 5
    # A premium filter is not a solver failure: the free circuit stays closed.
    assert acquirer.free_failures_in_row == 0 and acquirer.pages_scrape_do == 1
