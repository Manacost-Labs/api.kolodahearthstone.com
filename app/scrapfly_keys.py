from __future__ import annotations

import fcntl
import json
import os
import re
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .config import data_dir, scrapfly_default_key_credit_limit, scrapfly_key_reset_day

_KEY_ENTRY_RE = re.compile(
    r"^(?P<label>[^|]+)\|(?P<key>scp-(?:live-)?[A-Za-z0-9]+)(?:\|(?P<limit>\d+))?$"
)


@dataclass(frozen=True)
class ScrapflyKey:
    label: str
    key: str
    credit_limit: int

    @property
    def id(self) -> str:
        return self.label

    @property
    def fingerprint(self) -> str:
        return f"{self.key[:10]}…{self.key[-4:]}"


@dataclass(frozen=True)
class ScrapflyKeyLease:
    key: ScrapflyKey
    credits_used: int
    credits_remaining: int


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def current_credit_period_id(today: date | None = None) -> str:
    current = today or datetime.now(UTC).date()
    reset_day = scrapfly_key_reset_day()
    if current.day >= reset_day:
        year, month = current.year, current.month
    else:
        year, month = current.year, current.month - 1
        if month == 0:
            year -= 1
            month = 12
    day = min(reset_day, monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def next_credit_period_id(today: date | None = None) -> str:
    current = today or datetime.now(UTC).date()
    start = date.fromisoformat(current_credit_period_id(current))
    year, month = start.year, start.month + 1
    if month == 13:
        year += 1
        month = 1
    day = min(scrapfly_key_reset_day(), monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def usage_path() -> Path:
    path = data_dir() / "scrapfly"
    path.mkdir(parents=True, exist_ok=True)
    return path / "key-usage.json"


def _legacy_single_key() -> str | None:
    value = (
        os.environ.get("SCRAPFLY_API_KEY")
        or os.environ.get("HS_SCRAPFLY_API_KEY")
        or ""
    ).strip()
    return value or None


def parse_scrapfly_api_keys(raw: str | None = None) -> list[ScrapflyKey]:
    text = (
        raw if raw is not None else os.environ.get("HS_SCRAPFLY_API_KEYS", "")
    ).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    default_limit = scrapfly_default_key_credit_limit()
    keys: list[ScrapflyKey] = []
    seen: set[str] = set()

    if text:
        for position, part in enumerate(text.split(","), start=1):
            entry = part.strip().strip('"').strip("'")
            if not entry:
                continue
            match = _KEY_ENTRY_RE.match(entry)
            if not match:
                raise ValueError(
                    "Invalid HS_SCRAPFLY_API_KEYS entry at "
                    f"position {position} (expected label|scp-…|limit)"
                )
            label = match.group("label").strip().lower()
            key = match.group("key").strip()
            limit_raw = match.group("limit")
            limit = int(limit_raw) if limit_raw else default_limit
            if label in seen:
                raise ValueError(f"Duplicate Scrapfly key label at position {position}")
            seen.add(label)
            keys.append(ScrapflyKey(label=label, key=key, credit_limit=max(1, limit)))

    if keys:
        return keys

    legacy = _legacy_single_key()
    if not legacy:
        return []
    if not re.fullmatch(r"scp-(?:live-)?[A-Za-z0-9]+", legacy):
        raise ValueError(
            "Invalid SCRAPFLY_API_KEY/HS_SCRAPFLY_API_KEY format (expected scp-…)"
        )
    return [
        ScrapflyKey(
            label="default",
            key=legacy,
            credit_limit=default_limit,
        )
    ]


def _empty_state(
    keys: list[ScrapflyKey], *, period_id: str | None = None
) -> dict[str, Any]:
    period = period_id or current_credit_period_id()
    return {
        "updated_at": _now_iso(),
        "period_id": period,
        "period_reset_at": _now_iso(),
        "next_period_id": next_credit_period_id(),
        "active_label": keys[0].label if keys else None,
        "keys": {
            item.label: {
                "credits_used": 0,
                "credit_limit": item.credit_limit,
                "exhausted": False,
                "fingerprint": item.fingerprint,
                "last_used_at": None,
                "rotated_out_at": None,
            }
            for item in keys
        },
    }


def _first_available_label(
    keys: list[ScrapflyKey], state: dict[str, Any]
) -> str | None:
    for item in keys:
        info = state["keys"].get(item.label) or {}
        if (
            not info.get("exhausted")
            and int(info.get("credits_used") or 0) < item.credit_limit
        ):
            return item.label
    return None


def _merge_state(keys: list[ScrapflyKey], raw: dict[str, Any] | None) -> dict[str, Any]:
    period_id = current_credit_period_id()
    previous_period = raw.get("period_id") if isinstance(raw, dict) else None
    if not raw or previous_period != period_id:
        state = _empty_state(keys, period_id=period_id)
        if previous_period and previous_period != period_id:
            state["previous_period_id"] = previous_period
        return state

    state = _empty_state(keys, period_id=period_id)
    state["period_reset_at"] = raw.get("period_reset_at") or state["period_reset_at"]
    previous = raw.get("keys") if isinstance(raw.get("keys"), dict) else {}
    for item in keys:
        prior = (
            previous.get(item.label)
            if isinstance(previous.get(item.label), dict)
            else {}
        )
        credits_used = int(prior.get("credits_used") or 0)
        exhausted = bool(prior.get("exhausted")) or credits_used >= item.credit_limit
        state["keys"][item.label] = {
            "credits_used": max(0, credits_used),
            "credit_limit": item.credit_limit,
            "exhausted": exhausted,
            "fingerprint": item.fingerprint,
            "last_used_at": prior.get("last_used_at"),
            "rotated_out_at": prior.get("rotated_out_at"),
            "exhaust_reason": prior.get("exhaust_reason"),
        }

    active = raw.get("active_label")
    if (
        isinstance(active, str)
        and active in state["keys"]
        and not state["keys"][active]["exhausted"]
    ):
        state["active_label"] = active
    else:
        state["active_label"] = _first_available_label(keys, state)
    state["updated_at"] = _now_iso()
    if raw.get("previous_period_id"):
        state["previous_period_id"] = raw.get("previous_period_id")
    return state


def _read_state_unlocked(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_state_unlocked(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _with_usage_lock(
    mutate: Callable[
        [list[ScrapflyKey], dict[str, Any] | None],
        tuple[Any, dict[str, Any] | None],
    ],
) -> Any:
    path = usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            keys = parse_scrapfly_api_keys()
            raw = _read_state_unlocked(path) if keys else None
            period_id = current_credit_period_id()
            period_changed = bool(keys) and (
                not raw or raw.get("period_id") != period_id
            )
            state = _merge_state(keys, raw) if keys else None
            result, new_state = mutate(keys, state)
            final_state = (
                new_state
                if new_state is not None
                else (state if period_changed else None)
            )
            if final_state is not None:
                final_state["period_id"] = period_id
                final_state["next_period_id"] = next_credit_period_id()
                _write_state_unlocked(path, final_state)
            return result
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def peek_scrapfly_key() -> ScrapflyKeyLease | None:
    def mutate(keys: list[ScrapflyKey], state: dict[str, Any] | None):
        if not keys or state is None:
            return None, None
        by_label = {item.label: item for item in keys}
        label = state.get("active_label")
        if label not in by_label:
            label = _first_available_label(keys, state)
        if label is None:
            return None, None
        selected = by_label[label]
        info = state["keys"][label]
        credits_used = int(info.get("credits_used") or 0)
        if info.get("exhausted") or credits_used >= selected.credit_limit:
            label = _first_available_label(keys, state)
            if label is None:
                return None, None
            selected = by_label[label]
            info = state["keys"][label]
            credits_used = int(info.get("credits_used") or 0)
        return (
            ScrapflyKeyLease(
                key=selected,
                credits_used=credits_used,
                credits_remaining=max(0, selected.credit_limit - credits_used),
            ),
            None,
        )

    return _with_usage_lock(mutate)


def acquire_scrapfly_key() -> ScrapflyKeyLease:
    def mutate(keys: list[ScrapflyKey], state: dict[str, Any] | None):
        if not keys or state is None:
            return {
                "error": "SCRAPFLY_API_KEY/HS_SCRAPFLY_API_KEY/HS_SCRAPFLY_API_KEYS is not configured"
            }, None

        by_label = {item.label: item for item in keys}
        label = state.get("active_label") or _first_available_label(keys, state)
        if label is not None:
            selected = by_label[label]
            info = state["keys"][label]
            credits_used = int(info.get("credits_used") or 0)
            if credits_used >= selected.credit_limit or info.get("exhausted"):
                info["exhausted"] = True
                info["rotated_out_at"] = info.get("rotated_out_at") or _now_iso()
                label = _first_available_label(keys, state)

        if label is None:
            state["active_label"] = None
            state["updated_at"] = _now_iso()
            return {
                "error": (
                    "All Scrapfly API keys are exhausted "
                    f"(rotation limit reached for {len(keys)} key(s))"
                )
            }, state

        selected = by_label[label]
        info = state["keys"][label]
        credits_used = int(info.get("credits_used") or 0)
        state["active_label"] = selected.label
        info["last_used_at"] = _now_iso()
        state["updated_at"] = _now_iso()
        return {
            "lease": ScrapflyKeyLease(
                key=selected,
                credits_used=credits_used,
                credits_remaining=max(0, selected.credit_limit - credits_used),
            )
        }, state

    result = _with_usage_lock(mutate)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["lease"]


def record_scrapfly_credits(label: str, credits_used: int) -> dict[str, Any]:
    credits = max(0, int(credits_used or 0))

    def mutate(keys: list[ScrapflyKey], state: dict[str, Any] | None):
        if not keys or state is None:
            return {"ok": False, "reason": "no_keys"}, None
        if label not in state["keys"]:
            return {"ok": False, "reason": "unknown_label", "label": label}, state

        selected = {item.label: item for item in keys}[label]
        info = state["keys"][label]
        info["credits_used"] = int(info.get("credits_used") or 0) + credits
        info["last_used_at"] = _now_iso()
        info["credit_limit"] = selected.credit_limit
        info["fingerprint"] = selected.fingerprint

        rotated = False
        if info["credits_used"] >= selected.credit_limit:
            info["exhausted"] = True
            info["rotated_out_at"] = _now_iso()
            state["active_label"] = _first_available_label(keys, state)
            rotated = True
        elif state.get("active_label") is None:
            state["active_label"] = label
        state["updated_at"] = _now_iso()
        return {
            "ok": True,
            "label": label,
            "credits_added": credits,
            "credits_used": info["credits_used"],
            "credit_limit": selected.credit_limit,
            "exhausted": bool(info["exhausted"]),
            "rotated": rotated,
            "active_label": state.get("active_label"),
        }, state

    return _with_usage_lock(mutate)


def mark_scrapfly_key_exhausted(
    label: str, *, reason: str | None = None
) -> dict[str, Any]:
    def mutate(keys: list[ScrapflyKey], state: dict[str, Any] | None):
        if not keys or state is None:
            return {"ok": False, "reason": "no_keys"}, None
        if label not in state["keys"]:
            return {"ok": False, "reason": "unknown_label", "label": label}, state
        info = state["keys"][label]
        info["exhausted"] = True
        info["rotated_out_at"] = _now_iso()
        if reason:
            info["exhaust_reason"] = reason[:300]
        next_label = _first_available_label(keys, state)
        state["active_label"] = next_label
        state["updated_at"] = _now_iso()
        return {
            "ok": True,
            "label": label,
            "active_label": next_label,
            "reason": reason,
        }, state

    return _with_usage_lock(mutate)


def scrapfly_key_usage_snapshot() -> dict[str, Any]:
    keys = parse_scrapfly_api_keys()
    if not keys:
        return {"ok": False, "keys": [], "active_label": None}

    def mutate(parsed: list[ScrapflyKey], state: dict[str, Any] | None):
        assert state is not None
        public_keys = []
        for item in parsed:
            info = state["keys"][item.label]
            public_keys.append(
                {
                    "label": item.label,
                    "fingerprint": item.fingerprint,
                    "credits_used": int(info.get("credits_used") or 0),
                    "credit_limit": item.credit_limit,
                    "credits_remaining": max(
                        0,
                        item.credit_limit - int(info.get("credits_used") or 0),
                    ),
                    "exhausted": bool(info.get("exhausted")),
                    "last_used_at": info.get("last_used_at"),
                    "rotated_out_at": info.get("rotated_out_at"),
                }
            )
        return {
            "ok": True,
            "updated_at": state.get("updated_at"),
            "period_id": state.get("period_id"),
            "next_period_id": state.get("next_period_id") or next_credit_period_id(),
            "period_reset_at": state.get("period_reset_at"),
            "reset_day": scrapfly_key_reset_day(),
            "active_label": state.get("active_label"),
            "keys": public_keys,
        }, state

    return _with_usage_lock(mutate)


def is_scrapfly_credit_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    needles = (
        "insufficient credits",
        "payment required",
        "credit limit",
        "out of credits",
        "no credits",
        "402",
        "quota",
        "remaining-api-credit",
        "max quota",
    )
    return any(item in text for item in needles)


def is_scrapfly_pool_unavailable(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "all scrapfly api keys are exhausted" in text
        or "scrapfly_api_key/hs_scrapfly_api_key/hs_scrapfly_api_keys is not configured"
        in text
    )
