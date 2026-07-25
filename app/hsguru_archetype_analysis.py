from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

from bs4 import BeautifulSoup, Tag

from .config import scrape_do_token
from .firecrawl_backend import scrape_source_with_options
from .firecrawl_keys import peek_firecrawl_key
from .scrape_do_backend import scrape_url
from .sources import Source
from .storage import load_dataset, save_dataset, save_status


SOURCE_ID = "hsguru_archetype_analysis"
SCHEMA_VERSION = 2
ANALYSIS_RANK = "legend"
ANALYSIS_PERIOD = "past_week"
CARD_STATS_MIN_MULL_COUNT = 25
CARD_STATS_MIN_DRAWN_COUNT = 25
FORMAT_IDS = {"standard": 2, "wild": 1}
CARD_STATS_UNAVAILABLE_RETRY = timedelta(days=7)

CLASS_KEYS = {
    "death knight": "deathknight",
    "demon hunter": "demonhunter",
    "druid": "druid",
    "hunter": "hunter",
    "mage": "mage",
    "paladin": "paladin",
    "priest": "priest",
    "rogue": "rogue",
    "shaman": "shaman",
    "warlock": "warlock",
    "warrior": "warrior",
}


class HSGuruAnalysisUnavailable(RuntimeError):
    """The page loaded successfully, but HSGuru has no table for this sample."""


def _header(value: str) -> str:
    return re.sub(r"[^a-z]+", " ", value.casefold()).strip()


def _number(value: Any) -> float | None:
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _count(value: Any) -> int | None:
    text = str(value or "").split("(", 1)[0]
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _table_with_headers(
    html: str,
    required: set[str],
) -> tuple[Tag | None, dict[str, int]]:
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table"):
        header_row = table.find("thead") or table.find("tr")
        if header_row is None:
            continue
        cells = header_row.find_all(["th", "td"])
        headers = [_header(cell.get_text(" ", strip=True)) for cell in cells]
        indexes = {name: headers.index(name) for name in required if name in headers}
        if len(indexes) == len(required):
            return table, indexes
    return None, {}


def parse_class_matchups_html(html: str) -> list[dict[str, Any]]:
    required = {"class", "winrate", "total games"}
    table, indexes = _table_with_headers(html, required)
    if table is None:
        return []

    rows: list[dict[str, Any]] = []
    table_rows = table.select("tbody tr") or table.find_all("tr")[1:]
    for tr in table_rows:
        cells = tr.find_all(["th", "td"])
        if len(cells) <= max(indexes.values()):
            continue
        class_label = cells[indexes["class"]].get_text(" ", strip=True)
        class_key = CLASS_KEYS.get(_header(class_label))
        if not class_key:
            continue
        games_text = cells[indexes["total games"]].get_text(" ", strip=True)
        winrate = _number(cells[indexes["winrate"]].get_text(" ", strip=True))
        games = _count(games_text)
        share_match = re.search(r"\(([-+]?\d+(?:[.,]\d+)?)\s*%\)", games_text)
        share = _number(share_match.group(1)) if share_match else None
        if winrate is None or not 0 <= winrate <= 100 or games is None:
            continue
        rows.append(
            {
                "class_key": class_key,
                "class_label": class_label,
                "winrate": winrate,
                "games": games,
                "share_pct": share,
            }
        )
    return rows


