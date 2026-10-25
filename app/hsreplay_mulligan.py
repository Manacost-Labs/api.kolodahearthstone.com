"""HSReplay deck mulligan guide (mulligan v2) for the top decks of every rank.

HSReplay's deck page reads ``/api/v1/mulligan/?shortid=<deck>`` with
``game_type_filter``, ``league_rank_range``, ``player_initiative`` (ALL, FIRST,
COIN) and ``time_range``. Per card it gives the keep rate, the winrate when the
card ended up in the opening hand, when drawn and when played, turns held and
the turn played. With a premium session one answer also splits by each of the
eleven opponent classes; anonymously only Bronze-Gold ``ALL`` is readable.

The deck pool is the top of ``list_decks_by_win_rate_v2`` for each format and
rank: the most played decks plus the best winrate among decks with a solid
sample. Every deck, initiative and time range is a *target* with incremental
state (see :mod:`app.hsreplay_mulligan_store`); a target is due when it was
never read or its TTL expired, and each run reads due targets in tier order up
to a per-run and a per-UTC-day Scrape.do credit cap.

Requests go through Scrape.do without rendering (1 credit each) with the
project's HSReplay session cookie, at most five at a time: the account allows
50 concurrent requests and the HSGuru mulligan uses 40 of them. Each run first
checks the session on ``/api/v1/account/``. When HSReplay refuses it (401/403,
which Scrape.do reports as 502 after rotating) or the account lost premium,
premium slices are skipped, the source turns ``partial``, a Telegram alert goes
out and the API answers premium slices with an explicit session status instead
of an empty result.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from . import cards_index, provider_usage
from . import hsreplay_mulligan_store as store
from .db import get_db_connection, init_db
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .storage import load_dataset, save_dataset, save_status

logger = logging.getLogger(__name__)

SOURCE_ID = "hsreplay_mulligan"
HSREPLAY = "https://hsreplay.net"
MULLIGAN_API = f"{HSREPLAY}/api/v1/mulligan/"
ACCOUNT_API = f"{HSREPLAY}/api/v1/account/"
DECK_LIST_API = f"{HSREPLAY}/analytics/query/list_decks_by_win_rate_v2/"
# HSReplay ranks the deck lists over 30 days; mulligan slices use their own window.
DECK_LIST_TIME_RANGE = "LAST_30_DAYS"

FORMATS = {"standard": "RANKED_STANDARD", "wild": "RANKED_WILD"}
RANKS = {
    "legend": "LEGEND",
    "diamond_4to1": "DIAMOND_FOUR_THROUGH_DIAMOND_ONE",
    "diamond": "DIAMOND",
    "bronze_gold": "BRONZE_THROUGH_GOLD",
}
INITIATIVES = {"all": "ALL", "first": "FIRST", "coin": "COIN"}
PERIODS = {
    "last_30_days": "LAST_30_DAYS",
    "current_patch": "CURRENT_PATCH",
    "current_expansion": "CURRENT_EXPANSION",
}
ANY_OPPONENT = "any"
OPPONENT_CLASSES = (
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
# Verified 2026-10-25: anonymous Bronze-Gold ALL answers 200 with only the ALL
# group; every other rank or initiative answers 403 without premium.
FREE_RANK = "bronze_gold"
FREE_INITIATIVE = "all"

# Read order: the ranks people open most first; Wild after Standard.
TIERS = {
    ("standard", "legend"): 1,
    ("standard", "diamond_4to1"): 1,
    ("standard", "diamond"): 2,
    ("standard", "bronze_gold"): 2,
}
WILD_TIER = 3
TIER_TTL_FACTOR = {1: 1, 2: 2, 3: 3}
# The current patch moves fastest; the expansion window only grows.
PERIOD_TTL_HOURS = {"current_patch": 48, "last_30_days": 72, "current_expansion": 168}
PERIOD_PRIORITY = {"current_patch": 1, "last_30_days": 2, "current_expansion": 3}
DECK_LIST_TTL_HOURS = 24
# An unsupported filter (HTTP 400/404) or a rejected session waits this long.
UNSUPPORTED_RETRY_HOURS = 168
PREMIUM_DENIED_RETRY_HOURS = 6
# Consecutive premium refusals after which the session counts as rejected
# even when the account probe looked fine.
PREMIUM_DENIAL_THRESHOLD = 3
RUN_CIRCUIT_THRESHOLD = 10
MAX_CONCURRENCY = 5
SCRAPE_DO_TIMEOUT_MS = 60_000
# HSReplay computes non-default time ranges asynchronously and answers HTTP
# 202 meanwhile; on 2026-10-25 a cold CURRENT_PATCH deck query still answered
# 202 after six minutes, so a same-run re-poll only burns credits. A slice
# that keeps answering 202 backs off 1, 2, 4 ... 24 hours (``_retry_after``)
# so it cannot eat every run's budget.
PREMIUM_ALERT_STATE = "premium_auth_failed"
PREMIUM_BAD_STATES = frozenset({"missing_session", "not_premium", "denied"})


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
    # Deck pool per rank: the most played decks plus the best winrates.
    standard_popular_decks: int = 10
    standard_winrate_decks: int = 2
    wild_popular_decks: int = 5
    wild_winrate_decks: int = 1
    deck_min_games: int = 200
    winrate_min_games: int = 1000
    # Scrape.do: 50 concurrent requests per account, 40 belong to the HSGuru
    # mulligan, so this collector stays at five or fewer.
    concurrency: int = 5
    # The default plan needs ~130 requests a day by TTL plus 8 deck lists
    # and one session probe per run (see ``estimate_plan``).
    scrape_do_run_credits: int = 60
    scrape_do_daily_credits: int = 160
    deadline_seconds: int = 40 * 60
    snapshot_days: int = 30

    @classmethod
    def from_env(cls) -> Settings:
        base = cls()
        values: dict[str, Any] = {
            name: _env_int(f"HS_HSREPLAY_MULLIGAN_{name.upper()}", getattr(base, name))
            for name in cls.__dataclass_fields__
        }
        values["concurrency"] = max(1, min(values["concurrency"], MAX_CONCURRENCY))
        values["snapshot_days"] = max(1, values["snapshot_days"])
        return cls(**values)


# --- urls -----------------------------------------------------------------------


def mulligan_api_url(target: Mapping[str, Any]) -> str:
    return f"{MULLIGAN_API}?" + urlencode(
        {
            "shortid": target["deck_id"],
            "game_type_filter": FORMATS[str(target["format"])],
            "league_rank_range": RANKS[str(target["rank"])],
            "player_initiative": INITIATIVES[str(target["initiative"])],
            "time_range": PERIODS[str(target["period"])],
        }
    )


def deck_page_url(target: Mapping[str, Any]) -> str:
    """HSReplay's public deck page opened on the same mulligan filters."""

    fragment = urlencode(
        {
            "gameType": FORMATS[str(target["format"])],
            "rankRange": RANKS[str(target["rank"])],
            "timeRange": PERIODS[str(target["period"])],
            "playerInitiative": INITIATIVES[str(target["initiative"])],
            "tab": "mulligan-guide",
        }
    )
    return f"{HSREPLAY}/decks/{target['deck_id']}/#{fragment}"


