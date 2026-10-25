"""OpenAPI response examples of the HSGuru Brawl and esports endpoints.

Trimmed from the live run of 2026-10-25; the same examples are in docs/API.md.
"""

from __future__ import annotations

from typing import Any

_PLAYER = {
    "name": "Gaby59",
    "battletag": "Gaby59",
    "region": "EU",
    "country": "FR",
    "profile_url": "https://www.hsguru.com/player-profile/Gaby59",
}
_TOURNAMENT_ID = "6a6c88f821e3c30013db3d46"
_TOURNAMENT_URL = f"https://www.hsguru.com/battlefy/tournament/{_TOURNAMENT_ID}"


def _meta(source_id: str, count: int, **paging: int) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "fetched_at": "2026-10-25T16:28:34.520592+00:00",
        "stale": False,
        "count": count,
        **paging,
    }


def response(
    data: Any, source_id: str, count: int, **paging: int
) -> dict[int | str, Any]:
    """A FastAPI ``responses`` mapping with one JSON example of the envelope."""
    return {
        200: {
            "content": {
                "application/json": {
                    "example": {"data": data, "meta": _meta(source_id, count, **paging)}
                }
            }
        }
    }


BRAWL_META = {
    "rank": "all",
    "coin": "any_player",
    "opponent_class": "any",
    "min_games": 10,
    "total_games": 11740,
    "source_url": "https://www.hsguru.com/meta?format=-1&period=brawl&rank=all&min_games=10",
    "items": [
        {
            "archetype": "C'Thun Priest",
            "winrate": 72.6,
            "popularity": 21.0,
            "games": 2525,
            "turns": 9.2,
            "duration_minutes": 8.5,
            "climbing_speed": 3.21,
        }
    ],
}

BRAWL_DECKS = {
    "rank": "all",
    "min_games": 50,
    "source_url": "https://www.hsguru.com/decks?format=-1&period=brawl&rank=all&min_games=50&limit=200",
    "items": [
        {
            "source_id": "hsguru_brawl",
            "title": "Shaman",
            "archetype": "Shaman",
            "class": "Shaman",
            "format": "Brawl",
            "deck_code": "AAEBAaoIAA/X6Qf87gfF8Afr8Af79QeT9geW+geJ+we3/ge//wfA/wf1/weAgQjegQidgggAAA==",
            "win_rate": 60.5,
            "games": 816,
            "url": "https://www.hsguru.com/deck/42479368",
            "sample_rank": "all",
            "sample_period": "brawl",
        }
    ],
}

UPCOMING = {
    "items": [
        {
            "tournament_id": "6acf43976090a5001347461c",
            "name": "Aventa Cup #3 - 50€ Cashprize",
            "start_time": "2026-10-23T17:00:17+00:00",
            "tags": ["Open", "Bo3", "Europe"],
            "url": "https://www.hsguru.com/battlefy/tournament/6acf43976090a5001347461c",
        }
    ]
}

TOURNAMENTS = [
    {
        "tournament_id": _TOURNAMENT_ID,
        "name": "2026 Hearthstone Masters Tour Summer Championship",
        "kind": "tour_stop",
        "start_time": "2026-07-31T14:00:00+00:00",
        "standings_url": f"{_TOURNAMENT_URL}?view_mode=standings",
        "lineups_url": f"{_TOURNAMENT_URL}/lineups",
        "complete": True,
        "players": 16,
        "lineups_complete": True,
        "stages": ["Group A", "Group B", "Group C", "Group D", "Top 8"],
        "winner": {"name": "demoshadow", "country": "UA"},
        "top_archetypes": [{"archetype": "Void Soul DH", "lineups": 16, "share": 1.0}],
    }
]

TOURNAMENT = {
    "tournament_id": _TOURNAMENT_ID,
    "name": "2026 Hearthstone Masters Tour Summer Championship",
    "kind": "tour_stop",
    "start_time": "2026-07-31T14:00:00+00:00",
    "stages": [
        {
            "stage_id": "6a6f3a399e7a410019bde4e0",
            "name": "Top 8",
            "standings": [
                {
                    "place": 1,
                    "player": {"name": "demoshadow", "country": "UA"},
                    "score": "3 - 0",
                    "wins": 3,
                    "losses": 0,
                }
            ],
        }
    ],
    "lineups": [
        {
            "player": {"name": "Che0nsu", "country": "KR"},
            "decks": [
                {
                    "archetype": "Void Soul DH",
                    "class": "DEMONHUNTER",
                    "deck_code": "AAECAea5AwaongbNngaKqgeSqgeTqgfosQcM4fgFzp4G0J4G054G3v8GtpcHkr8Hlb8HxN0Hxt0Hx90HteEHAAA=",
                    "url": "https://www.hsguru.com/deck/41269302",
                }
            ],
        }
    ],
    "lineups_total": 16,
    "lineups_complete": True,
    "lineup_popularity": {
        "lineups": 16,
        "archetypes": [{"archetype": "Void Soul DH", "lineups": 16, "share": 1.0}],
        "classes": [{"class": "DEMONHUNTER", "lineups": 16, "share": 1.0}],
    },
    "complete": True,
    "reused": True,
}

POINTS = {
    "season": "2026",
    "season_label": "2026",
    "leaderboard": "STD",
    "leaderboard_label": "Standard",
    "headers": [
        "#",
        "Player",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "Total",
    ],
    "source_url": "https://www.hsguru.com/leaderboard/points?points_season=2026&leaderboard_id=STD&limit=3000",
    "items": [
        {
            "rank": 1,
            "player": _PLAYER,
            "january": 6,
            "february": 7,
            "march": 7,
            "april": 7,
            "may": 7,
            "june": 6,
            "total": 40,
        }
    ],
}

POINTS_SEASONS = [
    {
        "season": "2026",
        "season_label": "2026",
        "leaderboard": "STD",
        "leaderboard_label": "Standard",
        "players": 1182,
    }
]

LEGACY = {
    "table": "qualifier-stats",
    "options": ["current", "all", "2020", "2021", "2022"],
    "period": "2021",
    "label": "2021",
    "source_url": "https://www.hsguru.com/legacy-hsesports/mt/qualifier-stats/2021?limit=5000",
    "headers": ["#", "Player", "Cups", "Top 8", "Top 16", "Winrate %"],
    "items": [
        {
            "rank": 1,
            "player": {
                "name": "Rine",
                "battletag": "Rine#21929",
                "profile_url": "https://www.hsguru.com/player-profile/Rine%2321929",
            },
            "cups": 108,
            "top_8": 15,
            "top_16": 18,
            "winrate_pct": 70.57,
        }
    ],
}
