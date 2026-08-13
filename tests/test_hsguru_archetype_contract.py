from __future__ import annotations

from copy import deepcopy

import pytest

from app.publish_gate import validate_candidate_for_publish
from app.source_contracts import get_contract
from app.source_validators import validate_structured
from app.sources import SOURCE_BY_ID

SOURCE_ID = "hsguru_archetype_analysis"
CHECKED_AT = "2026-08-13T12:00:00+00:00"


def _row(format_name: str, archetype: str) -> dict[str, object]:
    return {
        "format": format_name,
        "archetype": archetype,
        "rank": "legend",
        "period": "past_week",
        "state": "ok",
        "class_matchups": [
            {
                "class_key": "mage",
                "class_label": "Mage",
                "winrate": 51.2,
                "games": 120,
                "share_pct": 10.0,
            }
        ],
        "card_stats": [
            {
                "card_id": "TEST_001",
                "card_name": "Test Card",
                "mulligan_impact": 1.2,
                "mulligan_count": 120,
                "drawn_impact": 0.8,
                "drawn_count": 100,
            }
        ],
        "matchups_state": "complete",
        "card_stats_state": "complete",
        "matchups_checked_at": CHECKED_AT,
        "card_stats_checked_at": CHECKED_AT,
        "matchups_updated_at": CHECKED_AT,
        "card_stats_updated_at": CHECKED_AT,
        "checked_at": CHECKED_AT,
        "updated_at": CHECKED_AT,
        "components": {
            "matchups": {
                "state": "complete",
                "checked_at": CHECKED_AT,
                "updated_at": CHECKED_AT,
            },
            "card_stats": {
                "state": "complete",
                "checked_at": CHECKED_AT,
                "updated_at": CHECKED_AT,
            },
        },
    }


def _structured() -> dict[str, object]:
    rows = [
        _row("standard", "Tempo Mage"),
        _row("wild", "Even Shaman"),
    ]
    return {
        "type": SOURCE_ID,
        "schema_version": 2,
        "criteria": {
            "rank": "legend",
            "period": "past_week",
            "formats": ["standard", "wild"],
            "requires_decks": False,
            "target_source": "hsguru_meta_matrix:legend:past_week",
        },
        "expected_targets": [
            {"format": "standard", "archetype": "Tempo Mage"},
            {"format": "wild", "archetype": "Even Shaman"},
        ],
        "expected_targets_total": 2,
        "coverage": {
            "standard": {
                "archetypes": 1,
                "with_matchups": 1,
                "with_card_stats": 1,
                "complete": 1,
            },
            "wild": {
                "archetypes": 1,
                "with_matchups": 1,
                "with_card_stats": 1,
                "complete": 1,
            },
        },
        "negative_cache": [],
        "archetypes": rows,
    }


def _candidate(structured: dict[str, object]) -> dict[str, object]:
    return {"structured": structured}


def _validation_codes(structured: dict[str, object]) -> set[str]:
    return {issue.code for issue in validate_structured(SOURCE_ID, structured).issues}


def test_hsguru_archetype_analysis_has_a_strict_source_contract() -> None:
    contract = get_contract(SOURCE_ID)

    assert contract is not None
    assert contract.structured_type == SOURCE_ID
    assert contract.min_rows == 1
    assert contract.critical_fields == ("format", "archetype")
    assert contract.min_field_fill_rate == 1.0


