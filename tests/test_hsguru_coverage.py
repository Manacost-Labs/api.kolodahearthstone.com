from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from app import hsguru_brawl as brawl
from app import hsguru_esports as esports
from app.hsguru_pages import Page, PageReader, find_table, player_cell
from app.storage import load_dataset, load_status, save_dataset

TOURNAMENT_ID = "6a6c88f821e3c30013db3d46"
OLD_ID = "5cdb04cdce130203069be2dd"
COMMUNITY_ID = "6acf43976090a5001347461c"


def _meta_html(rows: list[tuple[str, float, str, float, float, str]]) -> str:
    body = "".join(
        f"<tr><td><a href='/archetype/{name}'>{name}</a></td><td>{winrate}</td>"
        f"<td>{popularity}</td><td>{turns}</td><td>{duration}</td><td>{speed}</td></tr>"
        for name, winrate, popularity, turns, duration, speed in rows
    )
    return (
        "<html><body><table><thead><tr><th>Archetype</th><th>Winrate↓</th>"
        "<th>Popularity</th><th>Turns</th><th>Duration</th><th>Climbing Speed</th>"
        f"</tr></thead><tbody>{body}</tbody></table></body></html>"
    )


BRAWL_META = _meta_html(
    [
        ("C'Thun Priest", 72.6, "21.0% (2525)", 9.2, 8.5, "3.21⭐/h"),
        ("Priest", 65.7, "21.6% (2595)", 9.3, 8.6, "2.19⭐/h"),
        ("Y'Shaarj Warrior", 76.9, "0.1% (13)", 8.3, 7.5, "4.33⭐/h"),
    ]
)
EMPTY_META = _meta_html([])
BRAWL_DECKS = """
<html><body><div id="deck_stats_viewport">
<div class="column is-narrow" id="deck_stats-42478834">
  <div class="decklist-info priest">
    <button data-clipboard-text="### Priest
AAEBAa0GAA/X6Qf87gfF8Afo8QeB9geK9geM9ges+Afp+Qfh+gfj+gfk+gfc+wfR/QenkggAAA==
### You can view this deck at https://www.hsguru.com/deck/42478834
"></button>
  </div>
  <span class="tag column"><span>64.4</span></span> Games: 374
</div>
</div></body></html>
"""

ESPORTS = f"""
<table><thead><tr><th>Name</th><th>Start Time</th><th>Tags</th></tr></thead><tbody>
<tr><td><a href="/battlefy/tournament/{COMMUNITY_ID}">Aventa Cup #3</a></td>
<td><time datetime="{{started}}">x</time></td>
<td><div><span>Open</span></div><div><span>Bo3</span></div><div><span>Europe</span></div></td></tr>
<tr><td><a href="/battlefy/tournament/6acf43976090a5001347461d">Future Cup</a></td>
<td><time datetime="2099-01-01T10:00:00.000">x</time></td><td><span>Open</span></td></tr>
</tbody></table>
"""

TOUR_STOPS = f"""
<table><thead><tr><th>Name</th><th>Start Time</th><th>Links</th></tr></thead><tbody>
<tr><td><a href="https://battlefy.com/hsesports/las-vegas/{OLD_ID}/info">Masters Tour Las Vegas</a></td>
<td><time datetime="2019-06-14T16:00:05.000">x</time></td>
<td><a href="/battlefy/tournament/{OLD_ID}">standings</a>
<a href="/battlefy/tournament/{OLD_ID}/lineups">lineups</a></td></tr>
<tr><td><a href="https://battlefy.com/x/summer/{TOURNAMENT_ID}/info">2026 Summer Championship</a></td>
<td><time datetime="{{recent}}">x</time></td>
<td><a href="/battlefy/tournament/{TOURNAMENT_ID}">standings</a>
<a href="/battlefy/tournament/{TOURNAMENT_ID}/lineups">lineups</a></td></tr>
</tbody></table>
"""


