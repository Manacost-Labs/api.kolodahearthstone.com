"""OpenAPI examples of /v1/hsguru/streamers*.

Shapes, profiles, decks and VOD links come from the live run of 2026-10-25;
the counts are illustrative.
"""

from __future__ import annotations

from typing import Any

_DECK = {
    "deck_id": 42709303,
    "deck_code": "AAECAa0GCPCfBMODB/WYB+ybB9edB7DJB4XrB9z7BwuFnwSFhgfFlAfgnQfksgeyxQevyQeT3wfV4Af04Afh+gcAAA==",
    "archetype": "Egg Priest",
    "player_class": "PRIEST",
    "format": "standard",
    "peak_rank": 20,
    "latest_rank": 24,
    "worst_rank": 110,
    "wins": 33,
    "losses": 17,
    "games": 50,
    "winrate": 66.0,
    "last_played_at": "2026-10-25T00:42:46+00:00",
    "first_seen_at": "2026-10-25T16:18:19.745750+00:00",
    "hsguru_url": "https://www.hsguru.com/deck/42709303",
    "on_current_stream": False,
    "stream_url": None,
    "vod": {
        "url": "https://www.twitch.tv/videos/2909721478?t=5h41m20s",
        "title": "Egg Priest going for #1 Legend !hsreplay",
        "started_at": "2026-10-24T18:56:26+00:00",
        "offset_seconds": 20480,
    },
}

_LIVE = {
    "platform": "twitch",
    "title": "High Legend Ladder - Best Decks Rotation !gx !deck",
    "viewers": 852,
    "mode": None,
    "language": None,
    "started_at": "2026-10-25T13:37:53+00:00",
    "thumbnail_url": "https://static-cdn.jtvnw.net/previews-ttv/live_user_reqvam-384x216.jpg",
    "stream_url": "https://www.twitch.tv/reqvam",
    "observed_at": "2026-10-25T16:26:22+00:00",
}

_STREAMER = {
    "id": 48793025,
    "slug": "reqvam",
    "display_name": "reqvam",
    "login": "reqvam",
    "twitch_url": "https://www.twitch.tv/reqvam",
    "youtube_url": "https://www.youtube.com/@reqvam",
    "x_url": "https://x.com/reqvamhs",
    "links": [{"kind": "discord", "url": "https://discord.com/invite/UedvbNAaJ5"}],
    "avatar_url": "https://static-cdn.jtvnw.net/jtv_user_pictures/a1d9044e-496e-402f-b7ba-86008ec86d37-profile_image-300x300.png",
    "description": "3rd at the 2023 Hearthstone World Championship",
    "followers": 25174,
    "language": None,
    "decks_total": 64,
    "last_played_at": "2026-10-25T16:20:11+00:00",
    "profile_state": "ok",
    "profile_checked_at": "2026-10-25T16:21:23+00:00",
    "hsguru_url": "https://www.hsguru.com/streamer-decks?twitch_id=48793025",
    "live": _LIVE,
}


def _meta(count: int, **extra: Any) -> dict[str, Any]:
    return {
        "source_id": "hsguru_streamers",
        "fetched_at": "2026-10-25T16:26:22.512647+00:00",
        "stale": False,
        "count": count,
        **extra,
    }


def _ok(description: str, example: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"example": example}},
    }


NOT_FOUND = {
    404: {
        "description": "HSGuru has no decks of this streamer.",
        "content": {
            "application/json": {
                "example": {"detail": "HSGuru has no decks of this streamer"}
            }
        },
    }
}

STREAMERS = {
    200: _ok(
        "Streamers who played a deck in the window, live ones first.",
        {
            "data": [
                {
                    **_STREAMER,
                    "window": {
                        "decks": 12,
                        "best_rank": 39,
                        "last_played_at": "2026-10-25T16:20:11+00:00",
                    },
                    "recent_decks": [
                        {
                            **_DECK,
                            "on_current_stream": True,
                            "stream_url": _LIVE["stream_url"],
                            "vod": None,
                        }
                    ],
                }
            ],
            "meta": _meta(152, limit=24, offset=0),
        },
    )
}

STREAMER = {
    200: _ok(
        "One streamer: profile, channel links, live stream, decks by format and class, past broadcasts.",
        {
            "data": {
                **_STREAMER,
                "facets": [
                    {
                        "format": "standard",
                        "player_class": "DEMONHUNTER",
                        "decks": 21,
                        "best_rank": 39,
                        "last_played_at": "2026-10-25T16:20:11+00:00",
                    },
                    {
                        "format": "standard",
                        "player_class": "WARLOCK",
                        "decks": 9,
                        "best_rank": 40,
                        "last_played_at": "2026-10-24T21:02:40+00:00",
                    },
                ],
                "vods": [
                    {
                        "url": "https://www.twitch.tv/videos/2909484152",
                        "title": "High Legend Ladder - Four Class Meta? !gx !deck",
                        "started_at": "2026-10-24T14:16:29+00:00",
                        "duration_seconds": 18900,
                        "views": 13815,
                        "thumbnail_url": None,
                    }
                ],
                "last_deck": {
                    **_DECK,
                    "on_current_stream": True,
                    "stream_url": _LIVE["stream_url"],
                    "vod": None,
                },
            },
            "meta": _meta(64),
        },
    ),
    **NOT_FOUND,
}

STREAMER_DECKS = {
    200: _ok(
        "Decks of one streamer, newest game first, with a VOD link at the moment of the game where known.",
        {
            "data": [_DECK],
            "streamer": {**_STREAMER, "live": None},
            "meta": _meta(146, limit=50, offset=0),
        },
    ),
    **NOT_FOUND,
}

RECENT_DECKS = {
    200: _ok(
        "Decks of every streamer, newest game first.",
        {
            "data": [
                {
                    **_DECK,
                    "streamer": {
                        "id": 48793025,
                        "slug": "reqvam",
                        "display_name": "reqvam",
                        "avatar_url": _STREAMER["avatar_url"],
                    },
                }
            ],
            "meta": _meta(1184, limit=50, offset=0),
        },
    )
}

STREAMING_NOW = {
    200: _ok(
        "Live Hearthstone streams by viewers with the deck of the current broadcast.",
        {
            "data": [
                {
                    **_LIVE,
                    "login": "reqvam",
                    "display_name": "reqvam",
                    "mode": "standard",
                    "mode_source": "deck",
                    "streamer": {**_STREAMER, "live": None},
                    "current_deck": {
                        **_DECK,
                        "on_current_stream": True,
                        "stream_url": _LIVE["stream_url"],
                        "vod": None,
                    },
                }
            ],
            "meta": _meta(146, observed_at="2026-10-25T16:26:22+00:00"),
        },
    )
}

COVERAGE = {
    200: _ok(
        "Stored streamers, profiles, decks, past broadcasts, live streams and the last runs.",
        {
            "data": {
                "streamers": 226,
                "profiles": 80,
                "decks": 14853,
                "oldest_deck": "2026-06-27T16:20:31+00:00",
                "newest_deck": "2026-10-25T16:24:05+00:00",
                "vods": 312,
                "live": 146,
                "runs": [
                    {
                        "id": 4,
                        "state": "ok",
                        "deck_pages": 1,
                        "decks_new": 3,
                        "decks_complete": 1,
                        "live_streams": 146,
                        "profiles_attempted": 30,
                        "profiles_ok": 30,
                        "scrape_do_credits": 3,
                    }
                ],
            },
            "meta": _meta(14853),
        },
    )
}
