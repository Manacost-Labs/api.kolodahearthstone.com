from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from .config import (
    api_json_attempts_per_channel,
    api_json_retry_delay_seconds,
    flaresolverr_url,
    hsreplay_json_channels,
    hsreplay_markdown_channels,
    hsreplay_scrape_do_max_concurrency,
    hsreplay_scrape_do_max_credits,
    hsreplay_scrape_do_max_requests,
    http_retry_attempts,
    proxy_check_url,
    request_timeout_seconds,
    user_agent,
)
from .hsreplay_auth import hsreplay_cookies_for_fetch
from .proxy_errors import ProxyPaymentRequiredError, proxy_tunnel_error
from .refresh_context import get_cached_hsreplay_json, set_cached_hsreplay_json
from .refresh_log import log_action
from .scrape_do_backend import ScrapeDoRequestError, scrape_url
from .scrapers.http_resilience import (
    DEFAULT_BACKOFF_SECONDS,
    build_fetch_headers,
    is_session_blocked,
    log_http_error,
    resilient_http_get,
)
from .scrapers.proxy import (
    assert_proxy_configured,
    burn_proxy_session,
    httpx_client_kwargs,
    proxy_url_for_source,
)

logger = logging.getLogger(__name__)

JINA_PREFIX = "https://r.jina.ai/"
_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_SCRAPE_DO_STANDARD_CREDIT_RESERVATION = 1
_SCRAPE_DO_MAX_RETRY_DELAY_SECONDS = 30.0


class HsReplayScrapeDoBudgetError(RuntimeError):
    """The refresh-scoped Scrape.do HSReplay JSON budget is exhausted."""


class _HsReplayProxyCircuitOpen(RuntimeError):
    """A proxy-backed channel was skipped after another task opened the circuit."""

    def __init__(self, proxy_error: ProxyPaymentRequiredError) -> None:
        super().__init__("HSReplay residential proxy circuit is open")
        self.proxy_error: ProxyPaymentRequiredError = proxy_error


