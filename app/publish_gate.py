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


def validate_existing_publication_for_serving(
    source: Source,
    parsed: dict[str, Any],
    *,
    backend: str | None,
) -> PublishGateResult:
    """Revalidate content of an existing publication without re-ingesting it.

    Backend policy is an ingestion boundary. A previously published snapshot
    can remain the last-known-good fallback after that policy becomes stricter,
    provided its content still passes the current contract and semantic checks.
    """

    from .scrapers.quality import validate_parsed_data

    ok, reason = validate_parsed_data(source, parsed)
    backend_allowed = not _is_unstructured_page_backend(backend) or (
        allows_browser_fallback(source.id, default=True)
    )
    return PublishGateResult(
        ok=ok,
        reason=reason,
        extra={
            "backend": backend,
            "backend_allowed": backend_allowed,
            "existing_publication": True,
            "backend_policy_grandfathered": not backend_allowed,
        },
    )
