from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


class StructuredSchemaError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StructuredSchemaError(message)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_unit_rate(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0.0 <= float(value) <= 1.0
    )


def _validate_card_stats(data: dict[str, Any]) -> None:
    cards = data.get("cards")
    _require(isinstance(cards, list), "card_stats.cards must be a list")
    metric_keys = {
        "deck_popularity",
        "deck_winrate",
        "games_played",
        "copies",
        "winrate_when_drawn",
        "winrate_when_played",
    }
    for idx, card in enumerate(cards):
        _require(isinstance(card, dict), f"card_stats.cards[{idx}] must be an object")
        _require(
            card.get("id") is not None or card.get("dbfId") is not None,
            f"card_stats.cards[{idx}] missing id/dbfId",
        )
    _require(
        any(any(card.get(key) is not None for key in metric_keys) for card in cards if isinstance(card, dict)),
        "card_stats.cards missing all card metrics",
    )


def _validate_bg_heroes(data: dict[str, Any]) -> None:
    heroes = data.get("heroes")
    _require(isinstance(heroes, list), "bg_heroes.heroes must be a list")
    for idx, hero in enumerate(heroes):
        _require(isinstance(hero, dict), f"bg_heroes.heroes[{idx}] must be an object")
        _require(hero.get("hero") or hero.get("name"), f"bg_heroes.heroes[{idx}] missing hero")
        if hero.get("placement_distribution") is not None:
            _require(
                isinstance(hero["placement_distribution"], list),
                f"bg_heroes.heroes[{idx}].placement_distribution must be a list",
            )


def _validate_bg_minions(data: dict[str, Any]) -> None:
    minions = data.get("minions")
    _require(isinstance(minions, list), "bg_minions.minions must be a list")
    for idx, minion in enumerate(minions):
        _require(isinstance(minion, dict), f"bg_minions.minions[{idx}] must be an object")
        _require(minion.get("minion") or minion.get("name"), f"bg_minions.minions[{idx}] missing name")
        _require("impact" in minion, f"bg_minions.minions[{idx}] missing impact")


def _validate_bg_compositions(data: dict[str, Any]) -> None:
    comps = data.get("compositions")
    _require(isinstance(comps, list), "bg_compositions.compositions must be a list")
    for idx, comp in enumerate(comps):
        _require(isinstance(comp, dict), f"bg_compositions.compositions[{idx}] must be an object")
        _require(comp.get("type"), f"bg_compositions.compositions[{idx}] missing type")
        _require("avg_placement" in comp, f"bg_compositions.compositions[{idx}] missing avg_placement")
        if comp.get("placement_distribution") is not None:
            _require(
                isinstance(comp["placement_distribution"], list),
                f"bg_compositions.compositions[{idx}].placement_distribution must be a list",
            )


def _validate_arena_card_tiers(data: dict[str, Any]) -> None:
    cards = data.get("cards")
    _require(isinstance(cards, list), "arena_card_tiers.cards must be a list")
    for idx, card in enumerate(cards):
        _require(isinstance(card, dict), f"arena_card_tiers.cards[{idx}] must be an object")
        _require(card.get("name"), f"arena_card_tiers.cards[{idx}] missing name")


def _validate_arena_class_pages(data: dict[str, Any]) -> None:
    classes = data.get("classes")
    _require(isinstance(classes, list), "arena_class_pages.classes must be a list")
    for idx, row in enumerate(classes):
        _require(isinstance(row, dict), f"arena_class_pages.classes[{idx}] must be an object")
        _require(row.get("class") or row.get("class_name"), f"arena_class_pages.classes[{idx}] missing class")
        _require("win_rate" in row, f"arena_class_pages.classes[{idx}] missing win_rate")
        _require("pick_rate" in row, f"arena_class_pages.classes[{idx}] missing pick_rate")
        _require("pct_7_plus" in row, f"arena_class_pages.classes[{idx}] missing pct_7_plus")


def _validate_vicious_live(data: dict[str, Any]) -> None:
    _require(isinstance(data.get("class_distribution"), list), "vicious_live.class_distribution must be a list")
    _require(isinstance(data.get("deck_distribution"), list), "vicious_live.deck_distribution must be a list")
    tier_list = data.get("tier_list")
    _require(isinstance(tier_list, list), "vicious_live.tier_list must be a list")
    for idx, bracket in enumerate(tier_list):
        _require(isinstance(bracket, dict), f"vicious_live.tier_list[{idx}] must be an object")
        _require(bracket.get("rank_bracket"), f"vicious_live.tier_list[{idx}] missing rank_bracket")
        _require(isinstance(bracket.get("decks"), list), f"vicious_live.tier_list[{idx}].decks must be a list")


