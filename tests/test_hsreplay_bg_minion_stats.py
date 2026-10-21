from __future__ import annotations

import asyncio
import copy
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from app import bg_minion_stats_formulas as formulas
from app import bg_minion_stats_store as store
from app import hsreplay_bg_minion_stats as stats

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/hsreplay_bg_minion_stats"
BONEHEAD = 104551
POOL_MINION = 59670
NON_POOL = 59202  # present in position rows but not in the minion list
SLICE = stats.MinionStatsSlice("TOP_50_PERCENT", "LAST_7_DAYS", True)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _payloads(extra_minions: int = 160, as_of: str | None = None) -> dict[str, dict[str, Any]]:
    """Fixture payloads widened with clones of Harmless Bonehead to pass the size gate."""

    payloads = {
        "minion_list": _fixture("battlegrounds_minion_list"),
        "positions": _fixture("battlegrounds_minion_position"),
        "purchases": _fixture("battlegrounds_purchase_rates_by_turn"),
        "medians": _fixture("battlegrounds_minion_stats"),
    }
    keys = {"minion_list": "minion_dbf_id", "positions": "minion_dbf_id", "purchases": "minion_dbf_id", "medians": "normal_dbf_id"}
    for name, payload in payloads.items():
        rows = payload["series"]["data"]
        template = [row for row in rows if row[keys[name]] == BONEHEAD]
        for index in range(extra_minions):
            for row in template:
                clone = copy.deepcopy(row)
                clone[keys[name]] = 900_000 + index
                rows.append(clone)
        if as_of:
            payload["as_of"] = as_of
    return payloads


def _fetcher(payloads: dict[str, dict[str, Any]], calls: list[str] | None = None):
    by_query = {query: name for name, query in stats.QUERIES.items()}

    async def fetch(url: str, cache_key: str) -> dict[str, Any]:
        query = url.split("/analytics/query/")[1].split("/")[0]
        if calls is not None:
            calls.append(url)
        value = payloads[by_query[query]]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    return fetch


async def _no_names() -> dict[int, str]:
    return {47: "Undead"}


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    cards = {
        BONEHEAD: {
            "id": "BG28_300",
            "dbfId": BONEHEAD,
            "name": "Harmless Bonehead",
            "techLevel": 1,
            "races": ["UNDEAD"],
            "mechanics": ["DEATHRATTLE"],
            "attack": 1,
            "health": 1,
            "battlegroundsPremiumDbfId": 104554,
        },
        104554: {"id": "BG28_300_G", "dbfId": 104554, "name": "Harmless Bonehead", "battlegroundsNormalDbfId": BONEHEAD},
    }
    monkeypatch.setattr(stats, "cards_by_dbfid", lambda: cards)
    monkeypatch.setattr(stats, "cards_by_id", lambda locale="enUS": {card["id"]: card for card in cards.values()})
    monkeypatch.setattr(
        stats, "_load_raw_cards", lambda locale="enUS": [{"dbfId": BONEHEAD, "name": "Безобидная черепушка"}]
    )
    return tmp_path


def _refresh(payloads, *, slices=(SLICE,), calls=None) -> dict[str, Any]:
    return asyncio.run(stats.refresh_bg_minion_stats(slices=slices, fetch=_fetcher(payloads, calls), names=_no_names))


# --- formulas: dbf 104551 against hsreplay.net (TOP_50_PERCENT, LAST_7_DAYS) --------


def _bonehead() -> tuple[dict[str, Any], dict[int, float]]:
    payload = _fixture("battlegrounds_minion_list")
    minions = {row["dbf_id"]: row for row in formulas.parse_minion_list(payload)}
    return minions[BONEHEAD], formulas.parse_pct_players_remaining(payload)


