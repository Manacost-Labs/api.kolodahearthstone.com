from __future__ import annotations

import pytest

from app.completeness import (
    ARENA_LEGENDARY_EXPECTED_BUCKETS,
    row_retrieval_evidence,
)
from app.firestone_standard import (
    _annotate_core_card_availability,
    _normalize_archetype,
)
from app.hsreplay_arena_api import normalize_arena_card_row
from app.hsreplay_bg_stats import _minion_stats
from app.hsreplay_legendaries_api import (
    HS_BUCKET_TO_CLASS_KEY,
    normalize_legendary_package,
)
from app.publish_gate import validate_candidate_for_publish
from app.source_contracts import contract_quality_report, field_availability_status
from app.source_validators import validate_structured
from app.sources import SOURCE_BY_ID
from app.structured_schema import StructuredSchemaError, validate_structured_schema


def _complete_arena_card(index: int) -> dict[str, object]:
    fields = (
        "deck_winrate",
        "winrate_when_drawn",
        "winrate_when_played",
    )
    return {
        "name": f"Card {index}",
        "card_id": f"CARD_{index}",
        "deck_winrate": "50.00%",
        "winrate_when_drawn": "51.00%",
        "winrate_when_played": "52.00%",
        "in_runs": "1.00%",
        "avg_copies": 1.0,
        "times_played": 10,
        "field_availability": {
            field: {"available": True, "reason": None} for field in fields
        },
    }


def _complete_row_retrieval(total: int) -> dict[str, object]:
    return row_retrieval_evidence(
        raw_rows=total,
        eligible_rows=total,
        normalized_rows=total,
        scope="test_rows",
    )


def _complete_legendary_row_retrieval(total: int) -> dict[str, object]:
    evidence = _complete_row_retrieval(total)
    evidence["bucket_coverage"] = {
        "expected_buckets": list(ARENA_LEGENDARY_EXPECTED_BUCKETS),
        "observed_buckets": list(ARENA_LEGENDARY_EXPECTED_BUCKETS),
        "missing_buckets": [],
        "unknown_buckets": [],
        "duplicate_bucket_package_keys": [],
    }
    return evidence


def _fresh_bg_evidence() -> dict[str, object]:
    return {
        "status": "fresh",
        "reason": None,
        "observed_at": "2026-08-14T02:20:00+00:00",
        "age_seconds": 60,
        "evidence": ["body_as_of"],
        "response_headers": {},
        "body_as_of": "2026-08-14T02:19:00+00:00",
    }


def test_bg_empty_current_patch_aggregates_are_explicitly_unavailable() -> None:
    row = _minion_stats(
        {
            "minion_dbf_id": 59670,
            "minion_tier": 1,
            "normal_aggregates": [],
        }
    )

    assert row is not None
    assert row["impact"] is None
    assert row["field_availability"] == {
        "impact": {
            "available": False,
            "reason": "no_current_patch_aggregates",
        },
        "win_share": {
            "available": False,
            "reason": "no_current_patch_aggregates",
        },
        "popularity": {
            "available": False,
            "reason": "no_current_patch_aggregates",
        },
    }


def test_bg_rejects_missing_or_wrong_aggregate_shape() -> None:
    with pytest.raises(TypeError, match="normal_aggregates must be a list"):
        _minion_stats({"minion_dbf_id": 59670, "normal_aggregates": None})

    with pytest.raises(TypeError, match="normal_aggregates must be a list"):
        _minion_stats({"minion_dbf_id": 59670, "normal_aggregates": {}})

    with pytest.raises(TypeError, match="normal_aggregates entries must be objects"):
        _minion_stats({"minion_dbf_id": 59670, "normal_aggregates": [None]})

    with pytest.raises(ValueError, match="count_of_games_with_minion"):
        _minion_stats({"minion_dbf_id": 59670, "normal_aggregates": [{}]})


