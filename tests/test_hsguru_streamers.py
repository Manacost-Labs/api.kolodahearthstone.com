from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from app import hsguru_streamers as streamers
from app import hsguru_streamers_store as store

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/hsguru_streamers"
NOW = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- parsing (dead renders saved from hsguru.com and twitch.tv on 2026-10-25) ---


def test_streamer_decks_page_parses_every_column() -> None:
    rows = streamers.parse_streamer_decks(fixture("streamer_decks_norender.html"))

    assert len(rows) == 4
    first = rows[0]
    assert first == {
        "streamer_id": 47076737,
        "streamer_name": "kubu_gaming",
        "login": "kubu_gaming",
        "twitch_url": "https://www.twitch.tv/kubu_gaming",
        "deck_id": 42052066,
        "deck_code": first["deck_code"],
        "archetype": "Imbue Paladin",
        "player_class": "PALADIN",
        "format": "standard",
        "peak_rank": 468,
        "latest_rank": 468,
        "worst_rank": 468,
        "wins": 0,
        "losses": 1,
        "last_played_at": "2026-09-12T17:37:31+00:00",
    }
    assert first["deck_code"].startswith("AAECAZ8F")
    # A deck played outside Legend has no ranks; Wild rows come in the same list.
    assert rows[1]["peak_rank"] is None and rows[1]["player_class"] == "DEMONHUNTER"
    assert rows[3]["format"] == "wild" and rows[3]["archetype"] == "XL HL Exodia Mage"


def test_streamer_decks_page_validation() -> None:
    assert streamers.parse_streamer_decks(fixture("streamer_decks_empty.html")) == []
    assert streamers.page_is_valid("decks", fixture("streamer_decks_empty.html"))
    assert not streamers.page_is_valid(
        "decks", "<html><body>Just a moment</body></html>"
    )
    with pytest.raises(streamers.PageRejected):
        streamers.parse_streamer_decks("<html><body><table></table></body></html>")


def test_streaming_now_cards() -> None:
    html = fixture("streaming_now_norender.html")
    streams = streamers.parse_streaming_now(html, fetched_at=NOW)

    assert [stream["login"] for stream in streams] == [
        "denimblue7",
        "patrix01_",
        "ferz1lla",
    ]
    patrix = streams[1]
    assert patrix["display_name"] == "patrix01_" and patrix["viewers"] == 36
    assert patrix["stream_url"] == "https://www.twitch.tv/patrix01_"
    # 00:56:38 on air when the page was read.
    assert patrix["started_at"] == (NOW - timedelta(minutes=56, seconds=38)).isoformat()
    assert patrix["mode"] is None  # HSGuru shows "Unknown"
    wild = re.sub(
        r"(--color-)unknown(\);\">)\s*Unknown", r"\1wild\2Wild", html, count=1
    )
    assert streamers.parse_streaming_now(wild, fetched_at=NOW)[0]["mode"] == "wild"
    assert streamers.page_is_valid("live", html)
    assert not streamers.page_is_valid("live", "<html><body></body></html>")


def test_twitch_channel_profile_and_vods() -> None:
    profile = streamers.parse_twitch_channel(fixture("twitch_channel.html"))

    assert profile is not None
    assert profile["login"] == "reqvam"
    assert profile["youtube_url"] == "https://www.youtube.com/@reqvam"
    assert profile["x_url"] == "https://x.com/reqvamhs"
    assert profile["links"] == [
        {"kind": "discord", "url": "https://discord.com/invite/UedvbNAaJ5"}
    ]
    assert profile["avatar_url"].endswith("profile_image-300x300.png")
    assert profile["followers"] == 25174
    vod = profile["vods"][0]
    assert vod["url"] == "https://www.twitch.tv/videos/2909484152"
    assert vod["started_at"] == "2026-10-24T14:16:29+00:00"
    assert vod["duration_seconds"] == 18900
    assert (
        streamers.parse_twitch_channel(fixture("twitch_channel_missing.html")) is None
    )


def test_login_only_from_latin_names() -> None:
    assert streamers.login_from_name("Nik1taStar") == "nik1tastar"
    assert streamers.login_from_name("뀨효미") is None


