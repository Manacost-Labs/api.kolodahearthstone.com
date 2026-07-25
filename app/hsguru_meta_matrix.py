from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import product
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote, urlencode, urlparse

from bs4 import BeautifulSoup

from .firecrawl_backend import FirecrawlScrape, scrape_source_with_options
from .config import hsguru_current_patch_period, scrape_do_token
from .hsguru_auth import hsguru_firecrawl_headers
from .hsguru_decks import cached_hsguru_catalog_decks
from .scrape_do_backend import scrape_url
from .source_state import SourceState
from .sources import Source
from .storage import load_dataset, save_dataset, save_status


SOURCE_ID = "hsguru_meta_matrix"
HSGURU_META_URL = "https://www.hsguru.com/meta"
FORMATS = ("standard", "wild")
RANKS = (
    "all",
    "diamond",
    "diamond_4to1",
    "diamond_to_legend",
    "legend",
    "top_5k",
    "top_legend",
    "top_500",
    "top_100",
)
ROLLING_PERIODS = (
    "past_day",
    "past_3_days",
    "past_week",
    "past_2_weeks",
)
DEFAULT_PATCH_PERIOD = "patch_36.0.3"
NAMED_PERIODS = ("violet_hold",)
PERIODS = (*ROLLING_PERIODS, DEFAULT_PATCH_PERIOD, *NAMED_PERIODS)
COINS = ("any_player",)
MIN_GAMES = (100, 250, 500, 1000, 2500, 5000)
CURRENT_MIN_GAMES = 50

_FORMAT_QUERY = {"standard": "2", "wild": "1"}
_REQUIRED_HEADERS = {
    "archetype": "archetype",
    "winrate": "winrate",
    "popularity": "popularity",
    "turns": "turns",
    "duration": "duration_minutes",
    "climbing speed": "climbing_speed",
}


@dataclass(frozen=True)
class SliceSpec:
    format: str
    rank: str
    period: str
    coin: str
    key: str
    url: str


def matrix_periods(current_patch_period: str) -> tuple[str, ...]:
    if not re.fullmatch(r"patch_\d+(?:\.\d+){1,3}", current_patch_period):
        raise ValueError(f"Invalid HSGuru patch period: {current_patch_period}")
    return (*ROLLING_PERIODS, current_patch_period, *NAMED_PERIODS)


def _patch_version_key(period: str) -> tuple[int, ...]:
    return tuple(int(part) for part in period.removeprefix("patch_").split("."))


def patch_periods_from_html(page_html: str) -> tuple[str, ...]:
    periods: set[str] = set()
    soup = BeautifulSoup(page_html, "lxml")
    for link in soup.find_all("a", href=True):
        values = parse_qs(urlparse(str(link.get("href") or "")).query).get("period") or []
        for value in values:
            if re.fullmatch(r"patch_\d+(?:\.\d+){1,3}", value):
                periods.add(value)
    return tuple(sorted(periods, key=_patch_version_key))


def iter_slice_specs(periods: tuple[str, ...] = PERIODS) -> tuple[SliceSpec, ...]:
    specs = []
    for format_name, rank, period, coin in product(FORMATS, RANKS, periods, COINS):
        params = {
            "format": _FORMAT_QUERY[format_name],
            "rank": rank,
            "period": period,
            "min_games": MIN_GAMES[0],
        }
        query = urlencode(params)
        key = "|".join((format_name, rank, period, coin))
        specs.append(SliceSpec(format_name, rank, period, coin, key, f"{HSGURU_META_URL}?{query}"))
    return tuple(specs)


def _number(value: str) -> float | None:
    match = re.search(r"[-+]?\d[\d\s.,]*", value.replace("\u00a0", " "))
    if not match:
        return None
    token = match.group(0).replace(" ", "")
    if token.count(",") == 1 and "." not in token:
        token = token.replace(",", ".")
    else:
        token = token.replace(",", "")
    try:
        return float(token)
    except ValueError:
        return None