def test_complete_hsguru_archetype_analysis_passes_the_generic_publish_gate() -> None:
    source = SOURCE_BY_ID[SOURCE_ID]

    result = validate_candidate_for_publish(
        source,
        _candidate(_structured()),
        backend="scrape_do_super",
    )

    assert result.ok, result.reason


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda payload: payload.pop("schema_version"),
            "hsguru_analysis.schema_version",
        ),
        (
            lambda payload: payload.__setitem__("schema_version", 1),
            "hsguru_analysis.schema_version",
        ),
        (
            lambda payload: payload["criteria"].__setitem__("rank", "diamond"),
            "hsguru_analysis.criteria",
        ),
        (
            lambda payload: payload["criteria"].__setitem__("period", "past_day"),
            "hsguru_analysis.criteria",
        ),
        (
            lambda payload: payload["criteria"].__setitem__(
                "formats", ["standard", "twist"]
            ),
            "hsguru_analysis.criteria_formats",
        ),
        (
            lambda payload: payload.__setitem__("archetypes", []),
            "hsguru_analysis.empty_targets",
        ),
        (
            lambda payload: payload["archetypes"][1].__setitem__("format", "standard"),
            "hsguru_analysis.missing_expected_targets",
        ),
    ],
)
def test_identity_and_scope_fail_closed(mutation, expected_code: str) -> None:
    structured = _structured()
    mutation(structured)

    assert expected_code in _validation_codes(structured)


def test_duplicate_archetype_targets_are_rejected_case_insensitively() -> None:
    structured = _structured()
    duplicate = deepcopy(structured["archetypes"][0])
    duplicate["archetype"] = " tempo mage "
    structured["archetypes"].append(duplicate)

    assert "hsguru_analysis.duplicate_targets" in _validation_codes(structured)


def test_expected_target_metadata_requires_exact_target_coverage() -> None:
    structured = _structured()
    structured["expected_targets"].append(
        {"format": "standard", "archetype": "Control Warrior"}
    )
    structured["expected_targets_total"] = 3

    assert "hsguru_analysis.missing_expected_targets" in _validation_codes(structured)


def test_expected_target_count_without_target_metadata_fails_closed() -> None:
    structured = _structured()
    structured.pop("expected_targets")

    assert "hsguru_analysis.expected_targets_missing" in _validation_codes(structured)


def test_coverage_must_equal_the_archetype_and_component_rows() -> None:
    structured = _structured()
    structured["coverage"]["standard"]["with_card_stats"] = 0

    assert "hsguru_analysis.coverage_mismatch" in _validation_codes(structured)


def test_component_summary_must_match_scalar_states_and_actual_rows() -> None:
    structured = _structured()
    standard = structured["archetypes"][0]
    standard["components"]["matchups"]["state"] = "transport_error"

    assert "hsguru_analysis.component_mismatch" in _validation_codes(structured)


def test_cached_lkg_components_cannot_be_published_as_fresh() -> None:
    structured = _structured()
    standard = structured["archetypes"][0]
    standard["matchups_state"] = "cached"
    standard["components"]["matchups"]["state"] = "cached"
    standard["state"] = "partial"

    codes = _validation_codes(structured)

    assert "hsguru_analysis.not_fresh" in codes


def test_sparse_card_stats_are_fresh_when_the_collector_verified_a_low_sample() -> None:
    structured = _structured()
    standard = structured["archetypes"][0]
    standard["card_stats"] = []
    standard["card_stats_state"] = "sparse_valid"
    standard["components"]["card_stats"]["state"] = "sparse_valid"
    structured["coverage"]["standard"]["with_card_stats"] = 0
    report = validate_structured(SOURCE_ID, structured)

    assert report.ok, report.reason
    assert report.metrics["verified_sparse_targets"] == 1


def test_zero_card_stats_need_a_matching_fresh_negative_cache_entry() -> None:
    structured = _structured()
    standard = structured["archetypes"][0]
    standard["card_stats"] = []
    standard["card_stats_state"] = "source_no_data"
    standard["components"]["card_stats"]["state"] = "source_no_data"
    standard["state"] = "partial"
    structured["coverage"]["standard"]["with_card_stats"] = 0
    structured["coverage"]["standard"]["complete"] = 0
    missing_evidence = _validation_codes(structured)
    structured["negative_cache"] = [
        {
            "format": "standard",
            "archetype": "Tempo Mage",
            "kind": "card_stats",
            "state": "source_no_data",
            "checked_at": CHECKED_AT,
        }
    ]
    accepted = validate_structured(SOURCE_ID, structured)

    assert "hsguru_analysis.zero_stats_without_evidence" in missing_evidence
    assert accepted.ok, accepted.reason