def _standings(
    tournament_id: str, stages: list[tuple[str, str]], rows: list[tuple[int, str, str]]
) -> str:
    links = "".join(
        f'<a href="/battlefy/tournament/{tournament_id}?stage_id={stage_id}'
        f'&amp;view_mode=standings">{name}</a>'
        for stage_id, name in stages
    )
    body = "".join(
        f'<tr><td>{place}</td><td><span><img src="https://flagcdn.com/64x48/ua.png" alt="ua"></span>'
        f'<a href="/battlefy/tournament/{tournament_id}/player/{name}"><span class=""><span>{name}</span></span></a></td>'
        f"<td>{score}</td></tr>"
        for place, name, score in rows
    )
    return (
        f"<div>{links}<a href='/battlefy/tournament/{tournament_id}?stage_id=all_brackets'>All</a></div>"
        "<table><tr><th>Place</th><th>Name</th><th>Score</th></tr>"
        f"{body}</table>"
    )


LINEUPS = """
<table><tr><th>Name</th><th>Decks</th></tr>
<tr><td><a href="/battlefy/tournament/x/player/Che0nsu"><span><span>
<img src="https://flagcdn.com/64x48/kr.png" alt="kr"></span><span>Che0nsu</span></span></a></td>
<td>
<div class="decklist-info demonhunter"><button data-clipboard-text="### Void Soul DH
AAECAea5AwaongbNngaKqgeSqgeTqgfosQcM4fgFzp4G0J4G054G3v8GtpcHkr8Hlb8HxN0Hxt0Hx90HteEHAAA=
### You can view this deck at https://www.hsguru.com/deck/41269302
"></button></div>
<div class="decklist-info rogue"><button data-clipboard-text="### Two-Bit Rogue
AAECAaIHBpegBIaoB4eoB4ioB4jZB4jdBwz3nwTQngbZogb3gQfBlweaswf5wwfVxQfL1QeN2ge/9wfl9wcAAA==
### You can view this deck at https://www.hsguru.com/deck/41155127
"></button></div>
</td></tr>
<tr><td><a href="/battlefy/tournament/x/player/Gaby59"><span>Gaby59</span></a></td>
<td><div class="decklist-info demonhunter"><button data-clipboard-text="### Void Soul DH
AAECAea5AwaongbNngaKqgeSqgeTqgfosQcM4fgFzp4G0J4G054G3v8GtpcHkr8Hlb8HxN0Hxt0Hx90HteEHAAA=
"></button></div></td></tr>
</table>
"""

POINTS = """
<a href="/leaderboard/points?points_season=2025">2025</a>
<a href="/leaderboard/points?points_season=2026">2026</a>
<a href="/leaderboard/points?leaderboard_id=STD">Standard</a>
<a href="/leaderboard/points?leaderboard_id=BG_LL">Battlegrounds</a>
<a href="/leaderboard?region=EU">Europe</a>
<table><tr><th>#</th><th>Player</th><th>January</th><th>Total</th></tr>
<tr><td> 1 </td><td><span><span class="tag is-primary is-family-code">EU</span>
<span class="icon"><img src="https://flagcdn.com/64x48/fr.png" alt="fr"></span>
<span><a class="is-link" href="/player-profile/Gaby59"><span>Gaby59</span></a></span></span></td>
<td>6</td><td>40</td></tr>
<tr><td> 2 </td><td><span><span class="tag is-success is-family-code">AP</span>
<span><a class="is-link" href="/player-profile/Che0nsu%233213"><span><span class="icon small">
<object><a href="https://x.com/che0nsu"><img src="/i.webp"></a></object></span>Che0nsu</span></a></span></span></td>
<td>6</td><td>37</td></tr>
</table>
"""

LEGACY_STATS = """
<a href="/legacy-hsesports/mt/qualifier-stats/2021">2021</a>
<a href="/legacy-hsesports/mt/qualifier-stats/all">2020-2026</a>
<a href="/legacy-hsesports/mt/qualifier-stats/2026?limit=100">100</a>
<table><tr><th>#</th><th>Player</th><th>Tour Stops</th><th>Winrate %↑</th></tr>
<tr><td>1</td><td><a href="/player-profile/Furyhunter%232166"><span>Furyhunter</span></a></td>
<td>21</td><td>68.31</td></tr></table>
"""