@pytest.mark.parametrize("bad_count", [1.5, True, "1"])
def test_bg_normalizer_rejects_non_integer_counts_and_impossible_placements(
    bad_count: object,
) -> None:
    aggregate = {
        "combat_round": 1,
        "count_of_games_with_minion": 1,
        "count_of_games_without_minion": 1,
        "sum_of_placements_for_players_with_minion": 4,
        "sum_of_placements_for_players_without_minion": 4,
        "total_wins": 1,
        "total_losses": 1,
    }
    with pytest.raises(ValueError, match="count_of_games_with_minion.*integer"):
        _minion_stats(
            {
                "minion_dbf_id": 59670,
                "normal_aggregates": [
                    {**aggregate, "count_of_games_with_minion": bad_count}
                ],
            }
        )
    with pytest.raises(ValueError, match="sum_of_placements.*reconcile"):
        _minion_stats(
            {
                "minion_dbf_id": 59670,
                "normal_aggregates": [
                    {
                        **aggregate,
                        "sum_of_placements_for_players_with_minion": 9,
                    }
                ],
            }
        )


def test_bg_missing_combat_result_pair_is_explicitly_unavailable() -> None:
    result = _minion_stats(
        {
            "minion_dbf_id": 59670,
            "normal_aggregates": [
                {
                    "combat_round": 16,
                    "count_of_games_with_minion": 591,
                    "count_of_games_without_minion": 15_473,
                    "sum_of_placements_for_players_with_minion": 810,
                    "sum_of_placements_for_players_without_minion": 23_913,
                    "total_wins": None,
                    "total_losses": None,
                }
            ],
        }
    )

    assert result is not None
    assert result["win_share"] is None
    assert result["combat_winrate_value"] is None
    assert result["field_availability"]["win_share"] == {
        "available": False,
        "reason": "insufficient_current_patch_sample",
    }
    assert result["combat_rounds"][0]["wins"] is None
    assert result["combat_rounds"][0]["losses"] is None
    validate_structured_schema(
        {
            "type": "bg_minions",
            "completeness_schema_version": 1,
            "population_completeness": "unverifiable",
            "upstream_freshness": {
                "status": "unknown",
                "reason": "transport_evidence_unavailable",
                "observed_at": "2026-08-14T03:40:00+00:00",
                "age_seconds": None,
                "evidence": [],
                "response_headers": {},
            },
            "minions": [result],
        }
    )


def test_bg_rejects_half_missing_combat_result_pair() -> None:
    with pytest.raises(ValueError, match="total_wins and total_losses"):
        _minion_stats(
            {
                "minion_dbf_id": 59670,
                "normal_aggregates": [
                    {
                        "combat_round": 16,
                        "count_of_games_with_minion": 1,
                        "count_of_games_without_minion": 1,
                        "sum_of_placements_for_players_with_minion": 1,
                        "sum_of_placements_for_players_without_minion": 1,
                        "total_wins": None,
                        "total_losses": 1,
                    }
                ],
            }
        )


def test_arena_zero_game_row_keeps_null_rates_with_reason() -> None:
    row = normalize_arena_card_row(
        {
            "card_id": "CATA_488",
            "win_rate": None,
            "winrate_when_drawn": None,
            "winrate_when_played": None,
            "popularity": 0,
            "avg_copies_in_deck": 0,
            "num_games": 0,
        }
    )

    assert row is not None
    assert row["deck_winrate"] is None
    assert row["field_availability"] == {
        "deck_winrate": {"available": False, "reason": "no_games_in_window"},
        "winrate_when_drawn": {
            "available": False,
            "reason": "no_games_in_window",
        },
        "winrate_when_played": {
            "available": False,
            "reason": "no_games_in_window",
        },
    }


def test_arena_rejects_missing_sample_size() -> None:
    with pytest.raises(ValueError, match="num_games must be a non-negative integer"):
        normalize_arena_card_row(
            {
                "card_id": "CATA_488",
                "win_rate": None,
                "popularity": 0,
                "avg_copies_in_deck": 0,
            }
        )

    with pytest.raises(ValueError, match="num_games must be a non-negative integer"):
        normalize_arena_card_row(
            {
                "card_id": "CATA_488",
                "win_rate": 50,
                "popularity": 1,
                "avg_copies_in_deck": 1,
                "num_games": "10",
            }
        )


def test_legendary_zero_pick_null_winrate_is_explicitly_unavailable() -> None:
    row = normalize_legendary_package(
        {
            "package_key_card_id": "TOY_813",
            "package_card_ids": ["TOY_813"],
            "win_rate": None,
            "pick_rate": 0,
            "offer_rate": 4.7,
            "score": 1.2,
        },
        locale="enUS",
    )

    assert row is not None
    assert row["winrate"] is None
    assert row["field_availability"]["winrate"] == {
        "available": False,
        "reason": "upstream_unavailable_at_zero_pick_rate",
    }


