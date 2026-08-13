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

    with patch("app.routers.system.load_resolved_public_dataset", side_effect=dataset):
        legacy_response = client.get(
            "/v1/system/sources?site=hsreplay&category=arena"
        )
        response = client.get("/v1/sources?site=hsreplay&category=arena")

    assert legacy_response.status_code == 200
    assert response.status_code == 200
    assert response.json() == legacy_response.json()
    body = response.json()
    assert body["meta"]["source_id"] == "source_registry"
    assert body["meta"]["count"] == len(body["data"])
    assert body["meta"]["stale"] is False
    assert all(
        row["site"] == "hsreplay" and row["category"] == "arena" for row in body["data"]
    )
    assert any(
        row["id"] == "hsreplay_arena" and row["has_dataset"] for row in body["data"]
    )


def test_v1_system_paths_do_not_replace_legacy_paths() -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    for legacy, system_path, canonical in (
        ("/sources", "/v1/system/sources", "/v1/sources"),
        ("/datasets", "/v1/system/datasets", "/v1/datasets"),
        ("/health", "/v1/system/health", "/v1/health"),
    ):
        assert legacy in paths
        assert system_path in paths
        assert canonical in paths


def test_v1_parsing_reliability_returns_sanitized_public_contract() -> None:
    report = {
        "methodology": {
            "version": "logical-source-observed-v7",
            "unit": "one terminal outcome per source in a refresh run",
            "scope": "observed_scrape_and_pipeline_sources",
            "completeness": "observed_attempts_only",
            "limitations": [
                "missing_scheduled_pipeline_windows_not_detectable_until_ledger",
                "best_effort_write_gaps_not_detectable",
            ],
            "coverage_method": "complete_generic_refresh_per_24h_bucket",
            "coverage_scope": "generic_scrape_sources_only",
            "coverage_max_gap_hours": 25.0,
            "coverage_cohort_method": "current_canonical_scrape_registry_hash",
            "combined_slo_readiness": "collecting_pipeline_schedule_ledger",
            "eligible_outcomes": [
                "fresh_published",
                "provisional",
                "lkg_served",
                "failed",
                "timed_out",
            ],
            "excluded_outcomes": ["skipped"],
            "slo_target_rate_pct": 99.0,
            "failure_reason_values": [
                "proxy_payment",
                "authentication",
                "rate_limited",
                "access_blocked",
                "upstream_4xx",
                "upstream_5xx",
                "timeout",
                "transport",
                "unavailable",
                "contract",
                "parse_error",
                "regression",
                "backend_policy",
                "ai_quarantine",
                "publication_sync",
                "preflight",
                "dependency",
                "unknown",
            ],
            "ai_accuracy_method": "human_labels_required",
        },
        "generated_at": "2026-08-11T12:00:00+00:00",
        "coverage_cohort_hash": "a" * 64,
        "coverage_started_at": "2026-08-10T12:00:00+00:00",
        "windows": [
            {
                "window": "24h",
                "from_at": "2026-08-10T12:00:00+00:00",
                "to_at": "2026-08-11T12:00:00+00:00",
                "measurement_status": "collecting",
                "coverage_ratio": 1.0,
                "physical_attempts": 12,
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
                "failure_reasons": {
                    "proxy_payment": 0,
                    "authentication": 0,
                    "rate_limited": 0,
                    "access_blocked": 0,
                    "upstream_4xx": 0,
                    "upstream_5xx": 0,
                    "timeout": 0,
                    "transport": 0,
                    "unavailable": 0,
                    "contract": 0,
                    "parse_error": 0,
                    "regression": 0,
                    "backend_policy": 0,
                    "ai_quarantine": 0,
                    "publication_sync": 0,
                    "preflight": 0,
                    "dependency": 0,
                    "unknown": 1,
                },
                "full_fresh_rate_pct": 80.0,
                "accepted_fresh_rate_pct": 90.0,
                "data_available_rate_pct": 90.0,
                "freshness_slo": {
                    "target_rate_pct": 99.0,
                    "objective_status": "collecting",
                    "good_attempts": 8,
                    "bad_attempts": 2,
                    "allowed_bad_attempts": 0.1,
                    "bad_attempts_over_budget": 2,
                    "error_budget_remaining_attempts": -1.9,
                    "error_budget_consumed_pct": 2000.0,
                },
                "availability_slo": {
                    "target_rate_pct": 99.0,
                    "objective_status": "collecting",
                    "good_attempts": 9,
                    "bad_attempts": 1,
                    "allowed_bad_attempts": 0.1,
                    "bad_attempts_over_budget": 1,
                    "error_budget_remaining_attempts": -0.9,
                    "error_budget_consumed_pct": 1000.0,
                },
                "ai_quality": {
                    "candidate_review": {
                        "all_parser_attempts": 10,
                        "attempted": 2,
                        "completed": 1,
                        "errors": 1,
                        "coverage_of_all_parser_attempts_pct": 10.0,
                        "valid_response_rate_pct": 50.0,
                        "verdicts": {"pass": 1, "fail": 0, "uncertain": 0},
                        "quarantined": 0,
                    },
                    "failure_diagnosis": {
                        "all_problem_attempts": 1,
                        "attempted": 1,
                        "completed": 1,
                        "errors": 0,
                        "coverage_of_all_problem_attempts_pct": 100.0,
                        "valid_response_rate_pct": 100.0,
                        "classifications": {
                            "healthy": 0,
                            "anomalous": 1,
                            "inconclusive": 0,
                        },
                        "failure_domains": {
                            "identity": 0,
                            "protection": 0,
                            "auth": 0,
                            "scope": 0,
                            "schema": 0,
                            "completeness": 0,
                            "semantics": 0,
                            "freshness": 0,
                            "regression": 1,
                            "backend_policy": 0,
                            "unknown": 0,
                        },
                    },
                    "calibration": {
                        "status": "not_calibrated",
                        "human_labeled_examples": 0,
                        "limitation": "human_labels_not_collected",
                    },
                },
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
