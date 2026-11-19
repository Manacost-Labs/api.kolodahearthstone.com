"""HSReplay Assemble statistics: which base and donors players meet and what they take.

HSReplay's *Assemble Pools* pages (``/cards/assemble-pools/`` -> card -> base)
are fed by one query,
``/analytics/query/assemble_choices_by_middle/?GameType&TimeRange`` (no premium,
plain Scrape.do read for one credit; verified live on 2026-11-19). The answer is
``{"render_as": "table", "as_of": ..., "series": {"data": {"<assemble dbf>": [rows]}}}``
with one row per (base, donor shown at ``option_position`` 1 or 2):

``middle_dbf_id`` (the base), ``times_middle_offered`` (how often the base was
the middle one, equal in all rows of the base), ``option_dbf_id`` (the donor),
``times_offered`` (how often the donor stood at this position next to the base),
``effect_picks`` / ``trigger_picks`` (how often its effect or its trigger was
taken) and ``effect_win_rate`` / ``trigger_win_rate`` (percent of won games, or
``null`` without picks). The ``LeagueRankRange`` filter is ignored (the answer is
byte-identical) and there is no "original class only" dimension.

The two positions of a donor are merged (win rates weighted by picks), so the
snapshot keeps counts and rates, never percentages. Texts of the cards are built
from the card base at read time (:mod:`app.assemble_pools`). A candidate that
breaks a publication check is never published: the last good snapshot (LKG)
stays and the status says why (``fetch_error``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from . import card_choice_pools as pools
from .assemble_pools import AssembleTexts, assemble_cards, is_assemble_card
from .hsreplay_mulligan import (
    JsonFetcher,
    ScrapeDoClient,
    _parse_time,
    _utc_day_start,
)
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .storage import load_dataset, load_status, save_dataset, save_status

logger = logging.getLogger(__name__)

SOURCE_ID = "hsreplay_assemble"
HSREPLAY = "https://hsreplay.net"
QUERY_API = f"{HSREPLAY}/analytics/query/assemble_choices_by_middle/"
SOURCE_URL = f"{HSREPLAY}/cards/assemble-pools/"
FORMATS = {"standard": "RANKED_STANDARD", "wild": "RANKED_WILD"}
TIME_RANGE = "LAST_7_DAYS"
# A format is read again when its last good read is older than this.
MAX_AGE_HOURS = 20
# The API calls a snapshot stale after this.
STALE_HOURS = 48
# Fewer picks are marked with ``*`` on HSReplay.
LOW_SAMPLE_PICKS = 10
UNDERVALUED_WINRATE_MARGIN = 1.0
# Publication checks (percent).
BASES_OFFER_RANGE = (95.0, 105.0)
DONORS_OFFER_RANGE = (190.0, 210.0)
ANY_HALF_PICK_RANGE = (48.0, 52.0)
MIN_CHECKED_BASE_OFFER = 1.0
MAX_UNRESOLVED_SHARE = 0.05


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 1000) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class Settings:
    formats: tuple[str, ...] = ("standard", "wild")
    scrape_do_daily_credits: int = 6
    max_age_hours: int = MAX_AGE_HOURS

    @classmethod
    def from_env(cls) -> Settings:
        base = cls()
        raw = os.environ.get("HS_ASSEMBLE_FORMATS", "")
        formats = tuple(
            dict.fromkeys(
                name
                for name in (part.strip().lower() for part in raw.split(","))
                if name in FORMATS
            )
        )
        return cls(
            formats=formats or base.formats,
            scrape_do_daily_credits=_env_int(
                "HS_ASSEMBLE_SCRAPE_DO_DAILY_CREDITS", base.scrape_do_daily_credits
            ),
        )


def query_url(format_name: str) -> str:
    return f"{QUERY_API}?" + urlencode(
        {"GameType": FORMATS[format_name], "TimeRange": TIME_RANGE}
    )


# --- parsing --------------------------------------------------------------------


class PayloadRejected(ValueError):
    """The body is JSON but not an Assemble answer (contract drift or an error page)."""


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _win(value: Any, picks: int) -> float:
    """A winrate (percent) is required, finite and within 0-100 once there are picks."""

    if not picks:
        return 0.0
    number = _num(value)
    if number is None or not math.isfinite(number) or not 0.0 <= number <= 100.0:
        raise PayloadRejected("a winrate is missing or outside 0-100 despite picks")
    return number


def _whole(value: Any, what: str) -> int:
    """A finite non-negative whole number (an integral float such as 5.0 is fine)."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadRejected(f"{what} is not a number")
    if isinstance(value, float) and not (math.isfinite(value) and value.is_integer()):
        raise PayloadRejected(f"{what} is not a whole number")
    if value < 0:
        raise PayloadRejected(f"{what} is negative")
    return int(value)


