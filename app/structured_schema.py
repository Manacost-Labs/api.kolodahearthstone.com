from __future__ import annotations

import math
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from .completeness import (
    ARENA_LEGENDARY_EXPECTED_BUCKETS,
    COMPLETENESS_SCHEMA_VERSION,
    HSREPLAY_ARENA_EXPECTED_PARAMS,
)
from .source_contracts import uses_completeness_schema


class StructuredSchemaError(ValueError):
    pass


_HSREPLAY_FRESHNESS_EVIDENCE = frozenset(
    {
        "body_as_of",
        "last_modified",
        "age",
        "etag",
        "meta_period_id",
        "selected_params",
    }
)
_HSREPLAY_TARGET_HEADERS = frozenset(
    {"date", "age", "etag", "last-modified", "cache-control", "cf-cache-status"}
)


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


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_percent(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            number = float(text.replace(",", "."))
        except ValueError:
            return False
    else:
        return False
    return math.isfinite(number) and 0.0 <= number <= 100.0


def _is_aware_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _field_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _validate_field_availability(
    row: dict[str, Any],
    fields: tuple[str, ...],
    *,
    path: str,
    required: bool = False,
) -> None:
    availability = row.get("field_availability")
    if availability is None:
        _require(not required, f"{path}.field_availability is required")
        return
    _require(isinstance(availability, dict), f"{path}.field_availability must be an object")
    for field_name in fields:
        if field_name not in availability:
            _require(
                not required,
                f"{path}.field_availability.{field_name} is required",
            )
            continue
        descriptor = availability[field_name]
        descriptor_path = f"{path}.field_availability.{field_name}"
        _require(isinstance(descriptor, dict), f"{descriptor_path} must be an object")
        available = descriptor.get("available")
        reason = descriptor.get("reason")
        _require(isinstance(available, bool), f"{descriptor_path}.available must be boolean")
        present = _field_value_present(row.get(field_name))
        if available:
            _require(reason in (None, ""), f"{descriptor_path}.reason must be empty when available")
            _require(present, f"{descriptor_path} contradicts a missing field")
        else:
            _require(
                isinstance(reason, str) and bool(reason.strip()),
                f"{descriptor_path}.reason is required when unavailable",
            )
            _require(not present, f"{descriptor_path} contradicts a present field")


def _validate_hsreplay_upstream_freshness(
    data: dict[str, Any],
    *,
    arena: bool,
) -> None:
    if not uses_completeness_schema(data):
        return
    _require(
        data.get("population_completeness") == "unverifiable",
        "structured.population_completeness must be 'unverifiable'",
    )
    freshness = data.get("upstream_freshness")
    _require(
        isinstance(freshness, dict),
        "structured.upstream_freshness must be an object",
    )
    status = freshness.get("status")
    _require(
        status in {"fresh", "stale", "unknown"},
        "structured.upstream_freshness.status is invalid",
    )
    reason = freshness.get("reason")
    if status == "fresh":
        _require(
            reason in (None, ""),
            "structured.upstream_freshness.reason must be empty when fresh",
        )
    else:
        _require(
            isinstance(reason, str) and 0 < len(reason.strip()) <= 128,
            "structured.upstream_freshness.reason is required and bounded",
        )
    _require(
        _is_aware_iso_timestamp(freshness.get("observed_at")),
        "structured.upstream_freshness.observed_at must be timezone-aware ISO",
    )
    age = freshness.get("age_seconds")
    _require(
        age is None
        or (
            _is_finite_number(age)
            and float(age) >= 0
            and float(age) <= 365 * 24 * 60 * 60
        ),
        "structured.upstream_freshness.age_seconds must be bounded non-negative",
    )
    if status in {"fresh", "stale"}:
        _require(
            age is not None,
            "structured.upstream_freshness.age_seconds is required when known",
        )
    evidence = freshness.get("evidence")
    _require(
        isinstance(evidence, list)
        and len(evidence) == len(set(evidence))
        and all(item in _HSREPLAY_FRESHNESS_EVIDENCE for item in evidence),
        "structured.upstream_freshness.evidence is invalid",
    )
    headers = freshness.get("response_headers")
    _require(
        isinstance(headers, dict),
        "structured.upstream_freshness.response_headers must be an object",
    )
    _require(
        all(
            isinstance(name, str)
            and name in _HSREPLAY_TARGET_HEADERS
            and isinstance(value, str)
            and 0 < len(value) <= 512
            and "\r" not in value
            and "\n" not in value
            for name, value in headers.items()
        ),
        "structured.upstream_freshness.response_headers contains unsafe evidence",
    )

    if arena:
        meta_period_id = freshness.get("meta_period_id")
        if meta_period_id is not None:
            _require(
                isinstance(meta_period_id, int)
                and not isinstance(meta_period_id, bool)
                and 0 < meta_period_id <= 1_000_000_000,
                "structured.upstream_freshness.meta_period_id is invalid",
            )
        selected_params = freshness.get("selected_params")
        if selected_params is not None:
            _require(
                isinstance(selected_params, list)
                and all(_is_non_empty_string(item) and len(item) <= 128 for item in selected_params),
                "structured.upstream_freshness.selected_params is invalid",
            )
        if status in {"fresh", "stale"}:
            _require(
                meta_period_id is not None,
                "structured.upstream_freshness.meta_period_id is required",
            )
            _require(
                freshness.get("filters_match") is True
                and selected_params == list(HSREPLAY_ARENA_EXPECTED_PARAMS),
                "structured.upstream_freshness.selected_params must match Arena filters",
            )
    else:
        body_as_of = freshness.get("body_as_of")
        if body_as_of is not None:
            _require(
                _is_aware_iso_timestamp(body_as_of),
                "structured.upstream_freshness.body_as_of must be timezone-aware ISO",
            )
        if status in {"fresh", "stale"}:
            _require(
                body_as_of is not None,
                "structured.upstream_freshness.body_as_of is required",
            )


def _require_optional_range(
    value: Any,
    *,
    minimum: float,
    maximum: float,
    path: str,
) -> None:
    _require(
        value is None
        or (
            _is_finite_number(value)
            and minimum <= float(value) <= maximum
        ),
        f"{path} must be finite in {minimum:g}..{maximum:g} or null",
    )


def _validate_bg_minion_metrics(minion: dict[str, Any], *, path: str) -> None:
    _require_optional_range(
        minion.get("avg_placement_with"),
        minimum=1,
        maximum=8,
        path=f"{path}.avg_placement_with",
    )
    _require_optional_range(
        minion.get("avg_placement_without"),
        minimum=1,
        maximum=8,
        path=f"{path}.avg_placement_without",
    )
    _require_optional_range(
        minion.get("impact"),
        minimum=-7,
        maximum=7,
        path=f"{path}.impact",
    )
    for field_name in ("win_share", "popularity"):
        value = minion.get(field_name)
        _require(
            value is None or _is_percent(value),
            f"{path}.{field_name} must be a finite percentage in 0..100 or null",
        )
    for field_name in ("games_with_minion", "games_without_minion"):
        value = minion.get(field_name)
        _require(
            value is None or _is_non_negative_int(value),
            f"{path}.{field_name} must be a non-negative integer or null",
        )
    avg_with = minion.get("avg_placement_with")
    avg_without = minion.get("avg_placement_without")
    impact = minion.get("impact")
    if all(_is_finite_number(value) for value in (avg_with, avg_without, impact)):
        _require(
            abs(float(impact) - (float(avg_without) - float(avg_with))) <= 0.03,
            f"{path}.impact does not reconcile with average placements",
        )
    rounds = minion.get("combat_rounds")
    _require(
        rounds is None or isinstance(rounds, list),
        f"{path}.combat_rounds must be a list",
    )
    for round_idx, round_row in enumerate(rounds or []):
        round_path = f"{path}.combat_rounds[{round_idx}]"
        _require(isinstance(round_row, dict), f"{round_path} must be an object")
        combat_round = round_row.get("combat_round")
        _require(
            isinstance(combat_round, int)
            and not isinstance(combat_round, bool)
            and 1 <= combat_round <= 16,
            f"{round_path}.combat_round must be an integer in 1..16",
        )
        for field_name in ("games_with_minion", "games_without_minion"):
            _require(
                _is_non_negative_int(round_row.get(field_name)),
                f"{round_path}.{field_name} must be a non-negative integer",
            )
        round_wins = round_row.get("wins")
        round_losses = round_row.get("losses")
        _require(
            (round_wins is None and round_losses is None)
            or (
                _is_non_negative_int(round_wins)
                and _is_non_negative_int(round_losses)
            ),
            f"{round_path}.wins and losses must both be non-negative integers or null",
        )
        for field_name in ("avg_placement_with", "avg_placement_without"):
            _require_optional_range(
                round_row.get(field_name),
                minimum=1,
                maximum=8,
                path=f"{round_path}.{field_name}",
            )
        _require_optional_range(
            round_row.get("impact"),
            minimum=-7,
            maximum=7,
            path=f"{round_path}.impact",
        )
        round_avg_with = round_row.get("avg_placement_with")
        round_avg_without = round_row.get("avg_placement_without")
        round_impact = round_row.get("impact")
        if all(
            _is_finite_number(value)
            for value in (round_avg_with, round_avg_without, round_impact)
        ):
            _require(
                abs(
                    float(round_impact)
                    - (float(round_avg_without) - float(round_avg_with))
                )
                <= 0.03,
                f"{round_path}.impact does not reconcile with average placements",
            )
        for field_name in ("combat_winrate", "combat_winrate_value"):
            value = round_row.get(field_name)
            _require(
                value is None or _is_percent(value),
                f"{round_path}.{field_name} must be a finite percentage in 0..100 or null",
            )
    if rounds:
        for field_name in ("games_with_minion", "games_without_minion"):
            expected = sum(round_row[field_name] for round_row in rounds)
            observed = minion.get(field_name)
            observed_count = 0 if observed is None else observed
            _require(
                observed_count == expected,
                f"{path}.{field_name} does not reconcile with combat_rounds",
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
        if uses_completeness_schema(data):
            _validate_bg_minion_metrics(minion, path=f"bg_minions.minions[{idx}]")
        _validate_field_availability(
            minion,
            ("impact", "win_share", "popularity"),
            path=f"bg_minions.minions[{idx}]",
            required=uses_completeness_schema(data),
        )
    _validate_hsreplay_upstream_freshness(data, arena=False)


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
    if uses_completeness_schema(data) and (
        "primary_class" in data or "selected_class" in data
    ):
        primary_class = data.get("primary_class")
        selected_class = data.get("selected_class")
        _require(
            _is_non_empty_string(primary_class),
            "arena_card_tiers.primary_class must be a non-empty string",
        )
        _require(
            selected_class == primary_class,
            "arena_card_tiers.selected_class must exactly match primary_class",
        )
    for idx, card in enumerate(cards):
        path = f"arena_card_tiers.cards[{idx}]"
        _require(isinstance(card, dict), f"{path} must be an object")
        _require(card.get("name"), f"{path} missing name")
        if uses_completeness_schema(data):
            _require(_is_non_empty_string(card.get("card_id")), f"{path} missing card_id")
            for field_name in (
                "deck_winrate",
                "winrate_when_drawn",
                "winrate_when_played",
            ):
                value = card.get(field_name)
                _require(
                    value is None or _is_percent(value),
                    f"{path}.{field_name} must be a finite percentage in 0..100",
                )
            _require(
                _is_percent(card.get("in_runs")),
                f"{path}.in_runs must be a finite percentage in 0..100",
            )
            _require(
                _is_finite_number(card.get("avg_copies"))
                and float(card["avg_copies"]) >= 0.0,
                f"{path}.avg_copies must be a finite non-negative number",
            )
        _validate_field_availability(
            card,
            ("deck_winrate", "winrate_when_drawn", "winrate_when_played"),
            path=f"arena_card_tiers.cards[{idx}]",
            required=uses_completeness_schema(data),
        )
    _validate_hsreplay_upstream_freshness(data, arena=True)


def _validate_legendary_bucket_coverage(data: dict[str, Any]) -> None:
    row_retrieval = data.get("row_retrieval")
    _require(
        isinstance(row_retrieval, dict),
        "arena_legendary_groups.row_retrieval must be an object",
    )
    coverage = row_retrieval.get("bucket_coverage")
    _require(
        isinstance(coverage, dict),
        "arena_legendary_groups.row_retrieval.bucket_coverage must be an object",
    )
    expected = list(ARENA_LEGENDARY_EXPECTED_BUCKETS)
    values: dict[str, list[str]] = {}
    for field_name in (
        "expected_buckets",
        "observed_buckets",
        "missing_buckets",
        "unknown_buckets",
        "duplicate_bucket_package_keys",
    ):
        value = coverage.get(field_name)
        _require(
            isinstance(value, list)
            and all(_is_non_empty_string(item) for item in value),
            "arena_legendary_groups.row_retrieval.bucket_coverage."
            f"{field_name} must be a string list",
        )
        values[field_name] = value
    _require(
        values["expected_buckets"] == expected,
        "arena_legendary_groups.row_retrieval.bucket_coverage.expected_buckets "
        "does not match the full endpoint contract",
    )
    observed = values["observed_buckets"]
    missing = values["missing_buckets"]
    _require(
        observed == [bucket for bucket in expected if bucket in observed],
        "arena_legendary_groups.row_retrieval.bucket_coverage.observed_buckets "
        "has invalid order or names",
    )
    _require(
        missing == [bucket for bucket in expected if bucket not in observed],
        "arena_legendary_groups.row_retrieval.bucket_coverage.missing_buckets "
        "does not reconcile",
    )
    _require(
        not missing,
        "arena_legendary_groups.row_retrieval.bucket_coverage.missing_buckets "
        "must be empty",
    )
    _require(
        not values["unknown_buckets"],
        "arena_legendary_groups.row_retrieval.bucket_coverage.unknown_buckets "
        "must be empty",
    )
    _require(
        not values["duplicate_bucket_package_keys"],
        "arena_legendary_groups.row_retrieval.bucket_coverage."
        "duplicate_bucket_package_keys must be empty",
    )


def _validate_legendary_metrics(metrics: dict[str, Any], *, path: str) -> None:
    winrate = metrics.get("winrate")
    _require(
        winrate is None or _is_percent(winrate),
        f"{path}.winrate must be a finite percentage in 0..100",
    )
    for field_name in ("pick_rate", "offer_rate"):
        _require(
            _is_percent(metrics.get(field_name)),
            f"{path}.{field_name} must be a finite percentage in 0..100",
        )
    score = metrics.get("score")
    _require(
        score is None or _is_finite_number(score),
        f"{path}.score must be null or finite numeric",
    )


def _validate_arena_legendary_groups(data: dict[str, Any]) -> None:
    groups = data.get("groups")
    _require(
        isinstance(groups, list),
        "arena_legendary_groups.groups must be a list",
    )
    versioned = uses_completeness_schema(data)
    if versioned:
        _validate_legendary_bucket_coverage(data)
    for group_idx, group in enumerate(groups):
        path = f"arena_legendary_groups.groups[{group_idx}]"
        _require(isinstance(group, dict), f"{path} must be an object")
        if versioned:
            key_card = group.get("key_card")
            _require(isinstance(key_card, dict), f"{path}.key_card must be an object")
            _require(
                _is_non_empty_string(key_card.get("card_id")),
                f"{path}.key_card.card_id is required",
            )
            cards = group.get("cards")
            _require(
                isinstance(cards, list) and bool(cards),
                f"{path}.cards must be a non-empty list",
            )
            for card_idx, card in enumerate(cards):
                card_path = f"{path}.cards[{card_idx}]"
                _require(isinstance(card, dict), f"{card_path} must be an object")
                _require(
                    _is_non_empty_string(card.get("card_id")),
                    f"{card_path}.card_id is required",
                )
                _require(
                    isinstance(card.get("count"), int)
                    and not isinstance(card.get("count"), bool)
                    and card["count"] > 0,
                    f"{card_path}.count must be a positive integer",
                )
            _validate_legendary_metrics(group, path=path)
        _validate_field_availability(
            group,
            ("winrate", "score"),
            path=path,
            required=versioned,
        )
        by_class = group.get("by_class")
        if versioned:
            _require(isinstance(by_class, dict) and bool(by_class), f"{path}.by_class is required")
        if not isinstance(by_class, dict):
            continue
        for class_name, metrics in by_class.items():
            class_path = f"{path}.by_class.{class_name}"
            _require(isinstance(metrics, dict), f"{class_path} must be an object")
            if versioned:
                _validate_legendary_metrics(metrics, path=class_path)
            _validate_field_availability(
                metrics,
                ("winrate", "score"),
                path=class_path,
                required=versioned,
            )
    _validate_hsreplay_upstream_freshness(data, arena=True)


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
            _is_aware_iso_timestamp(item.get("last_updated")),
            (
                f"firestone_standard.metadata.{collection}.last_updated "
                "must be an ISO timestamp with timezone"
            ),
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
            _validate_field_availability(
                row,
                ("core_cards",),
                path=path,
                required=uses_completeness_schema(data),
            )
            _require(row.get("format") == "standard", f"{path}.format must be standard")
            if collection == "decks":
                _require(
                    row.get("rank_bracket") == "legend",
                    f"{path}.rank_bracket must be legend",
                )
                _require(
                    row.get("time_period") == "last-patch",
                    f"{path}.time_period must be last-patch",
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
    "arena_legendary_groups": _validate_arena_legendary_groups,
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
    if "completeness_schema_version" in structured:
        version = structured.get("completeness_schema_version")
        _require(
            isinstance(version, int)
            and not isinstance(version, bool)
            and version == COMPLETENESS_SCHEMA_VERSION,
            "structured.completeness_schema_version must be supported version "
            f"{COMPLETENESS_SCHEMA_VERSION}",
        )
    validator = _VALIDATORS.get(str(stype))
    if validator is None:
        return {"ok": True, "type": stype, "validated": False, "reason": "no schema registered"}
    validator(structured)
    return {"ok": True, "type": stype, "validated": True}
