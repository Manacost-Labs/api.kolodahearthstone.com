"""HSReplay card choice statistics: what players pick from Discover and Adapt.

HSReplay's card page reads
``/analytics/query/single_card_choices_by_winrate_v2/?card_id=<dbf>&GameType&LeagueRankRange``.
The answer is ``series.data.ALL[]`` with ``dbf_id``, ``popularity`` (how often
the option is taken when it is offered, so the column sums to more than 100%)
and ``win_rate`` (games won after taking it). Only options with enough games
are listed (up to about 100), there are no game counts and no split by class.
HSReplay answers HTTP 204 for Choose One and Excavate cards (verified live on
2026-10-25), so only ``card_choice_pools.STATS_KINDS`` cards are requested.

The full pool every card chooses from comes from the card base
(:mod:`app.card_choice_pools`); options without HSReplay numbers are marked
``has_stats: false`` ("мало данных" on the site).

Targets are the most played choice cards of each format (by HSReplay's own
card table) at each configured rank. A target is due when it was never read,
when its TTL (one week) expired, or when a patch landed after its last read
(one day after the patch, so the new numbers have games behind them). Each
run reads due targets through plain Scrape.do (one credit each, at most five in
parallel) with the project's HSReplay session, inside per-run and per-UTC-day
credit caps.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from . import card_choice_pools as pools
from . import hsreplay_card_choices_store as store
from .db import get_db_connection, init_db
from .hsreplay_mulligan import (
    PREMIUM_BAD_STATES,
    FetchResult,
    JsonFetcher,
    ScrapeDoClient,
    _parse_time,
    _retry_after,
    _utc_day_start,
    premium_session_from_run,
    probe_premium,
)
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .storage import load_dataset, save_dataset, save_status

logger = logging.getLogger(__name__)

SOURCE_ID = "hsreplay_card_choices"
HSREPLAY = "https://hsreplay.net"
CHOICES_API = f"{HSREPLAY}/analytics/query/single_card_choices_by_winrate_v2/"
FORMATS = {"standard": "RANKED_STANDARD", "wild": "RANKED_WILD"}
RANKS = {
    "legend": "LEGEND",
    "diamond_4to1": "DIAMOND_FOUR_THROUGH_DIAMOND_ONE",
    "diamond": "DIAMOND",
    "bronze_gold": "BRONZE_THROUGH_GOLD",
}
# HSReplay's played-card tables rank the choice cards by how often they are played.
PLAY_DATASETS = {
    "standard": ("hsreplay_cards_legend_14d", "hsreplay_cards_legend_7d"),
    "wild": ("hsreplay_cards_wild_legend_14d", "hsreplay_cards_wild_legend_7d"),
}
TTL_HOURS = 168
# A patch makes every target due once, a day after it landed.
PATCH_SETTLE_HOURS = 24
UNSUPPORTED_RETRY_HOURS = 168
PREMIUM_DENIED_RETRY_HOURS = 6
RUN_CIRCUIT_THRESHOLD = 10
MAX_CONCURRENCY = 5
# Without game counts, an exact 0 or 100 marks a handful of games.
EDGE_VALUES = (0.0, 100.0)
# "Undervalued": winrate at least this far above the options' average while
# taken less often than the median option.
UNDERVALUED_WINRATE_MARGIN = 1.0
PREMIUM_ALERT_STATE = "premium_auth_failed"


def _env_int(
    name: str, default: int, *, minimum: int = 0, maximum: int = 100_000
) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class Settings:
    # ~60 cards x 2 ranks a week by default (50 Standard + 10 Wild).
    standard_cards: int = 50
    wild_cards: int = 10
    concurrency: int = 5
    scrape_do_run_credits: int = 40
    scrape_do_daily_credits: int = 60
    deadline_seconds: int = 20 * 60
    snapshot_days: int = 120
    ranks: tuple[str, ...] = ("legend", "bronze_gold")

    @classmethod
    def from_env(cls) -> Settings:
        base = cls()
        values: dict[str, Any] = {
            name: _env_int(f"HS_CARD_CHOICES_{name.upper()}", getattr(base, name))
            for name in cls.__dataclass_fields__
            if name != "ranks"
        }
        values["concurrency"] = max(1, min(values["concurrency"], MAX_CONCURRENCY))
        raw_ranks = os.environ.get("HS_CARD_CHOICES_RANKS", "")
        ranks = tuple(
            rank
            for rank in (part.strip() for part in raw_ranks.split(","))
            if rank in RANKS
        )
        values["ranks"] = ranks or base.ranks
        return cls(**values)


# --- urls -----------------------------------------------------------------------


def choices_api_url(dbf_id: int, format_name: str, rank: str) -> str:
    return f"{CHOICES_API}?" + urlencode(
        {
            "card_id": int(dbf_id),
            "GameType": FORMATS[format_name],
            "LeagueRankRange": RANKS[rank],
        }
    )


def card_page_url(dbf_id: int) -> str:
    return f"{HSREPLAY}/cards/{int(dbf_id)}/"


# --- parsing --------------------------------------------------------------------


class PayloadRejected(ValueError):
    """The body is JSON but not a choices answer (contract drift or an error page)."""


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_choices_payload(
    payload: Any, *, cards_by_dbf: Mapping[int, Mapping[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], int, str | None]:
    """Rows sorted by popularity, the count of rows without a dbf id, and ``as_of``."""

    series = payload.get("series") if isinstance(payload, dict) else None
    data = series.get("data") if isinstance(series, dict) else None
    rows = data.get("ALL") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise PayloadRejected("choices payload has no series.data.ALL list")
    lookup = cards_by_dbf or {}
    parsed: list[dict[str, Any]] = []
    unidentified = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        dbf = row.get("dbf_id")
        if dbf is None or isinstance(dbf, bool):
            # HSReplay adds one row without a card (options it cannot name).
            unidentified += 1
            continue
        try:
            dbf_id = int(dbf)
        except (TypeError, ValueError):
            unidentified += 1
            continue
        parsed.append(
            {
                "dbf_id": dbf_id,
                "card_id": (lookup.get(dbf_id) or {}).get("id"),
                "popularity": _num(row.get("popularity")),
                "win_rate": _num(row.get("win_rate")),
            }
        )
    parsed.sort(key=lambda row: (-(row["popularity"] or 0), row["dbf_id"]))
    as_of = payload.get("as_of") if isinstance(payload, dict) else None
    return parsed, unidentified, str(as_of) if as_of else None


def content_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    numbers = [
        [row["dbf_id"], row.get("popularity"), row.get("win_rate")] for row in rows
    ]
    return hashlib.sha256(
        json.dumps(numbers, separators=(",", ":")).encode()
    ).hexdigest()


# --- plan -----------------------------------------------------------------------


def target_key(target: Mapping[str, Any]) -> str:
    return "|".join(str(target[key]) for key in ("format", "rank", "card_id"))


def _times_played(format_name: str) -> dict[int, int]:
    counts: dict[int, int] = {}
    for source_id in PLAY_DATASETS[format_name]:
        try:
            payload = load_dataset(source_id) or {}
        except Exception as exc:  # noqa: BLE001 - a missing table only drops the ordering
            logger.debug("card table %s unreadable: %s", source_id, type(exc).__name__)
            continue
        structured = (payload.get("data") or {}).get("structured") or {}
        for row in structured.get("cards") or []:
            if not isinstance(row, dict):
                continue
            try:
                dbf = int(row.get("dbfId", row.get("dbf_id")))
                played = int(row.get("times_played") or 0)
            except (TypeError, ValueError):
                continue
            counts[dbf] = max(counts.get(dbf, 0), played)
        if counts:
            return counts
    return counts


def plan_targets(
    base: pools.CardBase,
    settings: Settings,
    *,
    times_played: Mapping[str, Mapping[int, int]] | None = None,
) -> list[dict[str, Any]]:
    """The most played choice cards with HSReplay stats, per format and rank."""

    planned: list[dict[str, Any]] = []
    limits = {"standard": settings.standard_cards, "wild": settings.wild_cards}
    for tier, format_name in enumerate(FORMATS, start=1):
        played = (
            times_played[format_name]
            if times_played is not None
            else _times_played(format_name)
        )
        cards = [
            card
            for card in pools.choice_cards(base, format_name)
            if card["has_hsreplay_stats"]
        ]
        cards.sort(key=lambda card: (-played.get(card["dbf_id"], 0), card["card_id"]))
        # Never-played cards would only answer 204.
        cards = [
            card for card in cards if played.get(card["dbf_id"], 0) > 0 or not played
        ]
        for priority, card in enumerate(cards[: limits[format_name]]):
            for rank in settings.ranks:
                target = {
                    "card_id": card["card_id"],
                    "dbf_id": card["dbf_id"],
                    "format": format_name,
                    "rank": rank,
                    "kind": card["kind"],
                    "tier": tier,
                    "priority": priority,
                    "times_played": played.get(card["dbf_id"]),
                }
                target["target_key"] = target_key(target)
                planned.append(target)
    return planned


def latest_patch_at() -> datetime | None:
    try:
        from .patches_db import list_patches

        patches = list_patches(limit=5).get("patches") or []
    except Exception:  # noqa: BLE001 - patches only make targets due earlier
        return None
    dates = [_parse_time(patch.get("published_at")) for patch in patches]
    found = [value for value in dates if value is not None]
    return max(found) if found else None


def due_score(
    target: Mapping[str, Any], now: datetime, patch_at: datetime | None = None
) -> float | None:
    """How overdue a target is (>= 1 means due); ``None`` when not due."""

    retry_after = _parse_time(target.get("next_attempt_after"))
    if retry_after is not None and retry_after > now:
        return None
    success = _parse_time(target.get("last_success_at"))
    if success is None:
        return float("inf")
    if patch_at is not None:
        settled = patch_at + timedelta(hours=PATCH_SETTLE_HOURS)
        if success < settled <= now:
            return 1e6
    score = (now - success).total_seconds() / 3600 / TTL_HOURS
    return score if score >= 1 else None


def due_targets(
    targets: Iterable[Mapping[str, Any]],
    now: datetime,
    patch_at: datetime | None = None,
) -> list[dict[str, Any]]:
    scored = []
    for target in targets:
        score = due_score(target, now, patch_at)
        if score is not None:
            scored.append((score, dict(target)))
    scored.sort(
        key=lambda item: (
            item[1].get("last_success_at") is not None,
            int(item[1]["tier"]),
            int(item[1]["priority"]),
            -min(item[0], 1e9),
            str(item[1]["rank"]),
        )
    )
    return [target for _, target in scored]


def estimate_plan(planned: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    targets = list(planned)
    groups: dict[str, int] = {}
    for target in targets:
        key = f"{target['format']}_{target['rank']}"
        groups[key] = groups.get(key, 0) + 1
    return {
        "targets": len(targets),
        "requests_per_week_by_ttl": round(len(targets) * 168 / TTL_HOURS, 1),
        "groups": dict(sorted(groups.items())),
    }


# --- refresh --------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


async def _alert_premium(detail: str) -> None:
    try:
        from .fetcher import send_telegram_alert

        await send_telegram_alert(
            SOURCE_ID, PREMIUM_ALERT_STATE, detail, f"{HSREPLAY}/cards/"
        )
    except Exception as exc:  # noqa: BLE001 - an alert must never break the run
        logger.warning("HSReplay card choices alert failed: %s", type(exc).__name__)


async def _refresh_unlocked(
    *,
    settings: Settings,
    call_json: JsonFetcher | None,
    now: datetime | None,
    max_requests: int | None,
    base: pools.CardBase | None,
    times_played: Mapping[str, Mapping[int, int]] | None,
    patch_at: datetime | None,
) -> dict[str, Any]:
    started = now or _now()
    started_at = started.isoformat()

    def clock() -> datetime:
        return now or _now()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.deadline_seconds
    card_base = base or pools.load_card_base()
    patch = patch_at if patch_at is not None else latest_patch_at()
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
            credit_cap=credit_cap,
            concurrency=settings.concurrency,
            call=call_json,
            usage_source=SOURCE_ID,
        )
        counters = {"requests": 0, "credits": 0}

        async def counted(url: str) -> FetchResult:
            result = await client.fetch(url)
            if result.outcome != "budget":
                counters["requests"] += 1
                counters["credits"] += max(0, int(result.credits or 0))
            return result

        premium = await probe_premium(counted)
        previous = premium_session_from_run(store.latest_finished_run(conn))
        if premium["state"] == "unknown" and previous["state"] in PREMIUM_BAD_STATES:
            premium = {
                **previous,
                "detail": f"{previous['detail']} (kept: {premium['detail']})",
            }
        planned = plan_targets(card_base, settings, times_played=times_played)
        store.sync_targets(conn, planned, planned_at=started_at)
        due = due_targets(store.active_targets(conn), started, patch)
        counts = {
            "attempted": 0,
            "changed": 0,
            "unchanged": 0,
            "no_data": 0,
            "failed": 0,
            "processing": 0,
        }
        outcomes: dict[str, int] = {}
        stop_reason: str | None = None
        if premium["state"] in PREMIUM_BAD_STATES:
            stop_reason = "premium_session"
            await _alert_premium(str(premium["detail"]))
        queue = list(due)
        lock = asyncio.Lock()
        failures_in_row = 0

        async def handle(target: dict[str, Any]) -> None:
            nonlocal failures_in_row, stop_reason
            url = choices_api_url(
                int(target["dbf_id"]), str(target["format"]), str(target["rank"])
            )
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
                    rows, unidentified, as_of = (
                        parse_choices_payload(
                            result.payload, cards_by_dbf=card_base.by_dbf
                        )
                        if result.outcome == "ok"
                        else ([], 0, None)
                    )
                except PayloadRejected as exc:
                    result = FetchResult("error", detail=f"contract: {exc}")
                else:
                    async with lock:
                        failures_in_row = 0
                    _, changed = store.record_success(
                        conn,
                        target,
                        fetched_at=attempted_at,
                        as_of=as_of,
                        rows=rows,
                        unidentified_rows=unidentified,
                        content_hash=content_hash(rows),
                        source_url=url,
                        backend="scrape_do",
                        request_credits=result.credits,
                        state="ok" if rows else "no_data",
                    )
                    if not rows:
                        counts["no_data"] += 1
                    counts["changed" if changed else "unchanged"] += 1
                    return
            if result.outcome == "account":
                async with lock:
                    stop_reason = "scrape_do_account"
            counts["processing" if result.outcome == "processing" else "failed"] += 1
            failures = int(target.get("consecutive_failures") or 0)
            if result.outcome == "denied":
                retry = clock() + timedelta(hours=PREMIUM_DENIED_RETRY_HOURS)
                state, count_failure = "premium_denied", False
            elif result.outcome == "unsupported":
                retry = clock() + timedelta(hours=UNSUPPORTED_RETRY_HOURS)
                state, count_failure = "unsupported", False
            else:
                retry = _retry_after(failures, clock())
                state = "processing" if result.outcome == "processing" else "failed"
                count_failure = True
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
                await handle(target)

        await asyncio.gather(*(worker() for _ in range(settings.concurrency)))
        pruned = store.prune(conn, now=started, snapshot_days=settings.snapshot_days)
        coverage = store.coverage(conn)
        succeeded = counts["changed"] + counts["unchanged"]
        if not planned or (counts["failed"] and not succeeded):
            state = SourceState.FETCH_ERROR
        elif counts["failed"] or premium["state"] in PREMIUM_BAD_STATES:
            state = SourceState.PARTIAL
        else:
            state = SourceState.OK
        finished_at = clock().isoformat()
        summary = {
            "finished_at": finished_at,
            "state": str(state),
            "premium_state": premium["state"],
            "targets_planned": len(planned),
            "targets_due": len(due),
            **{
                key: counts[key]
                for key in ("attempted", "changed", "unchanged", "no_data", "failed")
            },
            "requests": counters["requests"],
            "scrape_do_credits": counters["credits"],
            "stop_reason": stop_reason,
            "detail": {
                "premium": premium,
                "outcomes": outcomes,
                "processing": counts["processing"],
                "scrape_do_credit_cap": credit_cap,
                "scrape_do_spent_today_before_run": spent_today,
                "patch_at": patch.isoformat() if patch else None,
                "pruned": pruned,
            },
        }
        store.finish_run(conn, run_id, summary)
    finally:
        conn.close()

    with_data = sum(int(row["with_data"] or 0) for row in coverage if row["active"])
    active_total = sum(int(row["targets"] or 0) for row in coverage if row["active"])
    remaining_due = max(0, len(due) - counts["attempted"])
    published = bool(planned) and (succeeded > 0 or not counts["failed"])
    if published:
        save_dataset(
            SOURCE_ID,
            {
                "state": SourceState.OK,
                "fetched_at": finished_at,
                "http_status": 200,
                "final_url": f"{HSREPLAY}/cards/",
                "backend": "hsreplay_card_choices_store",
                "data": {
                    "structured": {
                        "type": SOURCE_ID,
                        "fetched_at": finished_at,
                        "targets_active": active_total,
                        "targets_with_data": with_data,
                        "targets_due_after_run": remaining_due,
                        "premium_session": premium,
                        "coverage": coverage,
                        "last_run": {**summary, "started_at": started_at},
                    }
                },
            },
        )
    previous_dataset = load_dataset(SOURCE_ID) or {}
    status = {
        "source_id": SOURCE_ID,
        "site": "hsreplay",
        "category": "ranked_cards",
        "url": f"{HSREPLAY}/cards/",
        "state": str(
            state
            if premium["state"] in PREMIUM_BAD_STATES or not published
            else SourceState.OK
        ),
        "fetched_at": finished_at
        if published
        else previous_dataset.get("fetched_at") or finished_at,
        "http_status": 200 if succeeded else None,
        "backend": "scrape_do",
        "rows_total": with_data,
        "detail": (
            f"HSReplay card choices: {succeeded}/{counts['attempted']} targets ok, "
            f"{counts['no_data']} without data, {remaining_due} still due, "
            f"stop={stop_reason or 'done'}, scrape.do credits {counters['credits']}/{credit_cap}"
            + (
                f"; PREMIUM SESSION {premium['state'].upper()}: {premium['detail']}"
                if premium["state"] in PREMIUM_BAD_STATES
                else ""
            )
        ),
        "premium_session": premium,
        "serving_cached_dataset": not published and bool(previous_dataset),
        "last_refresh_state": str(state),
        "last_refresh_at": finished_at,
    }
    if counts["failed"] or premium["state"] in PREMIUM_BAD_STATES:
        status["last_refresh_failure_class"] = str(SourceState.FETCH_ERROR)
    save_status(SOURCE_ID, status)
    return {
        "ok": state == SourceState.OK,
        "state": str(state),
        "published": published,
        "source_id": SOURCE_ID,
        "fetched_at": finished_at,
        "premium_session": premium,
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


async def refresh_hsreplay_card_choices(
    *,
    settings: Settings | None = None,
    call_json: JsonFetcher | None = None,
    now: datetime | None = None,
    max_requests: int | None = None,
    base: pools.CardBase | None = None,
    times_played: Mapping[str, Mapping[int, int]] | None = None,
    patch_at: datetime | None = None,
) -> dict[str, Any]:
    """Read the due HSReplay card choice targets unless another run owns them."""

    locks = ResourceLockSet(
        [SOURCE_ID], metadata={"operation": "refresh_hsreplay_card_choices"}
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
            base=base,
            times_played=times_played,
            patch_at=patch_at,
        )
    finally:
        locks.release()


# --- reads ----------------------------------------------------------------------


class CardNotFound(LookupError):
    pass


def _read(callback: Callable[[Any], Any]) -> Any:
    init_db()
    conn = get_db_connection()
    try:
        return callback(conn)
    finally:
        conn.close()


def _load_stored(
    conn: Any, card_id: str, format_name: str, rank: str
) -> dict[str, Any]:
    """Same keys as ``PostgresGraphQLRepository.hsreplay_card_choices``."""

    found: dict[str, Any] = {
        "premium_session": premium_session_from_run(store.latest_finished_run(conn)),
        "available": [
            {"format": row["format"], "rank": row["rank"]}
            for row in store.card_targets(conn, card_id)
            if row["latest_snapshot_id"] is not None
        ],
    }
    target = next(
        (
            row
            for row in store.card_targets(conn, card_id)
            if row["format"] == format_name and row["rank"] == rank
        ),
        None,
    )
    if target is None:
        return found
    found["target"] = target
    if target.get("latest_snapshot_id") is not None:
        found["snapshot"] = store.snapshot(conn, int(target["latest_snapshot_id"]))
        found["rows"] = store.snapshot_rows(conn, int(target["latest_snapshot_id"]))
    return found


def _edge(value: float | None) -> bool:
    return value is None or float(value) in EDGE_VALUES


def _mark_options(options: list[dict[str, Any]]) -> None:
    """Flag thin samples and the undervalued picks (higher winrate, taken less)."""

    for option in options:
        option["low_sample"] = _edge(option.get("popularity")) or _edge(
            option.get("win_rate")
        )
        option["undervalued"] = False
    solid = [option for option in options if not option["low_sample"]]
    if len(solid) < 3:
        return
    mean_winrate = statistics.fmean(float(option["win_rate"]) for option in solid)
    median_popularity = statistics.median(
        float(option["popularity"]) for option in solid
    )
    for option in solid:
        option["undervalued"] = (
            float(option["win_rate"]) >= mean_winrate + UNDERVALUED_WINRATE_MARGIN
            and float(option["popularity"]) < median_popularity
        )


def _target_stale(target: Mapping[str, Any] | None, now: datetime) -> bool:
    checked = _parse_time((target or {}).get("last_success_at"))
    if checked is None:
        return True
    return (now - checked).total_seconds() / 3600 > 2 * TTL_HOURS


def assemble(
    base: pools.CardBase,
    card: Mapping[str, Any],
    format_name: str,
    rank: str,
    stored: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    kind = pools.choice_kind(card)
    pool = pools.card_pool(base, card, format_name)
    target = stored.get("target")
    snapshot = stored.get("snapshot")
    rows = list(stored.get("rows") or [])
    pool_by_name = {str(item.get("name")): item for item in pool.get("cards") or []}
    options: list[dict[str, Any]] = []
    for row in rows:
        option_card = base.by_dbf.get(int(row["dbf_id"]))
        described = (
            pools.option_row(base, option_card)
            if option_card
            else {"dbf_id": int(row["dbf_id"]), "card_id": row.get("card_id")}
        )
        name = described.get("name")
        options.append(
            {
                **described,
                "popularity": row.get("popularity"),
                "win_rate": row.get("win_rate"),
                "in_pool": bool(name and name in pool_by_name)
                if pool.get("resolved")
                else None,
            }
        )
    _mark_options(options)
    stats_by_name = {str(option.get("name")): option for option in options}
    pool_cards = []
    for item in pool.get("cards") or []:
        stat = stats_by_name.get(str(item.get("name")))
        pool_cards.append(
            {
                **item,
                "has_stats": stat is not None and not stat["low_sample"],
                "popularity": stat.get("popularity") if stat else None,
                "win_rate": stat.get("win_rate") if stat else None,
            }
        )
    if kind not in pools.STATS_KINDS:
        stats_state = "not_tracked"
    elif target is None:
        stats_state = "not_collected"
    elif snapshot is None:
        stats_state = str(target.get("state") or "pending")
    else:
        stats_state = "ok" if rows else "no_data"
    moment = now or _now()
    return {
        "source": "hsreplay",
        "card": {
            "card_id": str(card["id"]),
            "dbf_id": int(card["dbfId"]),
            "name": card.get("name"),
            "name_ru": base.name_ru(card),
            "kind": kind,
            "card_class": pools.card_classes(card)[0],
            "set": card.get("set"),
            "formats": base.formats_of(card),
            "text": pools.clean_text(card.get("text")),
        },
        "format": format_name,
        "rank": rank,
        "stats": {
            "state": stats_state,
            "tracked": kind in pools.STATS_KINDS,
            "checked_at": (target or {}).get("last_success_at"),
            "changed_at": (snapshot or {}).get("fetched_at"),
            "as_of": (snapshot or {}).get("as_of"),
            "stale": _target_stale(target, moment)
            if kind in pools.STATS_KINDS
            else False,
            "source_url": card_page_url(int(card["dbfId"])),
            "options_total": len(options),
            "unidentified_rows": int((snapshot or {}).get("unidentified_rows") or 0),
        },
        "options": options,
        "pool": {
            "kind": pool.get("kind"),
            "resolved": bool(pool.get("resolved")),
            "reason": pool.get("reason"),
            "class_dependent": bool(pool.get("class_dependent")),
            "total": len(pool_cards),
            "cards": pool_cards,
            "tiers": pool.get("tiers") or [],
        },
        "available": list(stored.get("available") or []),
        "premium_session": stored.get("premium_session"),
    }


def resolve_card(base: pools.CardBase, ref: str) -> Mapping[str, Any]:
    card = base.resolve(ref)
    if card is None or not card.get("collectible"):
        raise CardNotFound(f"card {ref!r} not found")
    if pools.choice_kind(card) is None:
        raise CardNotFound(f"card {card['id']} offers no choice")
    return card


def default_format(base: pools.CardBase, card: Mapping[str, Any]) -> str:
    return "standard" if base.is_standard(card) else "wild"


def get_card_choices(
    ref: str,
    *,
    format_name: str | None = None,
    rank: str = "legend",
    base: pools.CardBase | None = None,
) -> dict[str, Any]:
    card_base = base or pools.load_card_base()
    card = resolve_card(card_base, ref)
    chosen = format_name or default_format(card_base, card)
    stored = _read(lambda conn: _load_stored(conn, str(card["id"]), chosen, rank))
    return assemble(card_base, card, chosen, rank, stored)


def list_choice_cards(
    *,
    format_name: str | None = None,
    kind: str | None = None,
    base: pools.CardBase | None = None,
) -> list[dict[str, Any]]:
    card_base = base or pools.load_card_base()
    collected = _read(store.collected_cards)
    rows = []
    for card in pools.choice_cards(card_base, format_name):
        if kind is not None and card["kind"] != kind:
            continue
        rows.append(
            {
                **card,
                "collected": [
                    {
                        "format": row["format"],
                        "rank": row["rank"],
                        "checked_at": row["last_success_at"],
                    }
                    for row in collected.get(card["card_id"], [])
                ],
            }
        )
    return rows


def coverage_report() -> dict[str, Any]:
    def build(conn: Any) -> dict[str, Any]:
        return {
            "coverage": store.coverage(conn),
            "last_run": store.latest_finished_run(conn),
        }

    return _read(build)


def latest_revision() -> str | None:
    try:
        return _read(store.latest_revision)
    except Exception:  # noqa: BLE001 - a cache revision must never break a read
        return None