def _attribute_in_tree(cell: Tag, *names: str) -> str | None:
    for node in (cell, *cell.find_all(True)):
        for name in names:
            value = node.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _card_identity(cell: Tag) -> tuple[str | None, int | None, str]:
    name_node = cell.select_one(".card-name")
    if name_node is not None:
        name = name_node.get_text(" ", strip=True)
        for hidden in name_node.select('[style*="font-size: 0"]'):
            name = name.replace(hidden.get_text(" ", strip=True), "")
    else:
        name = (
            _attribute_in_tree(cell, "data-card-name", "aria-label", "alt")
            or cell.get_text(" ", strip=True)
        )
    card_id = _attribute_in_tree(cell, "data-card-id", "data-cardid")
    dbf_raw = _attribute_in_tree(cell, "data-dbf-id", "data-dbfid")
    dbf_id = int(dbf_raw) if dbf_raw and dbf_raw.isdigit() else None

    link = cell.find("a", href=True)
    if link is not None:
        parsed = urlparse(str(link.get("href") or ""))
        query = parse_qs(parsed.query)
        card_id = card_id or next(iter(query.get("card_id", [])), None)
        dbf_query = next(iter(query.get("dbf_id", [])), None)
        if dbf_id is None and dbf_query and dbf_query.isdigit():
            dbf_id = int(dbf_query)
        path_match = re.search(r"/card/([^/?#]+)(?:/([^/?#]+))?", parsed.path)
        if path_match:
            first, second = path_match.groups()
            if first.isdigit():
                dbf_id = dbf_id or int(first)
                card_id = card_id or second
            else:
                card_id = card_id or first

    image = cell.find("img", src=True)
    if card_id is None and image is not None:
        image_match = re.search(
            r"/(?:tiles|render/[^/]+/[^/]+)/([^/.?]+)",
            str(image.get("src") or ""),
        )
        if image_match:
            card_id = image_match.group(1)

    if dbf_id is not None:
        try:
            from .cards_index import cards_by_dbfid

            metadata = cards_by_dbfid().get(dbf_id) or {}
            card_id = card_id or metadata.get("id")
            name = name or str(metadata.get("name") or "")
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
    return card_id, dbf_id, re.sub(r"\s+", " ", name).strip()


def parse_card_stats_html(html: str) -> list[dict[str, Any]]:
    required = {
        "card",
        "mulligan impact",
        "mulligan count",
        "drawn impact",
        "drawn count",
        "kept impact",
        "kept count",
    }
    table, indexes = _table_with_headers(html, required)
    if table is None:
        return []

    rows: list[dict[str, Any]] = []
    table_rows = table.select("tbody tr") or table.find_all("tr")[1:]
    for tr in table_rows:
        cells = tr.find_all(["th", "td"])
        if len(cells) <= max(indexes.values()):
            continue
        card_id, dbf_id, card_name = _card_identity(cells[indexes["card"]])
        mulligan_count = _count(cells[indexes["mulligan count"]].get_text(" ", strip=True))
        if not card_name or mulligan_count is None:
            continue
        rows.append(
            {
                "card_id": card_id,
                "dbf_id": dbf_id,
                "card_name": card_name,
                "mulligan_impact": _number(
                    cells[indexes["mulligan impact"]].get_text(" ", strip=True)
                ),
                "mulligan_count": mulligan_count,
                "drawn_impact": _number(
                    cells[indexes["drawn impact"]].get_text(" ", strip=True)
                ),
                "drawn_count": _count(
                    cells[indexes["drawn count"]].get_text(" ", strip=True)
                ),
                "kept_impact": _number(
                    cells[indexes["kept impact"]].get_text(" ", strip=True)
                ),
                "kept_count": _count(
                    cells[indexes["kept count"]].get_text(" ", strip=True)
                ),
            }
        )
    return rows


def analysis_urls(archetype: str, format_name: str) -> dict[str, str]:
    format_id = FORMAT_IDS[format_name]
    filters = {
        "format": format_id,
        "rank": ANALYSIS_RANK,
        "period": ANALYSIS_PERIOD,
    }
    return {
        "matchups": (
            f"https://www.hsguru.com/archetype/{quote(archetype)}?"
            f"{urlencode(filters)}"
        ),
        "cards": (
            "https://www.hsguru.com/card-stats?"
            + urlencode(
                {
                    "archetype": archetype,
                    **filters,
                    "min_mull_count": CARD_STATS_MIN_MULL_COUNT,
                    "min_drawn_count": CARD_STATS_MIN_DRAWN_COUNT,
                    "show_counts": "yes",
                }
            )
        ),
    }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _utc_now().isoformat()


