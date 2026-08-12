from __future__ import annotations

from enum import Enum
from typing import NamedTuple

from .hsreplay_card_periods import HSREPLAY_CARD_PERIOD_SOURCE_IDS
from .sources import SOURCES, Source
from .trinket_slices import (
    LEGACY_DEFAULT_TRINKET_SOURCE_IDS,
    TRINKET_SLICE_SOURCE_IDS,
)


class SourceTier(str, Enum):
    LIGHT_API = "light_api"
    MEDIUM_API = "medium_api"
    BROWSER_PATCHRIGHT = "browser_patchright"
    BROWSER_PROTECTED = "browser_protected"


API_FIRST_TIERS: frozenset[SourceTier] = frozenset(
    {
        SourceTier.LIGHT_API,
        SourceTier.MEDIUM_API,
        SourceTier.BROWSER_PATCHRIGHT,
    }
)

# These sources retain their browser-protected cadence/concurrency class, but
# have a cheaper deterministic API route before the existing browser fallback.
API_FIRST_SOURCE_IDS: frozenset[str] = frozenset({"hsreplay_decks_trending"})


LIGHT_API_IDS: frozenset[str] = frozenset(
    {
        "firestone_standard",
        "firestone_battlegrounds_comps",
        "firestone_battlegrounds_cards",
        "firestone_battlegrounds_spells",
        "firestone_arena_cards_normal",
        "firestone_arena_cards_underground",
        "firestone_arena_legendaries_underground",
        "firestone_arena_legendaries_normal",
        "hsreplay_arena",
        "hsreplay_arena_cards_advanced",
        "hsreplay_arena_winning_decks",
        "hsreplay_arena_legendaries",
        "hsreplay_battlegrounds_comps",
        "heartharena_tierlist",
        "metastats_decks",
        "metastats_matchups",
    }
)

MEDIUM_API_IDS: frozenset[str] = frozenset(
    {
        "hearthstone_decks",
        "hsreplay_battlegrounds_compositions",
        "hsreplay_battlegrounds_heroes",
        "hsreplay_battlegrounds_minions",
        *HSREPLAY_CARD_PERIOD_SOURCE_IDS,
        "hsreplay_meta_archetypes_legend_eu_1d",
        "hsreplay_meta_top_1000_legend_1d_firecrawl",
        "hsreplay_meta_legend_1d_firecrawl",
        "hsreplay_meta_diamond_4to1_1d_firecrawl",
        "hsreplay_arena_class_pages_firecrawl",
        *LEGACY_DEFAULT_TRINKET_SOURCE_IDS,
        *TRINKET_SLICE_SOURCE_IDS,
        "vicious_syndicate_live_beta",
        "vicious_syndicate_radars",
    }
)

BROWSER_PATCHRIGHT_IDS: frozenset[str] = frozenset(
    {
        "hsreplay_cards_legend_included_winrate",
        "hsreplay_cards_legend_included_popularity",
    }
)

BROWSER_PROTECTED_IDS: frozenset[str] = frozenset(
    {
        "hsguru_streamer_decks_legend_1000",
        "hsguru_meta_standard_legend",
        "hsguru_meta_standard_diamond_4to1",
        "hsguru_meta_wild_legend",
        "hsguru_meta_wild_diamond_4to1",
        "hsguru_meta_standard_top_5k",
        "hsguru_meta_standard_top_legend",
        "hsguru_meta_wild_top_legend",
        "hsguru_meta_wild_top_5k",
        "hsguru_matchups_legend",
        "hsguru_matchups_wild_legend",
        "hsguru_matchups_diamond_4to1",
        "hsreplay_decks_trending",
    }
)

_ALL_TIER_IDS = (
    LIGHT_API_IDS | MEDIUM_API_IDS | BROWSER_PATCHRIGHT_IDS | BROWSER_PROTECTED_IDS
)


def tier_for(source_id: str) -> SourceTier:
    if source_id in LIGHT_API_IDS:
        return SourceTier.LIGHT_API
    if source_id in MEDIUM_API_IDS:
        return SourceTier.MEDIUM_API
    if source_id in BROWSER_PATCHRIGHT_IDS:
        return SourceTier.BROWSER_PATCHRIGHT
    if source_id in BROWSER_PROTECTED_IDS:
        return SourceTier.BROWSER_PROTECTED
    raise KeyError(f"Unknown source_id for tier mapping: {source_id}")


class PartitionedSources(NamedTuple):
    light_api: list[Source]
    medium_api: list[Source]
    browser_patchright: list[Source]
    browser_protected: list[Source]


def partition_sources(sources: list[Source]) -> PartitionedSources:
    """Split sources into scrape tiers; kind="pipeline" sources are skipped.

    Pipeline sources are refreshed by dedicated systemd timers/commands and
    have no scrape tier, so they never enter the fetch planner.
    """
    light: list[Source] = []
    medium: list[Source] = []
    patchright: list[Source] = []
    protected: list[Source] = []
    for source in sources:
        if source.kind != "scrape":
            continue
        match tier_for(source.id):
            case SourceTier.LIGHT_API:
                light.append(source)
            case SourceTier.MEDIUM_API:
                medium.append(source)
            case SourceTier.BROWSER_PATCHRIGHT:
                patchright.append(source)
            case SourceTier.BROWSER_PROTECTED:
                protected.append(source)
    return PartitionedSources(light, medium, patchright, protected)


def validate_tier_registry() -> None:
    """Every kind="scrape" source must map to exactly one tier.

    kind="pipeline" sources (dedicated systemd timers) are exempt from tier
    coverage and must NOT appear in any tier set.
    """
    scrape_ids = {s.id for s in SOURCES if s.kind == "scrape"}
    pipeline_ids = {s.id for s in SOURCES if s.kind != "scrape"}
    leaked = pipeline_ids & _ALL_TIER_IDS
    if leaked:
        raise RuntimeError(
            f"Pipeline sources must not be assigned a scrape tier: {sorted(leaked)}"
        )
    if scrape_ids != _ALL_TIER_IDS:
        missing = scrape_ids - _ALL_TIER_IDS
        extra = _ALL_TIER_IDS - scrape_ids
        raise RuntimeError(
            f"Source tier registry mismatch: missing={sorted(missing)} extra={sorted(extra)}"
        )
