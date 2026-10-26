from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import blizzard_leaderboards as lb
from app import blizzard_leaderboards_store as store

DAY1 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
DAY2 = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
FAST = lb.Settings(
    regions=("EU",),
    modes=("standard", "battlegrounds"),
    concurrency=4,
    requests_per_second=10_000,
    backoff_seconds=0,
    throttle_pause_seconds=0,
    max_pages=2000,
)


def _rows(
    prefix: str, count: int, rated: bool, top: int = 9000
) -> list[tuple[str, float | None]]:
    return [
        (f"{prefix}{index}", float(top - index * 10) if rated else None)
        for index in range(1, count + 1)
    ]


class FakeBlizzard:
    """Serves leaderboardsData pages from in-memory boards and records requests."""

    def __init__(
        self, boards: dict[tuple[str, str, int], list[tuple[str, float | None]]]
    ) -> None:
        self.boards = boards
        self.requests: list[dict[str, str]] = []
        self.fail: Callable[[dict[str, str]], httpx.Response | None] = lambda _params: (
            None
        )

    def seasons(self) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        for region, mode, season in self.boards:
            entry = meta.setdefault(region, {}).setdefault(
                mode, {"name": mode, "seasons": []}
            )
            entry["seasons"].append({"season_id": season})
        for modes in meta.values():
            for entry in modes.values():
                entry["seasons"].sort(key=lambda item: -item["season_id"])
        return meta

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        failure = self.fail(params)
        if failure is not None:
            return failure
        region, mode = params["region"], params["leaderboardId"]
        seasons = [season for (r, m, season) in self.boards if (r, m) == (region, mode)]
        season = int(params.get("seasonId") or max(seasons))
        rows = self.boards.get((region, mode, season), [])
        page = int(params["page"])
        chunk = rows[(page - 1) * 25 : page * 25]
        pages = -(-len(rows) // 25)
        return httpx.Response(
            200,
            json={
                "seasonId": season,
                "leaderboard": {
                    "columns": ["rank", "accountid", "rating"],
                    "rows": [
                        {"rank": (page - 1) * 25 + index + 1, "accountid": name}
                        | ({"rating": rating} if rating is not None else {})
                        for index, (name, rating) in enumerate(chunk)
                    ],
                    "pagination": {
                        "totalPages": pages if chunk else 0,
                        "totalSize": len(rows) if chunk else 0,
                    },
                },
                "seasonMetaData": self.seasons(),
                "displayMetaData": {
                    "standard": {
                        "seasonsWithStartDate": {
                            "155": 1756684800000,
                            "156": 1759276800000,
                        }
                    }
                },
            },
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture(autouse=True)
def _data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))


def _run(
    fake: FakeBlizzard, scope: str, when: datetime = DAY1, settings: lb.Settings = FAST
) -> dict[str, Any]:
    return asyncio.run(
        lb.refresh_blizzard_leaderboards(
            scope=scope,
            settings=settings,
            transport=fake.transport(),
            clock=lambda: when,
        )
    )


def _fake() -> FakeBlizzard:
    return FakeBlizzard(
        {
            ("EU", "standard", 155): _rows("old", 30, rated=False),
            ("EU", "standard", 156): _rows("p", 60, rated=False),
            ("EU", "battlegrounds", 18): _rows("bgold", 10, rated=True),
            ("EU", "battlegrounds", 19): _rows("bg", 40, rated=True, top=12000),
        }
    )


def _board_requests(fake: FakeBlizzard, mode: str, season: int) -> int:
    return sum(
        1
        for params in fake.requests
        if params["leaderboardId"] == mode and params.get("seasonId") == str(season)
    )


