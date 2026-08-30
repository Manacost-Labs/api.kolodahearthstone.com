from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.completeness import (
    HSREPLAY_ARENA_EXPECTED_PARAMS,
    build_hsreplay_arena_upstream_freshness,
    build_hsreplay_bg_upstream_freshness,
    build_hsreplay_transport_evidence_unavailable,
)
from app.scrapers.quality import quality_metrics
from app.sources import SOURCE_BY_ID
from app.structured_schema import StructuredSchemaError, validate_structured_schema


NOW = datetime(2026, 8, 14, 2, 20, tzinfo=UTC)


def test_bg_body_as_of_proves_freshness_and_uses_honest_cache_age() -> None:
    payload = {"as_of": (NOW - timedelta(hours=1)).isoformat()}
    evidence = build_hsreplay_bg_upstream_freshness(
        payload,
        response_headers={
            "Date": NOW.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "Age": "7200",
            "ETag": 'W/"safe"',
            "Last-Modified": (NOW - timedelta(hours=1)).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            ),
            "Cache-Control": "private, no-cache",
            "CF-Cache-Status": "BYPASS",
            "Set-Cookie": "must-not-survive",
        },
        now=NOW,
    )

    assert evidence["status"] == "fresh"
    assert evidence["reason"] is None
    assert evidence["age_seconds"] == 7200
    assert evidence["body_as_of"] == payload["as_of"]
    assert evidence["evidence"] == ["body_as_of", "last_modified", "age"]
    assert set(evidence["response_headers"]) == {
        "date",
        "age",
        "etag",
        "last-modified",
        "cache-control",
        "cf-cache-status",
    }


def test_bg_malformed_future_and_stale_body_as_of_fail_closed() -> None:
    malformed = build_hsreplay_bg_upstream_freshness(
        {"as_of": "not-a-date"},
        response_headers={},
        now=NOW,
    )
    future = build_hsreplay_bg_upstream_freshness(
        {"as_of": (NOW + timedelta(hours=1)).isoformat()},
        response_headers={},
        now=NOW,
    )
    stale = build_hsreplay_bg_upstream_freshness(
        {"as_of": (NOW - timedelta(hours=37)).isoformat()},
        response_headers={},
        now=NOW,
    )

    assert (malformed["status"], malformed["reason"]) == (
        "unknown",
        "invalid_body_as_of",
    )
    assert (future["status"], future["reason"]) == (
        "unknown",
        "source_timestamp_in_future",
    )
    assert (stale["status"], stale["reason"]) == (
        "stale",
        "upstream_snapshot_too_old",
    )


def test_arena_requires_exact_filters_meta_period_and_last_modified() -> None:
    payload = {
        "metadata": {"meta_period_id": 16},
        "selected_params": list(HSREPLAY_ARENA_EXPECTED_PARAMS),
    }
    evidence = build_hsreplay_arena_upstream_freshness(
        payload,
        response_headers={
            "Last-Modified": (NOW - timedelta(hours=1)).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            ),
            "Age": "38",
            "ETag": 'W/"representation"',
        },
        now=NOW,
    )

    assert evidence["status"] == "fresh"
    assert evidence["reason"] is None
    assert evidence["age_seconds"] == 3600
    assert evidence["meta_period_id"] == 16
    assert evidence["selected_params"] == list(HSREPLAY_ARENA_EXPECTED_PARAMS)
    assert evidence["filters_match"] is True
    assert evidence["evidence"] == [
        "meta_period_id",
        "selected_params",
        "last_modified",
        "age",
        "etag",
    ]


def test_arena_missing_headers_and_wrong_filters_are_unknown() -> None:
    correct_payload = {
        "metadata": {"meta_period_id": 16},
        "selected_params": list(HSREPLAY_ARENA_EXPECTED_PARAMS),
    }
    missing_header = build_hsreplay_arena_upstream_freshness(
        correct_payload,
        response_headers={},
        now=NOW,
    )
    wrong_filter = build_hsreplay_arena_upstream_freshness(
        {
            "metadata": {"meta_period_id": 16},
            "selected_params": ["ArenaTimestampRangeFilter.LAST_7_DAYS"],
        },
        response_headers={
            "Last-Modified": NOW.strftime("%a, %d %b %Y %H:%M:%S GMT")
        },
        now=NOW,
    )

    assert (missing_header["status"], missing_header["reason"]) == (
        "unknown",
        "missing_last_modified",
    )
    assert (wrong_filter["status"], wrong_filter["reason"]) == (
        "unknown",
        "unexpected_selected_params",
    )


