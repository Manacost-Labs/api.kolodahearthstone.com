from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .config import (
    data_dir,
    firecrawl_map_hsreplay_limit,
    firecrawl_map_hsreplay_url,
)
from .scrape_do_backend import (
    ScrapeDoRequestError,
    scrape_url_sync,
)
from .storage import load_dataset

HSREPLAY_MAP_LIMIT = 5000
MIN_HSREPLAY_MAP_URLS = 500
MAX_HSREPLAY_CHILD_SITEMAPS = 32
ALLOWED_HSREPLAY_HOSTS = frozenset({"hsreplay.net", "www.hsreplay.net"})
SUPER_FALLBACK_STATUSES = frozenset({403, 408, 425, 429, 500, 502, 503, 504})
MIN_INDEX_COUNTS = {
    "standard_minions": 100,
    "battlegrounds_minions": 150,
    "battlegrounds_heroes": 30,
    "standard_unique_archetypes": 20,
}


class SitemapContentError(RuntimeError):
    """The provider returned a response that is not a usable sitemap."""


def firecrawl_dir() -> Path:
    path = data_dir() / "firecrawl"
    path.mkdir(parents=True, exist_ok=True)
    return path


def hsreplay_map_path() -> Path:
    return firecrawl_dir() / "hsreplay-map-latest.json"


def hsreplay_index_path() -> Path:
    return firecrawl_dir() / "hsreplay-index-latest.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o644)
    except OSError:
        pass


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_hsreplay_map() -> dict[str, Any] | None:
    return _read_json(hsreplay_map_path())


def load_hsreplay_index() -> dict[str, Any] | None:
    return _read_json(hsreplay_index_path())