# The archive nests a <tr> inside its header row.
LEGACY_POINTS = """
<a href="/legacy-hsesports/mt/earnings?points_system=match_wins&amp;season=2022_fall">2022 Fall</a>
<a href="/legacy-hsesports/mt/earnings?points_system=mt_earnings_2020">2020 Earnings</a>
<a href="/legacy-hsesports/mt/earnings?region=EU">Europe</a>
<table><thead><tr><tr><th>#</th><th>Name</th><th>Murder</th><th>Total</th></tr></tr></thead>
<tbody><tr><td>1</td><td><span><span class="tag is-primary">EU</span>
<a href="/player-profile/Furyhunter%232166"><span>Furyhunter</span></a></span></td>
<td>6</td><td>24</td></tr></tbody></table>
"""


class FakeFetcher:
    def __init__(self, pages: dict[str, str], *, fail: set[str] | None = None) -> None:
        self.pages = pages
        self.fail = fail or set()
        self.calls: list[tuple[str, bool]] = []
        self.active = 0
        self.max_active = 0

    async def __call__(self, url: str, validate: Any, render: bool) -> Page:
        self.calls.append((url, render))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if url in self.fail:
                raise RuntimeError("provider failed")
            for key, html in self.pages.items():
                if key(url) if callable(key) else key == url:
                    return Page(url=url, html=html, backend="scrape_do", credits=1)
            raise RuntimeError(f"unexpected url {url}")
        finally:
            self.active -= 1


class RoutedFetcher(FakeFetcher):
    """Routes by a function of the URL instead of exact URLs."""

    def __init__(self, route: Any, *, fail: set[str] | None = None) -> None:
        super().__init__({}, fail=fail)
        self.route = route

    async def __call__(self, url: str, validate: Any, render: bool) -> Page:
        self.calls.append((url, render))
        await asyncio.sleep(0)
        if url in self.fail:
            raise RuntimeError("provider failed")
        html = self.route(url)
        if html is None:
            raise RuntimeError(f"unexpected url {url}")
        return Page(url=url, html=html, backend="scrape_do", credits=1)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.storage.save_status", _save_status_only)
    monkeypatch.setattr(brawl, "save_status", _save_status_only)
    monkeypatch.setattr(esports, "save_status", _save_status_only)
    return tmp_path


def _save_status_only(source_id: str, payload: dict[str, Any]) -> None:
    # The real save_status also feeds the Meta-Lab research store.
    from app.storage import status_path, write_json

    write_json(status_path(source_id), payload)


# --- shared reader ---------------------------------------------------------------


def test_reader_caps_concurrency_at_five_and_records_failures() -> None:
    urls = [f"https://www.hsguru.com/p{index}" for index in range(12)]
    fetcher = FakeFetcher({url: "<p>ok</p>" for url in urls}, fail={urls[3]})
    reader = PageReader("test", fetcher=fetcher, concurrency=40)

    pages = asyncio.run(reader.read_many(urls, lambda html: "ok" in html))

    assert reader.concurrency == 5
    assert fetcher.max_active <= 5
    assert pages[urls[3]] is None
    summary = reader.summary()
    assert summary["pages_ok"] == 11
    assert summary["pages_failed"] == 1
    assert summary["scrape_do_credits"] == 11
    assert summary["errors"][0]["url"] == urls[3]


def test_reader_rejects_pages_that_fail_validation() -> None:
    url = "https://www.hsguru.com/meta"
    reader = PageReader("test", fetcher=FakeFetcher({url: "challenge"}))

    assert asyncio.run(reader.read(url, lambda html: "table" in html)) is None
    assert reader.summary()["errors"] == [{"url": url, "error": "content_validation"}]


def test_player_cell_reads_region_country_and_battletag() -> None:
    table = find_table(POINTS, ("#", "Player", "Total"))
    cells = table.xpath(".//tr")[2].xpath("./td")

    assert player_cell(cells[1]) == {
        "name": "Che0nsu",
        "battletag": "Che0nsu#3213",
        "region": "AP",
        "country": None,
        "profile_url": "https://www.hsguru.com/player-profile/Che0nsu%233213",
    }


# --- Brawl -----------------------------------------------------------------------


def _brawl_route(url: str) -> str | None:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert query["format"] == ["-1"] and query["period"] == ["brawl"]
    if parts.path == "/decks":
        return BRAWL_DECKS
    if parts.path == "/meta":
        return BRAWL_META if query["rank"] == ["all"] else EMPTY_META
    return None