def test_settings_cap_scrape_do_concurrency(monkeypatch) -> None:
    monkeypatch.setenv("HS_HSGURU_STREAMERS_CONCURRENCY", "40")
    monkeypatch.setenv("HS_HSGURU_STREAMERS_LANGUAGES", "ru, en,bogus")
    settings = streamers.Settings.from_env()
    assert settings.concurrency == streamers.MAX_CONCURRENCY == 5
    assert settings.languages == ("ru", "en")


# --- synthetic pages with the HSGuru markup ------------------------------------


def deck_row(
    *,
    streamer_id: int,
    name: str,
    deck_id: int,
    played: datetime,
    player_class: str = "priest",
    archetype: str = "C'Thun Priest",
    format_name: str = "Standard",
    peak: str = "100",
    wins: int = 3,
    losses: int = 1,
) -> str:
    code = f"AAECAa0G{deck_id}AAA="
    stamp = played.strftime("%Y-%m-%dT%H:%M:%S")
    return (
        "<tr><td><div><div class='decklist-info "
        f"{player_class}'><button data-clipboard-text=\"### {archetype}\n{code}\n"
        f'### You can view this deck at https://www.hsguru.com/deck/{deck_id}"></button>'
        f"<h2 class='deck-title'><a href='https://www.hsguru.com/deck/{deck_id}'>{archetype}"
        f"</a></h2></div></div></td>"
        f"<td><a href='/streamer-decks?twitch_id={streamer_id}'>{name}</a></td>"
        f"<td><span class='tag'>{format_name}</span></td>"
        f"<td><span class='tag'>{peak}</span></td><td>{peak}</td><td>{peak}</td>"
        f"<td><span>{wins} - {losses}</span></td>"
        f"<td><a href='/streamer-decks?deck_id={deck_id}'></a>"
        f"<a href='https://www.twitch.tv/{name}'>{name}</a></td>"
        f"<td><time datetime='{stamp}'>{stamp} UTC</time></td></tr>"
    )


def decks_page(rows: list[str]) -> str:
    headers = "".join(
        f"<th>{name}</th>"
        for name in (
            "Deck",
            "Streamer",
            "Format",
            "Peak",
            "Latest",
            "Worst",
            "Win - Loss",
            "Links",
            "Last Played",
        )
    )
    return (
        f"<html><body><table><thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></body></html>"
    )


def live_page(cards: list[tuple[str, str, int, str, str]]) -> str:
    body = "".join(
        f"<div class='cestor card live-streamer twitch'><a href='https://www.twitch.tv/{login}'>"
        f"<img src='https://static-cdn.jtvnw.net/previews-ttv/live_user_{login}.jpg'/></a>"
        f"<a href='https://www.twitch.tv/{login}'><div class='title is-6'>Ladder</div></a>"
        f"<div class='tags'><strong class='tag is-twitch'>{name}</strong>"
        f"<div class='tag is-info'><span class='icon'></span><p>{viewers}</p></div>"
        f"<span class='tag' style='background-color: var(--color-{mode.lower()});'>{mode}</span>"
        f"<div class='tag is-info'>{duration}</div></div></div>"
        for login, name, viewers, mode, duration in cards
    )
    return f"<html><body><h1>Streaming Now</h1>{body}</body></html>"


class FakeHsguru:
    """Serves /streamer-decks from a list ordered newest first, 50 per page."""

    def __init__(self, rows: list[str], live: dict[str | None, str]) -> None:
        self.rows = rows
        self.live = live
        self.urls: list[str] = []

    async def __call__(self, url: str, kind: str) -> tuple[str, int]:
        self.urls.append(url)
        if kind == "live":
            language = re.search(r"filter_language=(\w+)", url)
            return self.live[language.group(1) if language else None], 1
        offset = (
            int(re.search(r"offset=(\d+)", url).group(1)) if "offset=" in url else 0
        )
        return decks_page(self.rows[offset : offset + streamers.PAGE_SIZE]), 1


def twitch_page(login: str, *, vod_start: datetime, seconds: int) -> str:
    return (
        f'<html><head><meta property="og:profile:username" content="{login}"/>'
        '<script type="application/ld+json">{"@graph":[{"@type":"ProfilePage",'
        '"mainEntity":{"@type":"Person","name":"' + login + '",'
        '"image":"https://static-cdn.jtvnw.net/a.png","url":"https://www.twitch.tv/'
        + login
        + '","sameAs":["https://www.youtube.com/@'
        + login
        + '"],'
        '"interactionStatistic":{"userInteractionCount":1200}}},'
        '{"@type":"ItemList","itemListElement":[{"@type":"VideoObject","name":"VOD",'
        '"url":"https://www.twitch.tv/videos/77","uploadDate":"'
        + vod_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        + f'","duration":"PT{seconds}S"'
        + "}]}]}</script></head></html>"
    )


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    return tmp_path


