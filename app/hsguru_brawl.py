"""HSGuru Tavern Brawl statistics: archetype meta and the most played decks.

HSGuru reports the current Brawl as ``format=-1&period=brawl``: one archetype
table per rank, coin and opponent class (same columns as the ladder meta) and a
deck catalog. Only the ``brawl`` period holds Brawl games; the rolling windows
of the same format are empty. Brawl matchups belong to the matchup slices
pipeline and are not read here.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from .hsguru_decks import parse_hsguru_decks_html
from .hsguru_meta_matrix import HSGuruMetaSchemaError, parse_meta_table
from .hsguru_pages import HSGURU, PageFetcher, PageReader, is_paywalled
from .source_state import SourceState
from .storage import load_dataset, save_dataset, save_status

SOURCE_ID = "hsguru_brawl"
FORMAT_ID = -1
PERIOD = "brawl"
RANKS = (
    "all",
    "top_legend",
    "top_5k",
    "legend",
    "diamond_4to1",
    "diamond_to_legend",
    "bronze_plat",
)
COINS = {"any_player": None, "on_coin": "yes", "going_first": "no"}
OPPONENT_CLASSES = (
    "DEATHKNIGHT",
    "DEMONHUNTER",
    "DRUID",
    "HUNTER",
    "MAGE",
    "PALADIN",
    "PRIEST",
    "ROGUE",
    "SHAMAN",
    "WARLOCK",
    "WARRIOR",
)
ANY_OPPONENT = "any"
# HSGuru's selector starts at 100 games; the query accepts less and a Brawl
# rarely has more than a few thousand games per archetype.
META_MIN_GAMES = 10
DECK_RANKS = ("all", "legend", "diamond_to_legend")
DECK_MIN_GAMES = 50
DECK_LIMIT = 200
BASE_KEY = f"all|any_player|{ANY_OPPONENT}"
_DECKS_MARKER = "deck_stats_viewport"


def meta_key(rank: str, coin: str, opponent_class: str) -> str:
    return f"{rank}|{coin}|{opponent_class}"


def meta_url(rank: str, coin: str, opponent_class: str) -> str:
    query: dict[str, Any] = {
        "format": FORMAT_ID,
        "period": PERIOD,
        "rank": rank,
        "min_games": META_MIN_GAMES,
    }
    if COINS[coin]:
        query["player_has_coin"] = COINS[coin]
    if opponent_class != ANY_OPPONENT:
        query["opponent_class"] = opponent_class
    return f"{HSGURU}/meta?{urlencode(query)}"


def deck_url(rank: str) -> str:
    query = {
        "format": FORMAT_ID,
        "period": PERIOD,
        "rank": rank,
        "min_games": DECK_MIN_GAMES,
        "limit": DECK_LIMIT,
    }
    return f"{HSGURU}/decks?{urlencode(query)}"


def meta_slices() -> list[tuple[str, str, str]]:
    """Every rank by coin, plus each opponent class for all ranks."""
    slices = [(rank, coin, ANY_OPPONENT) for rank in RANKS for coin in COINS]
    slices += [("all", "any_player", opponent) for opponent in OPPONENT_CLASSES]
    return slices


def meta_page_is_valid(page_html: str) -> bool:
    if is_paywalled(page_html):
        return False
    try:
        parse_meta_table(page_html)
    except (HSGuruMetaSchemaError, ValueError):
        return False
    return True


def deck_page_is_valid(page_html: str) -> bool:
    return _DECKS_MARKER in (page_html or "") and not is_paywalled(page_html)


def parse_meta_page(page_html: str) -> list[dict[str, Any]]:
    rows = parse_meta_table(page_html).rows
    return sorted(rows, key=lambda row: int(row.get("games") or 0), reverse=True)


def parse_deck_page(
    page_html: str, *, rank: str, fetched_at: str
) -> list[dict[str, Any]]:
    rows = parse_hsguru_decks_html(
        page_html, archetype="", format_name="brawl", fetched_at=fetched_at
    )
    for row in rows:
        row["source_id"] = SOURCE_ID
        row["format"] = "Brawl"
        row["sample_rank"] = rank
        row["sample_period"] = PERIOD
    return rows


async def collect(reader: PageReader) -> dict[str, Any]:
    fetched_at = datetime.now(UTC).isoformat()
    slices = meta_slices()
    meta_urls = [meta_url(*spec) for spec in slices]
    deck_urls = [deck_url(rank) for rank in DECK_RANKS]
    meta_pages, deck_pages = await asyncio.gather(
        reader.read_many(meta_urls, meta_page_is_valid),
        reader.read_many(deck_urls, deck_page_is_valid),
    )
    meta: list[dict[str, Any]] = []
    missing: list[str] = []
    for (rank, coin, opponent), url in zip(slices, meta_urls, strict=True):
        key = meta_key(rank, coin, opponent)
        page = meta_pages.get(url)
        if page is None:
            missing.append(key)
            continue
        rows = parse_meta_page(page.html)
        meta.append(
            {
                "key": key,
                "rank": rank,
                "coin": coin,
                "opponent_class": opponent,
                "min_games": META_MIN_GAMES,
                "source_url": url,
                "total_games": sum(int(row.get("games") or 0) for row in rows),
                "rows": rows,
            }
        )
    decks: list[dict[str, Any]] = []
    for rank, url in zip(DECK_RANKS, deck_urls, strict=True):
        page = deck_pages.get(url)
        if page is None:
            missing.append(f"decks|{rank}")
            continue
        decks.append(
            {
                "rank": rank,
                "min_games": DECK_MIN_GAMES,
                "source_url": url,
                "rows": parse_deck_page(page.html, rank=rank, fetched_at=fetched_at),
            }
        )
    return {
        "type": SOURCE_ID,
        "fetched_at": fetched_at,
        "format_id": FORMAT_ID,
        "period": PERIOD,
        "dimensions": {
            "ranks": list(RANKS),
            "coins": list(COINS),
            "opponent_classes": [ANY_OPPONENT, *OPPONENT_CLASSES],
            "deck_ranks": list(DECK_RANKS),
        },
        "meta": meta,
        "decks": decks,
        "missing_slices": missing,
    }


def _base_slice(structured: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (item for item in structured.get("meta") or [] if item.get("key") == BASE_KEY),
        None,
    )


async def refresh_hsguru_brawl(*, fetcher: PageFetcher | None = None) -> dict[str, Any]:
    reader = PageReader(SOURCE_ID, fetcher=fetcher)
    structured = await collect(reader)
    finished_at = datetime.now(UTC).isoformat()
    base = _base_slice(structured)
    acquisition = reader.summary()
    structured["acquisition"] = acquisition
    # The all-ranks table is what consumers read first; without it the run is
    # not a usable Brawl snapshot and the last one keeps being served.
    published = base is not None and bool(base.get("rows"))
    previous = load_dataset(SOURCE_ID) or {}
    if published:
        save_dataset(
            SOURCE_ID,
            {
                "source_id": SOURCE_ID,
                "state": SourceState.OK,
                "fetched_at": structured["fetched_at"],
                "http_status": 200,
                "final_url": meta_url("all", "any_player", ANY_OPPONENT),
                "backend": "scrape_do",
                "credits_used": acquisition["scrape_do_credits"],
                "credits_scope": "scrape_do",
                "data": {"structured": structured},
            },
        )
    state = (
        SourceState.OK
        if published and not structured["missing_slices"]
        else SourceState.PARTIAL
        if published
        else SourceState.FETCH_ERROR
    )
    archetypes = len(base.get("rows") or []) if base else 0
    decks = sum(len(item["rows"]) for item in structured["decks"])
    save_status(
        SOURCE_ID,
        {
            "source_id": SOURCE_ID,
            "site": "hsguru",
            "category": "brawl",
            "url": meta_url("all", "any_player", ANY_OPPONENT),
            "state": str(state),
            "fetched_at": structured["fetched_at"]
            if published
            else previous.get("fetched_at") or finished_at,
            "http_status": 200 if published else None,
            "backend": "scrape_do",
            "rows_total": archetypes,
            "detail": (
                f"HSGuru Brawl: {len(structured['meta'])} meta slices, {archetypes} "
                f"archetypes, {decks} decks, {len(structured['missing_slices'])} missing, "
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
        "source_id": SOURCE_ID,
        "fetched_at": structured["fetched_at"],
        "meta_slices": len(structured["meta"]),
        "archetypes": archetypes,
        "decks": decks,
        "missing_slices": structured["missing_slices"],
        "acquisition": acquisition,
    }
