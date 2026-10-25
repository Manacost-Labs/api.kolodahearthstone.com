"""HSGuru streamers: their decks, who is live now and their channel links.

HSGuru collects the decks Hearthstone streamers play (``/streamer-decks``) and
lists live Hearthstone streams (``/streaming-now``). Both pages are LiveView
dead renders, so a plain Scrape.do request (no browser, 1 credit) already has
the data.

* ``/streamer-decks`` without filters lists every format, every rank (legend
  or not) and every time window, newest game first, 50 rows per page with
  ``offset`` pagination. Rank and window filters are subsets of this list, so
  one walk from the top down to a cutoff reads all of them: the first run goes
  back ``backfill_days`` (HSGuru's widest window is 120 days), later runs stop
  at the start of the last complete walk minus an overlap. Pages only shift
  down while a walk reads them (new games go to the top), so a walk can see a
  row twice but does not skip one; a deck replayed during the walk is read by
  the next run.
* ``/streaming-now`` is read in full plus once per language filter
  (``HS_HSGURU_STREAMERS_LANGUAGES``, default ``ru``) to mark the language.
  HSGuru reports the game mode for few streams; the deck the streamer played
  during the current broadcast comes from ``/streamer-decks``.
* The Twitch channel page carries schema.org JSON-LD with the avatar,
  description, follower count, the channel's social links (YouTube, X, ...)
  and the last past broadcasts with start and duration. A deck played inside
  a past broadcast links to that moment of the VOD. Twitch pages are read
  directly (public, a few per run).

Storage is :mod:`app.hsguru_streamers_store`. Reads are written once in SQL that
SQLite (REST) and the PostgreSQL mirror (GraphQL) both run.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from lxml import etree
from lxml import html as lxml_html

from . import hsguru_streamers_store as store
from .db import get_db_connection, init_db
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .sources import Source
from .storage import load_dataset, save_dataset, save_status

logger = logging.getLogger(__name__)

SOURCE_ID = "hsguru_streamers"
PAGE_SOURCE_ID = f"{SOURCE_ID}:page"
HSGURU = "https://www.hsguru.com"
TWITCH = "https://www.twitch.tv"
PAGE_SIZE = 50
FORMATS = {
    "standard": "standard",
    "wild": "wild",
    "classic": "classic",
    "twist": "twist",
}
CLASSES = (
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
# Scrape.do allows 50 concurrent requests per account and the mulligan job
# takes 40 of them.
MAX_CONCURRENCY = 5
SCRAPE_DO_TIMEOUT_MS = 60_000
# Plain first (measured 2026-10-25: full table, 8 s, 1 credit), then a
# rendered page if the plain one did not validate.
SCRAPE_DO_PROFILES = ((False, False), (True, False))
TWITCH_TIMEOUT_SECONDS = 20.0
# Twitch sometimes serves its cached app shell without the JSON-LD: 19 of 30
# channels at three in parallel, then 21 of 30 one at a time, then 0 of 20
# minutes later (2026-10-25). Reads go one at a time with a pause, a shell is
# read once more, and a channel still without data is retried two hours later.
TWITCH_PAUSE_SECONDS = 1.0
TWITCH_RETRY_SECONDS = 3.0
TWITCH_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)
LIVE_STALE_MINUTES = 20
# A game ends some minutes after its last turn; open the VOD a bit earlier.
VOD_LEAD_SECONDS = 300
LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{2,25}$")


def _env_int(
    name: str, default: int, *, minimum: int = 0, maximum: int = 1_000_000
) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class Settings:
    concurrency: int = MAX_CONCURRENCY
    backfill_days: int = 120
    overlap_minutes: int = 120
    max_deck_pages: int = 400
    profile_limit: int = 30
    profile_concurrency: int = 1
    deadline_seconds: int = 25 * 60
    languages: tuple[str, ...] = ("ru",)

    @classmethod
    def from_env(cls) -> Settings:
        base = cls()
        values: dict[str, Any] = {
            name: _env_int(f"HS_HSGURU_STREAMERS_{name.upper()}", getattr(base, name))
            for name in cls.__dataclass_fields__
            if name != "languages"
        }
        values["concurrency"] = max(1, min(values["concurrency"], MAX_CONCURRENCY))
        values["profile_concurrency"] = max(1, min(values["profile_concurrency"], 3))
        values["backfill_days"] = max(1, values["backfill_days"])
        values["max_deck_pages"] = max(1, values["max_deck_pages"])
        raw = os.environ.get("HS_HSGURU_STREAMERS_LANGUAGES")
        values["languages"] = (
            base.languages
            if raw is None
            else tuple(
                code
                for code in (part.strip().casefold() for part in raw.split(","))
                if re.fullmatch(r"[a-z]{2}", code)
            )
        )
        return cls(**values)


# --- parsing --------------------------------------------------------------------


class PageRejected(RuntimeError):
    """The page is not a usable HSGuru or Twitch page."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _text(element: Any) -> str:
    return " ".join(part.strip() for part in element.itertext() if part.strip())


def _document(html: str) -> Any:
    try:
        return lxml_html.document_fromstring(html)
    except ValueError:
        return lxml_html.document_fromstring(html.encode("utf-8"))
    except etree.ParserError as exc:
        raise PageRejected("content_validation") from exc


def _has_class(name: str) -> str:
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {name} ')"


def _int(value: Any) -> int | None:
    digits = re.sub(r"[^\d]", "", str(value or ""))
    return int(digits) if digits else None


def _utc(value: str | None) -> str | None:
    """HSGuru ``datetime`` attributes are naive UTC; return ISO with offset."""

    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def login_from_name(name: str | None) -> str | None:
    """A Latin Twitch display name differs from the login only in case."""

    value = (name or "").strip()
    return value.casefold() if LOGIN_RE.fullmatch(value) else None


def _twitch_login(url: str | None) -> str | None:
    parsed = urlparse(url or "")
    if not parsed.netloc.endswith("twitch.tv"):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    return login_from_name(unquote(parts[0])) if parts else None


_DECK_HEADERS = {"deck", "streamer", "format", "peak", "latest", "worst", "links"}


def _deck_table(document: Any) -> tuple[Any, dict[str, int]]:
    for table in document.iter("table"):
        header = next(table.iter("thead"), None)
        if header is None:
            continue
        names = [
            re.sub(r"[^a-z]+", " ", _text(cell).casefold()).strip()
            for cell in header.iter("th")
        ]
        if _DECK_HEADERS.issubset(names) and "last played" in names:
            return table, {name: index for index, name in enumerate(names)}
    raise PageRejected("streamer_table_missing")


