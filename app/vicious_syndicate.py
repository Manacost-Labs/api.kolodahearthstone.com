from __future__ import annotations

import asyncio
import logging
import posixpath
import random
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from .proxy_errors import ProxyPaymentRequiredError, proxy_tunnel_error
from .scrapers.http_resilience import (
    DEFAULT_BACKOFF_SECONDS,
    backoff_delay_seconds,
    build_fetch_headers,
    is_session_blocked,
    log_http_error,
)
from .scrapers.proxy import burn_proxy_session, httpx_client_kwargs
from .sources import Source
from .storage import load_status
from .vicious_syndicate_auth import vicious_syndicate_cookies_for_fetch

logger = logging.getLogger(__name__)


class _ViciousProxyCircuit:
    """Fail fast within one radar refresh after a verified proxy CONNECT rejection."""

    def __init__(self) -> None:
        self.error: ProxyPaymentRequiredError | None = None
        self.successful_transports: set[str] = set()

    def open(self, error: ProxyPaymentRequiredError) -> None:
        if self.error is None:
            self.error = error

    def raise_if_open(self) -> None:
        if self.error is not None:
            raise self.error

    def record_success(self, *, proxy_used: bool) -> None:
        self.successful_transports.add(
            "residential_httpx" if proxy_used else "proxyless_direct"
        )

    @property
    def transport_backend(self) -> str | None:
        if not self.successful_transports:
            return None
        transports = sorted(self.successful_transports)
        if len(transports) == 1:
            return transports[0]
        return f"mixed[{','.join(transports)}]"


CLASSES = [
    "DeathKnight",
    "DemonHunter",
    "Druid",
    "Hunter",
    "Mage",
    "Paladin",
    "Priest",
    "Rogue",
    "Shaman",
    "Warlock",
    "Warrior",
]

CLASS_SLUGS = {
    "DeathKnight": "death-knight-decks",
    "DemonHunter": "demon-hunter-decks",
    "Druid": "druid-decks",
    "Hunter": "hunter-decks",
    "Mage": "mage-decks",
    "Paladin": "paladin-decks",
    "Priest": "priest-decks",
    "Rogue": "rogue-decks",
    "Shaman": "shaman-decks",
    "Warlock": "warlock-decks",
    "Warrior": "warrior-decks",
}
REPORT_INDEX_URL = "https://www.vicioussyndicate.com/tag/data-reaper-report/"
_UPSTREAM_PUBLICATION_ISSUES = frozenset(
    {
        "vicious_radars.outdated_issue",
        "vicious_radars.incomplete_active_coverage",
        "vicious_radars.row_issue_mismatch",
    }
)
_READINESS_URL_FIELDS = ("radar_urls", "blocking_radar_urls")
_MAX_READINESS_URLS = 24
_MAX_READINESS_URL_LENGTH = 2048
_READINESS_TIMEOUT_SECONDS = 5.0
_READINESS_TOTAL_TIMEOUT_SECONDS = 15.0
_READINESS_REDISCOVERY_INTERVAL = timedelta(hours=6)
_READINESS_TIMESTAMP_FIELD = "full_discovery_at"
_READINESS_COUNT_FIELDS = (
    "active_radar_urls",
    "parsed_radars",
    "ready_latest_issue_radars",
)


class ViciousUpstreamPublicationPending(RuntimeError):
    """The report exists, but its official radar assets are not all published."""

    failure_reason_code = "unavailable"
    upstream_state = "upstream_publication_pending"
    skip_browser_fallback = True

    def __init__(
        self,
        readiness: dict[str, Any],
        *,
        transport_backend: str | None = None,
    ) -> None:
        self.upstream_readiness = sanitize_upstream_readiness(readiness)
        self.transport_backend = transport_backend
        latest = self.upstream_readiness.get("latest_report_issue", "unknown")
        candidate = self.upstream_readiness.get("candidate_issue", "unknown")
        super().__init__(
            "Vicious upstream publication pending "
            f"(candidate issue {candidate}, latest report {latest})"
        )


