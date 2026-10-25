"""Small JSON client shared by the paid social APIs.

Retries transient answers (429, 5xx, transport errors) with ``Retry-After`` or
exponential backoff, caches successful bodies on disk for a TTL so a rerun does
not pay twice, and writes one ``provider_usage`` row per HTTP call. API keys
live only in the request header: neither the cache key, the ledger nor the logs
contain them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn, Self

import httpx

from .. import provider_usage
from ..storage import root_dir

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
DEFAULT_CACHE_TTL_SECONDS = 3 * 3600
_CACHE_RETENTION_SECONDS = 7 * 24 * 3600
_MAX_RETRY_AFTER_SECONDS = 60.0


class SocialApiError(RuntimeError):
    """A provider call failed; ``terminal`` means retrying cannot help."""

    def __init__(
        self, message: str, *, status_code: int | None = None, terminal: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.terminal = terminal


@dataclass
class CallStats:
    """Per-client counters reported in the run usage block."""

    requests: int = 0
    billable: int = 0
    cached: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self, usd_per_request: float) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "billableRequests": self.billable,
            "cacheHits": self.cached,
            "failedRequests": self.failed,
            "costUsd": round(self.billable * usd_per_request, 4),
            "errors": self.errors[:10],
        }


def cache_ttl_seconds() -> float:
    raw = os.environ.get("RADAR_SOCIAL_CACHE_TTL_SECONDS", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_CACHE_TTL_SECONDS
    except ValueError:
        value = DEFAULT_CACHE_TTL_SECONDS
    return max(0.0, value)


class JsonApiClient:
    """GET-only JSON client for one bearer-authenticated provider."""

    provider: str = ""
    base_url: str = ""
    usd_per_request: float = 0.0

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        cache_dir: Path | None = None,
        cache_ttl: float | None = None,
        max_attempts: int = 4,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise SocialApiError(
                f"{self.provider} API key is not configured", terminal=True
            )
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        )
        self._cache_dir = cache_dir
        self._cache_ttl = cache_ttl_seconds() if cache_ttl is None else cache_ttl
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self.stats = CallStats()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- cache -----------------------------------------------------------

    def _cache_root(self) -> Path:
        if self._cache_dir is not None:
            return self._cache_dir
        return root_dir() / "cache" / "balance-radar" / self.provider

    def _cache_path(self, path: str, params: Mapping[str, Any]) -> Path:
        identity = json.dumps(
            [self.provider, path, sorted((str(k), str(v)) for k, v in params.items())],
            ensure_ascii=False,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return self._cache_root() / f"{digest}.json"

    def _cache_read(self, cache_path: Path) -> Any | None:
        if self._cache_ttl <= 0:
            return None
        try:
            stored = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(stored, dict) or "body" not in stored:
            return None
        if time.time() - float(stored.get("stored_at") or 0) > self._cache_ttl:
            return None
        return stored["body"]

    def _cache_write(self, cache_path: Path, body: Any) -> None:
        if self._cache_ttl <= 0:
            return
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            pending = cache_path.with_suffix(".tmp")
            pending.write_text(
                json.dumps(
                    {"stored_at": time.time(), "body": body}, ensure_ascii=False
                ),
                encoding="utf-8",
            )
            os.replace(pending, cache_path)
            cutoff = time.time() - _CACHE_RETENTION_SECONDS
            for old in cache_path.parent.glob("*.json"):
                if old.stat().st_mtime < cutoff:
                    old.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "%s cache write skipped: %s", self.provider, type(exc).__name__
            )

    # -- requests --------------------------------------------------------

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(_MAX_RETRY_AFTER_SECONDS, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return min(_MAX_RETRY_AFTER_SECONDS, 2.0**attempt + random.uniform(0, 0.5))

    def get(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        billable: bool = True,
        use_cache: bool = True,
    ) -> Any:
        query = {
            key: value for key, value in (params or {}).items() if value is not None
        }
        cache_path = self._cache_path(path, query)
        if use_cache:
            cached = self._cache_read(cache_path)
            if cached is not None:
                self.stats.cached += 1
                return cached
        url = f"{self.base_url}{path}"
        last_error = "no attempt"
        for attempt in range(self._max_attempts):
            started = time.monotonic()
            self.stats.requests += 1
            try:
                response = self._client.get(path, params=query)
            except httpx.TimeoutException:
                last_error = "timeout"
                self._record(url, started, None, "failed", "timeout")
            except httpx.TransportError:
                last_error = "transport"
                self._record(url, started, None, "failed", "transport")
            else:
                status = response.status_code
                if 200 <= status <= 299:
                    content = response.content
                    if len(content) > MAX_RESPONSE_BYTES:
                        self._record(
                            url, started, status, "failed", "oversize", content
                        )
                        self._fail(
                            f"{self.provider} response exceeded size limit", status
                        )
                    try:
                        body = response.json()
                    except ValueError:
                        # The provider delivered (and billed) an unusable body.
                        self._record(
                            url,
                            started,
                            status,
                            "failed",
                            "invalid_json",
                            content,
                            cost=1 if billable else 0,
                        )
                        self.stats.billable += int(billable)
                        self._fail(f"{self.provider} returned invalid JSON", status)
                    self._record(
                        url,
                        started,
                        status,
                        "success",
                        None,
                        content,
                        cost=1 if billable else 0,
                    )
                    if billable:
                        self.stats.billable += 1
                    self._cache_write(cache_path, body)
                    return body
                self._record(
                    url, started, status, "failed", f"http_{status}", response.content
                )
                last_error = f"HTTP {status}"
                if status not in RETRYABLE_STATUSES:
                    terminal_reason = {
                        401: "API key was rejected",
                        402: "credit balance is exhausted",
                        403: "API key or target was refused",
                    }.get(status, f"returned HTTP {status}")
                    self._fail(
                        f"{self.provider} {terminal_reason}",
                        status,
                        terminal=status != 404,
                    )
                if attempt + 1 < self._max_attempts:
                    self._sleep(
                        self._backoff(attempt, response.headers.get("retry-after"))
                    )
                continue
            if attempt + 1 < self._max_attempts:
                self._sleep(self._backoff(attempt, None))
        self._fail(f"{self.provider} retries exhausted ({last_error})", None)

    def _fail(
        self, message: str, status: int | None, *, terminal: bool = False
    ) -> NoReturn:
        self.stats.failed += 1
        self.stats.errors.append(message)
        raise SocialApiError(message, status_code=status, terminal=terminal)

    def _record(
        self,
        url: str,
        started: float,
        status: int | None,
        outcome: str,
        reason: str | None,
        content: bytes | None = None,
        *,
        cost: int = 0,
    ) -> None:
        if outcome == "failed":
            logger.info("%s request failed: %s", self.provider, reason)
        provider_usage.record_call(
            self.provider,
            url=url,
            started=started,
            status=status,
            outcome=outcome,
            reason=reason,
            content=len(content) if content is not None else 0,
            cost=cost,
            source_id="balance_radar",
        )
