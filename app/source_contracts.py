from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .completeness import (
    ARENA_LEGENDARY_EXPECTED_BUCKETS,
    COMPLETENESS_SCHEMA_VERSION,
)
from .hsreplay_card_periods import (
    STANDARD_HSREPLAY_CARD_PERIOD_SOURCE_IDS,
    WILD_HSREPLAY_CARD_PERIOD_SOURCE_IDS,
)
from .post_patch_policy import effective_contract_min_rows
from .trinket_slices import TRINKET_SLICE_SOURCE_IDS


@dataclass(frozen=True)
class SourceContract:
    source_id: str
    structured_type: str | None = None
    preferred_channels: tuple[str, ...] = ()
    allow_browser_fallback: bool = True
    min_rows: int | None = None
    min_collection_rows: tuple[tuple[str, int], ...] = ()
    critical_fields: tuple[str, ...] = ()
    min_field_fill_rate: float = 0.0
    regression_drop_ratio: float | None = None
    volatility: str = "stable"
    fallback_policy: str = "html_allowed"
    recommendation: str | None = None
    min_html_bytes: int = 2_000
    early_min_html_bytes: int | None = None


HSREPLAY_JSON_CHANNELS = ("flaresolverr", "scrape_do", "curl_cffi")
HSREPLAY_ARENA_FRESHNESS_CHANNELS = ("scrape_do", "flaresolverr", "curl_cffi")
HSGURU_STREAMER_ROLLING_SOURCE_ID = "hsguru_streamer_decks_legend_1000"

# Only parser-owned, deterministic reasons may turn a missing metric into an
# explained upstream absence.  Keeping the allow-list beside the contracts
# makes it impossible for arbitrary provider text (or an LLM explanation) to
# inflate retrieval completeness.
FIELD_UNAVAILABLE_REASONS: dict[str, dict[str, frozenset[str]]] = {
    # Composition metrics are required for every row.  The empty map opts the
    # source into strict completeness telemetry without inventing an upstream
    # absence that could hide missing data.
    "hsreplay_battlegrounds_compositions": {},
    "hsreplay_battlegrounds_minions": {
        field: frozenset(
            {
                "no_current_patch_aggregates",
                "insufficient_current_patch_sample",
            }
        )
        for field in ("impact", "win_share", "popularity")
    },
    "hsreplay_arena_cards_advanced": {
        field: frozenset({"no_games_in_window"})
        for field in (
            "deck_winrate",
            "winrate_when_drawn",
            "winrate_when_played",
        )
    },
    "hsreplay_arena_legendaries": {
        "winrate": frozenset({"upstream_unavailable_at_zero_pick_rate"}),
        "score": frozenset({"upstream_score_not_reported"}),
    },
    "firestone_standard": {
        "core_cards": frozenset(
            {"generic_class_bucket_without_observed_deck_cluster"}
        ),
    },
}
FIELD_UNEXPLAINED_REASONS: dict[str, dict[str, frozenset[str]]] = {
    "firestone_standard": {
        "core_cards": frozenset(
            {"empty_core_cards_without_deterministic_explanation"}
        ),
    },
}
EXPLAINED_ROW_DROP_REASONS: dict[str, frozenset[str]] = {
    source_id: frozenset()
    for source_id in (
        "hsreplay_battlegrounds_minions",
        "hsreplay_battlegrounds_compositions",
        "hsreplay_arena_cards_advanced",
        "hsreplay_arena_legendaries",
        "firestone_standard",
    )
}
for _hsguru_matchups_source_id in (
    "hsguru_matchups_legend",
    "hsguru_matchups_wild_legend",
    "hsguru_matchups_diamond_4to1",
):
    EXPLAINED_ROW_DROP_REASONS[_hsguru_matchups_source_id] = frozenset(
        {
            "self_matchup_not_applicable",
            "upstream_insufficient_matchup_sample",
        }
    )
EXPLAINED_ROW_DROP_REASONS[HSGURU_STREAMER_ROLLING_SOURCE_ID] = frozenset(
    {"duplicate_streamer_deck"}
)
for _legacy_trinket_source_id in (
    "hsreplay_battlegrounds_trinkets_lesser",
    "hsreplay_battlegrounds_trinkets_greater",
):
    EXPLAINED_ROW_DROP_REASONS[_legacy_trinket_source_id] = frozenset(
        {"unselected_trinket_tier"}
    )
HSREPLAY_FRESHNESS_GATED_SOURCE_IDS = frozenset(
    {
        "hsreplay_battlegrounds_minions",
        "hsreplay_battlegrounds_compositions",
        "hsreplay_arena_cards_advanced",
        "hsreplay_arena_legendaries",
    }
)
HSREPLAY_UNVERIFIED_PUBLISH_REASONS = frozenset(
    {"missing_last_modified", "transport_evidence_unavailable"}
)


