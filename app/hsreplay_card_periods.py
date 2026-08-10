from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from typing import Any

HSREPLAY_CARD_FORMATS = (
    ("standard", "RANKED_STANDARD", "Standard"),
    ("wild", "RANKED_WILD", "Wild"),
)
HSREPLAY_CARD_RANKS = (
    ("legend", "LEGEND", "Legend"),
    (
        "diamond_4_1",
        "DIAMOND_FOUR_THROUGH_DIAMOND_ONE",
        "Diamond 4-1",
    ),
    ("diamond", "DIAMOND", "Diamond"),
    ("platinum", "PLATINUM", "Platinum"),
)
HSREPLAY_CARD_PERIODS = (
    ("1d", "LAST_1_DAY", "last 1 day"),
    ("3d", "LAST_3_DAYS", "last 3 days"),
    ("7d", "LAST_7_DAYS", "last 7 days"),
    ("14d", "LAST_14_DAYS", "last 14 days"),
    ("patch", "CURRENT_PATCH", "current patch"),
)


@dataclass(frozen=True)
class CardPeriodSourceSpec:
    source_id: str
    format_id: str
    rank_id: str
    period_id: str
    rank_range: str
    time_range: str
    game_type: str
    url: str
    description: str


def _card_period_source_id(format_id: str, rank_id: str, period_id: str) -> str:
    format_fragment = "_wild" if format_id == "wild" else ""
    return f"hsreplay_cards{format_fragment}_{rank_id}_{period_id}"


HSREPLAY_CARD_PERIOD_SOURCE_SPECS = tuple(
    CardPeriodSourceSpec(
        source_id=_card_period_source_id(format_id, rank_id, period_id),
        format_id=format_id,
        rank_id=rank_id,
        period_id=period_id,
        rank_range=rank_range,
        time_range=time_range,
        game_type=game_type,
        url=(
            "https://hsreplay.net/cards/"
            f"#rankRange={rank_range}&sortBy=includedPopularity"
            f"&timeRange={time_range}&gameType={game_type}"
        ),
        description=(
            f"HSReplay {format_label} cards, {rank_label}, {period_label}."
        ),
    )
    for format_id, game_type, format_label in HSREPLAY_CARD_FORMATS
    for rank_id, rank_range, rank_label in HSREPLAY_CARD_RANKS
    for period_id, time_range, period_label in HSREPLAY_CARD_PERIODS
)

STANDARD_HSREPLAY_CARD_PERIOD_SOURCE_IDS = tuple(
    spec.source_id
    for spec in HSREPLAY_CARD_PERIOD_SOURCE_SPECS
    if spec.format_id == "standard"
)
WILD_HSREPLAY_CARD_PERIOD_SOURCE_IDS = tuple(
    spec.source_id
    for spec in HSREPLAY_CARD_PERIOD_SOURCE_SPECS
    if spec.format_id == "wild"
)
HSREPLAY_CARD_PERIOD_SOURCE_IDS = (
    *STANDARD_HSREPLAY_CARD_PERIOD_SOURCE_IDS,
    *WILD_HSREPLAY_CARD_PERIOD_SOURCE_IDS,
)


@dataclass(frozen=True)
class CardPeriodFetch:
    payload: dict[str, Any]
    backend: str
    attempts: tuple[dict[str, str], ...]


def _timeout_seconds() -> float:
    return max(15.0, float(os.environ.get("HS_CARD_PERIOD_PROXY_TIMEOUT_SECONDS", "90")))


def _json_document(value: str) -> dict[str, Any]:
    candidate = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    pre = re.search(r"<pre[^>]*>(.*?)</pre>", candidate, flags=re.DOTALL | re.IGNORECASE)
    if pre:
        candidate = html.unescape(re.sub(r"<[^>]+>", "", pre.group(1))).strip()
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise RuntimeError("HSReplay card-list response is not a JSON object")  # noqa: TRY004
    return parsed


async def _scrape_card_period_page(url: str) -> Any:
    # Imported lazily because app.sources imports this module's generated specs.
    from .firecrawl_backend import scrape_source_with_options
    from .sources import Source

    source = Source(
        id="hsreplay_card_period_proxy",
        url=url,
        site="hsreplay",
        category="ranked_cards",
        kind="pipeline",
    )
    return await scrape_source_with_options(
        source,
        formats=["rawHtml"],
        only_main_content=False,
        max_age_ms=0,
        wait_ms=0,
        timeout_ms=int(_timeout_seconds() * 1000),
    )


async def fetch_hsreplay_card_period_json(url: str) -> CardPeriodFetch:
    result = await _scrape_card_period_page(url)
    payload = _json_document(result.html or result.markdown)
    backend = result.backend
    return CardPeriodFetch(
        payload=payload,
        backend=backend,
        attempts=({"backend": backend, "state": "ok"},),
    )