def test_header_metrics_match_hsreplay_page_for_normal_and_golden() -> None:
    minion, remaining = _bonehead()
    normal = formulas.summarize_variant(minion["rounds"]["normal"], remaining)
    golden = formulas.summarize_variant(minion["rounds"]["golden"], remaining)

    # hsreplay.net/ru/battlegrounds/minions/104551/: -0.01 / 56.8% / 1.9% and -0.09 / 51.7% / 0.2%
    assert round(normal["impact"], 2) == -0.01
    assert round(normal["combat_winrate"], 1) == 56.8
    assert round(normal["popularity"], 1) == 1.9
    assert round(golden["impact"], 2) == -0.09
    assert round(golden["combat_winrate"], 1) == 51.7
    assert round(golden["popularity"], 1) == 0.2
    # The previous with/(with+without) shortcut is what used to diverge from the site.
    shortcut = normal["games_with_minion"] / (normal["games_with_minion"] + normal["games_without_minion"]) * 100
    assert round(shortcut, 2) == 1.83


def test_round_metrics_follow_the_site_definitions() -> None:
    minion, _remaining = _bonehead()
    first = minion["rounds"]["normal"][0]
    metrics = formulas.round_metrics(first)
    assert first["combat_round"] == 1
    expected_impact = (
        first["sum_placement_without"] / first["games_without_minion"]
        - first["sum_placement_with"] / first["games_with_minion"]
    )
    assert metrics["impact"] == pytest.approx(expected_impact)
    assert metrics["combat_winrate"] == pytest.approx(100 * first["wins"] / (first["wins"] + first["losses"]))
    public = formulas.public_round({**first, **metrics})
    assert public["popularity"] == first["at_least_one"]


def test_position_chart_matches_hsreplay_all_compositions() -> None:
    rows = formulas.aggregate_positions(_fixture("battlegrounds_minion_position"), {BONEHEAD, POOL_MINION})
    overall = next(r for r in rows if r["dbf_id"] == BONEHEAD and r["composition_id"] == 0 and r["board"] == "normal")
    chart = formulas.position_chart(overall)
    assert [round(cell["popularity"], 1) for cell in chart] == [6.7, 14.6, 21.0, 22.3, 18.9, 12.0, 4.4]
    assert [round(cell["impact"], 2) + 0.0 for cell in chart] == [-0.26, 0.0, -0.01, 0.11, 0.05, -0.02, -0.12]
    assert all(row["dbf_id"] != NON_POOL for row in rows)


def test_composition_filter_sums_rows_of_that_composition_only() -> None:
    payload = _fixture("battlegrounds_minion_position")
    rows = formulas.aggregate_positions(payload, {BONEHEAD})
    expected = sum(
        (row["normal"] or {}).get("occurrences") or 0
        for row in payload["series"]["data"]
        if row["minion_dbf_id"] == BONEHEAD and row["composition_id"] == 47
    )
    selected = next(r for r in rows if r["composition_id"] == 47 and r["board"] == "normal")
    assert selected["occurrences"] == expected
    assert {r["composition_id"] for r in rows} >= {0, 47}
    assert -1 not in {r["composition_id"] for r in rows}


def test_purchase_rates_and_medians() -> None:
    purchases = formulas.parse_purchases(_fixture("battlegrounds_purchase_rates_by_turn"), {BONEHEAD})
    first_copy = [formulas.public_purchase(row)["first_copy"] for row in purchases][:4]
    assert [round(value, 1) for value in first_copy] == [20.9, 1.5, 6.3, 5.1]
    assert formulas.purchase_rate(10, 30) is None  # the site needs more than 30 offers
    medians = formulas.parse_medians(_fixture("battlegrounds_minion_stats"), {BONEHEAD})
    last = [row for row in medians if row["variant"] == "normal" and row["combat_round"] == 16][0]
    assert (last["median_attack"], last["median_health"]) == (8.0, 1.0)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["series"]["data"].append(copy.deepcopy(p["series"]["data"][0])), "duplicate"),
        (lambda p: p["series"]["data"][0]["normal_aggregates"][0].update(count_of_games_with_minion=-1), "non-negative"),
        (lambda p: p["series"]["data"][0]["normal_aggregates"][0].update(sum_of_placements_for_players_with_minion=1), "1..8"),
        (lambda p: p["series"]["data"][0]["normal_aggregates"][0].update(combat_round=17), "combat_round"),
        (lambda p: p["series"]["data"][0]["normal_aggregates"][0].update(total_wins=None), "paired"),
        (lambda p: p.update(series={"data": {}}), "series.data"),
    ],
)
def test_minion_list_schema_is_strict(mutate, message) -> None:
    payload = _fixture("battlegrounds_minion_list")
    mutate(payload)
    with pytest.raises(formulas.MinionStatsPayloadError, match=message):
        formulas.parse_minion_list(payload)


