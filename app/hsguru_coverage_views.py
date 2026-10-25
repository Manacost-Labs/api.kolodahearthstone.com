"""Read views over the HSGuru Brawl and esports datasets.

REST reads the published dataset file and GraphQL reads the same payload from
PostgreSQL ``raw.datasets``; both select through these functions so the two
APIs return the same shape. A missing selection raises ``LookupError`` and an
invalid selector ``ValueError``.
"""

from __future__ import annotations

from typing import Any

from .hsguru_brawl import ANY_OPPONENT, COINS, DECK_RANKS, OPPONENT_CLASSES, RANKS

TOURNAMENT_SUMMARY_FIELDS = (
    "tournament_id",
    "name",
    "kind",
    "start_time",
    "tags",
    "battlefy_url",
    "standings_url",
    "lineups_url",
    "complete",
    "collected_at",
)


def structured_of(dataset: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(dataset, dict):
        return {}
    structured = (dataset.get("data") or {}).get("structured")
    return structured if isinstance(structured, dict) else {}


def brawl_meta(
    structured: dict[str, Any],
    *,
    rank: str = "all",
    coin: str = "any_player",
    opponent_class: str = ANY_OPPONENT,
    min_games: int = 0,
) -> dict[str, Any]:
    if rank not in RANKS:
        raise ValueError(f"rank must be one of {', '.join(RANKS)}")
    if coin not in COINS:
        raise ValueError(f"coin must be one of {', '.join(COINS)}")
    opponent = (
        opponent_class.upper() if opponent_class != ANY_OPPONENT else opponent_class
    )
    if opponent not in {ANY_OPPONENT, *OPPONENT_CLASSES}:
        raise ValueError("opponent_class must be any or an upper-case class key")
    key = f"{rank}|{coin}|{opponent}"
    selected = next(
        (item for item in structured.get("meta") or [] if item.get("key") == key), None
    )
    if selected is None:
        raise LookupError("Requested Brawl slice is not available")
    rows = [
        row
        for row in selected.get("rows") or []
        if int(row.get("games") or 0) >= min_games
    ]
    return {
        "rank": rank,
        "coin": coin,
        "opponent_class": opponent,
        "min_games": min_games,
        "total_games": selected.get("total_games"),
        "source_url": selected.get("source_url"),
        "items": rows,
    }


def brawl_decks(
    structured: dict[str, Any],
    *,
    rank: str = "all",
    class_name: str | None = None,
    archetype: str | None = None,
) -> dict[str, Any]:
    if rank not in DECK_RANKS:
        raise ValueError(f"rank must be one of {', '.join(DECK_RANKS)}")
    selected = next(
        (item for item in structured.get("decks") or [] if item.get("rank") == rank),
        None,
    )
    if selected is None:
        raise LookupError("Requested Brawl deck list is not available")
    rows = list(selected.get("rows") or [])
    if class_name:
        rows = [
            row
            for row in rows
            if str(row.get("class") or "").casefold() == class_name.casefold()
        ]
    if archetype:
        rows = [
            row
            for row in rows
            if str(row.get("archetype") or "").casefold() == archetype.casefold()
        ]
    return {
        "rank": rank,
        "min_games": selected.get("min_games"),
        "source_url": selected.get("source_url"),
        "items": rows,
    }


def tournament_summaries(
    structured: dict[str, Any], *, kind: str | None = None
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for tournament in structured.get("tournaments") or []:
        if kind and tournament.get("kind") != kind:
            continue
        summary = {field: tournament.get(field) for field in TOURNAMENT_SUMMARY_FIELDS}
        stages = tournament.get("stages") or []
        final = stages[-1].get("standings") if stages else []
        summary["players"] = tournament.get("lineups_total") or len(
            tournament.get("lineups") or []
        )
        summary["lineups_complete"] = tournament.get("lineups_complete", True)
        summary["stages"] = [stage.get("name") for stage in stages]
        summary["winner"] = final[0].get("player") if final else None
        summary["top_archetypes"] = (
            (tournament.get("lineup_popularity") or {}).get("archetypes") or []
        )[:5]
        items.append(summary)
    return items


def tournament(structured: dict[str, Any], tournament_id: str) -> dict[str, Any]:
    found = next(
        (
            item
            for item in structured.get("tournaments") or []
            if str(item.get("tournament_id")) == tournament_id
        ),
        None,
    )
    if found is None:
        raise LookupError("Tournament is not available")
    return found


def points_seasons(structured: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "season": item.get("season"),
            "season_label": item.get("season_label"),
            "leaderboard": item.get("leaderboard"),
            "leaderboard_label": item.get("leaderboard_label"),
            "players": len(item.get("rows") or []),
        }
        for item in structured.get("seasons") or []
    ]


def default_points_season(structured: dict[str, Any]) -> str | None:
    """The latest whole-year season (``2026``), which HSGuru shows by default."""
    years = [
        str(item.get("season"))
        for item in structured.get("seasons") or []
        if str(item.get("season") or "").isdigit()
    ]
    return max(years) if years else None


def points(
    structured: dict[str, Any],
    *,
    season: str | None = None,
    leaderboard: str = "STD",
    region: str | None = None,
) -> dict[str, Any]:
    season = season or default_points_season(structured)
    selected = next(
        (
            item
            for item in structured.get("seasons") or []
            if item.get("season") == season and item.get("leaderboard") == leaderboard
        ),
        None,
    )
    if selected is None:
        raise LookupError("Requested points season is not available")
    rows = list(selected.get("rows") or [])
    if region:
        rows = [
            row
            for row in rows
            if str(((row.get("player") or {}).get("region")) or "").upper()
            == region.upper()
        ]
    return {
        "season": selected.get("season"),
        "season_label": selected.get("season_label"),
        "leaderboard": selected.get("leaderboard"),
        "leaderboard_label": selected.get("leaderboard_label"),
        "headers": selected.get("headers"),
        "source_url": selected.get("source_url"),
        "items": rows,
    }


LEGACY_TABLES = ("mt-stats", "qualifier-stats", "mt-points")


def legacy(
    structured: dict[str, Any], table: str, *, selector: str | None = None
) -> dict[str, Any]:
    if table not in LEGACY_TABLES:
        raise ValueError(f"table must be one of {', '.join(LEGACY_TABLES)}")
    if table == "mt-stats":
        selected = structured.get("mt_stats") or None
        options: list[str] = []
    else:
        key, field, default = (
            ("qualifier_stats", "period", "all")
            if table == "qualifier-stats"
            else ("mt_points", "season", None)
        )
        tables = structured.get(key) or []
        options = [str(item.get(field)) for item in tables]
        wanted = selector or default or (options[0] if options else None)
        selected = next(
            (item for item in tables if str(item.get(field)) == wanted),
            None,
        )
    if not selected:
        raise LookupError("Requested legacy table is not available")
    return {
        "table": table,
        "options": options,
        **{name: value for name, value in selected.items() if name != "rows"},
        "items": list(selected.get("rows") or []),
    }
