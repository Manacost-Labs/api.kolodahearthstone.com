import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from app.demo import build_demo_view, build_overview
from app.hsguru_evidence import hsguru_data_evidence
from app.main import source_payload
from app.sources import SOURCE_BY_ID

NOW = datetime(2026, 1, 3, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "hsguru_replay"


def test_recent_download_does_not_claim_upstream_freshness():
    dataset = {"fetched_at": "2026-01-02T10:00:00Z", "data": {}}
    with (
        patch("app.main.load_status", return_value={"state": "ok"}),
        patch("app.parser_control.load_resolved_public_dataset", return_value=dataset),
    ):
        result = source_payload("hsguru_matchups_legend")
    evidence = result["data_evidence"]
    assert evidence["collection"]["fetched_at"] == "2026-01-02T10:00:00Z"
    assert evidence["upstream"]["status"] == "unknown"
    assert evidence["upstream"]["as_of"] is None
    assert "fresh_only_eligible" not in result


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        True,
        12,
        {},
        [],
        "",
        "x" * 100,
        "2026-01-01",
        "2026-01-01T12:00:00",
        "9999-12-31T23:59:59Z",
    ],
)
def test_untrusted_collection_timestamps_are_unknown(timestamp):
    assert (
        hsguru_data_evidence({"fetched_at": timestamp}, now=NOW)["collection"][
            "fetched_at"
        ]
        is None
    )


def test_components_keep_old_cache_and_missing_evidence_visible():
    dataset = json.loads((FIXTURES / "partial_analysis.json").read_text())
    result = hsguru_data_evidence(dataset, now=NOW)
    assert result["upstream"]["status"] == "unknown"
    assert result["coverage"] == {
        "status": "partial",
        "scope": "observed_archetype_components",
        "observed_archetypes": 2,
    }
    matchups, cards = result["components"]
    assert matchups["state_counts"] == {"complete": 2}
    assert cards["state_counts"] == {"cached": 1, "unknown": 1}
    assert cards["oldest_updated_at"] == "2025-12-01T00:00:00Z"
    assert cards["missing_updated_at_count"] == 1
    assert "private-error" not in json.dumps(result)


def test_nested_malformed_component_cannot_be_promoted_by_legacy_fields():
    row = {
        "components": {"card_stats": []},
        "card_stats_state": "complete",
        "card_stats_updated_at": "2026-01-01T00:00:00Z",
    }
    result = hsguru_data_evidence(
        {
            "data": {
                "structured": {
                    "type": "hsguru_archetype_analysis",
                    "archetypes": [row, None],
                }
            }
        },
        now=NOW,
    )
    assert result["components"][1]["state_counts"] == {"unknown": 2}
    assert result["components"][1]["oldest_updated_at"] is None


def test_reported_components_never_claim_full_upstream_catalogue():
    component = {
        "state": "complete",
        "checked_at": "2026-01-02T01:00:00+01:00",
        "updated_at": "2026-01-02T00:00:00Z",
    }
    result = hsguru_data_evidence(
        {
            "data": {
                "structured": {
                    "type": "hsguru_archetype_analysis",
                    "archetypes": [
                        {"components": {"matchups": component, "card_stats": component}}
                    ],
                }
            }
        },
        now=NOW,
    )
    assert result["coverage"]["status"] == "reported"
    assert result["upstream"]["status"] == "unknown"
    assert result["components"][0]["oldest_checked_at"] == "2026-01-02T00:00:00Z"


@pytest.mark.parametrize("components", [[], "broken", None, {}])
def test_present_invalid_component_container_does_not_promote_legacy(components):
    row = {"components": components}
    for name in ("matchups", "card_stats"):
        row.update(
            {
                f"{name}_state": "complete",
                f"{name}_checked_at": "2026-01-02T00:00:00Z",
                f"{name}_updated_at": "2026-01-02T00:00:00Z",
            }
        )
    result = hsguru_data_evidence(
        {
            "data": {
                "structured": {"type": "hsguru_archetype_analysis", "archetypes": [row]}
            }
        },
        now=NOW,
    )
    assert result["coverage"]["status"] == "partial"
    for component in result["components"]:
        assert component["state_counts"] == {"unknown": 1}
        assert component["oldest_updated_at"] is None


@pytest.mark.parametrize(
    "dataset", [None, {}, {"data": []}, {"data": {"structured": "bad"}}]
)
def test_missing_or_malformed_payload_never_fabricates_evidence(dataset):
    result = hsguru_data_evidence(dataset, now=NOW)
    assert result["collection"]["fetched_at"] is None
    assert result["coverage"]["status"] == "unknown"
    assert result["components"] == []


def test_overview_and_detail_use_the_published_dataset_not_last_attempt():
    source = SOURCE_BY_ID["hsguru_archetype_analysis"]
    dataset = json.loads((FIXTURES / "partial_analysis.json").read_text())
    status = {
        "state": "partial",
        "fetched_at": "2026-01-03T00:00:00Z",
        "serving_cached_dataset": True,
    }
    with (
        patch("app.demo.SOURCES", (source,)),
        patch("app.demo.load_status", return_value=status),
        patch("app.demo.load_dataset", return_value=dataset),
        patch("app.parser_control.load_resolved_public_dataset", return_value=dataset),
        patch("app.parser_control.resolve_public_dataset", return_value=dataset),
        patch("app.stale_monitor.find_stale_sources", return_value=[]),
    ):
        overview = build_overview()["sources"][0]
        detail = build_demo_view(source.id)
    assert overview["data_evidence"] == detail["data_evidence"]
    assert (
        overview["data_evidence"]["collection"]["fetched_at"] == dataset["fetched_at"]
    )
    assert overview["serving_cached_dataset"] is True


def test_absent_dataset_never_uses_attempt_timestamp():
    source = SOURCE_BY_ID["hsguru_matchups_legend"]
    with (
        patch("app.demo.SOURCES", (source,)),
        patch(
            "app.demo.load_status", return_value={"fetched_at": "2026-01-02T00:00:00Z"}
        ),
        patch("app.demo.load_dataset", return_value=None),
        patch("app.parser_control.load_resolved_public_dataset", return_value=None),
        patch("app.stale_monitor.find_stale_sources", return_value=[]),
    ):
        overview = build_overview()["sources"][0]
        detail = build_demo_view(source.id)
    assert overview["data_evidence"] == detail["data_evidence"]
    assert overview["data_evidence"]["collection"]["fetched_at"] is None
    assert overview["data_evidence"]["has_dataset"] is False


def test_other_providers_are_unchanged():
    with (
        patch("app.main.load_status", return_value=None),
        patch("app.parser_control.load_resolved_public_dataset", return_value=None),
    ):
        assert "data_evidence" not in source_payload("hsreplay_archetypes")