def test_pct_players_remaining_must_cover_every_round() -> None:
    payload = _fixture("battlegrounds_minion_list")
    del payload["series"]["metadata"]["pct_players_remaining"]["1"]
    minion = formulas.parse_minion_list(payload)[0]
    with pytest.raises(formulas.MinionStatsPayloadError, match="lacks round"):
        formulas.summarize_variant(minion["rounds"]["normal"], formulas.parse_pct_players_remaining(payload))


# --- pipeline -----------------------------------------------------------------


def test_refresh_stores_complete_snapshot_and_dataset(isolated) -> None:
    calls: list[str] = []
    result = _refresh(_payloads(), calls=calls)

    assert result["ok"] is True and result["published"] is True
    assert result["slices"][0]["state"] == "stored"
    assert len(calls) == 4
    assert all("BattlegroundsMMRPercentile=TOP_50_PERCENT&BattlegroundsTimeRange=LAST_7_DAYS" in url for url in calls)
    assert not any("scrape.do" in url for url in calls)
    rows = result["slices"][0]["rows"]
    assert rows["bg_minion_stat_summaries"] == 2 * 162
    assert rows["bg_minion_stat_positions"] > 0 and rows["bg_minion_stat_purchases"] > 0
    dataset = json.loads((isolated / "datasets" / f"{stats.SOURCE_ID}.json").read_text())
    structured = dataset["data"]["structured"]
    assert structured["type"] == "bg_minion_stats"
    assert structured["slices"][0]["minions_total"] == 162
    status = json.loads((isolated / "statuses" / f"{stats.SOURCE_ID}.json").read_text())
    assert status["state"] == "ok" and status["serving_cached_dataset"] is False


def test_unchanged_upstream_does_not_duplicate_history(isolated) -> None:
    _refresh(_payloads(as_of="2026-10-21T13:41:22Z"))
    second = _refresh(_payloads(as_of="2026-10-21T13:41:22Z"))
    assert second["slices"][0]["state"] == "unchanged"
    third = _refresh(_payloads(as_of="2026-10-22T13:41:22Z"))
    assert third["slices"][0]["state"] == "stored"
    history = stats.get_minion_stats_history(BONEHEAD)
    assert len(history["points"]) == 2
    assert history["points"][0]["normal"]["popularity"] == pytest.approx(1.885, abs=0.001)


def test_regression_and_fetch_failures_keep_last_known_good(isolated) -> None:
    first = _refresh(_payloads(as_of="2026-10-21T00:00:00Z"))
    snapshot_id = first["slices"][0]["snapshot_id"]

    shrunk = _refresh(_payloads(extra_minions=60, as_of="2026-10-22T00:00:00Z"))
    assert shrunk["slices"][0]["state"] == "quality_rejected"
    assert shrunk["serving_cached_dataset"] is True
    assert any("too small" in error for error in shrunk["slices"][0]["quality_errors"])

    broken = _payloads(as_of="2026-10-22T00:00:00Z")
    broken["positions"] = RuntimeError("upstream timeout")
    failed = _refresh(broken)
    assert failed["slices"][0]["state"] == "fetch_error"
    assert failed["ok"] is False and failed["serving_cached_dataset"] is True

    bad_schema = _payloads(as_of="2026-10-22T00:00:00Z")
    bad_schema["medians"]["series"]["data"][0]["is_premium"] = 2
    rejected = _refresh(bad_schema)
    assert rejected["slices"][0]["state"] == "schema_rejected"

    detail = stats.get_minion_stats(BONEHEAD)
    assert detail["slice"]["snapshot_id"] == snapshot_id
    status = json.loads((isolated / "statuses" / f"{stats.SOURCE_ID}.json").read_text())
    assert status["state"] == "ok" and status["serving_cached_dataset"] is True
    assert status["last_refresh_state"] == "fetch_error"


