"""Fail-closed bridge from the API parser to the ParsesUnix transport core."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from web_scraper import PAID_ESCALATION_VERDICTS, ContentRules, Verdict
from web_scraper.budget import BudgetLedger
from web_scraper.fetchers import RawResponse, UrllibTransport
from web_scraper.providers.base import (
    ProviderRequest,
    ProviderResponse,
    ProviderStrategy,
)
from web_scraper.providers.breaker import BreakerStore, ProviderBreakers
from web_scraper.providers.multi_escalation import MultiProviderEscalator, PaidAttempt
from web_scraper.providers.multi_router import MultiProviderRouter
from web_scraper.providers.scrape_do import ScrapeDoProvider
from web_scraper.providers.stats import ProviderStatsStore
from web_scraper.triage import classify_response

from .config import (
    parsesunix_allowed_providers,
    parsesunix_max_body_bytes,
    parsesunix_max_concurrency,
    parsesunix_scrape_do_daily_credit_limit,
    parsesunix_scrape_do_max_requests_per_refresh,
    parsesunix_scrape_do_strategies,
    parsesunix_state_dir,
    parsesunix_timeout_seconds,
    scrape_do_token,
    user_agent,
)
from .refresh_context import reserve_parsesunix_paid_request

_LIMITERS_LOCK = threading.Lock()
_LIMITERS: dict[int, threading.BoundedSemaphore] = {}
_PAID_LOCK = threading.Lock()
_RECOVERED_BUDGET_PATHS: set[Path] = set()


@dataclass(frozen=True)
class TransportEvidence:
    """One transport result, explicitly short of publication success."""

    body: str
    http_status: int | None
    final_url: str
    verdict: str
    reason: str
    body_bytes: int
    elapsed_ms: int | None
    truncated: bool
    content_sha256: str
    backend: str = "parsesunix_direct"
    route: str = "direct"
    provider: str | None = None
    attempts: int = 1
    paid_requests: int = 0
    paid_cost_usd: str = "0"
    cost_certainty: str = "exact"

    @property
    def transport_validated(self) -> bool:
        return self.verdict == Verdict.OK.value and not self.truncated

    @property
    def paid_escalation_allowed(self) -> bool:
        return self.verdict in {verdict.value for verdict in PAID_ESCALATION_VERDICTS}

    def telemetry(self) -> dict[str, object]:
        """Return bounded, secret-free evidence without response bodies or URL queries."""

        return {
            "backend": self.backend,
            "route": self.route,
            "provider": self.provider,
            "http_status": self.http_status,
            "final_host": urlsplit(self.final_url).hostname or "",
            "verdict": self.verdict,
            "reason": self.reason[:500],
            "body_bytes": self.body_bytes,
            "elapsed_ms": self.elapsed_ms,
            "truncated": self.truncated,
            "content_sha256": self.content_sha256,
            "attempts": self.attempts,
            "paid_requests": self.paid_requests,
            "paid_cost_usd": self.paid_cost_usd,
            "cost_certainty": self.cost_certainty,
            "transport_validated": self.transport_validated,
            "paid_escalation_allowed": self.paid_escalation_allowed,
            "publication_validated": None,
        }


class ParsesUnixIntegrationError(RuntimeError):
    """The integration could not safely produce transport evidence."""


class ParsesUnixExecutionError(ParsesUnixIntegrationError):
    """Configuration, safety validation, or adapter execution failed."""


class ParsesUnixTransportRejected(ParsesUnixIntegrationError):
    """A response arrived, but deterministic triage rejected it."""

    def __init__(self, evidence: TransportEvidence) -> None:
        self.evidence = evidence
        super().__init__(
            f"ParsesUnix rejected the response: {evidence.verdict}: {evidence.reason}"
        )


def _limiter(limit: int) -> threading.BoundedSemaphore:
    with _LIMITERS_LOCK:
        return _LIMITERS.setdefault(limit, threading.BoundedSemaphore(limit))


def _safe_reason(response: RawResponse, reason: str) -> str:
    if response.transport_error:
        return "direct transport failed before receiving a complete HTTP response"
    return reason[:500]


def _fetch_direct_sync(url: str) -> TransportEvidence:
    max_body_bytes = parsesunix_max_body_bytes()
    transport = UrllibTransport(
        allow_private=False,
        timeout=parsesunix_timeout_seconds(),
        max_body_bytes=max_body_bytes,
        user_agent=user_agent(),
    )
    with _limiter(parsesunix_max_concurrency()):
        response = transport.fetch(url)

    triage = classify_response(
        status=response.status,
        body=response.body,
        headers=response.headers,
        rules=ContentRules(min_body_bytes=200, expected_content_type="html"),
        source="target",
        transport_error="network failure" if response.transport_error else None,
    )
    verdict = triage.verdict.value
    reason = _safe_reason(response, triage.reason)
    if response.truncated:
        verdict = Verdict.PARSE_FAIL.value
        reason = f"response exceeded the configured {max_body_bytes}-byte limit"

    return TransportEvidence(
        body=response.body.decode("utf-8", errors="replace"),
        http_status=response.status,
        final_url=response.final_url,
        verdict=verdict,
        reason=reason,
        body_bytes=len(response.body),
        elapsed_ms=response.elapsed_ms,
        truncated=response.truncated,
        content_sha256=hashlib.sha256(response.body).hexdigest(),
    )


async def fetch_direct(url: str) -> TransportEvidence:
    """Fetch one public URL on the bounded free ParsesUnix worker pool."""

    return await asyncio.to_thread(_fetch_direct_sync, url)


class _ConfiguredScrapeDoProvider:
    """Expose only operator-approved Scrape.do strategies to the core router."""

    name = "scrape.do"

    def __init__(self, strategies: tuple[str, ...]) -> None:
        self._delegate = ScrapeDoProvider(
            token=scrape_do_token(),
            max_body_bytes=parsesunix_max_body_bytes(),
        )
        allowed = frozenset(strategies)
        self._strategies = tuple(
            strategy
            for strategy in self._delegate.strategies()
            if strategy.id in allowed
        )

    def strategies(self) -> tuple[ProviderStrategy, ...]:
        return self._strategies

    def fetch(self, request: ProviderRequest) -> ProviderResponse:
        if request.strategy_id not in {strategy.id for strategy in self._strategies}:
            raise ParsesUnixExecutionError("Scrape.do strategy escaped its allowlist")
        return self._delegate.fetch(
            replace(request, timeout_seconds=parsesunix_timeout_seconds())
        )


def _paid_evidence(
    direct: TransportEvidence,
    attempt: PaidAttempt,
) -> TransportEvidence:
    certainty = attempt.cost.certainty.value.lower()
    paid_cost_usd = (
        f"{attempt.cost.estimated_usd:.6f}"
        if attempt.cost.estimated_usd is not None
        else ""
    )
    response = attempt.response
    if response is None or attempt.triage is None:
        return replace(
            direct,
            reason="paid Scrape.do attempt did not produce a validated target response",
            backend="parsesunix_scrape_do",
            route="direct+paid",
            provider=attempt.provider,
            attempts=2,
            paid_requests=1,
            paid_cost_usd=paid_cost_usd,
            cost_certainty=certainty,
        )

    body = response.body
    verdict = attempt.triage.verdict.value
    reason = attempt.triage.reason[:500]
    if response.truncated:
        verdict = Verdict.PARSE_FAIL.value
        reason = (
            f"response exceeded the configured {parsesunix_max_body_bytes()}-byte limit"
        )
    return TransportEvidence(
        body=body.decode("utf-8", errors="replace"),
        http_status=response.target_status,
        final_url=response.final_url or direct.final_url,
        verdict=verdict,
        reason=reason,
        body_bytes=len(body),
        elapsed_ms=response.latency_ms,
        truncated=response.truncated,
        content_sha256=hashlib.sha256(body).hexdigest(),
        backend="parsesunix_scrape_do",
        route="paid",
        provider=attempt.provider,
        attempts=2,
        paid_requests=1,
        paid_cost_usd=paid_cost_usd,
        cost_certainty=certainty,
    )


def _fetch_paid_sync(url: str, direct: TransportEvidence) -> TransportEvidence:
    if direct.verdict not in {verdict.value for verdict in PAID_ESCALATION_VERDICTS}:
        return direct
    if "scrape.do" not in parsesunix_allowed_providers() or not scrape_do_token():
        return direct

    daily_limit = parsesunix_scrape_do_daily_credit_limit()
    request_limit = parsesunix_scrape_do_max_requests_per_refresh()
    if daily_limit <= Decimal(0) or request_limit <= 0:
        return direct

    with _PAID_LOCK:
        state_dir = parsesunix_state_dir()
        provider = _ConfiguredScrapeDoProvider(parsesunix_scrape_do_strategies())
        stats = ProviderStatsStore(state_dir / "provider_stats.sqlite3")
        breakers = ProviderBreakers(
            store=BreakerStore(state_dir / "provider_breakers.sqlite3")
        )
        budget_path = state_dir / "provider_budget.sqlite3"
        budget = BudgetLedger(
            budget_path,
            daily_credit_limit=daily_limit,
        )
        if budget_path not in _RECOVERED_BUDGET_PATHS:
            budget.recover_after_crash()
            _RECOVERED_BUDGET_PATHS.add(budget_path)
        router = MultiProviderRouter(
            providers=[provider],
            stats=stats,
            breakers=breakers,
            shadow_probe_rate=0.0,
            _rng=lambda: 1.0,
        )
        verdict = Verdict(direct.verdict)
        domain = urlsplit(url).hostname or "unknown"
        decision = router.choose(
            domain=domain,
            url_class="generic_html",
            verdict=verdict,
        )
        if not decision.chosen or not budget.state().allows_paid_work:
            return direct
        if not reserve_parsesunix_paid_request(request_limit):
            return direct
        attempt = MultiProviderEscalator(
            router,
            budget=budget,
            stats=stats,
            breakers=breakers,
        ).attempt(
            url,
            verdict=verdict,
            domain=domain,
            url_class="generic_html",
            rules=ContentRules(min_body_bytes=200, expected_content_type="html"),
        )
    return _paid_evidence(direct, attempt) if attempt.attempted else direct


async def fetch(url: str) -> TransportEvidence:
    """Use direct transport first, then one budgeted Scrape.do attempt if justified."""

    direct = await fetch_direct(url)
    if not direct.paid_escalation_allowed:
        return direct
    return await asyncio.to_thread(_fetch_paid_sync, url, direct)