def test_brawl_refresh_publishes_every_slice_and_deck(isolated) -> None:
    fetcher = RoutedFetcher(_brawl_route)

    result = asyncio.run(brawl.refresh_hsguru_brawl(fetcher=fetcher))

    assert result["ok"] is True and result["state"] == "ok"
    assert len(fetcher.calls) == len(brawl.meta_slices()) + len(brawl.DECK_RANKS)
    assert all(render is False for _, render in fetcher.calls)
    structured = load_dataset(brawl.SOURCE_ID)["data"]["structured"]
    base = next(item for item in structured["meta"] if item["key"] == brawl.BASE_KEY)
    assert [row["archetype"] for row in base["rows"]] == [
        "Priest",
        "C'Thun Priest",
        "Y'Shaarj Warrior",
    ]
    assert base["total_games"] == 2595 + 2525 + 13
    assert "min_games=10" in base["source_url"]
    deck = structured["decks"][0]["rows"][0]
    assert deck["source_id"] == brawl.SOURCE_ID
    assert deck["format"] == "Brawl" and deck["games"] == 374
    assert deck["class"] == "Priest"
    assert load_status(brawl.SOURCE_ID)["state"] == "ok"


def test_brawl_missing_secondary_slice_publishes_partial(isolated) -> None:
    failed = brawl.meta_url("legend", "on_coin", brawl.ANY_OPPONENT)
    fetcher = RoutedFetcher(_brawl_route, fail={failed})

    result = asyncio.run(brawl.refresh_hsguru_brawl(fetcher=fetcher))

    assert result["published"] is True and result["state"] == "partial"
    assert result["missing_slices"] == ["legend|on_coin|any"]


def test_brawl_without_the_all_ranks_table_keeps_the_last_snapshot(isolated) -> None:
    save_dataset(
        brawl.SOURCE_ID, {"fetched_at": "2026-10-24T00:00:00+00:00", "data": {}}
    )
    failed = brawl.meta_url("all", "any_player", brawl.ANY_OPPONENT)

    result = asyncio.run(
        brawl.refresh_hsguru_brawl(fetcher=RoutedFetcher(_brawl_route, fail={failed}))
    )

    assert result["published"] is False and result["state"] == "fetch_error"
    assert load_dataset(brawl.SOURCE_ID)["fetched_at"] == "2026-10-24T00:00:00+00:00"
    status = load_status(brawl.SOURCE_ID)
    assert status["serving_cached_dataset"] is True
    assert status["fetched_at"] == "2026-10-24T00:00:00+00:00"


def test_paywalled_brawl_page_is_rejected() -> None:
    assert not brawl.meta_page_is_valid(
        BRAWL_META + "You do not have access to these filters"
    )
    assert not brawl.meta_page_is_valid("<html>Just a moment...</html>")


# --- esports parsers -------------------------------------------------------------


def test_tournament_parsers_read_stops_stages_standings_and_lineups() -> None:
    stops = esports.parse_tour_stops(
        TOUR_STOPS.replace("{recent}", "2026-07-31T14:00:00.000")
    )
    assert stops[0] == {
        "tournament_id": OLD_ID,
        "name": "Masters Tour Las Vegas",
        "start_time": "2019-06-14T16:00:05+00:00",
        "battlefy_url": f"https://battlefy.com/hsesports/las-vegas/{OLD_ID}/info",
    }
    page = _standings(
        TOURNAMENT_ID,
        [("a1", "Group A"), ("t8", "Top 8")],
        [(1, "demoshadow", "3 - 0"), (2, "Incurro", "2 - 1")],
    )
    assert esports.parse_stages(page, TOURNAMENT_ID) == [
        {"stage_id": "a1", "name": "Group A"},
        {"stage_id": "t8", "name": "Top 8"},
    ]
    standings = esports.parse_standings(page)
    assert standings[0]["place"] == 1
    assert standings[0]["player"]["name"] == "demoshadow"
    assert standings[0]["player"]["country"] == "UA"
    assert (standings[1]["wins"], standings[1]["losses"]) == (2, 1)

    lineups = esports.parse_lineups(LINEUPS)
    assert lineups[0]["player"]["name"] == "Che0nsu"
    assert [deck["archetype"] for deck in lineups[0]["decks"]] == [
        "Void Soul DH",
        "Two-Bit Rogue",
    ]
    assert lineups[0]["decks"][1]["class"] == "ROGUE"
    assert lineups[0]["decks"][0]["url"] == "https://www.hsguru.com/deck/41269302"
    popularity = esports.lineup_popularity(lineups)
    assert popularity["archetypes"][0] == {
        "archetype": "Void Soul DH",
        "lineups": 2,
        "share": 1.0,
    }
    assert popularity["classes"][1] == {"class": "ROGUE", "lineups": 1, "share": 0.5}


