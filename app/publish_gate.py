from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .source_contracts import allows_browser_fallback
from .sources import Source

_VICIOUS_TEMPORAL_LKG_ISSUES = frozenset(
    {
        "vicious_radars.outdated_issue",
        "vicious_radars.incomplete_active_coverage",
        "vicious_radars.row_issue_mismatch",
    }
)

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


def is_usable_vicious_temporal_lkg(
    source: Source,
    parsed: dict[str, Any],
) -> bool:
    """Pure policy check for an explicitly stale but complete Vicious snapshot.

    Unlike ``validate_parsed_data``, this helper has no telemetry side effects,
    so health polling can safely revalidate the publication on every cache
    refresh without emitting an expected semantic-failure event.
    """
    if source.id != "vicious_syndicate_radars":
        return False

    from .source_contracts import contract_quality_ok
    from .source_validators import validate_structured

    structured = parsed.get("structured") or parsed.get("hsreplay_extracted") or {}
    if not isinstance(structured, dict):
        return False
    contract_ok, _contract_reason, _contract_report = contract_quality_ok(
        source.id,
        structured,
    )
    semantic = validate_structured(source.id, structured)
    error_codes = {
        issue.code for issue in semantic.issues if issue.severity == "error"
    }
    issue = str(structured.get("issue") or "")
    latest_issue = str(structured.get("latest_report_issue") or "")
    radars = [
        row for row in (structured.get("radars") or []) if isinstance(row, dict)
    ]
    complete_graphs = bool(radars) and all(
        str(row.get("issue") or "") == issue
        and isinstance(row.get("nodes"), list)
        and bool(row["nodes"])
        and all(
            isinstance(node, dict) and bool(str(node.get("name") or "").strip())
            for node in row["nodes"]
        )
        and isinstance(row.get("edges"), list)
        and bool(row["edges"])
        and all(
            isinstance(edge, dict)
            and bool(str(edge.get("source") or "").strip())
            and bool(str(edge.get("target") or "").strip())
            for edge in row["edges"]
        )
        for row in radars
    )
    diagnostics = structured.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    active_radar_urls = diagnostics.get("active_radar_urls")
    parsed_radars = diagnostics.get("parsed_radars")
    bounded_coverage = bool(
        isinstance(active_radar_urls, int)
        and not isinstance(active_radar_urls, bool)
        and isinstance(parsed_radars, int)
        and not isinstance(parsed_radars, bool)
        and active_radar_urls > 0
        and parsed_radars == len(radars)
        and 0 <= active_radar_urls - parsed_radars <= 1
    )
    return bool(
        contract_ok
        and error_codes
        and error_codes <= _VICIOUS_TEMPORAL_LKG_ISSUES
        and issue.isdigit()
        and latest_issue.isdigit()
        and int(issue) < int(latest_issue)
        and complete_graphs
        and bounded_coverage
    )


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

    temporal_lkg = is_usable_vicious_temporal_lkg(source, parsed)
    if temporal_lkg:
        ok = True
        reason = (
            "existing complete Vicious snapshot is usable only as an explicit "
            "temporal LKG"
        )
    else:
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
            "lkg_temporal_grandfathered": temporal_lkg,
        },
    )
