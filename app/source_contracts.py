from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

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
    critical_fields: tuple[str, ...] = ()
    min_field_fill_rate: float = 0.0
    regression_drop_ratio: float | None = None
    volatility: str = "stable"
    fallback_policy: str = "html_allowed"
    recommendation: str | None = None
    min_html_bytes: int = 2_000


HSREPLAY_JSON_CHANNELS = ("curl_cffi", "flaresolverr")


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
        preferred_channels=HSREPLAY_JSON_CHANNELS,
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
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=True,
        min_rows=10,
        critical_fields=("key_card", "winrate", "pick_rate", "offer_rate", "score"),
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
        allow_browser_fallback=True,
        min_rows=80,
        critical_fields=("name", "trinket_id", "description", "pick_rate", "avg_placement"),
        min_field_fill_rate=0.90,
        regression_drop_ratio=0.35,
        fallback_policy="html_allowed",
        recommendation="Protected HSReplay trinkets page; prefer Firecrawl markdown rows with Avg. Placement and keep canonical ids/names enriched from cache.",
    ),
    "hsreplay_battlegrounds_trinkets_greater": SourceContract(
        source_id="hsreplay_battlegrounds_trinkets_greater",
        structured_type="bg_trinkets",
        allow_browser_fallback=True,
        min_rows=80,
        critical_fields=("name", "trinket_id", "description", "pick_rate", "avg_placement"),
        min_field_fill_rate=0.90,
        regression_drop_ratio=0.35,
        fallback_policy="html_allowed",
        recommendation="Protected HSReplay trinkets page; prefer Firecrawl markdown rows with Avg. Placement and keep canonical ids/names enriched from cache.",
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
        regression_drop_ratio=0.35,
        fallback_policy="html_allowed",
    ),
    "firestone_battlegrounds_comps": SourceContract(
        source_id="firestone_battlegrounds_comps",
        structured_type="bg_comps",
        allow_browser_fallback=False,
        min_rows=10,
        critical_fields=("name", "main_cards"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.35,
        fallback_policy="api_only",
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
        critical_fields=("title", "url"),
        min_field_fill_rate=0.80,
        regression_drop_ratio=0.35,
        fallback_policy="allow_partial",
        recommendation="Track deck_code fill rate separately; missing individual detail pages should not fail the whole source.",
    ),
}

for _trinket_slice_source_id in TRINKET_SLICE_SOURCE_IDS:
    CONTRACTS[_trinket_slice_source_id] = SourceContract(
        source_id=_trinket_slice_source_id,
        structured_type="bg_trinkets",
        allow_browser_fallback=True,
        min_rows=160,
        critical_fields=("name", "trinket_id", "description", "pick_rate", "avg_placement"),
        min_field_fill_rate=0.90,
        regression_drop_ratio=0.35,
        fallback_policy="html_allowed",
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
        ),
)

for source_id in STANDARD_HSREPLAY_CARD_PERIOD_SOURCE_IDS[1:]:
    CONTRACTS[source_id] = SourceContract(
        source_id=source_id,
        structured_type="card_stats",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=600,
        critical_fields=("deck_winrate", "deck_popularity"),
        min_field_fill_rate=0.55,
        regression_drop_ratio=0.50,
        volatility="daily",
        fallback_policy="api_only",
        recommendation="Preserve the previous valid Standard period snapshot on severe metric or row-count regression.",
    )

for source_id in WILD_HSREPLAY_CARD_PERIOD_SOURCE_IDS[1:]:
    CONTRACTS[source_id] = SourceContract(
        source_id=source_id,
        structured_type="card_stats",
        preferred_channels=HSREPLAY_JSON_CHANNELS,
        allow_browser_fallback=False,
        min_rows=700,
        critical_fields=("deck_winrate", "deck_popularity"),
        min_field_fill_rate=0.45,
        regression_drop_ratio=0.50,
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
        "ok": True,
        "warnings": [],
        "minimum_rows": None,
    }
    if contract is None:
        return report
    if contract.structured_type and structured.get("type") != contract.structured_type:
        report["ok"] = False
        report["warnings"].append(
            f"expected structured type {contract.structured_type}, got {structured.get('type')}"
        )
    if contract.min_rows is not None:
        minimum_rows = effective_contract_min_rows(source_id, contract.min_rows)
        report["minimum_rows"] = minimum_rows
        if len(rows) < minimum_rows:
            report["ok"] = False
            report["warnings"].append(f"too few rows ({len(rows)} < {minimum_rows})")
    rates: list[float] = []
    for field in contract.critical_fields:
        total = len(rows)
        filled = sum(1 for row in rows if _field_present(row, field))
        rate = (filled / total) if total else 0.0
        report["critical_fields"][field] = {
            "filled": filled,
            "total": total,
            "rate": round(rate, 4),
        }
        rates.append(rate)
        if contract.min_field_fill_rate and rate < contract.min_field_fill_rate:
            report["ok"] = False
            report["warnings"].append(
                f"{field} fill rate {rate:.2%} below {contract.min_field_fill_rate:.0%}"
            )
    report["quality_score"] = round(sum(rates) / len(rates), 4) if rates else None
    return report


def contract_quality_ok(source_id: str, structured: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    report = contract_quality_report(source_id, structured)
    if report["ok"]:
        return True, "ok", report
    return False, "; ".join(report["warnings"]) or "contract quality failed", report


def contract_ids() -> Iterable[str]:
    return CONTRACTS.keys()
