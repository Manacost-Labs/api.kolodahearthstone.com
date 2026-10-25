from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.balance_radar import social
from app.balance_radar.clients import GetXApiClient, RedditApisClient
from app.balance_radar.social import (
    SocialPost,
    collect_social,
    match_mentions,
    reddit_post,
    x_post,
)

FIXTURES = Path(__file__).parent / "fixtures" / "balance_radar"
NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def post(
    text: str, *, title: str | None = None, post_id: str = "reddit:a", score: int = 1
) -> SocialPost:
    return SocialPost(
        id=post_id,
        platform="reddit",
        kind="post",
        url="https://reddit.com/x",
        author="u",
        score=score,
        created_at="2026-10-24T00:00:00+00:00",
        community="r/hearthstone",
        text=text,
        title=title,
    )


def test_reddit_and_x_rows_normalize_to_social_posts() -> None:
    raw = fixture("reddit_search.json")["posts"][0]
    item = reddit_post(raw)
    assert item is not None
    assert item.id == f"reddit:{raw['id']}" and item.score == raw["upvotes"]
    assert item.community == "r/hearthstone" and item.formats == ("standard",)
    tweet = x_post(fixture("x_search.json")["tweets"][0])
    assert tweet is not None and tweet.platform == "x"
    assert tweet.created_at.endswith("+00:00") and tweet.url.startswith(
        "https://x.com/"
    )
    assert reddit_post({"id": "t3_bad"}) is None


def test_hand_buff_archetype_name_is_not_balance_talk() -> None:
    assert not post(
        "Rainbow Hand Buff Death Knight reached #58 Legend"
    ).is_balance_related()
    assert not post("Handbuff DK list").is_balance_related()
    assert post("Ritual of Summoning needs a nerf").is_balance_related()
    assert post("Друида надо баффнуть").is_balance_related()


def test_wild_posts_are_routed_to_the_wild_radar() -> None:
    wild = reddit_post(
        {
            "id": "w1",
            "subreddit": "wildhearthstone",
            "title": "Boar Rogue is busted",
            "upvotes": 3,
        }
    )
    both = reddit_post(
        {
            "id": "b1",
            "subreddit": "hearthstone",
            "title": "Wild Pirate Warrior nerf?",
            "upvotes": 3,
        }
    )
    standard = reddit_post(
        {"id": "s1", "subreddit": "hearthstone", "title": "Zoolock nerf", "upvotes": 3}
    )
    assert wild.formats == ("wild",)
    assert both.formats == ("standard", "wild")
    assert standard.formats == ("standard",)


def test_mentions_match_aliases_word_boundaries_and_curly_apostrophes() -> None:
    posts = [
        post("Zoo Warlock is everywhere", post_id="reddit:1", score=10),
        post("C’Thun DH is busted", post_id="reddit:2", score=50),
        post("zoolocker is not a word", post_id="reddit:3"),
        post("Ritual of Summoning!", title="Nerf", post_id="reddit:4"),
    ]
    found = match_mentions(
        posts,
        {
            "archetype:standard:Zoolock": ["Zoolock", "Zoo Warlock"],
            "archetype:standard:C'Thun DH": ["C'Thun DH", "C'Thun Demon Hunter"],
            "card:BE_1": ["Ritual of Summoning"],
            "card:short": ["Imp"],
        },
    )
    assert [p.id for p in found["archetype:standard:Zoolock"]] == ["reddit:1"]
    assert [p.id for p in found["archetype:standard:C'Thun DH"]] == ["reddit:2"]
    assert [p.id for p in found["card:BE_1"]] == ["reddit:4"]
    assert "card:short" not in found  # aliases shorter than four characters are ignored


def test_one_failed_listing_does_not_abort_the_collection(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    search = fixture("reddit_search.json")

    def reddit_handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("subreddit") == "CompetitiveHS":
            return httpx.Response(500)
        if "/comments" in request.url.path:
            return httpx.Response(200, json={"post": {}, "comments": [], "after": None})
        return httpx.Response(200, json=search)

    tweets = fixture("x_search.json")
    reddit = RedditApisClient(
        "k",
        transport=httpx.MockTransport(reddit_handler),
        sleep=lambda _s: None,
        max_attempts=1,
    )
    x = GetXApiClient(
        "k",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=tweets)),
        sleep=lambda _s: None,
    )

    bundle = collect_social(reddit=reddit, x=x, entity_terms=["Zoolock"], now=NOW)

    failed = [s for s in bundle.sources if s["state"] == "failed"]
    assert failed and all("CompetitiveHS" in s["id"] for s in failed)
    assert any(p.platform == "reddit" for p in bundle.posts)
    assert any(p.platform == "x" for p in bundle.posts)
    assert bundle.window_from.startswith("2026-10-18")
    assert set(bundle.usage) == {"redditapis", "getxapi"}
    # Posts are deduplicated across listings.
    assert len({p.id for p in bundle.posts}) == len(bundle.posts)


def test_terminal_credit_error_marks_the_platform_aborted(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    x = GetXApiClient(
        "k",
        transport=httpx.MockTransport(lambda r: httpx.Response(402)),
        sleep=lambda _s: None,
    )
    bundle = collect_social(reddit=None, x=x, now=NOW)
    states = {s["id"]: s["state"] for s in bundle.sources}
    assert states["reddit"] == "disabled" and states["x"] == "aborted"
    assert bundle.posts == []


def test_x_accounts_env_override_is_sanitized(monkeypatch) -> None:
    monkeypatch.setenv("RADAR_X_ACCOUNTS", "@PlayHearthstone, bad name!, ThijsHS")
    assert social.x_accounts() == ("PlayHearthstone", "ThijsHS")