def _count(value: Any) -> int:
    return _whole(value, "a count")


def _dbf(value: Any) -> int:
    return _whole(value, "a card id")


# One merged row: [donor dbf, offered, effect picks, trigger picks, effect win, trigger win]
# One base: [base dbf, times the base was the middle one, [donor rows]]
Rows = dict[str, list[list[Any]]]


def parse_payload(payload: Any) -> tuple[Rows, str | None]:
    """The normalized rows per Assemble card dbf (as strings) and HSReplay's ``as_of``."""

    series = payload.get("series") if isinstance(payload, dict) else None
    data = series.get("data") if isinstance(series, dict) else None
    if not isinstance(data, dict) or not data:
        raise PayloadRejected("assemble payload has no series.data object")
    cards: Rows = {}
    for key, rows in data.items():
        try:
            card_dbf = int(key)
        except (TypeError, ValueError):
            raise PayloadRejected("series.data key is not a card id") from None
        if not isinstance(rows, list):
            raise PayloadRejected("series.data value is not a list")
        middles: dict[int, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise PayloadRejected("a row is not an object")
            middle = _dbf(row.get("middle_dbf_id"))
            donor = _dbf(row.get("option_dbf_id"))
            offered_middle = _count(row.get("times_middle_offered"))
            entry = middles.setdefault(
                middle, {"offered": offered_middle, "donors": {}}
            )
            if entry["offered"] != offered_middle:
                raise PayloadRejected("times_middle_offered differs inside one base")
            effect_picks = _count(row.get("effect_picks"))
            trigger_picks = _count(row.get("trigger_picks"))
            merged = entry["donors"].setdefault(donor, [0, 0, 0, 0.0, 0.0])
            merged[0] += _count(row.get("times_offered"))
            merged[1] += effect_picks
            merged[2] += trigger_picks
            merged[3] += _win(row.get("effect_win_rate"), effect_picks) * effect_picks
            merged[4] += (
                _win(row.get("trigger_win_rate"), trigger_picks) * trigger_picks
            )
        cards[str(card_dbf)] = [
            [
                middle,
                entry["offered"],
                [
                    [
                        donor,
                        m[0],
                        m[1],
                        m[2],
                        round(m[3] / m[1], 6) if m[1] else None,
                        round(m[4] / m[2], 6) if m[2] else None,
                    ]
                    for donor, m in sorted(entry["donors"].items())
                ],
            ]
            for middle, entry in sorted(middles.items())
        ]
    as_of = payload.get("as_of")
    return cards, str(as_of) if as_of else None


def content_hash(cards: Rows) -> str:
    return hashlib.sha256(
        json.dumps(cards, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


# --- publication checks ---------------------------------------------------------


def check_snapshot(
    cards: Rows,
    base: pools.CardBase,
    format_name: str,
    *,
    texts: AssembleTexts | None = None,
) -> list[str]:
    """Reasons the candidate must not be published (empty when it is sound)."""

    texts = texts or AssembleTexts(base)
    reasons: list[str] = []
    expected = assemble_cards(base, format_name)
    missing = [
        str(card["id"]) for card in expected if not cards.get(str(card["dbfId"]))
    ]
    if missing:
        reasons.append(f"missing_cards: {', '.join(missing)}")
    rows_total = rows_unresolved = 0
    for card in expected:
        bases = cards.get(str(card["dbfId"]))
        if not bases:
            continue
        name = str(card["id"])
        middle_total = sum(int(entry[1]) for entry in bases)
        if middle_total <= 0:
            reasons.append(f"{name}: no base offers")
            continue
        # Bases the card base can show must cover about all the offers.
        offers = sum(
            int(entry[1]) / middle_total * 100
            for entry in bases
            if texts.resolvable(entry[0])
        )
        if not BASES_OFFER_RANGE[0] <= offers <= BASES_OFFER_RANGE[1]:
            reasons.append(f"{name}: bases offer sum {offers:.1f}")
        offered_sum = picks_sum = 0
        for middle, middle_offered, donors in bases:
            offered_sum += sum(int(donor[1]) for donor in donors)
            picks_sum += sum(int(donor[2]) + int(donor[3]) for donor in donors)
            share = int(middle_offered) / middle_total * 100
            if middle_offered and share >= MIN_CHECKED_BASE_OFFER:
                donor_offer = (
                    sum(int(donor[1]) for donor in donors) / middle_offered * 100
                )
                if not DONORS_OFFER_RANGE[0] <= donor_offer <= DONORS_OFFER_RANGE[1]:
                    reasons.append(
                        f"{name}: base {middle} donors offer sum {donor_offer:.1f}"
                    )
            rows_total += len(donors)
            if not texts.resolvable(middle):
                rows_unresolved += len(donors)
                continue
            rows_unresolved += sum(
                1 for donor in donors if not texts.resolvable(donor[0])
            )
        any_half = picks_sum / offered_sum * 100 if offered_sum else 0.0
        if not ANY_HALF_PICK_RANGE[0] <= any_half <= ANY_HALF_PICK_RANGE[1]:
            reasons.append(f"{name}: any-half pick rate {any_half:.1f}")
    if rows_total and rows_unresolved / rows_total > MAX_UNRESOLVED_SHARE:
        reasons.append(
            f"unresolved rows {rows_unresolved}/{rows_total} "
            f"({rows_unresolved / rows_total:.1%})"
        )
    return reasons


# --- refresh --------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _stored_formats(dataset: Mapping[str, Any] | None) -> dict[str, Any]:
    structured = ((dataset or {}).get("data") or {}).get("structured") or {}
    formats = structured.get("formats") if isinstance(structured, dict) else None
    return dict(formats) if isinstance(formats, dict) else {}


def is_due(
    format_name: str,
    snapshot: Mapping[str, Any] | None,
    state: Mapping[str, Any] | None,
    now: datetime,
    max_age_hours: int = MAX_AGE_HOURS,
) -> bool:
    """Read again when never read, the last good read is old or the last attempt failed."""

    checked = _parse_time((snapshot or {}).get("checked_at"))
    if checked is None:
        return True
    if (state or {}).get("last_outcome") not in (None, "ok"):
        return True
    return (now - checked).total_seconds() / 3600 > max_age_hours


def _spent_today(status: Mapping[str, Any], now: datetime) -> int:
    ledger = status.get("credits_ledger")
    if not isinstance(ledger, dict):
        return 0
    if ledger.get("day") != _utc_day_start(now)[:10]:
        return 0
    try:
        return max(0, int(ledger.get("spent") or 0))
    except (TypeError, ValueError):
        return 0


async def _refresh_unlocked(
    *,
    settings: Settings,
    call_json: JsonFetcher | None,
    now: datetime | None,
    max_requests: int | None,
    base: pools.CardBase | None,
) -> dict[str, Any]:
    started = now or _now()

    def clock() -> datetime:
        return now or _now()

    card_base = base or pools.load_card_base()
    texts = AssembleTexts(card_base)
    previous_dataset = load_dataset(SOURCE_ID) or {}
    previous_status = load_status(SOURCE_ID) or {}
    snapshots = _stored_formats(previous_dataset)
    format_state: dict[str, Any] = {
        name: dict(value)
        for name, value in (previous_status.get("formats") or {}).items()
        if isinstance(value, dict)
    }
    spent_before = _spent_today(previous_status, started)
    caps = [settings.scrape_do_daily_credits - spent_before]
    if max_requests is not None:
        caps.append(max_requests)
    credit_cap = max(0, min(caps))
    client = ScrapeDoClient(
        credit_cap=credit_cap,
        concurrency=1,
        call=call_json,
        usage_source=SOURCE_ID,
    )
    outcomes: dict[str, str] = {}
    failures: dict[str, str] = {}
    published: list[str] = []
    stop_reason: str | None = None
    for format_name in settings.formats:
        if not is_due(
            format_name,
            snapshots.get(format_name),
            format_state.get(format_name),
            started,
            settings.max_age_hours,
        ):
            outcomes[format_name] = "fresh"
            continue
        result = await client.fetch(query_url(format_name))
        attempted_at = clock().isoformat()
        if result.outcome == "budget":
            stop_reason = "scrape_do_budget"
            outcomes[format_name] = "budget"
            failures[format_name] = "daily Scrape.do credit budget exhausted"
            break
        outcome, error = result.outcome, result.detail or result.outcome
        candidate: dict[str, Any] | None = None
        if result.outcome == "ok":
            try:
                cards, as_of = parse_payload(result.payload)
            except PayloadRejected as exc:
                outcome, error = "contract", f"contract: {exc}"
            else:
                reasons = check_snapshot(cards, card_base, format_name, texts=texts)
                if reasons:
                    outcome, error = "rejected", "checks: " + "; ".join(reasons)[:900]
                else:
                    candidate = {
                        "format": format_name,
                        "game_type": FORMATS[format_name],
                        "time_range": TIME_RANGE,
                        "as_of": as_of,
                        "checked_at": attempted_at,
                        "content_hash": content_hash(cards),
                        "cards": cards,
                    }
        outcomes[format_name] = outcome
        entry = {
            **format_state.get(format_name, {}),
            "last_attempt_at": attempted_at,
            "last_outcome": "ok" if candidate else outcome,
            "last_error": None if candidate else error,
            "credits": result.credits,
        }
        format_state[format_name] = entry
        if candidate is None:
            failures[format_name] = error
            if result.outcome == "account":
                stop_reason = "scrape_do_account"
                break
            continue
        snapshots[format_name] = candidate
        entry["last_success_at"] = attempted_at
        published.append(format_name)

    finished_at = clock().isoformat()
    newest = max(
        (
            str(item["checked_at"])
            for item in snapshots.values()
            if item.get("checked_at")
        ),
        default=None,
    )
    if published:
        save_dataset(
            SOURCE_ID,
            {
                "state": SourceState.OK,
                "fetched_at": newest,
                "http_status": 200,
                "final_url": SOURCE_URL,
                "backend": "scrape_do",
                "data": {
                    "structured": {
                        "type": SOURCE_ID,
                        "fetched_at": newest,
                        "game_types": {
                            name: item.get("game_type")
                            for name, item in snapshots.items()
                        },
                        "cards_with_data": sum(
                            1
                            for item in snapshots.values()
                            for rows in item["cards"].values()
                            if rows
                        ),
                        "formats": snapshots,
                    }
                },
            },
        )
    attempted = [name for name, value in outcomes.items() if value != "fresh"]
    if not failures:
        state = SourceState.OK
    elif published:
        state = SourceState.PARTIAL
    else:
        state = SourceState.FETCH_ERROR
    spent_total = spent_before + client.credits
    per_format = ", ".join(
        f"{name}={outcomes.get(name, 'skipped')}" for name in settings.formats
    )
    detail = (
        f"HSReplay Assemble: {per_format}, stop={stop_reason or 'done'}, "
        f"scrape.do credits {client.credits}/{credit_cap}"
    )
    if failures:
        detail += "; " + "; ".join(f"{name}: {text}" for name, text in failures.items())
    status = {
        "source_id": SOURCE_ID,
        "site": "hsreplay",
        "category": "ranked_cards",
        "url": SOURCE_URL,
        "state": str(state),
        "fetched_at": newest or finished_at,
        "http_status": 200 if snapshots else None,
        "backend": "scrape_do",
        "rows_total": sum(
            1 for item in snapshots.values() for rows in item["cards"].values() if rows
        ),
        "detail": detail[:1500],
        "serving_cached_dataset": bool(failures) and bool(snapshots),
        "last_refresh_state": str(state),
        "last_refresh_at": finished_at,
        "formats": format_state,
        "credits_ledger": {"day": _utc_day_start(started)[:10], "spent": spent_total},
    }
    if failures:
        status["last_refresh_failure_class"] = str(SourceState.FETCH_ERROR)
    save_status(SOURCE_ID, status)
    return {
        "ok": not failures,
        "state": str(state),
        "published": bool(published),
        "source_id": SOURCE_ID,
        "fetched_at": newest,
        "formats": outcomes,
        "published_formats": published,
        "failures": failures,
        "attempted": len(attempted),
        "requests": client.requests,
        "scrape_do_credits": client.credits,
        "scrape_do_credit_cap": credit_cap,
        "scrape_do_spent_today": spent_total,
        "stop_reason": stop_reason,
    }


async def refresh_hsreplay_assemble(
    *,
    settings: Settings | None = None,
    call_json: JsonFetcher | None = None,
    now: datetime | None = None,
    max_requests: int | None = None,
    base: pools.CardBase | None = None,
) -> dict[str, Any]:
    """Read the due formats (one request each) unless another run owns the source."""

    locks = ResourceLockSet(
        [SOURCE_ID], metadata={"operation": "refresh_hsreplay_assemble"}
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
        )
    finally:
        locks.release()


# --- reads ----------------------------------------------------------------------


class CardNotFound(LookupError):
    pass


class BaseNotFound(LookupError):
    pass


def _pct(part: float, whole: float) -> float | None:
    return part / whole * 100 if whole else None


def card_ref(base: pools.CardBase, card: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "card_id": str(card["id"]),
        "dbf_id": int(card["dbfId"]),
        "name": card.get("name"),
        "name_ru": base.name_ru(card),
        "card_class": pools.card_classes(card)[0],
        "cost": card.get("cost"),
        "type": card.get("type"),
        "rarity": card.get("rarity"),
    }


def _mark_options(options: list[dict[str, Any]]) -> None:
    """Flag the undervalued halves (higher winrate, taken less) among solid ones."""

    for option in options:
        option["undervalued"] = False
    solid = [
        option
        for option in options
        if not option["low_sample"]
        and option["win_rate"] is not None
        and option["pick_rate"] is not None
    ]
    if len(solid) < 3:
        return
    mean_winrate = statistics.fmean(float(option["win_rate"]) for option in solid)
    median_pick = statistics.median(float(option["pick_rate"]) for option in solid)
    for option in solid:
        option["undervalued"] = (
            float(option["win_rate"]) >= mean_winrate + UNDERVALUED_WINRATE_MARGIN
            and float(option["pick_rate"]) < median_pick
        )


def _donor(
    texts: AssembleTexts,
    base_dbf: int,
    base_offered: int,
    row: list[Any],
) -> dict[str, Any]:
    donor_dbf, offered, effect_picks, trigger_picks, effect_win, trigger_win = row
    picks = int(effect_picks) + int(trigger_picks)
    any_win = (
        (
            float(effect_win or 0) * effect_picks
            + float(trigger_win or 0) * trigger_picks
        )
        / picks
        if picks
        else None
    )
    options = []
    for half, half_picks, win in (
        ("effect", int(effect_picks), effect_win),
        ("trigger", int(trigger_picks), trigger_win),
    ):
        options.append(
            {
                "half": half,
                "text": texts.option(base_dbf, int(donor_dbf), half),
                "pick_rate": _pct(half_picks, offered),
                "win_rate": float(win) if win is not None and half_picks else None,
                "picks": half_picks,
                "low_sample": half_picks < LOW_SAMPLE_PICKS,
                "undervalued": False,
            }
        )
    return {
        "_dbf": int(donor_dbf),
        "offer_rate": _pct(int(offered), int(base_offered)),
        "pick_rate": _pct(picks, offered),
        "win_rate": any_win,
        "picks": picks,
        "low_sample": picks < LOW_SAMPLE_PICKS,
        "options": options,
    }


def summarize_card(
    base: pools.CardBase,
    texts: AssembleTexts,
    rows: list[list[Any]],
) -> dict[str, Any]:
    """Bases with their donors (texts, rates), the card's winrate and unresolved rows."""

    middle_total = sum(int(entry[1]) for entry in rows)
    bases: list[dict[str, Any]] = []
    unresolved = 0
    won = picked = 0.0
    for middle_dbf, middle_offered, donor_rows in rows:
        base_card = base.by_dbf.get(int(middle_dbf))
        if base_card is None or not texts.resolvable(int(middle_dbf)):
            unresolved += len(donor_rows)
            continue
        donors: list[dict[str, Any]] = []
        for row in donor_rows:
            donor_card = base.by_dbf.get(int(row[0]))
            if donor_card is None or not texts.resolvable(int(row[0])):
                unresolved += 1
                continue
            donor = _donor(texts, int(middle_dbf), int(middle_offered), row)
            donor["card"] = card_ref(base, donor_card)
            donors.append(donor)
        options = [option for donor in donors for option in donor["options"]]
        _mark_options(options)
        base_picks = sum(option["picks"] for option in options)
        base_won = sum(
            option["win_rate"] * option["picks"]
            for option in options
            if option["win_rate"] is not None
        )
        won += base_won
        picked += base_picks
        donors.sort(key=lambda donor: (-(donor["offer_rate"] or 0), donor["_dbf"]))
        bases.append(
            {
                "card": card_ref(base, base_card),
                "_dbf": int(middle_dbf),
                "offer_rate": _pct(int(middle_offered), middle_total),
                "win_rate": base_won / base_picks if base_picks else None,
                "low_sample": all(option["low_sample"] for option in options),
                "donors_total": len(donors),
                "donors": donors,
            }
        )
    bases.sort(
        key=lambda item: (
            -(item["offer_rate"] or 0),
            str(item["card"]["name_ru"] or item["card"]["name"] or ""),
            item["_dbf"],
        )
    )
    return {
        "bases": bases,
        "unresolved_rows": unresolved,
        "win_rate": won / picked if picked else None,
    }


def _load_state() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        dataset = load_dataset(SOURCE_ID)
    except (OSError, ValueError):
        dataset = None
    try:
        status = load_status(SOURCE_ID) or {}
    except (OSError, ValueError):
        status = {}
    return _stored_formats(dataset), status


def _stale(checked_at: str | None, now: datetime) -> bool:
    checked = _parse_time(checked_at)
    if checked is None:
        return True
    return (now - checked).total_seconds() / 3600 > STALE_HOURS


def _card_state(
    snapshot: Mapping[str, Any] | None,
    rows: list[Any] | None,
    state: Mapping[str, Any] | None,
    configured: bool,
) -> str:
    """``ok``, ``no_data``, ``not_collected`` or ``failed``."""

    if snapshot is not None:
        return "ok" if rows else "no_data"
    outcome = (state or {}).get("last_outcome")
    if outcome == "no_data":
        return "no_data"
    if outcome not in (None, "ok") and configured:
        return "failed"
    return "not_collected"


def resolve_card(base: pools.CardBase, ref: str) -> Mapping[str, Any]:
    card = base.resolve(ref)
    if card is None or not is_assemble_card(card):
        raise CardNotFound(f"card {ref!r} is not an Assemble card")
    return card


def _is_configured(format_name: str) -> bool:
    try:
        return format_name in Settings.from_env().formats
    except Exception:  # noqa: BLE001 - a settings error only hides "failed"
        return True


def get_assemble(
    ref: str,
    *,
    format_name: str = "standard",
    base_selector: str | None = None,
    base: pools.CardBase | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    card_base = base or pools.load_card_base()
    card = resolve_card(card_base, ref)
    snapshots, status = _load_state()
    snapshot = snapshots.get(format_name)
    format_state = (status.get("formats") or {}).get(format_name)
    rows = ((snapshot or {}).get("cards") or {}).get(str(card["dbfId"]))
    moment = now or _now()
    texts = AssembleTexts(card_base)
    summary = (
        summarize_card(card_base, texts, rows)
        if rows
        else {"bases": [], "unresolved_rows": 0, "win_rate": None}
    )
    bases = summary["bases"]
    wanted: int | None = None
    if base_selector == "top":
        wanted = bases[0]["_dbf"] if bases else None
    elif base_selector is not None:
        wanted = int(base_selector)
        if bases and wanted not in {item["_dbf"] for item in bases}:
            raise BaseNotFound(f"base {wanted} is not in the pool of {card['id']}")
    expanded = base_selector is None
    out_bases = []
    for item in bases:
        shown = expanded or item["_dbf"] == wanted
        donors = None
        if shown:
            donors = [
                {key: value for key, value in donor.items() if key != "_dbf"}
                for donor in item["donors"]
            ]
        out_bases.append(
            {
                key: (donors if key == "donors" else value)
                for key, value in item.items()
                if key != "_dbf"
            }
        )
    checked_at = (snapshot or {}).get("checked_at")
    stats_state = _card_state(snapshot, rows, format_state, _is_configured(format_name))
    data = {
        "card": {
            **card_ref(card_base, card),
            "text": texts.card_text(card, "en"),
            "text_ru": texts.card_text(card, "ru"),
        },
        "format": format_name,
        "game_type": FORMATS[format_name],
        "time_range": TIME_RANGE,
        "original_class_only": False,
        "stats": {
            "state": stats_state,
            "checked_at": checked_at,
            "as_of": (snapshot or {}).get("as_of"),
            "stale": _stale(checked_at, moment) if snapshot else False,
            "source_url": SOURCE_URL,
            "unresolved_rows": summary["unresolved_rows"],
        },
        "summary": {"bases_total": len(bases), "win_rate": summary["win_rate"]},
        "bases": out_bases,
    }
    return data


def list_assemble_cards(
    *,
    format_name: str = "standard",
    base: pools.CardBase | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    card_base = base or pools.load_card_base()
    snapshots, status = _load_state()
    snapshot = snapshots.get(format_name)
    format_state = (status.get("formats") or {}).get(format_name)
    moment = now or _now()
    texts = AssembleTexts(card_base)
    configured = _is_configured(format_name)
    checked_at = (snapshot or {}).get("checked_at")
    result = []
    for card in assemble_cards(card_base, format_name):
        rows = ((snapshot or {}).get("cards") or {}).get(str(card["dbfId"]))
        summary = summarize_card(card_base, texts, rows) if rows else None
        result.append(
            {
                "card": card_ref(card_base, card),
                "formats": card_base.formats_of(card),
                "state": _card_state(snapshot, rows, format_state, configured),
                "checked_at": checked_at,
                "stale": _stale(checked_at, moment) if snapshot else False,
                "bases_total": len(summary["bases"]) if summary else None,
                "win_rate": summary["win_rate"] if summary else None,
            }
        )
    return result


def newest_checked_at(rows: Iterable[Mapping[str, Any]]) -> str | None:
    return max(
        (str(row["checked_at"]) for row in rows if row.get("checked_at")), default=None
    )
