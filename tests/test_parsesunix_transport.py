from __future__ import annotations

import asyncio

from web_scraper import ResponseContract
from web_scraper.fetchers import RawResponse
from web_scraper.providers.base import (
    ProviderCost,
    ProviderError,
    ProviderErrorKind,
    ProviderResponse,
)
from web_scraper.providers.scrape_do import NORMAL

from app import parsesunix_transport
from app.refresh_context import begin_refresh_run, end_refresh_run


class FakeTransport:
    response: RawResponse

    def __init__(self, **_kwargs: object) -> None:
        pass

    def fetch(self, _url: str, *, headers=None) -> RawResponse:
        return self.response


HTML_CONTRACT = ResponseContract.html(
    canaries=("content",),
    min_body_bytes=200,
)
JSON_CONTRACT = ResponseContract.json(
    required_json_paths=("data.0.id",),
)


class FakeScrapeDoProvider:
    calls = 0
    error: ProviderError | None = None
    body = b"<html><body>" + (b"paid content " * 30) + b"</body></html>"

    def __init__(self, **_kwargs: object) -> None:
        pass

    def strategies(self):
        return (NORMAL,)

    def fetch(self, request):
        type(self).calls += 1
        if self.error is not None:
            raise self.error
        return ProviderResponse(
            provider="scrape.do",
            strategy_id=request.strategy_id,
            target_status=200,
            provider_status=200,
            body=self.body,
            headers={"content-type": "text/html; charset=utf-8"},
            final_url=request.url,
            latency_ms=25,
            cost=ProviderCost.parse("1"),
        )


def _configure_paid_canary(monkeypatch, tmp_path, *, requests: int = 1) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HS_SCRAPE_DO_TOKEN", "test-token")
    monkeypatch.setenv("HS_PARSESUNIX_ALLOWED_PROVIDERS", "scrape.do")
    monkeypatch.setenv("HS_PARSESUNIX_SCRAPE_DO_DAILY_CREDIT_LIMIT", "10")
    monkeypatch.setenv(
        "HS_PARSESUNIX_SCRAPE_DO_MAX_REQUESTS_PER_REFRESH",
        str(requests),
    )
    monkeypatch.setenv("HS_PARSESUNIX_SCRAPE_DO_STRATEGIES", "normal")
    monkeypatch.setattr(
        parsesunix_transport,
        "ScrapeDoProvider",
        FakeScrapeDoProvider,
    )
    FakeScrapeDoProvider.calls = 0
    FakeScrapeDoProvider.error = None


def _soft_block_response() -> RawResponse:
    return RawResponse(
        requested_url="https://example.com",
        final_url="https://example.com",
        status=200,
        headers={"Content-Type": "text/html"},
        body=b"<html><body>Verify you are human" + (b" " * 300) + b"</body></html>",
    )