def test_arena_accepts_current_normal_arena_filter_pair() -> None:
    selected_params = [
        "ArenaGameTypeFilter.BGT_NORMAL_ARENA",
        "ArenaTimestampRangeFilter.CURRENT_META_PERIOD",
    ]
    evidence = build_hsreplay_arena_upstream_freshness(
        {"metadata": {"meta_period_id": 17}, "selected_params": selected_params},
        response_headers={
            "Last-Modified": (NOW - timedelta(hours=1)).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            )
        },
        now=NOW,
    )

    assert evidence["status"] == "fresh"
    assert evidence["filters_match"] is True
    assert evidence["selected_params"] == selected_params

    mixed_profile = build_hsreplay_arena_upstream_freshness(
        {
            "metadata": {"meta_period_id": 17},
            "selected_params": [
                "ArenaGameTypeFilter.BGT_NORMAL_ARENA",
                "ArenaTimestampRangeFilter.CURRENT_META_PERIOD_UNDERGROUND",
            ],
        },
        response_headers={
            "Last-Modified": NOW.strftime("%a, %d %b %Y %H:%M:%S GMT")
        },
        now=NOW,
    )
    assert (mixed_profile["status"], mixed_profile["reason"]) == (
        "unknown",
        "unexpected_selected_params",
    )


def test_arena_accepts_exact_card_stats_bounded_window_only() -> None:
    selected_params = ["ArenaTimestampRangeFilter.LAST_4_DAYS"]
    evidence = build_hsreplay_arena_upstream_freshness(
        {"metadata": {"meta_period_id": 17}, "selected_params": selected_params},
        response_headers={
            "Last-Modified": (NOW - timedelta(hours=1)).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            )
        },
        now=NOW,
    )

    assert evidence["status"] == "fresh"
    assert evidence["filters_match"] is True
    assert evidence["selected_params"] == selected_params

    for invalid in (
        selected_params * 2,
        selected_params + ["ArenaGameTypeFilter.BGT_NORMAL_ARENA"],
    ):
        rejected = build_hsreplay_arena_upstream_freshness(
            {"metadata": {"meta_period_id": 17}, "selected_params": invalid},
            response_headers={
                "Last-Modified": NOW.strftime("%a, %d %b %Y %H:%M:%S GMT")
            },
            now=NOW,
        )
        assert (rejected["status"], rejected["reason"]) == (
            "unknown",
            "unexpected_selected_params",
        )


def test_arena_stale_future_malformed_headers_and_invalid_meta_fail_closed() -> None:
    payload = {
        "metadata": {"meta_period_id": 16},
        "selected_params": list(HSREPLAY_ARENA_EXPECTED_PARAMS),
    }

    stale = build_hsreplay_arena_upstream_freshness(
        payload,
        response_headers={
            "Last-Modified": (NOW - timedelta(hours=7)).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            )
        },
        now=NOW,
    )
    future = build_hsreplay_arena_upstream_freshness(
        payload,
        response_headers={
            "Last-Modified": (NOW + timedelta(hours=1)).strftime(
                "%a, %d %b %Y %H:%M:%S GMT"
            )
        },
        now=NOW,
    )
    malformed = build_hsreplay_arena_upstream_freshness(
        payload,
        response_headers={"Last-Modified": "yesterday"},
        now=NOW,
    )
    invalid_age = build_hsreplay_arena_upstream_freshness(
        payload,
        response_headers={
            "Last-Modified": NOW.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "Age": "unknown",
        },
        now=NOW,
    )
    invalid_meta = build_hsreplay_arena_upstream_freshness(
        {**payload, "metadata": {"meta_period_id": True}},
        response_headers={
            "Last-Modified": NOW.strftime("%a, %d %b %Y %H:%M:%S GMT")
        },
        now=NOW,
    )

    assert (stale["status"], stale["reason"]) == (
        "stale",
        "upstream_snapshot_too_old",
    )
    assert (future["status"], future["reason"]) == (
        "unknown",
        "source_timestamp_in_future",
    )
    assert (malformed["status"], malformed["reason"]) == (
        "unknown",
        "invalid_last_modified",
    )
    assert (invalid_age["status"], invalid_age["reason"]) == (
        "unknown",
        "invalid_age_header",
    )
    assert (invalid_meta["status"], invalid_meta["reason"]) == (
        "unknown",
        "invalid_meta_period_id",
    )


