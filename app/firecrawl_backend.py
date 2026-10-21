from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString

from . import provider_usage
from .brightdata_backend import (
    BrightDataPolicyError,
    BrightDataRequestError,
    _assert_public_https_target,
    brightdata_configured_for_source,
)
from .brightdata_backend import scrape_url_sync as brightdata_scrape_url_sync
from .config import (
    decodo_api_key,
    firecrawl_hsguru_matchups_timeout_ms,
    firecrawl_max_age_ms,
    firecrawl_timeout_ms,
    firecrawl_wait_ms,
    scrape_do_token,
)
from .decodo_backend import scrape_url_sync as decodo_scrape_url_sync
from .firecrawl_keys import (
    acquire_firecrawl_key,
    is_firecrawl_credit_error,
    mark_firecrawl_key_exhausted,
    parse_firecrawl_api_keys,
    record_firecrawl_credits,
)
from .scrape_do_backend import (
    SCRAPE_DO_MAX_SESSION_ID,
    ScrapeDoAccountError,
    ScrapeDoContentError,
    ScrapeDoRequestError,
    ScrapeDoScrape,
    ScrapeDoTransientError,
    scrape_url_sync,
)
from .meta_intelligence.budget import BudgetExceeded, source_scope, validate_attempt
from .scrapers.http_resilience import is_session_blocked
from .scrapfly_backend import scrape_url_sync as scrapfly_scrape_url_sync
from .scrapfly_backend import scrapfly_configured
from .sources import Source

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
_PROVIDER_POLICY = "scrape_do_decodo_firecrawl_brightdata_scrapfly"
# Cheapest-first Scrape.do (render, super) profiles for HSGuru HTML, measured
# on 2026-10-21 (Scrape.do bills only successful responses):
# - plain datacenter (1 credit) never passes HSGuru's Cloudflare challenge
#   (0/40) and only burned 15-57 s per page, so it is no longer tried;
# - datacenter render (5) passes ~93% with complete tables;
# - residential without a browser (10) passes 32/32 in ~2 s, but only
#   anonymously and with Scrape.do's own (free) retries enabled;
# - residential render (25) is the last resort.
_HSGURU_SCRAPE_DO_PROFILES = (
    (True, False),
    (False, True),
    (True, True),
)
# HSGuru pages are Phoenix LiveView dead renders: every real page (including
# a legitimately empty window) carries a LiveView root, the Cloudflare
# interstitial never does. Waiting for it replaces a fixed customWait.
_HSGURU_WAIT_SELECTOR = "[data-phx-session]"
# Cloudflare cookies are bound to the browser/IP that obtained them. Forwarded
# through a proxy they trigger BLOCKED_BY_WAF, so only the site session goes.
_CLOUDFLARE_COOKIE_PREFIXES = ("__cf", "_cf", "cf_")
_HSGURU_PAYWALL_MARKERS = ("you do not have access to these filters",)
_SCREENSHOT_MIN_BYTES = 1_024
_SCREENSHOT_MAX_BYTES = 25 * 1024 * 1024
_HTML_SITE_MIN_BYTES = 256


ProviderResultValidator = Callable[["FirecrawlScrape"], bool]
ProviderAttemptObserver = Callable[["FirecrawlScrape", bool], None]
ProviderFailureObserver = Callable[[dict[str, Any]], None]


def _provider_error_code(exc: Exception) -> str:
    known = re.search(
        r"\b(ROTATION_FAILED|PAYMENT_REQUIRED|AUTHENTICATION_FAILED|"
        r"CONCURRENT_REQUEST_LIMIT|RATE_LIMITED)\b",
        str(exc),
    )
    return known.group(1) if known else type(exc).__name__


def _notify_provider_failure(
    observer: ProviderFailureObserver | None,
    backend: str,
    exc: Exception,
    *,
    profile_attempt: int | None = None,
    provider_attempt: int | None = None,
    super_proxy: bool | None = None,
) -> None:
    if observer is None:
        return
    status = getattr(exc, "status_code", None)
    if status is None and isinstance(exc, ScrapeDoContentError):
        status = exc.scrape.status_code
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    request_credits = getattr(exc, "request_cost", 0)
    if isinstance(exc, ScrapeDoContentError):
        request_credits = exc.scrape.request_cost
    elif isinstance(exc, BrightDataRequestError):
        request_credits = 0 if exc.billed is False else 1
    event = {
        "backend": backend,
        "state": "failed",
        "http_status": int(status or 0),
        "request_credits": int(request_credits or 0),
        "error_type": type(exc).__name__,
        "error_code": _provider_error_code(exc),
    }
    if profile_attempt is not None:
        event["profile_attempt"] = profile_attempt
    if provider_attempt is not None:
        event["provider_attempt"] = provider_attempt
    if super_proxy is not None:
        event["super_proxy"] = super_proxy
    try:
        observer(event)
    except Exception:  # noqa: BLE001,S110 - telemetry must not break acquisition
        pass