def test_firestone_requires_raw_core_card_list() -> None:
    with pytest.raises(TypeError, match="coreCards must be a list"):
        _normalize_archetype(
            {
                "id": 677,
                "name": "paladin",
                "heroCardClass": "paladin",
                "totalGames": 53,
                "totalWins": 17,
                "winrate": 0.32,
                "format": "standard",
            }
        )


def test_firestone_empty_unclustered_archetype_has_explicit_reason() -> None:
    archetypes = [
        _normalize_archetype(
            {
                "id": 677,
                "name": "paladin",
                "heroCardClass": "paladin",
                "totalGames": 53,
                "totalWins": 17,
                "winrate": 0.32,
                "coreCards": [],
                "heroCardIds": ["HERO_04"],
                "format": "standard",
            }
        )
    ]

    _annotate_core_card_availability([], archetypes)

    assert archetypes[0]["field_availability"]["core_cards"] == {
        "available": False,
        "reason": "generic_class_bucket_without_observed_deck_cluster",
    }


def test_firestone_non_generic_empty_core_cards_remain_unexplained() -> None:
    archetypes = [
        _normalize_archetype(
            {
                "id": 678,
                "name": "handbuff-paladin",
                "heroCardClass": "paladin",
                "totalGames": 53,
                "totalWins": 17,
                "winrate": 0.32,
                "coreCards": [],
                "heroCardIds": ["HERO_04"],
                "format": "standard",
            }
        )
    ]

    _annotate_core_card_availability([], archetypes)

    assert archetypes[0]["field_availability"]["core_cards"] == {
        "available": False,
        "reason": "empty_core_cards_without_deterministic_explanation",
    }
    assert field_availability_status(
        "firestone_standard",
        archetypes[0],
        "core_cards",
        require_descriptor=True,
    )[0] == "unexplained_missing"


def test_contract_separates_metric_availability_from_retrieval_completeness() -> None:
    cards = [_complete_arena_card(index) for index in range(899)]
    cards.append(
        {
            "name": "No sample card",
            "card_id": "NO_SAMPLE",
            "deck_winrate": None,
            "winrate_when_drawn": None,
            "winrate_when_played": None,
            "in_runs": "0.00%",
            "avg_copies": 0,
            "times_played": 0,
            "field_availability": {
                field: {"available": False, "reason": "no_games_in_window"}
                for field in (
                    "deck_winrate",
                    "winrate_when_drawn",
                    "winrate_when_played",
                )
            },
        }
    )

    report = contract_quality_report(
        "hsreplay_arena_cards_advanced",
        {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "primary_class": "ALL",
            "selected_class": "ALL",
            "row_retrieval": row_retrieval_evidence(
                raw_rows=len(cards),
                eligible_rows=len(cards),
                normalized_rows=len(cards),
                scope="primary_class:ALL",
            ),
            "cards": cards,
        },
    )

    assert report["ok"], report["warnings"]
    assert report["quality_score"] == 0.9993
    assert report["metric_availability_score"] == 0.9993
    assert report["retrieval_completeness_score"] == 1.0
    assert report["retrieval_complete"] is True
    deck_winrate = report["critical_fields"]["deck_winrate"]
    assert deck_winrate == {
        "filled": 899,
        "total": 900,
        "rate": 0.9989,
        "metric_availability_rate": 0.9989,
        "explained_unavailable": 1,
        "unexplained_missing": 0,
        "availability_conflicts": 0,
        "retrieval_completeness_rate": 1.0,
    }


def test_contract_rejects_one_unexplained_hole_even_above_legacy_floor() -> None:
    cards = [_complete_arena_card(index) for index in range(900)]
    cards[-1]["deck_winrate"] = None
    cards[-1]["field_availability"]["deck_winrate"] = {
        "available": False,
        "reason": None,
    }

    report = contract_quality_report(
        "hsreplay_arena_cards_advanced",
        {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "row_retrieval": _complete_row_retrieval(len(cards)),
            "cards": cards,
        },
    )

    assert report["ok"] is False
    assert report["retrieval_complete"] is False
    assert report["critical_fields"]["deck_winrate"]["unexplained_missing"] == 1
    assert "unexplained missing" in "; ".join(report["warnings"])