def _validate_hsreplay_meta_archetypes(data: dict[str, Any]) -> None:
    classes = data.get("classes")
    _require(isinstance(classes, list), "hsreplay_meta_archetypes.classes must be a list")
    for class_idx, class_group in enumerate(classes):
        _require(isinstance(class_group, dict), f"hsreplay_meta_archetypes.classes[{class_idx}] must be an object")
        _require(_is_non_empty_string(class_group.get("class")), f"classes[{class_idx}] missing class")
        archetypes = class_group.get("archetypes")
        _require(isinstance(archetypes, list), f"classes[{class_idx}].archetypes must be a list")
        for arch_idx, archetype in enumerate(archetypes):
            _require(isinstance(archetype, dict), f"classes[{class_idx}].archetypes[{arch_idx}] must be an object")
            _require(archetype.get("archetype_id") is not None, f"classes[{class_idx}].archetypes[{arch_idx}] missing archetype_id")
            _require(archetype.get("archetype"), f"classes[{class_idx}].archetypes[{arch_idx}] missing archetype")
            _require(archetype.get("winrate"), f"classes[{class_idx}].archetypes[{arch_idx}] missing winrate")
            _require(archetype.get("popularity"), f"classes[{class_idx}].archetypes[{arch_idx}] missing popularity")
            _require(archetype.get("games") is not None, f"classes[{class_idx}].archetypes[{arch_idx}] missing games")


def _validate_hearthstone_decks(data: dict[str, Any]) -> None:
    decks = data.get("decks")
    _require(isinstance(decks, list), "hearthstone_decks.decks must be a list")
    _require(
        data.get("total_decks") == len(decks),
        "hearthstone_decks.total_decks does not match decks",
    )
    standard_count = sum(
        1 for row in decks if isinstance(row, dict) and row.get("format") == "Standard"
    )
    wild_count = sum(
        1 for row in decks if isinstance(row, dict) and row.get("format") == "Wild"
    )
    _require(
        data.get("standard_count") == standard_count,
        "hearthstone_decks.standard_count does not match decks",
    )
    _require(
        data.get("wild_count") == wild_count,
        "hearthstone_decks.wild_count does not match decks",
    )
    with_code = sum(
        1 for row in decks if isinstance(row, dict) and row.get("deck_code")
    )
    _require(
        data.get("with_deck_code") == with_code,
        "hearthstone_decks.with_deck_code does not match decks",
    )
    _require(
        data.get("missing_deck_code_count") == len(decks) - with_code,
        "hearthstone_decks.missing_deck_code_count does not match decks",
    )
    fill_rate = data.get("deck_code_fill_rate")
    _require(_is_unit_rate(fill_rate), "hearthstone_decks.deck_code_fill_rate must be 0..1")
    expected_fill_rate = round(with_code / len(decks), 4) if decks else 0.0
    _require(
        float(fill_rate) == expected_fill_rate,
        "hearthstone_decks.deck_code_fill_rate does not match decks",
    )
    _require(
        data.get("fetch_strategy") in {"wordpress_rest", "validated_html_fallback"},
        "hearthstone_decks.fetch_strategy is invalid",
    )
    for idx, row in enumerate(decks):
        path = f"hearthstone_decks.decks[{idx}]"
        _require(isinstance(row, dict), f"{path} must be an object")
        _require(_is_non_empty_string(row.get("title")), f"{path} missing title")
        _require(row.get("format") in {"Standard", "Wild"}, f"{path}.format is invalid")
        raw_url = row.get("url")
        _require(_is_non_empty_string(raw_url), f"{path} missing url")
        parsed = urlparse(str(raw_url))
        _require(parsed.scheme == "https", f"{path}.url must use https")
        _require(
            (parsed.hostname or "").rstrip(".").lower()
            in {"hearthstone-decks.net", "www.hearthstone-decks.net"},
            f"{path}.url host is invalid",
        )
        _require(isinstance(row.get("deck_code"), str), f"{path}.deck_code must be a string")


