from __future__ import annotations

import asyncio
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup, NavigableString

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
    ScrapeDoRequestError,
    ScrapeDoTransientError,
    scrape_url_sync,
)
from .scrapers.http_resilience import is_session_blocked
from .scrapfly_backend import scrape_url_sync as scrapfly_scrape_url_sync
from .scrapfly_backend import scrapfly_configured
from .sources import Source

FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"


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
    def request_credits(self) -> int:
        return (
            self.scrape_do_credits_used
            or self.scrapfly_credits_used
            or self.firecrawl_credits_used
        )


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
            "providerPolicy": "scrape_do_firecrawl_scrapfly",
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
) -> FirecrawlScrape:
    if not scrape_do_token():
        raise RuntimeError(reason or "Scrape.do is not configured")
    screenshot, full_screenshot = _screenshot_options(formats)
    profiles = (True,) if source.site == "hsguru" else (False, True)
    errors: list[str] = []
    scraped = None
    attempts = 0
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
                    errors.append(
                        f"{'super' if super_proxy else 'standard'} "
                        f"attempt {profile_attempt}: blocked_or_challenge_content"
                    )
                    scraped = None
                    break
                break
            except ScrapeDoAccountError as exc:
                errors.append(
                    f"{'super' if super_proxy else 'standard'} "
                    f"attempt {profile_attempt}: {type(exc).__name__}: "
                    f"{str(exc)[:300]}"
                )
                # The next Scrape.do profile shares the same subscription.
                raise
            except ScrapeDoTransientError as exc:
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
                errors.append(
                    f"{'super' if super_proxy else 'standard'} "
                    f"attempt {profile_attempt}: {type(exc).__name__}: "
                    f"{str(exc)[:300]}"
                )
                break
        if scraped is not None:
            break
    if scraped is None:
        raise RuntimeError(f"Scrape.do failed: {'; '.join(errors)}")
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
            "providerPolicy": "scrape_do_firecrawl_scrapfly",
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
    payload = {
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
    html = data.get("html") or ""
    if not html and any(fmt == "html" for fmt in (formats or ["html", "markdown"])):
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
            metadata.get("ogUrl") or metadata.get("sourceURL") or source.fetch_url
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
        metadata["providerPolicy"] = "scrape_do_firecrawl_scrapfly"
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
) -> FirecrawlScrape:
    """Fetch through the shared Scrape.do → Firecrawl → Scrapfly policy."""
    errors: list[str] = []
    skip = frozenset(skip_providers or ())

    if "scrape_do" not in skip and scrape_do_token():
        try:
            return _scrape_via_scrape_do(
                source,
                formats=formats,
                headers=headers,
                wait_ms=wait_ms,
                timeout_ms=timeout_ms,
                reason="primary",
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            errors.append(f"scrape_do: {type(exc).__name__}: {str(exc)[:300]}")

    if "firecrawl" not in skip:
        try:
            return _scrape_via_firecrawl(
                source,
                formats=formats,
                only_main_content=only_main_content,
                headers=headers,
                max_age_ms=max_age_ms,
                wait_ms=wait_ms,
                timeout_ms=timeout_ms,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            errors.append(f"firecrawl: {type(exc).__name__}: {str(exc)[:300]}")

    if "scrapfly" not in skip and scrapfly_configured():
        try:
            return _scrape_via_scrapfly(
                source,
                formats=formats,
                headers=headers,
                wait_ms=wait_ms,
                timeout_ms=timeout_ms,
                reason="; ".join(errors) or "upstream providers failed",
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary
            errors.append(f"scrapfly: {type(exc).__name__}: {str(exc)[:300]}")

    detail = "; ".join(errors) if errors else "no scrape providers configured"
    raise RuntimeError(f"All scrape providers failed: {detail}")


async def scrape_source(source: Source) -> FirecrawlScrape:
    if source.site == "hsguru" and source.category == "matchups":
        return await asyncio.to_thread(
            _scrape_sync,
            source,
            timeout_ms=firecrawl_hsguru_matchups_timeout_ms(),
        )
    return await asyncio.to_thread(_scrape_sync, source)


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
    )