def test_contract_rejects_unexplained_non_descriptor_critical_field() -> None:
    cards = [_complete_arena_card(index) for index in range(900)]
    cards[-1]["avg_copies"] = None

    report = contract_quality_report(
        "hsreplay_arena_cards_advanced",
        {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "row_retrieval": _complete_row_retrieval(len(cards)),
            "cards": cards,
        },
    )

    assert report["critical_fields"]["avg_copies"]["rate"] > 0.99
    assert report["critical_fields"]["avg_copies"]["unexplained_missing"] == 1
    assert report["retrieval_complete"] is False
    assert report["ok"] is False
    assert "avg_copies has 1 unexplained missing values" in report["warnings"]


def test_semantic_validators_reject_availability_contradictions() -> None:
    arena_cards = [_complete_arena_card(index) for index in range(100)]
    arena_cards[-1]["deck_winrate"] = None
    arena_cards[-1]["field_availability"] = {
        field: {"available": True, "reason": None}
        for field in (
            "deck_winrate",
            "winrate_when_drawn",
            "winrate_when_played",
        )
    }
    arena_report = validate_structured(
        "hsreplay_arena_cards_advanced",
        {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "cards": arena_cards,
        },
    )

    legendary_groups = [
        {
            "key_card": {"card_id": f"CARD_{index}"},
            "winrate": None if index == 9 else "50%",
            "pick_rate": "10%",
            "offer_rate": "20%",
            "score": 1.0,
            "field_availability": {
                "winrate": {"available": True, "reason": None}
            },
            "by_class": {
                "all": {
                    "winrate": None if index == 9 else "50%",
                    "pick_rate": "10%",
                    "field_availability": {
                        "winrate": {"available": True, "reason": None}
                    },
                }
            },
        }
        for index in range(10)
    ]
    legendary_report = validate_structured(
        "hsreplay_arena_legendaries",
        {
            "type": "arena_legendary_groups",
            "completeness_schema_version": 1,
            "groups": legendary_groups,
        },
    )

    assert "arena_card_tiers.unexplained_missing_metrics" in {
        issue.code for issue in arena_report.issues
    }
    assert "arena_legendary_groups.unexplained_winrate" in {
        issue.code for issue in legendary_report.issues
    }


def test_legacy_dataset_keeps_retrieval_completeness_unknown() -> None:
    cards = [_complete_arena_card(index) for index in range(900)]
    for card in cards:
        card.pop("field_availability")
    cards[-1]["deck_winrate"] = None

    report = contract_quality_report(
        "hsreplay_arena_cards_advanced",
        {"type": "arena_card_tiers", "cards": cards},
    )

    assert report["ok"], report["warnings"]
    assert report["retrieval_complete"] is None
    assert report["retrieval_completeness_score"] is None
    assert report["critical_fields"]["deck_winrate"]["unexplained_missing"] is None


def test_v1_requires_descriptors_and_conflicts_do_not_count_as_retrieved() -> None:
    cards = [_complete_arena_card(index) for index in range(900)]
    cards[-1]["field_availability"]["deck_winrate"] = {
        "available": False,
        "reason": "no_games_in_window",
    }

    report = contract_quality_report(
        "hsreplay_arena_cards_advanced",
        {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "row_retrieval": _complete_row_retrieval(len(cards)),
            "cards": cards,
        },
    )

    deck_winrate = report["critical_fields"]["deck_winrate"]
    assert report["ok"] is False
    assert deck_winrate["filled"] == 900
    assert deck_winrate["availability_conflicts"] == 1
    assert deck_winrate["retrieval_completeness_rate"] == 0.9989


def test_v1_unexplained_row_drop_caps_overall_retrieval_score() -> None:
    cards = [_complete_arena_card(index) for index in range(900)]
    row_retrieval = row_retrieval_evidence(
        raw_rows=901,
        eligible_rows=901,
        normalized_rows=900,
        unexplained_reasons={"normalizer_rejected": 1},
        scope="test_rows",
    )

    report = contract_quality_report(
        "hsreplay_arena_cards_advanced",
        {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "row_retrieval": row_retrieval,
            "cards": cards,
        },
    )

    assert report["ok"] is False
    assert report["retrieval_complete"] is False
    assert report["retrieval_completeness_score"] == 0.9989
    assert report["row_retrieval"]["unexplained_drops"] == 1


