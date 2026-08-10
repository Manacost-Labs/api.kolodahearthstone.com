from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import product
from typing import Any
from urllib.parse import parse_qs, parse_qsl, quote, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from .config import hsguru_current_patch_period
from .firecrawl_backend import FirecrawlScrape, scrape_source_with_options
from .hsguru_auth import hsguru_firecrawl_headers
from .hsguru_decks import cached_hsguru_catalog_decks
from .job_run import AtomicJobRunSnapshotWriter, JobRunContext
from .resource_locks import ResourceLocked, ResourceLockSet
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
DEFAULT_PATCH_PERIOD = "patch_36.2.0"
NAMED_PERIODS = ("violet_hold",)
PERIODS = (*ROLLING_PERIODS, DEFAULT_PATCH_PERIOD, *NAMED_PERIODS)
COINS = ("any_player",)
MIN_GAMES = (100, 250, 500, 1000, 2500, 5000)
CURRENT_MIN_GAMES = 50
DEFAULT_RUN_DEADLINE_SECONDS = 60 * 60

_FORMAT_QUERY = {"standard": "2", "wild": "1"}
_REQUIRED_HEADERS = {
    "archetype": "archetype",
    "winrate": "winrate",
    "popularity": "popularity",
    "turns": "turns",
    "duration": "duration_minutes",
    "climbing speed": "climbing_speed",
}
_CLASS_TOKENS = frozenset(
    {
        "deathknight",
        "demonhunter",
        "druid",
        "hunter",
        "mage",
        "paladin",
        "priest",
        "rogue",
        "shaman",
        "warlock",
        "warrior",
    }
)


class HSGuruMetaValidationError(ValueError):
    """HSGuru returned HTML that cannot be published safely."""


class HSGuruMetaSchemaError(HSGuruMetaValidationError):
    """The expected HSGuru table structure was not rendered."""


class HSGuruMetaDataError(HSGuruMetaValidationError):
    """The rendered HSGuru table contains contradictory row data."""


@dataclass(frozen=True)
class SliceSpec:
    format: str
    rank: str
    period: str
    coin: str
    key: str
    url: str


@dataclass(frozen=True)
class MetaTableParseResult:
    rows: list[dict[str, Any]]
    duplicate_groups: list[dict[str, Any]]
    duplicate_rows_merged: int


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


def _canonical_archetype_url(value: str) -> str:
    parsed = urlparse(urljoin(HSGURU_META_URL, value))
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return parsed._replace(query=query, fragment="").geturl()


def _archetype_class(cell: Any) -> str:
    classes = {
        str(value).casefold()
        for value in (cell.get("class") or [])
    }
    return next(
        (name for name in sorted(_CLASS_TOKENS) if name in classes),
        "",
    )


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


def _weighted_value(rows: list[dict[str, Any]], field: str, total_games: int) -> float:
    return round(
        sum(float(row[field]) * int(row["games"]) for row in rows) / total_games,
        6,
    )