def _deck_code(cell: Any) -> str | None:
    for button in cell.xpath(".//*[@data-clipboard-text]"):
        for line in str(button.get("data-clipboard-text") or "").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    for hidden in cell.xpath(".//h2//*[contains(@style, 'font-size: 0')]"):
        value = _text(hidden)
        if value.startswith("AAE"):
            return value
    return None


def _player_class(cell: Any) -> str | None:
    for info in cell.xpath(f".//*[{_has_class('decklist-info')}]"):
        for token in str(info.get("class") or "").split():
            key = token.replace("-", "").upper()
            if key in CLASSES:
                return key
    return None


def _win_loss(value: str) -> tuple[int | None, int | None]:
    match = re.search(r"(\d+)\s*-\s*(\d+)", value)
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def parse_streamer_decks(html: str) -> list[dict[str, Any]]:
    """Rows of one ``/streamer-decks`` page; an intact empty page has none."""

    document = _document(html)
    table, columns = _deck_table(document)
    rows: list[dict[str, Any]] = []
    for row in (row for body in table.iter("tbody") for row in body.iter("tr")):
        cells = [cell for cell in row if cell.tag == "td"]
        if len(cells) < len(columns):
            continue
        deck_cell = cells[columns["deck"]]
        streamer_cell = cells[columns["streamer"]]
        deck_link = next(
            iter(deck_cell.xpath(".//h2//a[contains(@href, '/deck/')]")), None
        )
        streamer_link = next(
            iter(streamer_cell.xpath(".//a[contains(@href, 'twitch_id=')]")), None
        )
        played = next(iter(cells[columns["last played"]].xpath(".//time")), None)
        if deck_link is None or streamer_link is None or played is None:
            continue
        deck_id = re.search(r"/deck/(\d+)", str(deck_link.get("href")))
        twitch_id = parse_qs(urlparse(str(streamer_link.get("href"))).query).get(
            "twitch_id"
        )
        code = _deck_code(deck_cell)
        last_played = _utc(played.get("datetime"))
        format_name = FORMATS.get(_text(cells[columns["format"]]).casefold())
        if not (deck_id and twitch_id and code and last_played and format_name):
            continue
        name = _text(streamer_link)
        channel = next(
            iter(cells[columns["links"]].xpath(".//a[contains(@href, 'twitch.tv/')]")),
            None,
        )
        wins, losses = _win_loss(_text(cells[columns["win loss"]]))
        rows.append(
            {
                "streamer_id": int(twitch_id[0]),
                "streamer_name": name,
                "login": login_from_name(name),
                "twitch_url": channel.get("href") if channel is not None else None,
                "deck_id": int(deck_id.group(1)),
                "deck_code": code,
                "archetype": _text(deck_link),
                "player_class": _player_class(deck_cell),
                "format": format_name,
                "peak_rank": _int(_text(cells[columns["peak"]])),
                "latest_rank": _int(_text(cells[columns["latest"]])),
                "worst_rank": _int(_text(cells[columns["worst"]])),
                "wins": wins,
                "losses": losses,
                "last_played_at": last_played,
            }
        )
    return rows


def _duration_seconds(value: str) -> int | None:
    match = re.fullmatch(r"\s*(\d+):(\d{2}):(\d{2})\s*", value)
    if not match:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def parse_streaming_now(html: str, *, fetched_at: datetime) -> list[dict[str, Any]]:
    """Live stream cards of ``/streaming-now``; HSGuru shows the mode it knows."""

    document = _document(html)
    if not document.xpath("//*[contains(normalize-space(.), 'Streaming Now')]"):
        raise PageRejected("streaming_now_missing")
    streams: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in document.xpath(f"//*[{_has_class('live-streamer')}]"):
        link = next(iter(card.xpath(".//a[@href]")), None)
        url = str(link.get("href")) if link is not None else ""
        login = _twitch_login(url)
        name_node = next(iter(card.xpath(f".//*[{_has_class('is-twitch')}]")), None)
        if not login or login in seen:
            continue
        seen.add(login)
        title = next(iter(card.xpath(f".//*[{_has_class('title')}]")), None)
        image = next(iter(card.xpath(".//img[@src]")), None)
        viewers = duration = None
        for tag in card.xpath(f".//*[{_has_class('tag')} and {_has_class('is-info')}]"):
            text = _text(tag)
            if duration is None and _duration_seconds(text) is not None:
                duration = _duration_seconds(text)
            elif viewers is None and tag.xpath(".//p"):
                viewers = _int(text)
        mode_tag = next(
            iter(
                card.xpath(
                    f".//span[{_has_class('tag')} and contains(@style, '--color-')]"
                )
            ),
            None,
        )
        mode = _text(mode_tag) if mode_tag is not None else ""
        mode_key = re.sub(r"[^a-z]+", "_", mode.casefold()).strip("_")
        streams.append(
            {
                "login": login,
                "display_name": _text(name_node) if name_node is not None else login,
                "platform": "twitch",
                "title": _text(title) if title is not None else None,
                "viewers": viewers,
                "mode": None if mode_key in ("", "unknown") else mode_key,
                "started_at": (fetched_at - timedelta(seconds=duration)).isoformat()
                if duration is not None
                else None,
                "thumbnail_url": image.get("src") if image is not None else None,
                "stream_url": f"{TWITCH}/{login}",
            }
        )
    return streams


_SOCIAL_KINDS = (
    ("youtube", ("youtube.com", "youtu.be")),
    ("x", ("twitter.com", "x.com")),
    ("discord", ("discord.gg", "discord.com")),
    ("instagram", ("instagram.com",)),
    ("tiktok", ("tiktok.com",)),
    ("telegram", ("t.me", "telegram.me")),
    ("vk", ("vk.com",)),
    ("boosty", ("boosty.to",)),
)


def _social_kind(url: str) -> str | None:
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    if host.endswith("twitch.tv"):
        return None
    for kind, hosts in _SOCIAL_KINDS:
        if any(host == item or host.endswith("." + item) for item in hosts):
            return kind
    return "other" if host else None


def _iso_duration(value: Any) -> int | None:
    match = re.fullmatch(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(value or "").strip()
    )
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _interaction_count(value: Any) -> int | None:
    if isinstance(value, Mapping):
        return _int(value.get("userInteractionCount"))
    return None


