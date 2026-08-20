"""Fail-closed bridge from the API parser to the ParsesUnix transport core.

This module deliberately exposes only the free direct route. Paid providers are
introduced separately after budget and accounting telemetry can be enforced at
the call site.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from web_scraper import PAID_ESCALATION_VERDICTS, ContentRules, Verdict
from web_scraper.fetchers import RawResponse, UrllibTransport
from web_scraper.triage import classify_response

from .config import (
    parsesunix_max_body_bytes,
    parsesunix_max_concurrency,
    parsesunix_timeout_seconds,
    user_agent,
)

_LIMITERS_LOCK = threading.Lock()
_LIMITERS: dict[int, threading.BoundedSemaphore] = {}


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