def test_parse_page_skips_malformed_rows_and_rejects_missing_board() -> None:
    total_size, total_pages, rows = lb.parse_page(
        {
            "leaderboard": {
                "rows": [
                    {"rank": 1, "accountid": " Alpha ", "rating": 9000},
                    {"rank": 0, "accountid": "zero"},
                    {"rank": 2, "accountid": ""},
                    {"rank": "3", "accountid": "text"},
                    {"rank": 4, "accountid": "Bravo", "rating": True},
                ],
                "pagination": {"totalPages": 3, "totalSize": 61},
            }
        }
    )
    assert (total_size, total_pages) == (61, 3)
    assert rows == [(1, "Alpha", 9000.0), (4, "Bravo", None)]
    with pytest.raises(lb.LeaderboardError):
        lb.parse_page({"seasonId": 1})


def test_parse_metadata_reads_seasons_and_start_dates() -> None:
    payload = (
        _fake()
        .handler(
            httpx.Request(
                "GET",
                lb.API_URL,
                params={"region": "EU", "leaderboardId": "standard", "page": 1},
            )
        )
        .json()
    )
    seasons, starts = lb.parse_metadata(
        payload, regions=("EU",), modes=("standard", "battlegrounds")
    )
    assert seasons == {
        ("EU", "standard"): [155, 156],
        ("EU", "battlegrounds"): [18, 19],
    }
    assert starts[("standard", 156)] == "2025-10-01"
    with pytest.raises(lb.LeaderboardError):
        lb.parse_metadata({"seasonMetaData": {}})


def test_live_run_reads_every_page_of_latest_seasons_only() -> None:
    fake = _fake()
    result = _run(fake, "live")

    assert result["ok"] and result["state"] == "ok" and result["published"]
    assert (result["live_ok"], result["history_ok"], result["rows"]) == (2, 0, 100)
    assert _board_requests(fake, "standard", 156) == 3
    assert _board_requests(fake, "standard", 155) == 0
    table = lb.get_table(
        region="EU",
        mode="standard",
        season_id=None,
        day=None,
        query=None,
        limit=25,
        offset=50,
    )
    assert table is not None
    assert table["board"]["season_id"] == 156 and table["board"]["is_latest"]
    assert table["snapshot"]["total_size"] == 60 and not table["snapshot"]["truncated"]
    assert [row["rank"] for row in table["rows"]] == list(range(51, 61))
    assert table["rows"][0] == {
        "rank": 51,
        "name": "p51",
        "rating": None,
        "previous_rank": None,
    }


def test_same_day_run_replaces_snapshot_and_next_day_adds_history() -> None:
    fake = _fake()
    _run(fake, "live")
    rows = fake.boards[("EU", "standard", 156)]
    rows.insert(0, rows.pop(4))  # p5 climbs to rank 1
    _run(fake, "live")
    conn = store.connect()
    assert conn.execute("SELECT COUNT(*) FROM lb_snapshots").fetchone()[0] == 2
    conn.close()

    rows.insert(0, rows.pop(9))  # next day: p10 climbs to rank 1
    _run(fake, "live", when=DAY2)
    table = lb.get_table(
        region="EU",
        mode="standard",
        season_id=156,
        day=None,
        query=None,
        limit=3,
        offset=0,
    )
    assert table is not None
    assert table["days"] == ["2026-10-25", "2026-10-26"]
    assert [(row["name"], row["previous_rank"]) for row in table["rows"]] == [
        ("p10", 10),
        ("p5", 1),
        ("p1", 2),
    ]
    first_day = lb.get_table(
        region="EU",
        mode="standard",
        season_id=156,
        day="2026-10-25",
        query=None,
        limit=1,
        offset=0,
    )
    assert first_day is not None and first_day["rows"][0]["name"] == "p5"

    profile = lb.get_player(region="EU", name="P10", mode="standard")
    assert profile is not None and profile["player"]["name"] == "p10"
    (season,) = profile["seasons"]
    assert [(point["day"], point["rank"]) for point in season["series"]] == [
        ("2026-10-25", 10),
        ("2026-10-26", 1),
    ]
    assert (season["best_rank"], season["last_rank"], season["days"]) == (1, 1, 2)
    assert season["in_latest_snapshot"]