def test_v1_rejects_non_allowlisted_explained_row_drop_reason() -> None:
    cards = [_complete_arena_card(index) for index in range(900)]
    row_retrieval = row_retrieval_evidence(
        raw_rows=901,
        eligible_rows=901,
        normalized_rows=900,
        explained_reasons={"provider_said_it_was_fine": 1},
        scope="test_rows",
    )

    report = contract_quality_report(
        "hsreplay_arena_cards_advanced",
        {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "row_retrieval": row_retrieval,
            "cards": cards,
        },
    )

    assert report["ok"] is False
    assert report["retrieval_complete"] is False
    assert report["row_retrieval"]["valid"] is False
    assert "not allow-listed" in "; ".join(report["warnings"])


def test_v1_legendary_by_class_conflict_fails_semantic_and_contract_gates() -> None:
    groups = [
        {
            "key_card": {"card_id": f"CARD_{index}"},
            "cards": [{"card_id": f"CARD_{index}", "count": 1}],
            "winrate": "50%",
            "pick_rate": "10%",
            "offer_rate": "20%",
            "score": 1.0,
            "field_availability": {
                "winrate": {"available": True, "reason": None}
            },
            "by_class": {
                class_key: {
                    "winrate": "50%",
                    "pick_rate": "10%",
                    "offer_rate": "20%",
                    "score": 1.0,
                    "field_availability": {
                        "winrate": {"available": True, "reason": None}
                    },
                }
                for class_key in HS_BUCKET_TO_CLASS_KEY.values()
            },
        }
        for index in range(10)
    ]
    groups[-1]["by_class"]["mage"]["winrate"] = None

    payload = {
        "type": "arena_legendary_groups",
        "completeness_schema_version": 1,
        "row_retrieval": _complete_legendary_row_retrieval(len(groups)),
        "groups": groups,
    }
    report = validate_structured("hsreplay_arena_legendaries", payload)
    contract_report = contract_quality_report(
        "hsreplay_arena_legendaries",
        payload,
    )

    assert "arena_legendary_groups.unexplained_by_class_winrate" in {
        issue.code for issue in report.issues
    }
    by_class_winrate = contract_report["critical_fields"]["by_class.winrate"]
    assert by_class_winrate["total"] == 120
    assert by_class_winrate["availability_conflicts"] == 1
    assert by_class_winrate["retrieval_completeness_rate"] == 0.9917
    assert contract_report["retrieval_completeness_score"] < 1.0
    assert contract_report["retrieval_complete"] is False
    assert contract_report["ok"] is False


@pytest.mark.parametrize("field", ["in_runs", "avg_copies"])
def test_v1_arena_advanced_missing_critical_value_fails_retrieval(field: str) -> None:
    cards = [_complete_arena_card(index) for index in range(900)]
    cards[-1][field] = None

    report = contract_quality_report(
        "hsreplay_arena_cards_advanced",
        {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "row_retrieval": _complete_row_retrieval(len(cards)),
            "cards": cards,
        },
    )

    assert report["ok"] is False
    assert report["retrieval_complete"] is False
    assert report["critical_fields"][field]["unexplained_missing"] == 1


def test_v1_arena_advanced_duplicate_card_id_fails_identity_gate() -> None:
    cards = [_complete_arena_card(index) for index in range(900)]
    cards[-1]["card_id"] = cards[0]["card_id"]

    report = contract_quality_report(
        "hsreplay_arena_cards_advanced",
        {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "row_retrieval": _complete_row_retrieval(len(cards)),
            "cards": cards,
        },
    )

    assert report["ok"] is False
    assert report["retrieval_complete"] is False
    assert report["identity_checks"]["cards"]["duplicates"] == 1


def test_v1_bg_duplicate_minion_id_fails_identity_gate() -> None:
    minions = [
        {
            "minion": f"Minion {index}",
            "minion_dbf_id": index + 1,
            "impact": 0.1,
            "win_share": "50%",
            "popularity": "5%",
            "field_availability": {
                field: {"available": True, "reason": None}
                for field in ("impact", "win_share", "popularity")
            },
        }
        for index in range(50)
    ]
    minions[-1]["minion_dbf_id"] = minions[0]["minion_dbf_id"]

    report = contract_quality_report(
        "hsreplay_battlegrounds_minions",
        {
            "type": "bg_minions",
            "completeness_schema_version": 1,
            "row_retrieval": _complete_row_retrieval(len(minions)),
            "minions": minions,
        },
    )

    assert report["ok"] is False
    assert report["retrieval_complete"] is False
    assert report["identity_checks"]["minions"]["duplicates"] == 1


