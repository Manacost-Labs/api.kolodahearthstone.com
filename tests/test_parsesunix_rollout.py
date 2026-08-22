from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import fetcher
from app.parsesunix_transport import (
    ParsesUnixExecutionError,
    ParsesUnixTransportRejected,
    TransportEvidence,
)
from app.sources import SOURCE_BY_ID, Source

SOURCE = Source(
    id="parsesunix_test_source",
    url="https://example.com/data?scope=test",
    site="example",
    category="test",
)


def _evidence(body: str, *, verdict: str = "OK") -> TransportEvidence:
    encoded = body.encode()
    return TransportEvidence(
        body=body,
        http_status=200,
        final_url=SOURCE.fetch_url,
        verdict=verdict,
        reason="response passed deterministic triage",
        body_bytes=len(encoded),
        elapsed_ms=12,
        truncated=False,
        content_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _legacy_result(body: str) -> SimpleNamespace:
    return SimpleNamespace(
        html=body,
        http_status=200,
        final_url=SOURCE.fetch_url,
        backend="legacy_browser",
        snapshot=None,
    )


def test_legacy_mode_never_calls_parsesunix(monkeypatch: pytest.MonkeyPatch) -> None:
    body = "<html>legacy result</html>"
    parsesunix = AsyncMock(side_effect=AssertionError("ParsesUnix must stay disabled"))
    monkeypatch.setattr(
        fetcher, "parsesunix_mode_for_source", lambda _source_id: "legacy"
    )
    monkeypatch.setattr(fetcher, "fetch_direct_enabled", lambda: False)
    monkeypatch.setattr(fetcher, "fetch_with_parsesunix", parsesunix)
    monkeypatch.setattr(
        fetcher, "fetch_html", AsyncMock(return_value=_legacy_result(body))
    )

    result = asyncio.run(
        fetcher._fetch_generic_html(None, SOURCE, preferred_backend="legacy_browser")
    )

    assert result.body == body
    assert result.parsesunix_mode == "legacy"
    assert result.parsesunix_observation is None
    parsesunix.assert_not_awaited()


def test_streamer_defers_quality_gate_until_after_deck_code_enrichment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamer = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]
    fetch_html = AsyncMock(return_value=_legacy_result("<html>streamer table</html>"))
    monkeypatch.setattr(
        fetcher, "parsesunix_mode_for_source", lambda _source_id: "legacy"
    )
    monkeypatch.setattr(fetcher, "fetch_direct_enabled", lambda: False)
    monkeypatch.setattr(fetcher, "fetch_html", fetch_html)

    asyncio.run(
        fetcher._fetch_generic_html(
            None,
            streamer,
            preferred_backend="legacy_browser",
        )
    )

    assert fetch_html.await_args.kwargs["parse_preview"] is None


def test_active_mode_uses_only_validated_parsesunix_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "<html>validated ParsesUnix result</html>"
    legacy = AsyncMock(side_effect=AssertionError("legacy fetch must not run"))
    monkeypatch.setattr(
        fetcher,
        "parsesunix_mode_for_source",
        lambda _source_id: "parsesunix",
    )
    monkeypatch.setattr(
        fetcher,
        "fetch_with_parsesunix",
        AsyncMock(return_value=_evidence(body)),
    )
    monkeypatch.setattr(fetcher, "fetch_html", legacy)
    monkeypatch.setattr(fetcher, "log_action", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        fetcher._fetch_generic_html(None, SOURCE, preferred_backend="legacy_browser")
    )

    assert result.body == body
    assert result.backend == "parsesunix_direct"
    assert result.parsesunix_observation["transport_validated"] is True
    assert result.parsesunix_observation["publication_validated"] is None
    legacy.assert_not_awaited()


def test_active_mode_rejects_soft_block_without_legacy_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = AsyncMock(side_effect=AssertionError("legacy fetch must not run"))
    monkeypatch.setattr(
        fetcher,
        "parsesunix_mode_for_source",
        lambda _source_id: "parsesunix",
    )
    monkeypatch.setattr(
        fetcher,
        "fetch_with_parsesunix",
        AsyncMock(return_value=_evidence("challenge", verdict="SOFT_BLOCK")),
    )
    monkeypatch.setattr(fetcher, "fetch_html", legacy)
    monkeypatch.setattr(fetcher, "log_action", lambda *_args, **_kwargs: None)

    with pytest.raises(ParsesUnixTransportRejected):
        asyncio.run(
            fetcher._fetch_generic_html(
                None,
                SOURCE,
                preferred_backend="legacy_browser",
            )
        )

    legacy.assert_not_awaited()