@dataclass
class _HsReplayTransportState:
    """Mutable transport guards shared only by tasks in one refresh context."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    proxy_circuit_error: ProxyPaymentRequiredError | None = None
    proxy_probe_completed: bool = False
    proxy_probe_task: asyncio.Task[str] | None = None
    scrape_do_requests_reserved: int = 0
    scrape_do_credits_reserved: int = 0
    json_cache_transports: dict[str, str] = field(default_factory=dict)
    json_key_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    source_json_transports: dict[str, set[str]] = field(default_factory=dict)
    scrape_do_semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(
            hsreplay_scrape_do_max_concurrency()
        )
    )


_transport_state: ContextVar[_HsReplayTransportState | None] = ContextVar(
    "hsreplay_transport_state",
    default=None,
)


def _current_transport_state() -> _HsReplayTransportState:
    state = _transport_state.get()
    if state is None:
        state = _HsReplayTransportState()
        _transport_state.set(state)
    return state


def reset_hsreplay_refresh_state() -> None:
    """Start isolated proxy and provider guards for the current refresh task."""

    _transport_state.set(_HsReplayTransportState())


def record_hsreplay_proxy_failure(exc: ProxyPaymentRequiredError) -> None:
    """Open the HSReplay and refresh-wide proxy circuits."""

    state = _current_transport_state()
    with state.lock:
        if state.proxy_circuit_error is None:
            state.proxy_circuit_error = exc

    # Import lazily to keep the transport modules independent at import time.
    # A recovered HSReplay request (for example through Scrape.do) must still
    # stop later sources in the same refresh from retrying a paid proxy that
    # has already rejected CONNECT with 402/407.
    from .scrapers.rotator import record_residential_proxy_failure

    record_residential_proxy_failure(exc)


def hsreplay_proxy_circuit_is_open() -> bool:
    state = _current_transport_state()
    with state.lock:
        return state.proxy_circuit_error is not None


def _current_proxy_circuit_error() -> ProxyPaymentRequiredError | None:
    state = _current_transport_state()
    with state.lock:
        return state.proxy_circuit_error


def _json_transport_label(channel: str, *, proxy_backed: bool) -> str:
    if proxy_backed:
        return "residential_httpx"
    if channel in {"curl_cffi", "direct", "flaresolverr", "jina"}:
        return f"proxyless_{channel}"
    return channel


def _record_json_transport(
    *,
    cache_key: str,
    source_id: str,
    transport_backend: str,
) -> None:
    state = _current_transport_state()
    with state.lock:
        state.json_cache_transports[cache_key] = transport_backend
        state.source_json_transports.setdefault(source_id, set()).add(
            transport_backend
        )


def _json_key_gate(cache_key: str) -> asyncio.Lock:
    """Return the refresh-scoped single-flight gate for one JSON snapshot."""

    state = _current_transport_state()
    with state.lock:
        gate = state.json_key_locks.get(cache_key)
        if gate is None:
            gate = asyncio.Lock()
            state.json_key_locks[cache_key] = gate
        return gate


def consume_hsreplay_json_transport_backend(source_id: str) -> str | None:
    """Return and clear the successful JSON transports for one source run."""

    state = _current_transport_state()
    with state.lock:
        transports = state.source_json_transports.pop(source_id, set())
    if not transports:
        return None
    normalized = sorted(transports)
    if len(normalized) == 1:
        return normalized[0]
    return f"mixed[{','.join(normalized)}]"


def _scrape_do_gate() -> asyncio.Semaphore:
    return _current_transport_state().scrape_do_semaphore


def _reserve_scrape_do_request() -> None:
    state = _current_transport_state()
    with state.lock:
        if state.scrape_do_requests_reserved >= hsreplay_scrape_do_max_requests():
            raise HsReplayScrapeDoBudgetError(
                "HSReplay Scrape.do request budget exhausted for this refresh"
            )
        if (
            state.scrape_do_credits_reserved
            + _SCRAPE_DO_STANDARD_CREDIT_RESERVATION
            > hsreplay_scrape_do_max_credits()
        ):
            raise HsReplayScrapeDoBudgetError(
                "HSReplay Scrape.do credit budget exhausted for this refresh"
            )
        # Reservations are intentionally never refunded. Failed/rejected calls
        # can still be billed and must consume the local safety budget.
        state.scrape_do_requests_reserved += 1
        state.scrape_do_credits_reserved += _SCRAPE_DO_STANDARD_CREDIT_RESERVATION


def _account_scrape_do_actual_cost(actual_cost: int) -> None:
    extra = max(0, int(actual_cost) - _SCRAPE_DO_STANDARD_CREDIT_RESERVATION)
    if not extra:
        return
    state = _current_transport_state()
    with state.lock:
        state.scrape_do_credits_reserved += extra
        if state.scrape_do_credits_reserved > hsreplay_scrape_do_max_credits():
            raise HsReplayScrapeDoBudgetError(
                "HSReplay Scrape.do response exceeded the refresh credit budget"
            )


def jina_url(url: str) -> str:
    return JINA_PREFIX + url


def extract_json_payload(body: str) -> dict[str, Any] | list[Any] | None:
    text = body.strip()
    marker = "Markdown Content:\n"
    if marker in text:
        text = text.split(marker, 1)[1].strip()

    # Django REST Framework returns a syntax-highlighted browsable API when a
    # browser transport cannot set Accept: application/json. The actual JSON
    # remains losslessly available in the longest <pre> response block. Parse
    # that before the raw HTML so an unrelated early JS object cannot win.
    if text.startswith("<") and "<pre" in text.lower():
        soup = BeautifulSoup(text, "html.parser")
        candidates = sorted(
            (pre.get_text("", strip=False) for pre in soup.find_all("pre")),
            key=len,
            reverse=True,
        )
        for candidate in candidates:
            payload = _decode_json_payload(candidate.strip())
            if payload is not None:
                return payload

    return _decode_json_payload(text)


def _decode_json_payload(text: str) -> dict[str, Any] | list[Any] | None:
    object_start = text.find("{")
    array_start = text.find("[")
    starts = [pos for pos in (object_start, array_start) if pos >= 0]
    start = min(starts) if starts else -1
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text, start)
        return value
    except json.JSONDecodeError:
        return None


async def download_text(url: str, source_id: str | None = None) -> str:
    # FIX: resilient GET — sticky session burn, exponential backoff, detailed [ERROR] logs
    headers = build_fetch_headers(
        url,
        accept="application/json,text/plain,*/*",
        extra={"User-Agent": user_agent()},
    )

    def _client_kwargs() -> dict[str, Any]:
        return httpx_client_kwargs(
            source_id, timeout=request_timeout_seconds(), page_url=url
        )

    kwargs = _client_kwargs()
    proxy_url = proxy_url_for_source(source_id, page_url=url)
    proxy_used = bool(kwargs.get("proxy") or proxy_url)
    circuit_error = _current_proxy_circuit_error()
    if proxy_used and circuit_error is not None:
        raise circuit_error
    # CONNECT payment/auth failures must never be multiplied by the inner HTTP
    # retry loop. The channel cascade provides the safer retry boundary.
    attempts = (
        1 if proxy_used else max(api_json_attempts_per_channel(), http_retry_attempts())
    )

    log_action(
        "http.request.begin",
        source_id=source_id,
        url=url,
        attempt=1,
        extra={"via": "proxy" if kwargs.get("proxy") else "direct"},
    )
    started = time.monotonic()

    def _burn() -> None:
        nonlocal proxy_url, proxy_used
        burn_proxy_session(source_id, page_url=url, reason="download_text_blocked")
        kwargs.clear()
        kwargs.update(_client_kwargs())
        proxy_url = proxy_url_for_source(source_id, page_url=url)
        proxy_used = bool(kwargs.get("proxy") or proxy_url)

    try:
        text, status, final_url = await resilient_http_get(
            url,
            source_id=source_id,
            client_kwargs=kwargs,
            headers=headers,
            max_attempts=attempts,
            backoff=DEFAULT_BACKOFF_SECONDS,
            proxy_url=proxy_url,
            proxy_check_url=proxy_check_url(),
            on_session_burn=_burn,
            validate_body=lambda code, body: not is_session_blocked(code, body),
        )
        log_action(
            "http.request.ok",
            source_id=source_id,
            url=str(final_url),
            http_status=status,
            bytes_out=len(text.encode("utf-8", errors="replace")),
            duration_ms=(time.monotonic() - started) * 1000,
            attempt=attempts,
        )
        return text
    except ProxyPaymentRequiredError as exc:
        if proxy_used:
            record_hsreplay_proxy_failure(exc)
            raise
        raise RuntimeError("Origin returned HTTP 407 without a proxy path") from exc
    except Exception as exc:
        typed_proxy_error = proxy_tunnel_error(exc, proxy_used=proxy_used)
        if typed_proxy_error is not None:
            record_hsreplay_proxy_failure(typed_proxy_error)
            raise typed_proxy_error from exc
        log_action(
            "http.request.fail",
            source_id=source_id,
            url=url,
            error_type=type(exc).__name__,
            detail=str(exc)[:1000],
            level="error",
        )
        log_http_error(
            url=url,
            status_code=None,
            proxy_ip=None,
            body=None,
            error=str(exc),
            source_id=source_id,
        )
        raise


def _fetch_text_via_curl_cffi_sync(url: str, source_id: str | None) -> str:
    from curl_cffi import requests as curl_requests

    assert_proxy_configured()
    max_attempts = http_retry_attempts()
    headers = build_fetch_headers(url, accept="application/json,text/plain,*/*")
    # HSReplay premium endpoints (arena card_stats, analytics) reject anonymous
    # requests with 403, so reuse the stored session cookies like FlareSolverr does.
    cookies: dict[str, str] = {}
    if "hsreplay.net" in url:
        cookies = {
            c["name"]: c["value"]
            for c in (hsreplay_cookies_for_fetch() or [])
            if c.get("name") and c.get("value")
        }
        if cookies:
            headers.setdefault("Referer", "https://hsreplay.net/")
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        proxy_url = proxy_url_for_source(source_id, page_url=url)
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        circuit_error = _current_proxy_circuit_error()
        if proxies and circuit_error is not None:
            raise circuit_error
        try:
            response = curl_requests.get(
                url,
                impersonate="chrome131",
                proxies=proxies,
                timeout=request_timeout_seconds(),
                allow_redirects=True,
                headers=headers,
                cookies=cookies or None,
            )
            if response.status_code == 407 and proxies:
                error = ProxyPaymentRequiredError(
                    "Residential proxy rejected the CONNECT request (HTTP 407)",
                    status_code=407,
                )
                record_hsreplay_proxy_failure(error)
                raise error
            if is_session_blocked(response.status_code, response.text):
                burn_proxy_session(
                    source_id, page_url=url, reason="curl_cffi_json_blocked"
                )
                if attempt < max_attempts:
                    time.sleep(5 * attempt)
                    continue
                raise RuntimeError(
                    f"curl_cffi JSON blocked (status={response.status_code})"
                )
            if response.status_code >= 400:
                response.raise_for_status()
            return response.text
        except ProxyPaymentRequiredError as exc:
            if proxies:
                record_hsreplay_proxy_failure(exc)
            raise
        except Exception as exc:
            typed_proxy_error = proxy_tunnel_error(exc, proxy_used=bool(proxies))
            if typed_proxy_error is not None:
                record_hsreplay_proxy_failure(typed_proxy_error)
                raise typed_proxy_error from exc
            last_exc = exc
            log_http_error(
                url=url,
                status_code=getattr(exc, "response", None)
                and getattr(exc.response, "status_code", None),
                proxy_ip=None,
                body=None,
                error=str(exc),
                source_id=source_id,
                backend="curl_cffi",
            )
            if attempt >= max_attempts:
                raise
            time.sleep(5 * attempt)
    assert last_exc is not None
    raise last_exc


async def fetch_text_via_curl_cffi(url: str, *, source_id: str | None = None) -> str:
    try:
        return await asyncio.to_thread(_fetch_text_via_curl_cffi_sync, url, source_id)
    except ProxyPaymentRequiredError as exc:
        # ContextVar assignments made inside ``to_thread`` do not flow back to
        # the awaiting task. Record the typed failure again in the parent
        # context so every later source in this refresh sees the open circuit.
        record_hsreplay_proxy_failure(exc)
        raise


async def fetch_text_via_flaresolverr(url: str, *, source_id: str | None = None) -> str:
    from .scrapers.proxy import proxy_dict_for_flaresolverr

    payload: dict[str, Any] = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": int(request_timeout_seconds() * 1000),
    }
    proxy = proxy_dict_for_flaresolverr(source_id, page_url=url)
    circuit_error = _current_proxy_circuit_error()
    if proxy and circuit_error is not None:
        raise circuit_error
    if proxy:
        payload["proxy"] = proxy
    cookies = hsreplay_cookies_for_fetch()
    if cookies:
        payload["cookies"] = cookies

    timeout = httpx.Timeout(request_timeout_seconds() + 30.0)
    started = time.monotonic()
    log_action(
        "http.request.begin",
        source_id=source_id,
        url=url,
        extra={"via": "flaresolverr"},
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(flaresolverr_url(), json=payload)
        response.raise_for_status()
        body = response.json()

    if body.get("status") != "ok":
        message = body.get("message") or str(body)
        typed_proxy_error = proxy_tunnel_error(
            RuntimeError(str(message)),
            proxy_used=bool(proxy),
        )
        if typed_proxy_error is not None:
            record_hsreplay_proxy_failure(typed_proxy_error)
            raise typed_proxy_error
        raise RuntimeError(f"FlareSolverr error: {message}")

    solution = body.get("solution") or {}
    text = solution.get("response") or ""
    status = int(solution.get("status") or 0)
    if status == 407 and proxy:
        error = ProxyPaymentRequiredError(
            "FlareSolverr residential proxy rejected the request (HTTP 407)",
            status_code=407,
        )
        record_hsreplay_proxy_failure(error)
        raise error
    if status >= 400:
        raise RuntimeError(f"FlareSolverr origin returned HTTP {status}")
    if not text.strip():
        raise RuntimeError("FlareSolverr returned empty response")
    log_action(
        "http.request.ok",
        source_id=source_id,
        url=url,
        http_status=status or 200,
        bytes_out=len(text.encode("utf-8", errors="replace")),
        duration_ms=(time.monotonic() - started) * 1000,
        backend="flaresolverr",
    )
    return text


def _assert_exact_hsreplay_https_url(url: str) -> None:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "Scrape.do JSON target must be an exact HSReplay HTTPS URL"
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "hsreplay.net"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ValueError("Scrape.do JSON target must be an exact HSReplay HTTPS URL")


def _hsreplay_cookie_header() -> str | None:
    pairs: list[str] = []
    for cookie in hsreplay_cookies_for_fetch() or []:
        domain = str(cookie.get("domain") or "hsreplay.net").lstrip(".").lower()
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if domain != "hsreplay.net" or not _COOKIE_NAME.fullmatch(name):
            continue
        if not value or any(char in value for char in ("\r", "\n", ";")):
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs) or None


async def _fetch_text_via_scrape_do(url: str, *, source_id: str) -> str:
    """Fetch exact HSReplay JSON through cheap non-rendered Scrape.do."""

    _assert_exact_hsreplay_https_url(url)
    headers: dict[str, str] = {"Accept": "application/json"}
    cookie_header = _hsreplay_cookie_header()
    if cookie_header:
        # scrape_url prefixes this as Sd-Cookie. The provider receives no
        # unrelated browser/request headers and the value is never logged.
        headers["Cookie"] = cookie_header

    attempts = max(1, min(api_json_attempts_per_channel(), 3))
    retryable_statuses = {403, 408, 425, 429, 500, 502, 503, 504}
    for attempt in range(1, attempts + 1):
        try:
            async with _scrape_do_gate():
                _reserve_scrape_do_request()
                result = await scrape_url(
                    url,
                    render=False,
                    super_proxy=False,
                    headers=headers,
                    forward_headers=False,
                )
            _assert_exact_hsreplay_https_url(result.final_url)
            _account_scrape_do_actual_cost(result.request_cost)
            log_action(
                "provider.scrape_do.hsreplay_json.transport_ok",
                source_id=source_id,
                backend="scrape_do",
                http_status=result.status_code,
                bytes_out=result.content_length,
                attempt=attempt,
                extra={"render": False, "request_cost": result.request_cost},
            )
            return result.html
        except ScrapeDoRequestError as exc:
            retryable = bool(
                exc.retryable or exc.status_code in retryable_statuses
            )
            will_retry = retryable and attempt < attempts
            log_action(
                (
                    "provider.scrape_do.hsreplay_json.retry"
                    if will_retry
                    else "provider.scrape_do.hsreplay_json.fail"
                ),
                source_id=source_id,
                backend="scrape_do",
                level="warn",
                error_type=type(exc).__name__,
                attempt=attempt,
                extra={
                    "provider_status": exc.status_code,
                    "retryable": retryable,
                    "will_retry": will_retry,
                },
            )
            if not will_retry:
                raise
            delay = (
                exc.retry_after_seconds
                if exc.retry_after_seconds is not None
                else api_json_retry_delay_seconds()
            )
            await asyncio.sleep(
                min(
                    _SCRAPE_DO_MAX_RETRY_DELAY_SECONDS,
                    max(0.0, delay),
                )
            )

    raise RuntimeError("Scrape.do HSReplay JSON attempts exhausted")


def _channel_uses_residential_proxy(
    label: str,
    fetch_url: str,
    source_id: str,
) -> bool:
    if label == "scrape_do":
        return False
    if label == "flaresolverr":
        from .scrapers.proxy import proxy_dict_for_flaresolverr

        return bool(proxy_dict_for_flaresolverr(source_id, page_url=fetch_url))
    return bool(proxy_url_for_source(source_id, page_url=fetch_url))


def _channel_urls_for_labels(api_url: str, labels: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for label in labels:
        if label == "direct":
            out.append(("direct", api_url))
        elif label == "jina":
            out.append(("jina", jina_url(api_url)))
        elif label == "flaresolverr":
            out.append(("flaresolverr", api_url))
        elif label == "curl_cffi":
            out.append(("curl_cffi", api_url))
        elif label == "scrape_do":
            out.append(("scrape_do", api_url))
    return out


def _channel_urls(
    api_url: str, *, source_id: str | None = None
) -> list[tuple[str, str]]:
    from .source_contracts import preferred_channels_for_source

    labels: list[str] = []
    for label in (
        *preferred_channels_for_source(source_id),
        *hsreplay_json_channels(),
    ):
        normalized = str(label).strip().lower()
        if normalized and normalized not in labels:
            labels.append(normalized)
    out = _channel_urls_for_labels(api_url, labels)
    if not out:
        out = _channel_urls_for_labels(api_url, ["flaresolverr", "curl_cffi"])
    return out


def _markdown_channel_urls(page_url: str) -> list[tuple[str, str]]:
    out = _channel_urls_for_labels(page_url, hsreplay_markdown_channels())
    if not out:
        out = _channel_urls_for_labels(page_url, ["flaresolverr", "curl_cffi"])
    return out


async def _fetch_body_for_channel(
    label: str,
    fetch_url: str,
    *,
    source_id: str,
) -> str:
    if label == "flaresolverr":
        return await fetch_text_via_flaresolverr(fetch_url, source_id=source_id)
    if label == "curl_cffi":
        try:
            return await fetch_text_via_curl_cffi(fetch_url, source_id=source_id)
        except ImportError as exc:
            raise RuntimeError("curl_cffi not installed") from exc
    if label == "scrape_do":
        return await _fetch_text_via_scrape_do(fetch_url, source_id=source_id)
    return await download_text(fetch_url, source_id=source_id)


async def _fetch_body_with_proxy_probe_gate(
    label: str,
    fetch_url: str,
    *,
    source_id: str,
    proxy_backed: bool,
) -> str:
    """Single-flight the first residential request in one refresh.

    Parallel HSReplay slices use different cache keys, so their per-key gates
    cannot prevent several simultaneous CONNECT failures. All callers share a
    state-owned initial probe task. A typed 402/407 opens the circuit before
    its waiters resume, while any other terminal result restores parallelism.
    """

    if not proxy_backed:
        return await _fetch_body_for_channel(label, fetch_url, source_id=source_id)

    state = _current_transport_state()
    is_probe_owner = False
    with state.lock:
        circuit_error = state.proxy_circuit_error
        probe_completed = state.proxy_probe_completed
        probe_task = state.proxy_probe_task
        if (
            circuit_error is None
            and not probe_completed
            and probe_task is None
        ):
            probe_task = asyncio.create_task(
                _run_initial_proxy_probe(
                    label,
                    fetch_url,
                    source_id=source_id,
                    state=state,
                )
            )
            probe_task.add_done_callback(_consume_proxy_probe_exception)
            state.proxy_probe_task = probe_task
            is_probe_owner = True

    if circuit_error is not None:
        raise _HsReplayProxyCircuitOpen(circuit_error)
    if probe_completed:
        return await _fetch_body_for_channel(label, fetch_url, source_id=source_id)

    assert probe_task is not None
    # Cancelling this individual caller only cancels the shield Future. The
    # state-owned physical CONNECT continues and publishes its terminal state.
    probe_result = (
        await asyncio.gather(
            asyncio.shield(probe_task),
            return_exceptions=True,
        )
    )[0]
    if isinstance(probe_result, ProxyPaymentRequiredError):
        if is_probe_owner:
            raise probe_result
        raise _HsReplayProxyCircuitOpen(probe_result) from probe_result
    if isinstance(probe_result, BaseException):
        if is_probe_owner:
            raise probe_result
        # An inconclusive transport/origin error completes only the initial
        # gate. Waiters resume normal parallel channel attempts.
        return await _fetch_body_for_channel(label, fetch_url, source_id=source_id)

    if is_probe_owner:
        return probe_result
    return await _fetch_body_for_channel(label, fetch_url, source_id=source_id)


async def _run_initial_proxy_probe(
    label: str,
    fetch_url: str,
    *,
    source_id: str,
    state: _HsReplayTransportState,
) -> str:
    """Run and publish the refresh-owned initial residential probe."""

    try:
        return await _fetch_body_for_channel(
            label,
            fetch_url,
            source_id=source_id,
        )
    except ProxyPaymentRequiredError as exc:
        # Publish the typed circuit before waking any shared-task waiters.
        record_hsreplay_proxy_failure(exc)
        try:
            log_action(
                "routing.proxy_probe.fail",
                source_id=source_id,
                backend=_json_transport_label(label, proxy_backed=True),
                level="warn",
                error_type=type(exc).__name__,
                extra={"channel": label, "proxy_status": exc.status_code},
            )
        except OSError as log_exc:
            logger.debug(
                "Could not persist HSReplay proxy probe telemetry: %s",
                log_exc,
            )
        raise
    finally:
        with state.lock:
            state.proxy_probe_completed = True
            state.proxy_probe_task = None


def _consume_proxy_probe_exception(task: asyncio.Task[str]) -> None:
    """Retrieve abandoned probe exceptions without changing task semantics."""

    if not task.cancelled():
        _ = task.exception()


def _payload_to_dict(payload: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(payload, list):
        return {"data": payload}
    return payload


async def _fetch_hsreplay_json_serialized(
    api_url: str,
    *,
    source_id: str,
    key: str,
) -> dict[str, Any]:
    cached = get_cached_hsreplay_json(key)
    if cached is not None:
        state = _current_transport_state()
        with state.lock:
            cached_transport = state.json_cache_transports.get(key)
            if cached_transport is not None:
                state.source_json_transports.setdefault(source_id, set()).add(
                    cached_transport
                )
        log_action(
            "api.route.ok",
            source_id=source_id,
            backend="hsreplay_cache",
            extra={"channel": "cache", "api_url": api_url},
        )
        return cached

    errors: list[str] = []
    channels = _channel_urls(api_url, source_id=source_id)
    first_proxy_error: ProxyPaymentRequiredError | None = None
    independent_channel_attempted = False

    for label, fetch_url in channels:
        proxy_backed = _channel_uses_residential_proxy(
            label,
            fetch_url,
            source_id,
        )
        circuit_error = _current_proxy_circuit_error()
        if proxy_backed and circuit_error is not None:
            first_proxy_error = first_proxy_error or circuit_error
            errors.append(f"{label}: skipped after proxy CONNECT failure")
            log_action(
                "routing.channel.skip",
                source_id=source_id,
                level="warn",
                detail="proxy-backed HSReplay JSON channel skipped after CONNECT failure",
                extra={"channel": label, "proxy_status": circuit_error.status_code},
            )
            continue
        if not proxy_backed:
            independent_channel_attempted = True
        try:
            body = await _fetch_body_with_proxy_probe_gate(
                label,
                fetch_url,
                source_id=source_id,
                proxy_backed=proxy_backed,
            )
            payload = extract_json_payload(body)
            if isinstance(payload, (dict, list)):
                result = _payload_to_dict(payload)
                _record_json_transport(
                    cache_key=key,
                    source_id=source_id,
                    transport_backend=_json_transport_label(
                        label,
                        proxy_backed=proxy_backed,
                    ),
                )
                log_action(
                    "api.route.ok",
                    source_id=source_id,
                    backend=f"hsreplay_{label}",
                    bytes_out=len(body.encode("utf-8", errors="replace")),
                    extra={"channel": label, "api_url": api_url},
                )
                log_action(
                    "routing.channel.ok",
                    source_id=source_id,
                    backend=f"hsreplay_{label}",
                    bytes_out=len(body.encode("utf-8", errors="replace")),
                    extra={"channel": label, "api_url": api_url},
                )
                set_cached_hsreplay_json(key, result)
                return result
            err = f"{label}: payload is not JSON object"
            errors.append(err)
            log_action(
                "routing.channel.fail",
                source_id=source_id,
                detail=err,
                level="warn",
                extra={"channel": label},
            )
        except _HsReplayProxyCircuitOpen as exc:
            first_proxy_error = first_proxy_error or exc.proxy_error
            errors.append(f"{label}: skipped after proxy CONNECT failure")
            log_action(
                "routing.channel.skip",
                source_id=source_id,
                level="warn",
                detail="proxy-backed HSReplay JSON channel skipped after CONNECT failure",
                extra={
                    "channel": label,
                    "proxy_status": exc.proxy_error.status_code,
                },
            )
            continue
        except ProxyPaymentRequiredError as exc:
            record_hsreplay_proxy_failure(exc)
            first_proxy_error = first_proxy_error or exc
            errors.append(f"{label}: proxy CONNECT HTTP {exc.status_code}")
            log_action(
                "routing.channel.fail",
                source_id=source_id,
                detail=f"{label}: proxy CONNECT HTTP {exc.status_code}",
                level="warn",
                extra={"channel": label, "proxy_status": exc.status_code},
            )
            continue
        except Exception as exc:
            # Provider/transport errors may contain reflected request data.
            # Keep operational logs typed and bounded; never include raw body,
            # cookie headers, or token-bearing provider URLs.
            error_name = type(exc).__name__
            errors.append(f"{label}: {error_name}")
            logger.warning(
                "HSReplay JSON channel %s failed for source %s (%s)",
                label,
                source_id,
                error_name,
            )
            log_action(
                "routing.channel.fail",
                source_id=source_id,
                detail=f"{label}: {error_name}",
                level="warn",
                extra={"channel": label},
            )
        await asyncio.sleep(api_json_retry_delay_seconds())

    detail = "Could not fetch HSReplay JSON: " + "; ".join(errors)
    log_action("api.route.fail", source_id=source_id, detail=detail, level="error")
    if first_proxy_error is not None and not independent_channel_attempted:
        raise first_proxy_error
    raise RuntimeError(detail)


async def fetch_hsreplay_json(
    api_url: str,
    *,
    source_id: str,
    cache_key: str | None = None,
) -> dict[str, Any]:
    """Fetch and single-flight one HSReplay JSON snapshot per refresh cache key."""

    key = cache_key or api_url
    async with _json_key_gate(key):
        return await _fetch_hsreplay_json_serialized(
            api_url,
            source_id=source_id,
            key=key,
        )


def _is_bg_comps_listing_url(page_url: str) -> bool:
    normalized = page_url.rstrip("/")
    return normalized.endswith(
        "hsreplay.net/battlegrounds/comps"
    ) or normalized.endswith("/battlegrounds/comps")


def _markdown_body_usable(body: str, page_url: str) -> bool:
    """Reject FlareSolverr listing HTML; accept Jina markdown or comp detail pages."""
    if not body:
        return False
    lower = body.lower()
    if "just a moment" in lower or "cf-chl" in lower:
        return False
    if "Markdown Content:" in body:
        return len(body) >= 200
    try:
        from .battlegrounds_comps_parse import _find_comp_headers

        header_count = len(_find_comp_headers(body))
        if _is_bg_comps_listing_url(page_url):
            return header_count >= 3
        if header_count >= 1:
            return True
    except Exception:
        pass
    if "hearthstonejson.com" in lower or "battlegrounds/minions/" in lower:
        return len(body) >= 200
    return len(body) >= 400


async def fetch_hsreplay_markdown(url: str, *, source_id: str) -> tuple[str, str]:
    """Return (body, backend label e.g. hsreplay_jina)."""
    errors: list[str] = []
    for label, fetch_url in _markdown_channel_urls(url):
        try:
            if label == "flaresolverr":
                body = await fetch_text_via_flaresolverr(fetch_url, source_id=source_id)
            elif label == "curl_cffi":
                body = await fetch_text_via_curl_cffi(fetch_url, source_id=source_id)
            else:
                body = await download_text(fetch_url, source_id=source_id)
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            await asyncio.sleep(api_json_retry_delay_seconds())
            continue
        if _markdown_body_usable(body, url):
            return body, f"hsreplay_{label}"
        errors.append(f"{label}: body not usable markdown ({len(body)} bytes)")
        await asyncio.sleep(api_json_retry_delay_seconds())
    raise RuntimeError("Could not fetch HSReplay markdown: " + "; ".join(errors))


async def fetch_hsreplay_html(url: str, *, source_id: str) -> tuple[str, str]:
    """Rendered HTML for HSReplay pages (FlareSolverr first)."""
    errors: list[str] = []
    order = [("flaresolverr", url), ("curl_cffi", url)]
    for label, fetch_url in order:
        try:
            if label == "flaresolverr":
                body = await fetch_text_via_flaresolverr(fetch_url, source_id=source_id)
            else:
                body = await fetch_text_via_curl_cffi(fetch_url, source_id=source_id)
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        if len(body) > 5000 and "just a moment" not in body.lower():
            return body, f"hsreplay_{label}"
        errors.append(f"{label}: html too short")
    raise RuntimeError("Could not fetch HSReplay HTML: " + "; ".join(errors))
