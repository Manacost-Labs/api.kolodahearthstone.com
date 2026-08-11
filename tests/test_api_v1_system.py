from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_v1_sources_returns_registry_envelope() -> None:
    fetched_at = datetime.now(UTC).isoformat()

    def dataset(source_id: str) -> dict[str, object] | None:
        if source_id == "hsreplay_arena":
            return {"fetched_at": fetched_at}
        return None

    with patch(
        "app.routers.system.load_resolved_public_dataset", side_effect=dataset
    ):
        response = client.get("/v1/system/sources?site=hsreplay&category=arena")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source_id"] == "source_registry"
    assert body["meta"]["count"] == len(body["data"])
    assert body["meta"]["stale"] is False
    assert all(row["site"] == "hsreplay" and row["category"] == "arena" for row in body["data"])
    assert any(row["id"] == "hsreplay_arena" and row["has_dataset"] for row in body["data"])


def test_v1_system_paths_do_not_replace_legacy_paths() -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    for legacy, versioned in (
        ("/sources", "/v1/system/sources"),
        ("/datasets", "/v1/system/datasets"),
        ("/health", "/v1/system/health"),
    ):
        assert legacy in paths
        assert versioned in paths


def test_v1_parsing_reliability_returns_sanitized_public_contract() -> None:
    report = {
        "methodology": {
            "version": "logical-source-observed-v1",
            "unit": "one terminal outcome per source in a refresh run",
            "scope": "generic_refresh_sources",
            "completeness": "observed_attempts_only",
            "limitations": [
                "dedicated_pipeline_sources_excluded",
                "best_effort_write_gaps_not_detectable",
            ],
            "eligible_outcomes": [
                "fresh_published",
                "provisional",
                "lkg_served",
                "failed",
                "timed_out",
            ],
            "excluded_outcomes": ["skipped"],
        },
        "generated_at": "2026-08-11T12:00:00+00:00",
        "coverage_started_at": "2026-08-10T12:00:00+00:00",
        "windows": [
            {
                "window": "24h",
                "from_at": "2026-08-10T12:00:00+00:00",
                "to_at": "2026-08-11T12:00:00+00:00",
                "measurement_status": "observed",
                "coverage_ratio": 1.0,
                "total_attempts": 10,
                "eligible_attempts": 10,
                "counts": {
                    "fresh_published": 8,
                    "provisional": 1,
                    "lkg_served": 0,
                    "failed": 1,
                    "timed_out": 0,
                    "skipped": 0,
                },
                "full_fresh_rate_pct": 80.0,
                "accepted_fresh_rate_pct": 90.0,
                "data_available_rate_pct": 90.0,
            }
        ],
    }

    with patch("app.routers.system.build_reliability_report", return_value=report):
        response = client.get("/v1/system/parsing-reliability")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers
    body = response.json()
    assert body["data"] == report
    assert body["meta"] == {
        "source_id": "parser_reliability",
        "fetched_at": "2026-08-11T12:00:00+00:00",
        "stale": False,
        "count": 10,
    }