def _settings(**changes: Any) -> streamers.Settings:
    return streamers.Settings(**{"backfill_days": 120, "languages": ("ru",), **changes})


def _run(
    fake: FakeHsguru, *, now: datetime, twitch: Any = None, **settings: Any
) -> dict:
    async def no_twitch(login: str) -> tuple[int, str]:
        return 404, ""

    return asyncio.run(
        streamers.refresh_hsguru_streamers(
            settings=_settings(**settings),
            fetch_hsguru=fake,
            fetch_twitch=twitch or no_twitch,
            now=now,
        )
    )


def _history(count: int, *, newest: datetime, step: timedelta) -> list[str]:
    return [
        deck_row(
            streamer_id=1000 + index % 7,
            name=f"Streamer{index % 7}",
            deck_id=5000 + index,
            played=newest - index * step,
        )
        for index in range(count)
    ]


def test_first_run_walks_back_to_the_backfill_window(isolated) -> None:
    # 160 rows, one every day: the first 120 days are three full pages.
    rows = _history(160, newest=NOW - timedelta(hours=1), step=timedelta(days=1))
    fake = FakeHsguru(rows, {None: live_page([]), "ru": live_page([])})

    result = _run(fake, now=NOW, concurrency=2)

    assert result["ok"] and result["published"]
    assert result["decks_complete"] == 1
    deck_urls = [url for url in fake.urls if "streamer-decks" in url]
    # A batch of one page, then two; the walk stops at the page that crosses
    # the cutoff instead of reading all of HSGuru's history.
    assert deck_urls == [
        streamers.streamer_decks_url(offset) for offset in (0, 50, 100)
    ]
    assert result["deck_pages"] == 3
    conn = sqlite3.connect(isolated / "hs_parses.db")
    stored = conn.execute(f"SELECT COUNT(*) FROM {store.DECKS}").fetchone()[0]
    assert stored == 150
    assert conn.execute(f"SELECT COUNT(*) FROM {store.STREAMERS}").fetchone()[0] == 7


def test_next_run_reads_only_new_rows_and_updates_replayed_decks(isolated) -> None:
    rows = _history(60, newest=NOW - timedelta(hours=1), step=timedelta(hours=6))
    fake = FakeHsguru(rows, {None: live_page([]), "ru": live_page([])})
    _run(fake, now=NOW)

    later = NOW + timedelta(minutes=5)
    replayed = deck_row(
        streamer_id=1000, name="Streamer0", deck_id=5007, played=later, wins=9, losses=2
    )
    fresh = deck_row(streamer_id=1009, name="Newcomer", deck_id=9000, played=later)
    fake.rows = [fresh, replayed, *rows]
    fake.urls.clear()
    second = _run(fake, now=later)

    assert second["decks_new"] == 1 and second["decks_updated"] == 1
    # Cutoff = first run start - 2 h overlap: one page is enough.
    assert [url for url in fake.urls if "streamer-decks" in url] == [
        streamers.streamer_decks_url(0)
    ]
    conn = sqlite3.connect(isolated / "hs_parses.db")
    wins = conn.execute(
        f"SELECT wins, last_played_at FROM {store.DECKS} WHERE deck_id = 5007"
    ).fetchone()
    assert wins == (9, later.isoformat())


def test_an_older_read_never_overwrites_a_newer_row(isolated) -> None:
    from app.db import get_db_connection, init_db

    init_db()
    conn = get_db_connection()
    new = streamers.parse_streamer_decks(
        decks_page([deck_row(streamer_id=1, name="A", deck_id=2, played=NOW, wins=5)])
    )
    old = streamers.parse_streamer_decks(
        decks_page(
            [
                deck_row(
                    streamer_id=1, name="A", deck_id=2, played=NOW - timedelta(hours=1)
                )
            ]
        )
    )
    store.upsert_decks(conn, new, seen_at=NOW.isoformat())
    assert store.upsert_decks(conn, old, seen_at=NOW.isoformat()) == {
        "new": 0,
        "updated": 0,
    }
    assert conn.execute(f"SELECT wins FROM {store.DECKS}").fetchone()[0] == 5