def test_invalid_rollout_configuration_fails_before_any_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsesunix = AsyncMock(side_effect=AssertionError("transport must not run"))

    def invalid_mode(_source_id: str) -> str:
        raise ValueError("shadow and active lists overlap")

    monkeypatch.setattr(fetcher, "parsesunix_mode_for_source", invalid_mode)
    monkeypatch.setattr(fetcher, "fetch_with_parsesunix", parsesunix)

    with pytest.raises(ParsesUnixExecutionError, match="Invalid ParsesUnix"):
        asyncio.run(
            fetcher._fetch_generic_html(
                None,
                SOURCE,
                preferred_backend="legacy_browser",
            )
        )

    parsesunix.assert_not_awaited()


def test_active_parsesunix_overrides_firecrawl_primary_without_changing_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fetcher, "firecrawl_primary_source_ids", lambda: {SOURCE.id}
    )

    assert fetcher._firecrawl_primary_enabled(SOURCE.id, "legacy") is True
    assert fetcher._firecrawl_primary_enabled(SOURCE.id, "shadow") is True
    assert fetcher._firecrawl_primary_enabled(SOURCE.id, "parsesunix") is False


def test_firecrawl_primary_records_parsesunix_shadow_without_changing_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = {
        "source_id": SOURCE.id,
        "state": "ok",
        "backend": "scrape_do",
        "http_status": 200,
        "rows_total": 12,
    }
    monkeypatch.setattr(
        fetcher, "parsesunix_mode_for_source", lambda _source_id: "shadow"
    )
    monkeypatch.setattr(
        fetcher, "firecrawl_primary_source_ids", lambda: {SOURCE.id}
    )
    monkeypatch.setattr(
        fetcher,
        "_try_firecrawl_html",
        AsyncMock(return_value=dict(published)),
    )
    monkeypatch.setattr(
        fetcher,
        "fetch_with_parsesunix",
        AsyncMock(return_value=_evidence("<html>shadow candidate</html>")),
    )
    monkeypatch.setattr(
        fetcher, "parse_html", lambda _source, _body: {"rows": [1, 2]}
    )
    monkeypatch.setattr(
        fetcher,
        "validate_candidate_for_publish",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, reason="accepted"),
    )
    monkeypatch.setattr(fetcher, "estimate_metric_count", lambda _source, _parsed: 2)
    monkeypatch.setattr(fetcher, "complete_source_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fetcher, "log_action", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        fetcher._fetch_source_with_active_lifecycle(
            None,
            SOURCE,
            True,
            started=0.0,
            fetched_at="2026-08-20T17:00:00+00:00",
            publication_attempt=None,
            previous={},
            preferred_backend="legacy_browser",
            source_tier="browser_protected",
            trace_id="shadow-test",
        )
    )

    assert {key: result[key] for key in published} == published
    assert result["parsesunix_shadow"]["transport_validated"] is True
    assert result["parsesunix_shadow"]["candidate_validated"] is True
    assert result["parsesunix_shadow"]["candidate_metric_count"] == 2
    assert "candidate_error_type" not in result["parsesunix_shadow"]
    assert result["parsesunix_shadow"]["publication_validated"] is None


def test_parsesunix_never_reenters_the_legacy_paid_chain() -> None:
    blocked = ParsesUnixTransportRejected(_evidence("challenge", verdict="BLOCKED"))
    origin_down = ParsesUnixTransportRejected(
        _evidence("upstream unavailable", verdict="ORIGIN_DOWN")
    )
    integration_failure = ParsesUnixExecutionError("local configuration failed")

    assert fetcher._parsesunix_allows_paid_fallback(blocked) is False
    assert fetcher._parsesunix_allows_paid_fallback(origin_down) is False
    assert fetcher._parsesunix_allows_paid_fallback(integration_failure) is False
    assert (
        fetcher._parsesunix_allows_paid_fallback(RuntimeError("legacy failed")) is True
    )


@pytest.mark.parametrize(
    ("verdict", "reason_code"),
    [
        ("BLOCKED", "access_blocked"),
        ("SOFT_BLOCK", "access_blocked"),
        ("ACCESS_DENIED", "access_blocked"),
        ("RATE_LIMITED", "rate_limited"),
        ("AUTH_REQUIRED", "authentication"),
        ("ORIGIN_DOWN", "transport"),
        ("PARSE_FAIL", "contract"),
    ],
)
def test_parsesunix_rejections_keep_bounded_failure_reason(
    verdict: str,
    reason_code: str,
) -> None:
    status: dict[str, object] = {}

    fetcher._attach_failure_class(
        status,
        ParsesUnixTransportRejected(_evidence("rejected", verdict=verdict)),
    )

    assert status["failure_reason_code"] == reason_code


