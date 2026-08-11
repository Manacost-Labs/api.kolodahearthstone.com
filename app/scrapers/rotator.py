from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable

from ..config import fetch_backend_max_seconds, fetch_max_retries
from ..proxy_errors import ProxyPaymentRequiredError
from ..publish_gate import validate_candidate_for_publish
from ..refresh_log import log_action
from ..source_state import SourceState
from ..sources import Source
from .base import FetchResult
from .http_resilience import DEFAULT_BACKOFF_SECONDS, backoff_delay_seconds
from .proxy import (
    assert_proxy_configured,
    burn_proxy_session,
    source_can_use_flaresolverr_without_proxy,
)
from .quality import looks_like_real_page, quality_metrics

logger = logging.getLogger(__name__)

BackendFn = Callable[[Source], Awaitable[FetchResult]]
_backend_failures: Counter[tuple[str, str, str]] = Counter()
_CIRCUIT_THRESHOLD = 2


def classify_backend_error(exc_type: str, detail: str) -> str:
    text = f"{exc_type} {detail}".lower()
    if "timeout" in text:
        return "timeout"
    if "err_name_not_resolved" in text or "name or service" in text or "dns" in text:
        return "dns_error"
    if "407" in text:
        return "proxy_407"
    if "403" in text or "cloudflare" in text or "captcha" in text or "challenge" in text:
        return "blocked_403"
    if "tls" in text or "ssl" in text or "certificate" in text:
        return "tls_error"
    if "not json" in text or "jsondecode" in text:
        return "not_json"
    if "quality check failed" in text or "too few" in text or "missing metrics" in text:
        return "quality_empty"
    if "empty shell" in text or "looks like" in text:
        return "empty_shell"
    return "backend_error"


def _circuit_scope(source: Source, classification: str) -> str:
    if classification in {"timeout", "quality_empty", "empty_shell"}:
        return f"source:{source.id}"
    return f"site:{source.site}"


def _circuit_scope_label(source: Source, classification: str) -> str:
    return _circuit_scope(source, classification).replace(":", " ", 1)


def _circuit_key(source: Source, backend: str, classification: str) -> tuple[str, str, str]:
    return (_circuit_scope(source, classification), backend, classification)


def _open_circuit(source: Source, backend: str) -> tuple[str, int] | None:
    for (scope, name, classification), count in _backend_failures.items():
        if scope == _circuit_scope(source, classification) and name == backend and count >= _CIRCUIT_THRESHOLD:
            return classification, count
    return None


def _record_backend_success(source: Source, backend: str) -> None:
    for key in list(_backend_failures):
        scope, name, classification = key
        if name == backend and scope == _circuit_scope(source, classification):
            del _backend_failures[key]


def reset_backend_circuits() -> None:
    _backend_failures.clear()


def _site_backend_order(source: Source) -> list[str]:
    from ..fetch_routes import configured_browser_backend_names

    configured = list(configured_browser_backend_names(source))
    if source.site == "hsguru":
        # Patchright has been noisy for HSGuru DNS in production. Keep it
        # configurable, but late in the default order.
        preferred = configured
    else:
        from ..config import hsreplay_storage_path

        preferred = [
            "patchright",
            "cloakbrowser",
            "flaresolverr",
            "scrapling",
            "curl_cffi",
            "cloudscraper",
            "camoufox",
        ]
        if not hsreplay_storage_path().exists():
            preferred = [
                "flaresolverr",
                "cloakbrowser",
                "patchright",
                "scrapling",
                "curl_cffi",
                "cloudscraper",
                "camoufox",
            ]
    ordered: list[str] = []
    for name in preferred:
        if name in configured and name not in ordered:
            ordered.append(name)
    for name in configured:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _ordered_backends(source: Source | None = None) -> list[tuple[str, BackendFn, Callable[[], bool]]]:
    from ..config import fetch_backends
    from .camoufox_browser import camoufox_available, fetch_via_camoufox
    from .cloakbrowser_pool import cloakbrowser_available, fetch_via_cloakbrowser
    from .cloudscraper_client import fetch_via_cloudscraper
    from .curl_impersonate import curl_cffi_available, fetch_via_curl_cffi
    from .flaresolverr import fetch_via_flaresolverr
    from .patchright_browser import fetch_via_patchright, patchright_available
    from .scrapling_browser import fetch_via_scrapling, scrapling_available

    registry: dict[str, tuple[BackendFn, Callable[[], bool]]] = {
        "flaresolverr": (fetch_via_flaresolverr, lambda: True),
        "cloakbrowser": (fetch_via_cloakbrowser, cloakbrowser_available),
        "scrapling": (fetch_via_scrapling, scrapling_available),
        "patchright": (fetch_via_patchright, patchright_available),
        "playwright": (fetch_via_patchright, patchright_available),
        "camoufox": (fetch_via_camoufox, camoufox_available),
        "cloudscraper": (fetch_via_cloudscraper, lambda: True),
        "curl_cffi": (fetch_via_curl_cffi, curl_cffi_available),
        "cloudflare_scrape": (fetch_via_cloudscraper, lambda: True),
    }

    names = _site_backend_order(source) if source else [b.lower() for b in fetch_backends()]
    if source and source.site == "hsreplay":
        from ..config import hsreplay_storage_path

        if hsreplay_storage_path().exists():
            # Authenticated HSReplay pages prefer Patchright because it can use
            # browser storage_state, but keeping FlareSolverr/Scrapling/curl_cffi
            # fallbacks prevents a single slow browser path from blocking refreshes.
            names = sorted(names, key=lambda n: 0 if n == "patchright" else 1)
    ordered: list[tuple[str, BackendFn, Callable[[], bool]]] = []
    for key in names:
        if key not in registry:
            logger.warning("Unknown fetch backend %r, skipping", key)
            continue
        fn, available = registry[key]
        ordered.append((key, fn, available))
    return ordered


