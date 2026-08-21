"""Parser-control adapter for one bounded convergence recovery attempt."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .convergence_store import ConvergenceClaim
from .convergence_worker import RecoveryExecution

_TERMINAL_STATUSES = frozenset({"succeeded", "partial", "failed"})
_ACTIVE_STATUSES = frozenset({"queued", "running"})
_RUN_ID = re.compile(r"^[a-f0-9]{32}$")
_MAX_RESPONSE_BYTES = 1_000_000
_INSECURE_CONTROL_HOSTS = frozenset({"api", "localhost", "127.0.0.1", "::1"})


class ParserControlContractError(RuntimeError):
    """The control plane returned a response outside the recovery contract."""


class PaidUsageUnavailable(ParserControlContractError):
    """A recovery finished without exact bounded paid-provider accounting."""


class ParserControlClient(Protocol):
    async def enqueue_recovery(self, claim: ConvergenceClaim) -> Mapping[str, Any]: ...

    async def get_run(self, run_id: str) -> Mapping[str, Any]: ...


class HttpParserControlClient:
    """Minimal scoped HTTP client for the internal parser control plane."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(str(base_url).strip())
        hostname = parsed.hostname or ""
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "http" and hostname not in _INSECURE_CONTROL_HOSTS)
        ):
            raise ValueError("Parser control base URL is unsafe")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("Parser control base URL is invalid") from exc
        clean_token = str(token).strip()
        if len(clean_token) < 32 or "\r" in clean_token or "\n" in clean_token:
            raise ValueError("Parser control token is invalid")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("HTTP timeout must be between 1 and 120 seconds")
        self._base_url = str(base_url).strip().rstrip("/")
        self._token = clean_token
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            response = await client.request(
                method,
                url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Orchestrator-Key": self._token,
                },
                json=dict(payload) if payload is not None else None,
            )
        if response.is_redirect:
            raise ParserControlContractError("Parser control redirects are forbidden")
        if not 200 <= response.status_code < 300:
            raise ParserControlContractError(
                f"Parser control returned HTTP {response.status_code}"
            )
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise ParserControlContractError("Parser control response is too large")
        try:
            envelope = response.json()
        except ValueError as exc:
            raise ParserControlContractError(
                "Parser control returned invalid JSON"
            ) from exc
        if not isinstance(envelope, Mapping) or not isinstance(
            envelope.get("run"), Mapping
        ):
            raise ParserControlContractError("Parser control envelope is invalid")
        return envelope

    async def enqueue_recovery(self, claim: ConvergenceClaim) -> Mapping[str, Any]:
        envelope = await self._request(
            "POST",
            "admin/orchestrator/parser-runs",
            payload={
                "requestId": (
                    f"convergence:{claim.chain.chain_id}:attempt:{claim.attempt_number}"
                ),
                "sourceIds": list(claim.chain.source_ids),
                "attemptPurpose": "recovery",
                "originOccurrenceId": claim.chain.origin_occurrence_id,
                "recoveryChainId": claim.chain.chain_id,
                "reason": "bounded automatic freshness recovery",
            },
        )
        deduplicated = envelope.get("deduplicated")
        if not isinstance(deduplicated, bool):
            raise ParserControlContractError("Parser enqueue response is invalid")
        return envelope["run"]

    async def get_run(self, run_id: str) -> Mapping[str, Any]:
        if _RUN_ID.fullmatch(run_id) is None:
            raise ValueError("Parser run ID is invalid")
        envelope = await self._request(
            "GET",
            f"admin/orchestrator/parser-runs/{run_id}",
        )
        return envelope["run"]


def _bounded_count(value: object, *, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= maximum else None


def _validate_run(
    claim: ConvergenceClaim,
    raw_run: Mapping[str, Any],
) -> tuple[str, str]:
    run_id = str(raw_run.get("id") or "")
    if _RUN_ID.fullmatch(run_id) is None:
        raise ParserControlContractError("Parser run ID is invalid")
    if (
        raw_run.get("attemptPurpose") != "recovery"
        or raw_run.get("originOccurrenceId") != claim.chain.origin_occurrence_id
        or raw_run.get("recoveryChainId") != claim.chain.chain_id
    ):
        raise ParserControlContractError("Parser run correlation does not match the claim")
    raw_source_ids = raw_run.get("sourceIds")
    if (
        not isinstance(raw_source_ids, list)
        or any(not isinstance(source_id, str) for source_id in raw_source_ids)
        or len(raw_source_ids) != len(set(raw_source_ids))
        or set(raw_source_ids) != set(claim.chain.source_ids)
    ):
        raise ParserControlContractError("Parser run source set does not match the claim")
    status = str(raw_run.get("status") or "")
    if status not in _ACTIVE_STATUSES | _TERMINAL_STATUSES:
        raise ParserControlContractError("Parser run status is invalid")
    return run_id, status


def _terminal_execution(
    claim: ConvergenceClaim,
    run: Mapping[str, Any],
) -> RecoveryExecution:
    raw_results = run.get("results")
    if not isinstance(raw_results, list) or any(
        not isinstance(result, Mapping) for result in raw_results
    ):
        raise ParserControlContractError("Parser run results are invalid")
    results = tuple(dict(result) for result in raw_results)
    source_ids = [str(result.get("sourceId") or "") for result in results]
    if (
        len(source_ids) != len(set(source_ids))
        or set(source_ids) != set(claim.chain.source_ids)
    ):
        raise ParserControlContractError("Terminal parser results are incomplete")

    paid_requests = 0
    paid_cost_microusd = 0
    for result in results:
        requests = _bounded_count(result.get("paidRequests"), maximum=1_000_000)
        cost = _bounded_count(
            result.get("paidCostMicrousd"),
            maximum=1_000_000_000_000,
        )
        if result.get("paidUsageExact") is not True or requests is None or cost is None:
            raise PaidUsageUnavailable(
                "Parser result does not include exact paid usage"
            )
        paid_requests += requests
        paid_cost_microusd += cost

    return RecoveryExecution(
        parser_run_id=str(run["id"]),
        results=results,
        paid_requests=paid_requests,
        paid_cost_microusd=paid_cost_microusd,
    )


async def execute_parser_control_recovery(
    claim: ConvergenceClaim,
    *,
    client: ParserControlClient,
    poll_interval_seconds: float = 2.0,
    timeout_seconds: float = 20 * 60,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> RecoveryExecution:
    """Enqueue one idempotent recovery and wait for its exact terminal result."""

    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must be non-negative")
    if not 1 <= timeout_seconds <= 30 * 60:
        raise ValueError("timeout_seconds must be between 1 and 1800")
    started = monotonic()
    run = await client.enqueue_recovery(claim)
    run_id, status = _validate_run(claim, run)
    while status in _ACTIVE_STATUSES:
        if monotonic() - started >= timeout_seconds:
            raise TimeoutError("Parser recovery did not finish before its deadline")
        await sleep(poll_interval_seconds)
        run = await client.get_run(run_id)
        observed_run_id, status = _validate_run(claim, run)
        if observed_run_id != run_id:
            raise ParserControlContractError("Parser run ID changed while polling")
    return _terminal_execution(claim, run)