def test_shadow_mode_compares_candidate_but_returns_legacy_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "<html>same result</html>"
    monkeypatch.setattr(
        fetcher, "parsesunix_mode_for_source", lambda _source_id: "shadow"
    )
    monkeypatch.setattr(fetcher, "fetch_direct_enabled", lambda: False)
    monkeypatch.setattr(
        fetcher,
        "fetch_with_parsesunix",
        AsyncMock(return_value=_evidence(body)),
    )
    monkeypatch.setattr(
        fetcher, "fetch_html", AsyncMock(return_value=_legacy_result(body))
    )
    monkeypatch.setattr(fetcher, "parse_html", lambda _source, _body: {"rows": [1, 2]})
    monkeypatch.setattr(
        fetcher,
        "validate_candidate_for_publish",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, reason="candidate accepted"),
    )
    monkeypatch.setattr(fetcher, "estimate_metric_count", lambda _source, _parsed: 2)
    monkeypatch.setattr(fetcher, "log_action", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        fetcher._fetch_generic_html(None, SOURCE, preferred_backend="legacy_browser")
    )

    assert result.body == body
    assert result.backend == "legacy_browser"
    assert result.parsesunix_mode == "shadow"
    assert result.parsesunix_observation["content_hash_match"] is True
    assert result.parsesunix_observation["candidate_validated"] is True
    assert result.parsesunix_observation["publication_validated"] is None


def test_shadow_failure_cannot_break_legacy_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = "<html>legacy remains authoritative</html>"
    monkeypatch.setattr(
        fetcher, "parsesunix_mode_for_source", lambda _source_id: "shadow"
    )
    monkeypatch.setattr(fetcher, "fetch_direct_enabled", lambda: False)
    monkeypatch.setattr(
        fetcher,
        "fetch_with_parsesunix",
        AsyncMock(side_effect=RuntimeError("shadow failed")),
    )
    monkeypatch.setattr(
        fetcher, "fetch_html", AsyncMock(return_value=_legacy_result(body))
    )
    monkeypatch.setattr(fetcher, "log_action", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        fetcher._fetch_generic_html(None, SOURCE, preferred_backend="legacy_browser")
    )

    assert result.body == body
    assert result.parsesunix_observation["error_type"] == "RuntimeError"
    assert result.parsesunix_observation["transport_validated"] is False


def test_publication_outcome_is_attached_without_mutating_transport_evidence() -> None:
    observation = _evidence("<html>candidate</html>").telemetry()
    payload: dict[str, object] = {}

    fetcher._attach_parsesunix_observation(
        payload,
        mode="parsesunix",
        observation=observation,
        publication_validated=True,
    )

    assert payload["parsesunix_transport"]["publication_validated"] is True
    assert observation["publication_validated"] is None


def test_cached_lkg_keeps_latest_parsesunix_failure_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_observation = _evidence("challenge", verdict="SOFT_BLOCK").telemetry()
    failed_status = {
        "state": "fetch_error",
        "fetched_at": "2026-08-20T10:00:00+00:00",
        "detail": "new candidate rejected",
        "parsesunix_transport": failed_observation,
    }
    cached_dataset = {
        "fetched_at": "2026-08-20T09:00:00+00:00",
        "http_status": 200,
        "final_url": SOURCE.fetch_url,
        "content_length": 100,
        "backend": "legacy_browser",
        "data": {"rows": [1]},
    }
    monkeypatch.setattr(
        "app.parser_control.load_resolved_public_dataset",
        lambda _source_id: cached_dataset,
    )
    monkeypatch.setattr(
        fetcher,
        "validate_existing_publication_for_serving",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, reason="ok", extra={}),
    )
    monkeypatch.setattr(fetcher, "quality_metrics", lambda *_args: {})
    monkeypatch.setattr(fetcher, "save_status", lambda *_args: None)
    monkeypatch.setattr(fetcher, "log_action", lambda *_args, **_kwargs: None)

    preserved = fetcher._preserve_cached_ok_status(SOURCE, failed_status)

    assert preserved is not None
    assert preserved["serving_cached_dataset"] is True
    assert (
        preserved["last_refresh_parsesunix_transport"]["transport_validated"] is False
    )