def test_slices_are_independent(isolated) -> None:
    payloads = _payloads()
    core = stats.MinionStatsSlice("TOP_20_PERCENT", "LAST_7_DAYS", False)
    calls: list[str] = []
    result = _refresh(payloads, slices=(SLICE, core), calls=calls)
    assert [row["state"] for row in result["slices"]] == ["stored", "stored"]
    assert sum("battlegrounds_minion_position" in url for url in calls) == 1
    assert stats.get_minion_stats(BONEHEAD, mmr="TOP_20_PERCENT")["positions"] is None
    assert {row["mmr_percentile"] for row in stats.available_slices()} == {"TOP_50_PERCENT", "TOP_20_PERCENT"}


def test_retention_keeps_summaries_and_the_latest_snapshot(isolated) -> None:
    _refresh(_payloads(as_of="2026-10-01T00:00:00Z"))
    _refresh(_payloads(as_of="2026-10-02T00:00:00Z"))
    conn = sqlite3.connect(isolated / "hs_parses.db")
    try:
        old_id, latest_id = [row[0] for row in conn.execute("SELECT id FROM bg_minion_stat_snapshots ORDER BY id")]
        conn.execute("UPDATE bg_minion_stat_snapshots SET fetched_at = ? WHERE id = ?", ("2026-01-01T00:00:00+00:00", old_id))
        conn.execute("UPDATE bg_minion_stat_snapshots SET fetched_at = ? WHERE id = ?", ("2026-01-02T00:00:00+00:00", latest_id))
        conn.commit()
        deleted = store.prune_details(conn, now=datetime(2026, 10, 21, tzinfo=UTC), retention_days=stats.retention_days())
        assert deleted["bg_minion_stat_rounds"] > 0
        for table in store.DETAIL_TABLES:
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE snapshot_id = ?", (old_id,)).fetchone()[0] == 0
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE snapshot_id = ?", (latest_id,)).fetchone()[0] > 0
        assert conn.execute("SELECT count(*) FROM bg_minion_stat_summaries WHERE snapshot_id = ?", (old_id,)).fetchone()[0] > 0
    finally:
        conn.close()


def test_retention_floor_cannot_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("HS_BG_MINION_STATS_DETAIL_RETENTION_DAYS", "0")
    assert min(stats.retention_days().values()) == 7


# --- API contract ---------------------------------------------------------------


@pytest.fixture()
def client(isolated, monkeypatch):
    async def no_catalog(dbf_ids):
        return {
            BONEHEAD: {
                "card_id": "BG28_300",
                "name_ru": "Безобидная черепушка",
                "name_en": "Harmless Bonehead",
                "tavern_tier": 1,
                "framed_image": "/uploads/framed/BG28_300.png",
                "golden_image": "/uploads/golden/BG28_300-abc.png",
                "art_image": "/uploads/art/BG28_300.jpg",
                "golden_dbf": 104554,
                "in_pool": True,
            }
        }

    monkeypatch.setattr(stats, "catalog_cards", no_catalog)
    _refresh(_payloads(as_of="2026-10-21T13:41:22Z"))
    from app.main import app

    return TestClient(app)


