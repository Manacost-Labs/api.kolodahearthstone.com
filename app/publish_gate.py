from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .source_contracts import allows_browser_fallback
from .sources import Source

_UNSTRUCTURED_PAGE_BACKENDS = frozenset(
    {
        "brightdata_web_unlocker",
        "camoufox",
        "cloakbrowser",
        "cloudflare_scrape",
        "cloudscraper",
        "curl_cffi",
        "direct",
        "firecrawl",
        "flaresolverr",
        "patchright",
        "playwright",
        "scrapfly",
        "scrapling",
    }
)


def _is_unstructured_page_backend(backend: str | None) -> bool:
    normalized = str(backend or "").strip().lower()
    return normalized in _UNSTRUCTURED_PAGE_BACKENDS or normalized.startswith(
        "scrape_do"
    )


@dataclass(frozen=True)
class PublishGateResult:
    ok: bool
    reason: str
    extra: dict[str, Any]


def validate_candidate_for_publish(
    source: Source,
    parsed: dict[str, Any],
    *,
    backend: str | None,
) -> PublishGateResult:
    if _is_unstructured_page_backend(backend) and not allows_browser_fallback(
        source.id,
        default=True,
    ):
        return PublishGateResult(
            ok=False,
            reason=(
                "backend policy rejected candidate: unstructured page acquisition "
                "is diagnostic only "
                f"for {source.id}"
            ),
            extra={"backend": backend, "backend_allowed": False},
        )

    from .scrapers.quality import validate_parsed_data

    ok, reason = validate_parsed_data(source, parsed)
    return PublishGateResult(
        ok=ok,
        reason=reason,
        extra={"backend": backend, "backend_allowed": True},
    )