CONTRACTS: dict[str, SourceContract] = {
    "hsreplay_cards_legend_included_winrate": SourceContract(
        source_id="hsreplay_cards_legend_included_winrate",
        structured_type="card_stats",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=900,
        critical_fields=("deck_winrate",),
        min_field_fill_rate=0.75,
        regression_drop_ratio=0.30,
        fallback_policy="api_only",
        recommendation="Use HSReplay card_list analytics API; do not save HTML fallback without card metrics.",
    ),
    "hsreplay_cards_legend_included_popularity": SourceContract(
        source_id="hsreplay_cards_legend_included_popularity",
        structured_type="card_stats",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=900,
        critical_fields=("deck_popularity",),
        min_field_fill_rate=0.75,
        regression_drop_ratio=0.30,
        fallback_policy="api_only",
        recommendation="Use HSReplay card_list analytics API; do not save HTML fallback without card metrics.",
    ),
    "hsreplay_cards_legend_1d": SourceContract(
        source_id="hsreplay_cards_legend_1d",
        structured_type="card_stats",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        # Legend/24h is sample-limited and legitimately contains fewer cards
        # than the other Standard rank windows. Regression and field-fill
        # checks still reject truncated or malformed payloads.
        min_rows=450,
        critical_fields=("deck_winrate", "deck_popularity"),
        min_field_fill_rate=0.55,
        regression_drop_ratio=0.50,
        volatility="daily",
        fallback_policy="api_only",
        recommendation="Daily Legend payload is volatile; preserve previous good only on severe drops.",
    ),
    "hsreplay_cards_wild_legend_1d": SourceContract(
        source_id="hsreplay_cards_wild_legend_1d",
        structured_type="card_stats",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=700,
        critical_fields=("deck_winrate", "deck_popularity"),
        min_field_fill_rate=0.45,
        regression_drop_ratio=0.50,
        volatility="daily",
        fallback_policy="api_only",
        recommendation="Wild daily sample swings strongly; accept larger count variance but keep metric checks.",
    ),
    "hsreplay_arena_cards_advanced": SourceContract(
        source_id="hsreplay_arena_cards_advanced",
        structured_type="arena_card_tiers",
        preferred_channels=HSREPLAY_ARENA_FRESHNESS_CHANNELS,
        allow_browser_fallback=False,
        min_rows=900,
        critical_fields=(
            "deck_winrate",
            "winrate_when_drawn",
            "winrate_when_played",
            "in_runs",
            "avg_copies",
        ),
        min_field_fill_rate=0.85,
        regression_drop_ratio=0.30,
        fallback_policy="never_cross_source_fallback",
        recommendation="Arenasmith view requires HSReplay card_stats; preserve previous good instead of Firestone fallback.",
    ),
    "hsreplay_arena_winning_decks": SourceContract(
        source_id="hsreplay_arena_winning_decks",
        structured_type="arena_winning_decks",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=1,
        critical_fields=("final_deck",),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
        recommendation="Use HSReplay winning decks feed and preserve previous good when detail payload is incomplete.",
    ),
    "hsreplay_arena_legendaries": SourceContract(
        source_id="hsreplay_arena_legendaries",
        structured_type="arena_legendary_groups",
        preferred_channels=HSREPLAY_ARENA_FRESHNESS_CHANNELS,
        allow_browser_fallback=True,
        min_rows=10,
        critical_fields=(
            "key_card",
            "cards",
            "winrate",
            "pick_rate",
            "offer_rate",
            "score",
        ),
        min_field_fill_rate=0.50,
        regression_drop_ratio=0.30,
        fallback_policy="preserve_previous_good",
        recommendation=(
            "Primary: card_packages/free + Arenasmith card_stats enrich. "
            "Fallback: Firecrawl scrape of /arena/legendaries/ then same enrich."
        ),
    ),
    "hsreplay_battlegrounds_heroes": SourceContract(
        source_id="hsreplay_battlegrounds_heroes",
        structured_type="bg_heroes",
        allow_browser_fallback=False,
        min_rows=30,
        critical_fields=("hero", "pick_rate", "avg_placement", "tier", "placement_distribution"),
        min_field_fill_rate=0.70,
        regression_drop_ratio=0.35,
        fallback_policy="preserve_previous_good",
    ),
    "hsreplay_battlegrounds_minions": SourceContract(
        source_id="hsreplay_battlegrounds_minions",
        structured_type="bg_minions",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=50,
        critical_fields=("impact", "win_share", "popularity"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
    ),
    "hsreplay_battlegrounds_compositions": SourceContract(
        source_id="hsreplay_battlegrounds_compositions",
        structured_type="bg_compositions",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=5,
        critical_fields=("first_place", "avg_placement", "popularity", "placement_distribution"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
    ),
    "hsreplay_battlegrounds_trinkets_lesser": SourceContract(
        source_id="hsreplay_battlegrounds_trinkets_lesser",
        structured_type="bg_trinkets",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=80,
        critical_fields=("name", "trinket_id", "description", "pick_rate", "avg_placement"),
        min_field_fill_rate=0.90,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
        recommendation="Use the HSReplay trinkets JSON API and preserve the previous valid Lesser snapshot on regression.",
    ),
    "hsreplay_battlegrounds_trinkets_greater": SourceContract(
        source_id="hsreplay_battlegrounds_trinkets_greater",
        structured_type="bg_trinkets",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=80,
        critical_fields=("name", "trinket_id", "description", "pick_rate", "avg_placement"),
        min_field_fill_rate=0.90,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
        recommendation="Use the HSReplay trinkets JSON API and preserve the previous valid Greater snapshot on regression.",
    ),
    "hsreplay_meta_archetypes_legend_eu_1d": SourceContract(
        source_id="hsreplay_meta_archetypes_legend_eu_1d",
        structured_type="hsreplay_meta_archetypes",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=20,
        critical_fields=("winrate", "popularity", "games"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
    ),
    "hsreplay_meta_top_1000_legend_1d_firecrawl": SourceContract(
        source_id="hsreplay_meta_top_1000_legend_1d_firecrawl",
        structured_type="hsreplay_meta_archetypes",
        min_rows=20,
        critical_fields=("winrate", "popularity", "games"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.45,
        volatility="daily",
        fallback_policy="api_only",
    ),
    "hsreplay_meta_legend_1d_firecrawl": SourceContract(
        source_id="hsreplay_meta_legend_1d_firecrawl",
        structured_type="hsreplay_meta_archetypes",
        min_rows=20,
        critical_fields=("winrate", "popularity", "games"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.45,
        volatility="daily",
        fallback_policy="api_only",
    ),
    "hsreplay_meta_diamond_4to1_1d_firecrawl": SourceContract(
        source_id="hsreplay_meta_diamond_4to1_1d_firecrawl",
        structured_type="hsreplay_meta_archetypes",
        min_rows=20,
        critical_fields=("winrate", "popularity", "games"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.45,
        volatility="daily",
        fallback_policy="api_only",
    ),
    "vicious_syndicate_live_beta": SourceContract(
        source_id="vicious_syndicate_live_beta",
        structured_type="vicious_live",
        allow_browser_fallback=False,
        min_rows=20,
        critical_fields=("deck", "winrate"),
        min_field_fill_rate=0.70,
        regression_drop_ratio=0.30,
        fallback_policy="api_only",
    ),
    "vicious_syndicate_radars": SourceContract(
        source_id="vicious_syndicate_radars",
        structured_type="vicious_syndicate_radars",
        allow_browser_fallback=True,
        min_rows=5,
        critical_fields=("nodes", "edges"),
        min_field_fill_rate=0.60,
        regression_drop_ratio=0.35,
        fallback_policy="html_allowed",
    ),
    "hsreplay_decks_trending": SourceContract(
        source_id="hsreplay_decks_trending",
        structured_type="trending_decks",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=True,
        min_rows=5,
        critical_fields=("name", "winrate", "games"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.50,
        fallback_policy="html_allowed",
        recommendation="Trending page is localized; deck rows must parse in both EN and RU UI.",
    ),
    "hsreplay_arena": SourceContract(
        source_id="hsreplay_arena",
        structured_type="arena_class_matrix",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=8,
        critical_fields=("win_rate", "pick_rate"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.30,
        fallback_policy="api_only",
    ),
    "hsreplay_arena_class_pages_firecrawl": SourceContract(
        source_id="hsreplay_arena_class_pages_firecrawl",
        structured_type="arena_class_pages",
        min_rows=10,
        critical_fields=("win_rate", "pick_rate", "pct_7_plus", "num_drafts"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.30,
        volatility="daily",
        fallback_policy="api_only",
    ),
    "hsreplay_battlegrounds_comps": SourceContract(
        source_id="hsreplay_battlegrounds_comps",
        structured_type="bg_comps",
        allow_browser_fallback=True,
        min_rows=5,
        critical_fields=("name",),
        min_field_fill_rate=0.80,
        # HSReplay hides retired guides during a patch reset. Publishing only
        # visible live guides can legitimately shrink the catalog from 27 to 8.
        regression_drop_ratio=0.85,
        fallback_policy="html_allowed",
    ),
    "firestone_battlegrounds_comps": SourceContract(
        source_id="firestone_battlegrounds_comps",
        structured_type="bg_comps",
        allow_browser_fallback=False,
        # Firestone initially publishes a deliberately small composition set
        # after a Battlegrounds patch. Five rows still reject an empty or
        # truncated response while allowing the first seven live strategies.
        min_rows=5,
        critical_fields=("name", "main_cards"),
        min_field_fill_rate=0.80,
        # A confirmed patch reset can shrink the curated strategy list from
        # about 30 rows to seven. Keep the five-row floor as the hard guard,
        # while allowing that expected one-time 76.7% contraction.
        regression_drop_ratio=0.85,
        fallback_policy="api_only",
    ),
    "firestone_standard": SourceContract(
        source_id="firestone_standard",
        structured_type="firestone_standard",
        allow_browser_fallback=False,
        min_rows=20,
        min_collection_rows=(("decks", 10), ("archetypes", 10)),
        critical_fields=(
            "archetype_id",
            "archetype_name",
            "player_class",
            "games",
            "wins",
            "winrate",
            "core_cards",
        ),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.50,
        volatility="patch",
        fallback_policy="api_only",
        recommendation=(
            "Use the two direct ZeroToHeroes Standard overview snapshots; "
            "preserve the previous valid dataset if either collection regresses."
        ),
    ),
    "firestone_battlegrounds_cards": SourceContract(
        source_id="firestone_battlegrounds_cards",
        structured_type="bg_card_stats",
        allow_browser_fallback=False,
        min_rows=100,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
    ),
    "firestone_battlegrounds_spells": SourceContract(
        source_id="firestone_battlegrounds_spells",
        structured_type="bg_card_stats",
        allow_browser_fallback=False,
        min_rows=30,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
    ),
    "heartharena_tierlist": SourceContract(
        source_id="heartharena_tierlist",
        structured_type="heartharena_tierlist",
        allow_browser_fallback=True,
        min_rows=300,
        critical_fields=("name",),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.35,
        fallback_policy="html_allowed",
    ),
    "metastats_decks": SourceContract(
        source_id="metastats_decks",
        structured_type="metastats_decks",
        allow_browser_fallback=False,
        min_rows=40,
        critical_fields=("archetype_name", "win_rate", "games"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
    ),
    "metastats_matchups": SourceContract(
        source_id="metastats_matchups",
        structured_type="metastats_matchups",
        allow_browser_fallback=False,
        min_rows=50,
        critical_fields=("archetype", "vs", "winrate"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
    ),
    "hearthstone_decks": SourceContract(
        source_id="hearthstone_decks",
        structured_type="hearthstone_decks",
        allow_browser_fallback=True,
        min_rows=40,
        critical_fields=("title", "url", "format", "deck_code"),
        min_field_fill_rate=0.95,
        regression_drop_ratio=0.35,
        fallback_policy="validated_html_fallback",
        recommendation=(
            "Prefer the two WordPress REST category feeds; accept HTML only as a "
            "validated fallback and preserve last-known-good deck codes."
        ),
    ),
}

for _trinket_slice_source_id in TRINKET_SLICE_SOURCE_IDS:
    CONTRACTS[_trinket_slice_source_id] = SourceContract(
        source_id=_trinket_slice_source_id,
        structured_type="bg_trinkets",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=160,
        critical_fields=("name", "trinket_id", "description", "pick_rate", "avg_placement"),
        min_field_fill_rate=0.90,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
        recommendation=(
            "HSReplay combined trinket JSON slice; retain both Lesser and Greater "
            "rows with canonical card identities."
        ),
    )


for _sid in (
    "firestone_arena_cards_normal",
    "firestone_arena_cards_underground",
):
    CONTRACTS.setdefault(
        _sid,
        SourceContract(
            source_id=_sid,
            structured_type="arena_card_tiers",
            allow_browser_fallback=False,
            min_rows=300,
            critical_fields=("name", "deck_winrate"),
            min_field_fill_rate=0.80,
            regression_drop_ratio=0.35,
            fallback_policy="api_only",
        ),
    )

# The first days of a constructed patch legitimately contain fewer archetypes
# above HSGuru's min_games=100 threshold. Keep a stronger absolute/field gate
# for the two public Legend views while allowing the verified patch reset.
for _sid in ("hsguru_meta_standard_legend", "hsguru_meta_wild_legend"):
    CONTRACTS[_sid] = SourceContract(
        source_id=_sid,
        structured_type="meta",
        allow_browser_fallback=True,
        min_rows=10,
        critical_fields=("Archetype", "Winrate↓", "Popularity"),
        min_field_fill_rate=0.95,
        regression_drop_ratio=0.75,
        fallback_policy="html_allowed",
        recommendation="Accept a verified early-patch archetype reset only when every visible row retains its identity and performance metrics.",
        min_html_bytes=25_000,
        early_min_html_bytes=2_000,
    )

for _sid in (
    "firestone_arena_legendaries_normal",
    "firestone_arena_legendaries_underground",
):
    CONTRACTS.setdefault(
        _sid,
        SourceContract(
            source_id=_sid,
            structured_type="arena_card_tiers",
            allow_browser_fallback=False,
            min_rows=40,
            critical_fields=("name", "deck_winrate"),
            min_field_fill_rate=0.80,
            regression_drop_ratio=0.35,
            fallback_policy="api_only",
        ),
    )

CONTRACTS[HSGURU_STREAMER_ROLLING_SOURCE_ID] = SourceContract(
    source_id=HSGURU_STREAMER_ROLLING_SOURCE_ID,
    structured_type="streamer_decks",
    allow_browser_fallback=True,
    min_rows=3,
    critical_fields=("Deck", "Streamer", "deck_code"),
    min_field_fill_rate=1.0,
    # The endpoint is explicitly a rolling last-60-minutes activity window.
    # Three rows is the normal floor; the report admits one or two rows only as
    # verified low activity after every required field and deckstring passes.
    # An empty window always remains a publication failure.
    regression_drop_ratio=1.0,
    volatility="rolling_hour",
    fallback_policy="html_allowed",
    recommendation=(
        "Accept a one- or two-row low-activity window only when every row retains "
        "deck, streamer, and a decodable deck code; reject empty windows."
    ),
    min_html_bytes=8_000,
)

CONTRACTS["hsguru_archetype_analysis"] = SourceContract(
    source_id="hsguru_archetype_analysis",
    structured_type="hsguru_archetype_analysis",
    allow_browser_fallback=True,
    min_rows=1,
    critical_fields=("format", "archetype"),
    min_field_fill_rate=1.0,
    regression_drop_ratio=0.30,
    volatility="daily",
    fallback_policy="preserve_previous_good",
    recommendation=(
        "Publish only an exact, semantically validated target set; incomplete "
        "provider runs must preserve the previous good snapshot."
    ),
    min_html_bytes=2_000,
)

for _sid in (
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
):
    CONTRACTS.setdefault(
        _sid,
        SourceContract(
            source_id=_sid,
            structured_type=(
                "streamer_decks"
                if "streamer_decks" in _sid
                else "matchups"
                if "matchups" in _sid
                else "meta"
            ),
            allow_browser_fallback=True,
            min_rows=3,
            regression_drop_ratio=0.30,
            fallback_policy="html_allowed",
            recommendation="Investigate HSGuru embedded/internal API and migrate away from hydrated browser pages.",
            min_html_bytes=8_000 if "streamer_decks" in _sid else 25_000,
            early_min_html_bytes=None if "streamer_decks" in _sid else 2_000,
        ),
)

for source_id in STANDARD_HSREPLAY_CARD_PERIOD_SOURCE_IDS[1:]:
    is_current_patch = source_id.endswith("_patch")
    CONTRACTS[source_id] = SourceContract(
        source_id=source_id,
        structured_type="card_stats",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        # A fresh patch initially contains only cards that have accumulated a
        # sample since the reset. Keep an absolute floor, but do not compare it
        # to mature rolling windows.
        min_rows=450 if is_current_patch else 600,
        critical_fields=("deck_winrate", "deck_popularity"),
        min_field_fill_rate=0.55,
        regression_drop_ratio=0.85 if is_current_patch else 0.50,
        volatility="daily",
        fallback_policy="api_only",
        recommendation="Preserve the previous valid Standard period snapshot on severe metric or row-count regression.",
    )

for source_id in WILD_HSREPLAY_CARD_PERIOD_SOURCE_IDS[1:]:
    is_current_patch = source_id.endswith("_patch")
    CONTRACTS[source_id] = SourceContract(
        source_id=source_id,
        structured_type="card_stats",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=700,
        critical_fields=("deck_winrate", "deck_popularity"),
        min_field_fill_rate=0.45,
        regression_drop_ratio=0.85 if is_current_patch else 0.50,
        volatility="daily",
        fallback_policy="api_only",
        recommendation="Preserve the previous valid Wild period snapshot on severe metric or row-count regression.",
    )


def get_contract(source_id: str) -> SourceContract | None:
    return CONTRACTS.get(source_id)


def allows_browser_fallback(source_id: str, *, default: bool = True) -> bool:
    contract = get_contract(source_id)
    if contract is None:
        return default
    return contract.allow_browser_fallback


def preferred_channels_for_source(source_id: str | None) -> tuple[str, ...]:
    if not source_id:
        return ()
    contract = get_contract(source_id)
    return contract.preferred_channels if contract else ()


def regression_drop_ratio_for_source(source_id: str, default: float) -> float:
    contract = get_contract(source_id)
    if contract and contract.regression_drop_ratio is not None:
        return max(default, contract.regression_drop_ratio)
    return default


def _rows_for_structured(structured: dict[str, Any]) -> list[dict[str, Any]]:
    stype = structured.get("type")
    if stype in {"card_stats", "arena_card_tiers"}:
        return [row for row in (structured.get("cards") or []) if isinstance(row, dict)]
    if stype == "bg_heroes":
        return [row for row in (structured.get("heroes") or []) if isinstance(row, dict)]
    if stype == "bg_minions":
        return [row for row in (structured.get("minions") or []) if isinstance(row, dict)]
    if stype == "bg_compositions":
        return [row for row in (structured.get("compositions") or []) if isinstance(row, dict)]
    if stype == "bg_trinkets":
        return [row for row in (structured.get("trinkets") or []) if isinstance(row, dict)]
    if stype == "arena_winning_decks":
        return [row for row in (structured.get("decks") or []) if isinstance(row, dict)]
    if stype == "arena_legendary_groups":
        return [row for row in (structured.get("groups") or []) if isinstance(row, dict)]
    if stype == "hsreplay_meta_archetypes":
        return [
            row
            for class_group in (structured.get("classes") or [])
            if isinstance(class_group, dict)
            for row in (class_group.get("archetypes") or [])
            if isinstance(row, dict)
        ]
    if stype == "vicious_live":
        return [
            deck
            for bracket in (structured.get("tier_list") or [])
            if isinstance(bracket, dict)
            for deck in (bracket.get("decks") or [])
            if isinstance(deck, dict)
        ]
    if stype == "vicious_syndicate_radars":
        return [row for row in (structured.get("radars") or []) if isinstance(row, dict)]
    if stype == "hearthstone_decks":
        return [row for row in (structured.get("decks") or []) if isinstance(row, dict)]
    if stype == "meta":
        return [row for row in (structured.get("strategies") or []) if isinstance(row, dict)]
    if stype == "matchups":
        return [row for row in (structured.get("matchups") or []) if isinstance(row, dict)]
    if stype == "streamer_decks":
        return [row for row in (structured.get("rows") or []) if isinstance(row, dict)]
    if stype == "hsguru_archetype_analysis":
        return [
            row
            for row in (structured.get("archetypes") or [])
            if isinstance(row, dict)
        ]
    if stype == "bg_card_stats":
        return [
            row
            for tier_rows in (structured.get("tiers") or {}).values()
            if isinstance(tier_rows, list)
            for row in tier_rows
            if isinstance(row, dict)
        ]
    if stype == "bg_comps":
        return [row for row in (structured.get("comps") or []) if isinstance(row, dict)]
    if stype == "heartharena_tierlist":
        return [
            card
            for cls in (structured.get("classes") or [])
            if isinstance(cls, dict)
            for card in (cls.get("cards") or [])
            if isinstance(card, dict)
        ]
    if stype == "metastats_decks":
        return [row for row in (structured.get("decks") or []) if isinstance(row, dict)]
    if stype == "firestone_standard":
        return [
            row
            for collection in ("decks", "archetypes")
            for row in (structured.get(collection) or [])
            if isinstance(row, dict)
        ]
    if stype == "metastats_matchups":
        return [row for row in (structured.get("matchups") or []) if isinstance(row, dict)]
    if stype == "trending_decks":
        return [row for row in (structured.get("decks") or []) if isinstance(row, dict)]
    if stype == "arena_class_matrix":
        return [row for row in (structured.get("classes") or []) if isinstance(row, dict)]
    if stype == "arena_class_pages":
        return [row for row in (structured.get("classes") or []) if isinstance(row, dict)]
    return []


def _field_present(row: dict[str, Any], field: str) -> bool:
    value = row.get(field)
    if value is None:
        return False
    if isinstance(value, str):
        stripped = value.strip()
        if field in {"hero", "name"} and stripped in {"", "-", "—"}:
            return False
        return bool(stripped)
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _bounded_percent_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            value = float(text.replace(",", "."))
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    number = float(value)
    return number if 0.0 <= number <= 100.0 else None


def _strict_bg_minion_domain_errors(rows: list[dict[str, Any]]) -> int:
    errors = 0
    for row in rows:
        row_invalid = False
        for field, minimum, maximum in (
            ("avg_placement_with", 1.0, 8.0),
            ("avg_placement_without", 1.0, 8.0),
            ("impact", -7.0, 7.0),
        ):
            value = row.get(field)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not minimum <= float(value) <= maximum
            ):
                row_invalid = True
        for field in ("win_share", "popularity"):
            value = row.get(field)
            if value is not None and _bounded_percent_value(value) is None:
                row_invalid = True
        for field in ("games_with_minion", "games_without_minion"):
            value = row.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                row_invalid = True
        avg_with = row.get("avg_placement_with")
        avg_without = row.get("avg_placement_without")
        impact = row.get("impact")
        if all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            for value in (avg_with, avg_without, impact)
        ) and abs(float(impact) - (float(avg_without) - float(avg_with))) > 0.03:
            row_invalid = True
        rounds = row.get("combat_rounds")
        if rounds is not None and not isinstance(rounds, list):
            row_invalid = True
            rounds = []
        if isinstance(rounds, list) and rounds:
            round_totals = {
                "games_with_minion": 0,
                "games_without_minion": 0,
            }
            for round_row in rounds:
                if not isinstance(round_row, dict):
                    row_invalid = True
                    continue
                for field in round_totals:
                    value = round_row.get(field)
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or value < 0
                    ):
                        row_invalid = True
                    else:
                        round_totals[field] += value
                round_avg_with = round_row.get("avg_placement_with")
                round_avg_without = round_row.get("avg_placement_without")
                round_impact = round_row.get("impact")
                if all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in (
                        round_avg_with,
                        round_avg_without,
                        round_impact,
                    )
                ) and abs(
                    float(round_impact)
                    - (float(round_avg_without) - float(round_avg_with))
                ) > 0.03:
                    row_invalid = True
            for field, expected in round_totals.items():
                observed = row.get(field)
                if (0 if observed is None else observed) != expected:
                    row_invalid = True
        errors += int(row_invalid)
    return errors


def _strict_bg_composition_domain(
    rows: list[dict[str, Any]],
) -> tuple[int, float | None]:
    errors = 0
    first_place_values: list[float] = []
    for row in rows:
        row_invalid = False
        avg_placement = row.get("avg_placement")
        if (
            isinstance(avg_placement, bool)
            or not isinstance(avg_placement, (int, float))
            or not math.isfinite(float(avg_placement))
            or not 1 <= float(avg_placement) <= 8
        ):
            row_invalid = True
        for field in ("first_place", "popularity"):
            value = _bounded_percent_value(row.get(field))
            if value is None:
                row_invalid = True
            elif field == "first_place":
                first_place_values.append(value)
        distribution = row.get("placement_distribution")
        if not isinstance(distribution, list) or len(distribution) != 8:
            row_invalid = True
        else:
            rates = [_bounded_percent_value(value) for value in distribution]
            if any(value is None for value in rates) or abs(
                sum(value or 0.0 for value in rates) - 100.0
            ) > 0.1:
                row_invalid = True
        games = row.get("games")
        if isinstance(games, bool) or not isinstance(games, int) or games < 0:
            row_invalid = True
        errors += int(row_invalid)
    first_place_total = (
        round(sum(first_place_values), 4)
        if len(first_place_values) == len(rows)
        else None
    )
    return errors, first_place_total


def completeness_schema_version(structured: dict[str, Any]) -> int | None:
    value = structured.get("completeness_schema_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def uses_completeness_schema(structured: dict[str, Any]) -> bool:
    version = completeness_schema_version(structured)
    return version == COMPLETENESS_SCHEMA_VERSION


def field_availability_status(
    source_id: str,
    row: dict[str, Any],
    field: str,
    *,
    require_descriptor: bool = False,
) -> tuple[str, str | None]:
    """Classify a metric without treating an explained upstream null as data.

    ``available`` means the metric itself was retrieved.  An absent metric is
    only ``explained_unavailable`` when the parser emitted a coherent
    descriptor containing a source/field-specific allow-listed reason.  A
    contradictory or malformed descriptor fails closed as a conflict.
    """

    present = _field_present(row, field)
    availability = row.get("field_availability")
    if availability is not None and not isinstance(availability, dict):
        return "availability_conflict", "field_availability_must_be_an_object"
    if not isinstance(availability, dict) or field not in availability:
        if require_descriptor:
            return "availability_conflict", "descriptor_missing"
        return ("available", None) if present else ("unexplained_missing", None)
    descriptor = availability[field]
    if not isinstance(descriptor, dict):
        return "availability_conflict", "descriptor_must_be_an_object"

    available = descriptor.get("available")
    reason = descriptor.get("reason")
    if not isinstance(available, bool):
        return "availability_conflict", "available_must_be_boolean"
    if available:
        if reason not in (None, ""):
            return "availability_conflict", "available_metric_has_reason"
        if not present:
            return "availability_conflict", "available_metric_is_missing"
        return "available", None

    if present:
        return "availability_conflict", "unavailable_metric_is_present"
    allowed_reasons = FIELD_UNAVAILABLE_REASONS.get(source_id, {}).get(field, frozenset())
    if isinstance(reason, str) and reason in allowed_reasons:
        return "explained_unavailable", reason
    unexplained_reasons = FIELD_UNEXPLAINED_REASONS.get(source_id, {}).get(
        field,
        frozenset(),
    )
    if isinstance(reason, str) and reason in unexplained_reasons:
        return "unexplained_missing", reason
    if not isinstance(reason, str) or not reason.strip():
        return "unexplained_missing", None
    return "availability_conflict", "reason_not_allowed"


def is_decodable_deck_code(value: Any) -> bool:
    """Return true only for a complete Hearthstone deckstring.

    This deliberately uses the canonical decoder instead of accepting an
    ``AAE``-shaped string. Provider error pages and partially hydrated rows can
    otherwise satisfy the ordinary non-empty-field check.
    """

    if not isinstance(value, str) or not value.strip():
        return False
    try:
        from hearthstone.deckstrings import Deck

        Deck.from_deckstring(value.strip())
    except Exception:  # noqa: BLE001 - malformed upstream data fails closed
        return False
    return True


def _legendary_bucket_coverage_report(evidence: dict[str, Any]) -> dict[str, Any]:
    coverage = evidence.get("bucket_coverage")
    report: dict[str, Any] = {
        "valid": True,
        "expected_buckets": None,
        "observed_buckets": None,
        "missing_buckets": None,
        "unknown_buckets": None,
        "duplicate_bucket_package_keys": None,
        "retrieval_completeness_rate": 0.0,
        "warnings": [],
    }
    if not isinstance(coverage, dict):
        report["valid"] = False
        report["warnings"].append("row_retrieval.bucket_coverage must be an object")
        return report

    values: dict[str, list[str]] = {}
    for field_name in (
        "expected_buckets",
        "observed_buckets",
        "missing_buckets",
        "unknown_buckets",
        "duplicate_bucket_package_keys",
    ):
        value = coverage.get(field_name)
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(value) != len(set(value))
        ):
            report["valid"] = False
            report["warnings"].append(
                f"row_retrieval.bucket_coverage.{field_name} must be a unique string list"
            )
            continue
        values[field_name] = value
        report[field_name] = value

    expected = list(ARENA_LEGENDARY_EXPECTED_BUCKETS)
    if values.get("expected_buckets") != expected:
        report["valid"] = False
        report["warnings"].append(
            "row_retrieval.bucket_coverage.expected_buckets does not match the full endpoint contract"
        )
    observed = values.get("observed_buckets", [])
    missing = values.get("missing_buckets", [])
    unknown = values.get("unknown_buckets", [])
    duplicate_keys = values.get("duplicate_bucket_package_keys", [])
    if observed != [bucket for bucket in expected if bucket in observed]:
        report["valid"] = False
        report["warnings"].append(
            "row_retrieval.bucket_coverage.observed_buckets has invalid order or names"
        )
    if missing != [bucket for bucket in expected if bucket not in observed]:
        report["valid"] = False
        report["warnings"].append(
            "row_retrieval.bucket_coverage.missing_buckets does not reconcile"
        )
    if missing:
        report["valid"] = False
        report["warnings"].append(
            "row_retrieval.bucket_coverage is missing required buckets: "
            + ", ".join(missing)
        )
    if unknown:
        report["valid"] = False
        report["warnings"].append(
            "row_retrieval.bucket_coverage has unknown buckets: "
            + ", ".join(unknown)
        )
    if duplicate_keys:
        report["valid"] = False
        report["warnings"].append(
            "row_retrieval.bucket_coverage has duplicate (bucket, package_key) rows"
        )
    denominator = len(expected) + len(unknown) + len(duplicate_keys)
    report["retrieval_completeness_rate"] = round(
        len(observed) / denominator if denominator else 0.0,
        4,
    )
    return report


def _row_retrieval_report(
    source_id: str,
    structured: dict[str, Any],
    *,
    expected_normalized_rows: int,
) -> dict[str, Any]:
    evidence = structured.get("row_retrieval")
    report: dict[str, Any] = {
        "valid": True,
        "raw_rows": None,
        "eligible_rows": None,
        "normalized_rows": None,
        "explained_drops": None,
        "unexplained_drops": None,
        "drop_reasons": None,
        "bucket_coverage": None,
        "retrieval_completeness_rate": 0.0,
        "warnings": [],
    }
    if not isinstance(evidence, dict):
        report["valid"] = False
        report["warnings"].append("row_retrieval must be an object")
        return report

    count_fields = (
        "raw_rows",
        "eligible_rows",
        "normalized_rows",
        "explained_drops",
        "unexplained_drops",
    )
    counts: dict[str, int] = {}
    for field in count_fields:
        value = evidence.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            report["valid"] = False
            report["warnings"].append(f"row_retrieval.{field} must be a non-negative integer")
            continue
        counts[field] = value
        report[field] = value

    reasons = evidence.get("drop_reasons")
    reason_totals: dict[str, int] = {}
    if not isinstance(reasons, dict):
        report["valid"] = False
        report["warnings"].append("row_retrieval.drop_reasons must be an object")
    else:
        for category in ("explained", "unexplained"):
            category_reasons = reasons.get(category)
            if not isinstance(category_reasons, dict):
                report["valid"] = False
                report["warnings"].append(
                    f"row_retrieval.drop_reasons.{category} must be an object"
                )
                continue
            total = 0
            for reason, count in category_reasons.items():
                if (
                    not isinstance(reason, str)
                    or not reason.strip()
                    or isinstance(count, bool)
                    or not isinstance(count, int)
                    or count < 0
                ):
                    report["valid"] = False
                    report["warnings"].append(
                        f"row_retrieval.drop_reasons.{category} is invalid"
                    )
                    continue
                if (
                    category == "explained"
                    and reason
                    not in EXPLAINED_ROW_DROP_REASONS.get(source_id, frozenset())
                ):
                    report["valid"] = False
                    report["warnings"].append(
                        "row_retrieval explained reason "
                        f"{reason!r} is not allow-listed for {source_id}"
                    )
                total += count
            reason_totals[category] = total
        report["drop_reasons"] = reasons

    if len(counts) == len(count_fields):
        raw = counts["raw_rows"]
        eligible = counts["eligible_rows"]
        normalized = counts["normalized_rows"]
        explained = counts["explained_drops"]
        unexplained = counts["unexplained_drops"]
        if not raw >= eligible >= normalized:
            report["valid"] = False
            report["warnings"].append(
                "row_retrieval counts must satisfy raw >= eligible >= normalized"
            )
        if raw - normalized != explained + unexplained:
            report["valid"] = False
            report["warnings"].append(
                "row_retrieval drops do not reconcile with raw/normalized rows"
            )
        if normalized != expected_normalized_rows:
            report["valid"] = False
            report["warnings"].append(
                "row_retrieval.normalized_rows does not match published rows"
            )
        if reason_totals.get("explained") != explained:
            report["valid"] = False
            report["warnings"].append(
                "row_retrieval explained drop reasons do not reconcile"
            )
        if reason_totals.get("unexplained") != unexplained:
            report["valid"] = False
            report["warnings"].append(
                "row_retrieval unexplained drop reasons do not reconcile"
            )
        report["retrieval_completeness_rate"] = round(
            (normalized + explained) / raw if raw else 0.0,
            4,
        )
    if source_id == "hsreplay_arena_legendaries":
        coverage_report = _legendary_bucket_coverage_report(evidence)
        report["bucket_coverage"] = coverage_report
        report["retrieval_completeness_rate"] = min(
            float(report["retrieval_completeness_rate"]),
            float(coverage_report["retrieval_completeness_rate"]),
        )
        if not coverage_report["valid"]:
            report["valid"] = False
            report["warnings"].extend(coverage_report["warnings"])
    return report


def _identity_quality_report(
    rows: list[dict[str, Any]],
    field_path: str,
) -> dict[str, Any]:
    identities: list[tuple[str, str | int]] = []
    missing = 0
    path_parts = field_path.split(".")
    for row in rows:
        value: Any = row
        for part in path_parts:
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, str) and value.strip():
            identities.append(("string", value.strip()))
        elif isinstance(value, int) and not isinstance(value, bool):
            identities.append(("integer", value))
        else:
            missing += 1
    unique = len(set(identities))
    duplicates = len(identities) - unique
    total = len(rows)
    return {
        "field": field_path,
        "total": total,
        "unique": unique,
        "missing": missing,
        "duplicates": duplicates,
        "retrieval_completeness_rate": round(
            unique / total if total else 0.0,
            4,
        ),
        "complete": bool(total) and not missing and not duplicates,
    }


def _matchup_identity_quality_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    identities: list[tuple[str, str]] = []
    missing = 0
    for row in rows:
        archetype = row.get("archetype")
        opponent = row.get("vs")
        if (
            isinstance(archetype, str)
            and archetype.strip()
            and isinstance(opponent, str)
            and opponent.strip()
        ):
            identities.append((archetype.strip(), opponent.strip()))
        else:
            missing += 1
    unique = len(set(identities))
    duplicates = len(identities) - unique
    total = len(rows)
    return {
        "fields": ["archetype", "vs"],
        "total": total,
        "unique": unique,
        "missing": missing,
        "duplicates": duplicates,
        "retrieval_completeness_rate": round(
            unique / total if total else 0.0,
            4,
        ),
        "complete": bool(total) and not missing and not duplicates,
    }


def contract_quality_report(
    source_id: str,
    structured: dict[str, Any],
) -> dict[str, Any]:
    contract = get_contract(source_id)
    rows = _rows_for_structured(structured)
    report: dict[str, Any] = {
        "source_id": source_id,
        "structured_type": structured.get("type"),
        "rows_total": len(rows),
        "contract_present": contract is not None,
        "critical_fields": {},
        "quality_score": None,
        "metric_availability_score": None,
        "retrieval_completeness_score": None,
        "retrieval_complete": None,
        "completeness_schema_version": completeness_schema_version(structured),
        "upstream_freshness_status": (
            structured.get("upstream_freshness", {}).get("status")
            if isinstance(structured.get("upstream_freshness"), dict)
            else None
        ),
        "population_completeness": structured.get("population_completeness"),
        "class_scope": None,
        "row_retrieval": None,
        "identity_checks": {},
        "ok": True,
        "warnings": [],
        "minimum_rows": None,
        "minimum_collections": {},
        "low_activity": False,
    }
    if contract is None:
        return report
    if "completeness_schema_version" in structured:
        version = completeness_schema_version(structured)
        if version != COMPLETENESS_SCHEMA_VERSION:
            report["ok"] = False
            report["warnings"].append(
                "unsupported completeness_schema_version; expected "
                f"{COMPLETENESS_SCHEMA_VERSION}, got "
                f"{structured.get('completeness_schema_version')!r}"
            )
    strict_completeness = uses_completeness_schema(structured)
    if contract.structured_type and structured.get("type") != contract.structured_type:
        report["ok"] = False
        report["warnings"].append(
            f"expected structured type {contract.structured_type}, got {structured.get('type')}"
        )
    if contract.min_rows is not None:
        minimum_rows = effective_contract_min_rows(source_id, contract.min_rows)
        report["minimum_rows"] = minimum_rows
        low_activity_candidate = (
            source_id == HSGURU_STREAMER_ROLLING_SOURCE_ID
            and contract.volatility == "rolling_hour"
            and 0 < len(rows) < minimum_rows
        )
        if len(rows) < minimum_rows and not low_activity_candidate:
            report["ok"] = False
            report["warnings"].append(f"too few rows ({len(rows)} < {minimum_rows})")
    for collection, minimum in contract.min_collection_rows:
        value = structured.get(collection)
        count = sum(1 for row in value if isinstance(row, dict)) if isinstance(value, list) else 0
        report["minimum_collections"][collection] = {
            "rows": count,
            "minimum_rows": minimum,
        }
        if count < minimum:
            report["ok"] = False
            report["warnings"].append(
                f"{collection} has too few rows ({count} < {minimum})"
            )
    rates: list[float] = []
    retrieval_rates: list[float] = []
    if strict_completeness:
        report["retrieval_complete"] = True
        if source_id == "hsreplay_arena_cards_advanced":
            primary_class = structured.get("primary_class")
            selected_class = structured.get("selected_class")
            exact_match = (
                primary_class == "ALL"
                and selected_class == primary_class
            )
            report["class_scope"] = {
                "primary_class": primary_class,
                "selected_class": selected_class,
                "exact_match": exact_match,
            }
            row_retrieval = structured.get("row_retrieval")
            row_scope = None
            if isinstance(row_retrieval, dict):
                row_scope = row_retrieval.get("scope")
            if not exact_match:
                report["ok"] = False
                report["retrieval_complete"] = False
                report["warnings"].append(
                    "primary_class and selected_class must both be ALL"
                )
            elif exact_match and row_scope != f"primary_class:{selected_class}":
                report["ok"] = False
                report["retrieval_complete"] = False
                report["warnings"].append(
                    "row_retrieval.scope must match selected_class"
                )
        if source_id in HSREPLAY_FRESHNESS_GATED_SOURCE_IDS:
            freshness = structured.get("upstream_freshness")
            if isinstance(freshness, dict):
                freshness_status = freshness.get("status")
                freshness_reason = freshness.get("reason")
                freshness_failure: str | None = None
                if freshness_status == "stale":
                    freshness_failure = "upstream snapshot is known stale"
                elif freshness_status == "unknown" and (
                    freshness_reason not in HSREPLAY_UNVERIFIED_PUBLISH_REASONS
                ):
                    freshness_failure = (
                        "upstream freshness evidence is invalid: "
                        f"{freshness_reason or 'unknown_reason'}"
                    )
                elif freshness_status not in {"fresh", "unknown"}:
                    freshness_failure = "upstream freshness status is invalid"
                if structured.get("population_completeness") not in (
                    None,
                    "unverifiable",
                ):
                    freshness_failure = "population completeness claim is not verifiable"
                if freshness_failure:
                    report["ok"] = False
                    report["retrieval_complete"] = False
                    report["warnings"].append(freshness_failure)
    for field in contract.critical_fields:
        total = len(rows)
        filled = sum(1 for row in rows if _field_present(row, field))
        rate = (filled / total) if total else 0.0
        strict_availability = field in FIELD_UNAVAILABLE_REASONS.get(source_id, {})
        statuses = (
            [
                field_availability_status(
                    source_id,
                    row,
                    field,
                    require_descriptor=strict_availability,
                )[0]
                for row in rows
            ]
            if strict_completeness
            else []
        )
        explained_unavailable = (
            statuses.count("explained_unavailable") if strict_completeness else None
        )
        unexplained_missing = (
            statuses.count("unexplained_missing") if strict_completeness else None
        )
        availability_conflicts = (
            statuses.count("availability_conflict") if strict_completeness else None
        )
        retrieval_rate = (
            (
                statuses.count("available")
                + statuses.count("explained_unavailable")
            )
            / total
            if strict_completeness and total
            else None
        )
        report["critical_fields"][field] = {
            "filled": filled,
            "total": total,
            "rate": round(rate, 4),
            "metric_availability_rate": round(rate, 4),
            "explained_unavailable": explained_unavailable,
            "unexplained_missing": unexplained_missing,
            "availability_conflicts": availability_conflicts,
            "retrieval_completeness_rate": (
                round(retrieval_rate, 4) if retrieval_rate is not None else None
            ),
        }
        rates.append(rate)
        if retrieval_rate is not None:
            retrieval_rates.append(retrieval_rate)
        gate_rate = retrieval_rate if strict_completeness else rate
        if (
            contract.min_field_fill_rate
            and gate_rate is not None
            and gate_rate < contract.min_field_fill_rate
        ):
            report["ok"] = False
            rate_label = "retrieval rate" if strict_completeness else "fill rate"
            report["warnings"].append(
                f"{field} {rate_label} {gate_rate:.2%} below "
                f"{contract.min_field_fill_rate:.0%}"
            )
        if strict_completeness and (
            not total or unexplained_missing or availability_conflicts
        ):
            report["retrieval_complete"] = False
        if strict_completeness and unexplained_missing:
            report["ok"] = False
            report["warnings"].append(
                f"{field} has {unexplained_missing} unexplained missing values"
            )
        if strict_completeness and availability_conflicts:
            report["ok"] = False
            report["warnings"].append(
                f"{field} has {availability_conflicts} availability conflicts"
            )
    if source_id == "hsreplay_arena_legendaries" and strict_completeness:
        # A package can look complete at the top level while one of its class
        # slices is missing metrics. Treat every advertised class bucket as
        # part of the retrieval denominator so the aggregate cannot hide it.
        class_buckets: list[dict[str, Any]] = []
        for row in rows:
            by_class = row.get("by_class")
            if not isinstance(by_class, dict) or not by_class:
                class_buckets.append({})
                continue
            class_buckets.extend(
                bucket if isinstance(bucket, dict) else {}
                for bucket in by_class.values()
            )

        for field in ("winrate", "pick_rate", "offer_rate", "score"):
            total = len(class_buckets)
            filled = sum(
                1 for bucket in class_buckets if _field_present(bucket, field)
            )
            rate = (filled / total) if total else 0.0
            statuses = [
                field_availability_status(
                    source_id,
                    bucket,
                    field,
                    require_descriptor=(
                        field in FIELD_UNAVAILABLE_REASONS.get(source_id, {})
                    ),
                )[0]
                for bucket in class_buckets
            ]
            explained_unavailable = statuses.count("explained_unavailable")
            unexplained_missing = statuses.count("unexplained_missing")
            availability_conflicts = statuses.count("availability_conflict")
            retrieval_rate = (
                (
                    statuses.count("available")
                    + statuses.count("explained_unavailable")
                )
                / total
                if total
                else 0.0
            )
            report_key = f"by_class.{field}"
            report["critical_fields"][report_key] = {
                "filled": filled,
                "total": total,
                "rate": round(rate, 4),
                "metric_availability_rate": round(rate, 4),
                "explained_unavailable": explained_unavailable,
                "unexplained_missing": unexplained_missing,
                "availability_conflicts": availability_conflicts,
                "retrieval_completeness_rate": round(retrieval_rate, 4),
            }
            rates.append(rate)
            retrieval_rates.append(retrieval_rate)
            if (
                contract.min_field_fill_rate
                and retrieval_rate < contract.min_field_fill_rate
            ):
                report["ok"] = False
                report["warnings"].append(
                    f"{report_key} retrieval rate "
                    f"{retrieval_rate:.2%} below {contract.min_field_fill_rate:.0%}"
                )
            if not total or unexplained_missing or availability_conflicts:
                report["ok"] = False
                report["retrieval_complete"] = False
            if unexplained_missing:
                report["warnings"].append(
                    f"{report_key} has {unexplained_missing} "
                    "unexplained missing values"
                )
            if availability_conflicts:
                report["warnings"].append(
                    f"{report_key} has {availability_conflicts} "
                    "availability conflicts"
                )
    if strict_completeness:
        identity_specs: list[tuple[str, list[dict[str, Any]], str]] = []
        if source_id == "hsreplay_arena_cards_advanced":
            identity_specs.append(("cards", rows, "card_id"))
        elif structured.get("type") == "card_stats":
            identity_specs.append(("cards", rows, "dbfId"))
        elif structured.get("type") == "meta":
            identity_specs.append(("strategies", rows, "Archetype"))
        elif structured.get("type") == "bg_trinkets":
            identity_specs.append(("trinkets", rows, "variant_key"))
        elif source_id == "hsreplay_battlegrounds_minions":
            identity_specs.append(("minions", rows, "minion_dbf_id"))
        elif source_id == "hsreplay_battlegrounds_compositions":
            identity_specs.append(("compositions", rows, "composition_id"))
        elif source_id == "hsreplay_arena_legendaries":
            identity_specs.append(("groups", rows, "key_card.card_id"))
        elif source_id == "firestone_standard":
            identity_specs.extend(
                (
                    (
                        "decks",
                        [
                            row
                            for row in (structured.get("decks") or [])
                            if isinstance(row, dict)
                        ],
                        "decklist",
                    ),
                    (
                        "archetypes",
                        [
                            row
                            for row in (structured.get("archetypes") or [])
                            if isinstance(row, dict)
                        ],
                        "archetype_id",
                    ),
                )
            )
        for collection, identity_rows, field_path in identity_specs:
            identity_report = _identity_quality_report(identity_rows, field_path)
            report["identity_checks"][collection] = identity_report
            retrieval_rates.append(
                float(identity_report["retrieval_completeness_rate"])
            )
            if not identity_report["complete"]:
                report["ok"] = False
                report["retrieval_complete"] = False
                report["warnings"].append(
                    f"{collection} identity {field_path} is incomplete or duplicated "
                    f"(missing={identity_report['missing']}, "
                    f"duplicates={identity_report['duplicates']})"
                )
        if structured.get("type") == "matchups":
            matchup_identity = _matchup_identity_quality_report(rows)
            report["identity_checks"]["matchups"] = matchup_identity
            retrieval_rates.append(
                float(matchup_identity["retrieval_completeness_rate"])
            )
            if not matchup_identity["complete"]:
                report["ok"] = False
                report["retrieval_complete"] = False
                report["warnings"].append(
                    "matchup (archetype, vs) identity is incomplete or duplicated "
                    f"(missing={matchup_identity['missing']}, "
                    f"duplicates={matchup_identity['duplicates']})"
                )
        if source_id == "hsreplay_battlegrounds_minions":
            domain_errors = _strict_bg_minion_domain_errors(rows)
            report["bg_minion_domain"] = {
                "invalid_rows": domain_errors,
                "total_rows": len(rows),
            }
            retrieval_rates.append(
                (len(rows) - domain_errors) / len(rows) if rows else 0.0
            )
            if domain_errors:
                report["ok"] = False
                report["retrieval_complete"] = False
                report["warnings"].append(
                    f"bg minion metrics have {domain_errors} physically invalid rows"
                )
        elif source_id == "hsreplay_battlegrounds_compositions":
            domain_errors, first_place_total = _strict_bg_composition_domain(rows)
            first_place_reconciles = (
                first_place_total is not None
                and abs(first_place_total - 100.0) <= 0.1
            )
            report["bg_composition_domain"] = {
                "invalid_rows": domain_errors,
                "total_rows": len(rows),
                "first_place_total": first_place_total,
                "first_place_reconciles": first_place_reconciles,
            }
            retrieval_rates.append(
                (len(rows) - domain_errors) / len(rows) if rows else 0.0
            )
            retrieval_rates.append(1.0 if first_place_reconciles else 0.0)
            if domain_errors or not first_place_reconciles:
                report["ok"] = False
                report["retrieval_complete"] = False
                report["warnings"].append(
                    "bg composition metrics are physically invalid or global "
                    "first_place share does not sum to 100"
                )
    if source_id == HSGURU_STREAMER_ROLLING_SOURCE_ID:
        total = len(rows)
        decodable = sum(
            1 for row in rows if is_decodable_deck_code(row.get("deck_code"))
        )
        rate = (decodable / total) if total else 0.0
        report["decodable_deck_codes"] = {
            "filled": decodable,
            "total": total,
            "rate": round(rate, 4),
        }
        rates.append(rate)
        if decodable != total:
            report["ok"] = False
            report["warnings"].append(
                f"decodable deck codes {decodable}/{total}; every row is required"
            )
        report["low_activity"] = bool(
            0 < total < int(report["minimum_rows"] or 0) and report["ok"]
        )
    metric_availability_score = (
        round(sum(rates) / len(rates), 4) if rates else None
    )
    report["quality_score"] = metric_availability_score
    report["metric_availability_score"] = metric_availability_score
    if strict_completeness:
        row_report = _row_retrieval_report(
            source_id,
            structured,
            expected_normalized_rows=len(rows),
        )
        report["row_retrieval"] = row_report
        if not row_report["valid"]:
            report["ok"] = False
            report["retrieval_complete"] = False
            report["warnings"].extend(row_report["warnings"])
        if row_report.get("unexplained_drops"):
            report["ok"] = False
            report["retrieval_complete"] = False
            report["warnings"].append(
                "row_retrieval has unexplained dropped rows"
            )
        field_retrieval_score = (
            round(sum(retrieval_rates) / len(retrieval_rates), 4)
            if retrieval_rates
            else 1.0
        )
        report["retrieval_completeness_score"] = min(
            field_retrieval_score,
            float(row_report["retrieval_completeness_rate"]),
        )
    return report


def contract_quality_ok(source_id: str, structured: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    report = contract_quality_report(source_id, structured)
    if report["ok"]:
        return True, "ok", report
    return False, "; ".join(report["warnings"]) or "contract quality failed", report


def contract_ids() -> Iterable[str]:
    return CONTRACTS.keys()