def test_failed_deck_page_keeps_the_walk_incomplete(isolated) -> None:
    rows = _history(120, newest=NOW - timedelta(hours=1), step=timedelta(days=1))
    fake = FakeHsguru(rows, {None: live_page([]), "ru": live_page([])})

    async def flaky(url: str, kind: str) -> tuple[str, int]:
        if "offset=100" in url:
            raise RuntimeError("scrape_do: timeout")
        return await fake(url, kind)

    result = asyncio.run(
        streamers.refresh_hsguru_streamers(
            settings=_settings(), fetch_hsguru=flaky, now=NOW, profiles=False
        )
    )

    assert result["decks_complete"] == 0
    assert result["stop_reason"] == "deck_page_failed:100"
    # Live and the pages before the failure are still stored and published.
    assert result["published"] and result["decks"] == 100
    from app.db import get_db_connection

    assert store.deck_watermark(get_db_connection()) is None


def _live_world(isolated) -> FakeHsguru:
    rows = [
        deck_row(
            streamer_id=96748115,
            name="patrix01_",
            deck_id=1,
            played=NOW - timedelta(minutes=10),
        ),
        deck_row(
            streamer_id=96748115,
            name="patrix01_",
            deck_id=2,
            played=NOW - timedelta(days=1, hours=1),
            player_class="warlock",
            archetype="Zoo Warlock",
            format_name="Wild",
        ),
        deck_row(
            streamer_id=2,
            name="뀨효미",
            deck_id=3,
            played=NOW - timedelta(hours=5),
            player_class="mage",
            archetype="Spell Mage",
        ),
    ]
    live = live_page(
        [
            ("patrix01_", "patrix01_", 36, "Unknown", "02:00:00"),
            ("kyuhyomi", "뀨효미", 500, "Battlegrounds", "01:00:00"),
            ("bgonly", "BGonly", 12, "Unknown", "00:30:00"),
        ]
    )
    ru = live_page([("patrix01_", "patrix01_", 36, "Unknown", "02:00:00")])
    fake = FakeHsguru(rows, {None: live, "ru": ru})

    async def twitch(login: str) -> tuple[int, str]:
        if login != "patrix01_":
            return 404, ""
        return 200, twitch_page(
            login, vod_start=NOW - timedelta(days=1, hours=3), seconds=4 * 3600
        )

    result = _run(fake, now=NOW, twitch=twitch)
    assert result["ok"], result
    return fake


def test_live_streams_link_streamers_and_current_deck(isolated) -> None:
    _live_world(isolated)
    data = asyncio.run(streamers.streaming_now(streamers.sqlite_fetch, now=NOW))

    assert [item["login"] for item in data["items"]] == [
        "kyuhyomi",
        "patrix01_",
        "bgonly",
    ]
    korean, patrix, unknown = data["items"]
    # The live card gives the real login of a non-Latin display name.
    assert korean["streamer"]["slug"] == "kyuhyomi"
    assert korean["mode"] == "battlegrounds" and korean["current_deck"] is None
    assert patrix["language"] == "ru"
    assert patrix["current_deck"]["deck_id"] == 1
    assert patrix["mode"] == "standard" and patrix["mode_source"] == "deck"
    assert patrix["streamer"]["youtube_url"] == "https://www.youtube.com/@patrix01_"
    assert unknown["streamer"] is None and unknown["mode"] is None
    assert data["stale"] is False

    only_standard = asyncio.run(
        streamers.streaming_now(streamers.sqlite_fetch, mode="standard", now=NOW)
    )
    assert [item["login"] for item in only_standard["items"]] == ["patrix01_"]


