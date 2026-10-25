"""Shared page acquisition and table helpers for the HSGuru coverage datasets.

HSGuru pages are Phoenix LiveView dead renders: the server sends the complete
first render, so most pages need no browser. Pages are read through Scrape.do
(a plain page first, one rendered retry when it fails validation; no super
proxy, as for the deck catalogs); the few pages whose rows arrive over the
LiveView socket ask for a rendered page directly.

Scrape.do allows 50 concurrent requests per account and the mulligan queue uses
40 of them, so a reader never runs more than five pages at once.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from lxml import html as lxml_html

from .sources import Source

HSGURU = "https://www.hsguru.com"
MAX_CONCURRENCY = 5
PAGE_TIMEOUT_MS = 60_000
# (render, super): a plain page costs 1 credit and a rendered one 5. Super
# proxies (10/25) are not used: on 2026-10-25 a rendered retry recovered the
# pages a plain request missed.
PLAIN_PROFILES = ((False, False), (True, False))
RENDER_PROFILES = ((True, False),)
PAYWALL_MARKER = "you do not have access to these filters"
_SKIPPED_PROVIDERS = frozenset({"decodo", "firecrawl", "brightdata", "scrapfly"})

Validator = Callable[[str], bool]


@dataclass(frozen=True)
class Page:
    url: str
    html: str
    backend: str
    credits: int


PageFetcher = Callable[[str, Validator, bool], Awaitable[Page]]


def _scrape_page(source_id: str, url: str, validate: Validator, render: bool) -> Page:
    from .firecrawl_backend import _scrape_sync
    from .meta_intelligence.budget import acquisition_scope

    source = Source(
        id=source_id, url=url, site="hsguru", category="hsguru_page", kind="pipeline"
    )
    # "detail" jobs stop at the Meta-Lab throttle threshold of the shared
    # monthly HSGuru budget, before critical meta refreshes are affected.
    with acquisition_scope(source_id, job_class="detail"):
        result = _scrape_sync(
            source,
            formats=["html"],
            only_main_content=True,
            headers=None,
            max_age_ms=0,
            timeout_ms=PAGE_TIMEOUT_MS,
            skip_providers=set(_SKIPPED_PROVIDERS),
            accept_result=lambda scraped: validate(str(scraped.html or "")),
            scrape_do_profiles=RENDER_PROFILES if render else PLAIN_PROFILES,
        )
    return Page(
        url=url,
        html=str(result.html or ""),
        backend=str(getattr(result, "backend", "scrape_do") or "scrape_do"),
        credits=int(getattr(result, "request_credits", 0) or 0),
    )


def scrape_do_fetcher(source_id: str) -> PageFetcher:
    """The production fetcher: Scrape.do through the shared provider cascade."""

    async def fetch(url: str, validate: Validator, render: bool) -> Page:
        call = functools.partial(
            contextvars.copy_context().run,
            _scrape_page,
            source_id,
            url,
            validate,
            render,
        )
        return await asyncio.to_thread(call)

    return fetch


@dataclass
class PageReader:
    """Reads pages with bounded concurrency and keeps the run's accounting."""

    source_id: str
    fetcher: PageFetcher | None = None
    concurrency: int = MAX_CONCURRENCY
    pages_ok: int = 0
    pages_failed: int = 0
    credits: int = 0
    backends: dict[str, int] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    _semaphore: asyncio.Semaphore | None = None

    def __post_init__(self) -> None:
        self.concurrency = max(1, min(int(self.concurrency), MAX_CONCURRENCY))
        if self.fetcher is None:
            self.fetcher = scrape_do_fetcher(self.source_id)

    async def read(
        self, url: str, validate: Validator, *, render: bool = False
    ) -> Page | None:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.concurrency)
        assert self.fetcher is not None
        async with self._semaphore:
            try:
                page = await self.fetcher(url, validate, render)
            except Exception as exc:  # noqa: BLE001 - one page never ends a run
                self.pages_failed += 1
                self.errors.append(
                    {"url": url, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
                )
                return None
        if not validate(page.html):
            self.pages_failed += 1
            self.errors.append({"url": url, "error": "content_validation"})
            return None
        self.pages_ok += 1
        self.credits += max(0, int(page.credits))
        self.backends[page.backend] = self.backends.get(page.backend, 0) + 1
        return page

    async def read_many(
        self, urls: list[str], validate: Validator, *, render: bool = False
    ) -> dict[str, Page | None]:
        unique = list(dict.fromkeys(urls))
        pages = await asyncio.gather(
            *(self.read(url, validate, render=render) for url in unique)
        )
        return dict(zip(unique, pages, strict=True))

    def summary(self) -> dict[str, Any]:
        return {
            "pages_ok": self.pages_ok,
            "pages_failed": self.pages_failed,
            "scrape_do_credits": self.credits,
            "backends": dict(self.backends),
            "errors": self.errors[:50],
        }


# --- HTML helpers ---------------------------------------------------------------


def document(page_html: str) -> Any:
    return lxml_html.fromstring(page_html or "<html></html>")


def text(node: Any) -> str:
    return " ".join(str(node.text_content()).split()) if node is not None else ""


def number(value: str) -> float | None:
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value or "").replace("−", "-"))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def integer(value: str) -> int | None:
    parsed = number(str(value or "").replace(",", "").replace(" ", ""))
    return int(parsed) if parsed is not None else None


def is_paywalled(page_html: str) -> bool:
    return PAYWALL_MARKER in (page_html or "").casefold()


def table_headers(table: Any) -> list[str]:
    # Some legacy pages nest a <tr> inside the <thead> row; take the first row
    # that really has header cells.
    header_row = next((row for row in table.iter("tr") if row.xpath("./th")), None)
    if header_row is None:
        return []
    return [
        re.sub(r"[↑↓]", "", text(cell)).strip() for cell in header_row.xpath("./th")
    ]


def table_body_rows(table: Any) -> list[Any]:
    rows = table.xpath("./tbody/tr")
    if rows:
        return rows
    return [row for row in table.xpath(".//tr") if not row.xpath("./th")]


def find_table(page_html: str, required_headers: tuple[str, ...]) -> Any | None:
    """The first table whose header row contains every required label."""
    wanted = {label.casefold() for label in required_headers}
    for table in document(page_html).xpath("//table"):
        headers = {label.casefold() for label in table_headers(table)}
        if wanted <= headers:
            return table
    return None


def player_cell(cell: Any) -> dict[str, Any]:
    """Name, region tag, country flag and profile of an HSGuru player cell."""
    region_tag = next(
        (
            text(node)
            for node in cell.xpath('.//span[contains(@class, "tag")]')
            if text(node)
        ),
        "",
    )
    flag = cell.xpath('.//img[contains(@src, "flagcdn.com")]/@alt')
    profile = ""
    battletag = ""
    for href in cell.xpath(".//a/@href"):
        if href.startswith("/player-profile/"):
            profile = f"{HSGURU}{href}"
            battletag = unquote(href.removeprefix("/player-profile/"))
            break
    name = ""
    links = [
        link
        for link in cell.xpath(".//a")
        if not str(link.get("href") or "").startswith("http")
    ]
    if links:
        spans = links[0].xpath(".//span[not(@class)]")
        name = next((text(span) for span in reversed(spans) if text(span)), "")
        name = name or text(links[0])
    if not name:
        name = text(cell)
        if region_tag and name.startswith(region_tag):
            name = name[len(region_tag) :].strip()
    return {
        "name": name,
        "battletag": battletag or None,
        "region": region_tag or None,
        "country": flag[0].upper() if flag else None,
        "profile_url": profile or None,
    }