def test_history_reads_each_past_season_once_and_finalizes_rollover() -> None:
    fake = _fake()
    _run(fake, "live")
    result = _run(fake, "history")
    assert (result["history_ok"], result["live_ok"]) == (2, 0)
    requests = len(fake.requests)
    again = _run(fake, "history")
    assert again["planned_boards"] == 0 and again["state"] == "ok"
    assert len(fake.requests) == requests + 1  # metadata only

    fake.boards[("EU", "standard", 157)] = _rows("n", 5, rated=False)
    _run(fake, "live", when=DAY2)
    catalog = lb.get_catalog()
    standard = next(mode for mode in catalog["modes"] if mode["mode"] == "standard")
    assert standard["regions"]["EU"]["latest_season"] == 157
    history = _run(fake, "history", when=DAY2)
    assert history["history_ok"] == 1
    old = lb.get_table(
        region="EU",
        mode="standard",
        season_id=156,
        day=None,
        query=None,
        limit=1,
        offset=0,
    )
    assert old is not None and old["board"]["final"] and old["snapshot"]["final"]
    assert old["days"] == ["2026-10-25", "2026-10-26"]


def test_failed_page_keeps_previous_snapshot_and_reports_partial() -> None:
    fake = _fake()
    _run(fake, "live")
    fake.fail = lambda params: (
        httpx.Response(503)
        if params.get("seasonId") == "156" and params["page"] == "2"
        else None
    )
    result = _run(fake, "live", when=DAY2)

    assert result["state"] == "partial" and result["published"]
    assert (result["live_ok"], result["failed"]) == (1, 1)
    assert result["errors"] and "EU/standard/156" in result["errors"][0]
    table = lb.get_table(
        region="EU",
        mode="standard",
        season_id=156,
        day=None,
        query=None,
        limit=1,
        offset=0,
    )
    assert table is not None and table["days"] == ["2026-10-25"]
    conn = store.connect()
    board = conn.execute(
        "SELECT failures, last_error FROM lb_boards WHERE mode = 'standard' AND season_id = 156"
    ).fetchone()
    conn.close()
    assert board["failures"] == 1 and "HTTP 503" in board["last_error"]


def test_throttling_pauses_and_retries_the_same_page() -> None:
    fake = _fake()
    seen: set[str] = set()

    def throttle_once(params: dict[str, str]) -> httpx.Response | None:
        key = f"{params.get('seasonId')}:{params['page']}"
        if params.get("seasonId") == "19" and key not in seen:
            seen.add(key)
            # Blizzard answers a burst with a bare 403; 429 may carry Retry-After.
            status = 403 if params["page"] == "1" else 429
            return httpx.Response(status, headers={"Retry-After": "0"})
        return None

    fake.fail = throttle_once
    result = _run(fake, "live")
    assert result["state"] == "ok" and result["live_ok"] == 2
    assert (result["throttled"], result["retries"], result["failed"]) == (2, 0, 0)


def test_persistent_throttling_fails_the_board_not_the_run() -> None:
    fake = _fake()
    fake.fail = lambda params: (
        httpx.Response(403) if params.get("seasonId") == "19" else None
    )
    result = _run(fake, "live")
    assert result["state"] == "partial" and result["live_ok"] == 1
    assert result["errors"] == [
        "EU/battlegrounds/19: EU/battlegrounds/19 page 1: HTTP 403"
    ]
    assert result["throttled"] == lb.MAX_THROTTLES_PER_PAGE


def test_pacer_pause_holds_requests_once_per_episode() -> None:
    pacer = lb.Pacer(requests_per_second=2)
    assert pacer.pause(30) is True
    assert pacer.pause(5) is False
    pacer.slow_down()
    pacer.slow_down()
    pacer.slow_down()
    assert pacer.interval == lb.MAX_REQUEST_INTERVAL


def test_metadata_failure_publishes_nothing() -> None:
    fake = _fake()
    fake.fail = lambda _params: httpx.Response(500)
    result = _run(fake, "live")
    assert not result["ok"] and not result["published"]
    assert (
        result["stop_reason"] == "metadata_failed" and result["state"] == "fetch_error"
    )