def test_detail_endpoint_contract(client) -> None:
    response = client.get("/v1/battlegrounds/minions/104551/stats")
    assert response.status_code == 200, response.text
    body = response.json()
    data = body["data"]
    assert body["meta"]["source_id"] == stats.SOURCE_ID
    assert data["minion"]["card_id"] == "BG28_300"
    assert data["minion"]["name_ru"] == "Безобидная черепушка"
    assert data["minion"]["images"]["card"] == "https://api.kolodahearthstone.com/uploads/framed/BG28_300.png"
    assert round(data["normal"]["popularity"], 1) == 1.9 and round(data["golden"]["impact"], 2) == -0.09
    assert [row["combat_round"] for row in data["rounds"]] == list(range(1, 17))
    assert data["rounds"][0]["normal"]["games_with_minion"] > 0
    assert data["rounds"][0]["golden"] is None
    assert data["positions"]["composition_id"] == 0
    assert len(data["positions"]["normal"]["positions"]) == 7
    compositions = data["positions"]["compositions"]
    assert [row["composition_id"] for row in compositions] == [2, 3, 8, 9, 20, 36, 39, 41, 47, 56]
    assert {"composition_id": 47, "name": "Undead"} in compositions
    assert round(data["purchases"][0]["first_copy"], 1) == 20.9
    assert data["medians"][-1]["combat_round"] == 16
    assert data["slice"]["as_of"]["positions"] == "2026-10-21T13:41:22Z"
    assert "formula_source" in data["methodology"]

    by_card = client.get("/v1/battlegrounds/minions/BG28_300/stats").json()["data"]
    golden_card = client.get("/v1/battlegrounds/minions/BG28_300_G/stats").json()["data"]
    assert by_card["slice"] == data["slice"] == golden_card["slice"]

    composition = client.get("/v1/battlegrounds/minions/104551/stats?composition=47").json()["data"]
    assert composition["positions"]["composition_id"] == 47
    assert composition["positions"]["normal"]["occurrences"] < data["positions"]["normal"]["occurrences"]


def test_list_history_and_errors(client) -> None:
    listing = client.get("/v1/battlegrounds/minions/stats?limit=5&sort=impact").json()
    assert listing["meta"]["count"] == 162 and len(listing["data"]) == 5
    assert set(listing["data"][0]) >= {"dbf_id", "card_id", "normal", "golden", "images"}
    tier = client.get("/v1/battlegrounds/minions/stats?tavern_tier=2").json()
    assert tier["meta"]["count"] == 0
    history = client.get("/v1/battlegrounds/minions/104551/stats/history").json()["data"]
    assert len(history["points"]) == 1
    slices = client.get("/v1/battlegrounds/minions/stats/slices").json()["data"]
    assert slices[0]["has_positions"] is True

    assert client.get("/v1/battlegrounds/minions/104551/stats?mmr=TOP_1_PERCENT").status_code == 422
    assert client.get("/v1/battlegrounds/minions/stats?period=YESTERDAY").status_code == 422
    assert client.get("/v1/battlegrounds/minions/UNKNOWN_CARD/stats").status_code == 404
    assert client.get("/v1/battlegrounds/minions/104551/stats?period=CURRENT_BATTLEGROUNDS_PATCH").status_code == 404


def test_graphql_assembles_postgres_rows_like_rest(isolated) -> None:
    _refresh(_payloads())
    conn = sqlite3.connect(isolated / "hs_parses.db")
    conn.row_factory = sqlite3.Row
    try:
        snapshot = dict(conn.execute("SELECT * FROM bg_minion_stat_snapshots").fetchone())
        rows = store.minion_detail_rows(conn, snapshot["id"], BONEHEAD)
    finally:
        conn.close()
    # PostgreSQL returns jsonb as decoded objects.
    snapshot["pct_players_remaining_json"] = json.loads(snapshot["pct_players_remaining_json"])
    snapshot["composition_names_json"] = json.loads(snapshot["composition_names_json"])
    positions = [
        {
            **{k: v for k, v in row.items() if k not in {"position_occurrences", "position_sum_placement"}},
            "position_occurrences_json": row["position_occurrences"],
            "position_sum_placement_json": row["position_sum_placement"],
        }
        for row in rows["positions"]
    ]
    mirrored = stats.assemble_mirrored_stats(
        {**rows, "snapshot": snapshot, "positions": positions}, dbf_id=BONEHEAD, composition_id=0, catalog=None
    )
    assert mirrored == stats.get_minion_stats(BONEHEAD)
    assert mirrored["minion"]["catalog"] is False and mirrored["minion"]["images"] is None


# --- PostgreSQL mirror contract -------------------------------------------------


