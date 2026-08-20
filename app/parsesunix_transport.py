"""Fail-closed bridge from the API parser to the ParsesUnix transport core."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from web_scraper import (
    PAID_ESCALATION_VERDICTS,
    ResponseContract,
    ValidatedResponse,
    Verdict,
    fetch_validated,
    validate_response,
)
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
    content_kind: str | None = None
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
            "content_kind": self.content_kind,
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


def _evidence_from_validated(
    validated: ValidatedResponse,
    *,
    backend: str,
    route: str,
    provider: str | None = None,
    attempts: int = 1,
    paid_requests: int = 0,
    paid_cost_usd: str = "0",
    cost_certainty: str = "exact",
) -> TransportEvidence:
    response = validated.response
    triage = validated.triage
    return TransportEvidence(
        body=response.body.decode("utf-8", errors="replace"),
        http_status=response.status,
        final_url=response.final_url,
        verdict=triage.verdict.value,
        reason=_safe_reason(response, triage.reason),
        body_bytes=len(response.body),
        elapsed_ms=response.elapsed_ms,
        truncated=response.truncated,
        content_sha256=validated.content_sha256,
        content_kind=validated.content_kind.value,
        backend=backend,
        route=route,
        provider=provider,
        attempts=attempts,
        paid_requests=paid_requests,
        paid_cost_usd=paid_cost_usd,
        cost_certainty=cost_certainty,
    )


def validate_acquired_response(
    url: str,
    body: str | bytes,
    contract: ResponseContract,
    *,
    status: int | None = 200,
    headers: Mapping[str, str] | None = None,
    final_url: str | None = None,
    elapsed_ms: int | None = None,
    truncated: bool = False,
    transport_error: str | None = None,
    backend: str = "existing_transport",
) -> TransportEvidence:
    """Apply the same core contract to a response acquired by an existing client."""

    encoded = body if isinstance(body, bytes) else body.encode("utf-8")
    validated = validate_response(
        RawResponse(
            requested_url=url,
            final_url=final_url or url,
            status=status,
            headers=dict(headers or {}),
            body=encoded,
            elapsed_ms=elapsed_ms,
            truncated=truncated,
            transport_error=transport_error,
        ),
        contract,
    )
    return _evidence_from_validated(
        validated,
        backend=backend,
        route="existing",
    )


def _fetch_direct_sync(
    url: str,
    contract: ResponseContract,
    headers: Mapping[str, str] | None,
) -> TransportEvidence:
    max_body_bytes = parsesunix_max_body_bytes()
    transport = UrllibTransport(
        allow_private=False,
        timeout=parsesunix_timeout_seconds(),
        max_body_bytes=max_body_bytes,
        user_agent=user_agent(),
    )
    with _limiter(parsesunix_max_concurrency()):
        validated = fetch_validated(
            transport,
            url,
            contract,
            headers=headers,
        )

    return _evidence_from_validated(
        validated,
        backend="parsesunix_direct",
        route="direct",
    )


async def fetch_direct(
    url: str,
    contract: ResponseContract,
    *,
    headers: Mapping[str, str] | None = None,
) -> TransportEvidence:
    """Fetch one public URL on the bounded free ParsesUnix worker pool."""

    return await asyncio.to_thread(_fetch_direct_sync, url, contract, headers)


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
    contract: ResponseContract,
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

    validated = validate_response(
        RawResponse(
            requested_url=direct.final_url,
            final_url=response.final_url or direct.final_url,
            status=response.target_status,
            headers=response.headers,
            body=response.body,
            elapsed_ms=response.latency_ms,
            truncated=response.truncated,
        ),
        contract,
    )
    return _evidence_from_validated(
        validated,
        backend="parsesunix_scrape_do",
        route="paid",
        provider=attempt.provider,
        attempts=2,
        paid_requests=1,
        paid_cost_usd=paid_cost_usd,
        cost_certainty=certainty,
    )


def _fetch_paid_sync(
    url: str,
    direct: TransportEvidence,
    contract: ResponseContract,
) -> TransportEvidence:
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
        url_class = f"embedded_{contract.expected_kind.value.lower()}"
        decision = router.choose(
            domain=domain,
            url_class=url_class,
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
            url_class=url_class,
            rules=contract.content_rules(),
        )
    return _paid_evidence(direct, attempt, contract) if attempt.attempted else direct


async def fetch(
    url: str,
    contract: ResponseContract,
    *,
    headers: Mapping[str, str] | None = None,
) -> TransportEvidence:
    """Use direct transport first, then one budgeted Scrape.do attempt if justified."""

    direct = await fetch_direct(url, contract, headers=headers)
    if not direct.paid_escalation_allowed:
        return direct
    return await asyncio.to_thread(_fetch_paid_sync, url, direct, contract)
