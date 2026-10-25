"""RedditAPIs (https://docs.redditapis.com) and GetXAPI (https://docs.getxapi.com)."""

from __future__ import annotations

from typing import Any

from .http import JsonApiClient, SocialApiError

REDDIT_SORTS = frozenset({"new", "hot", "top", "rising", "controversial", "best"})
REDDIT_SEARCH_SORTS = frozenset({"relevance", "new", "hot", "top", "comments"})
REDDIT_TIMEFRAMES = frozenset({"hour", "day", "week", "month", "year", "all"})


def _posts(body: Any) -> list[dict[str, Any]]:
    posts = body.get("posts") if isinstance(body, dict) else None
    if not isinstance(posts, list):
        raise SocialApiError("redditapis listing has no posts array")
    return [post for post in posts if isinstance(post, dict) and post.get("id")]


class RedditApisClient(JsonApiClient):
    """Reads at $0.0015 per call; 429 answers are paced and not billed."""

    provider = "redditapis"
    base_url = "https://api.redditapis.com"
    usd_per_request = 0.0015

    def subreddit_posts(
        self,
        subreddit: str,
        *,
        sort: str = "hot",
        t: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if sort not in REDDIT_SORTS or (t is not None and t not in REDDIT_TIMEFRAMES):
            raise ValueError("unsupported Reddit listing")
        return _posts(
            self.get(
                "/api/reddit/posts",
                {
                    "subreddit": subreddit,
                    "sort": sort,
                    "t": t,
                    "limit": min(100, limit),
                },
            )
        )

    def search(
        self,
        q: str,
        *,
        subreddit: str | None = None,
        sort: str = "relevance",
        t: str = "week",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if sort not in REDDIT_SEARCH_SORTS or t not in REDDIT_TIMEFRAMES:
            raise ValueError("unsupported Reddit search")
        return _posts(
            self.get(
                "/api/reddit/search",
                {
                    "q": q,
                    "subreddit": subreddit,
                    "sort": sort,
                    "t": t,
                    "limit": min(100, limit),
                },
            )
        )

    def post_comments(
        self, post_id: str, *, limit: int = 50, depth: int = 2, sort: str = "top"
    ) -> list[dict[str, Any]]:
        """Flatten Reddit's native ``t1`` tree; ``more`` stubs are skipped."""
        if not post_id.isalnum():
            raise ValueError("Reddit post id must be alphanumeric")
        body = self.get(
            f"/api/reddit/post/{post_id.lower()}/comments",
            {"limit": limit, "depth": depth, "sort": sort},
        )
        comments: list[dict[str, Any]] = []

        def walk(nodes: Any, level: int) -> None:
            if not isinstance(nodes, list):
                return
            for node in nodes:
                if not isinstance(node, dict) or node.get("kind") != "t1":
                    continue
                data = node.get("data")
                if not isinstance(data, dict) or not data.get("id"):
                    continue
                comments.append({**data, "depth": level})
                replies = data.get("replies")
                if isinstance(replies, dict):
                    walk((replies.get("data") or {}).get("children"), level + 1)

        walk(body.get("comments") if isinstance(body, dict) else None, 0)
        return comments

    def account(self) -> dict[str, Any]:
        body = self.get("/api/account/me", billable=False, use_cache=False)
        return body if isinstance(body, dict) else {}


class GetXApiClient(JsonApiClient):
    """Advanced search at $0.001 per page of about 20 posts."""

    provider = "getxapi"
    base_url = "https://api.getxapi.com"
    usd_per_request = 0.001

    def advanced_search(
        self, q: str, *, product: str = "Top", pages: int = 1
    ) -> list[dict[str, Any]]:
        if product not in {"Top", "Latest"}:
            raise ValueError("unsupported X search product")
        tweets: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max(1, pages)):
            body = self.get(
                "/twitter/tweet/advanced_search",
                {"q": q, "product": product, "cursor": cursor},
            )
            page = body.get("tweets") if isinstance(body, dict) else None
            if not isinstance(page, list):
                raise SocialApiError("getxapi search has no tweets array")
            tweets.extend(
                tweet for tweet in page if isinstance(tweet, dict) and tweet.get("id")
            )
            cursor = body.get("next_cursor") if body.get("has_more") else None
            if not cursor:
                break
        return tweets

    def account(self) -> dict[str, Any]:
        body = self.get("/account/me", billable=False, use_cache=False)
        return body if isinstance(body, dict) else {}
