import copy
from datetime import UTC, datetime

import pytest

from app.acquisition_coverage import ListingEvidence, coverage_report, source_view
from app.acquisition_panel import build_panel_snapshot
from app.sources import SOURCE_BY_ID, SOURCES

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def observation():
    view = source_view(
        SOURCE_BY_ID["hsreplay_battlegrounds_comps"],
        snapshot_id="capture-1",
        patch_id="patch-1",
    )
    report = coverage_report(
        view,
        listing=ListingEvidence(
            view.scope_id,
            ("hsreplay-1", "hsreplay-2"),
            exhausted=True,
            expected_count=2,
            view_confirmed=True,
        ),
        detail_states={"hsreplay-1": "succeeded", "hsreplay-2": "retry"},
    )
    return {
        "observed_at": "2026-09-05T11:00:00Z",
        "coverage": report,
        "credits_spent": 5,
        "unknown_cost_attempts": 1,
    }


def test_registry_without_observations_is_unknown_not_zero():
    result = build_panel_snapshot([], now=NOW)
    assert len(result["sources"]) == len(SOURCES)
    assert all(row["coverage"]["status"] == "unknown" for row in result["sources"])
    assert all(
        row["observed_at"] is None and row["credits_spent"] is None
        for row in result["sources"]
    )


def test_snapshot_preserves_scope_and_queue_evidence_without_raw_payload():
    obs = observation()
    obs["rows"] = [{"secret": "never-export-this"}]
    before = copy.deepcopy(obs)
    result = build_panel_snapshot([obs], now=NOW)
    row = next(
        row
        for row in result["sources"]
        if row["source_id"] == obs["coverage"]["source_id"]
    )
    assert row["coverage"] == obs["coverage"]
    assert row["coverage"]["unresolved_details"] == 1
    assert row["observed_at"] == obs["observed_at"]
    assert row["credits_spent"] == 5 and row["unknown_cost_attempts"] == 1
    assert "never-export-this" not in str(result)
    assert obs == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("found", True),
        ("found", -1),
        ("status", "complete_for_view"),
        ("unresolved_details", 0),
        ("scope_id", "foreign"),
        ("expected_count", 1),
        ("listing_percent", 99),
        ("query", {"rank": ["wrong"]}),
        ("listing_complete", "true"),
    ],
)
def test_contradictory_evidence_is_rejected(field, value):
    obs = observation()
    obs["coverage"][field] = value
    with pytest.raises(ValueError):
        build_panel_snapshot([obs], now=NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"observed_at": "tomorrow"},
        {"observed_at": "2026-09-06T12:00:00Z"},
        {"credits_spent": -1},
        {"unknown_cost_attempts": True},
    ],
)
def test_invalid_observation_metadata_is_rejected(change):
    with pytest.raises(ValueError):
        build_panel_snapshot([{**observation(), **change}], now=NOW)


def test_duplicate_source_snapshot_is_rejected():
    with pytest.raises(ValueError):
        build_panel_snapshot([observation(), observation()], now=NOW)


def test_unknown_report_requires_boolean_evidence_flags():
    view = source_view(
        SOURCE_BY_ID["hsreplay_battlegrounds_comps"], snapshot_id="capture-1"
    )
    report = coverage_report(view)
    report["listing_complete"] = 0
    with pytest.raises(ValueError):
        build_panel_snapshot(
            [{"coverage": report, "observed_at": "2026-09-05T11:00:00Z"}], now=NOW
        )


def test_malformed_source_identity_has_a_fixed_validation_error():
    obs = observation()
    obs["coverage"]["source_id"] = []
    with pytest.raises(ValueError):
        build_panel_snapshot([obs], now=NOW)