def test_streamer_reads(isolated) -> None:
    _live_world(isolated)
    fetch = streamers.sqlite_fetch

    listed = asyncio.run(streamers.list_streamers(fetch, now=NOW))
    assert [item["slug"] for item in listed["items"]] == ["patrix01_", "kyuhyomi"]
    assert listed["total"] == 2
    patrix = listed["items"][0]
    assert patrix["live"]["viewers"] == 36 and patrix["avatar_url"]
    assert [deck["deck_id"] for deck in patrix["recent_decks"]] == [1, 2]
    assert patrix["recent_decks"][0]["on_current_stream"] is True
    # The Wild deck was played 2 h into yesterday's 4 h VOD: open the VOD
    # 5 minutes before the game ended.
    vod = patrix["recent_decks"][1]["vod"]
    assert vod["url"] == "https://www.twitch.tv/videos/77?t=1h55m0s"

    wild = asyncio.run(streamers.list_streamers(fetch, format_name="wild", now=NOW))
    assert [item["slug"] for item in wild["items"]] == ["patrix01_"]
    mages = asyncio.run(
        streamers.list_streamers(fetch, player_class="MAGE", q="효", now=NOW)
    )
    assert [item["id"] for item in mages["items"]] == [2]
    live_only = asyncio.run(streamers.list_streamers(fetch, live_only=True, now=NOW))
    assert live_only["total"] == 2

    detail = asyncio.run(streamers.get_streamer(fetch, streamer="96748115"))
    assert detail["login"] == "patrix01_" and detail["followers"] == 1200
    assert {(facet["format"], facet["player_class"]) for facet in detail["facets"]} == {
        ("standard", "PRIEST"),
        ("wild", "WARLOCK"),
    }
    assert detail["vods"][0]["url"] == "https://www.twitch.tv/videos/77"
    assert (
        asyncio.run(streamers.get_streamer(fetch, streamer="PATRIX01_"))["id"]
        == 96748115
    )
    assert asyncio.run(streamers.get_streamer(fetch, streamer="nobody")) is None

    decks = asyncio.run(
        streamers.streamer_decks(fetch, streamer="patrix01_", player_class="WARLOCK")
    )
    assert decks["total"] == 1 and decks["items"][0]["archetype"] == "Zoo Warlock"
    assert decks["items"][0]["hsguru_url"] == "https://www.hsguru.com/deck/2"

    recent = asyncio.run(streamers.recent_decks(fetch, days=1, now=NOW))
    assert [deck["deck_id"] for deck in recent["items"]] == [1, 3]
    assert recent["items"][1]["streamer"]["slug"] == "kyuhyomi"


def test_profiles_due_order_and_backoff(isolated) -> None:
    from app.db import get_db_connection, init_db

    init_db()
    conn = get_db_connection()
    rows = streamers.parse_streamer_decks(
        decks_page(
            [
                deck_row(streamer_id=1, name="Fresh", deck_id=1, played=NOW),
                deck_row(streamer_id=2, name="뀨효미", deck_id=2, played=NOW),
                deck_row(streamer_id=3, name="Checked", deck_id=3, played=NOW),
            ]
        )
    )
    store.upsert_decks(conn, rows, seen_at=NOW.isoformat())
    store.record_profile(
        conn, 3, None, checked_at=(NOW - timedelta(hours=1)).isoformat(), state="failed"
    )

    due = store.profiles_due(conn, now=NOW, limit=10)
    # No Latin login: cannot be looked up; a failure waits two hours.
    assert [row["id"] for row in due] == [1]
    assert [
        row["id"]
        for row in store.profiles_due(conn, now=NOW + timedelta(hours=2), limit=10)
    ] == [1, 3]


# --- REST -----------------------------------------------------------------------


@pytest.fixture()
def client(isolated):
    _live_world(isolated)
    from app.main import app

    return TestClient(app)


def test_rest_endpoints(client) -> None:
    listed = client.get("/v1/hsguru/streamers", params={"days": 0})
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert (
        body["meta"]["source_id"] == "hsguru_streamers" and body["meta"]["count"] == 2
    )

    detail = client.get("/v1/hsguru/streamers/patrix01_")
    assert detail.status_code == 200 and detail.json()["data"]["decks_total"] == 2
    assert client.get("/v1/hsguru/streamers/nobody").status_code == 404

    decks = client.get(
        "/v1/hsguru/streamers/patrix01_/decks", params={"format": "wild"}
    )
    assert [deck["deck_id"] for deck in decks.json()["data"]] == [2]
    assert (
        client.get("/v1/hsguru/streamer-decks", params={"days": 0}).json()["meta"][
            "count"
        ]
        == 3
    )
    assert (
        client.get(
            "/v1/hsguru/streamer-decks", params={"player_class": "priest"}
        ).status_code
        == 422
    )

    live = client.get("/v1/hsguru/streaming-now", params={"known": True})
    assert live.status_code == 200
    assert [item["login"] for item in live.json()["data"]] == ["kyuhyomi", "patrix01_"]
    assert live.headers["cache-control"] == "no-store"
    coverage = client.get("/v1/hsguru/streamers/coverage").json()["data"]
    assert coverage["decks"] == 3 and coverage["runs"][0]["decks_complete"] == 1


