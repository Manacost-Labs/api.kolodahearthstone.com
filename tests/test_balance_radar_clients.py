from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from app import provider_usage
from app.balance_radar.clients import GetXApiClient, RedditApisClient
from app.balance_radar.http import SocialApiError

FIXTURES = Path(__file__).parent / "fixtures" / "balance_radar"
SECRET = "test-key-never-logged"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    provider_usage._last_prune.clear()
    return tmp_path


def ledger(data_dir: Path) -> list[dict]:
    with sqlite3.connect(data_dir / provider_usage.DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM calls ORDER BY id")]


def client(cls, handler, **kwargs):
    return cls(
        SECRET,
        transport=httpx.MockTransport(handler),
        sleep=kwargs.pop("sleep", lambda _s: None),
        **kwargs,
    )


def test_reddit_search_sends_bearer_and_records_one_billed_read(data_dir) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=fixture("reddit_search.json"))

    reddit = client(RedditApisClient, handler)
    posts = reddit.search("nerf OR buff", subreddit="hearthstone", sort="top", t="week")

    assert posts[0]["title"] == "Nerf Ritual of Summoning."
    request = seen[0]
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert request.url.path == "/api/reddit/search"
    assert request.url.params["subreddit"] == "hearthstone"
    rows = ledger(data_dir)
    assert [(r["provider"], r["outcome"], r["cost"], r["source_id"]) for r in rows] == [
        ("redditapis", "success", 1.0, "balance_radar")
    ]
    # The ledger never stores the key, the query or the URL.
    assert SECRET not in json.dumps(rows) and "nerf" not in json.dumps(rows)
    assert reddit.stats.as_dict(reddit.usd_per_request)["costUsd"] == 0.0015


def test_cached_body_is_reused_without_a_second_paid_call(data_dir) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=fixture("reddit_search.json"))

    reddit = client(RedditApisClient, handler)
    reddit.subreddit_posts("CompetitiveHS", sort="hot")
    reddit.subreddit_posts("CompetitiveHS", sort="hot")
    reddit.subreddit_posts("CompetitiveHS", sort="new")

    assert calls == 2
    assert reddit.stats.cached == 1 and reddit.stats.billable == 2
    cache_files = list(
        (data_dir / "cache" / "balance-radar" / "redditapis").glob("*.json")
    )
    assert len(cache_files) == 2
    assert all(SECRET not in path.read_text() for path in cache_files)


def test_rate_limit_honours_retry_after_and_is_not_billed(data_dir) -> None:
    answers = [
        httpx.Response(
            429,
            headers={"Retry-After": "3"},
            json={"error": "rate_limited", "retry_after": 3},
        ),
        httpx.Response(503),
        httpx.Response(200, json=fixture("reddit_search.json")),
    ]
    sleeps: list[float] = []
    reddit = client(
        RedditApisClient, lambda _r: answers.pop(0), sleep=sleeps.append, cache_ttl=0
    )

    assert reddit.search("nerf", t="week")
    assert sleeps[0] == 3.0 and len(sleeps) == 2
    rows = ledger(data_dir)
    assert [(r["outcome"], r["reason"], r["cost"]) for r in rows] == [
        ("failed", "http_429", 0.0),
        ("failed", "http_503", 0.0),
        ("success", None, 1.0),
    ]


@pytest.mark.parametrize("status", [401, 402, 403])
def test_auth_and_credit_errors_are_terminal(data_dir, status) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, json={"error": "nope"})

    x = client(GetXApiClient, handler)
    with pytest.raises(SocialApiError) as error:
        x.advanced_search("hearthstone nerf")
    assert error.value.terminal and error.value.status_code == status
    assert attempts == 1
    assert SECRET not in str(error.value)


def test_retries_stop_after_max_attempts(data_dir) -> None:
    reddit = client(
        RedditApisClient, lambda _r: httpx.Response(502), max_attempts=3, cache_ttl=0
    )
    with pytest.raises(SocialApiError, match="retries exhausted") as error:
        reddit.subreddit_posts("hearthstone")
    assert not error.value.terminal
    assert reddit.stats.requests == 3 and reddit.stats.billable == 0


def test_comment_tree_is_flattened_and_more_stubs_skipped(data_dir) -> None:
    tree = {
        "post": {"id": "1xdju41"},
        "comments": [
            {
                "kind": "t1",
                "data": {
                    "id": "c1",
                    "author": "a",
                    "body": "Ritual needs a nerf",
                    "score": 120,
                    "permalink": "/r/hearthstone/comments/1xdju41/x/c1/",
                    "created_utc": 1792800000,
                    "replies": {
                        "kind": "Listing",
                        "data": {
                            "children": [
                                {
                                    "kind": "t1",
                                    "data": {
                                        "id": "c2",
                                        "body": "agreed",
                                        "score": 5,
                                        "replies": "",
                                    },
                                }
                            ]
                        },
                    },
                },
            },
            {"kind": "more", "data": {"count": 40, "children": ["c3"]}},
        ],
        "after": None,
    }
    reddit = client(RedditApisClient, lambda r: httpx.Response(200, json=tree))
    comments = reddit.post_comments("1XDJU41")
    assert [(c["id"], c["depth"]) for c in comments] == [("c1", 0), ("c2", 1)]
    with pytest.raises(ValueError):
        reddit.post_comments("t3_bad")


def test_x_search_follows_cursor_until_has_more_is_false(data_dir) -> None:
    first = {**fixture("x_search.json"), "has_more": True, "next_cursor": "CURSOR2"}
    second = {
        **fixture("x_search.json"),
        "tweets": fixture("x_search.json")["tweets"][:1],
    }
    cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursors.append(request.url.params.get("cursor"))
        return httpx.Response(200, json=first if len(cursors) == 1 else second)

    x = client(GetXApiClient, handler)
    tweets = x.advanced_search("hearthstone nerf", product="Latest", pages=3)
    assert cursors == [None, "CURSOR2"]
    assert len(tweets) == 4
    assert x.stats.billable == 2
    with pytest.raises(ValueError):
        x.advanced_search("q", product="Popular")


def test_missing_key_is_a_terminal_configuration_error() -> None:
    with pytest.raises(SocialApiError) as error:
        RedditApisClient("")
    assert error.value.terminal