def deck_list_url(format_name: str, rank: str) -> str:
    return f"{DECK_LIST_API}?" + urlencode(
        {
            "GameType": FORMATS[format_name],
            "LeagueRankRange": RANKS[rank],
            "TimeRange": DECK_LIST_TIME_RANGE,
        }
    )


def is_premium_slice(rank: str, initiative: str) -> bool:
    return not (rank == FREE_RANK and initiative == FREE_INITIATIVE)


# --- parsing --------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


class PayloadRejected(ValueError):
    """The body is JSON but not a mulligan answer (contract drift or an error page)."""


def parse_mulligan_payload(
    payload: Any, *, cards_by_dbf: Mapping[int, Mapping[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Normalize one ``/api/v1/mulligan/`` answer into opponent-class groups.

    The answer maps ``ALL`` and, with premium, each class key to
    ``{query_score_scope, cards_by_dbf_id: [...], base_winrate, num_games,
    as_of, metadata}``. ``query_score_scope`` tells whether HSReplay had
    enough games of this exact deck (``deck``) or fell back to the archetype
    or class.
    """

    if not isinstance(payload, dict) or not isinstance(payload.get("ALL"), dict):
        raise PayloadRejected("mulligan payload has no ALL group")
    lookup = cards_by_dbf if cards_by_dbf is not None else {}
    groups: list[dict[str, Any]] = []
    for key in (store.ALL_OPPONENTS, *OPPONENT_CLASSES):
        raw = payload.get(key)
        if not isinstance(raw, dict):
            continue
        rows = raw.get("cards_by_dbf_id")
        if not isinstance(rows, list):
            raise PayloadRejected(f"group {key} has no cards_by_dbf_id list")
        meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        cards: list[dict[str, Any]] = []
        for row in rows:
            dbf_id = _int(row.get("dbf_id")) if isinstance(row, dict) else None
            if dbf_id is None:
                continue
            card = lookup.get(dbf_id) or {}
            cards.append(
                {
                    "dbf_id": dbf_id,
                    "card_id": card.get("id"),
                    "card_name": card.get("name"),
                    **{field: _num(row.get(field)) for field in store.CARD_FIELDS},
                }
            )
        groups.append(
            {
                "opponent_class": key,
                "games": _int(raw.get("num_games")),
                "base_winrate": _num(raw.get("base_winrate")),
                "scope": str(raw.get("query_score_scope") or "") or None,
                "as_of": str(raw.get("as_of") or "") or None,
                "deck_games": _int(meta.get("deck_games")),
                "deck_winrate": _num(meta.get("deck_winrate")),
                "archetype_games": _int(meta.get("archetype_games")),
                "archetype_winrate": _num(meta.get("archetype_winrate")),
                "class_games": _int(meta.get("class_games")),
                "class_winrate": _num(meta.get("class_winrate")),
                "cards": cards,
            }
        )
    if not groups[0]["cards"]:
        raise PayloadRejected("ALL group has no cards")
    return groups


def content_hash(groups: Iterable[Mapping[str, Any]]) -> str:
    """Hash of the numbers only: a recomputed ``as_of`` alone is no change."""

    payload = [
        {
            key: value
            for key, value in group.items()
            if key not in {"as_of", "card_id", "card_name"}
        }
        | {
            "cards": [
                {
                    key: value
                    for key, value in card.items()
                    if key not in {"card_id", "card_name"}
                }
                for card in group["cards"]
            ]
        }
        for group in groups
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _archetype_names() -> dict[int, str]:
    try:
        conn = get_db_connection()
    except Exception:  # noqa: BLE001 - names are decoration only
        return {}
    try:
        return {
            int(row[0]): str(row[1])
            for row in conn.execute(
                "SELECT archetype_id, name FROM hsreplay_archetypes"
            )
        }
    except Exception:  # noqa: BLE001 - the archetype table may not exist yet
        return {}
    finally:
        conn.close()


def select_decks(
    payload: Any,
    *,
    format_name: str,
    settings: Settings,
    archetype_names: Mapping[int, str] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Pick the pool from one ``list_decks_by_win_rate_v2`` answer.

    The most played decks come first, then the best winrates among decks with
    at least ``winrate_min_games``; HSReplay's ``deck_id`` is the shortid the
    mulligan endpoint takes.
    """

    series = payload.get("series") if isinstance(payload, dict) else None
    data = series.get("data") if isinstance(series, dict) else None
    if not isinstance(data, dict):
        raise PayloadRejected("deck list has no series.data")
    names = archetype_names or {}
    rows: list[dict[str, Any]] = []
    for class_key, items in data.items():
        for row in items if isinstance(items, list) else []:
            if not isinstance(row, dict) or not row.get("deck_id"):
                continue
            games = _int(row.get("total_games")) or 0
            if games < settings.deck_min_games:
                continue
            archetype_id = _int(row.get("archetype_id"))
            name = names.get(archetype_id) if archetype_id is not None else None
            if name is None and archetype_id is not None and archetype_id < 0:
                name = f"Other {class_key.title()}"
            rows.append(
                {
                    "deck_id": str(row["deck_id"]),
                    "archetype_id": archetype_id,
                    "archetype_name": name,
                    "player_class": str(class_key),
                    "total_games": games,
                    "win_rate": _num(row.get("win_rate")),
                    "deck_list": row.get("deck_list"),
                }
            )
    popular, best = (
        (settings.standard_popular_decks, settings.standard_winrate_decks)
        if format_name == "standard"
        else (settings.wild_popular_decks, settings.wild_winrate_decks)
    )
    rows.sort(key=lambda row: (-row["total_games"], row["deck_id"]))
    picked = [{**row, "pick_reason": "popular"} for row in rows[:popular]]
    seen = {row["deck_id"] for row in picked}
    by_winrate = sorted(
        (
            row
            for row in rows
            if row["deck_id"] not in seen
            and row["total_games"] >= settings.winrate_min_games
            and row["win_rate"] is not None
        ),
        key=lambda row: (-(row["win_rate"] or 0), -row["total_games"]),
    )
    picked += [{**row, "pick_reason": "winrate"} for row in by_winrate[:best]]
    for position, row in enumerate(picked):
        row["position"] = position
    as_of = payload.get("as_of") if isinstance(payload, dict) else None
    for row in picked:
        row["as_of"] = as_of
    return picked, as_of


# --- plan -----------------------------------------------------------------------


def target_key(target: Mapping[str, Any]) -> str:
    return "|".join(
        str(target[key])
        for key in ("format", "rank", "deck_id", "initiative", "period")
    )


def tier_of(format_name: str, rank: str) -> int:
    return TIERS.get((format_name, rank), WILD_TIER)


def plan_targets(decks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every pool deck x initiative x time range of its own format and rank."""

    planned: dict[str, dict[str, Any]] = {}
    for deck in decks:
        format_name, rank = str(deck["format"]), str(deck["rank"])
        for initiative in INITIATIVES:
            for period in PERIODS:
                target = {
                    "deck_id": str(deck["deck_id"]),
                    "format": format_name,
                    "rank": rank,
                    "initiative": initiative,
                    "period": period,
                    "tier": tier_of(format_name, rank),
                    "premium": int(is_premium_slice(rank, initiative)),
                }
                target["target_key"] = target_key(target)
                planned[target["target_key"]] = target
    return list(planned.values())


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def ttl_hours(target: Mapping[str, Any]) -> float:
    base = PERIOD_TTL_HOURS.get(str(target["period"]), 72)
    return float(base * TIER_TTL_FACTOR.get(int(target["tier"]), WILD_TIER))


def due_score(target: Mapping[str, Any], now: datetime) -> float | None:
    """How overdue a target is (>= 1 means due); ``None`` when not due."""

    retry_after = _parse_time(target.get("next_attempt_after"))
    if retry_after is not None and retry_after > now:
        return None
    success = _parse_time(target.get("last_success_at"))
    if success is None:
        return float("inf")
    score = (now - success).total_seconds() / 3600 / ttl_hours(target)
    return score if score >= 1 else None


def due_targets(
    targets: Iterable[Mapping[str, Any]], now: datetime
) -> list[dict[str, Any]]:
    scored = []
    for target in targets:
        score = due_score(target, now)
        if score is not None:
            scored.append((score, dict(target)))
    scored.sort(
        key=lambda item: (
            int(item[1]["tier"]),
            item[1].get("last_success_at") is not None,
            -min(item[0], 1e9),
            PERIOD_PRIORITY.get(str(item[1]["period"]), 9),
            str(item[1]["target_key"]),
        )
    )
    return [target for _, target in scored]


def estimate_plan(decks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Requests per day the plan needs by TTL, plus deck lists."""

    targets = plan_targets(decks)
    groups: dict[str, dict[str, float]] = {}
    total = 0.0
    for target in targets:
        group = f"{target['format']}_{target['rank']}_tier{target['tier']}"
        per_day = 24 / ttl_hours(target)
        entry = groups.setdefault(group, {"targets": 0, "requests_per_day": 0.0})
        entry["targets"] += 1
        entry["requests_per_day"] += per_day
        total += per_day
    deck_lists = len(FORMATS) * len(RANKS) * 24 / DECK_LIST_TTL_HOURS
    return {
        "targets": len(targets),
        "mulligan_requests_per_day_by_ttl": round(total, 1),
        "deck_list_requests_per_day": round(deck_lists, 1),
        "groups": {
            name: {
                "targets": int(value["targets"]),
                "requests_per_day": round(value["requests_per_day"], 1),
            }
            for name, value in sorted(groups.items())
        },
    }


def _retry_after(failures: int, now: datetime) -> datetime:
    minutes = min(60 * (2 ** max(0, failures)), 24 * 60)
    return now + timedelta(minutes=minutes)


# --- acquisition ----------------------------------------------------------------


@dataclass
class FetchResult:
    """One classified HSReplay answer.

    ``outcome``: ``ok`` (JSON payload), ``no_data`` (HTTP 204: no games),
    ``processing`` (HTTP 202: query still computing), ``denied`` (HSReplay
    refused the session: 401/403, which Scrape.do reports as 502
    ROTATION_FAILED), ``unsupported`` (400/404), ``account`` (Scrape.do token
    or plan), ``budget`` (credit cap reached, nothing sent) or ``error``.
    """

    outcome: str
    payload: Any = None
    status: int | None = None
    credits: int = 0
    detail: str = ""


JsonFetcher = Callable[[str], Awaitable[FetchResult]]


def _empty_body_outcome(status: int | None) -> str:
    """HSReplay answers a cold query with an empty 202 and an empty slice with 204."""

    if status == 202:
        return "processing"
    if status == 204:
        return "no_data"
    return "error"


def _denied(status: int | None, message: str) -> bool:
    return status in {401, 403} or (status == 502 and "ROTATION_FAILED" in message)


@dataclass
class ScrapeDoClient:
    """Bounded-concurrency Scrape.do JSON reads with a hard credit cap.

    A call is sent only while spent credits plus every in-flight reservation
    plus one stay inside ``credit_cap``; render=false costs exactly one
    credit, refused calls usually cost none.
    """

    credit_cap: int
    concurrency: int = MAX_CONCURRENCY
    call: JsonFetcher | None = None
    # Provider usage is booked to this source (other HSReplay collectors reuse the client).
    usage_source: str = SOURCE_ID
    requests: int = 0
    credits: int = 0
    _in_flight: int = 0
    _gate: asyncio.Semaphore | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def exhausted(self) -> bool:
        return self.credits + self._in_flight + 1 > self.credit_cap

    async def _reserve(self) -> bool:
        async with self._lock:
            if self.exhausted():
                return False
            self._in_flight += 1
            self.requests += 1
            return True

    async def _settle(self, cost: int) -> None:
        async with self._lock:
            self._in_flight -= 1
            self.credits += max(0, int(cost or 0))

    async def fetch(self, url: str) -> FetchResult:
        if self._gate is None:
            self._gate = asyncio.Semaphore(
                max(1, min(self.concurrency, MAX_CONCURRENCY))
            )
        async with self._gate:
            if not await self._reserve():
                return FetchResult("budget")
            cost = 0
            try:
                result = await (self.call or self._call)(url)
                cost = result.credits
                return result
            finally:
                await self._settle(cost)

    async def _call(self, url: str) -> FetchResult:
        from .hsreplay_client import (
            _hsreplay_cookie_header,
            extract_json_payload,
            is_hsreplay_query_processing,
        )
        from .scrape_do_backend import (
            ScrapeDoAccountError,
            ScrapeDoContentError,
            ScrapeDoRequestError,
            scrape_url,
        )

        headers = {"Accept": "application/json"}
        cookie = _hsreplay_cookie_header()
        if cookie:
            # scrape_url sends it as Sd-Cookie; the value is never logged.
            headers["Cookie"] = cookie
        try:
            with provider_usage.usage_source(self.usage_source):
                scraped = await scrape_url(
                    url,
                    render=False,
                    super_proxy=False,
                    headers=headers,
                    forward_headers=False,
                    timeout_ms=SCRAPE_DO_TIMEOUT_MS,
                )
        except ScrapeDoAccountError as exc:
            return FetchResult(
                "account", status=exc.status_code, credits=exc.request_cost
            )
        except ScrapeDoContentError as exc:
            # Scrape.do raises on an empty body; HSReplay sends one with 202.
            status = exc.scrape.status_code
            return FetchResult(
                _empty_body_outcome(status),
                status=status,
                credits=exc.scrape.request_cost,
                detail=f"empty_body_http_{status}",
            )
        except ScrapeDoRequestError as exc:
            message = str(exc)
            status = exc.status_code
            if _denied(status, message):
                outcome = "denied"
            elif status in {400, 404}:
                outcome = "unsupported"
            else:
                outcome = "error"
            return FetchResult(
                outcome,
                status=status,
                credits=exc.request_cost,
                detail=f"http_{status}" if status else type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            return FetchResult("error", detail=type(exc).__name__)
        if not scraped.html.strip():
            return FetchResult(
                _empty_body_outcome(scraped.status_code),
                status=scraped.status_code,
                credits=scraped.request_cost,
                detail=f"empty_body_http_{scraped.status_code}",
            )
        payload = extract_json_payload(scraped.html)
        if scraped.status_code == 202 or is_hsreplay_query_processing(payload):
            return FetchResult(
                "processing", status=scraped.status_code, credits=scraped.request_cost
            )
        if payload is None:
            return FetchResult(
                "error",
                status=scraped.status_code,
                credits=scraped.request_cost,
                detail="not_json",
            )
        return FetchResult(
            "ok",
            payload=payload,
            status=scraped.status_code,
            credits=scraped.request_cost,
        )


async def probe_premium(fetch: JsonFetcher) -> dict[str, Any]:
    """Check the session on ``/api/v1/account/`` (one credit)."""

    from .hsreplay_client import _hsreplay_cookie_header

    checked_at = _now().isoformat()
    if not _hsreplay_cookie_header():
        return {
            "state": "missing_session",
            "checked_at": checked_at,
            "detail": "HSReplay session cookie is missing; run python -m app.cli hsreplay-login",
        }
    result = await fetch(ACCOUNT_API)
    if result.outcome == "ok" and isinstance(result.payload, dict):
        premium = result.payload.get("is_premium")
        if premium is True:
            return {"state": "ok", "checked_at": checked_at, "detail": None}
        return {
            "state": "not_premium",
            "checked_at": checked_at,
            "detail": "HSReplay account answered without premium (is_premium is not true)",
        }
    if result.outcome == "denied":
        return {
            "state": "denied",
            "checked_at": checked_at,
            "detail": (
                f"HSReplay refused the session on /api/v1/account/ (HTTP {result.status}); "
                "re-login with python -m app.cli hsreplay-login"
            ),
        }
    return {
        "state": "unknown",
        "checked_at": checked_at,
        "detail": f"session probe inconclusive: {result.outcome} {result.detail}".strip(),
    }


async def _alert_premium(detail: str) -> None:
    try:
        from .fetcher import send_telegram_alert

        await send_telegram_alert(
            SOURCE_ID, PREMIUM_ALERT_STATE, detail, f"{HSREPLAY}/decks/"
        )
    except Exception as exc:  # noqa: BLE001 - an alert must never break the run
        logger.warning("HSReplay mulligan premium alert failed: %s", type(exc).__name__)


# --- refresh --------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _utc_day_start(now: datetime) -> str:
    return (
        now.astimezone(UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )


async def _refresh_deck_pools(
    conn: Any,
    fetch: JsonFetcher,
    *,
    settings: Settings,
    now: datetime,
    premium_ok: bool,
) -> dict[str, Any]:
    """Re-list the deck pool of every format and rank whose list is older than its TTL."""

    names = _archetype_names()
    report: dict[str, Any] = {"listed": {}, "failed": {}, "skipped": []}
    due: list[tuple[str, str]] = []
    for format_name in FORMATS:
        for rank in RANKS:
            listed_at = _parse_time(
                store.deck_pool_listed_at(conn, format_name=format_name, rank=rank)
            )
            if listed_at is not None and now - listed_at < timedelta(
                hours=DECK_LIST_TTL_HOURS
            ):
                continue
            # Every deck list except Standard Bronze-Gold needs premium.
            if not premium_ok and (format_name, rank) != ("standard", FREE_RANK):
                report["skipped"].append(f"{format_name}:{rank}")
                continue
            due.append((format_name, rank))

    async def one(format_name: str, rank: str) -> None:
        key = f"{format_name}:{rank}"
        result = await fetch(deck_list_url(format_name, rank))
        if result.outcome != "ok":
            report["failed"][key] = result.outcome
            return
        try:
            decks, _as_of = select_decks(
                result.payload,
                format_name=format_name,
                settings=settings,
                archetype_names=names,
            )
        except PayloadRejected as exc:
            report["failed"][key] = f"contract: {exc}"
            return
        if not decks:
            report["failed"][key] = "empty"
            return
        report["listed"][key] = store.replace_deck_pool(
            conn,
            format_name=format_name,
            rank=rank,
            decks=decks,
            listed_at=now.isoformat(),
        )

    await asyncio.gather(*(one(format_name, rank) for format_name, rank in due))
    return report


async def _refresh_unlocked(
    *,
    settings: Settings,
    call_json: JsonFetcher | None,
    now: datetime | None,
    max_requests: int | None,
) -> dict[str, Any]:
    started = now or _now()
    started_at = started.isoformat()

    def clock() -> datetime:
        return now or _now()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.deadline_seconds
    init_db()
    conn = get_db_connection()
    try:
        run_id = store.start_run(conn, started_at=started_at)
        spent_today = store.scrape_do_credits_since(conn, _utc_day_start(started))
        caps = [
            settings.scrape_do_run_credits,
            settings.scrape_do_daily_credits - spent_today,
        ]
        if max_requests is not None:
            caps.append(max_requests)
        credit_cap = max(0, min(caps))
        client = ScrapeDoClient(
            credit_cap=credit_cap, concurrency=settings.concurrency, call=call_json
        )
        counters: dict[str, int] = {"requests": 0, "credits": 0}

        async def counted(url: str) -> FetchResult:
            result = await client.fetch(url)
            if result.outcome != "budget":
                counters["requests"] += 1
                counters["credits"] += max(0, int(result.credits or 0))
            return result

        stop_reason: str | None = None
        premium = await probe_premium(counted)
        previous = premium_session_from_run(store.latest_finished_run(conn))
        if premium["state"] == "unknown" and previous["state"] in PREMIUM_BAD_STATES:
            # An inconclusive probe (no budget, network) must not hide a
            # rejected session: keep skipping premium until a probe succeeds.
            premium = {
                **previous,
                "detail": f"{previous['detail']} (kept: {premium['detail']})",
            }
        premium_denials = 0
        premium_ok_in_run = False
        alerted = premium["state"] in PREMIUM_BAD_STATES
        if alerted:
            await _alert_premium(str(premium["detail"]))

        pools = await _refresh_deck_pools(
            conn,
            counted,
            settings=settings,
            now=started,
            premium_ok=premium["state"] not in PREMIUM_BAD_STATES,
        )
        premium_denials += sum(
            1 for outcome in pools["failed"].values() if outcome == "denied"
        )
        decks = store.active_decks(conn)
        planned = plan_targets(decks)
        if planned:
            store.sync_targets(conn, planned, planned_at=started_at)
        cards_by_dbf = cards_index.cards_by_dbfid()
        due = due_targets(store.active_targets(conn), started)
        counts = {
            "attempted": 0,
            "changed": 0,
            "unchanged": 0,
            "no_data": 0,
            "failed": 0,
            "processing": 0,
            "skipped_premium": 0,
        }
        outcomes: dict[str, int] = {}
        queue = list(due)
        failures_in_row = 0
        lock = asyncio.Lock()

        def premium_blocked() -> bool:
            return premium["state"] in PREMIUM_BAD_STATES

        async def handle(target: dict[str, Any]) -> None:
            nonlocal failures_in_row, premium_denials, premium_ok_in_run, stop_reason
            url = mulligan_api_url(target)
            attempted_at = clock().isoformat()
            result = await counted(url)
            if result.outcome == "budget":
                async with lock:
                    stop_reason = stop_reason or "scrape_do_budget"
                return
            counts["attempted"] += 1
            outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
            if result.outcome in {"ok", "no_data"}:
                try:
                    groups = (
                        parse_mulligan_payload(
                            result.payload, cards_by_dbf=cards_by_dbf
                        )
                        if result.outcome == "ok"
                        else []
                    )
                except PayloadRejected as exc:
                    result = FetchResult("error", detail=f"contract: {exc}")
                else:
                    async with lock:
                        failures_in_row = 0
                        if target["premium"]:
                            premium_ok_in_run = True
                            premium_denials = 0
                    _, changed = store.record_success(
                        conn,
                        target,
                        fetched_at=attempted_at,
                        groups=groups,
                        content_hash=content_hash(groups),
                        source_url=url,
                        backend="scrape_do",
                        request_credits=result.credits,
                        state="ok" if groups else "no_data",
                    )
                    if not groups:
                        counts["no_data"] += 1
                    counts["changed" if changed else "unchanged"] += 1
                    return
            if result.outcome == "account":
                async with lock:
                    stop_reason = "scrape_do_account"
            # HSReplay still computing is an upstream readiness state, not a failure.
            counts["processing" if result.outcome == "processing" else "failed"] += 1
            failures = int(target.get("consecutive_failures") or 0)
            if result.outcome == "denied" and target["premium"]:
                async with lock:
                    premium_denials += 1
                    if (
                        premium_denials >= PREMIUM_DENIAL_THRESHOLD
                        and not premium_ok_in_run
                        and not premium_blocked()
                    ):
                        premium.update(
                            state="denied",
                            detail=(
                                f"HSReplay refused {premium_denials} premium mulligan "
                                "requests in a row (401/403); re-login with "
                                "python -m app.cli hsreplay-login"
                            ),
                        )
                retry = clock() + timedelta(hours=PREMIUM_DENIED_RETRY_HOURS)
                state, count_failure = "premium_denied", False
            elif result.outcome == "unsupported":
                retry = clock() + timedelta(hours=UNSUPPORTED_RETRY_HOURS)
                state, count_failure = "unsupported", False
            elif result.outcome == "processing":
                retry = _retry_after(failures, clock())
                state, count_failure = "processing", True
            else:
                retry = _retry_after(failures, clock())
                state, count_failure = "failed", True
            store.record_failure(
                conn,
                target,
                attempted_at=attempted_at,
                state=state,
                error=result.detail or result.outcome,
                retry_after=retry.isoformat(),
                count_failure=count_failure,
            )
            if result.outcome == "error":
                async with lock:
                    failures_in_row += 1
                    if failures_in_row >= RUN_CIRCUIT_THRESHOLD and stop_reason is None:
                        stop_reason = "provider_circuit_open"

        async def worker() -> None:
            nonlocal stop_reason
            while True:
                async with lock:
                    if not queue or stop_reason is not None:
                        return
                    if loop.time() >= deadline:
                        stop_reason = "deadline"
                        return
                    target = queue.pop(0)
                    if target["premium"] and premium_blocked():
                        counts["skipped_premium"] += 1
                        continue
                await handle(target)

        await asyncio.gather(*(worker() for _ in range(settings.concurrency)))
        if premium_blocked() and not alerted:
            await _alert_premium(str(premium["detail"]))
        pruned = store.prune(conn, now=started, snapshot_days=settings.snapshot_days)
        coverage = store.coverage(conn)
        succeeded = counts["changed"] + counts["unchanged"]
        if not planned or (counts["failed"] and not succeeded):
            state = SourceState.FETCH_ERROR
        elif counts["failed"] or premium_blocked():
            state = SourceState.PARTIAL
        else:
            state = SourceState.OK
        finished_at = clock().isoformat()
        summary = {
            "finished_at": finished_at,
            "state": str(state),
            "premium_state": premium["state"],
            "decks_listed": sum(pools["listed"].values()),
            "targets_planned": len(planned),
            "targets_due": len(due),
            **counts,
            "requests": counters["requests"],
            "scrape_do_credits": counters["credits"],
            "stop_reason": stop_reason,
            "detail": {
                "premium": premium,
                "deck_pools": pools,
                "outcomes": outcomes,
                "processing": counts["processing"],
                "scrape_do_credit_cap": credit_cap,
                "scrape_do_spent_today_before_run": spent_today,
                "pruned": pruned,
            },
        }
        store.finish_run(conn, run_id, summary)
        decks_total = len(decks)
    finally:
        conn.close()

    with_data = sum(int(row["with_data"] or 0) for row in coverage if row["active"])
    active_total = sum(int(row["targets"] or 0) for row in coverage if row["active"])
    remaining_due = max(0, len(due) - counts["attempted"])
    published = bool(planned) and (succeeded > 0 or not counts["failed"])
    structured = {
        "type": SOURCE_ID,
        "fetched_at": finished_at,
        "decks": decks_total,
        "targets_active": active_total,
        "targets_with_data": with_data,
        "targets_due_after_run": remaining_due,
        "premium_session": premium,
        "coverage": coverage,
        "last_run": {**summary, "started_at": started_at},
    }
    if published:
        save_dataset(
            SOURCE_ID,
            {
                "state": SourceState.OK,
                "fetched_at": finished_at,
                "http_status": 200,
                "final_url": f"{HSREPLAY}/decks/",
                "backend": "hsreplay_mulligan_store",
                "data": {"structured": structured},
            },
        )
    previous = load_dataset(SOURCE_ID) or {}
    premium_note = (
        f"; PREMIUM SESSION {premium['state'].upper()}: {premium['detail']}"
        if premium["state"] in PREMIUM_BAD_STATES
        else ""
    )
    status = {
        "source_id": SOURCE_ID,
        "site": "hsreplay",
        "category": "archetype_analysis",
        "url": f"{HSREPLAY}/decks/",
        "state": str(state if premium_blocked() or not published else SourceState.OK),
        "fetched_at": finished_at
        if published
        else previous.get("fetched_at") or finished_at,
        "http_status": 200 if succeeded else None,
        "backend": "scrape_do",
        "rows_total": with_data,
        "detail": (
            f"HSReplay mulligan: {succeeded}/{counts['attempted']} slices ok, "
            f"{counts['processing']} still computing upstream, "
            f"{counts['skipped_premium']} premium skipped, {remaining_due} still due, "
            f"stop={stop_reason or 'done'}, scrape.do credits "
            f"{counters['credits']}/{credit_cap}{premium_note}"
        ),
        "premium_session": premium,
        "serving_cached_dataset": not published and bool(previous),
        "last_refresh_state": str(state),
        "last_refresh_at": finished_at,
    }
    if counts["failed"] or premium_blocked():
        status["last_refresh_failure_class"] = str(
            SourceState.HTTP_ERROR if premium_blocked() else SourceState.FETCH_ERROR
        )
    save_status(SOURCE_ID, status)
    return {
        "ok": state == SourceState.OK,
        "state": str(state),
        "published": published,
        "serving_cached_dataset": status["serving_cached_dataset"],
        "source_id": SOURCE_ID,
        "fetched_at": finished_at,
        "premium_session": premium,
        "deck_pools": pools,
        "decks": decks_total,
        "targets": active_total,
        "targets_with_data": with_data,
        "targets_due": len(due),
        "targets_due_after_run": remaining_due,
        **counts,
        "outcomes": outcomes,
        "requests": counters["requests"],
        "scrape_do_credits": counters["credits"],
        "scrape_do_credit_cap": credit_cap,
        "stop_reason": stop_reason,
        "pruned": pruned,
    }


async def refresh_hsreplay_mulligan(
    *,
    settings: Settings | None = None,
    call_json: JsonFetcher | None = None,
    now: datetime | None = None,
    max_requests: int | None = None,
) -> dict[str, Any]:
    """Read the most overdue HSReplay mulligan slices unless another run owns them."""

    locks = ResourceLockSet(
        [SOURCE_ID], metadata={"operation": "refresh_hsreplay_mulligan"}
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
            call_json=call_json,
            now=now,
            max_requests=max_requests,
        )
    finally:
        locks.release()


# --- reads ----------------------------------------------------------------------


def _read(callback: Callable[[Any], Any]) -> Any:
    init_db()
    conn = get_db_connection()
    try:
        return callback(conn)
    finally:
        conn.close()


@dataclass(frozen=True)
class Selector:
    """One stored slice as the read API names it."""

    deck_id: str
    format_name: str | None
    rank: str
    initiative: str = "all"
    period: str = "last_30_days"
    opponent_class: str = ANY_OPPONENT


class SliceUnavailable(LookupError):
    """The slice cannot be served; ``status`` is the HTTP code to answer with."""

    def __init__(
        self, status: int, detail: str, *, premium_session: Any = None
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.premium_session = premium_session


def premium_session_from_run(run: Mapping[str, Any] | None) -> dict[str, Any]:
    if not run:
        return {"state": "unknown", "checked_at": None, "detail": "no refresh run yet"}
    premium = (run.get("detail") or {}).get("premium") or {}
    return {
        "state": premium.get("state") or run.get("premium_state") or "unknown",
        "checked_at": premium.get("checked_at") or run.get("finished_at"),
        "detail": premium.get("detail"),
    }


def target_is_stale(target: Mapping[str, Any], now: datetime | None = None) -> bool:
    checked = _parse_time(target.get("last_success_at"))
    if checked is None or not target.get("active"):
        return True
    age_hours = ((now or _now()) - checked).total_seconds() / 3600
    return age_hours > 2 * ttl_hours(target)


def _impact(value: Any, base: Any) -> float | None:
    if value is None or base is None:
        return None
    return round(float(value) - float(base), 2)


def assemble_mulligan(
    target: Mapping[str, Any],
    snapshot_row: Mapping[str, Any] | None,
    groups: Iterable[Mapping[str, Any]],
    cards: Iterable[Mapping[str, Any]],
    *,
    opponent_class: str,
    deck: Mapping[str, Any] | None,
    premium_session: Mapping[str, Any],
) -> dict[str, Any]:
    """REST and GraphQL answer for one stored slice.

    Card rows keep HSReplay's own metrics and add ``*_impact``: the metric's
    winrate minus the group's base winrate, in percentage points, the same
    unit as HSGuru's impact columns (``/v1/hsguru/mulligan``).
    """

    groups = list(groups)
    key = store.ALL_OPPONENTS if opponent_class == ANY_OPPONENT else opponent_class
    group = next((item for item in groups if item["opponent_class"] == key), None)
    base = group.get("base_winrate") if group else None
    rows = []
    for card in cards:
        row = {name: value for name, value in card.items() if name != "opponent_class"}
        row.pop("position", None)
        row["opening_hand_impact"] = _impact(card.get("opening_hand_winrate"), base)
        row["drawn_impact"] = _impact(card.get("winrate_when_drawn"), base)
        row["played_impact"] = _impact(card.get("winrate_when_played"), base)
        rows.append(row)
    no_data = snapshot_row is None
    return {
        "source": "hsreplay",
        "source_id": SOURCE_ID,
        "deck_id": target["deck_id"],
        "format": target["format"],
        "rank": target["rank"],
        "initiative": target["initiative"],
        "period": target["period"],
        "opponent_class": opponent_class,
        "premium": bool(target["premium"]),
        "deck": {
            "archetype_id": deck.get("archetype_id"),
            "archetype_name": deck.get("archetype_name"),
            "player_class": deck.get("player_class"),
            "deck_list": deck.get("deck_list"),
        }
        if deck
        else None,
        "state": target.get("state"),
        "no_data": no_data,
        "games": group.get("games") if group else (0 if no_data else None),
        "base_winrate": base,
        "scope": group.get("scope") if group else None,
        "as_of": group.get("as_of") if group else None,
        "sample": {
            name: group.get(name)
            for name in (
                "deck_games",
                "deck_winrate",
                "archetype_games",
                "archetype_winrate",
                "class_games",
                "class_winrate",
            )
        }
        if group
        else None,
        "opponent_classes": [
            ANY_OPPONENT
            if item["opponent_class"] == store.ALL_OPPONENTS
            else item["opponent_class"]
            for item in groups
        ],
        "snapshot_id": snapshot_row["id"] if snapshot_row else None,
        "changed_at": snapshot_row["fetched_at"] if snapshot_row else None,
        "checked_at": target.get("last_success_at"),
        "active": bool(target.get("active")),
        "stale": target_is_stale(target),
        "premium_session": dict(premium_session),
        "source_url": deck_page_url(target),
        "rows": rows,
    }


def resolve_slice(
    found: Mapping[str, Any] | None, selector: Selector
) -> dict[str, Any]:
    """Turn stored rows into the answer, or raise an explicit unavailability."""

    premium_session = (found or {}).get("premium_session") or premium_session_from_run(
        None
    )
    target = (found or {}).get("target")
    if target is None:
        raise SliceUnavailable(
            404,
            "This deck slice is not collected; /v1/hsreplay/mulligan/decks lists the "
            "collected decks and /v1/hsreplay/mulligan/slices their slices",
        )
    premium_bad = premium_session.get("state") in PREMIUM_BAD_STATES
    snapshot_row = found.get("snapshot")
    if snapshot_row is None and target.get("state") != "no_data":
        if target["premium"] and premium_bad:
            raise SliceUnavailable(
                503,
                "HSReplay premium session is not working "
                f"({premium_session.get('state')}: {premium_session.get('detail')}); "
                "premium slices cannot be collected until it is restored",
                premium_session=premium_session,
            )
        if target.get("state") == "processing":
            raise SliceUnavailable(
                404,
                "HSReplay is still computing this slice (HTTP 202); the next check is "
                f"after {target.get('next_attempt_after')}",
            )
        raise SliceUnavailable(
            404,
            f"This slice has no data yet (state {target.get('state')}"
            + (f": {target.get('last_error')}" if target.get("last_error") else "")
            + ")",
        )
    groups = found.get("groups") or []
    key = (
        store.ALL_OPPONENTS
        if selector.opponent_class == ANY_OPPONENT
        else selector.opponent_class
    )
    if snapshot_row is not None and not any(
        item["opponent_class"] == key for item in groups
    ):
        raise SliceUnavailable(
            503 if premium_bad else 404,
            f"The stored snapshot has no {selector.opponent_class} breakdown"
            + (
                f"; HSReplay premium session is not working ({premium_session.get('state')})"
                if premium_bad
                else "; HSReplay only splits by opponent class with premium"
            ),
            premium_session=premium_session,
        )
    return assemble_mulligan(
        target,
        snapshot_row,
        groups,
        found.get("cards") or [],
        opponent_class=selector.opponent_class,
        deck=found.get("deck"),
        premium_session=premium_session,
    )


def _load_slice(conn: Any, selector: Selector) -> dict[str, Any]:
    premium_session = premium_session_from_run(store.latest_finished_run(conn))
    target = store.find_target(
        conn,
        deck_id=selector.deck_id,
        format_name=selector.format_name,
        rank=selector.rank,
        initiative=selector.initiative,
        period=selector.period,
    )
    if target is None:
        return {"premium_session": premium_session}
    snapshot_row = (
        store.snapshot(conn, int(target["latest_snapshot_id"]))
        if target.get("latest_snapshot_id")
        else None
    )
    key = (
        store.ALL_OPPONENTS
        if selector.opponent_class == ANY_OPPONENT
        else selector.opponent_class
    )
    return {
        "premium_session": premium_session,
        "target": target,
        "snapshot": snapshot_row,
        "groups": store.snapshot_groups(conn, int(snapshot_row["id"]))
        if snapshot_row
        else [],
        "cards": store.snapshot_cards(conn, int(snapshot_row["id"]), opponent_class=key)
        if snapshot_row
        else [],
        "deck": store.deck_info(conn, selector.deck_id, target["format"]),
    }


def get_mulligan(selector: Selector) -> dict[str, Any]:
    """The latest stored answer of one slice; raises :class:`SliceUnavailable`."""

    return resolve_slice(_read(lambda conn: _load_slice(conn, selector)), selector)


def get_history(selector: Selector, *, card: str | None, limit: int) -> dict[str, Any]:
    """Stored snapshots of one slice, newest first, optionally with one card's row."""

    key = (
        store.ALL_OPPONENTS
        if selector.opponent_class == ANY_OPPONENT
        else selector.opponent_class
    )

    def read(conn):
        target = store.find_target(
            conn,
            deck_id=selector.deck_id,
            format_name=selector.format_name,
            rank=selector.rank,
            initiative=selector.initiative,
            period=selector.period,
        )
        if target is None:
            return None
        points = store.target_snapshots(conn, int(target["id"]), limit=limit)
        cards = (
            store.card_history(
                conn, int(target["id"]), card=card, opponent_class=key, limit=limit
            )
            if card
            else {}
        )
        return target, points, cards

    found = _read(read)
    if found is None:
        raise SliceUnavailable(404, "This deck slice is not collected")
    target, points, cards = found
    for point in points:
        point["snapshot_id"] = point.pop("id")
        if card:
            point["card"] = cards.get(int(point["snapshot_id"]))
    return {
        "source": "hsreplay",
        "deck_id": target["deck_id"],
        "format": target["format"],
        "rank": target["rank"],
        "initiative": target["initiative"],
        "period": target["period"],
        "opponent_class": selector.opponent_class,
        "checked_at": target.get("last_success_at"),
        "points": points,
    }


def list_decks(
    *, format_name: str | None, rank: str | None, limit: int
) -> list[dict[str, Any]]:
    return _read(
        lambda conn: store.deck_catalog(
            conn, format_name=format_name, rank=rank, limit=limit
        )
    )


def list_slices(*, deck_id: str, format_name: str | None) -> list[dict[str, Any]]:
    rows = _read(
        lambda conn: store.deck_targets(conn, deck_id=deck_id, format_name=format_name)
    )
    for row in rows:
        row["premium"] = bool(row["premium"])
        row["active"] = bool(row["active"])
    return rows


def premium_status() -> dict[str, Any]:
    return _read(lambda conn: premium_session_from_run(store.latest_finished_run(conn)))


def coverage_report(*, runs: int = 10) -> dict[str, Any]:
    def read(conn):
        recent = store.recent_runs(conn, limit=runs)
        finished = next((run for run in recent if run.get("finished_at")), None)
        return store.coverage(conn), recent, premium_session_from_run(finished)

    coverage, recent, premium = _read(read)
    return {
        "source": "hsreplay",
        "premium_session": premium,
        "coverage": coverage,
        "runs": recent,
        "settings": Settings.from_env().__dict__,
    }


def latest_revision() -> str | None:
    """Cache revision for public HTTP caching."""

    conn = get_db_connection()
    try:
        return store.latest_revision(conn)
    except Exception:  # noqa: BLE001 - a missing table means no revision yet
        return None
    finally:
        conn.close()
