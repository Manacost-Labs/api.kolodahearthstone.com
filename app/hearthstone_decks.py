from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .firecrawl_backend import FirecrawlScrape, scrape_source_with_options
from .proxy_errors import ProxyPaymentRequiredError, proxy_tunnel_error
from .scrapers.http_resilience import is_session_blocked
from .scrapers.proxy import httpx_client_kwargs
from .source_contracts import contract_quality_ok
from .source_validators import validate_structured
from .sources import Source
from .storage import load_dataset

logger = logging.getLogger(__name__)

DECK_CODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9+/=])AA[A-Za-z0-9+/=]{20,}(?![A-Za-z0-9+/=])"
)

_LIST_PAGE_LIMIT = 20
_MAX_CLOUD_DETAIL_FETCHES = 8
_ALLOWED_HOSTS = frozenset({"hearthstone-decks.net", "www.hearthstone-decks.net"})
_LIST_PAGES = (
    ("Standard", "https://hearthstone-decks.net/standard-decks/"),
    ("Wild", "https://hearthstone-decks.net/wild-decks/"),
)
_HTML_HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/apng,*/*;q=0.8"
    ),
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Pattern to parse title of the deck post on hearthstone-decks.net
# Format: "No Hand Hunter #2 Legend – Unknown (Score: 15-4)"
# Or: "Companion Hunter #138 Legend – unikoru11_uni"
DECK_TITLE_PATTERN = re.compile(
    r"^(?P<archetype>.+?)\s+(?P<rank>#\d+\s+\w+)\s*[–-]\s*(?P<player>.+?)(?:\s*\(\s*Score:\s*(?P<score>\d+-\d+)\s*\))?$",
    re.UNICODE,
)


def extract_deck_code_from_html(html: str) -> str:
    """Extract a Hearthstone deck code from common WordPress post locations."""
    soup = BeautifulSoup(html, "lxml")
    candidates: list[str] = []

    for tag in soup.find_all(["input", "textarea", "button"]):
        for attr in ("value", "data-clipboard-text", "data-deck-code", "aria-label"):
            value = tag.get(attr)
            if value:
                candidates.append(str(value))
        text = tag.get_text(" ", strip=True)
        if text:
            candidates.append(text)

    for script in soup.find_all("script"):
        script_text = script.string or script.get_text(" ", strip=True)
        if script_text:
            candidates.append(script_text)

    candidates.append(soup.get_text(" ", strip=True))

    for candidate in candidates:
        match = DECK_CODE_PATTERN.search(candidate)
        if match:
            return match.group(0)
    return ""


def _allowed_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").rstrip(".").lower()
    return parsed.scheme == "https" and host in _ALLOWED_HOSTS


def _canonical_url(value: str) -> str:
    return value.rstrip("/") + "/"


def _same_target_path(expected_url: str, actual_url: str) -> bool:
    if not _allowed_url(actual_url):
        return False
    expected = urlparse(expected_url)
    actual = urlparse(actual_url)
    return actual.path.rstrip("/") == expected.path.rstrip("/")


def parse_decks_list_html(
    html: str,
    *,
    page_url: str,
    format_name: str,
    limit: int = _LIST_PAGE_LIMIT,
) -> list[dict[str, Any]]:
    """Parse one public Standard/Wild listing without performing network I/O."""

    soup = BeautifulSoup(html, "lxml")
    decks_info: list[dict[str, Any]] = []
    for art in soup.find_all("article"):
        h3 = art.find(class_="elementor-post__title")
        a_tag = h3.find("a") if h3 else None
        if not a_tag:
            continue

        raw_title = a_tag.get_text(strip=True)
        href = urljoin(page_url, str(a_tag.get("href") or ""))
        if not raw_title or not _allowed_url(href):
            continue

        date_span = art.find(class_="elementor-post-date")
        date_str = date_span.get_text(strip=True) if date_span else ""

        archetype = raw_title
        rank = ""
        player = ""
        score: str | None = None

        match = DECK_TITLE_PATTERN.search(raw_title)
        if match:
            gd = match.groupdict()
            archetype = gd.get("archetype") or raw_title
            rank = gd.get("rank") or ""
            player = gd.get("player") or ""
            score = gd.get("score")

        decks_info.append(
            {
                "title": raw_title,
                "url": href,
                "date": date_str,
                "format": format_name,
                "archetype": archetype,
                "rank": rank,
                "player": player,
                "score": score,
            }
        )
        if len(decks_info) >= limit:
            break

    return decks_info


def _list_html_is_usable(
    html: str,
    *,
    page_url: str,
    format_name: str,
) -> bool:
    if not html or is_session_blocked(200, html):
        return False
    try:
        rows = parse_decks_list_html(
            html,
            page_url=page_url,
            format_name=format_name,
        )
    except Exception:  # noqa: BLE001 - provider candidates fail closed
        return False
    if len(rows) != _LIST_PAGE_LIMIT:
        return False
    if len({_canonical_url(str(row["url"])) for row in rows}) != _LIST_PAGE_LIMIT:
        return False
    identified = sum(bool(row.get("rank") and row.get("player")) for row in rows)
    return identified >= int(_LIST_PAGE_LIMIT * 0.8)


async def _fetch_residential_html(source_id: str, url: str) -> str:
    """Fetch only through the configured residential proxy, never direct."""

    options = httpx_client_kwargs(source_id, page_url=url)
    if not options.get("proxy"):
        raise RuntimeError("residential proxy route unavailable")
    try:
        async with httpx.AsyncClient(**options) as client:
            response = await client.get(url, headers=_HTML_HEADERS)
            response.raise_for_status()
            if not _same_target_path(url, str(response.url)):
                raise RuntimeError("Hearthstone-Decks redirected away from target path")
            return response.text
    except Exception as exc:
        typed_proxy_error = proxy_tunnel_error(exc, proxy_used=True)
        if typed_proxy_error is not None:
            if typed_proxy_error is exc:
                raise
            raise typed_proxy_error from exc
        raise


async def _fetch_cloud_html(
    source: Source,
    url: str,
    *,
    accept_html: Callable[[str], bool],
) -> tuple[str, str]:
    """Fetch public HTML via the shared Scrape.do-first provider chain."""

    candidate_source = replace(source, url=url)

    def accept_result(scraped: FirecrawlScrape) -> bool:
        html = scraped.html or scraped.markdown
        return (
            scraped.status_code == 200
            and _same_target_path(url, scraped.final_url)
            and accept_html(html)
        )

    # Deliberately pass no headers or cookies. The page is public, Scrape.do
    # stays first, and Bright Data remains eligible as the configured third tier.
    scraped = await scrape_source_with_options(
        candidate_source,
        formats=["html"],
        only_main_content=False,
        max_age_ms=0,
        brightdata_accept_html=accept_html,
        accept_result=accept_result,
    )
    html = scraped.html or scraped.markdown
    if not accept_result(scraped):
        raise RuntimeError("Hearthstone-Decks cloud response failed validation")
    return html, scraped.backend


def _previous_decks(source_id: str) -> list[dict[str, Any]]:
    try:
        dataset = load_dataset(source_id) or {}
    except Exception as exc:  # noqa: BLE001 - cache failure must not block refresh
        logger.warning(
            "Could not load previous decks for %s: %s",
            source_id,
            type(exc).__name__,
        )
        return []
    data = dataset.get("data") or {}
    candidates = (
        data.get("structured"),
        data.get("hsreplay_extracted"),
        data,
    )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        rows = candidate.get("decks")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


async def _fetch_list_page(
    source: Source,
    *,
    page_url: str,
    format_name: str,
    proxy_unavailable: bool,
) -> tuple[list[dict[str, Any]], str, bool]:
    html = ""
    backend = ""
    if not proxy_unavailable:
        try:
            html = await _fetch_residential_html(source.id, page_url)
            backend = "residential_httpx"
        except ProxyPaymentRequiredError as exc:
            from .scrapers.rotator import record_residential_proxy_failure

            record_residential_proxy_failure(exc)
            proxy_unavailable = True
        except Exception as exc:  # noqa: BLE001 - cloud is independent
            logger.warning(
                "Hearthstone-Decks residential list fetch failed format=%s error=%s",
                format_name,
                type(exc).__name__,
            )

    accept_html = lambda candidate: _list_html_is_usable(
        candidate,
        page_url=page_url,
        format_name=format_name,
    )
    if not accept_html(html):
        html, backend = await _fetch_cloud_html(
            source,
            page_url,
            accept_html=accept_html,
        )

    rows = parse_decks_list_html(
        html,
        page_url=page_url,
        format_name=format_name,
    )
    if len(rows) != _LIST_PAGE_LIMIT:
        raise RuntimeError(
            f"Hearthstone-Decks {format_name} list coverage incomplete "
            f"({len(rows)}/{_LIST_PAGE_LIMIT})"
        )
    return rows, backend, proxy_unavailable


async def _fetch_residential_deck_code(source_id: str, url: str) -> dict[str, Any]:
    last_error = "deck code not found"
    for attempt in range(1, 3):
        try:
            html = await _fetch_residential_html(source_id, url)
        except ProxyPaymentRequiredError:
            raise
        except Exception as exc:  # noqa: BLE001 - optional detail page
            last_error = type(exc).__name__
        else:
            code = extract_deck_code_from_html(html)
            if code:
                return {
                    "deck_code": code,
                    "deck_code_status": "ok",
                    "detail_attempts": attempt,
                }
        if attempt < 2:
            await asyncio.sleep(0.5 * attempt)
    return {
        "deck_code": "",
        "deck_code_status": "missing",
        "deck_code_error": last_error,
    }


async def _fetch_cloud_deck_code(
    source: Source,
    url: str,
) -> tuple[dict[str, Any], str | None]:
    try:
        html, backend = await _fetch_cloud_html(
            source,
            url,
            accept_html=lambda candidate: bool(extract_deck_code_from_html(candidate)),
        )
    except Exception as exc:  # noqa: BLE001 - individual details are optional
        return (
            {
                "deck_code": "",
                "deck_code_status": "missing",
                "deck_code_error": type(exc).__name__,
            },
            None,
        )
    return (
        {
            "deck_code": extract_deck_code_from_html(html),
            "deck_code_status": "ok",
            "detail_attempts": 1,
        },
        backend,
    )


def _transport_label(backends: set[str]) -> str:
    normalized = sorted(backend for backend in backends if backend)
    if not normalized:
        raise RuntimeError("Hearthstone-Decks has no successful transport")
    if len(normalized) == 1:
        return normalized[0]
    return f"mixed[{','.join(normalized)}]"


async def fetch_hearthstone_decks(source: Source) -> dict[str, Any]:
    """Fetch the current top 20 Standard and Wild decks with safe cloud failover."""

    previous_by_url = {
        _canonical_url(str(deck.get("url") or "")): deck
        for deck in _previous_decks(source.id)
        if deck.get("url")
    }
    proxy_unavailable = False
    backends: set[str] = set()
    lists: dict[str, list[dict[str, Any]]] = {}
    for format_name, page_url in _LIST_PAGES:
        rows, backend, proxy_unavailable = await _fetch_list_page(
            source,
            page_url=page_url,
            format_name=format_name,
            proxy_unavailable=proxy_unavailable,
        )
        lists[format_name] = rows
        backends.add(backend)

    final_decks = lists["Standard"] + lists["Wild"]
    standard_urls = {
        _canonical_url(str(deck["url"])) for deck in lists["Standard"]
    }
    wild_urls = {_canonical_url(str(deck["url"])) for deck in lists["Wild"]}
    if standard_urls & wild_urls:
        raise RuntimeError("Hearthstone-Decks format lists overlap")
    missing_details: list[dict[str, Any]] = []
    for deck in final_decks:
        previous = previous_by_url.get(_canonical_url(str(deck["url"])))
        previous_code = str((previous or {}).get("deck_code") or "")
        if previous_code and previous is not None:
            deck.update(
                {
                    "deck_code": previous_code,
                    "deck_code_status": previous.get("deck_code_status") or "ok",
                    "deck_code_reused": True,
                }
            )
            if previous.get("detail_attempts") is not None:
                deck["detail_attempts"] = previous["detail_attempts"]
        else:
            missing_details.append(deck)

    force_cloud_details = proxy_unavailable or any(
        backend != "residential_httpx" for backend in backends
    )
    cloud_detail_attempts = 0
    for deck in missing_details:
        result: dict[str, Any] | None = None
        if not force_cloud_details:
            try:
                result = await _fetch_residential_deck_code(
                    source.id,
                    str(deck["url"]),
                )
            except ProxyPaymentRequiredError as exc:
                from .scrapers.rotator import record_residential_proxy_failure

                record_residential_proxy_failure(exc)
                proxy_unavailable = True
                force_cloud_details = True
            if result and result.get("deck_code"):
                deck.update(result)
                backends.add("residential_httpx")
                continue

        if cloud_detail_attempts >= _MAX_CLOUD_DETAIL_FETCHES:
            deck.update(
                {
                    "deck_code": "",
                    "deck_code_status": "deferred",
                    "deck_code_error": "cloud detail fetch budget exhausted",
                }
            )
            continue
        cloud_detail_attempts += 1
        result, detail_backend = await _fetch_cloud_deck_code(
            source,
            str(deck["url"]),
        )
        deck.update(result)
        if detail_backend:
            backends.add(detail_backend)

    structured = {
        "type": "hearthstone_decks",
        "decks": final_decks,
        "standard_count": len(lists["Standard"]),
        "wild_count": len(lists["Wild"]),
        "total_decks": len(final_decks),
        "with_deck_code": sum(1 for deck in final_decks if deck.get("deck_code")),
        "missing_deck_code_count": sum(
            1 for deck in final_decks if not deck.get("deck_code")
        ),
        "deck_code_fill_rate": round(
            sum(1 for deck in final_decks if deck.get("deck_code")) / len(final_decks),
            4,
        )
        if final_decks
        else 0.0,
        "cloud_detail_attempts": cloud_detail_attempts,
        "_fetch_backend": _transport_label(backends),
    }
    contract_ok, reason, _report = contract_quality_ok(source.id, structured)
    semantic_report = validate_structured(source.id, structured)
    if not contract_ok:
        raise RuntimeError(f"Hearthstone-Decks failed source contract: {reason}")
    if not semantic_report.ok:
        raise RuntimeError(
            f"Hearthstone-Decks failed semantic validation: {semantic_report.reason}"
        )
    return structured