def parse_twitch_channel(html: str) -> dict[str, Any] | None:
    """Profile and past broadcasts from a Twitch channel's JSON-LD; None if absent."""

    graph: list[Any] = []
    for block in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    ):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data.get("@graph") if isinstance(data, dict) else None
        graph.extend(items if isinstance(items, list) else [data])
    person = next(
        (
            item.get("mainEntity")
            for item in graph
            if isinstance(item, dict) and item.get("@type") == "ProfilePage"
        ),
        None,
    )
    if not isinstance(person, dict):
        return None
    login = None
    username = re.search(
        r'<meta property="og:profile:username" content="([^"]+)"', html
    )
    if username:
        login = login_from_name(username.group(1))
    url = str(person.get("url") or "")
    login = login or _twitch_login(url)
    youtube = x_url = None
    links: list[dict[str, str]] = []
    for link in person.get("sameAs") or ():
        if not isinstance(link, str) or not link.startswith("https://"):
            continue
        kind = _social_kind(link)
        if kind is None:
            continue
        if kind == "youtube" and youtube is None:
            youtube = link
        elif kind == "x" and x_url is None:
            handle = urlparse(link).path.strip("/").split("/")[0]
            x_url = f"https://x.com/{handle}" if handle else link
        else:
            links.append({"kind": kind, "url": link})
    vods: list[dict[str, Any]] = []
    for item in graph:
        if not isinstance(item, dict) or item.get("@type") != "ItemList":
            continue
        for video in item.get("itemListElement") or ():
            if not isinstance(video, dict):
                continue
            match = re.search(r"/videos/(\d+)", str(video.get("url") or ""))
            started = _utc(str(video.get("uploadDate") or ""))
            if not match or not started:
                continue
            thumbnails = video.get("thumbnailUrl") or []
            vods.append(
                {
                    "id": int(match.group(1)),
                    "title": video.get("name"),
                    "url": f"{TWITCH}/videos/{match.group(1)}",
                    "started_at": started,
                    "duration_seconds": _iso_duration(video.get("duration")),
                    "views": _interaction_count(video.get("interactionStatistic")),
                    "thumbnail_url": thumbnails[-1]
                    if isinstance(thumbnails, list) and thumbnails
                    else None,
                }
            )
    avatar = person.get("image")
    return {
        "login": login,
        "display_name": person.get("name"),
        "twitch_url": f"{TWITCH}/{login}" if login else None,
        "youtube_url": youtube,
        "x_url": x_url,
        "links": links,
        "avatar_url": avatar if isinstance(avatar, str) else None,
        "description": (str(person.get("description") or "").strip() or None),
        "followers": _interaction_count(person.get("interactionStatistic")),
        "vods": vods,
    }


# --- acquisition ----------------------------------------------------------------

HsguruFetcher = Callable[[str, str], Awaitable[tuple[str, int]]]
TwitchFetcher = Callable[[str], Awaitable[tuple[int, str]]]


def streamer_decks_url(offset: int) -> str:
    query = {"limit": PAGE_SIZE}
    if offset:
        query["offset"] = offset
    return f"{HSGURU}/streamer-decks?{urlencode(query)}"


def streaming_now_url(language: str | None = None) -> str:
    if not language:
        return f"{HSGURU}/streaming-now"
    return f"{HSGURU}/streaming-now?{urlencode({'filter_language': language})}"


def page_is_valid(kind: str, html: str) -> bool:
    try:
        if kind == "decks":
            _deck_table(_document(html))
        else:
            parse_streaming_now(html, fetched_at=datetime.now(UTC))
    except PageRejected:
        return False
    return True


@dataclass
class ScrapeDoFetcher:
    """Plain Scrape.do pages through the shared provider cascade, a few at a time."""

    executor: ThreadPoolExecutor
    semaphore: asyncio.Semaphore
    credits: int = 0
    pages: int = 0

    def _source(self, url: str) -> Source:
        return Source(
            id=PAGE_SOURCE_ID, url=url, site="hsguru", category="decks", kind="pipeline"
        )

    async def __call__(self, url: str, kind: str) -> tuple[str, int]:
        from .firecrawl_backend import _scrape_sync
        from .meta_intelligence.budget import acquisition_scope

        async with self.semaphore:
            with acquisition_scope(SOURCE_ID, job_class="detail"):
                call = functools.partial(
                    contextvars.copy_context().run,
                    _scrape_sync,
                    self._source(url),
                    formats=["html"],
                    only_main_content=True,
                    headers=None,
                    max_age_ms=0,
                    timeout_ms=SCRAPE_DO_TIMEOUT_MS,
                    skip_providers={"decodo", "firecrawl", "brightdata", "scrapfly"},
                    accept_result=lambda scraped: page_is_valid(kind, scraped.html),
                    scrape_do_profiles=SCRAPE_DO_PROFILES,
                )
                result = await asyncio.get_running_loop().run_in_executor(
                    self.executor, call
                )
        credits = int(getattr(result, "request_credits", 0) or 0)
        self.credits += credits
        self.pages += 1
        return str(result.html or ""), credits


async def fetch_twitch_page(login: str) -> tuple[int, str]:
    import httpx

    async with httpx.AsyncClient(
        timeout=TWITCH_TIMEOUT_SECONDS,
        headers={"User-Agent": TWITCH_USER_AGENT, "Accept-Language": "en-US,en"},
        follow_redirects=True,
    ) as client:
        response = await client.get(f"{TWITCH}/{quote(login)}")
        return response.status_code, response.text


# --- refresh --------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


async def _walk_decks(
    fetch: HsguruFetcher,
    *,
    cutoff: str,
    settings: Settings,
    deadline: float,
) -> dict[str, Any]:
    """Read pages from the top until the oldest row is older than ``cutoff``.

    Batches grow 1, 2, 4, ... up to the concurrency: a steady run needs one
    page, and a parallel batch would pay for pages past the cutoff.
    """

    loop = asyncio.get_running_loop()
    rows: dict[tuple[int, int, str], dict[str, Any]] = {}
    pages = 0
    complete = False
    stop_reason: str | None = None
    offset = 0
    width = 1
    while True:
        if pages >= settings.max_deck_pages:
            stop_reason = "max_deck_pages"
            break
        if loop.time() >= deadline:
            stop_reason = "deadline"
            break
        batch = [
            offset + index * PAGE_SIZE
            for index in range(
                min(width, settings.concurrency, settings.max_deck_pages - pages)
            )
        ]
        width *= 2
        results = await asyncio.gather(
            *(fetch(streamer_decks_url(item), "decks") for item in batch),
            return_exceptions=True,
        )
        reached = False
        for item, result in zip(batch, results, strict=True):
            if isinstance(result, BaseException):
                stop_reason = f"deck_page_failed:{item}"
                logger.warning(
                    "HSGuru streamer decks offset %s failed: %s",
                    item,
                    str(result)[:200],
                )
                break
            pages += 1
            page_rows = parse_streamer_decks(result[0])
            for row in page_rows:
                key = (row["streamer_id"], row["deck_id"], row["format"])
                previous = rows.get(key)
                if (
                    previous is None
                    or row["last_played_at"] > previous["last_played_at"]
                ):
                    rows[key] = row
            if len(page_rows) < PAGE_SIZE or (
                page_rows and min(row["last_played_at"] for row in page_rows) < cutoff
            ):
                reached = True
                break
        if stop_reason is not None:
            break
        if reached:
            complete = True
            break
        offset = batch[-1] + PAGE_SIZE
    return {
        "rows": list(rows.values()),
        "pages": pages,
        "complete": complete,
        "stop_reason": stop_reason,
    }