def test_large_board_keeps_top_pages_and_is_marked_truncated() -> None:
    fake = _fake()
    settings = lb.Settings(**{**FAST.__dict__, "max_pages": 2})
    _run(fake, "live", settings=settings)
    table = lb.get_table(
        region="EU",
        mode="standard",
        season_id=156,
        day=None,
        query=None,
        limit=100,
        offset=0,
    )
    assert table is not None
    assert table["snapshot"]["truncated"] and table["snapshot"]["stored_rows"] == 50
    assert table["snapshot"]["total_size"] == 60


def test_stats_thresholds_histogram_search_and_shared_names() -> None:
    rows = _rows("bg", 120, rated=True, top=12000)
    rows[5] = ("bg1", 11940.0)  # a second account named bg1
    fake = FakeBlizzard({("EU", "battlegrounds", 19): rows})
    _run(
        fake,
        "live",
        settings=lb.Settings(**{**FAST.__dict__, "modes": ("battlegrounds",)}),
    )

    stats = lb.get_stats(region="EU", mode="battlegrounds", season_id=None, bucket=None)
    assert stats is not None
    assert stats["top_rating"] == 11990.0
    assert stats["thresholds"] == [
        {"rank": 100, "reached": True, "rating": 11000.0},
        {"rank": 1000, "reached": False, "rating": None},
        {"rank": 5000, "reached": False, "rating": None},
    ]
    assert stats["histogram_bucket"] == 500
    assert sum(bucket["players"] for bucket in stats["histogram"]) == 120
    assert stats["rating"]["rated_players"] == 120
    assert stats["series"][0]["total_size"] == 120

    found = lb.get_table(
        region="EU",
        mode="battlegrounds",
        season_id=None,
        day=None,
        query="G11",
        limit=5,
        offset=0,
    )
    assert found is not None
    assert found["total"] == 11  # bg11 and bg110..bg119
    assert found["rows"][0]["name"] == "bg11"

    players = lb.search_players(query="bg1", region="EU", limit=3)
    assert players[0]["name"] == "bg1"
    assert players[0]["current"] == [
        {"mode": "battlegrounds", "season_id": 19, "rank": 1, "rating": 11990.0}
    ]
    profile = lb.get_player(region="EU", name="bg1", mode=None)
    assert profile is not None and profile["shared_name"]
    assert profile["seasons"][0]["series"][0]["rank"] == 1