def test_oversized_or_non_string_header_values_are_not_saved() -> None:
    evidence = build_hsreplay_arena_upstream_freshness(
        {
            "metadata": {"meta_period_id": 16},
            "selected_params": list(HSREPLAY_ARENA_EXPECTED_PARAMS),
        },
        response_headers={
            "Last-Modified": "x" * 600,
            "ETag": 123,
            "Date": "safe-date",
        },
        now=NOW,
    )

    assert evidence["status"] == "unknown"
    assert evidence["reason"] == "missing_last_modified"
    assert evidence["response_headers"] == {"date": "safe-date"}


def _strict_bg_payload(*, freshness: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "type": "bg_minions",
        "completeness_schema_version": 1,
        "population_completeness": "unverifiable",
        "upstream_freshness": freshness
        or {
            "status": "fresh",
            "reason": None,
            "observed_at": NOW.isoformat(),
            "age_seconds": 60,
            "evidence": ["body_as_of"],
            "response_headers": {},
            "body_as_of": (NOW - timedelta(minutes=1)).isoformat(),
        },
        "minions": [],
    }


def test_strict_hsreplay_schema_requires_honest_population_and_freshness() -> None:
    assert validate_structured_schema(_strict_bg_payload())["ok"] is True

    missing_population = _strict_bg_payload()
    missing_population.pop("population_completeness")
    with pytest.raises(StructuredSchemaError, match="population_completeness"):
        validate_structured_schema(missing_population)

    missing_freshness = _strict_bg_payload()
    missing_freshness.pop("upstream_freshness")
    with pytest.raises(StructuredSchemaError, match="upstream_freshness"):
        validate_structured_schema(missing_freshness)


def test_unknown_freshness_is_valid_but_bounded_headers_fail_closed() -> None:
    unknown = _strict_bg_payload(
        freshness={
            "status": "unknown",
            "reason": "transport_did_not_expose_target_headers",
            "observed_at": NOW.isoformat(),
            "age_seconds": None,
            "evidence": [],
            "response_headers": {},
        }
    )
    assert validate_structured_schema(unknown)["ok"] is True

    invalid = _strict_bg_payload()
    invalid["upstream_freshness"]["response_headers"] = {  # type: ignore[index]
        "set-cookie": "secret"
    }
    with pytest.raises(StructuredSchemaError, match="response_headers"):
        validate_structured_schema(invalid)


def test_quality_telemetry_keeps_freshness_but_drops_raw_response_headers() -> None:
    payload = _strict_bg_payload()
    payload["upstream_freshness"]["response_headers"] = {  # type: ignore[index]
        "etag": 'W/"representation"',
        "last-modified": "Fri, 14 Aug 2026 02:19:00 GMT",
    }

    metrics = quality_metrics(
        SOURCE_BY_ID["hsreplay_battlegrounds_minions"],
        {"structured": payload},
    )

    assert metrics["upstream_freshness_status"] == "fresh"
    assert metrics["population_completeness"] == "unverifiable"
    assert "response_headers" not in metrics["upstream_freshness"]


def test_html_fallback_uses_honest_transport_evidence_unavailable_reason() -> None:
    evidence = build_hsreplay_transport_evidence_unavailable(now=NOW)

    assert evidence == {
        "status": "unknown",
        "reason": "transport_evidence_unavailable",
        "observed_at": NOW.isoformat(),
        "age_seconds": None,
        "evidence": [],
        "response_headers": {},
    }
