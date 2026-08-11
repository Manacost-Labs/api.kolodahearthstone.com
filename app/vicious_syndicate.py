from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

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
    parsed_url = urlparse(url)
    return parsed_url.scheme == "https" and (parsed_url.hostname or "").lower() in {
        "vicioussyndicate.com",
        "www.vicioussyndicate.com",
    }


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
            title_text = soup.title.string if soup.title else f"Data Reaper's Radar - {item['class']}"
            issue_match = re.search(r"Issue\s*&#35;(\d+)|Issue\s*#(\d+)", title_text)
            issue = issue_match.group(1) or issue_match.group(2) if issue_match else "Unknown"

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
    diagnostics = {
        "classes_attempted": len(CLASSES),
        "discovered_items": discovery_count,
        "resolved_items": resolved_count,
        "active_radar_urls": len(active_items),
        "parsed_radars": len(radars),
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

    issue = "Unknown"
    for r in radars:
        if r.get("issue") != "Unknown":
            issue = r["issue"]
            break

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
        "upstream_state": radar_upstream_state(issue, latest_report["latest_report_issue"]),
        "classes_summary": list(classes_summary.values()),
        "radars": radars,
        "total_radars": len(radars),
        "diagnostics": diagnostics,
    }