def test_generic_tables_read_points_and_the_nested_legacy_header() -> None:
    points = esports.parse_table(find_table(POINTS, ("#", "Player", "Total")))
    assert points["headers"] == ["#", "Player", "January", "Total"]
    assert points["rows"][0]["rank"] == 1
    assert points["rows"][0]["player"]["region"] == "EU"
    assert points["rows"][0]["player"]["country"] == "FR"
    assert points["rows"][0]["total"] == 40

    legacy = esports.parse_table(
        find_table(LEGACY_POINTS, ("#", "Name")), player_headers=("Name",)
    )
    assert legacy["headers"] == ["#", "Name", "Murder", "Total"]
    assert legacy["rows"] == [
        {
            "rank": 1,
            "player": {
                "name": "Furyhunter",
                "battletag": "Furyhunter#2166",
                "region": "EU",
                "country": None,
                "profile_url": "https://www.hsguru.com/player-profile/Furyhunter%232166",
            },
            "murder": 6,
            "total": 24,
        }
    ]
    stats = esports.parse_table(find_table(LEGACY_STATS, ("#", "Player")))
    assert stats["rows"][0]["winrate_pct"] == 68.31


def _esports_route(now: datetime, *, fail_tournament: bool = False):
    started = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000")
    recent = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.000")

    def route(url: str) -> str | None:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if url == esports.ESPORTS_URL:
            return ESPORTS.replace("{started}", started)
        if url == esports.TOUR_STOPS_URL:
            return TOUR_STOPS.replace("{recent}", recent)
        for tournament_id in (TOURNAMENT_ID, OLD_ID, COMMUNITY_ID):
            base = f"/battlefy/tournament/{tournament_id}"
            if parts.path == f"{base}/lineups":
                return LINEUPS
            if parts.path == base:
                if fail_tournament and tournament_id == TOURNAMENT_ID:
                    return None
                stage = (query.get("stage_id") or [None])[0]
                stages = [("a1", "Group A"), ("t8", "Top 8")]
                if tournament_id != TOURNAMENT_ID:
                    stages = [("s1", "Swiss")]
                rows = [(1, f"winner-{stage or 'default'}", "3 - 0")]
                return _standings(tournament_id, stages, rows)
        if parts.path == "/leaderboard/points":
            return POINTS
        if parts.path.startswith("/legacy-hsesports/mt/stats") or parts.path.startswith(
            "/legacy-hsesports/mt/qualifier-stats"
        ):
            return LEGACY_STATS
        if parts.path in {
            "/legacy-hsesports/mt/points",
            "/legacy-hsesports/mt/earnings",
        }:
            return LEGACY_POINTS
        return None

    return route


def test_tournaments_refresh_collects_stops_and_started_community_events(
    isolated,
) -> None:
    now = datetime.now(UTC)
    fetcher = RoutedFetcher(_esports_route(now))

    result = asyncio.run(esports.refresh_hsguru_esports_tournaments(fetcher=fetcher))

    assert result["ok"] is True and result["state"] == "ok"
    assert (esports.ESPORTS_URL, True) in fetcher.calls
    structured = load_dataset(esports.TOURNAMENTS_SOURCE_ID)["data"]["structured"]
    assert [item["name"] for item in structured["upcoming"]] == [
        "Aventa Cup #3",
        "Future Cup",
    ]
    by_id = {item["tournament_id"]: item for item in structured["tournaments"]}
    # The future community tournament has nothing to read yet.
    assert set(by_id) == {TOURNAMENT_ID, OLD_ID, COMMUNITY_ID}
    assert by_id[COMMUNITY_ID]["kind"] == "community"
    recent = by_id[TOURNAMENT_ID]
    assert [stage["name"] for stage in recent["stages"]] == ["Group A", "Top 8"]
    assert recent["stages"][1]["standings"][0]["player"]["name"] == "winner-t8"
    assert recent["lineup_popularity"]["lineups"] == 2
    assert recent["complete"] is True
    # A single-stage tournament takes its standings from the first page.
    assert (
        by_id[OLD_ID]["stages"][0]["standings"][0]["player"]["name"] == "winner-default"
    )