def test_postgres_migration_mirrors_sqlite_columns_and_types() -> None:
    sql = (ROOT / "platform/sql/016_bg_minion_stats.sql").read_text()
    conn = sqlite3.connect(":memory:")
    store.ensure_bg_minion_stats_schema(conn)
    expected_type = {"INTEGER": "bigint", "REAL": "real", "TEXT": "text"}
    for table in store.TABLES:
        block = re.search(rf"CREATE TABLE IF NOT EXISTS analytics\.{table} \((.*?)\n\);", sql, re.S)
        assert block, table
        pg_columns = [line.strip().rstrip(",").split()[:2] for line in block.group(1).strip().splitlines()]
        sqlite_columns = [(row[1], row[2]) for row in conn.execute(f"PRAGMA table_info({table})")]
        assert [name for name, _ in pg_columns] == [name for name, _ in sqlite_columns], table
        for (name, pg_type), (_name, sqlite_type) in zip(pg_columns, sqlite_columns, strict=True):
            assert pg_type == ("jsonb" if name.endswith("_json") else expected_type[sqlite_type]), (table, name)
        assert "id bigint PRIMARY KEY" in block.group(1)


def test_analytics_delta_contract_lists_every_table() -> None:
    stage = (ROOT / "platform/scripts/stage-analytics.py").read_text()
    merge = (ROOT / "platform/scripts/merge-analytics.sql").read_text()
    migrations = (ROOT / "platform/scripts/apply-migrations.sh").read_text()
    for table in store.TABLES:
        assert f"'{table}'" in stage and f"'{table}'" in merge
        # Retention deletes detail rows, so the delta must propagate deletions.
        assert f"'{table}'" not in stage.split("APPEND_ONLY")[1].split(")")[0]
    assert "016_bg_minion_stats.sql" in migrations


def test_missing_as_of_never_freezes_a_slice(isolated) -> None:
    def without_as_of():
        payloads = _payloads()
        for payload in payloads.values():
            payload.pop("as_of", None)
        return payloads

    _refresh(without_as_of())
    again = _refresh(without_as_of())
    assert again["slices"][0]["state"] == "stored"


def test_pool_drop_override_and_honest_partial_status(isolated, monkeypatch) -> None:
    _refresh(_payloads(as_of="2026-10-21T00:00:00Z"))
    monkeypatch.setattr(stats, "MIN_MINIONS", 10)
    rejected = _refresh(_payloads(extra_minions=40, as_of="2026-10-22T00:00:00Z"))
    assert "regressed" in rejected["slices"][0]["quality_errors"][0]
    monkeypatch.setenv("HS_BG_MINION_STATS_ALLOW_POOL_DROP", "1")
    accepted = _refresh(_payloads(extra_minions=40, as_of="2026-10-22T00:00:00Z"))
    assert accepted["slices"][0]["state"] == "stored"

    never_stored = stats.MinionStatsSlice("ALL", "LAST_7_DAYS", False)
    broken = _payloads(as_of="2026-10-23T00:00:00Z")
    broken["medians"] = RuntimeError("down")
    partial = _refresh(broken, slices=(never_stored,))
    assert partial["serving_cached_dataset"] is False
    status = json.loads((isolated / "statuses" / f"{stats.SOURCE_ID}.json").read_text())
    assert status["state"] == "fetch_error"


def test_degraded_and_invalid_requests(client, monkeypatch) -> None:
    good = client.get("/v1/battlegrounds/minions/104551/stats")
    assert good.headers.get("etag") and "x-hs-degraded-response" not in good.headers

    async def catalog_down(dbf_ids):
        return {}

    monkeypatch.setattr(stats, "catalog_cards", catalog_down)
    degraded = client.get("/v1/battlegrounds/minions/104551/stats")
    assert degraded.status_code == 200
    assert degraded.headers["cache-control"] == "no-store" and "etag" not in degraded.headers
    assert "x-hs-degraded-response" not in degraded.headers
    assert degraded.json()["data"]["minion"]["images"] is None

    assert client.get("/v1/battlegrounds/minions/" + "9" * 30 + "/stats").status_code == 404
    snapshot_id = good.json()["data"]["slice"]["snapshot_id"]
    assert client.get(f"/v1/battlegrounds/minions/104551/stats?snapshot_id={snapshot_id}").status_code == 200
    assert client.get("/v1/battlegrounds/minions/104551/stats?snapshot_id=999").status_code == 404
