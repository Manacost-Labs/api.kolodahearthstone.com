from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from ..proxy_errors import ProxyPaymentRequiredError, proxy_tunnel_error

logger = logging.getLogger(__name__)

# FIX: markers that mean cookies/session are stale — trigger clean retry
SESSION_BLOCKED_STATUSES = frozenset({401, 403, 429})
SESSION_BLOCKED_MARKERS = (
    "cf_clearance",
    "access denied",
    "attention required",
    "just a moment",
    "challenges.cloudflare",
    "cf-chl",
    "enable javascript",
    "request blocked",
    "datadome",
    "please verify you are a human",
)

# FIX: exponential backoff schedule (seconds) for HTTP retries
DEFAULT_BACKOFF_SECONDS = (5.0, 15.0, 45.0)


def is_session_blocked(status_code: int | None, body: str | None) -> bool:
    """True when the response indicates WAF block or dead session."""
    if status_code in SESSION_BLOCKED_STATUSES:
        return True
    if not body:
        return False
    lowered = body[:8000].lower()
    return any(marker in lowered for marker in SESSION_BLOCKED_MARKERS)


def response_snippet(body: str | None, *, limit: int = 200) -> str:
    if not body:
        return ""
    compact = re.sub(r"\s+", " ", body).strip()
    return compact[:limit]


def jitter_sleep_seconds(min_s: float, max_s: float) -> float:
    return random.uniform(min_s, max_s)


async def async_jitter_sleep(min_s: float, max_s: float) -> None:
    await asyncio.sleep(jitter_sleep_seconds(min_s, max_s))


def backoff_delay_seconds(attempt: int, *, schedule: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS) -> float:
    """attempt is 1-based; returns delay before next retry."""
    idx = min(attempt - 1, len(schedule) - 1)
    base = schedule[idx]
    return base * random.uniform(0.85, 1.15)


def site_root_referer(url: str) -> str:
    """FIX: Referer must match site root for WAF consistency."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}/"


def domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def build_fetch_headers(
    url: str,
    *,
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    FIX: Consistent browser-like headers; do not override curl_cffi impersonate UA.
    """
    root = site_root_referer(url)
    headers = {
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": root,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }
    if extra:
        headers.update(extra)
    return headers


def log_http_error(
    *,
    url: str,
    status_code: int | None = None,
    proxy_ip: str | None = None,
    body: str | None = None,
    error: str | None = None,
    source_id: str | None = None,
    backend: str | None = None,
) -> None:
    """FIX: structured error line for ops debugging."""
    snippet = response_snippet(body)
    ip_label = proxy_ip or "unknown"
    msg = (
        f"[ERROR] URL: {url} | Status: {status_code} | IP: {ip_label} | "
        f"Response Snippet: {snippet}"
    )
    if error:
        msg += f" | Detail: {error[:300]}"
    if source_id:
        msg = f"[{source_id}] {msg}"
    if backend:
        msg = f"[{backend}] {msg}"
    logger.error(msg)


async def resolve_proxy_egress_ip(proxy_url: str | None, *, check_url: str) -> str | None:
    if not proxy_url:
        return None
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url,
            timeout=8.0,
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            response = await client.get(check_url)
            if response.status_code == 407:
                return "407-proxy-auth"
            response.raise_for_status()
            return response.text.strip()[:64]
    except Exception as exc:
        return f"unresolved({type(exc).__name__})"


async def resilient_http_get(
    url: str,
    *,
    source_id: str | None = None,
    client_kwargs: dict[str, Any],
    headers: dict[str, str] | None = None,
    max_attempts: int = 3,
    backoff: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    proxy_url: str | None = None,
    proxy_check_url: str = "https://api.ipify.org",
    on_session_burn: Callable[[], None] | None = None,
    validate_body: Callable[[int, str], bool] | None = None,
) -> tuple[str, int, str]:
    """
    GET with exponential backoff, session burn on 403/401/429/WAF HTML, detailed logging.
    Returns (text, status_code, final_url). Raises last error if all attempts fail.
    """
    req_headers = headers or build_fetch_headers(url)
    last_exc: Exception | None = None
    last_status: int | None = None
    last_body = ""

    for attempt in range(1, max_attempts + 1):
        current_proxy_url = client_kwargs.get("proxy") or proxy_url
        proxy_used = bool(current_proxy_url)
        try:
            async with httpx.AsyncClient(headers=req_headers, **client_kwargs) as client:
                response = await client.get(url)
                last_status = response.status_code
                last_body = response.text
                final_url = str(response.url)

                blocked = is_session_blocked(response.status_code, last_body)
                quality_bad = validate_body is not None and not validate_body(
                    response.status_code, last_body
                )

                if blocked or quality_bad:
                    current_proxy_url = client_kwargs.get("proxy") or proxy_url
                    ip = await resolve_proxy_egress_ip(current_proxy_url, check_url=proxy_check_url)
                    log_http_error(
                        url=url,
                        status_code=response.status_code,
                        proxy_ip=ip,
                        body=last_body,
                        error="session_blocked_or_invalid_body",
                        source_id=source_id,
                    )
                    if on_session_burn:
                        on_session_burn()
                    if attempt < max_attempts:
                        await async_jitter_sleep(*_backoff_range(attempt, backoff))
                        continue
                    raise RuntimeError(
                        f"Session blocked after {max_attempts} attempts "
                        f"(status={response.status_code})"
                    )

                if response.status_code >= 400:
                    response.raise_for_status()

                return last_body, response.status_code, final_url

        except ProxyPaymentRequiredError:
            raise
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            last_status = exc.response.status_code
            last_body = exc.response.text
            if is_session_blocked(last_status, last_body) and on_session_burn:
                on_session_burn()
        except Exception as exc:
            typed_proxy_error = proxy_tunnel_error(exc, proxy_used=proxy_used)
            if typed_proxy_error is not None:
                raise typed_proxy_error from exc
            last_exc = exc

        current_proxy_url = client_kwargs.get("proxy") or proxy_url
        ip = await resolve_proxy_egress_ip(current_proxy_url, check_url=proxy_check_url)
        log_http_error(
            url=url,
            status_code=last_status,
            proxy_ip=ip,
            body=last_body,
            error=str(last_exc) if last_exc else "request_failed",
            source_id=source_id,
        )
        if attempt < max_attempts:
            await async_jitter_sleep(*_backoff_range(attempt, backoff))
        else:
            break

    assert last_exc is not None
    raise last_exc


def _backoff_range(attempt: int, schedule: tuple[float, ...]) -> tuple[float, float]:
    delay = backoff_delay_seconds(attempt, schedule=schedule)
    return delay * 0.9, delay * 1.1


async def run_with_exponential_backoff(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    max_attempts: int = 3,
    backoff: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> Any:
    """Generic async retry wrapper for non-HTTP coroutines."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            if on_retry:
                on_retry(attempt, exc)
            if attempt >= max_attempts:
                break
            await async_jitter_sleep(*_backoff_range(attempt, backoff))
    assert last_exc is not None
    raise last_exc
