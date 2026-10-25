"""Small synthetic datasets shaped like the published HSGuru and HSReplay ones."""

from __future__ import annotations

from typing import Any

FETCHED_AT = "2026-10-24T22:00:03+00:00"
PERIODS = [
    "past_day",
    "past_3_days",
    "past_week",
    "patch_36.6.3",
    "black_empire",
    "black_empire_ban",
]

# archetype -> (class in deck catalog, {period: (winrate, popularity, games)})
ARCHETYPES: dict[str, tuple[str | None, dict[str, tuple[float, float, int]]]] = {
    "Zoolock": (
        "Warlock",
        {
            "past_day": (62.8, 14.0, 30_000),
            "past_week": (63.1, 9.5, 120_000),
            "black_empire": (62.9, 7.7, 400_000),
            "black_empire_ban": (63.0, 9.8, 139_517),
        },
    ),
    "C'Thun DH": (
        None,
        {
            "past_day": (58.0, 13.0, 20_000),
            "past_week": (59.8, 8.2, 90_000),
            "black_empire": (59.3, 9.6, 186_384),
            "black_empire_ban": (59.3, 12.0, 171_234),
        },
    ),
    "Beast Druid": (
        "Druid",
        {
            "past_week": (44.6, 1.0, 9_000),
            "black_empire": (44.9, 1.2, 15_000),
            "black_empire_ban": (44.4, 0.9, 6_000),
        },
    ),
    "Tiny Rogue": ("Rogue", {"black_empire_ban": (70.0, 0.0, 150)}),
}

CARDS_EN: dict[str, dict[str, Any]] = {
    "BE_RIT": {
        "id": "BE_RIT",
        "dbfId": 900001,
        "name": "Ritual of Summoning",
        "set": "BE",
        "collectible": True,
        "cardClass": "WARLOCK",
        "rarity": "COMMON",
        "cost": 2,
        "type": "SPELL",
        "text": "Summon two <b>3/5</b>.",
    },
    "BE_BIG": {
        "id": "BE_BIG",
        "dbfId": 900002,
        "name": "Late Game Bomb",
        "set": "BE",
        "collectible": True,
        "cardClass": "NEUTRAL",
        "rarity": "LEGENDARY",
        "cost": 10,
        "type": "MINION",
    },
    "BE_WEAK": {
        "id": "BE_WEAK",
        "dbfId": 900003,
        "name": "Animator of Iron",
        "set": "BE",
        "collectible": True,
        "cardClass": "WARRIOR",
        "rarity": "EPIC",
        "cost": 3,
        "type": "MINION",
    },
    "OLD_1": {
        "id": "OLD_1",
        "dbfId": 100,
        "name": "Old Card",
        "set": "TIME_TRAVEL",
        "collectible": True,
        "cardClass": "WARLOCK",
        "rarity": "RARE",
        "cost": 5,
        "type": "MINION",
    },
}


def meta_matrix(format_name: str = "standard") -> dict[str, Any]:
    slices: dict[str, dict[str, Any]] = {}
    for name, (_class, cells) in ARCHETYPES.items():
        for period, (winrate, popularity, games) in cells.items():
            for rank, factor in (("all", 1.0), ("legend", 0.3)):
                key = f"{format_name}|{rank}|{period}|any_player"
                item = slices.setdefault(
                    key,
                    {
                        "key": key,
                        "format": format_name,
                        "rank": rank,
                        "period": period,
                        "coin": "any_player",
                        "rows": [],
                    },
                )
                item["rows"].append(
                    {
                        "archetype": name,
                        "winrate": winrate - (4.0 if rank == "legend" else 0.0),
                        "popularity": popularity,
                        "games": int(games * factor),
                    }
                )
    catalog = [
        {
            "format": format_name,
            "archetype": name,
            "decks": [{"class": deck_class}] if deck_class else [],
        }
        for name, (deck_class, _cells) in ARCHETYPES.items()
    ]
    return {
        "source_id": "hsguru_meta_matrix",
        "fetched_at": FETCHED_AT,
        "data": {
            "structured": {
                "type": "hsguru_meta_matrix",
                "dimensions": {"periods": ["past_3_hours", *PERIODS]},
                "slices": list(slices.values()),
                "patch_discovery": {
                    "selected_patch_period": "patch_36.6.3",
                    "selected_period": "black_empire",
                },
                "current_catalog": {
                    "criteria": {"period": "black_empire"},
                    "archetypes": catalog,
                },
            }
        },
    }


def card_row(
    card_id: str,
    name_ru: str,
    card_class: str,
    *,
    deck_wr: float,
    pop: float,
    played: float,
    plays: int,
) -> dict[str, Any]:
    return {
        "id": card_id,
        "dbfId": CARDS_EN.get(card_id, {}).get("dbfId", 1),
        "name": name_ru,
        "cardClass": card_class,
        "cost": CARDS_EN.get(card_id, {}).get("cost", 1),
        "rarity": CARDS_EN.get(card_id, {}).get("rarity", "COMMON"),
        "deck_winrate": f"{deck_wr:.2f}%",
        "deck_popularity": f"{pop:.2f}%",
        "winrate_when_played": f"{played:.2f}%",
        "winrate_when_drawn": f"{played - 1:.2f}%",
        "times_played": plays,
        "keep_percentage": "50.00%",
    }


def card_dataset(period: str, rank: str = "legend") -> dict[str, Any]:
    bump = {"1d": 8.0, "3d": 1.0, "7d": 0.0, "14d": -1.0, "patch": 0.0}[period]
    rows = [
        card_row(
            "BE_RIT",
            "Ритуал призыва",
            "WARLOCK",
            deck_wr=55.9,
            pop=18.8 + bump,
            played=56.0 + bump / 4,
            plays=90_000,
        ),
        # High "winrate when played" but losing decks: must not become a nerf candidate.
        card_row(
            "BE_BIG",
            "Поздняя бомба",
            "NEUTRAL",
            deck_wr=44.0,
            pop=6.0,
            played=76.0,
            plays=20_000,
        ),
        card_row(
            "BE_WEAK",
            "Оживитель железа",
            "WARRIOR",
            deck_wr=36.0,
            pop=1.1,
            played=38.0,
            plays=3_500,
        ),
        card_row(
            "OLD_1",
            "Старая карта",
            "WARLOCK",
            deck_wr=57.9,
            pop=8.3,
            played=58.0,
            plays=30_000,
        ),
    ]
    return {
        "source_id": f"hsreplay_cards_{rank}_{period}",
        "fetched_at": "2026-10-25T06:01:32+00:00",
        "data": {"structured": {"type": "card_stats", "cards": rows}},
    }


def loader(source_id: str) -> dict[str, Any] | None:
    if source_id == "hsguru_meta_matrix":
        return meta_matrix("standard")
    for rank in ("legend", "diamond_4_1"):
        for period in ("1d", "3d", "7d", "14d", "patch"):
            if source_id == f"hsreplay_cards_{rank}_{period}":
                return card_dataset(period, rank)
    return None