def parse_latest_report_metadata(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    reports: list[dict[str, str | int]] = []
    for article in soup.select("article"):
        link = article.find("a", href=re.compile(r"/vs-data-reaper-report-(\d+)/?"))
        if not link:
            continue
        match = re.search(r"/vs-data-reaper-report-(\d+)/?", str(link.get("href") or ""))
        if not match:
            continue
        date_node = article.select_one(".entry-meta-date")
        published_at = ""
        if date_node:
            try:
                published_at = datetime.strptime(
                    date_node.get_text(" ", strip=True), "%B %d, %Y"
                ).date().isoformat()
            except ValueError:
                published_at = ""
        issue = int(match.group(1))
        reports.append(
            {
                "latest_report_issue": issue,
                "latest_report_url": normalize_radar_url(str(link.get("href") or "")),
                "latest_report_published_at": published_at,
            }
        )
    if not reports:
        raise RuntimeError("Vicious Syndicate report index contained no Data Reaper reports")
    latest = max(reports, key=lambda item: int(item["latest_report_issue"]))
    return {key: str(value) for key, value in latest.items()}


def looks_like_vicious_deck_library(html: str) -> bool:
    lowered = html[:120_000].lower()
    return (
        "vicioussyndicate.com" in lowered
        and "deck-library" in lowered
        and ("mh-content" in lowered or "class=\"entry-content" in lowered or "/decks/" in lowered)
    )


def parse_radar_js(html: str) -> dict[str, Any]:
    """
    Extract nodes (var n = ...) and edges (var e = ...) from the inline setup function of index.html.
    """
    nodes = {}
    edges = []
    
    script_match = re.search(r"function\s+setup\s*\(canvas\)\s*\{(.*?)\}\s*</script>", html, re.DOTALL | re.IGNORECASE)
    script_content = script_match.group(1) if script_match else html

    node_match = re.search(r"var\s+n\s*=\s*(\{.*?\});", script_content, re.DOTALL)
    if node_match:
        node_str = node_match.group(1).strip()
        node_entries = re.findall(r'"([^"]+)":\s*(\{.*?\})', node_str, re.DOTALL)
        for name, props_str in node_entries:
            props = {}
            for k in ("radius", "strokewidth"):
                val_m = re.search(rf"{k}:\s*([\d.]+)", props_str)
                if val_m:
                    props[k] = float(val_m.group(1))
            for k in ("fill", "stroke", "text"):
                val_m = re.search(rf"{k}:\s*\"([^\"]+)\"", props_str)
                if val_m:
                    props[k] = val_m.group(1)
            nodes[name] = props

    edge_match = re.search(r"var\s+e\s*=\s*(\[.*?\]);", script_content, re.DOTALL)
    if edge_match:
        edge_str = edge_match.group(1).strip()
        edge_entries = re.findall(r'\[\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*(\{.*?\})\s*\]', edge_str, re.DOTALL)
        for card_a, card_b, props_str in edge_entries:
            props = {}
            for k in ("weight", "length"):
                val_m = re.search(rf"{k}:\s*([\d.]+)", props_str)
                if val_m:
                    props[k] = float(val_m.group(1))
            stroke_m = re.search(r'stroke:\s*"([^"]+)"', props_str)
            if stroke_m:
                props["stroke"] = stroke_m.group(1)
            edges.append({
                "source": card_a,
                "target": card_b,
                **props
            })

    return {
        "nodes": [{"name": k, **v} for k, v in nodes.items()],
        "edges": edges,
    }


def normalize_radar_url(path: str) -> str:
    """Ensure the path is a full, valid URL."""
    path = path.strip()
    if path.startswith("//"):
        return f"https:{path}"
    if path.startswith("/"):
        return f"https://www.vicioussyndicate.com{path}"
    if not path.startswith("http"):
        return f"https://www.vicioussyndicate.com/{path}"
    return path


def radar_upstream_state(issue: str, latest_report_issue: str) -> str:
    try:
        return "ready" if int(issue) == int(latest_report_issue) else "upstream_stale"
    except (TypeError, ValueError):
        return "upstream_unavailable"


def find_radar_url(html: str, *, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    embedded = soup.find(["object", "embed", "iframe"])
    if embedded:
        path = embedded.get("data") or embedded.get("src")
        if path:
            return normalize_radar_url(path)

    candidates: list[str] = []
    for tag in soup.find_all(["a", "script", "iframe", "object", "embed"], href=True):
        candidates.append(str(tag.get("href") or ""))
    for tag in soup.find_all(["script", "iframe", "object", "embed"], src=True):
        candidates.append(str(tag.get("src") or ""))
    for tag in soup.find_all(["object", "embed"], data=True):
        candidates.append(str(tag.get("data") or ""))

    for candidate in candidates:
        lowered = candidate.lower()
        if "radar" in lowered or lowered.endswith("index.html"):
            return normalize_radar_url(candidate)

    match = re.search(r'["\']([^"\']*(?:radar|index\.html)[^"\']*)["\']', html, re.I)
    if match:
        return normalize_radar_url(match.group(1))

    logger.info("No radar URL discovered on %s", base_url)
    return None


def _is_official_vicious_url(url: str) -> bool:
    try:
        parsed_url = urlparse(url)
        return parsed_url.scheme == "https" and (
            parsed_url.hostname or ""
        ).lower() in {
            "vicioussyndicate.com",
            "www.vicioussyndicate.com",
        }
    except ValueError:
        return False


def _is_official_readiness_radar_url(url: str) -> bool:
    if not url or len(url) > _MAX_READINESS_URL_LENGTH:
        return False
    try:
        parsed_url = urlparse(url)
        canonical_path = posixpath.normpath(parsed_url.path)
        decoded_path = unquote(parsed_url.path)
        canonical_decoded_path = posixpath.normpath(decoded_path)
        return bool(
            _is_official_vicious_url(url)
            and parsed_url.port in (None, 443)
            and not parsed_url.username
            and not parsed_url.password
            and not parsed_url.query
            and not parsed_url.fragment
            and canonical_path == parsed_url.path
            and canonical_decoded_path == decoded_path
            and "\\" not in decoded_path
            and not any(ord(character) < 32 for character in decoded_path)
            and parsed_url.path.startswith("/wp-content/datareaper/radars/")
            and parsed_url.path.endswith("/index.html")
        )
    except ValueError:
        return False


def sanitize_upstream_readiness(value: object) -> dict[str, Any]:
    """Bound status metadata and retain only official, non-secret readiness data."""

    if not isinstance(value, dict):
        return {}
    readiness: dict[str, Any] = {}
    for field in ("latest_report_issue", "candidate_issue"):
        candidate = str(value.get(field) or "").strip()
        if candidate.isdigit() and len(candidate) <= 8:
            readiness[field] = candidate
    observed_raw = str(value.get(_READINESS_TIMESTAMP_FIELD) or "").strip()
    if observed_raw and len(observed_raw) <= 64:
        try:
            observed_at = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=UTC)
            observed_at = observed_at.astimezone(UTC)
            if 2000 <= observed_at.year <= 2100:
                readiness[_READINESS_TIMESTAMP_FIELD] = observed_at.isoformat()
    for field in _READINESS_COUNT_FIELDS:
        candidate = value.get(field)
        if (
            isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and 0 <= candidate <= 10_000
        ):
            readiness[field] = candidate
    for field in _READINESS_URL_FIELDS:
        urls: list[str] = []
        raw_urls = value.get(field)
        if isinstance(raw_urls, list):
            for raw_url in raw_urls[:_MAX_READINESS_URLS]:
                url = str(raw_url or "").strip()
                if _is_official_readiness_radar_url(url) and url not in urls:
                    urls.append(url)
        if urls:
            readiness[field] = urls
    return readiness


def _radar_issue_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    title_text = soup.title.get_text(" ", strip=True) if soup.title else ""
    match = re.search(
        r"Issue\s*(?:&#35;|#)\s*(\d+)",
        title_text,
        re.IGNORECASE,
    )
    return match.group(1) if match else "Unknown"


def readiness_is_recent(readiness: dict[str, Any]) -> bool:
    """Return whether readiness was proven by a recent full discovery."""

    refreshed_raw = str(readiness.get(_READINESS_TIMESTAMP_FIELD) or "").strip()
    try:
        refreshed_at = datetime.fromisoformat(refreshed_raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=UTC)
    age = datetime.now(UTC) - refreshed_at.astimezone(UTC)
    return timedelta(0) <= age < _READINESS_REDISCOVERY_INTERVAL


def verified_upstream_pending_readiness(
    value: object,
) -> dict[str, Any] | None:
    """Return normalized recent evidence for a verified temporal upstream gap."""

    readiness = sanitize_upstream_readiness(value)
    candidate_issue = str(readiness.get("candidate_issue") or "")
    latest_issue = str(readiness.get("latest_report_issue") or "")
    radar_urls = readiness.get("radar_urls")
    blocking_urls = readiness.get("blocking_radar_urls")
    if not (
        candidate_issue.isdigit()
        and latest_issue.isdigit()
        and int(candidate_issue) < int(latest_issue)
        and isinstance(radar_urls, list)
        and radar_urls
        and isinstance(blocking_urls, list)
        and blocking_urls
        and set(blocking_urls) <= set(radar_urls)
        and readiness_is_recent(readiness)
    ):
        return None
    return readiness


def classify_radar_publication(
    row_issues: list[object],
    *,
    latest_issue: str,
) -> tuple[str, str]:
    """Classify the whole radar set without depending on response order."""

    latest = int(latest_issue) if str(latest_issue).isdigit() else None
    normalized = [str(issue or "Unknown").strip() or "Unknown" for issue in row_issues]
    numeric = {int(issue) for issue in normalized if issue.isdigit()}
    has_unknown = any(not issue.isdigit() for issue in normalized)
    if not normalized:
        return "Unknown", "upstream_unavailable"
    if latest is None or has_unknown or any(
        issue > latest for issue in numeric
    ):
        return "Mixed" if len(set(normalized)) > 1 else normalized[0], "upstream_unavailable"
    if any(issue < latest for issue in numeric):
        oldest = min(numeric)
        return str(oldest), "upstream_publication_pending"
    return str(latest), "ready"


def upstream_publication_metadata(
    structured: dict[str, Any],
    *,
    semantic_issues: list[object],
) -> dict[str, Any] | None:
    """Return bounded failure metadata only for a verified temporal upstream gap."""

    if structured.get("upstream_state") != "upstream_publication_pending":
        return None
    error_codes: set[str] = set()
    for issue in semantic_issues:
        if isinstance(issue, dict):
            severity = str(issue.get("severity") or "error")
            code = str(issue.get("code") or "")
        else:
            severity = str(getattr(issue, "severity", "error"))
            code = str(getattr(issue, "code", ""))
        if severity == "error" and code:
            error_codes.add(code)
    if not error_codes or not error_codes <= _UPSTREAM_PUBLICATION_ISSUES:
        return None

    candidate_issue = str(structured.get("issue") or "").strip()
    latest_issue = str(structured.get("latest_report_issue") or "").strip()
    if not (
        candidate_issue.isdigit()
        and latest_issue.isdigit()
        and int(candidate_issue) < int(latest_issue)
    ):
        return None

    diagnostics = structured.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    blocking_urls = diagnostics.get("blocking_radar_urls")
    if not isinstance(blocking_urls, list):
        blocking_urls = [
            *(diagnostics.get("broken_radar_urls") or []),
            *(diagnostics.get("outdated_radar_urls") or []),
        ]
    readiness = sanitize_upstream_readiness(
        {
            **diagnostics,
            "latest_report_issue": latest_issue,
            "candidate_issue": candidate_issue,
            "blocking_radar_urls": blocking_urls,
            _READINESS_TIMESTAMP_FIELD: datetime.now(UTC).isoformat(),
        }
    )
    if not readiness.get("radar_urls") or not readiness.get(
        "blocking_radar_urls"
    ):
        return None
    return {
        "failure_reason_code": "unavailable",
        "upstream_state": "upstream_publication_pending",
        "upstream_readiness": readiness,
    }


async def preflight_known_pending_publication(
    source_id: str,
    *,
    latest_issue: str,
    transport_backend: str | None = None,
) -> None:
    """Recheck known official blockers directly before a paid full discovery."""

    status = load_status(source_id) or {}
    readiness = sanitize_upstream_readiness(
        status.get("last_refresh_upstream_readiness")
        or status.get("upstream_readiness")
    )
    if not readiness_is_recent(readiness):
        # Only a full discovery advances this timestamp. Cheap pending checks
        # must not postpone the periodic rediscovery forever.
        return
    if readiness.get("latest_report_issue") != str(latest_issue):
        return
    radar_urls = readiness.get("radar_urls") or []
    blocking_urls = readiness.get("blocking_radar_urls") or []
    ordered_urls = [
        *blocking_urls,
        *(url for url in radar_urls if url not in blocking_urls),
    ][:_MAX_READINESS_URLS]
    if not ordered_urls:
        return

    headers = build_fetch_headers(
        ordered_urls[0],
        accept="text/html,application/xhtml+xml",
    )
    try:
        async with asyncio.timeout(_READINESS_TOTAL_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                timeout=_READINESS_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                for url in ordered_urls:
                    response = await client.get(url, headers=headers)
                    if response.status_code in {404, 410}:
                        # A previously discovered optional archetype can be
                        # removed while the remaining radar set advances. A
                        # missing URL therefore is not proof that the current
                        # issue is still unpublished; keep checking the known
                        # graph URLs and let full discovery reconcile removals.
                        continue
                    if response.status_code != 200:
                        return
                    response_url = str(response.url)
                    if not _is_official_readiness_radar_url(response_url):
                        return
                    if not _valid_vicious_response(response_url, response.text):
                        return
                    radar_issue = _radar_issue_from_html(response.text)
                    if (
                        radar_issue.isdigit()
                        and str(latest_issue).isdigit()
                        and int(radar_issue) < int(latest_issue)
                    ):
                        raise ViciousUpstreamPublicationPending(
                            readiness,
                            transport_backend=transport_backend,
                        )
                    if radar_issue != str(latest_issue):
                        return
    except ViciousUpstreamPublicationPending:
        raise
    except (TimeoutError, httpx.HTTPError):
        # A transport failure is not proof of an incomplete publication. Let the
        # normal resilient path classify it and use its configured providers.
        return


def _valid_vicious_response(url: str, html: str) -> bool:
    """Validate known public Vicious pages before they enter the parser.

    The site's normal HTML currently contains a ``cf_clearance`` reference, so
    the generic WAF marker check alone produces false positives.  These
    URL-specific checks accept only the structures consumed by this pipeline.
    """

    if not _is_official_vicious_url(url):
        return False

    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    if path.rstrip("/") == "/tag/data-reaper-report":
        try:
            parse_latest_report_metadata(html)
        except (RuntimeError, TypeError, ValueError):
            return False
        return True

    if "/datareaper/radars/" in path:
        radar = parse_radar_js(html)
        return bool(radar.get("nodes") and radar.get("edges"))

    if "/deck-library/" in path:
        return looks_like_vicious_deck_library(html)

    if "/decks/" in path:
        return re.search(r"AAE[A-Za-z0-9+/=]+", html) is not None

    return False


async def fetch_with_retry(
    _client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    max_retries: int = 5,
    *,
    source_id: str = "vicious_syndicate_radars",
    optional: bool = False,
    optional_context: str = "optional",
    proxy_circuit: _ViciousProxyCircuit | None = None,
) -> httpx.Response | None:
    """
    FIX: jitter + exponential backoff (5/15/45s) + session burn.

    Some Vicious radar/deck URLs are discovered from public pages but disappear
    later. Treat those as optional misses so systemd logs do not look like a
    source failure when the final radar dataset still passes contracts.
    """
    if not _is_official_vicious_url(url):
        logger.warning(
            "Vicious fetch rejected non-official host context=%s",
            optional_context,
        )
        if optional:
            return None
        raise RuntimeError("Vicious fetch rejected non-official host")

    headers = build_fetch_headers(
        url,
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        extra={
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
    )
    effective_max_retries = min(max_retries, 2) if optional else max_retries
    async with semaphore:
        attempt = 0
        attempt_budget = effective_max_retries
        proxy_error = proxy_circuit.error if proxy_circuit is not None else None
        while attempt < attempt_budget:
            attempt += 1
            await asyncio.sleep(random.uniform(0.2, 0.5))
            if proxy_circuit is not None and proxy_circuit.error is not None:
                proxy_error = proxy_circuit.error
            client_kwargs = httpx_client_kwargs(source_id, page_url=url)
            # Every pipeline URL is canonical and validated. Automatic redirects
            # could leak hostless WordPress cookies to an external Location.
            client_kwargs["follow_redirects"] = False
            proxyless_recovery = proxy_error is not None
            if proxyless_recovery:
                # This fallback is deliberately local to the public Vicious
                # pipeline.  It never changes global proxy routing.
                client_kwargs.pop("proxy", None)
                client_kwargs["trust_env"] = False
            proxy_used = bool(client_kwargs.get("proxy"))
            try:
                # Recreate the client per attempt so a burned sticky proxy session
                # is reflected on the very next retry.
                cookies = vicious_syndicate_cookies_for_fetch()
                if cookies and not proxyless_recovery:
                    client_kwargs["cookies"] = cookies
                async with httpx.AsyncClient(**client_kwargs) as attempt_client:
                    resp = await attempt_client.get(url, headers=headers)
                blocked = is_session_blocked(resp.status_code, resp.text)
                response_url = str(resp.url)
                valid = (
                    resp.status_code == 200
                    and _is_official_vicious_url(response_url)
                    and _valid_vicious_response(response_url, resp.text)
                )
                if valid:
                    if proxy_circuit is not None:
                        proxy_circuit.record_success(proxy_used=proxy_used)
                    if proxyless_recovery:
                        logger.info(
                            "Vicious public fetch recovered without residential proxy context=%s",
                            optional_context,
                        )
                    return resp
                if blocked and proxy_used:
                    burn_proxy_session(source_id, page_url=url, reason="vicious_syndicate_blocked")
                if optional:
                    logger.warning(
                        "Optional Vicious fetch failed context=%s status=%s url=%s",
                        optional_context,
                        resp.status_code,
                        url,
                    )
                else:
                    log_http_error(
                        url=url,
                        status_code=resp.status_code,
                        proxy_ip=None,
                        body=resp.text,
                        source_id=source_id,
                    )
                if attempt < attempt_budget:
                    await asyncio.sleep(
                        backoff_delay_seconds(attempt, schedule=DEFAULT_BACKOFF_SECONDS)
                    )
                    continue
            except ProxyPaymentRequiredError as exc:
                already_recovering = proxy_error is not None
                if proxy_circuit is not None:
                    proxy_circuit.open(exc)
                if proxy_error is None:
                    proxy_error = exc
                # Even a caller configured for one attempt gets one bounded
                # public/direct recovery after a verified CONNECT rejection.
                if not already_recovering and attempt >= attempt_budget:
                    attempt_budget += 1
                continue
            except Exception as exc:  # noqa: BLE001 - classify provider/tunnel failures
                typed_proxy_error = proxy_tunnel_error(exc, proxy_used=proxy_used)
                if typed_proxy_error is not None:
                    already_recovering = proxy_error is not None
                    if proxy_circuit is not None:
                        proxy_circuit.open(typed_proxy_error)
                    if proxy_error is None:
                        proxy_error = typed_proxy_error
                    if not already_recovering and attempt >= attempt_budget:
                        attempt_budget += 1
                    continue
                if optional:
                    logger.warning(
                        "Optional Vicious fetch failed context=%s error=%s url=%s",
                        optional_context,
                        type(exc).__name__,
                        url,
                    )
                else:
                    log_http_error(
                        url=url,
                        status_code=None,
                        proxy_ip=None,
                        body=None,
                        error=str(exc),
                        source_id=source_id,
                    )
                if attempt < attempt_budget:
                    await asyncio.sleep(
                        backoff_delay_seconds(attempt, schedule=DEFAULT_BACKOFF_SECONDS)
                    )
                    continue

        if optional:
            logger.warning(
                "Optional Vicious fetch failed after %d attempts context=%s url=%s",
                effective_max_retries,
                optional_context,
                url,
            )
        else:
            logger.error("Failed to fetch %s after %d attempts.", url, effective_max_retries)
        if proxy_error is not None and not optional:
            raise proxy_error
        return None


async def fetch_radar_html(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    *,
    proxy_circuit: _ViciousProxyCircuit | None = None,
) -> str | None:
    resp = await fetch_with_retry(
        client,
        url,
        semaphore,
        optional=True,
        optional_context="radar_html",
        proxy_circuit=proxy_circuit,
    )
    return resp.text if resp else None


async def fetch_deck_code(
    client: httpx.AsyncClient,
    deck_url: str,
    semaphore: asyncio.Semaphore,
    *,
    proxy_circuit: _ViciousProxyCircuit | None = None,
) -> str | None:
    resp = await fetch_with_retry(
        client,
        deck_url,
        semaphore,
        optional=True,
        optional_context="deck_code",
        proxy_circuit=proxy_circuit,
    )
    if not resp:
        return None

    # Look for button data-clipboard-text or input value containing AAE...
    soup = BeautifulSoup(resp.text, "lxml")
    
    # 1. Look for data-clipboard-text on buttons
    btn = soup.find("button", attrs={"data-clipboard-text": True})
    if btn:
        val = btn["data-clipboard-text"].strip()
        if val.startswith("AAE"):
            return val

    # 2. Look for input tag with AAE value
    inp = soup.find("input", value=True)
    if inp:
        val = inp["value"].strip()
        if val.startswith("AAE"):
            return val

    # 3. Text search
    text_match = re.search(r"(AAE[A-Za-z0-9+/=]+)", resp.text)
    if text_match:
        return text_match.group(1)
        
    return None


async def discover_class_radars(
    client: httpx.AsyncClient,
    class_name: str,
    semaphore: asyncio.Semaphore,
    *,
    proxy_circuit: _ViciousProxyCircuit | None = None,
) -> list[dict[str, Any]]:
    slug = CLASS_SLUGS.get(class_name)
    if not slug:
        return []

    index_url = f"https://www.vicioussyndicate.com/deck-library/{slug}/"
    discovered = []

    try:
        resp = await fetch_with_retry(
            client,
            index_url,
            semaphore,
            optional=True,
            optional_context=f"class_index:{class_name}",
            proxy_circuit=proxy_circuit,
        )
        if not resp:
            logger.warning("Optional Vicious class index missing for %s", class_name)
            return []

        soup = BeautifulSoup(resp.text, "lxml")

        # 1. Main Class Radar URL
        main_radar_url = find_radar_url(resp.text, base_url=index_url)

        if main_radar_url:
            discovered.append({
                "class": class_name,
                "archetype": None,
                "radar_url": main_radar_url,
                "source_page": index_url,
                "inner_deck_pages": [],
                "deck_code": None,
            })

        # 2. Archetype Sublinks
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "#" in href or "?" in href:
                continue
            
            pattern = rf"https?://(?:www\.)?vicioussyndicate\.com/deck-library/{slug}/([^/]+)/?$"
            match = re.match(pattern, href)
            if not match:
                pattern_rel = rf"^/deck-library/{slug}/([^/]+)/?$"
                match = re.match(pattern_rel, href)

            if match:
                arch_name = a.text.strip() or match.group(1).replace("-", " ").title()
                discovered.append({
                    "class": class_name,
                    "archetype": arch_name,
                    "radar_url": None,
                    "source_page": normalize_radar_url(href),
                    "inner_deck_pages": [],
                    "deck_code": None,
                })

    except ProxyPaymentRequiredError:
        raise
    except Exception as e:
        logger.warning("Optional Vicious discovery failed for %s: %s", class_name, e)

    return discovered


async def resolve_archetype_details(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    semaphore: asyncio.Semaphore,
    *,
    proxy_circuit: _ViciousProxyCircuit | None = None,
) -> dict[str, Any] | None:
    try:
        resp = await fetch_with_retry(
            client,
            item["source_page"],
            semaphore,
            optional=True,
            optional_context="archetype_details",
            proxy_circuit=proxy_circuit,
        )
        if resp:
            soup = BeautifulSoup(resp.text, "lxml")
            
            # Resolve radar URL if missing
            if not item["radar_url"]:
                item["radar_url"] = find_radar_url(resp.text, base_url=item["source_page"])

            # Discover nested deck page links (e.g., https://www.vicioussyndicate.com/decks/...)
            deck_pages = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/decks/" in href:
                    deck_pages.append(normalize_radar_url(href))
            
            item["inner_deck_pages"] = list(set(deck_pages))
            return item
    except ProxyPaymentRequiredError:
        raise
    except Exception as e:
        logger.warning(
            "Optional Vicious detail resolution failed for %s: %s",
            item.get("archetype") or item["class"],
            e,
        )
    return None


async def fetch_vicious_syndicate_radars(source: Source) -> dict[str, Any]:
    # Use strict Semaphore of 3 to avoid triggering Cloudflare / server rate-limiting/disconnects
    semaphore = asyncio.Semaphore(3)
    proxy_circuit = _ViciousProxyCircuit()

    async with httpx.AsyncClient(**httpx_client_kwargs(source.id)) as client:
        # This index is mandatory. Resolve it before fanning out optional class
        # requests so a dead paid proxy costs one CONNECT attempt, not 5 + 11×2.
        report_index_response = await fetch_with_retry(
            client,
            REPORT_INDEX_URL,
            semaphore,
            source_id=source.id,
            proxy_circuit=proxy_circuit,
        )
        if report_index_response is None:
            raise RuntimeError("Could not fetch Vicious Syndicate report index")
        latest_report = parse_latest_report_metadata(report_index_response.text)
        await preflight_known_pending_publication(
            source.id,
            latest_issue=latest_report["latest_report_issue"],
            transport_backend=proxy_circuit.transport_backend,
        )

        # Step 1: Discover class indexes & potential archetype pages
        discovery_tasks = [
            discover_class_radars(
                client,
                cls_name,
                semaphore,
                proxy_circuit=proxy_circuit,
            )
            for cls_name in CLASSES
        ]
        discovery_results = await asyncio.gather(*discovery_tasks)

        all_items = []
        for res_list in discovery_results:
            all_items.extend(res_list)
        discovery_count = len(all_items)

        # Step 2: Resolve archetype details (including inner deck links & radar URLs)
        resolve_tasks = [
            resolve_archetype_details(
                client,
                item,
                semaphore,
                proxy_circuit=proxy_circuit,
            )
            for item in all_items
        ]
        resolved_results = await asyncio.gather(*resolve_tasks)
        
        # Keep items that have resolved radar URLs
        active_items = [r for r in resolved_results if r is not None and r.get("radar_url")]
        resolved_count = sum(1 for r in resolved_results if r is not None)

        # Step 3: Fetch deck codes in parallel for items that have deck pages
        async def fetch_code_for_item(item: dict[str, Any]) -> dict[str, Any]:
            if item["inner_deck_pages"]:
                # Fetch first deck page code for this archetype
                code = await fetch_deck_code(
                    client,
                    item["inner_deck_pages"][0],
                    semaphore,
                    proxy_circuit=proxy_circuit,
                )
                if code:
                    item["deck_code"] = code
            return item

        code_tasks = [fetch_code_for_item(item) for item in active_items]
        active_items = await asyncio.gather(*code_tasks)

        # Step 4: Fetch index.html files for all resolved radars and parse them
        async def fetch_and_parse(item: dict[str, Any]) -> dict[str, Any] | None:
            html = await fetch_radar_html(
                client,
                item["radar_url"],
                semaphore,
                proxy_circuit=proxy_circuit,
            )
            if not html:
                return None

            soup = BeautifulSoup(html, "lxml")
            title_text = (
                soup.title.get_text(" ", strip=True)
                if soup.title
                else f"Data Reaper's Radar - {item['class']}"
            )
            issue = _radar_issue_from_html(html)

            radar_data = parse_radar_js(html)
            return {
                "class": item["class"],
                "archetype": item["archetype"],
                "title": title_text,
                "issue": issue,
                "url": item["source_page"],
                "radar_url": item["radar_url"],
                "deck_code": item["deck_code"],
                **radar_data
            }

        parse_tasks = [fetch_and_parse(item) for item in active_items]
        parse_results = await asyncio.gather(*parse_tasks)

    radars = [r for r in parse_results if r is not None]
    latest_issue = latest_report["latest_report_issue"]
    radar_urls = [
        str(item["radar_url"])
        for item in active_items
        if _is_official_vicious_url(str(item.get("radar_url") or ""))
    ]
    broken_radar_urls = [
        str(item["radar_url"])
        for item, parsed_radar in zip(active_items, parse_results, strict=True)
        if parsed_radar is None
    ]
    outdated_radar_urls = [
        str(parsed_radar["radar_url"])
        for parsed_radar in parse_results
        if parsed_radar is not None
        and str(parsed_radar.get("issue") or "") != latest_issue
    ]
    blocking_radar_urls = list(
        dict.fromkeys([*broken_radar_urls, *outdated_radar_urls])
    )
    diagnostics = {
        "classes_attempted": len(CLASSES),
        "discovered_items": discovery_count,
        "resolved_items": resolved_count,
        "active_radar_urls": len(active_items),
        "parsed_radars": len(radars),
        "ready_latest_issue_radars": sum(
            str(radar.get("issue") or "") == latest_issue for radar in radars
        ),
        "radar_urls": list(dict.fromkeys(radar_urls)),
        "broken_radar_urls": list(dict.fromkeys(broken_radar_urls)),
        "outdated_radar_urls": list(dict.fromkeys(outdated_radar_urls)),
        "blocking_radar_urls": blocking_radar_urls,
    }
    try:
        from .refresh_log import log_action

        log_action(
            "api.route.ok" if radars else "api.route.fail",
            source_id=source.id,
            level="info" if radars else "warn",
            detail=(
                "Vicious radar discovery "
                f"discovered={discovery_count} active={len(active_items)} parsed={len(radars)}"
            ),
            extra={"diagnostics": diagnostics},
        )
    except Exception:
        logger.debug("Failed to log Vicious diagnostics", exc_info=True)

    issue, upstream_state = classify_radar_publication(
        [radar.get("issue") for radar in radars],
        latest_issue=latest_issue,
    )

    classes_summary = {}
    for r in radars:
        cls = r["class"]
        arch = r["archetype"]
        if cls not in classes_summary:
            classes_summary[cls] = {
                "class": cls,
                "has_archetypes": False,
                "archetypes": [],
            }
        if arch is not None:
            classes_summary[cls]["has_archetypes"] = True
            classes_summary[cls]["archetypes"].append(arch)

    return {
        "type": "vicious_syndicate_radars",
        "_fetch_backend": proxy_circuit.transport_backend,
        "issue": issue,
        **latest_report,
        "upstream_state": upstream_state,
        "classes_summary": list(classes_summary.values()),
        "radars": radars,
        "total_radars": len(radars),
        "diagnostics": diagnostics,
    }