@pytest.mark.parametrize("collection", ["decks", "archetypes"])
def test_v1_firestone_duplicate_collection_identity_fails_gate(
    collection: str,
) -> None:
    decks = [
        {
            "decklist": f"deck-{index}",
            "archetype_id": index + 1,
            "archetype_name": f"Deck {index}",
            "player_class": "mage",
            "games": 100,
            "wins": 50,
            "winrate": 0.5,
            "core_cards": [f"CARD_{index}"],
            "field_availability": {
                "core_cards": {"available": True, "reason": None}
            },
        }
        for index in range(10)
    ]
    archetypes = [
        {
            "archetype_id": index + 101,
            "archetype_name": f"Archetype {index}",
            "player_class": "mage",
            "games": 100,
            "wins": 50,
            "winrate": 0.5,
            "core_cards": [f"CARD_{index}"],
            "field_availability": {
                "core_cards": {"available": True, "reason": None}
            },
        }
        for index in range(10)
    ]
    if collection == "decks":
        decks[-1]["decklist"] = decks[0]["decklist"]
    else:
        archetypes[-1]["archetype_id"] = archetypes[0]["archetype_id"]

    report = contract_quality_report(
        "firestone_standard",
        {
            "type": "firestone_standard",
            "completeness_schema_version": 1,
            "row_retrieval": _complete_row_retrieval(20),
            "decks": decks,
            "archetypes": archetypes,
        },
    )

    assert report["ok"] is False
    assert report["retrieval_complete"] is False
    assert report["identity_checks"][collection]["duplicates"] == 1


@pytest.mark.parametrize("version", [0, 2, True, "1"])
def test_explicit_unsupported_completeness_version_fails_closed(
    version: object,
) -> None:
    structured = {
        "type": "arena_card_tiers",
        "completeness_schema_version": version,
        "cards": [_complete_arena_card(index) for index in range(900)],
    }

    report = contract_quality_report("hsreplay_arena_cards_advanced", structured)

    assert report["ok"] is False
    assert "unsupported completeness_schema_version" in "; ".join(
        report["warnings"]
    )
    with pytest.raises(StructuredSchemaError, match="supported version"):
        validate_structured_schema(structured)


