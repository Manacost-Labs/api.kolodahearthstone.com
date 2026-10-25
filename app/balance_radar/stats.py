"""Statistical nerf/buff signals from datasets hs-data-api already publishes.

Archetypes and classes come from the HSGuru meta matrix (periods from three
hours to the expansion, including HSGuru's ``*_ban`` window: games since the
latest ban/balance update). Cards come from the HSReplay card-period datasets
(1, 3, 7 and 14 days and the current patch, Legend and Diamond 4-1).

Scores here only rank the candidate pools handed to the analysis; the model
makes the final call and every number it sees is copied from the datasets.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..storage import load_dataset

logger = logging.getLogger(__name__)

META_MATRIX_SOURCE_ID = "hsguru_meta_matrix"
ARCHETYPE_RANKS = ("all", "legend")
ROLLING_PERIODS = ("past_day", "past_3_days", "past_week")
CARD_RANKS = ("legend", "diamond_4_1")
CARD_PERIODS = ("1d", "3d", "7d", "14d", "patch")
MIN_ARCHETYPE_GAMES = 2_000
MIN_CARD_PLAYS = 1_000
ARCHETYPE_POOL = 40
CARD_NERF_POOL = 30
CARD_BUFF_POOL = 25
CARD_POOL_PER_CLASS = 8

CLASS_NAMES_RU = {
    "DEATHKNIGHT": "Рыцарь смерти",
    "DEMONHUNTER": "Охотник на демонов",
    "DRUID": "Друид",
    "HUNTER": "Охотник",
    "MAGE": "Маг",
    "PALADIN": "Паладин",
    "PRIEST": "Жрец",
    "ROGUE": "Разбойник",
    "SHAMAN": "Шаман",
    "WARLOCK": "Чернокнижник",
    "WARRIOR": "Воин",
    "NEUTRAL": "Нейтральная",
}
CLASS_NAMES_EN = {
    "DEATHKNIGHT": "Death Knight",
    "DEMONHUNTER": "Demon Hunter",
    "DRUID": "Druid",
    "HUNTER": "Hunter",
    "MAGE": "Mage",
    "PALADIN": "Paladin",
    "PRIEST": "Priest",
    "ROGUE": "Rogue",
    "SHAMAN": "Shaman",
    "WARLOCK": "Warlock",
    "WARRIOR": "Warrior",
}
# Archetype-name suffixes HSGuru uses, longest first.
_CLASS_SUFFIXES = (
    ("demon hunter", "DEMONHUNTER"),
    ("death knight", "DEATHKNIGHT"),
    ("dh", "DEMONHUNTER"),
    ("dk", "DEATHKNIGHT"),
    ("druid", "DRUID"),
    ("hunter", "HUNTER"),
    ("mage", "MAGE"),
    ("paladin", "PALADIN"),
    ("pally", "PALADIN"),
    ("priest", "PRIEST"),
    ("rogue", "ROGUE"),
    ("shaman", "SHAMAN"),
    ("warlock", "WARLOCK"),
    ("lock", "WARLOCK"),
    ("warrior", "WARRIOR"),
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        cleaned = value.strip().rstrip("%").replace(",", ".")
        try:
            parsed = float(cleaned)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _delta(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else round(left - right, 2)


def normalize_class(value: Any) -> str | None:
    text = re.sub(r"[^a-z]", "", str(value or "").casefold())
    for key in CLASS_NAMES_RU:
        if key.casefold() == text:
            return key
    return None


def class_from_archetype(name: str) -> str | None:
    lowered = name.casefold().strip()
    for suffix, class_id in _CLASS_SUFFIXES:
        if lowered.endswith(suffix):
            return class_id
    return None


def archetype_aliases(name: str, class_id: str | None) -> list[str]:
    aliases = {name}
    lowered = name.casefold()
    for short, full in ((" dh", " Demon Hunter"), (" dk", " Death Knight")):
        if lowered.endswith(short):
            aliases.add(name[: -len(short)] + full)
    if (
        lowered.endswith("lock")
        and not lowered.endswith(" warlock")
        and " " not in name
    ):
        aliases.add(name[:-4] + " Warlock")
    if lowered.endswith(" warlock"):
        aliases.add(name[: -len(" warlock")].replace(" ", "") + "lock")
    return sorted(aliases)


@dataclass
class StatsBundle:
    format: str
    archetypes: list[dict[str, Any]] = field(default_factory=list)
    classes: list[dict[str, Any]] = field(default_factory=list)
    card_nerf_pool: list[dict[str, Any]] = field(default_factory=list)
    card_buff_pool: list[dict[str, Any]] = field(default_factory=list)
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    periods: dict[str, Any] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.entities


# -- HSGuru archetypes and classes ----------------------------------------


def _meta_periods(structured: Mapping[str, Any]) -> dict[str, Any]:
    available = [
        str(p) for p in (structured.get("dimensions") or {}).get("periods") or []
    ]
    current = str(
        ((structured.get("current_catalog") or {}).get("criteria") or {}).get("period")
        or ""
    )
    patch = str(
        (structured.get("patch_discovery") or {}).get("selected_patch_period") or ""
    )
    if patch not in available:
        patch = next((p for p in reversed(available) if p.startswith("patch_")), "")
    ban = next((p for p in available if p.endswith("_ban")), "")
    if current not in available:
        current = next(
            (
                p
                for p in available
                if not p.startswith(("past_", "patch_")) and not p.endswith("_ban")
            ),
            "",
        )
    return {
        "available": available,
        "current": current or None,
        "patch": patch or None,
        "ban": ban or None,
    }


def _class_map(structured: Mapping[str, Any], format_name: str) -> dict[str, str]:
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    for row in (structured.get("current_catalog") or {}).get("archetypes") or []:
        if not isinstance(row, dict) or row.get("format") != format_name:
            continue
        for deck in row.get("decks") or []:
            class_id = (
                normalize_class(deck.get("class")) if isinstance(deck, dict) else None
            )
            if class_id:
                votes[str(row.get("archetype"))][class_id] += 1
    return {name: counter.most_common(1)[0][0] for name, counter in votes.items()}


def _metric_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "winrate": _number(row.get("winrate")),
        "popularity": _number(row.get("popularity")),
        "games": int(_number(row.get("games")) or 0),
    }


def archetype_signals(
    dataset: Mapping[str, Any] | None, format_name: str
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any] | None
]:
    """Archetype and class records plus the period description and source."""
    if not dataset:
        return [], [], {}, None
    structured = (dataset.get("data") or {}).get("structured") or {}
    periods = _meta_periods(structured)
    wanted = [
        p
        for p in (
            *ROLLING_PERIODS,
            periods["patch"],
            periods["current"],
            periods["ban"],
        )
        if p
    ]
    slices = {
        str(item.get("key")): item
        for item in structured.get("slices") or []
        if isinstance(item, dict)
    }
    table: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for rank in ARCHETYPE_RANKS:
        for period in wanted:
            item = slices.get(f"{format_name}|{rank}|{period}|any_player")
            for row in (item or {}).get("rows") or []:
                if isinstance(row, dict) and row.get("archetype"):
                    table[str(row["archetype"])][(rank, period)] = _metric_row(row)

    def has_rows(period: str | None) -> bool:
        item = slices.get(f"{format_name}|all|{period}|any_player") if period else None
        return bool(item and item.get("rows"))

    primary = next(
        (
            p
            for p in (periods["ban"], periods["current"], "past_week")
            if p and has_rows(p)
        ),
        None,
    )
    periods = {**periods, "primary": primary, "used": wanted}
    if primary is None:
        return [], [], periods, None
    classes = _class_map(structured, format_name)
    records: list[dict[str, Any]] = []
    for name, cells in table.items():
        main = cells.get(("all", primary))
        if not main or main["games"] < MIN_ARCHETYPE_GAMES or main["winrate"] is None:
            continue
        class_id = classes.get(name) or class_from_archetype(name)
        legend = cells.get(("legend", primary)) or {}
        day = cells.get(("all", "past_day")) or {}
        week = cells.get(("all", "past_week")) or {}
        current = cells.get(("all", periods["current"] or "")) or {}
        winrate = main["winrate"]
        popularity = main["popularity"] or 0.0
        nerf = max(0.0, winrate - 51.5) * math.sqrt(max(popularity, 0.2))
        if (legend.get("winrate") or 0) >= 53 and legend.get("games", 0) >= 1000:
            nerf += 1.0
        buff = max(0.0, 49.0 - winrate) * (1.5 if popularity < 2 else 1.0)
        ref = f"archetype:{format_name}:{name}"
        records.append(
            {
                "ref": ref,
                "kind": "archetype",
                "id": name,
                "name": name,
                "class": class_id,
                "className": CLASS_NAMES_RU.get(class_id or ""),
                "stats": {
                    "source": "hsguru",
                    "rank": "all",
                    "period": primary,
                    "winrate": winrate,
                    "popularity": popularity,
                    "games": main["games"],
                    "legendWinrate": legend.get("winrate"),
                    "legendPopularity": legend.get("popularity"),
                    "legendGames": legend.get("games"),
                    "trend": {
                        "winrateDelta": _delta(day.get("winrate"), week.get("winrate")),
                        "popularityDelta": _delta(
                            day.get("popularity"), week.get("popularity")
                        ),
                        "basis": "past_day − past_week, rank all",
                        "banWinrateDelta": _delta(
                            main["winrate"], current.get("winrate")
                        )
                        if primary == periods["ban"]
                        else None,
                        "banPopularityDelta": _delta(
                            main["popularity"], current.get("popularity")
                        )
                        if primary == periods["ban"]
                        else None,
                    },
                    "periods": [
                        {"rank": rank, "period": period, **cells[(rank, period)]}
                        for rank in ARCHETYPE_RANKS
                        for period in wanted
                        if (rank, period) in cells
                    ],
                },
                "scores": {"nerf": round(nerf, 3), "buff": round(buff, 3)},
                "aliases": archetype_aliases(name, class_id),
            }
        )
    records.sort(key=lambda record: record["stats"]["games"], reverse=True)
    class_records = _class_signals(records, format_name, primary)
    source = {
        "id": META_MATRIX_SOURCE_ID,
        "kind": "stats",
        "fetchedAt": dataset.get("fetched_at") or structured.get("fetched_at"),
        "items": len(records),
    }
    return records, class_records, periods, source


def _class_signals(
    archetypes: list[dict[str, Any]], format_name: str, period: str
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in archetypes:
        if record["class"]:
            grouped[record["class"]].append(record)
    result: list[dict[str, Any]] = []
    for class_id, members in grouped.items():
        games = sum(m["stats"]["games"] for m in members)
        if not games:
            continue
        winrate = (
            sum(m["stats"]["winrate"] * m["stats"]["games"] for m in members) / games
        )
        popularity = sum(m["stats"]["popularity"] or 0 for m in members)
        nerf = max(0.0, winrate - 52.0) * math.sqrt(max(popularity, 0.5))
        buff = max(0.0, 48.5 - winrate) * (1.5 if popularity < 5 else 1.0)
        result.append(
            {
                "ref": f"class:{format_name}:{class_id}",
                "kind": "class",
                "id": class_id,
                "name": CLASS_NAMES_RU.get(class_id, class_id),
                "nameEn": CLASS_NAMES_EN.get(class_id),
                "class": class_id,
                "className": CLASS_NAMES_RU.get(class_id),
                "stats": {
                    "source": "hsguru",
                    "rank": "all",
                    "period": period,
                    "winrate": round(winrate, 2),
                    "popularity": round(popularity, 2),
                    "games": games,
                    "topArchetypes": [m["name"] for m in members[:5]],
                    "trend": None,
                    "periods": [],
                },
                "scores": {"nerf": round(nerf, 3), "buff": round(buff, 3)},
                "aliases": [CLASS_NAMES_EN.get(class_id, class_id)],
            }
        )
    result.sort(key=lambda record: record["stats"]["games"], reverse=True)
    return result


# -- HSReplay cards ---------------------------------------------------------


def card_source_id(format_name: str, rank: str, period: str) -> str:
    return f"hsreplay_cards{'_wild' if format_name == 'wild' else ''}_{rank}_{period}"


def _card_metrics(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "deckWinrate": _number(row.get("deck_winrate")),
        "deckPopularity": _number(row.get("deck_popularity")),
        "playedWinrate": _number(row.get("winrate_when_played")),
        "drawnWinrate": _number(row.get("winrate_when_drawn")),
        "timesPlayed": int(_number(row.get("times_played")) or 0),
        "keepPercentage": _number(row.get("keep_percentage")),
    }


_NON_EXPANSION_SETS = frozenset(
    {
        None,
        "CORE",
        "CORE_HIDDEN",
        "EVENT",
        "LEGACY",
        "VANILLA",
        "EXPERT1",
        "HERO_SKINS",
        "PLACEHOLDER_202204",
    }
)


def latest_card_set(cards_en: Mapping[str, Mapping[str, Any]]) -> str | None:
    collectible = sorted(
        (
            card
            for card in cards_en.values()
            if card.get("collectible")
            and card.get("set") not in _NON_EXPANSION_SETS
            and card.get("type") != "HERO"
            and isinstance(card.get("dbfId"), int)
        ),
        key=lambda card: card["dbfId"],
        reverse=True,
    )[:120]
    counts = Counter(str(card["set"]) for card in collectible)
    return counts.most_common(1)[0][0] if counts else None


def card_signals(
    format_name: str,
    *,
    loader: Callable[[str], Mapping[str, Any] | None] = load_dataset,
    cards_en: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (nerf pool, buff pool, sources) of card records."""
    cards_en = cards_en or {}
    new_set = latest_card_set(cards_en)
    table: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    info: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    for rank in CARD_RANKS:
        for period in CARD_PERIODS:
            source_id = card_source_id(format_name, rank, period)
            dataset = loader(source_id)
            structured = ((dataset or {}).get("data") or {}).get("structured") or {}
            rows = [
                row
                for row in structured.get("cards") or []
                if isinstance(row, dict) and row.get("id")
            ]
            if not rows:
                continue
            sources.append(
                {
                    "id": source_id,
                    "kind": "stats",
                    "fetchedAt": (dataset or {}).get("fetched_at"),
                    "items": len(rows),
                }
            )
            for row in rows:
                card_id = str(row["id"])
                table[card_id][(rank, period)] = _card_metrics(row)
                info.setdefault(card_id, row)
    nerf_pool: list[dict[str, Any]] = []
    buff_pool: list[dict[str, Any]] = []
    for card_id, cells in table.items():
        primary_key = next(
            (
                key
                for key in (
                    ("legend", "7d"),
                    ("diamond_4_1", "7d"),
                    ("legend", "patch"),
                )
                if key in cells
            ),
            None,
        )
        if primary_key is None:
            continue
        main = cells[primary_key]
        row = info[card_id]
        english = cards_en.get(card_id) or {}
        if english and not english.get("collectible", True):
            continue
        class_id = (
            normalize_class(row.get("cardClass") or english.get("cardClass"))
            or "NEUTRAL"
        )
        rank = primary_key[0]
        day = cells.get((rank, "1d")) or {}
        three = cells.get((rank, "3d")) or {}
        fortnight = cells.get((rank, "14d")) or {}
        record = {
            "ref": f"card:{card_id}",
            "kind": "card",
            "id": card_id,
            "dbfId": row.get("dbfId")
            if isinstance(row.get("dbfId"), int)
            else english.get("dbfId"),
            "name": str(row.get("name") or english.get("name") or card_id),
            "nameEn": english.get("name"),
            "class": class_id,
            "className": CLASS_NAMES_RU.get(class_id),
            "cost": row.get("cost")
            if isinstance(row.get("cost"), int)
            else english.get("cost"),
            "rarity": row.get("rarity") or english.get("rarity"),
            "set": english.get("set"),
            "newSet": bool(new_set and english.get("set") == new_set),
            "text": english.get("text"),
            "imageUrl": f"https://art.hearthstonejson.com/v1/256x/{card_id}.png",
            "stats": {
                "source": "hsreplay",
                "rank": rank,
                "period": primary_key[1],
                "winrate": main["deckWinrate"],
                "popularity": main["deckPopularity"],
                "playedWinrate": main["playedWinrate"],
                "drawnWinrate": main["drawnWinrate"],
                "timesPlayed": main["timesPlayed"],
                "keepPercentage": main["keepPercentage"],
                "trend": {
                    "popularityDelta": _delta(
                        day.get("deckPopularity"), main["deckPopularity"]
                    ),
                    "playedWinrateDelta": _delta(
                        three.get("playedWinrate"), fortnight.get("playedWinrate")
                    ),
                    "basis": f"popularity 1d − 7d, played winrate 3d − 14d, rank {rank}",
                },
                "periods": [
                    {"rank": r, "period": p, **cells[(r, p)]}
                    for r in CARD_RANKS
                    for p in CARD_PERIODS
                    if (r, p) in cells
                ],
            },
            "aliases": [
                name for name in (english.get("name"), row.get("name")) if name
            ],
        }
        popularity = main["deckPopularity"] or 0.0
        deck_winrate = main["deckWinrate"]
        buff_cells = cells.get(("diamond_4_1", "7d")) or main
        buff_winrate = buff_cells["deckWinrate"]
        # "Winrate when played" is survivorship-biased (expensive cards are
        # played in games already being won), so pools rank on deck winrate.
        if deck_winrate is not None and popularity >= 4 and deck_winrate >= 52:
            record["scores"] = {
                "nerf": round((deck_winrate - 50) * math.sqrt(popularity), 3),
                "buff": 0.0,
            }
            nerf_pool.append(record)
        elif (
            buff_winrate is not None
            and buff_cells["timesPlayed"] >= MIN_CARD_PLAYS
            and buff_winrate <= 48.5
            and (buff_cells["deckPopularity"] or 0) <= 5
        ):
            weight = (1.5 if record["newSet"] else 1.0) * (
                1.3 if record["rarity"] == "LEGENDARY" else 1.0
            )
            record["scores"] = {
                "nerf": 0.0,
                "buff": round((50 - buff_winrate) * weight, 3),
            }
            buff_pool.append(record)
        elif (
            record["newSet"]
            and record["rarity"] == "LEGENDARY"
            and popularity < 1
            and main["timesPlayed"] >= 100
        ):
            # A new legendary nobody plays is a buff candidate even when the
            # few decks running it do fine.
            record["scores"] = {"nerf": 0.0, "buff": round(1.0 - popularity, 3)}
            buff_pool.append(record)
    return (
        _diverse(nerf_pool, "nerf", CARD_NERF_POOL),
        _diverse(buff_pool, "buff", CARD_BUFF_POOL),
        sources,
    )