def test_finished_tournaments_are_reused_and_failed_rereads_keep_the_last(
    isolated,
) -> None:
    now = datetime.now(UTC)
    asyncio.run(
        esports.refresh_hsguru_esports_tournaments(
            fetcher=RoutedFetcher(_esports_route(now))
        )
    )
    dataset = load_dataset(esports.TOURNAMENTS_SOURCE_ID)
    for item in dataset["data"]["structured"]["tournaments"]:
        if item["tournament_id"] == OLD_ID:
            item["collected_at"] = "2019-07-30T00:00:00+00:00"
    save_dataset(esports.TOURNAMENTS_SOURCE_ID, dataset)

    fetcher = RoutedFetcher(_esports_route(now, fail_tournament=True))
    result = asyncio.run(esports.refresh_hsguru_esports_tournaments(fetcher=fetcher))

    assert not any(OLD_ID in url for url, _ in fetcher.calls)
    assert result["state"] == "partial"
    by_id = {
        item["tournament_id"]: item
        for item in load_dataset(esports.TOURNAMENTS_SOURCE_ID)["data"]["structured"][
            "tournaments"
        ]
    }
    assert by_id[OLD_ID]["reused"] is True
    assert by_id[TOURNAMENT_ID]["refresh_failed"] is True
    assert (
        by_id[TOURNAMENT_ID]["stages"][1]["standings"][0]["player"]["name"]
        == "winner-t8"
    )


def test_points_refresh_reads_every_season_and_leaderboard(isolated) -> None:
    fetcher = RoutedFetcher(_esports_route(datetime.now(UTC)))

    result = asyncio.run(esports.refresh_hsguru_esports_points(fetcher=fetcher))

    assert result["ok"] is True
    seasons = load_dataset(esports.POINTS_SOURCE_ID)["data"]["structured"]["seasons"]
    assert [(item["season"], item["leaderboard"]) for item in seasons] == [
        ("2025", "STD"),
        ("2025", "BG_LL"),
        ("2026", "STD"),
        ("2026", "BG_LL"),
    ]
    assert "limit=3000" in seasons[0]["source_url"]


def test_legacy_refresh_runs_weekly_unless_forced(isolated) -> None:
    fetcher = RoutedFetcher(_esports_route(datetime.now(UTC)))

    first = asyncio.run(esports.refresh_hsguru_esports_legacy(fetcher=fetcher))
    calls = len(fetcher.calls)
    second = asyncio.run(esports.refresh_hsguru_esports_legacy(fetcher=fetcher))
    calls_after_skip = len(fetcher.calls)
    forced = asyncio.run(
        esports.refresh_hsguru_esports_legacy(fetcher=fetcher, force=True)
    )

    assert first["published"] is True
    assert second["skipped"] is True and calls_after_skip == calls
    assert forced["published"] is True and len(fetcher.calls) > calls
    structured = load_dataset(esports.LEGACY_SOURCE_ID)["data"]["structured"]
    assert [item["period"] for item in structured["qualifier_stats"]] == ["2021", "all"]
    assert [item["points_system"] for item in structured["mt_points"]] == [
        "match_wins",
        "mt_earnings_2020",
    ]
    assert structured["mt_points"][0]["season"] == "2022_fall"


# --- REST and GraphQL --------------------------------------------------------------


@pytest.fixture()
def published(isolated):
    now = datetime.now(UTC)
    asyncio.run(brawl.refresh_hsguru_brawl(fetcher=RoutedFetcher(_brawl_route)))
    route = _esports_route(now)
    asyncio.run(
        esports.refresh_hsguru_esports_tournaments(fetcher=RoutedFetcher(route))
    )
    asyncio.run(esports.refresh_hsguru_esports_points(fetcher=RoutedFetcher(route)))
    asyncio.run(esports.refresh_hsguru_esports_legacy(fetcher=RoutedFetcher(route)))
    return isolated