def parse_meta_table(page_html: str) -> MetaTableParseResult:
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
        raise HSGuruMetaSchemaError("HSGuru meta table was not found")

    parsed_rows: list[dict[str, Any]] = []
    table_rows = selected.select("tbody tr") or selected.find_all("tr")[1:]
    for row_number, tr in enumerate(table_rows, start=1):
        cell_nodes = tr.find_all(["th", "td"])
        cells = [cell.get_text(" ", strip=True) for cell in cell_nodes]
        if len(cells) <= max(indexes.values()):
            raise HSGuruMetaDataError(
                f"HSGuru meta row {row_number} has missing columns"
            )
        archetype = re.sub(r"\s+", " ", cells[indexes["archetype"]]).strip()
        popularity_cell = cells[indexes["popularity"]]
        games = _games(popularity_cell)
        if not archetype or games is None:
            raise HSGuruMetaDataError(
                f"HSGuru meta row {row_number} has no archetype or game count"
            )
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
            raise HSGuruMetaDataError(
                f"HSGuru meta row {row_number} has invalid statistics"
            )
        archetype_cell = cell_nodes[indexes["archetype"]]
        link = archetype_cell.find("a", href=True)
        parsed_rows.append(
            {
                "row": row,
                "row_number": row_number,
                "source_url": (
                    _canonical_archetype_url(str(link.get("href") or ""))
                    if link
                    else ""
                ),
                "class": _archetype_class(archetype_cell),
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in parsed_rows:
        key = str(item["row"]["archetype"]).casefold()
        grouped.setdefault(key, []).append(item)

    rows: list[dict[str, Any]] = []
    duplicate_groups: list[dict[str, Any]] = []
    duplicate_rows_merged = 0
    for group in grouped.values():
        if len(group) == 1:
            rows.append(group[0]["row"])
            continue

        identities = {
            (str(item["source_url"]), str(item["class"]))
            for item in group
        }
        archetype = str(group[0]["row"]["archetype"])
        missing_identity = any(
            not source_url or not class_name
            for source_url, class_name in identities
        )
        if len(identities) != 1 or missing_identity:
            rendered = ", ".join(
                f"row {item['row_number']} url={item['source_url'] or 'missing'} "
                f"class={item['class'] or 'missing'}"
                for item in group
            )
            raise HSGuruMetaDataError(
                f"HSGuru meta table contains conflicting identities for "
                f"{archetype}: {rendered}"
            )

        source_url, class_name = next(iter(identities))
        source_rows = [item["row"] for item in group]
        total_games = sum(int(row["games"]) for row in source_rows)
        if total_games <= 0:
            raise HSGuruMetaDataError(
                f"HSGuru duplicate archetype {archetype} has no games"
            )
        merged = {
            "archetype": archetype,
            "winrate": _weighted_value(source_rows, "winrate", total_games),
            "popularity": round(
                sum(float(row["popularity"]) for row in source_rows),
                6,
            ),
            "games": total_games,
            "turns": _weighted_value(source_rows, "turns", total_games),
            "duration_minutes": _weighted_value(
                source_rows,
                "duration_minutes",
                total_games,
            ),
            "climbing_speed": _weighted_value(
                source_rows,
                "climbing_speed",
                total_games,
            ),
        }
        if not _validate_row(merged):
            raise HSGuruMetaDataError(
                f"HSGuru duplicate archetype {archetype} produced invalid merged statistics"
            )
        rows.append(merged)
        duplicate_rows_merged += len(group) - 1
        duplicate_groups.append(
            {
                "archetype": archetype,
                "rows": len(group),
                "games": total_games,
                "class": class_name,
                "source_url": source_url,
            }
        )

    return MetaTableParseResult(
        rows=rows,
        duplicate_groups=duplicate_groups,
        duplicate_rows_merged=duplicate_rows_merged,
    )


def parse_meta_rows(page_html: str) -> list[dict[str, Any]]:
    return parse_meta_table(page_html).rows


def _carry_forward_missing_slices(
    *,
    specs: tuple[SliceSpec, ...],
    fresh_slices: list[dict[str, Any]],
    cached_dataset: dict[str, Any] | None,
    errors: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], int]:
    cached_structured = (
        (((cached_dataset or {}).get("data") or {}).get("structured") or {})
    )
    cached_by_key = {
        str(item.get("key") or ""): item
        for item in cached_structured.get("slices") or []
        if isinstance(item, dict) and item.get("key")
    }
    error_by_key = {
        str(item.get("key") or ""): str(item.get("error") or "")
        for item in errors
    }
    slices = list(fresh_slices)
    fresh_keys = {str(item.get("key") or "") for item in fresh_slices}
    carried = 0
    cached_fetched_at = str((cached_dataset or {}).get("fetched_at") or "")
    for spec in specs:
        if spec.key in fresh_keys:
            continue
        previous = cached_by_key.get(spec.key)
        if not previous:
            continue
        fallback = deepcopy(previous)
        fallback["fetched_at"] = str(
            fallback.get("fetched_at") or cached_fetched_at
        )
        fallback["quality"] = {
            **(
                fallback.get("quality")
                if isinstance(fallback.get("quality"), dict)
                else {}
            ),
            "serving_cached_slice": True,
            "last_refresh_error": error_by_key.get(
                spec.key,
                "slice was not refreshed",
            ),
        }
        slices.append(fallback)
        carried += 1
    slices.sort(key=lambda item: str(item.get("key") or ""))
    return slices, carried


def _carry_forward_current_catalog(
    *,
    current_period: str,
    fresh_rows: list[dict[str, Any]],
    acquisitions: list[dict[str, Any]],
    cached_dataset: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Fill a failed current-format scrape from the same patch snapshot."""
    cached_catalog = (
        (((cached_dataset or {}).get("data") or {}).get("structured") or {})
        .get("current_catalog", {})
    )
    cached_period = str(
        ((cached_catalog.get("criteria") or {}).get("period") or "")
    )
    if cached_period != current_period:
        return fresh_rows, acquisitions, []

    present_formats = {
        str(row.get("format") or "")
        for row in fresh_rows
        if isinstance(row, dict)
    }
    rows = list(fresh_rows)
    acquisition_rows = list(acquisitions)
    cached_formats: list[str] = []
    previous_rows = cached_catalog.get("archetypes") or []
    for format_name in FORMATS:
        if format_name in present_formats:
            continue
        carried = [
            deepcopy(row)
            for row in previous_rows
            if isinstance(row, dict)
            and str(row.get("format") or "") == format_name
        ]
        if not carried:
            continue
        rows.extend(carried)
        cached_formats.append(format_name)
        acquisition_rows.append(
            {
                "format": format_name,
                "backend": "cache",
                "rows": len(carried),
                "period": current_period,
                "serving_cached_catalog": True,
            }
        )
    return rows, acquisition_rows, cached_formats


def resolve_current_patch_period(cached_dataset: dict[str, Any] | None = None) -> str:
    configured = hsguru_current_patch_period()
    if configured:
        return configured
    try:
        from scripts.seed_hs_manacost_patches import current_patch_version

        version = current_patch_version()
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


def _refresh_current_catalog_deck_join_unlocked() -> dict[str, Any]:
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


def refresh_current_catalog_deck_join() -> dict[str, Any]:
    """Rejoin decks unless the matrix dataset is being refreshed elsewhere."""
    locks = ResourceLockSet(
        [SOURCE_ID],
        metadata={"operation": "refresh_current_catalog_deck_join"},
    )
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {
            "ok": True,
            "joined": False,
            "source_id": SOURCE_ID,
            **exc.as_outcome(),
        }

    try:
        return _refresh_current_catalog_deck_join_unlocked()
    finally:
        locks.release()


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
    result = await scrape_source_with_options(
        source,
        formats=["html"],
        only_main_content=True,
        headers=hsguru_firecrawl_headers(),
        max_age_ms=0,
        wait_ms=5_000,
        timeout_ms=120_000,
    )
    parsed = parse_meta_table(result.html)
    if not parsed.rows:
        raise RuntimeError("Provider cascade returned no current-patch meta rows")
    return _normalize_current_rows(
        parsed.rows,
        format_name=format_name,
        period=period,
        source_url=url,
    ), {
        "format": format_name,
        "backend": result.backend,
        "request_credits": result.request_credits,
        "rows": len(parsed.rows),
        "duplicate_rows_merged": parsed.duplicate_rows_merged,
        "duplicate_groups": parsed.duplicate_groups,
    }


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


async def _refresh_hsguru_meta_matrix_unlocked(
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
    run_context: JobRunContext | None = None,
    deadline_seconds: float = DEFAULT_RUN_DEADLINE_SECONDS,
) -> dict[str, Any]:
    run = run_context or JobRunContext.start(
        timeout_seconds=deadline_seconds,
        total_slices=0,
        snapshot_writer=AtomicJobRunSnapshotWriter.for_job(SOURCE_ID),
        heartbeat_interval_seconds=30,
    )
    run.heartbeat(phase="patch_discovery")
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
    run.set_total_slices(len(specs) + len(FORMATS))
    run.heartbeat(phase="matrix_slices")
    semaphore = asyncio.Semaphore(max(1, min(concurrency, 5)))
    errors: list[dict[str, str]] = list(discovery_errors)
    slice_acquisition: list[dict[str, Any]] = []

    async def fetch_one(spec: SliceSpec) -> dict[str, Any] | None:
        last_error: Exception | None = None
        started = False
        for attempt in range(1, max(1, attempts) + 1):
            try:
                async with semaphore:
                    if not started:
                        if not run.try_start_slice():
                            errors.append(
                                {
                                    "key": spec.key,
                                    "error": (
                                        "JobDeadlineExceeded: run deadline reached "
                                        "before slice start"
                                    ),
                                }
                            )
                            return None
                        started = True
                    elif run.deadline_reached():
                        run.mark_timed_out()
                        last_error = TimeoutError(
                            "run deadline reached before slice retry"
                        )
                        break
                    result = await scrape(spec)
                slice_acquisition.append(
                    {
                        "key": spec.key,
                        "attempt": attempt,
                        "backend": result.backend,
                        "content_length": result.content_length,
                        "firecrawl_credits_used": result.firecrawl_credits_used,
                        "scrape_do_credits_used": result.scrape_do_credits_used,
                    }
                )
                parsed = parse_meta_table(result.html)
                # Sparse premium ranks (Top-100/Top-500) can legitimately return an
                # empty table for short periods; still publish the slice.
                item = {
                    "key": spec.key,
                    "format": spec.format,
                    "rank": spec.rank,
                    "period": spec.period,
                    "coin": spec.coin,
                    "source_url": spec.url,
                    "fetched_at": fetched_at,
                    "rows": parsed.rows,
                    "row_counts": {
                        str(min_games): sum(
                            1
                            for row in parsed.rows
                            if int(row["games"]) >= min_games
                        )
                        for min_games in MIN_GAMES
                    },
                    "backend": result.backend,
                    "quality": {
                        "duplicate_rows_merged": parsed.duplicate_rows_merged,
                        "duplicate_groups": parsed.duplicate_groups,
                    },
                }
                run.finish_slice(succeeded=True)
                return item
            except HSGuruMetaDataError as exc:
                last_error = exc
                break
            except HSGuruMetaSchemaError as exc:
                last_error = exc
                if attempt >= min(max(1, attempts), 2):
                    break
                await asyncio.sleep(min(2 ** (attempt - 1), 4))
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
        if started:
            run.finish_slice(succeeded=False)
        return None

    fresh_slices = [
        item
        for item in await asyncio.gather(*(fetch_one(spec) for spec in specs))
        if item
    ]
    fresh_slices.sort(key=lambda item: item["key"])
    slices, cached_slice_count = _carry_forward_missing_slices(
        specs=specs,
        fresh_slices=fresh_slices,
        cached_dataset=cached_dataset,
        errors=errors,
    )
    content_length = sum(
        int(item.get("content_length") or 0)
        for item in slice_acquisition
    )
    firecrawl_credits_used = sum(
        float(item.get("firecrawl_credits_used") or 0)
        for item in slice_acquisition
    )
    scrape_do_credits_used = sum(
        int(item.get("scrape_do_credits_used") or 0)
        for item in slice_acquisition
    )
    current_rows: list[dict[str, Any]] = []
    current_acquisition: list[dict[str, Any]] = []

    async def fetch_current(
        format_name: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        if not run.try_start_slice():
            errors.append(
                {
                    "key": f"current|{format_name}|{current_period}",
                    "error": (
                        "JobDeadlineExceeded: run deadline reached before "
                        "current-catalog slice start"
                    ),
                }
            )
            return None
        try:
            result = await scrape_current(format_name, current_period)
        except BaseException:
            run.finish_slice(succeeded=False)
            raise
        run.finish_slice(succeeded=True)
        return result

    run.heartbeat(phase="current_catalog")
    try:
        current_results = await asyncio.gather(
            *(
                fetch_current(format_name)
                for format_name in FORMATS
            ),
            return_exceptions=True,
        )
        for format_name, result in zip(FORMATS, current_results, strict=True):
            if result is None:
                continue
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
    current_rows, current_acquisition, cached_current_formats = (
        _carry_forward_current_catalog(
            current_period=current_period,
            fresh_rows=current_rows,
            acquisitions=current_acquisition,
            cached_dataset=cached_dataset,
        )
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
    duplicate_groups: list[dict[str, Any]] = []
    duplicate_rows_merged = 0
    for item in slices:
        quality = item.get("quality") or {}
        duplicate_rows_merged += int(quality.get("duplicate_rows_merged") or 0)
        duplicate_groups.extend(
            {
                "key": item["key"],
                **group,
            }
            for group in quality.get("duplicate_groups") or []
            if isinstance(group, dict)
        )
    for acquisition in current_acquisition:
        duplicate_rows_merged += int(
            acquisition.get("duplicate_rows_merged") or 0
        )
        duplicate_groups.extend(
            {
                "key": (
                    f"current|{acquisition.get('format') or 'unknown'}"
                    f"|{current_period}"
                ),
                **group,
            }
            for group in acquisition.get("duplicate_groups") or []
            if isinstance(group, dict)
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
        1 for item in slice_acquisition if item.get("backend") == "firecrawl"
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
        for item in slice_acquisition
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
        for item in [*slice_acquisition, *current_acquisition]
        if item.get("backend")
    }
    if patch_discovery_acquisition and patch_discovery_acquisition.get("backend"):
        active_backends.add(str(patch_discovery_acquisition["backend"]))
    dataset_backend = "+".join(sorted(active_backends)) or "cache"
    run.heartbeat(phase="finalizing")
    if run.deadline_reached():
        run.mark_timed_out()
    publishable = (
        len(slices) == len(specs)
        and current_complete
        and not run.timed_out
    )
    complete = publishable and cached_slice_count == 0 and not errors
    run_state = (
        SourceState.TIMED_OUT
        if run.timed_out
        else SourceState.OK if complete else SourceState.PARTIAL
    )
    structured = {
        "type": "hsguru_meta_matrix",
        "schema_version": 8,
        "fetched_at": fetched_at,
        "dimensions": {
            "formats": list(FORMATS),
            "ranks": list(RANKS),
            "periods": list(periods),
            "coins": list(COINS),
            "min_games": list(MIN_GAMES),
        },
        "base_slice_count": len(slices),
        "fresh_base_slice_count": len(fresh_slices),
        "cached_base_slice_count": cached_slice_count,
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
        "quality": {
            "duplicate_rows_merged": duplicate_rows_merged,
            "duplicate_groups": duplicate_groups,
            "cached_slices": cached_slice_count,
            "cached_current_formats": cached_current_formats,
            "errors": errors,
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
        "state": SourceState.OK if complete else SourceState.PARTIAL,
        "fetched_at": fetched_at,
        "http_status": 200,
        "final_url": HSGURU_META_URL,
        "content_length": content_length,
        "backend": dataset_backend,
        "data": {"structured": structured},
    }
    if publishable:
        fresh_current_rows = [
            row
            for row in current_rows
            if str(row.get("format") or "") not in cached_current_formats
        ]
        _record_current_history(fresh_current_rows, fetched_at)
        save_dataset(SOURCE_ID, dataset)
    terminal_phase = (
        str(SourceState.TIMED_OUT)
        if run.timed_out
        else "complete" if complete else str(SourceState.PARTIAL)
    )
    run.finalize(phase=terminal_phase)
    save_status(
        SOURCE_ID,
        {
            "source_id": SOURCE_ID,
            "site": "hsguru",
            "category": "meta_matrix",
            "url": HSGURU_META_URL,
            "state": run_state,
            "fetched_at": fetched_at,
            "http_status": 200 if complete else None,
            "backend": dataset_backend,
            "detail": (
                f"HSGuru matrix timed out: {len(fresh_slices)} fresh + "
                f"{cached_slice_count} cached / {len(specs)} slices; "
                "the last-known-good dataset was preserved."
                if run.timed_out
                else (
                    f"HSGuru matrix: {len(fresh_slices)} fresh + "
                    f"{cached_slice_count} cached / {len(specs)} slices, "
                    f"{len(slices) * len(MIN_GAMES)}/"
                    f"{len(specs) * len(MIN_GAMES)} logical slices."
                )
            ),
            "errors": errors[:20],
            "serving_cached_dataset": bool(cached_dataset) and not complete,
            "last_refresh_state": run_state,
            "last_refresh_at": fetched_at,
            "run_id": run.run_id,
            "timed_out": run.timed_out,
            "job_run": run.snapshot(),
            "firecrawl_requests": firecrawl_requests,
            "scrape_do_requests": scrape_do_requests,
            "firecrawl_credits_used": structured["firecrawl"]["credits_used"],
            "scrape_do_credits_used": scrape_do_credits_used,
            "duplicate_rows_merged": duplicate_rows_merged,
            "duplicate_groups": duplicate_groups[:20],
            # Parser monitoring uses rows_total as its generic record count.
            # One base slice produces one row for every local min-games view.
            "rows_total": len(slices) * len(MIN_GAMES),
            "base_slices": len(slices),
            "fresh_base_slices": len(fresh_slices),
            "cached_base_slices": cached_slice_count,
            "cached_slices": cached_slice_count,
            "cached_current_formats": cached_current_formats,
            "published": publishable,
            "current_catalog_archetypes": len(current_rows),
            "current_catalog_period": current_period,
        },
    )
    return {
        "ok": publishable,
        "published": publishable,
        "complete": complete,
        "state": run_state,
        "timed_out": run.timed_out,
        "job_run": run.snapshot(),
        "serving_cached_dataset": bool(cached_dataset) and not complete,
        "source_id": SOURCE_ID,
        "fetched_at": fetched_at,
        "base_slices": len(slices),
        "fresh_base_slices": len(fresh_slices),
        "cached_base_slices": cached_slice_count,
        "cached_current_formats": cached_current_formats,
        "logical_slices": len(slices) * len(MIN_GAMES),
        "current_catalog_archetypes": len(current_rows),
        "current_catalog_period": current_period,
        "firecrawl_credits_used": structured["firecrawl"]["credits_used"],
        "scrape_do_credits_used": scrape_do_credits_used,
        "duplicate_rows_merged": duplicate_rows_merged,
        "duplicate_groups": duplicate_groups,
        "content_length": content_length,
        "errors": errors,
    }


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
    run_context: JobRunContext | None = None,
    deadline_seconds: float = DEFAULT_RUN_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Refresh the matrix unless another process already owns its resource."""
    locks = ResourceLockSet(
        [SOURCE_ID],
        metadata={"operation": "refresh_hsguru_meta_matrix"},
    )
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {
            "ok": True,
            "published": False,
            "source_id": SOURCE_ID,
            **exc.as_outcome(),
        }

    try:
        return await _refresh_hsguru_meta_matrix_unlocked(
            concurrency=concurrency,
            attempts=attempts,
            scrape=scrape,
            scrape_current=scrape_current,
            discover_patch=discover_patch,
            run_context=run_context,
            deadline_seconds=deadline_seconds,
        )
    finally:
        locks.release()