def test_direct_transport_returns_validated_secret_free_evidence(monkeypatch) -> None:
    body = b"<html><body>" + (b"real content " * 30) + b"</body></html>"
    FakeTransport.response = RawResponse(
        requested_url="https://example.com/list?private=value",
        final_url="https://example.com/list?session=secret",
        status=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=body,
        elapsed_ms=42,
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    evidence = asyncio.run(
        parsesunix_transport.fetch_direct(
            "https://example.com/list?private=value",
            HTML_CONTRACT,
        )
    )

    assert evidence.body == body.decode()
    assert evidence.transport_validated is True
    assert evidence.verdict == "OK"
    assert evidence.paid_requests == 0
    assert evidence.paid_fallback_decision == "not_evaluated"
    telemetry = evidence.telemetry()
    assert telemetry["final_host"] == "example.com"
    assert telemetry["publication_validated"] is None
    assert "body" not in telemetry
    assert "secret" not in repr(telemetry)
    assert "private" not in repr(telemetry)


def test_direct_transport_does_not_accept_soft_block(monkeypatch) -> None:
    body = b"<html><body>Verify you are human" + (b" " * 300) + b"</body></html>"
    FakeTransport.response = RawResponse(
        requested_url="https://example.com",
        final_url="https://example.com",
        status=200,
        headers={"Content-Type": "text/html"},
        body=body,
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    evidence = asyncio.run(
        parsesunix_transport.fetch_direct("https://example.com", HTML_CONTRACT)
    )

    assert evidence.transport_validated is False
    assert evidence.verdict == "SOFT_BLOCK"
    assert evidence.paid_escalation_allowed is True


def test_truncated_response_is_never_transport_validated(monkeypatch) -> None:
    FakeTransport.response = RawResponse(
        requested_url="https://example.com",
        final_url="https://example.com",
        status=200,
        headers={"Content-Type": "text/html"},
        body=b"x" * 300,
        truncated=True,
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    evidence = asyncio.run(
        parsesunix_transport.fetch_direct("https://example.com", HTML_CONTRACT)
    )

    assert evidence.transport_validated is False
    assert evidence.verdict == "PARSE_FAIL"
    assert evidence.paid_escalation_allowed is False
    assert evidence.paid_fallback_decision == "not_evaluated"
    assert evidence.failure_reason_code == "contract"
    assert "truncated" in evidence.reason


def test_transport_error_is_sanitized(monkeypatch) -> None:
    FakeTransport.response = RawResponse(
        requested_url="https://example.com?token=secret",
        final_url="https://example.com?token=secret",
        status=None,
        headers={},
        body=b"",
        transport_error="connection failed for https://example.com?token=secret",
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    evidence = asyncio.run(
        parsesunix_transport.fetch_direct(
            "https://example.com?token=secret",
            HTML_CONTRACT,
        )
    )

    assert evidence.transport_validated is False
    assert evidence.verdict == "ORIGIN_DOWN"
    assert evidence.paid_escalation_allowed is False
    assert evidence.failure_reason_code == "transport"
    assert "secret" not in evidence.reason
    assert "secret" not in repr(evidence.telemetry())


def test_budgeted_scrape_do_runs_only_after_deterministic_block(
    monkeypatch,
    tmp_path,
) -> None:
    _configure_paid_canary(monkeypatch, tmp_path)
    FakeTransport.response = _soft_block_response()
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    begin_refresh_run()
    try:
        evidence = asyncio.run(
            parsesunix_transport.fetch("https://example.com", HTML_CONTRACT)
        )
    finally:
        end_refresh_run()

    assert FakeScrapeDoProvider.calls == 1
    assert evidence.transport_validated is True
    assert evidence.backend == "parsesunix_scrape_do"
    assert evidence.route == "paid"
    assert evidence.provider == "scrape.do"
    assert evidence.paid_requests == 1
    assert evidence.paid_cost_usd == "0.000290"
    assert evidence.cost_certainty == "exact"
    assert evidence.paid_fallback_decision == "attempted"
    assert evidence.paid_budget_state == "OK"
    assert evidence.body == FakeScrapeDoProvider.body.decode()


def test_paid_provider_is_not_called_for_origin_outage(monkeypatch, tmp_path) -> None:
    _configure_paid_canary(monkeypatch, tmp_path)
    FakeTransport.response = RawResponse(
        requested_url="https://example.com",
        final_url="https://example.com",
        status=503,
        headers={"Content-Type": "text/html"},
        body=b"<html><body>upstream unavailable</body></html>",
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    begin_refresh_run()
    try:
        evidence = asyncio.run(
            parsesunix_transport.fetch("https://example.com", HTML_CONTRACT)
        )
    finally:
        end_refresh_run()

    assert FakeScrapeDoProvider.calls == 0
    assert evidence.backend == "parsesunix_direct"
    assert evidence.paid_requests == 0
    assert evidence.verdict == "ORIGIN_DOWN"
    assert evidence.paid_fallback_decision == "verdict_ineligible"


def test_paid_provider_requires_nonzero_limits_and_refresh_context(
    monkeypatch,
    tmp_path,
) -> None:
    _configure_paid_canary(monkeypatch, tmp_path)
    FakeTransport.response = _soft_block_response()
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    evidence = asyncio.run(
        parsesunix_transport.fetch("https://example.com", HTML_CONTRACT)
    )
    assert FakeScrapeDoProvider.calls == 0
    assert evidence.backend == "parsesunix_direct"
    assert evidence.paid_fallback_decision == "refresh_limit"

    monkeypatch.setenv("HS_PARSESUNIX_SCRAPE_DO_DAILY_CREDIT_LIMIT", "0")
    begin_refresh_run()
    try:
        evidence = asyncio.run(
            parsesunix_transport.fetch("https://example.com", HTML_CONTRACT)
        )
    finally:
        end_refresh_run()
    assert FakeScrapeDoProvider.calls == 0
    assert evidence.paid_requests == 0
    assert evidence.paid_fallback_decision == "budget_disabled"


def test_paid_request_cap_is_atomic_per_refresh(monkeypatch, tmp_path) -> None:
    _configure_paid_canary(monkeypatch, tmp_path, requests=1)
    FakeTransport.response = _soft_block_response()
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    begin_refresh_run()
    try:
        first = asyncio.run(
            parsesunix_transport.fetch("https://example.com/one", HTML_CONTRACT)
        )
        second = asyncio.run(
            parsesunix_transport.fetch("https://example.com/two", HTML_CONTRACT)
        )
    finally:
        end_refresh_run()

    assert first.paid_requests == 1
    assert second.paid_requests == 0
    assert second.paid_fallback_decision == "refresh_limit"
    assert FakeScrapeDoProvider.calls == 1


def test_unknown_provider_spend_is_never_reported_as_zero(
    monkeypatch,
    tmp_path,
) -> None:
    _configure_paid_canary(monkeypatch, tmp_path)
    FakeTransport.response = _soft_block_response()
    FakeScrapeDoProvider.error = ProviderError(
        kind=ProviderErrorKind.TIMEOUT,
        message="provider timed out",
        provider="scrape.do",
        retryable=True,
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    begin_refresh_run()
    try:
        evidence = asyncio.run(
            parsesunix_transport.fetch("https://example.com", HTML_CONTRACT)
        )
    finally:
        end_refresh_run()

    assert FakeScrapeDoProvider.calls == 1
    assert evidence.transport_validated is False
    assert evidence.paid_requests == 1
    assert evidence.paid_cost_usd == ""
    assert evidence.cost_certainty == "unknown"
    assert evidence.paid_fallback_decision == "attempted"
    assert evidence.paid_budget_state == "UNKNOWN_SPEND"
    assert evidence.telemetry()["cost_certainty"] == "unknown"


def test_unknown_spend_blocks_next_paid_attempt_with_exact_reason(
    monkeypatch,
    tmp_path,
) -> None:
    _configure_paid_canary(monkeypatch, tmp_path, requests=2)
    FakeTransport.response = _soft_block_response()
    FakeScrapeDoProvider.error = ProviderError(
        kind=ProviderErrorKind.TIMEOUT,
        message="provider timed out",
        provider="scrape.do",
        retryable=True,
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    begin_refresh_run()
    try:
        first = asyncio.run(
            parsesunix_transport.fetch("https://example.com/one", HTML_CONTRACT)
        )
        second = asyncio.run(
            parsesunix_transport.fetch("https://example.com/two", HTML_CONTRACT)
        )
    finally:
        end_refresh_run()

    assert first.paid_fallback_decision == "attempted"
    assert second.paid_requests == 0
    assert second.paid_fallback_decision == "budget_unknown"
    assert second.paid_budget_state == "UNKNOWN_SPEND"
    assert FakeScrapeDoProvider.calls == 1


def test_existing_transport_json_is_checked_by_the_same_contract() -> None:
    evidence = parsesunix_transport.validate_acquired_response(
        "https://example.com/api?token=secret",
        '{"data": [{"id": 7}]}',
        JSON_CONTRACT,
        headers={"Content-Type": "application/json"},
        backend="existing_json",
    )

    assert evidence.transport_validated is True
    assert evidence.content_kind == "JSON"
    assert evidence.backend == "existing_json"
    assert "secret" not in repr(evidence.telemetry())


def test_existing_transport_rejects_html_under_json_contract() -> None:
    evidence = parsesunix_transport.validate_acquired_response(
        "https://example.com/api",
        "<html><body>content shell</body></html>" * 20,
        JSON_CONTRACT,
        headers={"Content-Type": "text/html"},
    )

    assert evidence.transport_validated is False
    assert evidence.verdict == "PARSE_FAIL"


def test_rendered_hsguru_page_with_cloudflare_runtime_marker_is_valid() -> None:
    from app.parsesunix_contracts import page_response_contract
    from app.sources import SOURCE_BY_ID

    source = SOURCE_BY_ID["hsguru_matchups_legend"]
    body = (
        "<html><body><main>"
        + "<article>matchup table with validated content</article>" * 600
        + '<script>window.cf_chl_opt={};const widget="cf-chl-widget-container";</script>'
        + "</main></body></html>"
    )

    evidence = parsesunix_transport.validate_acquired_response(
        source.fetch_url,
        body,
        page_response_contract(source),
        headers={"Content-Type": "text/html"},
        backend="flaresolverr",
    )

    assert evidence.transport_validated is True
    assert evidence.verdict == "OK"
