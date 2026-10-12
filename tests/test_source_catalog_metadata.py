from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app import catalog_metadata, source_catalog, storage
from app.sources import SOURCE_BY_ID


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    catalog_metadata._cache.clear()
    return tmp_path


def document(stamp, provisional=False):
    return {
        "source_id": "hsreplay_archetypes",
        "fetched_at": stamp,
        "data": {
            "structured": {
                "type": "archetypes",
                "provisional": provisional,
                "archetypes": [{"id": 1}],
            }
        },
    }


def test_summary_reads_only_index_even_after_process_restart(data_root):
    stamp = datetime.now(UTC).isoformat()
    path = storage.dataset_path("hsreplay_archetypes")
    storage.write_json(path, document(stamp))
    catalog_metadata._cache.clear()
    original = Path.read_text

    def read(self, *args, **kwargs):
        assert self != path, "Inventory must not decode the full dataset"
        return original(self, *args, **kwargs)

    with patch.object(Path, "read_text", read):
        assert (
            source_catalog.public_catalog_metadata("hsreplay_archetypes")["fetched_at"]
            == stamp
        )


def test_cache_invalidates_on_atomic_replace_and_returns_copies(data_root):
    path = storage.dataset_path("hsreplay_archetypes")
    storage.write_json(path, document("2026-10-01T00:00:00+00:00"))
    first = catalog_metadata.load_catalog_metadata(path)
    first["fetched_at"] = "mutated"
    assert catalog_metadata.load_catalog_metadata(path)["fetched_at"] != "mutated"
    storage.write_json(path, document("2026-10-02T00:00:00+00:00"))
    assert (
        catalog_metadata.load_catalog_metadata(path)["fetched_at"]
        == "2026-10-02T00:00:00+00:00"
    )
    path.unlink()
    assert catalog_metadata.load_catalog_metadata(path) is None


def test_legacy_file_backfills_once(data_root):
    path = storage.dataset_path("hsreplay_archetypes")
    path.write_text('{"fetched_at":"2026-10-01T00:00:00+00:00","data":{}}')
    catalog_metadata.load_catalog_metadata(path)
    catalog_metadata._cache.clear()
    assert catalog_metadata._index_path(path).exists()
    assert catalog_metadata.load_catalog_metadata(path)["fetched_at"]


def test_stale_index_and_raced_record_never_override_publication(data_root):
    path = storage.dataset_path("hsreplay_archetypes")
    storage.write_json(path, document("2026-10-01T00:00:00+00:00"))
    before = catalog_metadata._version(path)
    storage.write_json(path, document("2026-10-02T00:00:00+00:00"))
    catalog_metadata.record_catalog_metadata(
        path, document("wrong"), expected_file=before
    )
    assert catalog_metadata.load_catalog_metadata(path)["fetched_at"].startswith(
        "2026-10-02"
    )


def test_stable_and_early_selection_matches_real_resolver(data_root):
    from app.post_patch_policy import STABLE_PUBLICATION_BASELINE_LABEL

    sid = "hsguru_meta_standard_legend"
    storage.write_json(
        storage.dataset_path(sid), document("2026-10-02T00:00:00+00:00", True)
    )
    storage.write_json(
        storage.baseline_path(sid, STABLE_PUBLICATION_BASELINE_LABEL),
        document("2026-10-01T00:00:00+00:00"),
    )
    with patch("app.source_catalog.effective_publication_mode", return_value="stable"):
        assert source_catalog.public_catalog_metadata(sid)["fetched_at"].startswith(
            "2026-10-01"
        )
    with patch("app.source_catalog.effective_publication_mode", return_value="early"):
        assert source_catalog.public_catalog_metadata(sid)["fetched_at"].startswith(
            "2026-10-02"
        )


def test_disabled_source_does_not_read_dataset(data_root):
    with (
        patch("app.source_catalog.source_operationally_enabled", return_value=False),
        patch("app.source_catalog.load_catalog_metadata") as read,
    ):
        assert source_catalog.public_catalog_metadata("firestone_standard") is None
    read.assert_not_called()


@pytest.mark.parametrize("age,expected", [(85, "fresh"), (121, "stale"), (-1, "stale")])
def test_actual_source_ttl_and_future_timestamp(data_root, age, expected):
    now = datetime.now(UTC)
    projection = {"fetched_at": (now - timedelta(hours=age)).isoformat()}
    result = source_catalog.source_summary(
        SOURCE_BY_ID["hsreplay_archetypes"],
        now=now,
        next_runs={},
        projection=projection,
    )
    assert result["freshness_state"] == expected


def test_upstream_pending_does_not_turn_old_content_fresh(data_root):
    now = datetime.now(UTC)
    storage.save_status(
        "vicious_syndicate_radars",
        {
            "state": "ok",
            "last_refresh_upstream_state": "upstream_publication_pending",
            "serving_cached_dataset": True,
            "cached_content_temporally_grandfathered": True,
        },
    )
    result = source_catalog.source_summary(
        SOURCE_BY_ID["vicious_syndicate_radars"],
        now=now,
        next_runs={},
        projection={"fetched_at": (now - timedelta(days=20)).isoformat()},
    )
    assert result["freshness_state"] == "upstream_pending"
    assert result["stale"] is True


def test_completeness_reconciles_identities_not_only_count():
    from app.source_completeness import completeness_view

    row = {
        "format": "wild",
        "archetype": "A",
        "matchups_state": "complete",
        "card_stats_state": "sparse_valid",
        "card_stats": [],
    }
    payload = {
        "data": {
            "structured": {
                "type": "hsguru_archetype_analysis",
                "expected_targets_total": 1,
                "expected_targets": [{"format": "wild", "archetype": "A"}],
                "archetypes": [row],
            }
        }
    }
    assert completeness_view(payload)["state"] == "complete"
    assert completeness_view(payload)["population_state"] == "unknown"
    row["archetype"] = "wrong"
    assert completeness_view(payload)["state"] == "incomplete"


def test_without_oracle_completeness_stays_unknown():
    from app.source_completeness import completeness_view

    assert (
        completeness_view(
            {
                "fetched_at": datetime.now(UTC).isoformat(),
                "data": {"structured": {"rows": [1]}},
            }
        )["state"]
        == "unknown"
    )


def test_metadata_projection_retains_bounded_upstream_evidence(data_root):
    from starlette.testclient import TestClient

    from app.main import app

    source_id = "hsreplay_meta_archetypes_legend_eu_1d"
    stamp = datetime.now(UTC).isoformat()
    storage.write_json(
        storage.dataset_path(source_id),
        {
            "source_id": source_id,
            "fetched_at": stamp,
            "data": {
                "structured": {
                    "upstream_freshness": {
                        "status": "fresh",
                        "age_seconds": 60,
                        "body_as_of": stamp,
                        "response_headers": {"authorization": "not-public"},
                    }
                }
            },
        },
    )
    response = TestClient(app).get("/v1/sources?site=hsreplay")
    assert response.status_code == 200
    row = next(item for item in response.json()["data"] if item["id"] == source_id)
    assert row["fresh_only_eligible"] is True
    assert row["upstream_freshness"]["status"] == "fresh"
    assert row["freshness_state"] == "fresh"
    assert "not-public" not in response.text
    assert "response_headers" not in response.text