def _games(value: str) -> int | None:
    match = re.search(r"\(([\d\s,.]+)\)", value)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def _header(value: str) -> str:
    return re.sub(r"[^a-z]+", " ", value.lower()).strip()


def _validate_row(row: dict[str, Any]) -> bool:
    return (
        bool(row["archetype"])
        and isinstance(row["games"], int)
        and row["games"] >= 0
        and row["winrate"] is not None
        and 0 <= row["winrate"] <= 100
        and row["popularity"] is not None
        and 0 <= row["popularity"] <= 100
        and row["turns"] is not None
        and row["turns"] >= 0
        and row["duration_minutes"] is not None
        and row["duration_minutes"] >= 0
        and row["climbing_speed"] is not None
    )


def parse_meta_rows(page_html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(page_html, "lxml")
    selected = None
    indexes: dict[str, int] = {}
    for table in soup.find_all("table"):
        header_row = table.find("thead") or table.find("tr")
        headers = [_header(cell.get_text(" ", strip=True)) for cell in header_row.find_all("th")]
        candidate = {
            field: headers.index(label)
            for label, field in _REQUIRED_HEADERS.items()
            if label in headers
        }
        if len(candidate) == len(_REQUIRED_HEADERS):
            selected = table
            indexes = candidate
            break
    if selected is None:
        return []

    rows: list[dict[str, Any]] = []
    table_rows = selected.select("tbody tr") or selected.find_all("tr")[1:]
    for row_number, tr in enumerate(table_rows, start=1):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
        if len(cells) <= max(indexes.values()):
            raise ValueError(f"HSGuru meta row {row_number} has missing columns")
        archetype = re.sub(r"\s+", " ", cells[indexes["archetype"]]).strip()
        popularity_cell = cells[indexes["popularity"]]
        games = _games(popularity_cell)
        if not archetype or games is None:
            raise ValueError(f"HSGuru meta row {row_number} has no archetype or game count")
        row = {
            "archetype": archetype,
            "winrate": _number(cells[indexes["winrate"]]),
            "popularity": _number(popularity_cell),
            "games": games,
            "turns": _number(cells[indexes["turns"]]),
            "duration_minutes": _number(cells[indexes["duration_minutes"]]),
            "climbing_speed": _number(cells[indexes["climbing_speed"]]),
        }
        if not _validate_row(row):
            raise ValueError(f"HSGuru meta row {row_number} has invalid statistics")
        rows.append(row)
    archetypes = [row["archetype"].casefold() for row in rows]
    if len(archetypes) != len(set(archetypes)):
        raise ValueError("HSGuru meta table contains duplicate archetypes")
    return rows


def resolve_current_patch_period(cached_dataset: dict[str, Any] | None = None) -> str:
    configured = hsguru_current_patch_period()
    if configured:
        return configured
    try:
        from scripts.seed_hs_manacost_patches import latest_official_patches

        latest = latest_official_patches(1)
        version = str((latest[0] if latest else {}).get("version") or "")
        if re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
            return f"patch_{version}"
    except Exception:
        pass
    previous = (
        (((cached_dataset or {}).get("data") or {}).get("structured") or {})
        .get("current_catalog", {})
        .get("criteria", {})
        .get("period")
    )
    if isinstance(previous, str) and re.fullmatch(r"patch_\d+(?:\.\d+){1,3}", previous):
        return previous
    raise RuntimeError(
        "Cannot discover the current Hearthstone patch; set HS_HSGURU_PATCH_PERIOD"
    )


async def _discover_hsguru_patch_period(
    cached_dataset: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    fallback = await asyncio.to_thread(resolve_current_patch_period, cached_dataset)
    url = (
        f"{HSGURU_META_URL}?"
        + urlencode(
            {
                "format": _FORMAT_QUERY["standard"],
                "rank": "all",
                "period": "past_day",
                "min_games": MIN_GAMES[0],
            }
        )
    )
    source = Source(
        id=f"{SOURCE_ID}:patch-discovery",
        url=url,
        site="hsguru",
        category="meta_patch_discovery",
    )
    try:
        result = await scrape_source_with_options(
            source,
            formats=["html"],
            only_main_content=True,
            headers=hsguru_firecrawl_headers(),
            max_age_ms=0,
            wait_ms=3_000,
            timeout_ms=120_000,
        )
    except Exception as exc:
        return fallback, {
            "kind": "patch_discovery",
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }
    discovered = patch_periods_from_html(result.html)
    period = discovered[-1] if discovered else fallback
    return period, {
        "kind": "patch_discovery",
        "backend": result.backend,
        "request_credits": result.request_credits,
        "source_url": url,
        "periods_found": list(discovered),
        "selected_period": period,
    }


def _current_catalog_url(format_name: str, period: str) -> str:
    params = {
        'format': _FORMAT_QUERY[format_name],
        'rank': 'all',
        'period': period,
        'min_games': CURRENT_MIN_GAMES,
    }
    return f"{HSGURU_META_URL}?{urlencode(params)}"


def _normalize_current_rows(
    rows: list[dict[str, Any]],
    *,
    format_name: str,
    period: str,
    source_url: str,
) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        games = int(row.get("games") or 0)
        if games < CURRENT_MIN_GAMES:
            continue
        archetype = str(row.get("archetype") or "").strip()
        normalized.append(
            {
                "format": format_name,
                "format_id": int(_FORMAT_QUERY[format_name]),
                "archetype": archetype,
                "games": games,
                "winrate": row.get("winrate"),
                "popularity_pct": row.get("popularity"),
                "avg_turns": row.get("turns"),
                "avg_duration_minutes": row.get("duration_minutes"),
                "climbing_speed_stars_per_hour": row.get("climbing_speed"),
                "period": period,
                "rank": "all",
                "source_url": source_url,
                "archetype_url": f"https://www.hsguru.com/archetype/{quote(archetype)}",
                "decks_url": (
                    "https://www.hsguru.com/decks?"
                    + urlencode(
                        [
                            ("format", _FORMAT_QUERY[format_name]),
                            ("min_games", str(CURRENT_MIN_GAMES)),
                            ("period", period),
                            ("rank", "all"),
                            ("player_deck_archetype[]", archetype),
                        ]
                    )
                ),
                "decks": [],
            }
        )
    return normalized


def enrich_current_rows_with_cached_decks(
    rows: list[dict[str, Any]],
    cached_dataset: dict[str, Any] | None = None,
) -> None:
    """Attach locally cached HSGuru builds without spending scrape credits.

    The dedicated deck-catalog job already refreshes Standard and Wild builds.
    Reusing it here keeps the archetype snapshot self-contained while avoiding
    one paid page request per archetype on every matrix refresh.
    """
    previous_rows = (
        ((((cached_dataset or {}).get("data") or {}).get("structured") or {})
        .get("current_catalog", {})
        .get("archetypes", []))
    )
    previous_decks = {
        (
            str(row.get("format") or ""),
            str(row.get("archetype") or "").casefold(),
        ): row.get("decks") or []
        for row in previous_rows
        if isinstance(row, dict)
    }

    for row in rows:
        format_name = str(row.get("format") or "")
        archetype = str(row.get("archetype") or "")
        decks = cached_hsguru_catalog_decks(archetype, format_name, "all")
        if not decks:
            decks = previous_decks.get((format_name, archetype.casefold()), [])

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for deck in decks:
            if not isinstance(deck, dict):
                continue
            deck_code = str(deck.get("deck_code") or "").strip()
            if not deck_code or deck_code in seen:
                continue
            seen.add(deck_code)
            merged.append(
                {
                    **deck,
                    "sample_rank": deck.get("sample_rank") or "all",
                    "sample_period": deck.get("sample_period") or "past_30_days",
                }
            )
        merged.sort(
            key=lambda deck: (
                int(deck.get("games") or 0),
                float(deck.get("win_rate") or 0),
            ),
            reverse=True,
        )
        row["decks"] = merged
        row["deck_count"] = len(merged)
        row["has_decks"] = bool(merged)


def _current_catalog_coverage(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    return {
        format_name: {
            "archetypes": sum(
                1 for row in rows if row["format"] == format_name
            ),
            "with_decks": sum(
                1
                for row in rows
                if row["format"] == format_name and row.get("has_decks")
            ),
            "decks": sum(
                int(row.get("deck_count") or 0)
                for row in rows
                if row["format"] == format_name
            ),
            "games": sum(
                int(row["games"])
                for row in rows
                if row["format"] == format_name
            ),
        }
        for format_name in FORMATS
    }


def refresh_current_catalog_deck_join() -> dict[str, Any]:
    """Rejoin refreshed deck catalogs into the current archetype snapshot."""
    dataset = load_dataset(SOURCE_ID)
    structured = (((dataset or {}).get("data") or {}).get("structured") or {})
    catalog = structured.get("current_catalog") or {}
    rows = catalog.get("archetypes")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Current HSGuru archetype catalog is unavailable")

    enrich_current_rows_with_cached_decks(rows, dataset)
    joined_at = datetime.now(UTC).isoformat()
    catalog["coverage"] = _current_catalog_coverage(rows)
    catalog["deck_catalog"] = {
        "joined_at": joined_at,
        "source_ids": [
            "hsguru_deck_catalog_standard_all",
            "hsguru_deck_catalog_wild_all",
        ],
        "sample_rank": "all",
        "sample_period": "past_30_days",
    }
    structured["schema_version"] = max(int(structured.get("schema_version") or 0), 7)
    save_dataset(SOURCE_ID, dataset)
    return {
        "ok": True,
        "joined_at": joined_at,
        "archetypes": len(rows),
        "with_decks": sum(1 for row in rows if row.get("has_decks")),
        "decks": sum(int(row.get("deck_count") or 0) for row in rows),
        "coverage": catalog["coverage"],
    }


async def _scrape_current_page(
    format_name: str,
    period: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = _current_catalog_url(format_name, period)
    source = Source(
        id=f"{SOURCE_ID}:current:{format_name}",
        url=url,
        site="hsguru",
        category="meta_current",
    )
    firecrawl_error: Exception | None = None
    try:
        result = await scrape_source_with_options(
            source,
            formats=["html"],
            only_main_content=True,
            headers=hsguru_firecrawl_headers(),
            max_age_ms=0,
            wait_ms=5_000,
            timeout_ms=120_000,
        )
        rows = parse_meta_rows(result.html)
        if not rows:
            raise RuntimeError("Firecrawl page contained no current-patch meta rows")
        return _normalize_current_rows(
            rows,
            format_name=format_name,
            period=period,
            source_url=url,
        ), {
            "format": format_name,
            "backend": result.backend,
            "request_credits": result.request_credits,
            "rows": len(rows),
        }
    except Exception as exc:
        firecrawl_error = exc

    if not scrape_do_token():
        raise RuntimeError(f"Firecrawl failed and Scrape.do is not configured: {firecrawl_error}")
    errors: list[str] = []
    for super_proxy, attempts in ((False, 2), (True, 3)):
        for attempt in range(1, attempts + 1):
            try:
                result = await scrape_url(url, render=True, super_proxy=super_proxy)
                rows = parse_meta_rows(result.html)
                if not rows:
                    raise RuntimeError("rendered page contained no meta rows")
                return _normalize_current_rows(
                    rows,
                    format_name=format_name,
                    period=period,
                    source_url=url,
                ), {
                    "format": format_name,
                    "backend": "scrape_do_super" if super_proxy else "scrape_do",
                    "request_credits": result.request_cost,
                    "rows": len(rows),
                    "attempt": attempt,
                }
            except Exception as exc:
                errors.append(
                    f"{'super' if super_proxy else 'standard'} attempt {attempt}: {exc}"
                )
                if attempt < attempts:
                    await asyncio.sleep(min(attempt * 2, 4))
    raise RuntimeError(
        f"Current {format_name} catalog failed; firecrawl={firecrawl_error}; "
        + "; ".join(errors)
    )


def _record_current_history(rows: list[dict[str, Any]], fetched_at: str) -> None:
    from .db import get_db_connection

    conn = get_db_connection()
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hsguru_archetype_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    format TEXT NOT NULL,
                    archetype TEXT NOT NULL,
                    patch TEXT NOT NULL,
                    rank TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    games INTEGER NOT NULL,
                    winrate REAL,
                    popularity_pct REAL,
                    avg_turns REAL,
                    avg_duration_minutes REAL,
                    climbing_speed_stars_per_hour REAL,
                    UNIQUE(format, archetype, patch, rank, recorded_at)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hsguru_archetype_history_lookup
                ON hsguru_archetype_history(format, archetype, recorded_at DESC)
                """
            )
            conn.executemany(
                """
                INSERT OR REPLACE INTO hsguru_archetype_history (
                    format, archetype, patch, rank, recorded_at, games, winrate,
                    popularity_pct, avg_turns, avg_duration_minutes,
                    climbing_speed_stars_per_hour
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["format"],
                        row["archetype"],
                        row["period"],
                        row["rank"],
                        fetched_at,
                        row["games"],
                        row.get("winrate"),
                        row.get("popularity_pct"),
                        row.get("avg_turns"),
                        row.get("avg_duration_minutes"),
                        row.get("climbing_speed_stars_per_hour"),
                    )
                    for row in rows
                ],
            )
    finally:
        conn.close()


async def _default_scrape(spec: SliceSpec) -> FirecrawlScrape:
    source = Source(
        id=f"{SOURCE_ID}:{spec.key}",
        url=spec.url,
        site="hsguru",
        category="meta_matrix_slice",
    )
    return await scrape_source_with_options(
        source,
        formats=["html"],
        only_main_content=True,
        headers=hsguru_firecrawl_headers(),
        max_age_ms=0,
        wait_ms=5_000,
        timeout_ms=120_000,
    )


async def refresh_hsguru_meta_matrix(
    *,
    concurrency: int = 2,
    attempts: int = 3,
    scrape: Callable[[SliceSpec], Awaitable[FirecrawlScrape]] = _default_scrape,
    scrape_current: Callable[
        [str, str],
        Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]],
    ] = _scrape_current_page,
    discover_patch: Callable[
        [dict[str, Any] | None],
        Awaitable[tuple[str, dict[str, Any] | None]],
    ] = _discover_hsguru_patch_period,
) -> dict[str, Any]:
    fetched_at = datetime.now(UTC).isoformat()
    cached_dataset = load_dataset(SOURCE_ID)
    discovery_errors: list[dict[str, str]] = []
    patch_discovery_acquisition: dict[str, Any] | None = None
    try:
        current_period, patch_discovery_acquisition = await discover_patch(
            cached_dataset,
        )
    except Exception as exc:
        current_period = DEFAULT_PATCH_PERIOD
        discovery_errors.append(
            {
                "key": "current|patch-discovery",
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }
        )
    periods = matrix_periods(current_period)
    specs = iter_slice_specs(periods)
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))
    errors: list[dict[str, str]] = list(discovery_errors)

    async def fetch_one(spec: SliceSpec) -> dict[str, Any] | None:
        last_error: Exception | None = None
        for attempt in range(1, max(1, attempts) + 1):
            try:
                async with semaphore:
                    result = await scrape(spec)
                rows = parse_meta_rows(result.html)
                # Sparse premium ranks (Top-100/Top-500) can legitimately return an
                # empty table for short periods; still publish the slice.
                return {
                    "key": spec.key,
                    "format": spec.format,
                    "rank": spec.rank,
                    "period": spec.period,
                    "coin": spec.coin,
                    "source_url": spec.url,
                    "rows": rows,
                    "row_counts": {
                        str(min_games): sum(1 for row in rows if int(row["games"]) >= min_games)
                        for min_games in MIN_GAMES
                    },
                    "content_length": result.content_length,
                    "backend": result.backend,
                    "firecrawl_credits_used": result.firecrawl_credits_used,
                    "scrape_do_credits_used": result.scrape_do_credits_used,
                }
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 4))
        errors.append(
            {
                "key": spec.key,
                "error": f"{type(last_error).__name__}: {str(last_error)[:300]}",
            }
        )
        return None

    slices = [item for item in await asyncio.gather(*(fetch_one(spec) for spec in specs)) if item]
    slices.sort(key=lambda item: item["key"])
    content_length = sum(int(item.pop("content_length", 0)) for item in slices)
    firecrawl_credits_used = sum(
        float(item.pop("firecrawl_credits_used") or 0) for item in slices
    )
    scrape_do_credits_used = sum(
        int(item.pop("scrape_do_credits_used") or 0) for item in slices
    )
    current_rows: list[dict[str, Any]] = []
    current_acquisition: list[dict[str, Any]] = []
    try:
        current_results = await asyncio.gather(
            *(
                scrape_current(format_name, current_period)
                for format_name in FORMATS
            ),
            return_exceptions=True,
        )
        for format_name, result in zip(FORMATS, current_results, strict=True):
            if isinstance(result, Exception):
                errors.append(
                    {
                        "key": f"current|{format_name}|{current_period}",
                        "error": f"{type(result).__name__}: {str(result)[:300]}",
                    }
                )
                continue
            rows, acquisition = result
            current_rows.extend(rows)
            current_acquisition.append(acquisition)
    except Exception as exc:
        errors.append(
            {
                "key": "current|patch-discovery",
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }
        )
    current_rows.sort(
        key=lambda row: (row["format"], -int(row["games"]), row["archetype"])
    )
    enrich_current_rows_with_cached_decks(current_rows, cached_dataset)
    current_coverage = _current_catalog_coverage(current_rows)
    current_complete = (
        current_period is not None
        and all(current_coverage[name]["archetypes"] > 0 for name in FORMATS)
    )
    firecrawl_credits_used += sum(
        float(item.get("request_credits") or 0) for item in current_acquisition
        if item.get("backend") == "firecrawl"
    )
    scrape_do_credits_used += sum(
        int(item.get("request_credits") or 0)
        for item in current_acquisition
        if str(item.get("backend") or "").startswith("scrape_do")
    )
    if patch_discovery_acquisition:
        backend = str(patch_discovery_acquisition.get("backend") or "")
        request_credits = int(
            patch_discovery_acquisition.get("request_credits") or 0
        )
        if backend == "firecrawl":
            firecrawl_credits_used += request_credits
        elif backend.startswith("scrape_do"):
            scrape_do_credits_used += request_credits
    firecrawl_requests = sum(
        1 for item in slices if item.get("backend") == "firecrawl"
    ) + sum(
        1 for item in current_acquisition if item.get("backend") == "firecrawl"
    )
    if (
        patch_discovery_acquisition
        and patch_discovery_acquisition.get("backend") == "firecrawl"
    ):
        firecrawl_requests += 1
    scrape_do_requests = sum(
        1
        for item in slices
        if str(item.get("backend") or "").startswith("scrape_do")
    ) + sum(
        1
        for item in current_acquisition
        if str(item.get("backend") or "").startswith("scrape_do")
    )
    if (
        patch_discovery_acquisition
        and str(patch_discovery_acquisition.get("backend") or "").startswith(
            "scrape_do"
        )
    ):
        scrape_do_requests += 1
    active_backends = {
        str(item.get("backend"))
        for item in [*slices, *current_acquisition]
        if item.get("backend")
    }
    if patch_discovery_acquisition and patch_discovery_acquisition.get("backend"):
        active_backends.add(str(patch_discovery_acquisition["backend"]))
    dataset_backend = "+".join(sorted(active_backends)) or "cache"
    complete = len(slices) == len(specs) and not errors and current_complete
    structured = {
        "type": "hsguru_meta_matrix",
        "schema_version": 7,
        "fetched_at": fetched_at,
        "dimensions": {
            "formats": list(FORMATS),
            "ranks": list(RANKS),
            "periods": list(periods),
            "coins": list(COINS),
            "min_games": list(MIN_GAMES),
        },
        "base_slice_count": len(slices),
        "logical_slice_count": len(slices) * len(MIN_GAMES),
        "slices": slices,
        "firecrawl": {
            "requests": firecrawl_requests,
            "credits_used": (
                int(firecrawl_credits_used)
                if firecrawl_credits_used.is_integer()
                else firecrawl_credits_used
            ),
            "content_length": content_length,
        },
        "scrape_do": {
            "requests": scrape_do_requests,
            "credits_used": scrape_do_credits_used,
        },
        "patch_discovery": patch_discovery_acquisition,
        "current_catalog": {
            "criteria": {
                "period": current_period,
                "rank": "all",
                "minimum_games": CURRENT_MIN_GAMES,
                "formats": list(FORMATS),
            },
            "coverage": current_coverage,
            "total_archetypes": len(current_rows),
            "archetypes": current_rows,
            "acquisition": current_acquisition,
            "deck_catalog": {
                "joined_at": fetched_at,
                "source_ids": [
                    "hsguru_deck_catalog_standard_all",
                    "hsguru_deck_catalog_wild_all",
                ],
                "sample_rank": "all",
                "sample_period": "past_30_days",
            },
        },
    }
    dataset = {
        "source_id": SOURCE_ID,
        "state": SourceState.OK,
        "fetched_at": fetched_at,
        "http_status": 200,
        "final_url": HSGURU_META_URL,
        "content_length": content_length,
        "backend": dataset_backend,
        "data": {"structured": structured},
    }
    if complete:
        _record_current_history(current_rows, fetched_at)
        save_dataset(SOURCE_ID, dataset)
    save_status(
        SOURCE_ID,
        {
            "source_id": SOURCE_ID,
            "site": "hsguru",
            "category": "meta_matrix",
            "url": HSGURU_META_URL,
            "state": SourceState.OK if complete else SourceState.PARTIAL,
            "fetched_at": fetched_at,
            "http_status": 200 if complete else None,
            "backend": dataset_backend,
            "detail": (
                f"HSGuru matrix: {len(slices)}/{len(specs)} slices, "
                f"{len(slices) * len(MIN_GAMES)}/{len(specs) * len(MIN_GAMES)} logical slices."
            ),
            "errors": errors[:20],
            "serving_cached_dataset": bool(cached_dataset) and not complete,
            "last_refresh_state": SourceState.OK if complete else SourceState.PARTIAL,
            "last_refresh_at": fetched_at,
            "firecrawl_requests": firecrawl_requests,
            "scrape_do_requests": scrape_do_requests,
            "firecrawl_credits_used": structured["firecrawl"]["credits_used"],
            "scrape_do_credits_used": scrape_do_credits_used,
            "current_catalog_archetypes": len(current_rows),
            "current_catalog_period": current_period,
        },
    )
    return {
        "ok": complete,
        "published": complete,
        "serving_cached_dataset": bool(cached_dataset) and not complete,
        "source_id": SOURCE_ID,
        "fetched_at": fetched_at,
        "base_slices": len(slices),
        "logical_slices": len(slices) * len(MIN_GAMES),
        "current_catalog_archetypes": len(current_rows),
        "current_catalog_period": current_period,
        "firecrawl_credits_used": structured["firecrawl"]["credits_used"],
        "scrape_do_credits_used": scrape_do_credits_used,
        "content_length": content_length,
        "errors": errors,
    }
