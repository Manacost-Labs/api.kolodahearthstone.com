from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

COMPLETENESS_SCHEMA_VERSION = 1
HSREPLAY_BG_MAX_UPSTREAM_AGE_SECONDS = 36 * 60 * 60
HSREPLAY_ARENA_MAX_UPSTREAM_AGE_SECONDS = 6 * 60 * 60
HSREPLAY_MAX_FUTURE_SKEW_SECONDS = 5 * 60
HSREPLAY_ARENA_EXPECTED_PARAMS = (
    "ArenaGameTypeFilter.BGT_UNDERGROUND_ARENA",
    "ArenaTimestampRangeFilter.LAST_4_DAYS",
)
_SAFE_HSREPLAY_TARGET_HEADERS = frozenset(
    {
        "date",
        "age",
        "etag",
        "last-modified",
        "cache-control",
        "cf-cache-status",
    }
)
_MAX_EVIDENCE_STRING_LENGTH = 512

# The full HSReplay Arena card-packages endpoint has one global bucket and one
# bucket for each playable class.  Empty lists are still observed buckets; a
# missing key means that the response itself is incomplete.
ARENA_LEGENDARY_EXPECTED_BUCKETS = (
    "ALL",
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


def _safe_hsreplay_target_headers(
    headers: Mapping[str, object] | None,
) -> dict[str, str]:
    safe: dict[str, str] = {}
    for raw_name, raw_value in (headers or {}).items():
        name = str(raw_name).strip().lower()
        if name not in _SAFE_HSREPLAY_TARGET_HEADERS or not isinstance(raw_value, str):
            continue
        value = raw_value.strip()
        if (
            not value
            or len(value) > _MAX_EVIDENCE_STRING_LENGTH
            or "\r" in value
            or "\n" in value
        ):
            continue
        safe[name] = value
    return safe


def _aware_iso_timestamp(value: object) -> tuple[str, datetime] | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return text, parsed.astimezone(UTC)


def _http_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _cache_age_seconds(headers: Mapping[str, str]) -> tuple[int | None, bool]:
    raw = headers.get("age")
    if raw is None:
        return None, True
    if not raw.isascii() or not raw.isdigit() or len(raw) > 12:
        return None, False
    value = int(raw)
    return (value, True) if value >= 0 else (None, False)


def _freshness_base(
    *,
    now: datetime,
    response_headers: Mapping[str, object] | None,
) -> dict[str, Any]:
    observed_at = now.astimezone(UTC)
    return {
        "status": "unknown",
        "reason": None,
        "observed_at": observed_at.isoformat(),
        "age_seconds": None,
        "evidence": [],
        "response_headers": _safe_hsreplay_target_headers(response_headers),
    }


def build_hsreplay_transport_evidence_unavailable(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a bounded unverified state for an HTML fallback without headers."""

    result = _freshness_base(
        now=(now or datetime.now(UTC)).astimezone(UTC),
        response_headers={},
    )
    result["reason"] = "transport_evidence_unavailable"
    return result


def _bounded_timestamp_age(
    timestamp: datetime,
    *,
    now: datetime,
) -> tuple[int | None, str | None]:
    age = (now.astimezone(UTC) - timestamp).total_seconds()
    if age < -HSREPLAY_MAX_FUTURE_SKEW_SECONDS:
        return None, "source_timestamp_in_future"
    return max(0, int(age)), None


def build_hsreplay_bg_upstream_freshness(
    payload: Mapping[str, object],
    *,
    response_headers: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Prove BG snapshot age from HSReplay's body-level ``as_of`` marker."""

    observed = (now or datetime.now(UTC)).astimezone(UTC)
    result = _freshness_base(now=observed, response_headers=response_headers)
    parsed_as_of = _aware_iso_timestamp(payload.get("as_of"))
    if parsed_as_of is None:
        result["reason"] = "invalid_body_as_of"
        return result
    body_as_of, source_time = parsed_as_of
    result["body_as_of"] = body_as_of
    result["evidence"].append("body_as_of")

    source_age, timestamp_error = _bounded_timestamp_age(source_time, now=observed)
    if timestamp_error:
        result["reason"] = timestamp_error
        return result

    headers = result["response_headers"]
    last_modified_raw = headers.get("last-modified")
    if last_modified_raw is not None:
        last_modified = _http_timestamp(last_modified_raw)
        if last_modified is None:
            result["reason"] = "invalid_last_modified"
            return result
        header_age, timestamp_error = _bounded_timestamp_age(
            last_modified,
            now=observed,
        )
        if timestamp_error:
            result["reason"] = timestamp_error
            return result
        if abs((last_modified - source_time).total_seconds()) > 5 * 60:
            result["reason"] = "body_last_modified_mismatch"
            return result
        result["evidence"].append("last_modified")
        source_age = max(source_age or 0, header_age or 0)

    cache_age, valid_cache_age = _cache_age_seconds(headers)
    if not valid_cache_age:
        result["reason"] = "invalid_age_header"
        return result
    if cache_age is not None:
        result["evidence"].append("age")
        source_age = max(source_age or 0, cache_age)

    result["age_seconds"] = source_age
    if source_age is not None and source_age > HSREPLAY_BG_MAX_UPSTREAM_AGE_SECONDS:
        result.update(status="stale", reason="upstream_snapshot_too_old")
    else:
        result.update(status="fresh", reason=None)
    return result


def build_hsreplay_arena_upstream_freshness(
    payload: Mapping[str, object],
    *,
    response_headers: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Prove Arena response freshness without treating fetch time as data time."""

    observed = (now or datetime.now(UTC)).astimezone(UTC)
    result = _freshness_base(now=observed, response_headers=response_headers)
    metadata = payload.get("metadata")
    meta_period_id = metadata.get("meta_period_id") if isinstance(metadata, Mapping) else None
    if (
        isinstance(meta_period_id, bool)
        or not isinstance(meta_period_id, int)
        or not 0 < meta_period_id <= 1_000_000_000
    ):
        result["reason"] = "invalid_meta_period_id"
        return result
    result["meta_period_id"] = meta_period_id
    result["evidence"].append("meta_period_id")

    selected = payload.get("selected_params")
    selected_values = (
        tuple(selected)
        if isinstance(selected, (list, tuple))
        and all(
            isinstance(value, str) and 0 < len(value) <= 128
            for value in selected
        )
        else ()
    )
    filters_match = (
        len(selected_values) == len(HSREPLAY_ARENA_EXPECTED_PARAMS)
        and frozenset(selected_values) == frozenset(HSREPLAY_ARENA_EXPECTED_PARAMS)
    )
    result["filters_match"] = filters_match
    if not filters_match:
        result["reason"] = "unexpected_selected_params"
        return result
    result["selected_params"] = list(HSREPLAY_ARENA_EXPECTED_PARAMS)
    result["evidence"].append("selected_params")

    headers = result["response_headers"]
    last_modified_raw = headers.get("last-modified")
    if last_modified_raw is None:
        result["reason"] = "missing_last_modified"
        return result
    last_modified = _http_timestamp(last_modified_raw)
    if last_modified is None:
        result["reason"] = "invalid_last_modified"
        return result
    source_age, timestamp_error = _bounded_timestamp_age(last_modified, now=observed)
    if timestamp_error:
        result["reason"] = timestamp_error
        return result
    result["evidence"].append("last_modified")

    cache_age, valid_cache_age = _cache_age_seconds(headers)
    if not valid_cache_age:
        result["reason"] = "invalid_age_header"
        return result
    if cache_age is not None:
        result["evidence"].append("age")
        source_age = max(source_age or 0, cache_age)
    if "etag" in headers:
        result["evidence"].append("etag")

    result["age_seconds"] = source_age
    if source_age is not None and source_age > HSREPLAY_ARENA_MAX_UPSTREAM_AGE_SECONDS:
        result.update(status="stale", reason="upstream_snapshot_too_old")
    else:
        result.update(status="fresh", reason=None)
    return result


def row_retrieval_evidence(
    *,
    raw_rows: int,
    eligible_rows: int,
    normalized_rows: int,
    explained_reasons: Mapping[str, int] | None = None,
    unexplained_reasons: Mapping[str, int] | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    explained = dict(explained_reasons or {})
    unexplained = dict(unexplained_reasons or {})
    explained_drops = sum(explained.values())
    unexplained_drops = sum(unexplained.values())
    if not raw_rows >= eligible_rows >= normalized_rows >= 0:
        raise ValueError("row retrieval counts must satisfy raw >= eligible >= normalized")
    if raw_rows - normalized_rows != explained_drops + unexplained_drops:
        raise ValueError("row retrieval drop reasons must reconcile with row counts")
    evidence: dict[str, Any] = {
        "raw_rows": raw_rows,
        "eligible_rows": eligible_rows,
        "normalized_rows": normalized_rows,
        "explained_drops": explained_drops,
        "unexplained_drops": unexplained_drops,
        "drop_reasons": {
            "explained": explained,
            "unexplained": unexplained,
        },
    }
    if scope:
        evidence["scope"] = scope
    return evidence