def _diverse(
    pool: list[dict[str, Any]], score: str, limit: int
) -> list[dict[str, Any]]:
    """Best-scored cards, at most ``CARD_POOL_PER_CLASS`` from one class.

    Every card of one dominant deck shares its deck winrate; without the cap
    that single deck fills the whole pool.
    """
    per_class: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []
    for record in sorted(pool, key=lambda r: r["scores"][score], reverse=True):
        if per_class[record["class"]] >= CARD_POOL_PER_CLASS:
            continue
        per_class[record["class"]] += 1
        selected.append(record)
        if len(selected) >= limit:
            break
    return selected


def _english_cards() -> dict[str, dict[str, Any]]:
    try:
        from ..cards_index import cards_by_id

        return cards_by_id("enUS")
    except Exception as exc:  # noqa: BLE001 - English names only improve matching
        logger.warning("HearthstoneJSON enUS index unavailable: %s", type(exc).__name__)
        return {}


def build_stats(
    format_name: str,
    *,
    loader: Callable[[str], Mapping[str, Any] | None] = load_dataset,
    cards_en: Mapping[str, Mapping[str, Any]] | None = None,
) -> StatsBundle:
    bundle = StatsBundle(format=format_name)
    archetypes, classes, periods, meta_source = archetype_signals(
        loader(META_MATRIX_SOURCE_ID), format_name
    )
    nerf_cards, buff_cards, card_sources = card_signals(
        format_name,
        loader=loader,
        cards_en=cards_en if cards_en is not None else _english_cards(),
    )
    bundle.periods = periods
    if meta_source:
        bundle.sources.append(meta_source)
    bundle.sources.extend(card_sources)
    by_nerf = sorted(archetypes, key=lambda r: r["scores"]["nerf"], reverse=True)[:12]
    by_buff = sorted(archetypes, key=lambda r: r["scores"]["buff"], reverse=True)[:12]
    selected = {r["ref"]: r for r in (*archetypes[:ARCHETYPE_POOL], *by_nerf, *by_buff)}
    bundle.archetypes = sorted(
        selected.values(), key=lambda r: r["stats"]["games"], reverse=True
    )
    bundle.classes = classes
    bundle.card_nerf_pool = nerf_cards
    bundle.card_buff_pool = buff_cards
    for record in (*bundle.archetypes, *classes, *nerf_cards, *buff_cards):
        bundle.entities[record["ref"]] = record
        bundle.aliases[record["ref"]] = list(record.get("aliases") or [])
    return bundle