def _active_archetypes() -> list[dict[str, str]]:
    dataset = load_dataset("hsguru_meta_matrix") or {}
    structured = ((dataset.get("data") or {}).get("structured") or {})
    rows = (
        (structured.get("current_catalog") or {}).get("archetypes")
        or structured.get("archetypes")
        or []
    )
    active: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        format_name = str(row.get("format") or "")
        archetype = str(row.get("archetype") or "").strip()
        has_decks = bool(row.get("has_decks") or row.get("decks"))
        if format_name in FORMAT_IDS and archetype and has_decks:
            active.append({"format": format_name, "archetype": archetype})
    return active


def _previous_analysis() -> dict[tuple[str, str], dict[str, Any]]:
    dataset = load_dataset(SOURCE_ID) or {}
    rows = ((dataset.get("data") or {}).get("structured") or {}).get("archetypes") or []
    return {
        (str(row.get("format") or ""), str(row.get("archetype") or "").casefold()): dict(row)
        for row in rows
        if isinstance(row, dict)
    }


def _previous_negative_cache() -> dict[tuple[str, str, str], dict[str, Any]]:
    dataset = load_dataset(SOURCE_ID) or {}
    entries = (
        ((dataset.get("data") or {}).get("structured") or {}).get("negative_cache")
        or []
    )
    return {
        (
            str(entry.get("format") or ""),
            str(entry.get("archetype") or "").casefold(),
            str(entry.get("kind") or ""),
        ): dict(entry)
        for entry in entries
        if isinstance(entry, dict)
    }


def _retry_is_pending(entry: dict[str, Any] | None, now: datetime) -> bool:
    if not entry or entry.get("state") != "upstream_unavailable":
        return False
    if (
        entry.get("min_mull_count") != CARD_STATS_MIN_MULL_COUNT
        or entry.get("min_drawn_count") != CARD_STATS_MIN_DRAWN_COUNT
    ):
        return False
    try:
        retry_after = datetime.fromisoformat(str(entry.get("retry_after") or ""))
    except ValueError:
        return False
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=UTC)
    return retry_after > now


def _negative_cache_entry(
    *,
    format_name: str,
    archetype: str,
    reason: str,
    checked_at: datetime,
) -> dict[str, Any]:
    return {
        "format": format_name,
        "archetype": archetype,
        "kind": "card_stats",
        "state": "upstream_unavailable",
        "min_mull_count": CARD_STATS_MIN_MULL_COUNT,
        "min_drawn_count": CARD_STATS_MIN_DRAWN_COUNT,
        "checked_at": checked_at.isoformat(),
        "retry_after": (checked_at + CARD_STATS_UNAVAILABLE_RETRY).isoformat(),
        "reason": reason,
    }


def _firecrawl_headers() -> dict[str, str] | None:
    try:
        from .hsguru_auth import hsguru_firecrawl_headers

        return hsguru_firecrawl_headers()
    except (ImportError, RuntimeError):
        return None


async def _fetch_html(url: str) -> tuple[str, dict[str, Any]]:
    errors: list[str] = []
    if peek_firecrawl_key() is not None:
        source = Source(
            id=f"{SOURCE_ID}:page",
            url=url,
            site="hsguru",
            category="archetype_analysis",
            kind="pipeline",
        )
        try:
            result = await scrape_source_with_options(
                source,
                formats=["html"],
                only_main_content=True,
                headers=_firecrawl_headers(),
                max_age_ms=0,
                wait_ms=5_000,
                timeout_ms=120_000,
            )
            return result.html, {
                "backend": result.backend,
                "request_credits": result.request_credits,
                "final_url": result.final_url,
            }
        except Exception as exc:
            errors.append(f"firecrawl: {exc}")

    if scrape_do_token():
        for super_proxy, attempts in ((False, 2), (True, 3)):
            for attempt in range(1, attempts + 1):
                try:
                    result = await scrape_url(
                        url,
                        render=True,
                        super_proxy=super_proxy,
                    )
                    return result.html, {
                        "backend": "scrape_do_super" if super_proxy else "scrape_do",
                        "request_credits": result.request_cost,
                        "final_url": result.final_url,
                        "attempt": attempt,
                    }
                except Exception as exc:
                    errors.append(
                        f"{'super' if super_proxy else 'standard'} "
                        f"attempt {attempt}: {exc}"
                    )
                    if attempt < attempts:
                        await asyncio.sleep(min(attempt * 2, 4))

    if not errors:
        raise RuntimeError("Firecrawl and Scrape.do are not configured")
    raise RuntimeError("; ".join(errors))