def test_v1_schema_requires_every_expected_descriptor() -> None:
    with pytest.raises(StructuredSchemaError, match="field_availability is required"):
        validate_structured_schema(
            {
                "type": "bg_minions",
                "completeness_schema_version": 1,
                "minions": [
                    {
                        "minion": "Complete but unversioned row shape",
                        "impact": 0.1,
                        "win_share": "50%",
                        "popularity": "5%",
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "descriptor",
    [
        {"available": "false", "reason": "no_current_patch_aggregates"},
        {"available": False, "reason": None},
        {"available": True, "reason": None},
    ],
)
def test_schema_rejects_malformed_or_contradictory_availability(
    descriptor: dict[str, object],
) -> None:
    with pytest.raises(StructuredSchemaError, match="field_availability.impact"):
        validate_structured_schema(
            {
                "type": "bg_minions",
                "minions": [
                    {
                        "minion": "No metrics minion",
                        "impact": None,
                        "field_availability": {"impact": descriptor},
                    }
                ],
            }
        )


def test_strict_bg_explained_absences_pass_retrieval_gate_but_keep_raw_score_zero() -> None:
    minions = [
        {
            "minion": f"Minion {index}",
            "minion_dbf_id": index + 1,
            "impact": None,
            "win_share": None,
            "popularity": None,
            "field_availability": {
                field: {
                    "available": False,
                    "reason": "no_current_patch_aggregates",
                }
                for field in ("impact", "win_share", "popularity")
            },
        }
        for index in range(50)
    ]
    payload = {
        "type": "bg_minions",
        "completeness_schema_version": 1,
        "row_retrieval": _complete_row_retrieval(50),
        "minions": minions,
    }

    contract = contract_quality_report("hsreplay_battlegrounds_minions", payload)
    semantic = validate_structured("hsreplay_battlegrounds_minions", payload)

    assert contract["ok"] is True, contract["warnings"]
    assert contract["quality_score"] == 0.0
    assert contract["retrieval_completeness_score"] == 1.0
    assert contract["retrieval_complete"] is True
    assert semantic.ok, semantic.reason


def test_strict_arena_explained_zero_game_rows_pass_semantic_and_retrieval_gates() -> None:
    cards = [_complete_arena_card(index) for index in range(900)]
    for card in cards[-200:]:
        card["deck_winrate"] = None
        card["winrate_when_drawn"] = None
        card["winrate_when_played"] = None
        card["in_runs"] = "0.00%"
        card["avg_copies"] = 0
        card["times_played"] = 0
        card["field_availability"] = {
            field: {"available": False, "reason": "no_games_in_window"}
            for field in (
                "deck_winrate",
                "winrate_when_drawn",
                "winrate_when_played",
            )
        }
    payload = {
        "type": "arena_card_tiers",
        "completeness_schema_version": 1,
        "primary_class": "ALL",
        "selected_class": "ALL",
        "row_retrieval": row_retrieval_evidence(
            raw_rows=900,
            eligible_rows=900,
            normalized_rows=900,
            scope="primary_class:ALL",
        ),
        "cards": cards,
    }

    contract = contract_quality_report("hsreplay_arena_cards_advanced", payload)
    semantic = validate_structured("hsreplay_arena_cards_advanced", payload)

    assert contract["ok"] is True, contract["warnings"]
    assert contract["metric_availability_score"] == 0.8667
    assert contract["retrieval_completeness_score"] == 1.0
    assert contract["retrieval_complete"] is True
    assert semantic.ok, semantic.reason


def test_strict_arena_all_zero_game_rows_have_coherent_presence_evidence() -> None:
    cards = [_complete_arena_card(index) for index in range(900)]
    for card in cards:
        card["deck_winrate"] = None
        card["winrate_when_drawn"] = None
        card["winrate_when_played"] = None
        card["in_runs"] = "0.00%"
        card["avg_copies"] = 0
        card["times_played"] = 0
        card["field_availability"] = {
            field: {"available": False, "reason": "no_games_in_window"}
            for field in (
                "deck_winrate",
                "winrate_when_drawn",
                "winrate_when_played",
            )
        }

    semantic = validate_structured(
        "hsreplay_arena_cards_advanced",
        {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "cards": cards,
        },
    )

    assert semantic.ok, semantic.reason
    assert semantic.metrics["has_tier_labels"] is True
    assert semantic.metrics["retrieved_winrates"] == 900
    assert semantic.metrics["valid_winrates"] == 0


def test_strict_firestone_explained_generic_buckets_pass_retrieval_gate() -> None:
    rows = [
        {
            "archetype_id": index + 1,
            "archetype_name": f"Archetype {index}",
            "player_class": "mage",
            "games": 100,
            "wins": 50,
            "winrate": 0.5,
            "core_cards": [] if index < 6 else [f"CARD_{index}"],
            "field_availability": {
                "core_cards": {
                    "available": index >= 6,
                    "reason": (
                        None
                        if index >= 6
                        else "generic_class_bucket_without_observed_deck_cluster"
                    ),
                }
            },
        }
        for index in range(20)
    ]
    decks = [
        {
            **row,
            "decklist": f"deck-{index}",
        }
        for index, row in enumerate(rows[:10])
    ]
    archetypes = [dict(row) for row in rows[10:]]
    payload = {
        "type": "firestone_standard",
        "completeness_schema_version": 1,
        "row_retrieval": _complete_row_retrieval(20),
        "decks": decks,
        "archetypes": archetypes,
    }

    contract = contract_quality_report("firestone_standard", payload)

    assert contract["ok"] is True, contract["warnings"]
    assert contract["critical_fields"]["core_cards"]["rate"] == 0.7
    assert contract["critical_fields"]["core_cards"][
        "retrieval_completeness_rate"
    ] == 1.0
    assert contract["retrieval_complete"] is True


def test_strict_bg_impossible_normalized_metrics_fail_schema_semantic_and_retrieval() -> None:
    minions = [
        {
            "minion": f"Minion {index}",
            "minion_dbf_id": index + 1,
            "impact": -999 if index == 0 else 0.0,
            "avg_placement_with": 1000 if index == 0 else 4.0,
            "avg_placement_without": 1.0 if index == 0 else 4.0,
            "win_share": "50%",
            "popularity": "5%",
            "games_with_minion": 10,
            "games_without_minion": 190,
            "combat_rounds": [],
            "field_availability": {
                field: {"available": True, "reason": None}
                for field in ("impact", "win_share", "popularity")
            },
        }
        for index in range(50)
    ]
    payload = {
        "type": "bg_minions",
        "completeness_schema_version": 1,
        "population_completeness": "unverifiable",
        "upstream_freshness": _fresh_bg_evidence(),
        "row_retrieval": _complete_row_retrieval(50),
        "minions": minions,
    }

    with pytest.raises(StructuredSchemaError, match="avg_placement_with"):
        validate_structured_schema(payload)
    semantic = validate_structured("hsreplay_battlegrounds_minions", payload)
    contract = contract_quality_report("hsreplay_battlegrounds_minions", payload)

    assert semantic.ok is False
    assert "bg_minions.impossible_metrics" in {
        issue.code for issue in semantic.issues
    }
    assert contract["ok"] is False
    assert contract["retrieval_complete"] is False


@pytest.mark.parametrize("corruption", ["round_impact", "aggregate_games"])
def test_strict_bg_rounds_must_reconcile_with_derived_metrics(
    corruption: str,
) -> None:
    minions = [
        {
            "minion": f"Minion {index}",
            "minion_dbf_id": index + 1,
            "impact": 0.0,
            "avg_placement_with": 4.0,
            "avg_placement_without": 4.0,
            "win_share": "50%",
            "popularity": "5%",
            "games_with_minion": 10,
            "games_without_minion": 190,
            "combat_rounds": [
                {
                    "combat_round": 1,
                    "games_with_minion": 10,
                    "games_without_minion": 190,
                    "avg_placement_with": 4.0,
                    "avg_placement_without": 4.0,
                    "impact": 0.0,
                    "combat_winrate": "50%",
                    "combat_winrate_value": 50.0,
                    "wins": 5,
                    "losses": 5,
                }
            ],
            "field_availability": {
                field: {"available": True, "reason": None}
                for field in ("impact", "win_share", "popularity")
            },
        }
        for index in range(50)
    ]
    if corruption == "round_impact":
        minions[0]["combat_rounds"][0]["impact"] = 1.0
    else:
        minions[0]["games_with_minion"] = 11
    payload = {
        "type": "bg_minions",
        "completeness_schema_version": 1,
        "population_completeness": "unverifiable",
        "upstream_freshness": _fresh_bg_evidence(),
        "row_retrieval": _complete_row_retrieval(50),
        "minions": minions,
    }

    with pytest.raises(StructuredSchemaError, match="does not reconcile"):
        validate_structured_schema(payload)
    contract = contract_quality_report("hsreplay_battlegrounds_minions", payload)

    assert contract["ok"] is False
    assert contract["retrieval_complete"] is False
    assert contract["bg_minion_domain"]["invalid_rows"] == 1


def _arena_publish_payload(freshness: dict[str, object]) -> dict[str, object]:
    cards = [_complete_arena_card(index) for index in range(900)]
    return {
        "type": "arena_card_tiers",
        "completeness_schema_version": 1,
        "primary_class": "ALL",
        "selected_class": "ALL",
        "population_completeness": "unverifiable",
        "upstream_freshness": freshness,
        "row_retrieval": row_retrieval_evidence(
            raw_rows=900,
            eligible_rows=900,
            normalized_rows=900,
            scope="primary_class:ALL",
        ),
        "cards": cards,
    }


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("stale", "upstream_snapshot_too_old"),
        ("unknown", "unexpected_selected_params"),
        ("unknown", "invalid_meta_period_id"),
        ("unknown", "source_timestamp_in_future"),
        ("unknown", "body_last_modified_mismatch"),
    ],
)
def test_publish_gate_rejects_stale_or_invalid_upstream_freshness(
    status: str,
    reason: str,
) -> None:
    payload = _arena_publish_payload({"status": status, "reason": reason})

    gate = validate_candidate_for_publish(
        SOURCE_BY_ID["hsreplay_arena_cards_advanced"],
        {"structured": payload},
        backend=None,
    )

    assert gate.ok is False
    assert "upstream" in gate.reason.lower()


@pytest.mark.parametrize(
    "reason",
    ["missing_last_modified", "transport_evidence_unavailable"],
)
def test_publish_gate_allows_honest_missing_transport_evidence_as_unverified(
    reason: str,
) -> None:
    payload = _arena_publish_payload({"status": "unknown", "reason": reason})

    gate = validate_candidate_for_publish(
        SOURCE_BY_ID["hsreplay_arena_cards_advanced"],
        {"structured": payload},
        backend=None,
    )

    assert gate.ok is True, gate.reason