def test_rest_endpoints_serve_brawl_and_esports(published) -> None:
    from app.main import app

    client = TestClient(app)
    meta = client.get("/v1/hsguru/brawl/meta", params={"min_games": 100}).json()
    assert meta["meta"]["source_id"] == "hsguru_brawl"
    assert [row["archetype"] for row in meta["data"]["items"]] == [
        "Priest",
        "C'Thun Priest",
    ]
    assert (
        client.get("/v1/hsguru/brawl/meta", params={"rank": "nope"}).status_code == 422
    )
    assert (
        client.get("/v1/hsguru/brawl/meta", params={"opponent_class": "x"}).status_code
        == 422
    )
    decks = client.get("/v1/hsguru/brawl/decks", params={"class": "priest"}).json()
    assert decks["data"]["items"][0]["games"] == 374

    summaries = client.get(
        "/v1/hsguru/esports/tournaments", params={"kind": "tour_stop"}
    ).json()
    assert summaries["meta"]["count"] == 2
    recent = next(
        item for item in summaries["data"] if item["tournament_id"] == TOURNAMENT_ID
    )
    assert recent["winner"]["name"] == "winner-t8"
    assert recent["top_archetypes"][0]["archetype"] == "Void Soul DH"
    assert "lineups" not in recent
    one = client.get(f"/v1/hsguru/esports/tournaments/{TOURNAMENT_ID}").json()
    assert one["data"]["lineups"][0]["player"]["name"] == "Che0nsu"
    assert client.get("/v1/hsguru/esports/tournaments/unknown").status_code == 404
    assert len(client.get("/v1/hsguru/esports/upcoming").json()["data"]["items"]) == 2

    points = client.get("/v1/hsguru/esports/points", params={"region": "AP"}).json()
    assert points["data"]["season"] == "2026"
    assert [row["player"]["name"] for row in points["data"]["items"]] == ["Che0nsu"]
    seasons = client.get("/v1/hsguru/esports/points/seasons").json()
    assert seasons["meta"]["count"] == 4

    legacy = client.get("/v1/hsguru/esports/legacy/qualifier-stats").json()
    assert legacy["data"]["period"] == "all"
    assert legacy["data"]["options"] == ["2021", "all"]
    earnings = client.get(
        "/v1/hsguru/esports/legacy/mt-points", params={"selector": "mt_earnings_2020"}
    ).json()
    assert earnings["data"]["points_system"] == "mt_earnings_2020"


def test_rest_endpoints_answer_503_before_the_first_publication(isolated) -> None:
    from app.main import app

    response = TestClient(app).get("/v1/hsguru/esports/points")
    assert response.status_code == 503


def test_graphql_fields_read_the_imported_dataset(published, monkeypatch) -> None:
    from app.main import app

    datasets = {
        source_id: load_dataset(source_id)
        for source_id in (brawl.SOURCE_ID, *esports.SOURCE_IDS)
    }

    class Repository:
        async def dataset(self, source_id: str, dataset_version: str | None) -> Any:
            payload = datasets.get(source_id)
            return {"payload": payload} if payload else None

    monkeypatch.setattr(
        "app.graphql_api.router.get_graphql_repository", lambda: Repository()
    )
    query = f"""
    query {{
      hsguruBrawlMeta(rank: "all", minGames: 100)
      hsguruEsportsTournaments(tournamentId: "{TOURNAMENT_ID}")
      hsguruEsportsPoints(leaderboard: "BG_LL", season: "2025")
      hsguruEsportsLegacy(table: "mt-stats")
      missing: hsguruEsportsTournaments(tournamentId: "unknown")
    }}
    """
    response = TestClient(app).post(
        "/v1/", json={"query": query}, follow_redirects=True
    )

    body = response.json()
    assert "errors" not in body, body
    data = body["data"]
    assert len(data["hsguruBrawlMeta"]["items"]) == 2
    assert data["hsguruEsportsTournaments"]["stages"][0]["name"] == "Group A"
    assert data["hsguruEsportsPoints"]["leaderboard"] == "BG_LL"
    assert data["hsguruEsportsLegacy"]["items"][0]["tour_stops"] == 21
    assert data["missing"] is None

    invalid = (
        TestClient(app)
        .post(
            "/v1/",
            json={"query": 'query { hsguruBrawlMeta(rank: "nope") }'},
            follow_redirects=True,
        )
        .json()
    )
    assert invalid["errors"][0]["extensions"]["code"] == "VALIDATION_ERROR"