async def refresh_hsguru_archetype_analysis(
    *,
    concurrency: int = 3,
    limit: int | None = None,
    archetypes: list[dict[str, str]] | None = None,
    fetch_html=_fetch_html,
) -> dict[str, Any]:
    started_at = _now()
    targets = list(archetypes if archetypes is not None else _active_archetypes())
    targets.sort(key=lambda row: (row["format"], row["archetype"].casefold()))
    if limit is not None:
        targets = targets[: max(0, limit)]
    if not targets:
        raise RuntimeError("No active HSGuru archetypes with cached decks")

    previous = _previous_analysis()
    negative_cache = _previous_negative_cache()
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 10)))
    acquisitions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    unavailable: list[dict[str, str]] = []
    card_stats_requests_skipped = 0

    async def fetch_and_parse(
        url: str,
        parser,
        *,
        format_name: str,
        archetype: str,
        kind: str,
    ) -> tuple[list[dict[str, Any]], str]:
        async with semaphore:
            html, acquisition = await fetch_html(url)
        rows = parser(html)
        fetched_at = _now()
        acquisitions.append(
            {
                **acquisition,
                "kind": kind,
                "format": format_name,
                "archetype": archetype,
                "rows": len(rows),
            }
        )
        if not rows:
            raise HSGuruAnalysisUnavailable(
                f"HSGuru {kind} page has no data for the requested sample"
            )
        return rows, fetched_at

    async def enrich(target: dict[str, str]) -> dict[str, Any]:
        nonlocal card_stats_requests_skipped
        format_name = target["format"]
        archetype = target["archetype"]
        urls = analysis_urls(archetype, format_name)
        cached = previous.get((format_name, archetype.casefold()), {})
        card_stats_cache_key = (
            format_name,
            archetype.casefold(),
            "card_stats",
        )
        cached_gap = negative_cache.get(card_stats_cache_key)
        entry = {
            "format": format_name,
            "archetype": archetype,
            "rank": ANALYSIS_RANK,
            "period": ANALYSIS_PERIOD,
            "source_urls": urls,
            "class_matchups": list(cached.get("class_matchups") or []),
            "card_stats": list(cached.get("card_stats") or []),
            "matchups_updated_at": cached.get("matchups_updated_at"),
            "card_stats_updated_at": cached.get("card_stats_updated_at"),
        }
        tasks = {
            "matchups": asyncio.create_task(
                fetch_and_parse(
                    urls["matchups"],
                    parse_class_matchups_html,
                    format_name=format_name,
                    archetype=archetype,
                    kind="matchups",
                )
            ),
        }
        if _retry_is_pending(cached_gap, _utc_now()):
            card_stats_requests_skipped += 1
        else:
            tasks["card_stats"] = asyncio.create_task(
                fetch_and_parse(
                    urls["cards"],
                    parse_card_stats_html,
                    format_name=format_name,
                    archetype=archetype,
                    kind="card_stats",
                )
            )
        fresh = 0
        for kind, task in tasks.items():
            try:
                rows, fetched_at = await task
                if kind == "matchups":
                    entry["class_matchups"] = rows
                    entry["matchups_updated_at"] = fetched_at
                else:
                    entry["card_stats"] = rows
                    entry["card_stats_updated_at"] = fetched_at
                    negative_cache.pop(card_stats_cache_key, None)
                fresh += 1
            except HSGuruAnalysisUnavailable as exc:
                if kind == "card_stats":
                    negative_cache[card_stats_cache_key] = _negative_cache_entry(
                        format_name=format_name,
                        archetype=archetype,
                        reason=str(exc),
                        checked_at=_utc_now(),
                    )
                unavailable.append(
                    {
                        "format": format_name,
                        "archetype": archetype,
                        "kind": kind,
                        "reason": str(exc),
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "format": format_name,
                        "archetype": archetype,
                        "kind": kind,
                        "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                    }
                )
        entry["state"] = (
            "ok"
            if fresh == 2
            else "partial"
            if entry["class_matchups"] or entry["card_stats"]
            else "error"
        )
        entry["updated_at"] = max(
            str(entry.get("matchups_updated_at") or ""),
            str(entry.get("card_stats_updated_at") or ""),
        ) or None
        return entry

    refreshed = await asyncio.gather(*(enrich(target) for target in targets))
    refreshed_keys = {
        (row["format"], row["archetype"].casefold())
        for row in refreshed
    }
    retained = [
        row
        for key, row in previous.items()
        if key not in refreshed_keys
    ]
    rows = [*refreshed, *retained]
    rows.sort(key=lambda row: (str(row.get("format")), str(row.get("archetype")).casefold()))
    negative_cache_rows = sorted(
        negative_cache.values(),
        key=lambda row: (
            str(row.get("format")),
            str(row.get("archetype")).casefold(),
            str(row.get("kind")),
        ),
    )
    coverage = {
        format_name: {
            "archetypes": sum(1 for row in rows if row.get("format") == format_name),
            "with_matchups": sum(
                1
                for row in rows
                if row.get("format") == format_name and row.get("class_matchups")
            ),
            "with_card_stats": sum(
                1
                for row in rows
                if row.get("format") == format_name and row.get("card_stats")
            ),
        }
        for format_name in FORMAT_IDS
    }
    firecrawl_credits = sum(
        int(item.get("request_credits") or 0)
        for item in acquisitions
        if item.get("backend") == "firecrawl"
    )
    scrape_do_credits = sum(
        int(item.get("request_credits") or 0)
        for item in acquisitions
        if str(item.get("backend") or "").startswith("scrape_do")
    )
    structured = {
        "type": SOURCE_ID,
        "schema_version": SCHEMA_VERSION,
        "criteria": {
            "rank": ANALYSIS_RANK,
            "period": ANALYSIS_PERIOD,
            "card_stats_min_mull_count": CARD_STATS_MIN_MULL_COUNT,
            "card_stats_min_drawn_count": CARD_STATS_MIN_DRAWN_COUNT,
            "formats": list(FORMAT_IDS),
            "requires_decks": True,
        },
        "coverage": coverage,
        "negative_cache": negative_cache_rows,
        "archetypes": rows,
    }
    payload = {
        "source_id": SOURCE_ID,
        "state": "ok" if not errors else "partial",
        "fetched_at": started_at,
        "http_status": 200,
        "final_url": "https://www.hsguru.com/archetype",
        "content_length": len(json.dumps(structured, ensure_ascii=False).encode("utf-8")),
        "backend": "+".join(
            sorted({str(item.get("backend")) for item in acquisitions})
        ) or "cache",
        "data": {
            "structured": structured,
            "acquisition": {
                "pages": acquisitions,
                "firecrawl_credits_used": firecrawl_credits,
                "scrape_do_credits_used": scrape_do_credits,
                "card_stats_requests_skipped": card_stats_requests_skipped,
            },
        },
    }
    save_dataset(SOURCE_ID, payload)
    status = {
        "source_id": SOURCE_ID,
        "site": "hsguru",
        "category": "archetype_analysis",
        "state": payload["state"],
        "fetched_at": started_at,
        "http_status": 200,
        "backend": payload["backend"],
        "rows_total": len(rows),
        "coverage": coverage,
        "errors": errors[:50],
        "unavailable": unavailable[:500],
        "negative_cache_entries": len(negative_cache_rows),
        "card_stats_requests_skipped": card_stats_requests_skipped,
        "firecrawl_credits_used": firecrawl_credits,
        "scrape_do_credits_used": scrape_do_credits,
    }
    save_status(SOURCE_ID, status)
    return {
        "ok": not errors,
        "source_id": SOURCE_ID,
        "targets": len(targets),
        "archetypes": len(rows),
        "coverage": coverage,
        "errors": errors,
        "unavailable": unavailable,
        "negative_cache_entries": len(negative_cache_rows),
        "card_stats_requests_skipped": card_stats_requests_skipped,
        "firecrawl_credits_used": firecrawl_credits,
        "scrape_do_credits_used": scrape_do_credits,
    }