async def fetch_html(
    source: Source,
    *,
    preferred_backend: str | None = None,
    parse_preview: Callable[[str], dict] | None = None,
) -> FetchResult:
    configured_backends = _site_backend_order(source)
    if not (
        "flaresolverr" in configured_backends
        and source_can_use_flaresolverr_without_proxy(source)
    ):
        assert_proxy_configured()
    backends = _ordered_backends(source)
    if not backends:
        raise RuntimeError("No fetch backends configured (HS_FETCH_BACKENDS).")

    if preferred_backend:
        preferred = preferred_backend.strip().lower()
        backends = sorted(backends, key=lambda item: 0 if item[0] == preferred else 1)

    backend_names = [b[0] for b in backends]
    log_action(
        "browser.fetch.begin",
        source_id=source.id,
        url=source.fetch_url,
        extra={"backends": backend_names, "preferred": preferred_backend},
    )

    errors: list[str] = []
    proxy_407_error: ProxyPaymentRequiredError | None = None
    for attempt in range(1, fetch_max_retries() + 1):
        log_action(
            "browser.round.begin",
            source_id=source.id,
            attempt=attempt,
            extra={"backends": backend_names},
        )
        for name, fetch_fn, is_available in backends:
            if proxy_407_error is not None and not (
                name == "flaresolverr"
                and source_can_use_flaresolverr_without_proxy(source)
            ):
                detail = f"{name}: skipped after residential proxy 407"
                errors.append(detail)
                log_action(
                    "browser.backend.skip",
                    source_id=source.id,
                    backend=name,
                    attempt=attempt,
                    detail=detail,
                    level="warn",
                    extra={"classification": "proxy_407"},
                )
                continue
            open_state = _open_circuit(source, name)
            if open_state is not None:
                classification, count = open_state
                scope_label = _circuit_scope_label(source, classification)
                detail = f"{name}: circuit open for {scope_label} after {count} {classification} failures"
                errors.append(detail)
                log_action(
                    "browser.backend.skip",
                    source_id=source.id,
                    backend=name,
                    attempt=attempt,
                    detail=detail,
                    level="warn",
                    extra={
                        "classification": classification,
                        "failure_count": count,
                        "circuit_scope": _circuit_scope(source, classification),
                    },
                )
                continue
            if not is_available():
                detail = f"{name}: not installed"
                errors.append(detail)
                log_action(
                    "browser.backend.skip",
                    source_id=source.id,
                    backend=name,
                    attempt=attempt,
                    detail=detail,
                    level="warn",
                )
                continue
            started = time.monotonic()
            log_action(
                "browser.backend.try",
                source_id=source.id,
                backend=name,
                attempt=attempt,
                url=source.fetch_url,
            )
            try:
                max_s = fetch_backend_max_seconds()
                if max_s is not None:
                    result = await asyncio.wait_for(fetch_fn(source), timeout=max_s)
                else:
                    result = await fetch_fn(source)
                html_len = len(result.html)
                if not looks_like_real_page(result.html, source):
                    raise RuntimeError("page looks like Cloudflare or empty shell")
                if parse_preview is not None:
                    parsed = parse_preview(result.html)
                    gate = validate_candidate_for_publish(source, parsed, backend=name)
                    if not gate.ok:
                        log_action(
                            "browser.quality.fail",
                            source_id=source.id,
                            backend=name,
                            attempt=attempt,
                            detail=gate.reason,
                            level="warn",
                            extra={
                                "quality_metrics": quality_metrics(source, parsed),
                                "publish_gate": gate.extra,
                            },
                        )
                        raise RuntimeError(f"quality check failed: {gate.reason}")
                logger.info(
                    "Fetched %s via %s attempt=%d (%d bytes)",
                    source.id,
                    name,
                    attempt,
                    html_len,
                )
                log_action(
                    "browser.backend.ok",
                    source_id=source.id,
                    backend=name,
                    state="ok",
                    attempt=attempt,
                    duration_ms=(time.monotonic() - started) * 1000,
                    http_status=result.http_status,
                    url=result.final_url,
                    bytes_out=html_len,
                )
                _record_backend_success(source, name)
                return result
            except TimeoutError:
                max_s = fetch_backend_max_seconds()
                msg = f"{name}[{attempt}]: TimeoutError: exceeded {max_s}s backend cap"
                classification = "timeout"
                _backend_failures[_circuit_key(source, name, classification)] += 1
                errors.append(msg)
                logger.warning("Backend failed for %s — %s", source.id, msg)
                log_action(
                    "browser.backend.fail",
                    source_id=source.id,
                    backend=name,
                    error_type="TimeoutError",
                    detail=msg,
                    attempt=attempt,
                    duration_ms=(time.monotonic() - started) * 1000,
                    level="error",
                    extra={
                        "classification": classification,
                        "failure_count": _backend_failures[_circuit_key(source, name, classification)],
                        "circuit_scope": _circuit_scope(source, classification),
                    },
                )
                continue
            except Exception as exc:
                msg = f"{name}[{attempt}]: {type(exc).__name__}: {exc}"
                classification = classify_backend_error(type(exc).__name__, str(exc))
                _backend_failures[_circuit_key(source, name, classification)] += 1
                if source.site == "hsguru" and classification == "blocked_403":
                    burn_proxy_session(
                        source.id,
                        page_url=source.fetch_url,
                        reason=f"{name}_blocked_403",
                    )
                    log_action(
                        "proxy.session.burn",
                        source_id=source.id,
                        backend=name,
                        attempt=attempt,
                        level="warn",
                        detail=f"{name} blocked by Cloudflare/403; rotated HSGuru proxy session",
                        extra={"classification": classification},
                    )
                errors.append(msg)
                logger.warning("Backend failed for %s — %s", source.id, msg)
                log_action(
                    "browser.backend.fail",
                    source_id=source.id,
                    backend=name,
                    error_type=type(exc).__name__,
                    detail=str(exc)[:1000],
                    attempt=attempt,
                    duration_ms=(time.monotonic() - started) * 1000,
                    level="error",
                    extra={
                        "classification": classification,
                        "failure_count": _backend_failures[_circuit_key(source, name, classification)],
                        "circuit_scope": _circuit_scope(source, classification),
                    },
                )
                if classification == "proxy_407":
                    # Do not repeat paid-proxy routes. A configured proxyless
                    # FlareSolverr route may still recover this source.
                    proxy_407_error = ProxyPaymentRequiredError(
                        "Residential proxy payment required (407)"
                    )
        if proxy_407_error is not None and not any(
            name == "flaresolverr"
            and source_can_use_flaresolverr_without_proxy(source)
            for name, _fetch_fn, _available in backends
        ):
            break
        if attempt < fetch_max_retries():
            # FIX: exponential backoff with jitter (5s → 15s → 45s), not fixed 3*attempt
            delay = backoff_delay_seconds(attempt, schedule=DEFAULT_BACKOFF_SECONDS)
            log_action(
                "browser.round.sleep",
                source_id=source.id,
                attempt=attempt,
                extra={"delay_seconds": round(delay, 2)},
            )
            await asyncio.sleep(delay * random.uniform(0.9, 1.1))

    detail = "; ".join(errors[-12:])
    log_action(
        "browser.fetch.end",
        source_id=source.id,
        state=SourceState.FETCH_ERROR,
        detail=detail,
        level="error",
    )
    if proxy_407_error is not None:
        raise proxy_407_error
    raise RuntimeError(detail)