def test_rest_and_graphql_read_the_store() -> None:
    from app.main import app

    client = TestClient(app)
    assert client.get("/v1/leaderboards?mode=standard&region=EU").status_code == 404
    assert client.get("/v1/leaderboards?mode=unknown").status_code == 422
    paths = app.openapi()["paths"]
    for path in ("", "/stats", "/players", "/player", "/player/history", "/seasons"):
        content = paths[f"/v1/leaderboards{path}"]["get"]["responses"]["200"]["content"]
        assert content["application/json"]["example"]["data"], path

    _run(_fake(), "live")
    response = client.get(
        "/v1/leaderboards", params={"mode": "battlegrounds", "limit": 2}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source_id"] == "blizzard_leaderboards"
    assert (body["meta"]["count"], body["meta"]["limit"]) == (40, 2)
    assert [row["name"] for row in body["data"]["rows"]] == ["bg1", "bg2"]
    stats = client.get("/v1/leaderboards/stats", params={"mode": "battlegrounds"})
    assert stats.json()["data"]["histogram"]
    assert (
        client.get("/v1/leaderboards/player", params={"name": "nobody"}).status_code
        == 404
    )
    assert (
        client.get("/v1/leaderboards/players", params={"q": "bg"}).json()["meta"][
            "count"
        ]
        == 20
    )
    coverage = client.get("/v1/leaderboards/coverage")
    assert coverage.headers["cache-control"] == "no-store"
    assert coverage.json()["data"]["runs"][0]["scope"] == "live"

    graphql = client.post(
        "/v1/graphql",
        json={
            "query": (
                'query { leaderboard(mode: "battlegrounds", limit: 1) '
                'leaderboardPlayer(name: "bg2", region: "EU") '
                'leaderboardStats(mode: "standard") }'
            )
        },
    )
    data = graphql.json()["data"]
    assert data["leaderboard"]["rows"] == [
        {"rank": 1, "name": "bg1", "rating": 11990.0, "previous_rank": None}
    ]
    assert data["leaderboardPlayer"]["seasons"][0]["best_rank"] == 2
    assert data["leaderboardStats"]["histogram"] is None
    invalid = client.post(
        "/v1/graphql", json={"query": 'query { leaderboard(region: "XX") }'}
    )
    assert invalid.json()["errors"][0]["message"].startswith("region must be one of")


def _entry_rows(conn: Any) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM lb_entries").fetchone()[0])


def test_unchanged_day_shares_rows_and_replacing_it_keeps_history() -> None:
    fake = _fake()
    _run(fake, "live")
    conn = store.connect()
    assert _entry_rows(conn) == 100
    conn.close()

    _run(fake, "live", when=DAY2)  # nothing moved: no new rows
    conn = store.connect()
    assert _entry_rows(conn) == 100
    shared = conn.execute(
        "SELECT COUNT(*) FROM lb_snapshots WHERE entries_id != id"
    ).fetchone()[0]
    assert shared == 2
    conn.close()
    table = lb.get_table(
        region="EU",
        mode="standard",
        season_id=None,
        day="2026-10-26",
        query=None,
        limit=2,
        offset=0,
    )
    assert table is not None and table["days"] == ["2026-10-25", "2026-10-26"]
    assert [(row["name"], row["previous_rank"]) for row in table["rows"]] == [
        ("p1", 1),
        ("p2", 2),
    ]

    # A later same-day read of day 1 that differs takes its own rows; day 2
    # keeps the old day-1 table it shared.
    rows = fake.boards[("EU", "standard", 156)]
    rows.insert(0, rows.pop(4))
    _run(fake, "live")
    conn = store.connect()
    assert _entry_rows(conn) == 160
    conn.close()
    day1 = lb.get_table(
        region="EU",
        mode="standard",
        season_id=156,
        day="2026-10-25",
        query=None,
        limit=1,
        offset=0,
    )
    day2 = lb.get_table(
        region="EU",
        mode="standard",
        season_id=156,
        day="2026-10-26",
        query=None,
        limit=1,
        offset=0,
    )
    assert day1 is not None and day1["rows"][0]["name"] == "p5"
    assert day2 is not None and day2["rows"][0]["name"] == "p1"

    profile = lb.get_player(region="EU", name="p5", mode="standard")
    assert profile is not None
    assert [(p["day"], p["rank"]) for p in profile["seasons"][0]["series"]] == [
        ("2026-10-25", 1),
        ("2026-10-26", 5),
    ]
    stats = lb.get_stats(region="EU", mode="battlegrounds", season_id=None, bucket=None)
    assert stats is not None and stats["rating"]["rated_players"] == 40
    hits = lb.search_players(query="bg1", region="EU", limit=1)
    assert hits[0]["current"][0]["rank"] == 1


def test_migration_adds_shared_entries_to_a_version_0_database() -> None:
    fake = _fake()
    _run(fake, "live")
    conn = store.connect()
    hashes = dict(conn.execute("SELECT id, rows_hash FROM lb_snapshots").fetchall())
    conn.execute("DROP INDEX idx_lb_snapshots_entries")
    conn.execute("ALTER TABLE lb_snapshots DROP COLUMN entries_id")
    conn.execute("ALTER TABLE lb_snapshots DROP COLUMN rows_hash")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    conn = store.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    migrated = conn.execute(
        "SELECT id, entries_id, rows_hash FROM lb_snapshots"
    ).fetchall()
    assert all(row["entries_id"] == row["id"] for row in migrated)
    assert {row["id"]: row["rows_hash"] for row in migrated} == hashes
    conn.close()
    _run(fake, "live", when=DAY2)
    conn = store.connect()
    assert _entry_rows(conn) == 100
    conn.close()


def test_rollover_keeps_final_and_player_history_spans_seasons() -> None:
    fake = _fake()
    _run(fake, "live")
    bg = fake.boards[("EU", "battlegrounds", 19)]
    bg[0] = ("bg1", 12500.0)  # day 2: bg1 peaks
    _run(fake, "live", when=DAY2)
    bg[0] = ("bg1", 12300.0)
    fake.boards[("EU", "battlegrounds", 20)] = [("bg1", 8000.0), ("bg2", 7990.0)]
    day3 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    _run(fake, "live", when=day3)
    _run(fake, "history", when=day3)

    profile = lb.get_player(region="EU", name="bg1", mode="battlegrounds")
    assert profile is not None
    by_season = {season["season_id"]: season for season in profile["seasons"]}
    ended = by_season[19]
    assert ended["final"] and not ended["is_latest"]
    assert (ended["peak_rating"], ended["peak_rating_day"]) == (12500.0, "2026-10-26")
    assert (ended["final_rank"], ended["final_rating"]) == (1, 12300.0)
    assert ended["first_day"] == "2026-10-25"
    # Battlegrounds seasons are undated: the final read on day 3 belongs to the
    # last day the season was live and replaces that day's live point.
    assert ended["season_end"] == "2026-10-26"
    assert [(p["day"], p["final"], p["read_day"]) for p in ended["series"]] == [
        ("2026-10-25", False, "2026-10-25"),
        ("2026-10-26", True, "2026-10-27"),
    ]
    assert (ended["last_rank"], ended["last_day"]) == (1, "2026-10-26")
    assert by_season[20]["final_rank"] is None and by_season[20]["is_latest"]

    history = lb.get_player_history(region="EU", name="bg1", mode="battlegrounds")
    assert history is not None and history["rated"]
    assert [s["season_id"] for s in history["seasons"]] == [19, 20]
    assert "series" not in history["seasons"][0]
    assert [(p["day"], p["season_id"], p["final"]) for p in history["points"]] == [
        ("2026-10-25", 19, False),
        ("2026-10-26", 19, True),
        ("2026-10-27", 20, False),
    ]
    window = lb.get_player_history(
        region="EU",
        name="bg1",
        mode="battlegrounds",
        day_from="2026-10-26",
        day_to="2026-10-26",
    )
    assert window is not None
    assert [(p["day"], p["rating"], p["final"]) for p in window["points"]] == [
        ("2026-10-26", 12300.0, True)
    ]
    assert lb.get_player_history(region="EU", name="nobody", mode="standard") is None


def test_live_run_puts_boards_missing_today_first() -> None:
    fake = _fake()
    _run(
        fake, "live", settings=lb.Settings(**{**FAST.__dict__, "modes": ("standard",)})
    )
    conn = store.connect()
    store.sync_boards(conn, {("EU", "battlegrounds"): [19]}, {}, "now")
    boards = store.live_boards(
        conn, ("EU",), ("standard", "battlegrounds"), today="2026-10-25"
    )
    assert [board["mode"] for board in boards] == ["battlegrounds", "standard"]
    boards = store.live_boards(conn, ("EU",), ("standard", "battlegrounds"))
    assert [board["mode"] for board in boards] == ["standard", "battlegrounds"]
    conn.close()


def test_live_run_waits_for_a_history_run_holding_the_lock() -> None:
    from app.resource_locks import ResourceLockSet

    fake = _fake()
    held = ResourceLockSet([lb.SOURCE_ID]).acquire()

    async def scenario(wait: float) -> dict[str, Any]:
        async def release_soon() -> None:
            await asyncio.sleep(0.05)
            held.release()

        releaser = asyncio.create_task(release_soon())
        result = await lb.refresh_blizzard_leaderboards(
            scope="live",
            settings=lb.Settings(**{**FAST.__dict__, "lock_wait_seconds": wait}),
            transport=fake.transport(),
            clock=lambda: DAY1,
        )
        await releaser
        return result

    original = lb.LOCK_POLL_SECONDS
    lb.LOCK_POLL_SECONDS = 0.01
    try:
        assert asyncio.run(scenario(5.0))["live_ok"] == 2
        held.acquire()
        history = _run(fake, "history")
        assert history["state"] == "locked" and not history["published"]
        held.release()
    finally:
        lb.LOCK_POLL_SECONDS = original
        held.release()


def test_coverage_reports_missing_live_days() -> None:
    from datetime import date

    fake = _fake()
    _run(fake, "live")
    _run(fake, "live", when=datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    conn = store.connect()
    days = store.live_days(conn, today=date(2026, 10, 29))
    standard = next(item for item in days if item["mode"] == "standard")
    assert standard["missing_days"] == ["2026-10-26", "2026-10-27"]
    assert (standard["first_day"], standard["last_day"], standard["days"]) == (
        "2026-10-25",
        "2026-10-28",
        2,
    )
    later = store.live_days(conn, today=date(2026, 10, 31))
    assert next(item for item in later if item["mode"] == "standard")[
        "missing_days"
    ] == ["2026-10-26", "2026-10-27", "2026-10-29", "2026-10-30"]

    # A snapshot an older writer stored after the migration is healed on the next run.
    conn.execute("UPDATE lb_snapshots SET entries_id = NULL WHERE day = '2026-10-25'")
    conn.commit()
    conn.close()
    stale = lb.get_player(region="EU", name="p1", mode="standard")
    assert stale is not None
    assert [p["day"] for p in stale["seasons"][0]["series"]] == ["2026-10-28"]
    _run(fake, "history")
    conn = store.connect()
    assert not conn.execute(
        "SELECT COUNT(*) FROM lb_snapshots WHERE entries_id IS NULL"
    ).fetchone()[0]
    conn.close()
    profile = lb.get_player(region="EU", name="p1", mode="standard")
    assert profile is not None
    assert [p["day"] for p in profile["seasons"][0]["series"]] == [
        "2026-10-25",
        "2026-10-28",
    ]


def test_rest_and_graphql_player_history() -> None:
    from app.main import app

    client = TestClient(app)
    _run(_fake(), "live")
    response = client.get(
        "/v1/leaderboards/player/history",
        params={"name": "bg2", "mode": "battlegrounds", "from": "2026-10-25"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["count"] == 1
    assert body["data"]["points"] == [
        {
            "season_id": 19,
            "day": "2026-10-25",
            "rank": 2,
            "rating": 11980.0,
            "total_size": 40,
            "final": False,
            "read_day": "2026-10-25",
        }
    ]
    assert body["data"]["seasons"][0]["peak_rating"] == 11980.0
    assert (
        client.get(
            "/v1/leaderboards/player/history", params={"name": "bg2"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/v1/leaderboards/player/history",
            params={"name": "bg2", "mode": "standard", "from": "25.10.2026"},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/v1/leaderboards/player/history",
            params={"name": "bg2", "mode": "standard", "to": "2026-13-45"},
        ).status_code
        == 422
    )
    graphql = client.post(
        "/v1/graphql",
        json={
            "query": (
                'query { leaderboardPlayerHistory(name: "bg2", mode: "battlegrounds", '
                'to: "2026-10-24") }'
            )
        },
    )
    assert graphql.json()["data"]["leaderboardPlayerHistory"]["points"] == []


def _dated_fake() -> FakeBlizzard:
    # Standard seasons 154-156 of one player; the fake dates 155 (2025-09-01)
    # and 156 (2025-10-01), so 154 ends 2025-08-31 and 155 ends 2025-09-30.
    return FakeBlizzard(
        {
            ("EU", "standard", 154): [("Norwis", None), ("a", None)],
            ("EU", "standard", 155): [("b", None), ("Norwis", None)],
            ("EU", "standard", 156): [("c", None), ("d", None), ("Norwis", None)],
        }
    )


def test_backfilled_finals_are_dated_by_season_end_and_points_are_chronological() -> (
    None
):
    fake = _dated_fake()
    settings = lb.Settings(**{**FAST.__dict__, "modes": ("standard",)})
    _run(fake, "live", settings=settings)
    _run(fake, "history", settings=settings)  # backfill on 2026-10-25
    _run(fake, "live", when=DAY2, settings=settings)

    history = lb.get_player_history(region="EU", name="Norwis", mode="standard")
    assert history is not None
    assert [
        (p["season_id"], p["day"], p["rank"], p["final"], p["read_day"])
        for p in history["points"]
    ] == [
        (154, "2025-08-31", 1, True, "2026-10-25"),
        (155, "2025-09-30", 2, True, "2026-10-25"),
        (156, "2026-10-25", 3, False, "2026-10-25"),
        (156, "2026-10-26", 3, False, "2026-10-26"),
    ]
    days = [p["day"] for p in history["points"]]
    assert days == sorted(days)
    ended = {s["season_id"]: s for s in history["seasons"]}
    assert (ended[155]["season_end"], ended[155]["last_day"]) == (
        "2025-09-30",
        "2025-09-30",
    )
    assert ended[156]["season_end"] is None
    # The table keeps the read day as its key and says what day it shows.
    table = lb.get_table(
        region="EU",
        mode="standard",
        season_id=155,
        day=None,
        query=None,
        limit=1,
        offset=0,
    )
    assert table is not None and table["days"] == ["2026-10-25"]
    assert table["snapshot"]["as_of_day"] == "2025-09-30"
    catalog = lb.get_catalog()
    seasons = catalog["modes"][0]["regions"]["EU"]["seasons"]
    assert {s["season_id"]: s["season_end"] for s in seasons} == {
        156: None,
        155: "2025-09-30",
        154: "2025-08-31",
    }


def test_final_read_of_a_season_seen_live_is_dated_by_season_end() -> None:
    fake = _dated_fake()
    del fake.boards[("EU", "standard", 156)]
    settings = lb.Settings(**{**FAST.__dict__, "modes": ("standard",)})
    _run(fake, "live", settings=settings)  # 155 live on 2026-10-25
    fake.boards[("EU", "standard", 156)] = [("Norwis", None)]
    _run(fake, "live", when=DAY2, settings=settings)
    _run(fake, "history", when=DAY2, settings=settings)
    profile = lb.get_player(region="EU", name="Norwis", mode="standard")
    assert profile is not None
    season = next(s for s in profile["seasons"] if s["season_id"] == 155)
    # The season was seen live after its dated end, so the final belongs to
    # that last live day, never before a live point.
    assert [(p["day"], p["final"]) for p in season["series"]] == [("2026-10-25", True)]
    assert season["final_rank"] == 2


def test_migration_v2_redates_stored_finals_and_keeps_read_days() -> None:
    fake = _dated_fake()
    settings = lb.Settings(**{**FAST.__dict__, "modes": ("standard",)})
    _run(fake, "live", settings=settings)
    _run(fake, "history", settings=settings)
    conn = store.connect()
    before = dict(conn.execute("SELECT id, day FROM lb_snapshots").fetchall())
    conn.execute("UPDATE lb_snapshots SET as_of_day = NULL")
    conn.execute("UPDATE lb_boards SET season_end = NULL")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    conn = store.connect()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert dict(conn.execute("SELECT id, day FROM lb_snapshots").fetchall()) == before
    conn.close()
    fixed = lb.get_player_history(region="EU", name="Norwis", mode="standard")
    assert fixed is not None
    assert [p["day"] for p in fixed["points"]] == [
        "2025-08-31",
        "2025-09-30",
        "2026-10-25",
    ]
