"""Collect, normalize and match social posts about Hearthstone balance."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .clients import GetXApiClient, RedditApisClient
from .http import SocialApiError

logger = logging.getLogger(__name__)

Platform = Literal["reddit", "x"]

# Subreddit -> formats its posts speak about. r/wildhearthstone is Wild only.
SUBREDDITS: dict[str, tuple[str, ...]] = {
    "hearthstone": ("standard", "wild"),
    "CompetitiveHS": ("standard", "wild"),
    "wildhearthstone": ("wild",),
}
BALANCE_QUERY = (
    "nerf OR nerfs OR nerfed OR buff OR buffs OR balance OR broken OR overpowered"
)
X_BALANCE_TERMS = (
    '(nerf OR nerfs OR nerfed OR buff OR buffs OR "balance patch" OR '
    '"balance changes" OR broken OR busted OR overpowered OR "too strong")'
)
# Official account, developers who post balance notes, and players whose
# opinions move the community. Override with RADAR_X_ACCOUNTS.
DEFAULT_X_ACCOUNTS = (
    "PlayHearthstone",
    "bmkibler",
    "ThijsHS",
    "TrumpSC",
    "Kripparrian",
    "HSReplayNet",
    "ViciousSyndicat",
    "ZalaeHS",
    "RduHS",
)
OFFICIAL_X_ACCOUNTS = frozenset({"playhearthstone"})

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})
_HAND_BUFF = re.compile(r"hand[\s-]*buff\w*", re.IGNORECASE)
_BALANCE = re.compile(
    r"\b(nerf\w*|buff(?:s|ed|ing)?|balanc\w*|overpowered|underpowered|op|"
    r"broken|busted|too (?:strong|weak|good|bad)|unplayable|hotfix|"
    r"нерф\w*|бафф?\w*|баланс\w*|имба\w*)\b",
    re.IGNORECASE,
)
_WILD = re.compile(r"\bwild\b|\[wild\]|вольн\w*", re.IGNORECASE)


@dataclass(frozen=True)
class SocialPost:
    id: str
    platform: Platform
    kind: Literal["post", "comment", "tweet"]
    url: str
    author: str
    score: int
    created_at: str
    community: str
    text: str
    title: str | None = None
    comments: int | None = None
    author_followers: int | None = None
    parent_id: str | None = None
    official: bool = False
    formats: tuple[str, ...] = ("standard", "wild")

    @property
    def body(self) -> str:
        return f"{self.title}\n{self.text}" if self.title else self.text

    def is_balance_related(self) -> bool:
        return bool(_BALANCE.search(_HAND_BUFF.sub(" ", self.body)))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SocialBundle:
    posts: list[SocialPost] = field(default_factory=list)
    window_from: str = ""
    window_to: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)

    def for_format(self, format_name: str) -> list[SocialPost]:
        return [post for post in self.posts if format_name in post.formats]


def x_accounts() -> tuple[str, ...]:
    raw = os.environ.get("RADAR_X_ACCOUNTS", "").strip()
    if not raw:
        return DEFAULT_X_ACCOUNTS
    names = [name.strip().lstrip("@") for name in raw.split(",")]
    return tuple(name for name in names if re.fullmatch(r"\w{1,15}", name))


def window_days() -> int:
    try:
        return max(1, min(30, int(os.environ.get("RADAR_SOCIAL_WINDOW_DAYS", "7"))))
    except ValueError:
        return 7


def _iso_from_epoch(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(float(value), UTC).isoformat(timespec="seconds")
    return None


def _int(value: Any) -> int:
    return (
        int(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else 0
    )


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").translate(_APOSTROPHES).strip()[:limit]


def _formats_for(community_formats: tuple[str, ...], body: str) -> tuple[str, ...]:
    if community_formats == ("wild",) or body.lstrip().upper().startswith("[WILD]"):
        return ("wild",)
    if _WILD.search(body):
        return ("standard", "wild")
    return (
        tuple(name for name in community_formats if name == "standard")
        or community_formats
    )


def reddit_post(raw: Mapping[str, Any]) -> SocialPost | None:
    post_id = str(raw.get("id") or "")
    if not post_id.isalnum() or raw.get("stickied") and _int(raw.get("upvotes")) < 5:
        return None
    subreddit = str(raw.get("subreddit") or "")
    title = _text(raw.get("title"), 300)
    text = _text(raw.get("text"))
    created = (
        str(raw.get("created") or "") or _iso_from_epoch(raw.get("created_utc")) or ""
    )
    community_formats = SUBREDDITS.get(subreddit, ("standard", "wild"))
    return SocialPost(
        id=f"reddit:{post_id}",
        platform="reddit",
        kind="post",
        url=str(raw.get("url") or f"https://www.reddit.com/comments/{post_id}/"),
        author=str(raw.get("author") or "[deleted]"),
        score=_int(raw.get("upvotes")),
        comments=_int(raw.get("comments")),
        created_at=created,
        community=f"r/{subreddit}",
        title=title,
        text=text,
        formats=_formats_for(community_formats, f"{title}\n{text}"),
    )


def reddit_comment(raw: Mapping[str, Any], parent: SocialPost) -> SocialPost | None:
    comment_id = str(raw.get("id") or "")
    body = _text(raw.get("body"), 2000)
    if not comment_id.isalnum() or not body or body in {"[deleted]", "[removed]"}:
        return None
    permalink = str(raw.get("permalink") or "")
    url = (
        f"https://www.reddit.com{permalink}"
        if permalink.startswith("/")
        else parent.url
    )
    return SocialPost(
        id=f"reddit-comment:{comment_id}",
        platform="reddit",
        kind="comment",
        url=url,
        author=str(raw.get("author") or "[deleted]"),
        score=_int(raw.get("score")),
        created_at=_iso_from_epoch(raw.get("created_utc")) or parent.created_at,
        community=parent.community,
        text=body,
        parent_id=parent.id,
        formats=parent.formats,
    )


def _x_time(value: Any) -> str:
    try:
        parsed = datetime.strptime(str(value), "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return ""
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def x_post(raw: Mapping[str, Any]) -> SocialPost | None:
    tweet_id = str(raw.get("id") or "")
    if not tweet_id.isdigit():
        return None
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    username = str(author.get("userName") or "")
    text = _text(raw.get("text"), 1200)
    return SocialPost(
        id=f"x:{tweet_id}",
        platform="x",
        kind="tweet",
        url=str(raw.get("url") or f"https://x.com/{username or 'i'}/status/{tweet_id}"),
        author=username or "unknown",
        score=_int(raw.get("likeCount")),
        comments=_int(raw.get("replyCount")),
        created_at=_x_time(raw.get("createdAt")),
        community="x",
        text=text,
        author_followers=_int(author.get("followers")) or None,
        official=username.casefold() in OFFICIAL_X_ACCOUNTS,
        formats=_formats_for(("standard", "wild"), text),
    )


@dataclass
class _Collector:
    posts: dict[str, SocialPost] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)

    def add(self, post: SocialPost | None) -> None:
        if post is None:
            return
        previous = self.posts.get(post.id)
        if previous is None or post.score > previous.score:
            self.posts[post.id] = post

    def run(self, label: str, platform: Platform, fetch, convert) -> list[SocialPost]:  # type: ignore[no-untyped-def]
        try:
            rows = fetch()
        except (SocialApiError, ValueError) as exc:
            self.sources.append(
                {
                    "id": label,
                    "platform": platform,
                    "state": "failed",
                    "error": str(exc),
                }
            )
            logger.warning("balance radar source %s failed: %s", label, exc)
            if isinstance(exc, SocialApiError) and exc.terminal:
                raise
            return []
        converted = [
            post for post in (convert(row) for row in rows) if post is not None
        ]
        for post in converted:
            self.add(post)
        self.sources.append(
            {"id": label, "platform": platform, "state": "ok", "items": len(converted)}
        )
        return converted


def collect_reddit(
    client: RedditApisClient, collector: _Collector, *, comment_threads: int = 12
) -> None:
    for subreddit in SUBREDDITS:
        for sort, t in (("hot", None), ("top", "week")):
            collector.run(
                f"reddit:r/{subreddit}:{sort}{':' + t if t else ''}",
                "reddit",
                lambda s=subreddit, so=sort, tt=t: client.subreddit_posts(
                    s, sort=so, t=tt
                ),
                reddit_post,
            )
        collector.run(
            f"reddit:r/{subreddit}:search:balance",
            "reddit",
            lambda s=subreddit: client.search(
                BALANCE_QUERY, subreddit=s, sort="relevance", t="week"
            ),
            reddit_post,
        )
    collector.run(
        "reddit:r/hearthstone:search:balance:new",
        "reddit",
        lambda: client.search(
            BALANCE_QUERY, subreddit="hearthstone", sort="new", t="week"
        ),
        reddit_post,
    )
    threads = sorted(
        (
            post
            for post in collector.posts.values()
            if post.platform == "reddit"
            and post.kind == "post"
            and post.is_balance_related()
        ),
        key=lambda post: (post.comments or 0) + post.score,
        reverse=True,
    )[:comment_threads]
    for parent in threads:
        collector.run(
            f"reddit:comments:{parent.id.split(':', 1)[1]}",
            "reddit",
            lambda p=parent: client.post_comments(p.id.split(":", 1)[1]),
            lambda row, p=parent: reddit_comment(row, p),
        )


def collect_x(
    client: GetXApiClient,
    collector: _Collector,
    *,
    since: str,
    entity_terms: Iterable[str] = (),
) -> None:
    base = f"hearthstone {X_BALANCE_TERMS} -filter:retweets since:{since}"
    collector.run(
        "x:search:balance:top",
        "x",
        lambda: client.advanced_search(base, product="Top"),
        x_post,
    )
    collector.run(
        "x:search:balance:latest",
        "x",
        lambda: client.advanced_search(base, product="Latest", pages=2),
        x_post,
    )
    accounts = x_accounts()
    official = [name for name in accounts if name.casefold() in OFFICIAL_X_ACCOUNTS]
    creators = [name for name in accounts if name.casefold() not in OFFICIAL_X_ACCOUNTS]
    # Separate queries: the official account posts often enough to crowd the
    # creators out of a shared "Latest" timeline.
    for label, names, pages in (
        ("x:official:latest", official, 1),
        ("x:creators:latest", creators, 2),
    ):
        if not names:
            continue
        authors = "(" + " OR ".join(f"from:{name}" for name in names) + ")"
        collector.run(
            label,
            "x",
            lambda a=authors, n=pages: client.advanced_search(
                f"{a} -filter:retweets since:{since}", product="Latest", pages=n
            ),
            x_post,
        )
    terms = [term for term in dict.fromkeys(entity_terms) if term][:8]
    if terms:
        quoted = " OR ".join(f'"{term}"' for term in terms)
        collector.run(
            "x:search:entities:top",
            "x",
            lambda: client.advanced_search(
                f"hearthstone ({quoted}) -filter:retweets since:{since}", product="Top"
            ),
            x_post,
        )
    collector.run(
        "x:search:wild:top",
        "x",
        lambda: client.advanced_search(
            f"hearthstone wild {X_BALANCE_TERMS} -filter:retweets since:{since}",
            product="Top",
        ),
        lambda row: _wild_only(x_post(row)),
    )


def _wild_only(post: SocialPost | None) -> SocialPost | None:
    if post is None:
        return None
    return SocialPost(**{**post.as_dict(), "formats": ("wild",)})


def collect_social(
    *,
    reddit: RedditApisClient | None,
    x: GetXApiClient | None,
    entity_terms: Iterable[str] = (),
    now: datetime | None = None,
) -> SocialBundle:
    """Collect both platforms; one failed listing never aborts the other ones."""
    now = now or datetime.now(UTC)
    since = (now - timedelta(days=window_days())).date()
    collector = _Collector()
    usage: dict[str, Any] = {}
    for name, client, collect in (
        ("reddit", reddit, lambda c: collect_reddit(c, collector)),
        (
            "x",
            x,
            lambda c: collect_x(
                c, collector, since=since.isoformat(), entity_terms=entity_terms
            ),
        ),
    ):
        if client is None:
            collector.sources.append(
                {"id": name, "platform": name, "state": "disabled"}
            )
            continue
        try:
            collect(client)
        except SocialApiError as exc:
            collector.sources.append(
                {"id": name, "platform": name, "state": "aborted", "error": str(exc)}
            )
        usage[client.provider] = client.stats.as_dict(client.usd_per_request)
    cutoff = datetime.combine(since, datetime.min.time(), UTC)
    posts = [
        post
        for post in collector.posts.values()
        if not post.created_at
        or datetime.fromisoformat(post.created_at.replace("Z", "+00:00")) >= cutoff
    ]
    posts.sort(key=lambda post: post.score, reverse=True)
    return SocialBundle(
        posts=posts,
        window_from=cutoff.isoformat(timespec="seconds"),
        window_to=now.astimezone(UTC).isoformat(timespec="seconds"),
        sources=collector.sources,
        usage=usage,
    )


# -- mentions ------------------------------------------------------------


def alias_pattern(aliases: Iterable[str]) -> re.Pattern[str] | None:
    cleaned = sorted(
        {
            alias.translate(_APOSTROPHES).strip()
            for alias in aliases
            if len(alias.strip()) >= 4
        },
        key=len,
        reverse=True,
    )
    if not cleaned:
        return None
    return re.compile(
        r"(?<![\w'])(?:"
        + "|".join(re.escape(alias) for alias in cleaned)
        + r")(?![\w'])",
        re.IGNORECASE,
    )


def match_mentions(
    posts: Iterable[SocialPost], aliases_by_ref: Mapping[str, Iterable[str]]
) -> dict[str, list[SocialPost]]:
    """Posts mentioning each entity, most-upvoted first."""
    patterns = {
        ref: pattern
        for ref, aliases in aliases_by_ref.items()
        if (pattern := alias_pattern(aliases)) is not None
    }
    found: dict[str, list[SocialPost]] = {ref: [] for ref in patterns}
    for post in posts:
        body = post.body.translate(_APOSTROPHES)
        for ref, pattern in patterns.items():
            if pattern.search(body):
                found[ref].append(post)
    for matches in found.values():
        matches.sort(key=lambda post: post.score, reverse=True)
    return {ref: matches for ref, matches in found.items() if matches}
