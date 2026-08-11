from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urlparse

from ..config import http_retry_attempts, request_timeout_seconds
from ..proxy_errors import ProxyPaymentRequiredError, proxy_tunnel_error
from ..sources import Source
from .base import FetchResult
from .http_resilience import (
    DEFAULT_BACKOFF_SECONDS,
    backoff_delay_seconds,
    build_fetch_headers,
    is_session_blocked,
    log_http_error,
)
from .proxy import assert_proxy_configured, burn_proxy_session, proxy_url_for_source

logger = logging.getLogger(__name__)


def _fetch_sync(source: Source) -> FetchResult:
    from curl_cffi import requests as curl_requests

    assert_proxy_configured()
    url = source.fetch_url or source.url
    max_attempts = http_retry_attempts()
    last_status = 0
    last_body = ""

    for attempt in range(1, max_attempts + 1):
        proxy_url = proxy_url_for_source(source.id, page_url=url)
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        # FIX: only Referer/Accept-Language — impersonate=chrome131 sets TLS + UA
        headers = build_fetch_headers(
            url,
            accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        try:
            response = curl_requests.get(
                url,
                impersonate="chrome131",
                proxies=proxies,
                timeout=request_timeout_seconds(),
                allow_redirects=True,
                headers=headers,
            )
            last_status = response.status_code
            last_body = response.text or ""

            if is_session_blocked(response.status_code, last_body):
                log_http_error(
                    url=url,
                    status_code=response.status_code,
                    proxy_ip=proxy_session_label(proxy_url),
                    body=last_body,
                    error="session_blocked",
                    source_id=source.id,
                    backend="curl_cffi",
                )
                burn_proxy_session(source.id, page_url=url, reason=f"curl_cffi status={response.status_code}")
                if attempt < max_attempts:
                    time.sleep(backoff_delay_seconds(attempt, schedule=DEFAULT_BACKOFF_SECONDS))
                    continue
                raise RuntimeError(
                    f"curl_cffi blocked after {max_attempts} attempts (status={response.status_code})"
                )

            if response.status_code >= 400:
                response.raise_for_status()

            return FetchResult(
                html=last_body,
                final_url=str(response.url),
                backend="curl_cffi",
                http_status=response.status_code,
            )
        except ProxyPaymentRequiredError:
            raise
        except Exception as exc:
            typed_proxy_error = proxy_tunnel_error(
                exc,
                proxy_used=bool(proxy_url),
            )
            if typed_proxy_error is not None:
                raise typed_proxy_error from exc
            log_http_error(
                url=url,
                status_code=last_status or None,
                proxy_ip=proxy_session_label(proxy_url),
                body=last_body,
                error=str(exc),
                source_id=source.id,
                backend="curl_cffi",
            )
            if attempt >= max_attempts:
                raise
            time.sleep(backoff_delay_seconds(attempt, schedule=DEFAULT_BACKOFF_SECONDS))

    raise RuntimeError("curl_cffi fetch failed")


def proxy_session_label(proxy_url: str | None) -> str:
    if not proxy_url:
        return "direct"
    user = urlparse(proxy_url).username or ""
    match = re.search(r"_session-([^:@]+)", user)
    return match.group(1) if match else "sticky"


async def fetch_via_curl_cffi(source: Source) -> FetchResult:
    return await asyncio.to_thread(_fetch_sync, source)


def curl_cffi_available() -> bool:
    try:
        import curl_cffi  # noqa: F401

        return True
    except ImportError:
        return False