async def _read_live(
    fetch: HsguruFetcher, *, settings: Settings, fetched_at: datetime
) -> list[dict[str, Any]] | None:
    urls = [streaming_now_url()] + [
        streaming_now_url(code) for code in settings.languages
    ]
    results = await asyncio.gather(
        *(fetch(url, "live") for url in urls), return_exceptions=True
    )
    if isinstance(results[0], BaseException):
        logger.warning("HSGuru streaming-now failed: %s", str(results[0])[:200])
        return None
    streams = {
        stream["login"]: stream
        for stream in parse_streaming_now(results[0][0], fetched_at=fetched_at)
    }
    for code, result in zip(settings.languages, results[1:], strict=True):
        if isinstance(result, BaseException):
            continue
        for stream in parse_streaming_now(result[0], fetched_at=fetched_at):
            streams.setdefault(stream["login"], stream)["language"] = code
    return list(streams.values())


async def _refresh_profiles(
    conn: Any,
    fetch: TwitchFetcher,
    *,
    settings: Settings,
    now: datetime,
    deadline: float,
    pause_seconds: float = TWITCH_PAUSE_SECONDS,
    retry_seconds: float = TWITCH_RETRY_SECONDS,
) -> dict[str, int]:
    due = store.profiles_due(conn, now=now, limit=settings.profile_limit)
    semaphore = asyncio.Semaphore(settings.profile_concurrency)
    loop = asyncio.get_running_loop()

    async def read(login: str) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            status, html = await fetch(login)
        except Exception as exc:  # noqa: BLE001 - one channel must not stop the run
            return "failed", None, f"{type(exc).__name__}: {str(exc)[:120]}"
        finally:
            await asyncio.sleep(pause_seconds)
        if status == 404:
            return "not_found", None, None
        if not 200 <= status <= 299:
            return "failed", None, f"http_status_{status}"
        profile = parse_twitch_channel(html)
        # An unknown channel and a throttled read look alike (200, app shell).
        if profile is None:
            return "failed", None, "profile_data_missing"
        return "ok", profile, None

    async def one(
        streamer: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        if loop.time() >= deadline:
            return "skipped", None, None
        async with semaphore:
            result = await read(str(streamer["login"]))
            if result[2] == "profile_data_missing":
                await asyncio.sleep(retry_seconds)
                result = await read(str(streamer["login"]))
        return result

    results = await asyncio.gather(*(one(streamer) for streamer in due))
    checked_at = _now().isoformat()
    attempted = ok = 0
    for streamer, (state, profile, error) in zip(due, results, strict=True):
        if state == "skipped":
            continue
        attempted += 1
        ok += state == "ok"
        store.record_profile(
            conn,
            int(streamer["id"]),
            profile,
            checked_at=checked_at,
            state=state,
            error=error,
        )
    return {"attempted": attempted, "ok": ok, "due": len(due)}


async def _refresh_unlocked(
    *,
    settings: Settings,
    fetch_hsguru: HsguruFetcher | None,
    fetch_twitch: TwitchFetcher | None,
    now: datetime | None,
    profiles: bool,
) -> dict[str, Any]:
    started = now or _now()
    started_at = started.isoformat()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.deadline_seconds
    executor = ThreadPoolExecutor(
        max_workers=settings.concurrency, thread_name_prefix="hsguru-streamers"
    )
    scrape_do = ScrapeDoFetcher(executor, asyncio.Semaphore(settings.concurrency))
    fetch = fetch_hsguru or scrape_do
    init_db()
    conn = get_db_connection()
    try:
        run_id = store.start_run(conn, started_at=started_at)
        watermark = store.deck_watermark(conn)
        cutoff = (
            datetime.fromisoformat(watermark)
            - timedelta(minutes=settings.overlap_minutes)
            if watermark
            else started - timedelta(days=settings.backfill_days)
        ).isoformat()
        live_task = asyncio.create_task(
            _read_live(fetch, settings=settings, fetched_at=started)
        )
        walk = await _walk_decks(
            fetch, cutoff=cutoff, settings=settings, deadline=deadline
        )
        live = await live_task
        deck_counts = store.upsert_decks(conn, walk["rows"], seen_at=_now().isoformat())
        live_count = (
            store.replace_live(conn, run_id, live, observed_at=started_at)
            if live is not None
            else None
        )
        profile_counts = (
            await _refresh_profiles(
                conn,
                fetch_twitch or fetch_twitch_page,
                settings=settings,
                now=started,
                deadline=deadline,
                pause_seconds=TWITCH_PAUSE_SECONDS if fetch_twitch is None else 0,
                retry_seconds=TWITCH_RETRY_SECONDS if fetch_twitch is None else 0,
            )
            if profiles
            else {"attempted": 0, "ok": 0, "due": 0}
        )
        pruned = store.prune(conn, now=started)
        finished_at = _now().isoformat()
        failed_parts = [
            name
            for name, failed in (
                ("live", live is None),
                ("decks", walk["stop_reason"] is not None and walk["pages"] == 0),
            )
            if failed
        ]
        state = SourceState.FETCH_ERROR if len(failed_parts) == 2 else SourceState.OK
        summary = {
            "finished_at": finished_at,
            "state": "ok"
            if not failed_parts
            else "partial"
            if state == SourceState.OK
            else "failed",
            "live_streams": live_count,
            "deck_pages": walk["pages"],
            "deck_rows": len(walk["rows"]),
            "decks_new": deck_counts["new"],
            "decks_updated": deck_counts["updated"],
            "decks_cutoff": cutoff,
            "decks_complete": int(walk["complete"]),
            "profiles_attempted": profile_counts["attempted"],
            "profiles_ok": profile_counts["ok"],
            "scrape_do_credits": scrape_do.credits,
            "stop_reason": walk["stop_reason"]
            or ("live_failed" if live is None else None),
            "detail": {
                "failed_parts": failed_parts,
                "profiles_due": profile_counts["due"],
                "pruned": pruned,
                "watermark": watermark,
            },
        }
        store.finish_run(conn, run_id, summary)
        totals = dict(
            conn.execute(
                f"""
                SELECT (SELECT COUNT(*) FROM {store.STREAMERS}) AS streamers,
                       (SELECT COUNT(*) FROM {store.DECKS}) AS decks,
                       (SELECT COUNT(*) FROM {store.LIVE}) AS live,
                       (SELECT COUNT(*) FROM {store.STREAMERS}
                        WHERE profile_state = 'ok') AS profiles
                """
            ).fetchone()
        )
    finally:
        conn.close()
        executor.shutdown(wait=False, cancel_futures=True)

    published = state == SourceState.OK
    if published:
        save_dataset(
            SOURCE_ID,
            {
                "state": SourceState.OK,
                "fetched_at": finished_at,
                "http_status": 200,
                "final_url": f"{HSGURU}/streamer-decks",
                "backend": "hsguru_streamers_store",
                "data": {
                    "structured": {
                        "type": SOURCE_ID,
                        "fetched_at": finished_at,
                        **totals,
                        "last_run": {**summary, "started_at": started_at},
                    }
                },
            },
        )
    previous = load_dataset(SOURCE_ID) or {}
    status = {
        "source_id": SOURCE_ID,
        "site": "hsguru",
        "category": "decks",
        "url": f"{HSGURU}/streamer-decks",
        "state": str(state),
        "fetched_at": finished_at
        if published
        else previous.get("fetched_at") or finished_at,
        "http_status": 200 if published else None,
        "backend": "scrape_do",
        "rows_total": totals["decks"],
        "detail": (
            f"HSGuru streamers: {totals['streamers']} streamers, {totals['decks']} decks, "
            f"{live_count if live_count is not None else 'no'} live; this run "
            f"{walk['pages']} deck pages (+{deck_counts['new']} new, "
            f"{deck_counts['updated']} updated, complete={walk['complete']}), "
            f"{profile_counts['ok']}/{profile_counts['attempted']} Twitch profiles, "
            f"scrape.do credits {scrape_do.credits}"
        ),
        "serving_cached_dataset": not published and bool(previous),
        "last_refresh_state": str(state),
        "last_refresh_at": finished_at,
    }
    if failed_parts:
        status["last_refresh_failure_class"] = str(SourceState.FETCH_ERROR)
    save_status(SOURCE_ID, status)
    return {
        "ok": not failed_parts,
        "state": str(state),
        "published": published,
        "source_id": SOURCE_ID,
        "fetched_at": finished_at,
        **totals,
        **{
            key: summary[key] for key in summary if key not in ("finished_at", "detail")
        },
        "failed_parts": failed_parts,
    }


async def refresh_hsguru_streamers(
    *,
    settings: Settings | None = None,
    fetch_hsguru: HsguruFetcher | None = None,
    fetch_twitch: TwitchFetcher | None = None,
    now: datetime | None = None,
    profiles: bool = True,
) -> dict[str, Any]:
    """Read who is live, new streamer decks and due Twitch profiles."""

    locks = ResourceLockSet(
        [SOURCE_ID], metadata={"operation": "refresh_hsguru_streamers"}
    )
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {
            "ok": True,
            "published": False,
            "source_id": SOURCE_ID,
            **exc.as_outcome(),
        }
    try:
        return await _refresh_unlocked(
            settings=settings or Settings.from_env(),
            fetch_hsguru=fetch_hsguru,
            fetch_twitch=fetch_twitch,
            now=now,
            profiles=profiles,
        )
    finally:
        locks.release()


# --- reads ----------------------------------------------------------------------
#
# Every query is written once with ``:name`` parameters and ``{table}``
# placeholders. REST runs it on SQLite; GraphQL runs it on the PostgreSQL
# mirror (``analytics.*``), whose repository turns ``:name`` into ``%(name)s``.
# Timestamps are ISO-8601 UTC text in both, so they compare as strings.

Fetch = Callable[[str, dict[str, Any]], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class Tables:
    streamers: str
    decks: str
    vods: str
    live: str
    runs: str


SQLITE_TABLES = Tables(*store.TABLES)
POSTGRES_TABLES = Tables(*(f"analytics.{name}" for name in store.TABLES))
SORTS = ("active", "followers", "decks", "rank")


def _sqlite_all(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    init_db()
    conn = get_db_connection()
    try:
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


async def sqlite_fetch(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_sqlite_all, sql, params)


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _in(name: str, values: Sequence[Any], params: dict[str, Any]) -> str:
    keys = []
    for index, value in enumerate(values):
        key = f"{name}_{index}"
        params[key] = value
        keys.append(f":{key}")
    return f"({', '.join(keys) or 'NULL'})"


def streamer_slug(row: Mapping[str, Any]) -> str:
    return str(row.get("login") or row["id"])


def _since(days: int | None, now: datetime | None) -> str | None:
    if not days:
        return None
    return ((now or _now()) - timedelta(days=days)).isoformat()


def _deck_filters(
    params: dict[str, Any],
    *,
    alias: str = "d",
    format_name: str | None = None,
    player_class: str | None = None,
    archetype: str | None = None,
    max_rank: int | None = None,
    since: str | None = None,
) -> str:
    clauses = []
    if format_name:
        params["format"] = format_name
        clauses.append(f"{alias}.format = :format")
    if player_class:
        params["player_class"] = player_class
        clauses.append(f"{alias}.player_class = :player_class")
    if archetype:
        params["archetype"] = archetype.casefold()
        clauses.append(f"lower({alias}.archetype) = :archetype")
    if max_rank:
        params["max_rank"] = max_rank
        clauses.append(f"{alias}.peak_rank <= :max_rank")
    if since:
        params["since"] = since
        clauses.append(f"{alias}.last_played_at >= :since")
    return " AND ".join(clauses) or "1 = 1"


def _public_vod(vod: Mapping[str, Any], played_at: str) -> dict[str, Any]:
    offset = (
        datetime.fromisoformat(played_at)
        - datetime.fromisoformat(str(vod["started_at"]))
    ).total_seconds()
    seconds = max(0, int(offset) - VOD_LEAD_SECONDS)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return {
        "url": f"{vod['url']}?t={hours}h{minutes}m{secs}s",
        "title": vod.get("title"),
        "started_at": vod["started_at"],
        "offset_seconds": seconds,
    }


def _vod_for(
    played_at: str, vods: Iterable[Mapping[str, Any]]
) -> dict[str, Any] | None:
    for vod in vods:
        duration = vod.get("duration_seconds")
        start = str(vod["started_at"])
        if not duration or start > played_at:
            continue
        end = (
            datetime.fromisoformat(start) + timedelta(seconds=int(duration))
        ).isoformat()
        if played_at <= end:
            return _public_vod(vod, played_at)
    return None


def public_deck(
    row: Mapping[str, Any],
    *,
    vods: Iterable[Mapping[str, Any]] = (),
    live: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    played = str(row["last_played_at"])
    wins, losses = row.get("wins"), row.get("losses")
    games = (
        (wins or 0) + (losses or 0) if wins is not None or losses is not None else None
    )
    on_stream = bool(
        live and live.get("started_at") and played >= str(live["started_at"])
    )
    return {
        "deck_id": int(row["deck_id"]),
        "deck_code": row["deck_code"],
        "archetype": row["archetype"],
        "player_class": row.get("player_class"),
        "format": row["format"],
        "peak_rank": row.get("peak_rank"),
        "latest_rank": row.get("latest_rank"),
        "worst_rank": row.get("worst_rank"),
        "wins": wins,
        "losses": losses,
        "games": games,
        "winrate": round(100 * (wins or 0) / games, 1) if games else None,
        "last_played_at": played,
        "first_seen_at": row.get("first_seen_at"),
        "hsguru_url": f"{HSGURU}/deck/{int(row['deck_id'])}",
        "on_current_stream": on_stream,
        "stream_url": live.get("stream_url") if on_stream and live else None,
        "vod": None if on_stream else _vod_for(played, vods),
    }


def _public_live(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "platform": row["platform"],
        "title": row.get("title"),
        "viewers": row.get("viewers"),
        "mode": row.get("mode"),
        "language": row.get("language"),
        "started_at": row.get("started_at"),
        "thumbnail_url": row.get("thumbnail_url"),
        "stream_url": row["stream_url"],
        "observed_at": row["observed_at"],
    }


def public_streamer(
    row: Mapping[str, Any], *, live: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    login = row.get("login")
    links = _json_value(row.get("links_json")) or []
    return {
        "id": int(row["id"]),
        "slug": streamer_slug(row),
        "display_name": row["display_name"],
        "login": login,
        "twitch_url": f"{TWITCH}/{login}" if login else row.get("twitch_url"),
        "youtube_url": row.get("youtube_url"),
        "x_url": row.get("x_url"),
        "links": links if isinstance(links, list) else [],
        "avatar_url": row.get("avatar_url"),
        "description": row.get("description"),
        "followers": row.get("followers"),
        "language": row.get("language"),
        "decks_total": int(row.get("decks_total") or 0),
        "last_played_at": row.get("last_played_at"),
        "profile_state": row.get("profile_state"),
        "profile_checked_at": row.get("profile_checked_at"),
        "hsguru_url": f"{HSGURU}/streamer-decks?twitch_id={int(row['id'])}",
        "live": _public_live(live),
    }


async def _live_by_streamer(
    fetch: Fetch, tables: Tables, ids: Sequence[int]
) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    params: dict[str, Any] = {}
    rows = await fetch(
        f"SELECT * FROM {tables.live} WHERE streamer_id IN {_in('id', ids, params)}",
        params,
    )
    return {int(row["streamer_id"]): row for row in rows}


async def _vods_by_streamer(
    fetch: Fetch, tables: Tables, ids: Sequence[int], since: str | None
) -> dict[int, list[dict[str, Any]]]:
    if not ids:
        return {}
    params: dict[str, Any] = {}
    clause = ""
    if since:
        params["vod_since"] = since
        clause = "AND started_at >= :vod_since"
    rows = await fetch(
        f"""
        SELECT * FROM {tables.vods}
        WHERE streamer_id IN {_in("id", ids, params)} {clause}
        ORDER BY started_at DESC
        """,
        params,
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["streamer_id"]), []).append(row)
    return grouped


def _vod_since(decks: Iterable[Mapping[str, Any]]) -> str | None:
    oldest = min((str(row["last_played_at"]) for row in decks), default=None)
    if oldest is None:
        return None
    return (datetime.fromisoformat(oldest) - timedelta(days=2)).isoformat()


async def _decorate_decks(
    fetch: Fetch,
    tables: Tables,
    decks: Sequence[Mapping[str, Any]],
    live: Mapping[int, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    ids = sorted({int(row["streamer_id"]) for row in decks})
    if live is None:
        live = await _live_by_streamer(fetch, tables, ids)
    vods = await _vods_by_streamer(fetch, tables, ids, _vod_since(decks))
    return [
        public_deck(
            row,
            vods=vods.get(int(row["streamer_id"]), ()),
            live=live.get(int(row["streamer_id"])),
        )
        for row in decks
    ]


async def list_streamers(
    fetch: Fetch,
    tables: Tables = SQLITE_TABLES,
    *,
    q: str | None = None,
    format_name: str | None = None,
    player_class: str | None = None,
    language: str | None = None,
    live_only: bool = False,
    days: int | None = 30,
    sort: str = "active",
    limit: int = 24,
    offset: int = 0,
    decks_per_streamer: int = 3,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Streamers with decks in the window, live ones first, with their last decks."""

    params: dict[str, Any] = {"limit": limit, "offset": offset}
    deck_where = _deck_filters(
        params,
        format_name=format_name,
        player_class=player_class,
        since=_since(days, now),
    )
    where = ["1 = 1"]
    if q:
        params["q"] = f"%{q.strip().casefold()}%"
        where.append("(lower(s.display_name) LIKE :q OR lower(s.login) LIKE :q)")
    if language:
        params["language"] = language
        where.append("s.language = :language")
    if live_only:
        where.append("l.streamer_id IS NOT NULL")
    order = {
        "active": "(l.streamer_id IS NULL), f.played_at DESC",
        "followers": "s.followers DESC NULLS LAST, f.played_at DESC",
        "decks": "f.decks DESC, f.played_at DESC",
        "rank": "f.best_rank ASC NULLS LAST, f.played_at DESC",
    }[sort]
    base = f"""
        FROM {tables.streamers} AS s
        JOIN (
            SELECT d.streamer_id, MAX(d.last_played_at) AS played_at,
                   COUNT(*) AS decks, MIN(d.peak_rank) AS best_rank
            FROM {tables.decks} AS d WHERE {deck_where}
            GROUP BY d.streamer_id
        ) AS f ON f.streamer_id = s.id
        LEFT JOIN (
            SELECT DISTINCT streamer_id FROM {tables.live} WHERE streamer_id IS NOT NULL
        ) AS l ON l.streamer_id = s.id
        WHERE {" AND ".join(where)}
    """
    rows = await fetch(
        f"""
        SELECT s.*, f.played_at AS window_played_at, f.decks AS window_decks,
               f.best_rank AS window_best_rank
        {base}
        ORDER BY {order}, s.id
        LIMIT :limit OFFSET :offset
        """,
        params,
    )
    total_rows = await fetch(f"SELECT COUNT(*) AS total {base}", params)
    ids = [int(row["id"]) for row in rows]
    live = await _live_by_streamer(fetch, tables, ids)
    recent: dict[int, list[dict[str, Any]]] = {}
    if ids and decks_per_streamer > 0:
        deck_params: dict[str, Any] = {"per_streamer": decks_per_streamer}
        filters = _deck_filters(
            deck_params, format_name=format_name, player_class=player_class
        )
        decks = await fetch(
            f"""
            SELECT * FROM (
                SELECT d.*, ROW_NUMBER() OVER (
                    PARTITION BY d.streamer_id ORDER BY d.last_played_at DESC, d.id DESC
                ) AS rn
                FROM {tables.decks} AS d
                WHERE d.streamer_id IN {_in("id", ids, deck_params)} AND {filters}
            ) AS ranked
            WHERE rn <= :per_streamer
            ORDER BY streamer_id, rn
            """,
            deck_params,
        )
        decorated = await _decorate_decks(fetch, tables, decks, live)
        for row, deck in zip(decks, decorated, strict=True):
            recent.setdefault(int(row["streamer_id"]), []).append(deck)
    items = []
    for row in rows:
        streamer = public_streamer(row, live=live.get(int(row["id"])))
        streamer["window"] = {
            "decks": int(row["window_decks"] or 0),
            "best_rank": row.get("window_best_rank"),
            "last_played_at": row.get("window_played_at"),
        }
        streamer["recent_decks"] = recent.get(int(row["id"]), [])
        items.append(streamer)
    return {
        "items": items,
        "total": int(total_rows[0]["total"]) if total_rows else 0,
        "limit": limit,
        "offset": offset,
    }


async def find_streamer(
    fetch: Fetch, tables: Tables, key: str
) -> dict[str, Any] | None:
    value = key.strip()
    if not value:
        return None
    if value.isdigit():
        rows = await fetch(
            f"SELECT * FROM {tables.streamers} WHERE id = :id", {"id": int(value)}
        )
    else:
        rows = await fetch(
            f"""
            SELECT * FROM {tables.streamers}
            WHERE login = :key OR lower(display_name) = :key
            ORDER BY last_played_at DESC NULLS LAST LIMIT 1
            """,
            {"key": value.casefold()},
        )
    return rows[0] if rows else None


async def get_streamer(
    fetch: Fetch, tables: Tables = SQLITE_TABLES, *, streamer: str
) -> dict[str, Any] | None:
    """One streamer: profile, live stream, deck counts by format and class, VODs."""

    row = await find_streamer(fetch, tables, streamer)
    if row is None:
        return None
    streamer_id = int(row["id"])
    live = await _live_by_streamer(fetch, tables, [streamer_id])
    facets = await fetch(
        f"""
        SELECT format, player_class, COUNT(*) AS decks, MIN(peak_rank) AS best_rank,
               MAX(last_played_at) AS last_played_at
        FROM {tables.decks} WHERE streamer_id = :id
        GROUP BY format, player_class
        ORDER BY format, player_class
        """,
        {"id": streamer_id},
    )
    vods = await fetch(
        f"""
        SELECT * FROM {tables.vods} WHERE streamer_id = :id
        ORDER BY started_at DESC LIMIT 10
        """,
        {"id": streamer_id},
    )
    current = await fetch(
        f"""
        SELECT * FROM {tables.decks} WHERE streamer_id = :id
        ORDER BY last_played_at DESC, id DESC LIMIT 1
        """,
        {"id": streamer_id},
    )
    data = public_streamer(row, live=live.get(streamer_id))
    data["facets"] = [
        {
            "format": facet["format"],
            "player_class": facet.get("player_class"),
            "decks": int(facet["decks"]),
            "best_rank": facet.get("best_rank"),
            "last_played_at": facet.get("last_played_at"),
        }
        for facet in facets
    ]
    data["vods"] = [
        {
            "url": vod["url"],
            "title": vod.get("title"),
            "started_at": vod["started_at"],
            "duration_seconds": vod.get("duration_seconds"),
            "views": vod.get("views"),
            "thumbnail_url": vod.get("thumbnail_url"),
        }
        for vod in vods
    ]
    data["last_deck"] = (
        public_deck(current[0], vods=vods, live=live.get(streamer_id))
        if current
        else None
    )
    return data


async def streamer_decks(
    fetch: Fetch,
    tables: Tables = SQLITE_TABLES,
    *,
    streamer: str,
    format_name: str | None = None,
    player_class: str | None = None,
    days: int | None = None,
    limit: int = 50,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Decks of one streamer, newest game first."""

    row = await find_streamer(fetch, tables, streamer)
    if row is None:
        return None
    params: dict[str, Any] = {"id": int(row["id"]), "limit": limit, "offset": offset}
    filters = _deck_filters(
        params,
        format_name=format_name,
        player_class=player_class,
        since=_since(days, now),
    )
    decks = await fetch(
        f"""
        SELECT * FROM {tables.decks} AS d WHERE d.streamer_id = :id AND {filters}
        ORDER BY d.last_played_at DESC, d.id DESC
        LIMIT :limit OFFSET :offset
        """,
        params,
    )
    total = await fetch(
        f"SELECT COUNT(*) AS total FROM {tables.decks} AS d "
        f"WHERE d.streamer_id = :id AND {filters}",
        params,
    )
    return {
        "streamer": public_streamer(row),
        "items": await _decorate_decks(fetch, tables, decks),
        "total": int(total[0]["total"]) if total else 0,
        "limit": limit,
        "offset": offset,
    }


async def recent_decks(
    fetch: Fetch,
    tables: Tables = SQLITE_TABLES,
    *,
    format_name: str | None = None,
    player_class: str | None = None,
    archetype: str | None = None,
    max_rank: int | None = None,
    days: int | None = 7,
    limit: int = 50,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Streamer decks of every streamer, newest game first."""

    params: dict[str, Any] = {"limit": limit, "offset": offset}
    filters = _deck_filters(
        params,
        format_name=format_name,
        player_class=player_class,
        archetype=archetype,
        max_rank=max_rank,
        since=_since(days, now),
    )
    decks = await fetch(
        f"""
        SELECT d.*, s.display_name, s.login, s.avatar_url
        FROM {tables.decks} AS d JOIN {tables.streamers} AS s ON s.id = d.streamer_id
        WHERE {filters}
        ORDER BY d.last_played_at DESC, d.id DESC
        LIMIT :limit OFFSET :offset
        """,
        params,
    )
    total = await fetch(
        f"SELECT COUNT(*) AS total FROM {tables.decks} AS d WHERE {filters}", params
    )
    decorated = await _decorate_decks(fetch, tables, decks)
    items = []
    for row, deck in zip(decks, decorated, strict=True):
        deck["streamer"] = {
            "id": int(row["streamer_id"]),
            "slug": str(row.get("login") or row["streamer_id"]),
            "display_name": row["display_name"],
            "avatar_url": row.get("avatar_url"),
        }
        items.append(deck)
    return {
        "items": items,
        "total": int(total[0]["total"]) if total else 0,
        "limit": limit,
        "offset": offset,
    }


async def streaming_now(
    fetch: Fetch,
    tables: Tables = SQLITE_TABLES,
    *,
    language: str | None = None,
    mode: str | None = None,
    known_only: bool = False,
    limit: int = 100,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Live Hearthstone streams with the deck played on the current broadcast.

    HSGuru reports a mode for few streams; when it does not, a deck played
    during this broadcast gives its format (``mode_source`` ``deck``).
    """

    params: dict[str, Any] = {"limit": limit}
    where = ["1 = 1"]
    if language:
        params["language"] = language
        where.append("l.language = :language")
    if known_only:
        where.append("l.streamer_id IS NOT NULL")
    streams = await fetch(
        f"""
        SELECT l.*, s.id AS known_id, s.display_name AS known_name, s.login AS known_login,
               s.avatar_url, s.youtube_url, s.x_url, s.links_json, s.followers,
               s.language AS streamer_language, s.decks_total, s.last_played_at,
               s.description, s.twitch_url, s.profile_state, s.profile_checked_at
        FROM {tables.live} AS l LEFT JOIN {tables.streamers} AS s ON s.id = l.streamer_id
        WHERE {" AND ".join(where)}
        ORDER BY l.viewers DESC NULLS LAST, l.id
        """,
        params,
    )
    ids = sorted({int(row["streamer_id"]) for row in streams if row.get("streamer_id")})
    current: dict[int, dict[str, Any]] = {}
    if ids:
        deck_params: dict[str, Any] = {}
        decks = await fetch(
            f"""
            SELECT * FROM (
                SELECT d.*, ROW_NUMBER() OVER (
                    PARTITION BY d.streamer_id ORDER BY d.last_played_at DESC, d.id DESC
                ) AS rn
                FROM {tables.decks} AS d
                WHERE d.streamer_id IN {_in("id", ids, deck_params)}
            ) AS ranked WHERE rn = 1
            """,
            deck_params,
        )
        current = {int(row["streamer_id"]): row for row in decks}
    observed = max((str(row["observed_at"]) for row in streams), default=None)
    items = []
    for row in streams:
        live = _public_live(row)
        streamer_id = row.get("streamer_id")
        deck_row = current.get(int(streamer_id)) if streamer_id else None
        deck = public_deck(deck_row, live=row) if deck_row else None
        if deck and not deck["on_current_stream"]:
            deck = None
        stream_mode, mode_source = (
            row.get("mode"),
            "hsguru" if row.get("mode") else None,
        )
        if stream_mode is None and deck:
            stream_mode, mode_source = deck["format"], "deck"
        if mode and stream_mode != mode:
            continue
        streamer = None
        if row.get("known_id"):
            streamer = public_streamer(
                {
                    "id": row["known_id"],
                    "display_name": row["known_name"],
                    "login": row.get("known_login"),
                    "twitch_url": row.get("twitch_url"),
                    "youtube_url": row.get("youtube_url"),
                    "x_url": row.get("x_url"),
                    "links_json": row.get("links_json"),
                    "avatar_url": row.get("avatar_url"),
                    "description": row.get("description"),
                    "followers": row.get("followers"),
                    "language": row.get("streamer_language"),
                    "decks_total": row.get("decks_total"),
                    "last_played_at": row.get("last_played_at"),
                    "profile_state": row.get("profile_state"),
                    "profile_checked_at": row.get("profile_checked_at"),
                }
            )
        items.append(
            {
                **(live or {}),
                "login": row["login"],
                "display_name": row["display_name"],
                "mode": stream_mode,
                "mode_source": mode_source,
                "streamer": streamer,
                "current_deck": deck,
            }
        )
        if len(items) >= limit:
            break
    stale = (
        observed is None
        or observed
        < ((now or _now()) - timedelta(minutes=LIVE_STALE_MINUTES)).isoformat()
    )
    return {
        "items": items,
        "total": len(items),
        "observed_at": observed,
        "stale": stale,
    }


def latest_revision() -> str | None:
    """Finish time of the last run, for public cache validators."""

    try:
        rows = _sqlite_all(
            f"SELECT MAX(finished_at) AS finished_at FROM {store.RUNS}", {}
        )
    except Exception:  # noqa: BLE001 - a cache key must not fail the request
        return None
    return str(rows[0]["finished_at"]) if rows and rows[0]["finished_at"] else None


def coverage_report(*, runs: int = 10) -> dict[str, Any]:
    init_db()
    conn = get_db_connection()
    try:
        totals = dict(
            conn.execute(
                f"""
                SELECT (SELECT COUNT(*) FROM {store.STREAMERS}) AS streamers,
                       (SELECT COUNT(*) FROM {store.STREAMERS}
                        WHERE profile_state = 'ok') AS profiles,
                       (SELECT COUNT(*) FROM {store.DECKS}) AS decks,
                       (SELECT MIN(last_played_at) FROM {store.DECKS}) AS oldest_deck,
                       (SELECT MAX(last_played_at) FROM {store.DECKS}) AS newest_deck,
                       (SELECT COUNT(*) FROM {store.VODS}) AS vods,
                       (SELECT COUNT(*) FROM {store.LIVE}) AS live
                """
            ).fetchone()
        )
        recent = store.recent_runs(conn, limit=runs)
    finally:
        conn.close()
    for run in recent:
        run["detail"] = _json_value(run.pop("detail_json", None)) or {}
    return {**totals, "runs": recent}