def _extract_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, str):
        if value.startswith(("http://", "https://", "/")):
            urls.append(value)
    elif isinstance(value, list):
        for item in value:
            urls.extend(_extract_urls(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"url", "urls", "link", "links"}:
                urls.extend(_extract_urls(item))
            elif isinstance(item, (dict, list)):
                urls.extend(_extract_urls(item))
    return urls


def _normalise_hsreplay_url(url: str) -> str:
    if url.startswith("/"):
        return f"https://hsreplay.net{url}"
    return url


def _is_allowed_hsreplay_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in ALLOWED_HSREPLAY_HOSTS
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def _unique_urls(payload: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in _extract_urls(payload):
        normalised = _normalise_hsreplay_url(url).split("#", 1)[0].rstrip("/")
        if not _is_allowed_hsreplay_url(normalised):
            continue
        if normalised in seen:
            continue
        seen.add(normalised)
        out.append(normalised)
    return sorted(out)


def _validate_map_size(url_count: int, *, previous_count: int) -> None:
    minimum_count = max(MIN_HSREPLAY_MAP_URLS, int(previous_count * 0.50))
    if url_count < minimum_count:
        raise RuntimeError(
            "HSReplay sitemap truncation guard rejected refresh: "
            f"discovered {url_count}, required at least {minimum_count} "
            f"(previous {previous_count})"
        )


def _sitemap_locations(xml: str) -> tuple[str, list[str]]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise SitemapContentError("HSReplay sitemap response is not valid XML") from exc
    root_kind = root.tag.rsplit("}", 1)[-1]
    entry_kind = {"sitemapindex": "sitemap", "urlset": "url"}.get(root_kind)
    if entry_kind is None:
        raise SitemapContentError("HSReplay sitemap response has no usable locations")
    locations: list[str] = []
    for entry in list(root):
        if entry.tag.rsplit("}", 1)[-1] != entry_kind:
            continue
        for child in list(entry):
            if child.tag.rsplit("}", 1)[-1] == "loc" and child.text:
                locations.append(str(child.text).strip())
                break
    if not locations:
        raise SitemapContentError("HSReplay sitemap response has no usable locations")
    return root_kind, locations


def _fetch_sitemap(url: str) -> tuple[str, int, bool, int]:
    if not _is_allowed_hsreplay_url(url):
        raise ValueError("HSReplay sitemap URL must use an approved HTTPS host")
    total_cost = 0
    transport_requests = 0

    def fetch(*, super_proxy: bool):
        nonlocal total_cost, transport_requests
        transport_requests += 1
        result = scrape_url_sync(
            url,
            render=False,
            super_proxy=super_proxy,
            timeout_ms=60_000,
            retry_timeout_ms=55_000,
        )
        total_cost += result.request_cost
        _sitemap_locations(result.html)
        return result

    try:
        scraped = fetch(super_proxy=False)
    except SitemapContentError:
        scraped = fetch(super_proxy=True)
    except ScrapeDoRequestError as exc:
        if not exc.retryable and exc.status_code not in SUPER_FALLBACK_STATUSES:
            raise
        scraped = fetch(super_proxy=True)
    return (
        scraped.html,
        total_cost,
        scraped.super_proxy,
        transport_requests,
    )


def fetch_hsreplay_scrape_do_map(*, publish: bool = True) -> dict[str, Any]:
    root_url = firecrawl_map_hsreplay_url().rstrip("/") + "/sitemap.xml"
    root_xml, root_cost, root_super, root_requests = _fetch_sitemap(root_url)
    root_kind, root_locations = _sitemap_locations(root_xml)
    sitemap_urls = (
        [url for url in root_locations if _is_allowed_hsreplay_url(url)]
        if root_kind == "sitemapindex"
        else []
    )
    page_urls = root_locations if root_kind == "urlset" else []
    if len(sitemap_urls) > MAX_HSREPLAY_CHILD_SITEMAPS:
        raise RuntimeError(
            "HSReplay sitemap index exceeds the child-sitemap safety limit: "
            f"{len(sitemap_urls)} > {MAX_HSREPLAY_CHILD_SITEMAPS}"
        )
    total_cost = root_cost
    super_requests = int(root_super)
    transport_requests = root_requests

    for sitemap_url in sitemap_urls:
        try:
            sitemap_xml, cost, used_super, request_count = _fetch_sitemap(sitemap_url)
            child_kind, locations = _sitemap_locations(sitemap_xml)
            if child_kind != "urlset":
                raise SitemapContentError("Nested HSReplay sitemap indexes are not supported")
        except SitemapContentError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError(
                f"HSReplay sitemap fetch failed for {sitemap_url}: {type(exc).__name__}"
            ) from exc
        page_urls.extend(locations)
        total_cost += cost
        super_requests += int(used_super)
        transport_requests += request_count

    now = datetime.now(UTC).isoformat()
    limit = firecrawl_map_hsreplay_limit(HSREPLAY_MAP_LIMIT)
    urls = _unique_urls({"urls": page_urls})[:limit]
    previous = load_hsreplay_map() or {}
    previous_count = int(previous.get("url_count") or 0)
    _validate_map_size(len(urls), previous_count=previous_count)
    result = {
        "ok": True,
        "schema_version": 2,
        "fetched_at": now,
        "provider": "scrape_do",
        "provider_policy": "scrape_do_only",
        "request": {
            "url": root_url,
            "limit": limit,
            "sitemaps": len(sitemap_urls),
        },
        "url_count": len(urls),
        "urls": urls,
        "scrape_do_requests": transport_requests,
        "scrape_do_request_credits": total_cost,
        "scrape_do_super_requests": super_requests,
    }
    if publish:
        _write_json(hsreplay_map_path(), result)
    return result


def fetch_hsreplay_firecrawl_map() -> dict[str, Any]:
    """Compatibility alias for the historical function name."""

    return fetch_hsreplay_scrape_do_map()


def _structured(source_id: str) -> dict[str, Any]:
    dataset = load_dataset(source_id) or {}
    return ((dataset.get("data") or {}).get("structured") or {})


def _unique_by(items: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = tuple(item.get(field) for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_hsreplay_index(
    *,
    map_payload: dict[str, Any] | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    cards = _structured("hsreplay_cards_legend_1d").get("cards") or []
    standard_minions = [
        {
            "id": card.get("id"),
            "dbfId": card.get("dbfId"),
            "name": card.get("name"),
            "class": card.get("cardClass"),
            "cost": card.get("cost"),
            "rarity": card.get("rarity"),
            "deck_popularity": card.get("deck_popularity"),
            "deck_winrate": card.get("deck_winrate"),
        }
        for card in cards
        if card.get("type") == "MINION" and card.get("name")
    ]

    bg_minions_raw = _structured("hsreplay_battlegrounds_minions").get("minions") or []
    battlegrounds_minions = [
        {
            "id": row.get("id"),
            "dbfId": row.get("dbfId") or row.get("minion_dbf_id"),
            "name": row.get("name") or row.get("minion"),
            "tavern_tier": row.get("tavern_tier") or row.get("techLevel"),
            "impact": row.get("impact"),
            "win_share": row.get("win_share"),
            "popularity": row.get("popularity"),
        }
        for row in bg_minions_raw
        if row.get("name") or row.get("minion")
    ]

    heroes_raw = _structured("hsreplay_battlegrounds_heroes").get("heroes") or []
    battlegrounds_heroes = [
        {
            "hero": row.get("hero"),
            "dbfId": row.get("dbfId"),
            "tier": row.get("tier"),
            "pick_rate": row.get("pick_rate"),
            "avg_placement": row.get("avg_placement"),
            "best_comp": row.get("best_comp"),
        }
        for row in heroes_raw
        if row.get("hero")
    ]

    meta = _structured("hsreplay_meta_archetypes_legend_eu_1d")
    standard_archetypes: list[dict[str, Any]] = []
    for class_row in meta.get("classes") or []:
        for archetype in class_row.get("archetypes") or []:
            name = archetype.get("archetype")
            archetype_id = archetype.get("archetype_id")
            if not name or not archetype.get("url") or (isinstance(archetype_id, int) and archetype_id < 0):
                continue
            standard_archetypes.append(
                {
                    "archetype_id": archetype_id,
                    "archetype": name,
                    "class": class_row.get("class"),
                    "class_name": class_row.get("class_name"),
                    "url": _normalise_hsreplay_url(archetype.get("url")),
                    "winrate": archetype.get("winrate"),
                    "popularity": archetype.get("popularity"),
                    "games": archetype.get("games"),
                }
            )

    resolved_map_payload = map_payload if map_payload is not None else load_hsreplay_map() or {}
    standard_minions = _unique_by(standard_minions, ("dbfId", "name"))
    battlegrounds_minions = _unique_by(battlegrounds_minions, ("dbfId", "name"))
    battlegrounds_heroes = _unique_by(battlegrounds_heroes, ("dbfId", "hero"))
    standard_archetypes = _unique_by(standard_archetypes, ("archetype_id", "archetype"))
    result = {
        "ok": True,
        "generated_at": datetime.now(UTC).isoformat(),
        "map_fetched_at": resolved_map_payload.get("fetched_at"),
        "map_url_count": resolved_map_payload.get("url_count"),
        "sources": {
            "standard_minions": "hsreplay_cards_legend_1d",
            "battlegrounds_minions": "hsreplay_battlegrounds_minions",
            "battlegrounds_heroes": "hsreplay_battlegrounds_heroes",
            "standard_archetypes": "hsreplay_meta_archetypes_legend_eu_1d",
        },
        "counts": {
            "standard_minions": len(standard_minions),
            "battlegrounds_minions": len(battlegrounds_minions),
            "battlegrounds_heroes": len(battlegrounds_heroes),
            "standard_unique_archetypes": len(standard_archetypes),
        },
        "standard_minions": standard_minions,
        "battlegrounds_minions": battlegrounds_minions,
        "battlegrounds_heroes": battlegrounds_heroes,
        "standard_unique_archetypes": standard_archetypes,
    }
    quality_errors = [
        f"{name} too small ({int(result['counts'].get(name) or 0)} < {minimum})"
        for name, minimum in MIN_INDEX_COUNTS.items()
        if int(result["counts"].get(name) or 0) < minimum
    ]
    if quality_errors:
        raise RuntimeError(
            "HSReplay derived index quality gate rejected refresh: " + "; ".join(quality_errors)
        )
    if publish:
        _write_json(hsreplay_index_path(), result)
    return result


def refresh_hsreplay_map_and_index() -> dict[str, Any]:
    map_payload = fetch_hsreplay_scrape_do_map(publish=False)
    index = build_hsreplay_index(map_payload=map_payload, publish=False)
    _write_json(hsreplay_map_path(), map_payload)
    _write_json(hsreplay_index_path(), index)
    return {
        "ok": True,
        "map_path": str(hsreplay_map_path()),
        "index_path": str(hsreplay_index_path()),
        "map_url_count": map_payload.get("url_count"),
        "provider": map_payload.get("provider"),
        "scrape_do_request_credits": map_payload.get("scrape_do_request_credits"),
        "counts": index.get("counts"),
    }
