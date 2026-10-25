"""HSGuru esports: tournaments, HSEsports points and the legacy Masters Tour archive.

Three datasets, each refreshed by ``refresh-hsguru-esports``:

* ``hsguru_esports_tournaments`` — upcoming and recent tournaments from
  ``/esports`` (rows arrive over the LiveView socket, so this one page is
  rendered) and every official tour stop from ``/mt/tour-stops`` with the
  standings of each stage, every player's lineup (deck codes) and the lineup
  archetype popularity derived from them. Finished tournaments are immutable:
  a complete earlier snapshot of one is reused instead of read again.
* ``hsguru_esports_points`` — ``/leaderboard/points`` for every season and
  leaderboard (Standard, Battlegrounds) the page offers.
* ``hsguru_esports_legacy`` — the archived Masters Tour pages: player MT
  statistics, qualifier statistics per year and tour stop, and the points or
  earnings of each legacy season. The archive changes rarely and is read at
  most once a week.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from .deck_decode import first_deck_code_from_text
from .hsguru_pages import (
    HSGURU,
    PageFetcher,
    PageReader,
    document,
    find_table,
    is_paywalled,
    player_cell,
    table_body_rows,
    table_headers,
    text,
)
from .source_state import SourceState
from .storage import load_dataset, save_dataset, save_status

TOURNAMENTS_SOURCE_ID = "hsguru_esports_tournaments"
POINTS_SOURCE_ID = "hsguru_esports_points"
LEGACY_SOURCE_ID = "hsguru_esports_legacy"
SOURCE_IDS = (TOURNAMENTS_SOURCE_ID, POINTS_SOURCE_ID, LEGACY_SOURCE_ID)

ESPORTS_URL = f"{HSGURU}/esports"
TOUR_STOPS_URL = f"{HSGURU}/mt/tour-stops"
POINTS_URL = f"{HSGURU}/leaderboard/points"
LEGACY_MT_STATS_URL = f"{HSGURU}/legacy-hsesports/mt/stats"
LEGACY_QUALIFIER_STATS_URL = f"{HSGURU}/legacy-hsesports/mt/qualifier-stats"
LEGACY_POINTS_URL = f"{HSGURU}/legacy-hsesports/mt/points"
LEGACY_EARNINGS_URL = f"{HSGURU}/legacy-hsesports/mt/earnings"

POINTS_LIMIT = 3000
LEGACY_LIMIT = 5000
# A tournament older than this is finished; its complete snapshot is final.
FINISHED_AFTER = timedelta(days=10)
# Community tournaments of the /esports list are read once they have started
# and while they are this recent.
RECENT_TOURNAMENT_WINDOW = timedelta(days=14)
LEGACY_MAX_AGE = timedelta(days=7)

_TOURNAMENT_PATH_RE = re.compile(r"^/battlefy/tournament/([0-9a-f]{24})(?:[/?]|$)")
_SCORE_RE = re.compile(r"^\s*(-?\d+)\s*-\s*(-?\d+)\s*$")
_NUMERIC_RE = re.compile(r"^-?\d+(?:[.,]\d+)?%?$")
_CLASS_TOKENS = {
    "deathknight": "DEATHKNIGHT",
    "demonhunter": "DEMONHUNTER",
    "druid": "DRUID",
    "hunter": "HUNTER",
    "mage": "MAGE",
    "paladin": "PALADIN",
    "priest": "PRIEST",
    "rogue": "ROGUE",
    "shaman": "SHAMAN",
    "warlock": "WARLOCK",
    "warrior": "WARRIOR",
}


# --- generic table parsing --------------------------------------------------------


def header_key(header: str) -> str:
    if header.strip() == "#":
        return "rank"
    key = header.casefold().replace("%", " pct ")
    return re.sub(r"[^0-9a-z]+", "_", key).strip("_") or "column"


def cell_value(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if _NUMERIC_RE.fullmatch(value):
        number = float(value.rstrip("%").replace(",", "."))
        return int(number) if number.is_integer() and "." not in value else number
    return value


def parse_table(
    table: Any, *, player_headers: tuple[str, ...] = ("Player",)
) -> dict[str, Any]:
    """Headers and rows of an HSGuru table; player columns become objects."""
    headers = table_headers(table)
    keys = [header_key(header) for header in headers]
    players = {label.casefold() for label in player_headers}
    rows: list[dict[str, Any]] = []
    for tr in table_body_rows(table):
        cells = tr.xpath("./td")
        if not cells:
            continue
        row: dict[str, Any] = {}
        for header, key, cell in zip(headers, keys, cells, strict=False):
            if header.casefold() in players:
                row["player"] = player_cell(cell)
            else:
                row[key] = cell_value(text(cell))
        rows.append(row)
    return {"headers": headers, "rows": rows}


def table_page_validator(*headers: str) -> Callable[[str], bool]:
    def validate(page_html: str) -> bool:
        return (
            not is_paywalled(page_html) and find_table(page_html, headers) is not None
        )

    return validate


def _tournament_id(href: str) -> str | None:
    match = _TOURNAMENT_PATH_RE.match(urlsplit(str(href or "")).path + "?")
    return match.group(1) if match else None


def _time(cell: Any) -> str | None:
    found = cell.xpath(".//time/@datetime")
    if not found:
        return None
    value = str(found[0]).strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --- tournament pages ------------------------------------------------------------


def tournament_url(tournament_id: str, suffix: str = "") -> str:
    return f"{HSGURU}/battlefy/tournament/{tournament_id}{suffix}"


def standings_url(tournament_id: str, stage_id: str | None = None) -> str:
    query = {"view_mode": "standings"}
    if stage_id:
        query["stage_id"] = stage_id
    return f"{tournament_url(tournament_id)}?{urlencode(query)}"


def lineups_url(tournament_id: str) -> str:
    return tournament_url(tournament_id, "/lineups")


def parse_esports_page(page_html: str) -> list[dict[str, Any]]:
    """Upcoming and running tournaments listed on ``/esports``."""
    table = find_table(page_html, ("Name", "Start Time", "Tags"))
    if table is None:
        return []
    rows: list[dict[str, Any]] = []
    for tr in table_body_rows(table):
        cells = tr.xpath("./td")
        if len(cells) < 3:
            continue
        link = cells[0].find(".//a[@href]")
        href = str(link.get("href")) if link is not None else ""
        tournament_id = _tournament_id(href)
        rows.append(
            {
                "tournament_id": tournament_id,
                "name": text(cells[0]),
                "start_time": _time(cells[1]),
                "tags": [text(tag) for tag in cells[2].xpath(".//span") if text(tag)],
                "url": f"{HSGURU}{href}" if href.startswith("/") else href or None,
            }
        )
    return rows


def esports_page_is_valid(page_html: str) -> bool:
    return (
        find_table(page_html, ("Name", "Start Time", "Tags")) is not None
        and "loading tournaments" not in (page_html or "").casefold()
    )


def parse_tour_stops(page_html: str) -> list[dict[str, Any]]:
    table = find_table(page_html, ("Name", "Start Time", "Links"))
    if table is None:
        return []
    stops: list[dict[str, Any]] = []
    for tr in table_body_rows(table):
        cells = tr.xpath("./td")
        if len(cells) < 3:
            continue
        tournament_id = next(
            (
                found
                for found in (
                    _tournament_id(href) for href in cells[2].xpath(".//a/@href")
                )
                if found
            ),
            None,
        )
        if tournament_id is None:
            continue
        battlefy = cells[0].xpath(".//a/@href")
        stops.append(
            {
                "tournament_id": tournament_id,
                "name": text(cells[0]),
                "start_time": _time(cells[1]),
                "battlefy_url": str(battlefy[0]) if battlefy else None,
            }
        )
    return stops


def parse_stages(page_html: str, tournament_id: str) -> list[dict[str, str]]:
    """Stages of the stage selector of a tournament standings page."""
    stages: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in document(page_html).xpath('//a[contains(@href, "stage_id=")]'):
        href = str(link.get("href") or "")
        parts = urlsplit(href)
        if parts.path.rstrip("/") != f"/battlefy/tournament/{tournament_id}":
            continue
        stage_id = (parse_qs(parts.query).get("stage_id") or [""])[0]
        if not stage_id or stage_id == "all_brackets" or stage_id in seen:
            continue
        seen.add(stage_id)
        stages.append({"stage_id": stage_id, "name": text(link)})
    return stages


def standings_page_is_valid(page_html: str) -> bool:
    return "/battlefy/tournament/" in (page_html or "") and not is_paywalled(page_html)


def parse_standings(page_html: str) -> list[dict[str, Any]]:
    table = find_table(page_html, ("Place", "Name", "Score"))
    if table is None:
        return []
    rows: list[dict[str, Any]] = []
    for tr in table_body_rows(table):
        cells = tr.xpath("./td")
        if len(cells) < 3:
            continue
        player = player_cell(cells[1])
        score = text(cells[2])
        match = _SCORE_RE.match(score)
        row = {
            "place": cell_value(text(cells[0])),
            "player": player,
            "score": score,
            "wins": int(match.group(1)) if match else None,
            "losses": int(match.group(2)) if match else None,
        }
        for header, cell in zip(table_headers(table)[3:], cells[3:], strict=False):
            row[header_key(header)] = cell_value(text(cell))
        rows.append(row)
    return rows


def lineups_page_is_valid(page_html: str) -> bool:
    return find_table(page_html, ("Name", "Decks")) is not None or (
        "lineups" in (page_html or "").casefold() and "Total: 0" in (page_html or "")
    )


_LINEUPS_TOTAL_RE = re.compile(r"Total:\s*(\d+)")


def lineups_total(page_html: str) -> int | None:
    """The lineup count HSGuru reports; the page shows the first 50 of them."""
    match = _LINEUPS_TOTAL_RE.search(text(document(page_html)))
    return int(match.group(1)) if match else None


def parse_lineups(page_html: str) -> list[dict[str, Any]]:
    table = find_table(page_html, ("Name", "Decks"))
    if table is None:
        return []
    lineups: list[dict[str, Any]] = []
    for tr in table_body_rows(table):
        cells = tr.xpath("./td")
        if len(cells) < 2:
            continue
        player = player_cell(cells[0])
        decks: list[dict[str, Any]] = []
        for info in cells[1].xpath('.//div[contains(@class, "decklist-info")]'):
            button = info.find(".//button[@data-clipboard-text]")
            clip = (
                str(button.get("data-clipboard-text") or "")
                if button is not None
                else ""
            )
            title = re.search(r"^###\s+(.+?)\s*$", clip, flags=re.MULTILINE)
            code = first_deck_code_from_text(clip) or ""
            if not code:
                continue
            link = re.search(r"https://www\.hsguru\.com/deck/\d+", clip)
            tokens = str(info.get("class") or "").split()
            decks.append(
                {
                    "archetype": title.group(1).strip() if title else None,
                    "class": next(
                        (
                            _CLASS_TOKENS[token]
                            for token in tokens
                            if token in _CLASS_TOKENS
                        ),
                        None,
                    ),
                    "deck_code": code,
                    "url": link.group(0) if link else None,
                }
            )
        lineups.append({"player": player, "decks": decks})
    return lineups


def lineup_popularity(lineups: list[dict[str, Any]]) -> dict[str, Any]:
    """How many lineups bring each archetype and class."""
    players = len(lineups)
    archetypes: Counter[str] = Counter()
    classes: Counter[str] = Counter()
    for lineup in lineups:
        archetypes.update(
            {
                str(deck["archetype"])
                for deck in lineup["decks"]
                if deck.get("archetype")
            }
        )
        classes.update(
            {str(deck["class"]) for deck in lineup["decks"] if deck.get("class")}
        )

    def shares(counter: Counter[str], key: str) -> list[dict[str, Any]]:
        return [
            {
                key: name,
                "lineups": count,
                "share": round(count / players, 4) if players else None,
            }
            for name, count in counter.most_common()
        ]

    return {
        "lineups": players,
        "archetypes": shares(archetypes, "archetype"),
        "classes": shares(classes, "class"),
    }


async def collect_tournament(
    reader: PageReader, tournament: dict[str, Any]
) -> dict[str, Any]:
    tournament_id = str(tournament["tournament_id"])
    first_url = standings_url(tournament_id)
    first, lineups_page = await asyncio.gather(
        reader.read(first_url, standings_page_is_valid),
        reader.read(lineups_url(tournament_id), lineups_page_is_valid),
    )
    missing: list[str] = []
    stages: list[dict[str, Any]] = []
    if first is None:
        missing.append("standings")
    else:
        found = parse_stages(first.html, tournament_id)
        if len(found) <= 1:
            name = found[0]["name"] if found else None
            stages.append(
                {
                    "stage_id": found[0]["stage_id"] if found else None,
                    "name": name,
                    "standings": parse_standings(first.html),
                }
            )
        else:
            urls = [standings_url(tournament_id, stage["stage_id"]) for stage in found]
            pages = await reader.read_many(urls, standings_page_is_valid)
            for stage, url in zip(found, urls, strict=True):
                page = pages.get(url)
                if page is None:
                    missing.append(f"stage:{stage['stage_id']}")
                    continue
                stages.append({**stage, "standings": parse_standings(page.html)})
    lineups: list[dict[str, Any]] = []
    total: int | None = None
    if lineups_page is None:
        missing.append("lineups")
    else:
        lineups = parse_lineups(lineups_page.html)
        total = lineups_total(lineups_page.html)
    return {
        **tournament,
        "standings_url": first_url,
        "lineups_url": lineups_url(tournament_id),
        "stages": stages,
        "lineups": lineups,
        # Further lineup pages switch over the LiveView socket only.
        "lineups_total": total if total is not None else len(lineups),
        "lineups_complete": total is None or len(lineups) >= total,
        "lineup_popularity": lineup_popularity(lineups),
        "missing": missing,
        "complete": not missing,
        "collected_at": datetime.now(UTC).isoformat(),
    }


def _reusable(previous: dict[str, Any] | None, now: datetime) -> bool:
    if not previous or not previous.get("complete"):
        return False
    started = _parse_time(previous.get("start_time"))
    collected = _parse_time(previous.get("collected_at"))
    return bool(
        started
        and collected
        and now - started > FINISHED_AFTER
        and collected - started > FINISHED_AFTER
    )


def _previous_tournaments() -> dict[str, dict[str, Any]]:
    dataset = load_dataset(TOURNAMENTS_SOURCE_ID) or {}
    structured = (dataset.get("data") or {}).get("structured") or {}
    return {
        str(item.get("tournament_id")): item
        for item in structured.get("tournaments") or []
        if isinstance(item, dict) and item.get("tournament_id")
    }


async def collect_tournaments(reader: PageReader) -> dict[str, Any]:
    now = datetime.now(UTC)
    esports_page, stops_page = await asyncio.gather(
        reader.read(ESPORTS_URL, esports_page_is_valid, render=True),
        reader.read(
            TOUR_STOPS_URL, table_page_validator("Name", "Start Time", "Links")
        ),
    )
    missing: list[str] = []
    upcoming = parse_esports_page(esports_page.html) if esports_page else []
    if esports_page is None:
        missing.append("esports")
    stops = parse_tour_stops(stops_page.html) if stops_page else []
    if stops_page is None:
        missing.append("tour_stops")
    candidates: dict[str, dict[str, Any]] = {}
    for stop in stops:
        candidates[stop["tournament_id"]] = {**stop, "kind": "tour_stop"}
    for item in upcoming:
        started = _parse_time(item.get("start_time"))
        if (
            item.get("tournament_id")
            and item["tournament_id"] not in candidates
            and started is not None
            and timedelta(0) <= now - started <= RECENT_TOURNAMENT_WINDOW
        ):
            candidates[item["tournament_id"]] = {
                "tournament_id": item["tournament_id"],
                "name": item["name"],
                "start_time": item["start_time"],
                "tags": item["tags"],
                "kind": "community",
            }
    previous = _previous_tournaments()
    reused: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for tournament_id, tournament in candidates.items():
        old = previous.get(tournament_id)
        if _reusable(old, now):
            reused.append({**old, **tournament, "reused": True})
        elif (
            _parse_time(tournament.get("start_time"))
            and _parse_time(tournament["start_time"]) > now
        ):
            continue
        else:
            pending.append(tournament)
    collected = await asyncio.gather(
        *(collect_tournament(reader, tournament) for tournament in pending)
    )
    for tournament in collected:
        old = previous.get(str(tournament["tournament_id"]))
        # A failed re-read keeps the last complete snapshot of the tournament.
        if not tournament["complete"] and old and old.get("complete"):
            missing.append(f"tournament:{tournament['tournament_id']}")
            reused.append({**old, "reused": True, "refresh_failed": True})
            continue
        if not tournament["complete"]:
            missing.append(f"tournament:{tournament['tournament_id']}")
        reused.append({**tournament, "reused": False})
    tournaments = sorted(
        reused, key=lambda item: str(item.get("start_time") or ""), reverse=True
    )
    return {
        "type": TOURNAMENTS_SOURCE_ID,
        "fetched_at": now.isoformat(),
        "upcoming": upcoming,
        "tournaments": tournaments,
        "missing": missing,
    }


# --- HSEsports points ------------------------------------------------------------


def _query_values(page_html: str, path: str, name: str) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in document(page_html).xpath(f'//a[contains(@href, "{name}=")]'):
        parts = urlsplit(str(link.get("href") or ""))
        if parts.path.rstrip("/") != path:
            continue
        value = (parse_qs(parts.query).get(name) or [""])[0]
        if value and value not in seen:
            seen.add(value)
            values.append((value, text(link)))
    return values


def points_url(season: str, leaderboard: str) -> str:
    query = {
        "points_season": season,
        "leaderboard_id": leaderboard,
        "limit": POINTS_LIMIT,
    }
    return f"{POINTS_URL}?{urlencode(query)}"


async def collect_points(reader: PageReader) -> dict[str, Any]:
    validate = table_page_validator("#", "Player", "Total")
    base = await reader.read(f"{POINTS_URL}?limit={POINTS_LIMIT}", validate)
    if base is None:
        return {"type": POINTS_SOURCE_ID, "seasons": [], "missing": ["points"]}
    seasons = _query_values(base.html, "/leaderboard/points", "points_season")
    leaderboards = _query_values(base.html, "/leaderboard/points", "leaderboard_id")
    targets = [
        (season, season_label, board, board_label)
        for season, season_label in seasons
        for board, board_label in leaderboards
    ]
    urls = [points_url(season, board) for season, _, board, _ in targets]
    pages = await reader.read_many(urls, validate)
    tables: list[dict[str, Any]] = []
    missing: list[str] = []
    for (season, season_label, board, board_label), url in zip(
        targets, urls, strict=True
    ):
        page = pages.get(url)
        if page is None:
            missing.append(f"{season}|{board}")
            continue
        table = find_table(page.html, ("#", "Player", "Total"))
        parsed = parse_table(table)
        tables.append(
            {
                "season": season,
                "season_label": season_label,
                "leaderboard": board,
                "leaderboard_label": board_label,
                "source_url": url,
                "headers": parsed["headers"],
                "rows": parsed["rows"],
            }
        )
    return {
        "type": POINTS_SOURCE_ID,
        "fetched_at": datetime.now(UTC).isoformat(),
        "seasons": tables,
        "missing": missing,
    }


# --- legacy Masters Tour archive -------------------------------------------------


def _path_values(page_html: str, prefix: str) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in document(page_html).xpath(f'//a[starts-with(@href, "{prefix}/")]'):
        parts = urlsplit(str(link.get("href") or ""))
        segment = parts.path[len(prefix) + 1 :]
        label = text(link)
        # Period links keep the page size (?limit=); sort and page-size links
        # of the current period are not periods.
        if (
            not segment
            or "/" in segment
            or set(parse_qs(parts.query)) - {"limit"}
            or label.startswith("Show ")
            or segment in seen
        ):
            continue
        seen.add(segment)
        values.append((unquote(segment), label))
    return values


def _earnings_targets(page_html: str) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in document(page_html).xpath('//a[contains(@href, "points_system=")]'):
        parts = urlsplit(str(link.get("href") or ""))
        query = parse_qs(parts.query)
        # Without a season HSGuru answers with its default season's table.
        if set(query) != {"points_system", "season"}:
            continue
        key = urlencode(sorted((name, values[0]) for name, values in query.items()))
        if key not in seen:
            seen.add(key)
            targets.append((key, text(link)))
    return targets


async def collect_legacy(reader: PageReader) -> dict[str, Any]:
    stats_validate = table_page_validator("#", "Player")
    mt_stats_url = f"{LEGACY_MT_STATS_URL}?limit=3000"
    stats_page, qualifier_base, points_base = await asyncio.gather(
        reader.read(mt_stats_url, stats_validate),
        reader.read(
            f"{LEGACY_QUALIFIER_STATS_URL}?limit={LEGACY_LIMIT}", stats_validate
        ),
        reader.read(LEGACY_POINTS_URL, table_page_validator("#", "Name", "Total")),
    )
    missing: list[str] = []
    mt_stats: dict[str, Any] = {}
    if stats_page is None:
        missing.append("mt_stats")
    else:
        mt_stats = {
            "source_url": mt_stats_url,
            **parse_table(find_table(stats_page.html, ("#", "Player"))),
        }
    qualifier_periods = (
        _path_values(qualifier_base.html, "/legacy-hsesports/mt/qualifier-stats")
        if qualifier_base
        else []
    )
    if qualifier_base is None:
        missing.append("qualifier_stats")
    qualifier_urls = [
        f"{LEGACY_QUALIFIER_STATS_URL}/{period}?limit={LEGACY_LIMIT}"
        for period, _ in qualifier_periods
    ]
    earnings_targets = _earnings_targets(points_base.html) if points_base else []
    if points_base is None:
        missing.append("mt_points")
    earnings_urls = [f"{LEGACY_EARNINGS_URL}?{query}" for query, _ in earnings_targets]
    qualifier_pages, earnings_pages = await asyncio.gather(
        reader.read_many(qualifier_urls, stats_validate),
        reader.read_many(earnings_urls, table_page_validator("#", "Name")),
    )
    qualifier_stats: list[dict[str, Any]] = []
    if qualifier_base is not None:
        # The page without a period is the current year, which has no link.
        title = document(qualifier_base.html).findtext(".//title") or ""
        qualifier_stats.append(
            {
                "period": "current",
                "label": " ".join(title.split()) or None,
                "source_url": f"{LEGACY_QUALIFIER_STATS_URL}?limit={LEGACY_LIMIT}",
                **parse_table(find_table(qualifier_base.html, ("#", "Player"))),
            }
        )
    for (period, label), url in zip(qualifier_periods, qualifier_urls, strict=True):
        page = qualifier_pages.get(url)
        if page is None:
            missing.append(f"qualifier_stats:{period}")
            continue
        qualifier_stats.append(
            {
                "period": period,
                "label": label,
                "source_url": url,
                **parse_table(find_table(page.html, ("#", "Player"))),
            }
        )
    earnings: list[dict[str, Any]] = []
    for (query, label), url in zip(earnings_targets, earnings_urls, strict=True):
        page = earnings_pages.get(url)
        if page is None:
            missing.append(f"mt_points:{query}")
            continue
        selector = {name: values[0] for name, values in parse_qs(query).items()}
        earnings.append(
            {
                "points_system": selector.get("points_system"),
                "season": selector.get("season"),
                "label": label,
                "source_url": url,
                **parse_table(
                    find_table(page.html, ("#", "Name")), player_headers=("Name",)
                ),
            }
        )
    return {
        "type": LEGACY_SOURCE_ID,
        "fetched_at": datetime.now(UTC).isoformat(),
        "mt_stats": mt_stats,
        "qualifier_stats": qualifier_stats,
        "mt_points": earnings,
        "missing": missing,
    }


# --- publication -------------------------------------------------------------------


def _rows_total(source_id: str, structured: dict[str, Any]) -> int:
    if source_id == TOURNAMENTS_SOURCE_ID:
        return len(structured.get("tournaments") or [])
    if source_id == POINTS_SOURCE_ID:
        return sum(len(item["rows"]) for item in structured.get("seasons") or [])
    return len((structured.get("mt_stats") or {}).get("rows") or []) + sum(
        len(item["rows"])
        for key in ("qualifier_stats", "mt_points")
        for item in structured.get(key) or []
    )


def _publishable(source_id: str, structured: dict[str, Any]) -> bool:
    if source_id == TOURNAMENTS_SOURCE_ID:
        return (
            bool(structured.get("tournaments"))
            and "tour_stops" not in structured["missing"]
        )
    if source_id == POINTS_SOURCE_ID:
        return bool(structured.get("seasons"))
    return bool((structured.get("mt_stats") or {}).get("rows"))


def _publish(
    source_id: str, url: str, structured: dict[str, Any], reader: PageReader
) -> dict[str, Any]:
    finished_at = datetime.now(UTC).isoformat()
    acquisition = reader.summary()
    structured["acquisition"] = acquisition
    published = _publishable(source_id, structured)
    previous = load_dataset(source_id) or {}
    if published:
        save_dataset(
            source_id,
            {
                "source_id": source_id,
                "state": SourceState.OK,
                "fetched_at": structured["fetched_at"],
                "http_status": 200,
                "final_url": url,
                "backend": "scrape_do",
                "credits_used": acquisition["scrape_do_credits"],
                "credits_scope": "scrape_do",
                "data": {"structured": structured},
            },
        )
    missing = list(structured.get("missing") or [])
    state = (
        SourceState.OK
        if published and not missing
        else SourceState.PARTIAL
        if published
        else SourceState.FETCH_ERROR
    )
    rows = _rows_total(source_id, structured)
    save_status(
        source_id,
        {
            "source_id": source_id,
            "site": "hsguru",
            "category": "esports",
            "url": url,
            "state": str(state),
            "fetched_at": structured["fetched_at"]
            if published
            else previous.get("fetched_at") or finished_at,
            "http_status": 200 if published else None,
            "backend": "scrape_do",
            "rows_total": rows,
            "detail": (
                f"{source_id}: {rows} rows, {len(missing)} missing, pages "
                f"{acquisition['pages_ok']} ok / {acquisition['pages_failed']} failed, "
                f"scrape.do credits {acquisition['scrape_do_credits']}"
            ),
            "serving_cached_dataset": not published and bool(previous),
            "last_refresh_state": str(state),
            "last_refresh_at": finished_at,
        },
    )
    return {
        "ok": published,
        "state": str(state),
        "published": published,
        "source_id": source_id,
        "fetched_at": structured["fetched_at"],
        "rows": rows,
        "missing": missing[:50],
        "acquisition": acquisition,
    }


def legacy_is_fresh(now: datetime | None = None) -> bool:
    dataset = load_dataset(LEGACY_SOURCE_ID) or {}
    fetched = _parse_time(dataset.get("fetched_at"))
    return bool(fetched and (now or datetime.now(UTC)) - fetched < LEGACY_MAX_AGE)


async def refresh_hsguru_esports_tournaments(
    *, fetcher: PageFetcher | None = None
) -> dict[str, Any]:
    reader = PageReader(TOURNAMENTS_SOURCE_ID, fetcher=fetcher)
    structured = await collect_tournaments(reader)
    return _publish(TOURNAMENTS_SOURCE_ID, TOUR_STOPS_URL, structured, reader)


async def refresh_hsguru_esports_points(
    *, fetcher: PageFetcher | None = None
) -> dict[str, Any]:
    reader = PageReader(POINTS_SOURCE_ID, fetcher=fetcher)
    structured = await collect_points(reader)
    structured.setdefault("fetched_at", datetime.now(UTC).isoformat())
    return _publish(POINTS_SOURCE_ID, POINTS_URL, structured, reader)


async def refresh_hsguru_esports_legacy(
    *, fetcher: PageFetcher | None = None, force: bool = False
) -> dict[str, Any]:
    if not force and legacy_is_fresh():
        return {
            "ok": True,
            "state": str(SourceState.OK),
            "published": False,
            "skipped": True,
            "reason": "archive_fresh",
            "source_id": LEGACY_SOURCE_ID,
        }
    reader = PageReader(LEGACY_SOURCE_ID, fetcher=fetcher)
    structured = await collect_legacy(reader)
    return _publish(LEGACY_SOURCE_ID, LEGACY_MT_STATS_URL, structured, reader)


REFRESHERS = {
    TOURNAMENTS_SOURCE_ID: refresh_hsguru_esports_tournaments,
    POINTS_SOURCE_ID: refresh_hsguru_esports_points,
    LEGACY_SOURCE_ID: refresh_hsguru_esports_legacy,
}
