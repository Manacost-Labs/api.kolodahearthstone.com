from __future__ import annotations

import asyncio
import base64
import binascii
import json
import random
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString

from .brightdata_backend import (
    BrightDataPolicyError,
    BrightDataRequestError,
    _assert_public_https_target,
    brightdata_configured_for_source,
)
from .brightdata_backend import scrape_url_sync as brightdata_scrape_url_sync
from .config import (
    firecrawl_hsguru_matchups_timeout_ms,
    firecrawl_max_age_ms,
    firecrawl_timeout_ms,
    firecrawl_wait_ms,
    scrape_do_token,
)
from .firecrawl_keys import (
    acquire_firecrawl_key,
    is_firecrawl_credit_error,
    mark_firecrawl_key_exhausted,
    parse_firecrawl_api_keys,
    record_firecrawl_credits,
)
from .scrape_do_backend import (
    ScrapeDoAccountError,
    ScrapeDoContentError,
    ScrapeDoRequestError,
    ScrapeDoScrape,
    ScrapeDoTransientError,
    scrape_url_sync,
)
from .scrapers.http_resilience import is_session_blocked
from .scrapfly_backend import scrape_url_sync as scrapfly_scrape_url_sync
from .scrapfly_backend import scrapfly_configured
from .sources import Source

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
_PROVIDER_POLICY = "scrape_do_firecrawl_brightdata_scrapfly"
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
    if not separator:
        return None
    declared_mime = {
        "data:image/png;base64": "image/png",
        "data:image/jpeg;base64": "image/jpeg",
        "data:image/jpg;base64": "image/jpeg",
        "data:image/webp;base64": "image/webp",
    }.get(header.casefold())
    if declared_mime is None or not encoded:
        return None
    maximum_encoded_size = ((_SCREENSHOT_MAX_BYTES + 2) // 3) * 4
    if len(encoded) > maximum_encoded_size:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    detected_mime = _image_mime(raw)
    if detected_mime is None or detected_mime != declared_mime:
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
            "providerPolicy": _PROVIDER_POLICY,
            "firecrawlFallbackReason": reason[:500],
        },
        status_code=scraped.status_code,
        final_url=scraped.final_url,
    )


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
) -> FirecrawlScrape:
    if not scrape_do_token():
        raise RuntimeError(reason or "Scrape.do is not configured")
    screenshot, full_screenshot = _screenshot_options(formats)
    profiles = (True,) if source.site == "hsguru" else (False, True)
    errors: list[str] = []
    scraped = None
    attempts = 0
    last_failure: Exception | None = None
    for super_proxy in profiles:
        for profile_attempt in range(1, 3):
            attempts += 1
            try:
                scraped = scrape_url_sync(
                    source.url,
                    render=True,
                    super_proxy=super_proxy,
                    headers=headers,
                    wait_ms=wait_ms,
                    timeout_ms=timeout_ms,
                    screenshot=screenshot,
                    full_screenshot=full_screenshot,
                )
                if is_session_blocked(scraped.status_code, scraped.html):
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
                if profile_attempt == 1:
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
                if exc.status_code == 403 and not super_proxy:
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
    )


def _scrape_do_result(
    scraped: ScrapeDoScrape,
    *,
    formats: list[Any] | None,
    attempts: int,
    profile_attempt: int,
    reason: str,
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
            "scrapeDoRemainingCredits": scraped.credits_remaining,
            "scrapeDoAttempts": attempts,
            "scrapeDoProfileAttempt": profile_attempt,
            "scrapeDoSuperProxy": scraped.super_proxy,
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
        metadata = dict(scraped.metadata)
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
    accept_result: ProviderResultValidator | None = None,
    attempt_observer: ProviderAttemptObserver | None = None,
    failure_observer: ProviderFailureObserver | None = None,
) -> FirecrawlScrape:
    """Fetch through Scrape.do → Firecrawl → Bright Data → Scrapfly."""
    errors: list[str] = []
    skip = frozenset(skip_providers or ())

    if "scrape_do" not in skip and scrape_do_token():
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
                ),
                source,
                formats=formats,
                accept_html=brightdata_accept_html,
                accept_result=accept_result,
                attempt_observer=attempt_observer,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            errors.append(f"scrape_do: {type(exc).__name__}: {str(exc)[:300]}")

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
    if "brightdata" not in skip and headers is None and brightdata_formats_allowed:
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
        accept_result=accept_result,
        attempt_observer=attempt_observer,
        failure_observer=failure_observer,
    )