def _validate_firestone_standard(data: dict[str, Any]) -> None:
    _require(data.get("format") == "standard", "firestone_standard.format must be standard")
    _require(data.get("rank_bracket") == "legend", "firestone_standard.rank_bracket must be legend")
    _require(data.get("time_period") == "last-patch", "firestone_standard.time_period must be last-patch")

    metadata = data.get("metadata")
    _require(isinstance(metadata, dict), "firestone_standard.metadata must be an object")
    for collection in ("decks", "archetypes"):
        item = metadata.get(collection) if isinstance(metadata, dict) else None
        _require(isinstance(item, dict), f"firestone_standard.metadata.{collection} must be an object")
        _require(
            _is_non_negative_int(item.get("data_points")),
            f"firestone_standard.metadata.{collection}.data_points must be a non-negative integer",
        )
        _require(
            _is_non_empty_string(item.get("last_updated")),
            f"firestone_standard.metadata.{collection}.last_updated is required",
        )
        _require(item.get("format") == "standard", f"metadata.{collection}.format must be standard")
        _require(item.get("rank_bracket") == "legend", f"metadata.{collection}.rank_bracket must be legend")
        _require(item.get("time_period") == "last-patch", f"metadata.{collection}.time_period must be last-patch")

    decks = data.get("decks")
    archetypes = data.get("archetypes")
    _require(isinstance(decks, list), "firestone_standard.decks must be a list")
    _require(isinstance(archetypes, list), "firestone_standard.archetypes must be a list")
    _require(data.get("total_decks") == len(decks), "firestone_standard.total_decks does not match decks")
    _require(
        data.get("total_archetypes") == len(archetypes),
        "firestone_standard.total_archetypes does not match archetypes",
    )

    for collection, rows in (("decks", decks), ("archetypes", archetypes)):
        for idx, row in enumerate(rows):
            path = f"firestone_standard.{collection}[{idx}]"
            _require(isinstance(row, dict), f"{path} must be an object")
            _require(_is_non_negative_int(row.get("archetype_id")), f"{path} missing archetype_id")
            _require(_is_non_empty_string(row.get("archetype_name")), f"{path} missing archetype_name")
            _require(_is_non_empty_string(row.get("player_class")), f"{path} missing player_class")
            _require(_is_non_negative_int(row.get("games")), f"{path}.games must be a non-negative integer")
            _require(_is_non_negative_int(row.get("wins")), f"{path}.wins must be a non-negative integer")
            _require(row["wins"] <= row["games"], f"{path}.wins cannot exceed games")
            _require(_is_unit_rate(row.get("winrate")), f"{path}.winrate must be between 0 and 1")
            _require(isinstance(row.get("core_cards"), list), f"{path}.core_cards must be a list")
            _require(
                all(_is_non_empty_string(card_id) for card_id in row["core_cards"]),
                f"{path}.core_cards must contain card ids",
            )

    for idx, deck in enumerate(decks):
        path = f"firestone_standard.decks[{idx}]"
        _require(_is_non_empty_string(deck.get("decklist")), f"{path} missing decklist")
        _require(deck.get("deck_code") == deck.get("decklist"), f"{path}.deck_code must equal decklist")
        variations = deck.get("card_variations")
        _require(isinstance(variations, dict), f"{path}.card_variations must be an object")
        for key in ("added", "removed"):
            values = variations.get(key) if isinstance(variations, dict) else None
            _require(isinstance(values, list), f"{path}.card_variations.{key} must be a list")
            _require(
                all(_is_non_empty_string(card_id) for card_id in values),
                f"{path}.card_variations.{key} must contain card ids",
            )


_VALIDATORS = {
    "arena_class_pages": _validate_arena_class_pages,
    "arena_card_tiers": _validate_arena_card_tiers,
    "bg_compositions": _validate_bg_compositions,
    "bg_heroes": _validate_bg_heroes,
    "bg_minions": _validate_bg_minions,
    "card_stats": _validate_card_stats,
    "firestone_standard": _validate_firestone_standard,
    "hearthstone_decks": _validate_hearthstone_decks,
    "hsreplay_meta_archetypes": _validate_hsreplay_meta_archetypes,
    "vicious_live": _validate_vicious_live,
}


def validate_structured_schema(structured: dict[str, Any]) -> dict[str, Any]:
    stype = structured.get("type")
    _require(_is_non_empty_string(stype), "structured.type is required")
    validator = _VALIDATORS.get(str(stype))
    if validator is None:
        return {"ok": True, "type": stype, "validated": False, "reason": "no schema registered"}
    validator(structured)
    return {"ok": True, "type": stype, "validated": True}