@dataclass(frozen=True)
class FirecrawlScrape:
    html: str
    markdown: str
    screenshot: str | None
    metadata: dict[str, Any]
    status_code: int
    final_url: str

    @property
    def content_length(self) -> int:
        return len(self.html.encode("utf-8", errors="replace"))

    @property
    def backend(self) -> str:
        return str(self.metadata.get("backend") or "firecrawl")

    @property
    def firecrawl_credits_used(self) -> int:
        if self.backend != "firecrawl":
            return 0
        try:
            return int(self.metadata.get("creditsUsed") or 1)
        except (TypeError, ValueError):
            return 1

    @property
    def scrape_do_credits_used(self) -> int:
        if not self.backend.startswith("scrape_do"):
            return 0
        try:
            return int(self.metadata.get("scrapeDoCreditsUsed") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def scrapfly_credits_used(self) -> int:
        if self.backend != "scrapfly":
            return 0
        try:
            return int(self.metadata.get("scrapflyCreditsUsed") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def brightdata_credits_used(self) -> int:
        if self.backend != "brightdata_web_unlocker":
            return 0
        try:
            return int(self.metadata.get("brightDataBillableRequests") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def brightdata_requests_used(self) -> int:
        if self.backend != "brightdata_web_unlocker":
            return 0
        try:
            return int(self.metadata.get("brightDataRequests") or 1)
        except (TypeError, ValueError):
            return 1

    @property
    def request_credits(self) -> int:
        return (
            self.scrape_do_credits_used
            or self.scrapfly_credits_used
            or self.brightdata_credits_used
            or self.firecrawl_credits_used
        )


class _ScreenshotNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # pyright: ignore[reportImplicitOverride]
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl


def _image_mime(raw: bytes) -> str | None:
    if not _SCREENSHOT_MIN_BYTES <= len(raw) <= _SCREENSHOT_MAX_BYTES:
        return None
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(raw) < 33 or raw[12:16] != b"IHDR" or b"IEND" not in raw[-32:]:
            return None
        width = int.from_bytes(raw[16:20], "big")
        height = int.from_bytes(raw[20:24], "big")
        if not 64 <= width <= 20_000 or not 64 <= height <= 20_000:
            return None
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff") and raw.rstrip().endswith(b"\xff\xd9"):
        return "image/jpeg"
    if (
        raw.startswith(b"RIFF")
        and raw[8:12] == b"WEBP"
        and raw[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
    ):
        declared_size = int.from_bytes(raw[4:8], "little") + 8
        if declared_size != len(raw):
            return None
        return "image/webp"
    return None


def _decode_inline_screenshot(value: str) -> tuple[str, bytes] | None:
    header, separator, encoded = value.partition(",")
    declared_mime: str | None = None
    if separator:
        declared_mime = {
            "data:image/png;base64": "image/png",
            "data:image/jpeg;base64": "image/jpeg",
            "data:image/jpg;base64": "image/jpeg",
            "data:image/webp;base64": "image/webp",
        }.get(header.casefold())
        if declared_mime is None:
            return None
    else:
        # Scrape.do's documented screenshot response is the bare Base64
        # payload rather than a data URI. It is still accepted only after the
        # same bounded decoding and image-signature validation below.
        encoded = value
    if not encoded:
        return None
    maximum_encoded_size = ((_SCREENSHOT_MAX_BYTES + 2) // 3) * 4
    if len(encoded) > maximum_encoded_size:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    detected_mime = _image_mime(raw)
    if detected_mime is None or (
        declared_mime is not None and detected_mime != declared_mime
    ):
        return None
    return detected_mime, raw


def _normalize_public_https_url(value: str) -> str:
    parsed = urlsplit(value)
    normalized = urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, "")
    )
    _assert_public_https_target(normalized)
    return normalized


def _download_https_screenshot(value: str) -> bytes:
    normalized = _normalize_public_https_url(value)
    request = urllib.request.Request(
        normalized,
        headers={
            "Accept": "image/png,image/jpeg,image/webp",
            "User-Agent": "HSDataAPI/0.1 screenshot validator",
        },
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _ScreenshotNoRedirectHandler(),
    )
    try:
        with opener.open(request, timeout=30.0) as response:
            status_code = int(getattr(response, "status", 0) or 0)
            if not 200 <= status_code <= 299:
                raise ValueError("screenshot response was not successful")
            raw = response.read(_SCREENSHOT_MAX_BYTES + 1)
    except (OSError, ValueError):
        raise ValueError("screenshot download failed") from None
    if len(raw) > _SCREENSHOT_MAX_BYTES:
        raise ValueError("screenshot response was too large")
    return raw


def _normalize_screenshot(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    decoded = _decode_inline_screenshot(text)
    if decoded is not None:
        mime, raw = decoded
    elif text.casefold().startswith("https://"):
        try:
            raw = _download_https_screenshot(text)
        except (BrightDataPolicyError, OSError, ValueError):
            return None
        mime = _image_mime(raw)
        if mime is None:
            return None
    else:
        return None
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _normalize_requested_screenshot(
    scraped: FirecrawlScrape,
    formats: list[Any] | None,
) -> FirecrawlScrape:
    screenshot_requested, _full_page = _screenshot_options(formats)
    if not screenshot_requested:
        return scraped
    return replace(scraped, screenshot=_normalize_screenshot(scraped.screenshot))


def _html_to_markdown(html: str) -> str:
    if not html.strip():
        return ""
    soup = BeautifulSoup(html, "lxml")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    for node in soup.find_all("br"):
        node.replace_with(NavigableString("\n"))
    for node in soup.find_all("a"):
        text = node.get_text(" ", strip=True)
        href = str(node.get("href") or "").strip()
        node.replace_with(
            NavigableString(f"[{text}]({href})" if text and href else text)
        )
    for level in range(1, 7):
        for node in soup.find_all(f"h{level}"):
            text = node.get_text(" ", strip=True)
            node.replace_with(NavigableString(f"\n{'#' * level} {text}\n"))
    for node in soup.find_all("li"):
        text = node.get_text(" ", strip=True)
        node.replace_with(NavigableString(f"\n- {text}"))
    for node in soup.find_all("tr"):
        cells = [
            cell.get_text(" ", strip=True)
            for cell in node.find_all(["th", "td"], recursive=False)
        ]
        if cells:
            node.replace_with(NavigableString("\n" + " | ".join(cells)))
    lines = [
        " ".join(line.split())
        for line in soup.get_text("\n").splitlines()
        if line.strip()
    ]
    return "\n".join(lines)


def _screenshot_options(formats: list[Any] | None) -> tuple[bool, bool]:
    for item in formats or []:
        if isinstance(item, str) and item.casefold() == "screenshot":
            return True, False
        if isinstance(item, dict) and item.get("type") == "screenshot":
            return True, bool(item.get("fullPage"))
    return False, False


def _scrape_via_scrapfly(
    source: Source,
    *,
    formats: list[Any] | None,
    headers: dict[str, str] | None,
    wait_ms: int | None,
    timeout_ms: int | None,
    reason: str,
) -> FirecrawlScrape:
    screenshot, full_screenshot = _screenshot_options(formats)
    profiles = (
        ((True, True),) if source.site == "hsguru" else ((True, False), (True, True))
    )
    errors: list[str] = []
    scraped = None
    for render_js, asp in profiles:
        try:
            scraped = scrapfly_scrape_url_sync(
                source.url,
                render_js=render_js,
                asp=asp,
                headers=headers,
                wait_ms=wait_ms,
                timeout_ms=timeout_ms,
                screenshot=screenshot,
                full_screenshot=full_screenshot,
            )
            break
        except Exception as exc:  # noqa: BLE001 - try the next provider profile
            errors.append(
                f"render_js={render_js},asp={asp}: "
                f"{type(exc).__name__}: {str(exc)[:300]}"
            )
    if scraped is None:
        raise RuntimeError(f"Scrapfly fallback failed: {'; '.join(errors)}")

    requested = formats or ["html", "markdown"]
    return FirecrawlScrape(
        html=scraped.html,
        markdown=(_html_to_markdown(scraped.html) if "markdown" in requested else ""),
        screenshot=scraped.screenshot,
        metadata={
            "backend": "scrapfly",
            "creditsUsed": 0,
            "scrapflyCreditsUsed": scraped.request_cost,
            "scrapflyRemainingCredits": scraped.credits_remaining,
            "scrapflyAsp": scraped.asp,
            "scrapflyKeyLabel": scraped.key_label,
            "scrapflyKeyFingerprint": scraped.key_fingerprint,
            "scrapflyKeyRotation": scraped.key_rotation,
            "usageCallId": getattr(scraped, "usage_call_id", None),
            "providerPolicy": _PROVIDER_POLICY,
            "firecrawlFallbackReason": reason[:500],
        },
        status_code=scraped.status_code,
        final_url=scraped.final_url,
    )


class HSGuruSessionRejectedError(ScrapeDoRequestError):
    """The logged-in browser request still got HSGuru's premium paywall."""


def _is_hsguru_paywall(content: str) -> bool:
    """HSGuru's premium-filter notice on a page without any data table."""
    lowered = content.casefold()
    return "<table" not in lowered and any(
        marker in lowered for marker in _HSGURU_PAYWALL_MARKERS
    )


def _without_cloudflare_cookies(cookie_header: str) -> str:
    kept = []
    for part in cookie_header.split(";"):
        name = part.split("=", 1)[0].strip()
        if name and not name.casefold().startswith(_CLOUDFLARE_COOKIE_PREFIXES):
            kept.append(part.strip())
    return "; ".join(kept)


def _hsguru_session_id(render: bool, super_proxy: bool) -> int:
    """A sticky Scrape.do session per worker thread and proxy profile."""
    seed = f"hsguru:{os.getpid()}:{threading.get_ident()}:{render}:{super_proxy}"
    return zlib.crc32(seed.encode()) % SCRAPE_DO_MAX_SESSION_ID


def _hsguru_scrape_do_options(
    headers: dict[str, str] | None,
    *,
    render: bool,
    super_proxy: bool,
    wait_ms: int | None,
) -> dict[str, Any]:
    """Per-profile HSGuru request shape backed by the 2026-10-21 benchmark."""
    forwarded: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if name.casefold() != "cookie":
            forwarded[name] = value
        elif render:
            # The logged-in session unlocks premium filters in the browser.
            cookie = _without_cloudflare_cookies(value)
            if cookie:
                forwarded[name] = cookie
        # Without a browser any forwarded cookie gets blocked by Cloudflare;
        # premium-only pages fail validation there and escalate instead.
    options: dict[str, Any] = {
        "headers": forwarded or None,
        "session_id": _hsguru_session_id(render, super_proxy),
    }
    if render:
        options.update(
            wait_ms=None,
            wait_selector=_HSGURU_WAIT_SELECTOR,
            block_resources=True,
        )
    else:
        options["wait_ms"] = wait_ms
    return options


@source_scope
def _scrape_via_scrape_do(
    source: Source,
    *,
    formats: list[Any] | None,
    headers: dict[str, str] | None,
    wait_ms: int | None,
    timeout_ms: int | None,
    reason: str,
    attempt_observer: ProviderAttemptObserver | None = None,
    failure_observer: ProviderFailureObserver | None = None,
    accept_html: Callable[[str], bool] | None = None,
    accept_result: ProviderResultValidator | None = None,
    profiles: tuple[tuple[bool, bool], ...] | None = None,
    attempt_offset: int = 0,
) -> FirecrawlScrape:
    if not scrape_do_token():
        raise RuntimeError(reason or "Scrape.do is not configured")
    screenshot, full_screenshot = _screenshot_options(formats)
    adaptive = source.site == "hsguru" and not screenshot
    profiles = profiles or (
        _HSGURU_SCRAPE_DO_PROFILES
        if adaptive
        else ((True, True),)
        if source.site == "hsguru"
        else ((True, False), (True, True))
    )
    errors: list[str] = []
    scraped = None
    attempts = attempt_offset
    last_failure: Exception | None = None
    for render, super_proxy in profiles:
        # Escalate on the next call rather than retrying the cheap profile.
        for profile_attempt in range(1, 2 if adaptive else 3):
            attempts += 1
            try:
                # Scrape.do retries are free (only successes are billed), so
                # HSGuru keeps them: disabling them made residential fail.
                request_options = (
                    _hsguru_scrape_do_options(
                        headers,
                        render=render,
                        super_proxy=super_proxy,
                        wait_ms=wait_ms,
                    )
                    if adaptive
                    else {"headers": headers, "wait_ms": wait_ms}
                )
                scraped = scrape_url_sync(
                    source.url,
                    render=render,
                    super_proxy=super_proxy,
                    timeout_ms=timeout_ms,
                    screenshot=screenshot,
                    full_screenshot=full_screenshot,
                    **request_options,
                )
                if adaptive:
                    candidate = _scrape_do_result(
                        scraped,
                        formats=formats,
                        attempts=attempts,
                        profile_attempt=profile_attempt,
                        reason=reason,
                        render=render,
                    )
                    accepted = _accepted_provider_result(
                        candidate,
                        source,
                        formats=formats,
                        accept_html=accept_html,
                        accept_result=accept_result,
                    )
                    if not accepted:
                        provider_usage.mark_rejected(
                            getattr(scraped, "usage_call_id", None)
                        )
                        if attempt_observer is not None:
                            attempt_observer(candidate, False)
                        validate_attempt(scraped.acquisition_id, False)
                        if render and _is_hsguru_paywall(scraped.html) and any(
                            name.casefold() == "cookie"
                            for name in request_options.get("headers") or {}
                        ):
                            # An expired session would be billed again by
                            # every remaining profile; stop the ladder.
                            raise HSGuruSessionRejectedError(
                                "HSGuru session no longer unlocks premium filters",
                                request_cost=scraped.request_cost,
                                super_proxy=super_proxy,
                            )
                        scraped = None
                        last_failure = RuntimeError(
                            "Scrape.do response failed content validation"
                        )
                        break
                if is_session_blocked(scraped.status_code, scraped.html):
                    provider_usage.mark_rejected(
                        getattr(scraped, "usage_call_id", None), "blocked_content"
                    )
                    if attempt_observer is not None:
                        attempt_observer(
                            _scrape_do_result(
                                scraped,
                                formats=formats,
                                attempts=attempts,
                                profile_attempt=profile_attempt,
                                reason=reason,
                            ),
                            False,
                        )
                    validate_attempt(scraped.acquisition_id, False)
                    errors.append(
                        f"{'super' if super_proxy else 'standard'} "
                        f"attempt {profile_attempt}: blocked_or_challenge_content"
                    )
                    last_failure = ScrapeDoRequestError(
                        "Scrape.do returned blocked or challenge content",
                        status_code=scraped.status_code,
                        super_proxy=super_proxy,
                    )
                    _notify_provider_failure(
                        failure_observer,
                        "scrape_do_super" if super_proxy else "scrape_do",
                        last_failure,
                        profile_attempt=profile_attempt,
                        provider_attempt=attempts,
                        super_proxy=super_proxy,
                    )
                    scraped = None
                    break
                break
            except BudgetExceeded:
                raise
            except ScrapeDoContentError as exc:
                last_failure = exc
                if attempt_observer is not None:
                    attempt_observer(
                        _scrape_do_result(
                            exc.scrape,
                            formats=formats,
                            attempts=attempts,
                            profile_attempt=profile_attempt,
                            reason=reason,
                        ),
                        False,
                    )
                _notify_provider_failure(
                    failure_observer,
                    "scrape_do_super" if super_proxy else "scrape_do",
                    exc,
                    profile_attempt=profile_attempt,
                    provider_attempt=attempts,
                    super_proxy=super_proxy,
                )
                errors.append(
                    f"{'super' if super_proxy else 'standard'} "
                    f"attempt {profile_attempt}: {type(exc).__name__}"
                )
                break
            except ScrapeDoAccountError as exc:
                last_failure = exc
                _notify_provider_failure(
                    failure_observer,
                    "scrape_do_super" if super_proxy else "scrape_do",
                    exc,
                    profile_attempt=profile_attempt,
                    provider_attempt=attempts,
                    super_proxy=super_proxy,
                )
                errors.append(
                    f"{'super' if super_proxy else 'standard'} "
                    f"attempt {profile_attempt}: {type(exc).__name__}: "
                    f"{str(exc)[:300]}"
                )
                # The next Scrape.do profile shares the same subscription.
                raise
            except ScrapeDoTransientError as exc:
                last_failure = exc
                _notify_provider_failure(
                    failure_observer,
                    "scrape_do_super" if super_proxy else "scrape_do",
                    exc,
                    profile_attempt=profile_attempt,
                    provider_attempt=attempts,
                    super_proxy=super_proxy,
                )
                errors.append(
                    f"{'super' if super_proxy else 'standard'} "
                    f"attempt {profile_attempt}: {type(exc).__name__}: "
                    f"{str(exc)[:300]}"
                )
                if profile_attempt == 1 and not adaptive:
                    provider_delay = min(
                        60.0,
                        max(0.0, float(exc.retry_after_seconds or 0.0)),
                    )
                    backoff_delay = 2.0 * random.uniform(0.85, 1.15)
                    time.sleep(max(provider_delay, backoff_delay))
                    continue
                break
            except ScrapeDoRequestError as exc:
                last_failure = exc
                _notify_provider_failure(
                    failure_observer,
                    "scrape_do_super" if super_proxy else "scrape_do",
                    exc,
                    profile_attempt=profile_attempt,
                    provider_attempt=attempts,
                    super_proxy=super_proxy,
                )
                errors.append(
                    f"{'super' if super_proxy else 'standard'} "
                    f"attempt {profile_attempt}: {type(exc).__name__}: "
                    f"{str(exc)[:300]}"
                )
                # A target 403 may improve with the residential Super profile.
                if exc.status_code == 403 and (adaptive or not super_proxy):
                    break
                raise
            except Exception as exc:  # noqa: BLE001 - normalize provider failures
                last_failure = exc
                _notify_provider_failure(
                    failure_observer,
                    "scrape_do_super" if super_proxy else "scrape_do",
                    exc,
                    profile_attempt=profile_attempt,
                    provider_attempt=attempts,
                    super_proxy=super_proxy,
                )
                errors.append(
                    f"{'super' if super_proxy else 'standard'} "
                    f"attempt {profile_attempt}: {type(exc).__name__}: "
                    f"{str(exc)[:300]}"
                )
                break
        if scraped is not None:
            break
    if scraped is None:
        if last_failure is not None:
            raise last_failure
        raise RuntimeError(f"Scrape.do failed: {'; '.join(errors)}")
    return _scrape_do_result(
        scraped,
        formats=formats,
        attempts=attempts,
        profile_attempt=profile_attempt,
        reason=reason,
        render=render,
    )


def _scrape_do_result(
    scraped: ScrapeDoScrape,
    *,
    formats: list[Any] | None,
    attempts: int,
    profile_attempt: int,
    reason: str,
    render: bool = True,
) -> FirecrawlScrape:
    html = scraped.html
    requested = formats or ["html", "markdown"]
    markdown = _html_to_markdown(html) if "markdown" in requested else ""
    return FirecrawlScrape(
        html=html,
        markdown=markdown,
        screenshot=scraped.screenshot,
        metadata={
            "backend": ("scrape_do_super" if scraped.super_proxy else "scrape_do"),
            "creditsUsed": 0,
            "scrapeDoCreditsUsed": scraped.request_cost,
            "labAcquisitionId": scraped.acquisition_id,
            "scrapeDoRemainingCredits": scraped.credits_remaining,
            "scrapeDoAttempts": attempts,
            "scrapeDoProfileAttempt": profile_attempt,
            "scrapeDoSuperProxy": scraped.super_proxy,
            "scrapeDoRender": render,
            "usageCallId": getattr(scraped, "usage_call_id", None),
            "providerPolicy": _PROVIDER_POLICY,
            "providerChainReason": reason[:500],
            "firecrawlFallbackReason": reason[:500],
        },
        status_code=scraped.status_code,
        final_url=scraped.final_url,
    )


def _scrape_once(
    source: Source,
    *,
    api_key: str,
    formats: list[Any] | None = None,
    only_main_content: bool = True,
    headers: dict[str, str] | None = None,
    max_age_ms: int | None = None,
    wait_ms: int | None = None,
    timeout_ms: int | None = None,
) -> FirecrawlScrape:
    effective_timeout_ms = firecrawl_timeout_ms() if timeout_ms is None else timeout_ms
    payload: dict[str, Any] = {
        "url": source.url,
        "formats": formats or ["html", "markdown"],
        "onlyMainContent": only_main_content,
        "maxAge": firecrawl_max_age_ms() if max_age_ms is None else max_age_ms,
        "waitFor": firecrawl_wait_ms() if wait_ms is None else wait_ms,
        "timeout": effective_timeout_ms,
    }
    if headers:
        payload["headers"] = headers
    request = urllib.request.Request(
        FIRECRAWL_SCRAPE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=(effective_timeout_ms / 1000) + 30
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Firecrawl HTTP {exc.code}: {detail[:500]}") from exc

    if not body.get("success"):
        raise RuntimeError(f"Firecrawl scrape failed: {body}")

    data = body.get("data") or {}
    html = data.get("html") or data.get("rawHtml") or ""
    if not html and any(
        fmt in ("html", "rawHtml") for fmt in (formats or ["html", "markdown"])
    ):
        raise RuntimeError("Firecrawl response did not include html")
    metadata = dict(data.get("metadata") or {})
    metadata.setdefault("backend", "firecrawl")
    if body.get("creditsUsed") is not None and metadata.get("creditsUsed") is None:
        metadata["creditsUsed"] = body.get("creditsUsed")
    return FirecrawlScrape(
        html=html,
        markdown=data.get("markdown") or "",
        screenshot=data.get("screenshot"),
        metadata=metadata,
        status_code=int(metadata.get("statusCode") or 200),
        final_url=str(
            metadata.get("sourceURL") or metadata.get("ogUrl") or source.fetch_url
        ),
    )


def _scrape_via_firecrawl(
    source: Source,
    *,
    formats: list[Any] | None = None,
    only_main_content: bool = True,
    headers: dict[str, str] | None = None,
    max_age_ms: int | None = None,
    wait_ms: int | None = None,
    timeout_ms: int | None = None,
) -> FirecrawlScrape:
    errors: list[str] = []
    attempt_limit = max(8, len(parse_firecrawl_api_keys()) or 1)
    for _ in range(attempt_limit):
        lease = acquire_firecrawl_key()
        started = time.monotonic()
        try:
            scraped = _scrape_once(
                source,
                api_key=lease.key.key,
                formats=formats,
                only_main_content=only_main_content,
                headers=headers,
                max_age_ms=max_age_ms,
                wait_ms=wait_ms,
                timeout_ms=timeout_ms,
            )
        except Exception as exc:
            status_match = re.search(r"Firecrawl HTTP (\d{3})", str(exc))
            provider_usage.record_call(
                "firecrawl",
                url=source.url,
                outcome="failed",
                started=started,
                status=int(status_match.group(1)) if status_match else None,
                reason=(
                    f"http_{status_match.group(1)}"
                    if status_match
                    else "credit_limit"
                    if is_firecrawl_credit_error(exc)
                    else type(exc).__name__
                ),
                profile="scrape",
                render=True,
                account=lease.key.label,
                source_id=source.id,
            )
            if is_firecrawl_credit_error(exc):
                mark_firecrawl_key_exhausted(lease.key.label, reason=str(exc))
                errors.append(f"{lease.key.label}: {exc}")
                continue
            raise

        credits = scraped.metadata.get("creditsUsed")
        try:
            credits_int = int(credits) if credits is not None else 1
        except (TypeError, ValueError):
            credits_int = 1
        rotation = record_firecrawl_credits(lease.key.label, credits_int)
        call_id = provider_usage.record_call(
            "firecrawl",
            url=source.url,
            started=started,
            status=getattr(scraped, "status_code", None),
            profile="scrape",
            render=True,
            content=getattr(scraped, "html", None),
            cost=credits_int,
            cost_basis="reported" if credits is not None else "estimated",
            account=lease.key.label,
            source_id=source.id,
        )
        metadata = dict(scraped.metadata)
        metadata["usageCallId"] = call_id
        metadata["firecrawl_key_label"] = lease.key.label
        metadata["firecrawl_key_fingerprint"] = lease.key.fingerprint
        metadata["firecrawl_key_rotation"] = rotation
        metadata["providerPolicy"] = _PROVIDER_POLICY
        return FirecrawlScrape(
            html=scraped.html,
            markdown=scraped.markdown,
            screenshot=scraped.screenshot,
            metadata=metadata,
            status_code=scraped.status_code,
            final_url=scraped.final_url,
        )

    detail = "; ".join(errors) if errors else "no available keys"
    raise RuntimeError(f"Firecrawl scrape failed after key rotation attempts: {detail}")


def _accepted_provider_content(
    content: str,
    source: Source,
    *,
    accept_html: Callable[[str], bool] | None,
) -> bool:
    if is_session_blocked(None, content):
        return False
    if source.site == "hsguru" and _is_hsguru_paywall(content):
        return False
    if accept_html is None:
        normalized = content.strip()
        content_size = len(normalized.encode("utf-8", errors="replace"))
        minimum_size = (
            _HTML_SITE_MIN_BYTES if source.site in {"hsreplay", "hsguru"} else 80
        )
        if content_size < minimum_size:
            return False
        lowered = normalized.casefold()
        if source.site == "hsreplay":
            if any(
                marker in lowered
                for marker in (
                    "log in to hsreplay",
                    "sign in to hsreplay",
                    "sign in to continue",
                    "accounts/login",
                    "login required",
                    "premium required",
                )
            ):
                return False
            return any(
                marker in lowered
                for marker in (
                    "userdata",
                    "__next_data__",
                    "react-root",
                    "battlegrounds",
                    "arena",
                    "card-list",
                    "deck-list",
                )
            )
        if source.site == "hsguru":
            if source.category in {
                "meta_matrix_slice",
                "meta_current",
                "meta_patch_discovery",
            }:
                from .hsguru_meta_matrix import parse_meta_table

                try:
                    parsed = parse_meta_table(content)
                except Exception:  # noqa: BLE001 - reject unusable provider HTML
                    return False
                return bool(parsed.rows) or source.category == "meta_matrix_slice"
            return any(
                marker in lowered
                for marker in (
                    "deck_stats_viewport",
                    "decklist-info",
                    "archetype",
                    "matchup",
                    "streamer",
                    "winrate",
                    "popularity",
                    "__next_data__",
                )
            )
        return True
    try:
        return bool(accept_html(content))
    except Exception:  # noqa: BLE001 - validation must fail closed
        return False


def _final_url_matches_source(source: Source, final_url: str) -> bool:
    source_host = (urlsplit(source.fetch_url).hostname or "").casefold()
    final_host = (urlsplit(final_url).hostname or "").casefold()
    if not source_host or not final_host:
        return False
    source_host = source_host.removeprefix("www.")
    final_host = final_host.removeprefix("www.")
    return (
        source_host == final_host
        or source_host.endswith(f".{final_host}")
        or final_host.endswith(f".{source_host}")
    )


def _accepted_provider_result(
    scraped: FirecrawlScrape,
    source: Source,
    *,
    formats: list[Any] | None,
    accept_html: Callable[[str], bool] | None,
    accept_result: ProviderResultValidator | None,
) -> bool:
    if not 200 <= scraped.status_code <= 299:
        return False
    if not _final_url_matches_source(source, scraped.final_url):
        return False
    content = scraped.html or scraped.markdown
    if content and is_session_blocked(scraped.status_code, content):
        return False
    screenshot_requested, _full_page = _screenshot_options(formats)
    if screenshot_requested:
        content_accepted = bool(
            scraped.screenshot
            and _decode_inline_screenshot(scraped.screenshot) is not None
        )
    elif accept_html is not None:
        content_accepted = bool(content) and _accepted_provider_content(
            content,
            source,
            accept_html=accept_html,
        )
    else:
        content_accepted = bool(content) and _accepted_provider_content(
            content,
            source,
            accept_html=None,
        )
    if not content_accepted:
        return False
    if accept_result is None:
        return True
    try:
        return bool(accept_result(scraped))
    except Exception:  # noqa: BLE001 - source validator must fail closed
        return False


def _require_accepted_provider_result(
    provider: str,
    scraped: FirecrawlScrape,
    source: Source,
    *,
    formats: list[Any] | None,
    accept_html: Callable[[str], bool] | None,
    accept_result: ProviderResultValidator | None,
    attempt_observer: ProviderAttemptObserver | None = None,
) -> FirecrawlScrape:
    scraped = _normalize_requested_screenshot(scraped, formats)
    accepted = _accepted_provider_result(
        scraped,
        source,
        formats=formats,
        accept_html=accept_html,
        accept_result=accept_result,
    )
    if not accepted:
        provider_usage.mark_rejected(
            scraped.metadata.get("usageCallId")
            if isinstance(scraped.metadata, dict)
            else None
        )
    if attempt_observer is not None:
        try:
            attempt_observer(scraped, accepted)
        except Exception:  # noqa: BLE001,S110 - telemetry must not break acquisition
            pass
    if not accepted:
        raise RuntimeError(f"{provider} response failed content validation")
    return scraped


def _scrape_sync(
    source: Source,
    *,
    formats: list[Any] | None = None,
    only_main_content: bool = True,
    headers: dict[str, str] | None = None,
    max_age_ms: int | None = None,
    wait_ms: int | None = None,
    timeout_ms: int | None = None,
    skip_providers: frozenset[str] | set[str] | None = None,
    brightdata_accept_html: Callable[[str], bool] | None = None,
    brightdata_render: bool = True,
    brightdata_anonymous_fallback: bool = False,
    accept_result: ProviderResultValidator | None = None,
    attempt_observer: ProviderAttemptObserver | None = None,
    failure_observer: ProviderFailureObserver | None = None,
) -> FirecrawlScrape:
    """Try cheap HSGuru HTML, Decodo, then paid escalation and reserves."""
    with provider_usage.usage_source(source.id):
        return _scrape_cascade(
            source,
            formats=formats,
            only_main_content=only_main_content,
            headers=headers,
            max_age_ms=max_age_ms,
            wait_ms=wait_ms,
            timeout_ms=timeout_ms,
            skip_providers=skip_providers,
            brightdata_accept_html=brightdata_accept_html,
            brightdata_render=brightdata_render,
            brightdata_anonymous_fallback=brightdata_anonymous_fallback,
            accept_result=accept_result,
            attempt_observer=attempt_observer,
            failure_observer=failure_observer,
        )


def _scrape_cascade(
    source: Source,
    *,
    formats: list[Any] | None,
    only_main_content: bool,
    headers: dict[str, str] | None,
    max_age_ms: int | None,
    wait_ms: int | None,
    timeout_ms: int | None,
    skip_providers: frozenset[str] | set[str] | None,
    brightdata_accept_html: Callable[[str], bool] | None,
    brightdata_render: bool,
    brightdata_anonymous_fallback: bool,
    accept_result: ProviderResultValidator | None,
    attempt_observer: ProviderAttemptObserver | None,
    failure_observer: ProviderFailureObserver | None,
) -> FirecrawlScrape:
    errors: list[str] = []
    skip = frozenset(skip_providers or ())

    screenshot_requested, _ = _screenshot_options(formats)
    decodo_enabled = bool(
        source.site == "hsguru"
        and not screenshot_requested
        and "decodo" not in skip
        and decodo_api_key()
    )
    decodo_tried = False

    def try_decodo() -> FirecrawlScrape | None:
        nonlocal decodo_tried
        if not decodo_enabled or decodo_tried:
            return None
        decodo_tried = True
        for render, super_proxy in ((False, False), (True, True)):
            try:
                result = decodo_scrape_url_sync(
                    source.url,
                    render=render,
                    super_proxy=super_proxy,
                    # Public reserve never receives the authenticated session.
                    # Source validators still reject missing premium data.
                    headers=None,
                    timeout_ms=timeout_ms,
                )
                candidate = FirecrawlScrape(
                    html=result.html,
                    markdown=_html_to_markdown(result.html)
                    if "markdown" in (formats or ["html", "markdown"])
                    else "",
                    screenshot=None,
                    metadata={
                        "backend": "decodo",
                        "providerPolicy": _PROVIDER_POLICY,
                        "decodoRender": render,
                        "decodoSuperProxy": super_proxy,
                        "decodoRequests": 1,
                        "decodoCostKnown": False,
                        "usageCallId": getattr(result, "usage_call_id", None),
                    },
                    status_code=result.status_code,
                    final_url=result.final_url,
                )
                return _require_accepted_provider_result(
                    "Decodo",
                    candidate,
                    source,
                    formats=formats,
                    accept_html=brightdata_accept_html,
                    accept_result=accept_result,
                    attempt_observer=attempt_observer,
                )
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate reserve provider
                _notify_provider_failure(
                    failure_observer,
                    "decodo",
                    exc,
                    super_proxy=super_proxy,
                )
                errors.append(f"decodo: {type(exc).__name__}: {str(exc)[:300]}")
                if getattr(exc, "terminal", False):
                    break
        return None

    if "scrape_do" not in skip and scrape_do_token():
        phases = (
            (_HSGURU_SCRAPE_DO_PROFILES[:1], _HSGURU_SCRAPE_DO_PROFILES[1:])
            if decodo_enabled
            else (None,)
        )
        for phase, profiles in enumerate(phases):
            try:
                return _require_accepted_provider_result(
                    "Scrape.do",
                    _scrape_via_scrape_do(
                        source,
                        formats=formats,
                        headers=headers,
                        wait_ms=wait_ms,
                        timeout_ms=timeout_ms,
                        reason="primary",
                        attempt_observer=attempt_observer,
                        failure_observer=failure_observer,
                        accept_html=brightdata_accept_html,
                        accept_result=accept_result,
                        profiles=profiles,
                        attempt_offset=phase,
                    ),
                    source,
                    formats=formats,
                    accept_html=brightdata_accept_html,
                    accept_result=accept_result,
                    attempt_observer=attempt_observer,
                )
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - provider boundary
                errors.append(f"scrape_do: {type(exc).__name__}: {str(exc)[:300]}")
                reserve = try_decodo()
                if reserve is not None:
                    return reserve
                if isinstance(exc, ScrapeDoRequestError) and (
                    isinstance(exc, ScrapeDoAccountError)
                    or not isinstance(exc, ScrapeDoTransientError)
                    and exc.status_code != 403
                ):
                    break

    reserve = try_decodo()
    if reserve is not None:
        return reserve

    if "firecrawl" not in skip:
        try:
            return _require_accepted_provider_result(
                "Firecrawl",
                _scrape_via_firecrawl(
                    source,
                    formats=formats,
                    only_main_content=only_main_content,
                    headers=headers,
                    max_age_ms=max_age_ms,
                    wait_ms=wait_ms,
                    timeout_ms=timeout_ms,
                ),
                source,
                formats=formats,
                accept_html=brightdata_accept_html,
                accept_result=accept_result,
                attempt_observer=attempt_observer,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            _notify_provider_failure(failure_observer, "firecrawl", exc)
            errors.append(f"firecrawl: {type(exc).__name__}: {str(exc)[:300]}")

    brightdata_formats_allowed = all(
        isinstance(item, str) and item.lower() in {"html", "rawhtml", "markdown"}
        for item in (formats or ["html", "markdown"])
    )
    brightdata_available = False
    if (
        "brightdata" not in skip
        and (headers is None or brightdata_anonymous_fallback)
        and brightdata_formats_allowed
    ):
        try:
            brightdata_available = brightdata_configured_for_source(source.id)
        except Exception as exc:  # noqa: BLE001 - isolate provider configuration
            _notify_provider_failure(
                failure_observer,
                "brightdata_web_unlocker",
                exc,
            )
            errors.append(
                f"brightdata: {type(exc).__name__}: configuration unavailable"
            )
    if brightdata_available:
        try:
            scraped = brightdata_scrape_url_sync(
                source.url,
                source_id=source.id,
                timeout_ms=timeout_ms,
                accept_html=lambda html: _accepted_provider_content(
                    html,
                    source,
                    accept_html=brightdata_accept_html,
                ),
                render=brightdata_render,
            )
            requested = formats or ["html", "markdown"]
            result = FirecrawlScrape(
                html=scraped.html,
                markdown=(
                    _html_to_markdown(scraped.html)
                    if any(
                        isinstance(item, str) and item.lower() == "markdown"
                        for item in requested
                    )
                    else ""
                ),
                screenshot=None,
                metadata={
                    "backend": "brightdata_web_unlocker",
                    "creditsUsed": 0,
                    "brightDataRequests": 1,
                    "brightDataBillableRequests": scraped.billable_requests,
                    "brightDataRequestId": scraped.request_id,
                    "brightDataRendered": scraped.rendered,
                    "brightDataBudgetRemaining": scraped.budget_remaining,
                    "providerPolicy": _PROVIDER_POLICY,
                },
                status_code=scraped.status_code,
                final_url=scraped.final_url,
            )
            return _require_accepted_provider_result(
                "Bright Data",
                result,
                source,
                formats=formats,
                accept_html=brightdata_accept_html,
                accept_result=accept_result,
                attempt_observer=attempt_observer,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            _notify_provider_failure(
                failure_observer,
                "brightdata_web_unlocker",
                exc,
            )
            errors.append(f"brightdata: {type(exc).__name__}: {str(exc)[:300]}")

    scrapfly_available = False
    if "scrapfly" not in skip:
        try:
            scrapfly_available = scrapfly_configured()
        except Exception as exc:  # noqa: BLE001 - isolate provider configuration
            _notify_provider_failure(failure_observer, "scrapfly", exc)
            errors.append(f"scrapfly: {type(exc).__name__}: configuration unavailable")
    if scrapfly_available:
        try:
            return _require_accepted_provider_result(
                "Scrapfly",
                _scrape_via_scrapfly(
                    source,
                    formats=formats,
                    headers=headers,
                    wait_ms=wait_ms,
                    timeout_ms=timeout_ms,
                    reason="; ".join(errors) or "upstream providers failed",
                ),
                source,
                formats=formats,
                accept_html=brightdata_accept_html,
                accept_result=accept_result,
                attempt_observer=attempt_observer,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            _notify_provider_failure(failure_observer, "scrapfly", exc)
            errors.append(f"scrapfly: {type(exc).__name__}: {str(exc)[:300]}")

    detail = "; ".join(errors) if errors else "no scrape providers configured"
    raise RuntimeError(f"All scrape providers failed: {detail}")


async def scrape_source(
    source: Source,
    *,
    accept_result: ProviderResultValidator | None = None,
) -> FirecrawlScrape:
    options: dict[str, Any] = {}
    if accept_result is not None:
        options["accept_result"] = accept_result
    if source.site == "hsguru" and source.category == "matchups":
        return await asyncio.to_thread(
            _scrape_sync,
            source,
            timeout_ms=firecrawl_hsguru_matchups_timeout_ms(),
            **options,
        )
    return await asyncio.to_thread(_scrape_sync, source, **options)


async def scrape_source_with_options(
    source: Source,
    *,
    formats: list[Any] | None = None,
    only_main_content: bool = True,
    headers: dict[str, str] | None = None,
    max_age_ms: int | None = None,
    wait_ms: int | None = None,
    timeout_ms: int | None = None,
    skip_providers: frozenset[str] | set[str] | None = None,
    brightdata_accept_html: Callable[[str], bool] | None = None,
    brightdata_render: bool = True,
    brightdata_anonymous_fallback: bool = False,
    accept_result: ProviderResultValidator | None = None,
    attempt_observer: ProviderAttemptObserver | None = None,
    failure_observer: ProviderFailureObserver | None = None,
) -> FirecrawlScrape:
    return await asyncio.to_thread(
        _scrape_sync,
        source,
        formats=formats,
        only_main_content=only_main_content,
        headers=headers,
        max_age_ms=max_age_ms,
        wait_ms=wait_ms,
        timeout_ms=timeout_ms,
        skip_providers=skip_providers,
        brightdata_accept_html=brightdata_accept_html,
        brightdata_render=brightdata_render,
        brightdata_anonymous_fallback=brightdata_anonymous_fallback,
        accept_result=accept_result,
        attempt_observer=attempt_observer,
        failure_observer=failure_observer,
    )