# --- PostgreSQL mirror and GraphQL ---------------------------------------------


def test_postgres_migration_mirrors_sqlite_columns_and_types() -> None:
    sql = (ROOT / "platform/sql/018_hsguru_streamers.sql").read_text()
    conn = sqlite3.connect(":memory:")
    store.ensure_hsguru_streamers_schema(conn)
    expected_type = {"INTEGER": "bigint", "TEXT": "text"}
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
    for table in store.TABLES:
        assert f"'{table}'" in stage and f"'{table}'" in merge
        assert f"'{table}'" not in stage.split("APPEND_ONLY")[1].split(")")[0]


def test_graphql_repository_binds_streamer_queries() -> None:
    from app.graphql_api.repository import (
        PostgresGraphQLRepository,
        RepositoryValidationError,
    )

    captured: list[tuple[str, dict[str, Any]]] = []

    class Repository(PostgresGraphQLRepository):
        def __init__(self) -> None:
            pass

        async def _fetch_all(
            self, query: Any, params: dict[str, Any]
        ) -> list[dict[str, Any]]:
            captured.append((query, params))
            return []

    repository = Repository()
    asyncio.run(
        streamers.list_streamers(
            repository.hsguru_streamer_rows,
            streamers.POSTGRES_TABLES,
            q="re",
            format_name="standard",
            now=NOW,
        )
    )
    query, params = captured[0]
    assert "analytics.hsguru_streamers" in query and "%(q)s" in query
    assert ":q" not in query and params["q"] == "%re%"
    with pytest.raises(RepositoryValidationError):
        asyncio.run(repository.hsguru_streamer_rows("SELECT * FROM catalog.cards", {}))


def test_graphql_fields_share_the_rest_reads(isolated) -> None:
    _live_world(isolated)
    from app.graphql_api.schema import schema

    class Repository:
        async def hsguru_streamer_rows(self, query: str, params: dict[str, Any]) -> Any:
            # The mirror has the same rows; run the converted names on SQLite.
            return await streamers.sqlite_fetch(query.replace("analytics.", ""), params)

    class Context:
        repository = Repository()
        request = None

    result = asyncio.run(
        schema.execute(
            """
            { hsguruStreamers(days: 0) hsguruStreamer(streamer: "patrix01_")
              hsguruStreamerDecks(streamer: "kyuhyomi") hsguruStreamingNow(known: true) }
            """,
            context_value=Context(),
        )
    )
    assert result.errors is None, result.errors
    assert result.data["hsguruStreamers"]["total"] == 2
    assert result.data["hsguruStreamer"]["slug"] == "patrix01_"
    assert result.data["hsguruStreamerDecks"]["items"][0]["archetype"] == "Spell Mage"
    assert len(result.data["hsguruStreamingNow"]["items"]) == 2


def test_systemd_timer_matches_the_schedule_inventory() -> None:
    from app.parser_control_schedule import _SCHEDULES

    spec = next(spec for spec in _SCHEDULES if spec.id == "refresh-hsguru-streamers")
    timer = (ROOT / "systemd" / spec.systemd_unit).read_text()
    service = (
        ROOT / "systemd" / spec.systemd_unit.replace(".timer", ".service")
    ).read_text()
    assert f"OnCalendar={spec.on_calendar[0]}" in timer
    assert (
        "app.cli refresh-hsguru-streamers --scheduled --schedule-id refresh-hsguru-streamers"
        in service
    )


def test_twitch_app_shell_is_retried_not_marked_missing(isolated) -> None:
    rows = [deck_row(streamer_id=1, name="Shell", deck_id=1, played=NOW)]
    fake = FakeHsguru(rows, {None: live_page([]), "ru": live_page([])})

    reads: list[str] = []

    async def shell(login: str) -> tuple[int, str]:
        reads.append(login)
        return 200, "<html><head><title>Twitch</title></head></html>"

    _run(fake, now=NOW, twitch=shell)
    assert reads == ["shell", "shell"]  # one more read before giving up
    from app.db import get_db_connection

    row = (
        get_db_connection()
        .execute(f"SELECT profile_state, profile_error FROM {store.STREAMERS}")
        .fetchone()
    )
    assert tuple(row) == ("failed", "profile_data_missing")
