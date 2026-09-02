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
        legacy_response = client.get("/v1/system/sources?site=hsreplay&category=arena")
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


def test_v1_sources_exposes_bounded_fresh_only_evidence_for_hsreplay_meta() -> None:
    source_id = "hsreplay_meta_archetypes_legend_eu_1d"
    freshness = {
        "status": "fresh",
        "reason": None,
        "age_seconds": 3600,
        "body_as_of": "2026-09-02T12:00:00+00:00",
        "response_headers": {"etag": "must-not-be-public"},
    }

    def dataset(candidate: str) -> dict[str, object] | None:
        if candidate != source_id:
            return None
        return {
            "fetched_at": "2026-09-02T13:00:00+00:00",
            "data": {"structured": {"upstream_freshness": freshness}},
        }

    with patch("app.routers.system.load_resolved_public_dataset", side_effect=dataset):
        response = client.get("/v1/sources?site=hsreplay&category=ranked")

    assert response.status_code == 200
    row = next(item for item in response.json()["data"] if item["id"] == source_id)
    assert row["fresh_only_eligible"] is True
    assert row["upstream_freshness"] == {
        "status": "fresh",
        "age_seconds": 3600,
        "body_as_of": "2026-09-02T12:00:00+00:00",
    }


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
            "version": "logical-source-observed-v15",
            "unit": "one terminal outcome per source in a refresh run",
            "scope": "observed_scrape_and_pipeline_sources",
            "completeness": "observed_attempts_plus_recorded_run_deficits",
            "limitations": [
                "entirely_missing_scheduled_runs_not_detectable_until_ledger",
                "best_effort_write_gaps_not_detectable",
                "parsesunix_rollout_rates_cover_observed_instrumented_attempts_only",
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
            "independently_ineligible_method": (
                "verified_upstream_absence_is_excluded_from_parser_slo_but_"
                "included_in_end_to_end_freshness"
            ),
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
            "missing_terminal_method": (
                "sum_positive_expected_minus_distinct_terminal_rows_per_recorded_logical_refresh"
            ),
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
                "observed_eligible_attempts": 10,
                "missing_terminal_windows": 2,
                "eligible_attempts": 12,
                "upstream_pending_attempts": 0,
                "end_to_end_attempts": 12,
                "counts": {
                    "fresh_published": 8,
                    "provisional": 1,
                    "lkg_served": 0,
                    "failed": 1,
                    "timed_out": 0,
                    "skipped": 0,
                },
                "outcome_recovery": {
                    "provisional": {
                        "events": 1,
                        "recovered_to_fresh": 1,
                        "reclassified_upstream_pending": 0,
                        "unresolved": 0,
                    },
                    "lkg_served": {
                        "events": 0,
                        "recovered_to_fresh": 0,
                        "reclassified_upstream_pending": 0,
                        "unresolved": 0,
                    },
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
                "full_fresh_rate_pct": 66.67,
                "end_to_end_fresh_rate_pct": 66.67,
                "accepted_fresh_rate_pct": 75.0,
                "data_available_rate_pct": 75.0,
                "freshness_slo": {
                    "target_rate_pct": 99.0,
                    "objective_status": "collecting",
                    "good_attempts": 8,
                    "bad_attempts": 4,
                    "allowed_bad_attempts": 0.12,
                    "bad_attempts_over_budget": 4,
                    "error_budget_remaining_attempts": -3.88,
                    "error_budget_consumed_pct": 3333.33,
                },
                "end_to_end_freshness_slo": {
                    "target_rate_pct": 99.0,
                    "objective_status": "collecting",
                    "good_attempts": 8,
                    "bad_attempts": 4,
                    "allowed_bad_attempts": 0.12,
                    "bad_attempts_over_budget": 4,
                    "error_budget_remaining_attempts": -3.88,
                    "error_budget_consumed_pct": 3333.33,
                },
                "availability_slo": {
                    "target_rate_pct": 99.0,
                    "objective_status": "collecting",
                    "good_attempts": 9,
                    "bad_attempts": 3,
                    "allowed_bad_attempts": 0.12,
                    "bad_attempts_over_budget": 3,
                    "error_budget_remaining_attempts": -2.88,
                    "error_budget_consumed_pct": 2500.0,
                },
                "verified_completeness": {
                    "instrumented_sources": 4,
                    "catalog_sources": 99,
                    "source_catalog_coverage_pct": 4.04,
                    "observed_instrumented_sources": 3,
                    "instrumented_source_observation_coverage_pct": 75.0,
                    "sources_meeting_target": 2,
                    "sources_below_target": 1,
                    "sources_without_observations": 1,
                    "source_target_attainment_pct": 50.0,
                    "macro_complete_fresh_rate_pct": 62.5,
                    "macro_target_met": False,
                    "worst_observed_source_rate_pct": 50.0,
                    "tracked_attempts": 4,
                    "complete_fresh": 3,
                    "states": {
                        "complete": 3,
                        "incomplete": 1,
                        "unknown": 0,
                    },
                    "coverage_of_all_parser_attempts_pct": 40.0,
                    "complete_fresh_rate_pct": 75.0,
                    "target_rate_pct": 99.0,
                    "objective_status": "collecting",
                },
                "parsesunix_rollout": {
                    "observed_attempts": 2,
                    "observed_sources": 2,
                    "shadow_attempts": 1,
                    "active_attempts": 1,
                    "transport_checked": 2,
                    "transport_validated": 2,
                    "transport_validated_rate_pct": 100.0,
                    "candidate_checked": 2,
                    "candidate_validated": 2,
                    "candidate_validated_rate_pct": 100.0,
                    "publication_checked": 1,
                    "publication_validated": 1,
                    "publication_validated_rate_pct": 100.0,
                    "http_status_compared": 1,
                    "http_status_matches": 1,
                    "http_status_match_rate_pct": 100.0,
                    "content_hash_compared": 1,
                    "content_hash_matches": 1,
                    "content_hash_match_rate_pct": 100.0,
                    "paid_requests_known_attempts": 2,
                    "paid_requests": 0,
                    "paid_cost_known_attempts": 2,
                    "paid_cost_usd": "0.000000",
                },
                "scheduled_reliability": {
                    "ledger_status": "partial",
                    "measurement_status": "collecting",
                    "schedule_coverage_ratio": 0.1429,
                    "temporal_coverage_ratio": 1.0,
                    "coverage_started_at": "2026-08-10T12:00:00+00:00",
                    "materialized_through": "2026-08-13T12:00:00+00:00",
                    "tracked_schedules": 2,
                    "catalog_schedules": 14,
                    "expected_slots": 12,
                    "eligible_slots": 11,
                    "excluded_slots": 1,
                    "pending_slots": 1,
                    "due_slots": 10,
                    "on_time_fresh": 8,
                    "on_time_upstream_pending": 0,
                    "on_time_nonfresh": 1,
                    "late": 0,
                    "missing": 1,
                    "on_time_fresh_rate_pct": 80.0,
                    "parser_eligible_due_slots": 10,
                    "parser_on_time_fresh_rate_pct": 80.0,
                    "target_rate_pct": 99.0,
                    "objective_status": "collecting",
                    "parser_objective_status": "collecting",
                },
                "ai_quality": {
                    "candidate_review": {
                        "all_parser_attempts": 10,
                        "attempted": 2,
                        "completed": 1,
                        "errors": 1,
                        "skipped": 3,
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

    convergence = {
        "ledger_status": "observed",
        "policy_version": 1,
        "total_chains": 3,
        "affected_sources": 2,
        "chain_states": {
            "waiting": 1,
            "running": 0,
            "fresh": 1,
            "upstream_pending": 1,
            "paused": 0,
            "quarantined": 0,
            "diagnosis_required": 0,
            "exhausted": 0,
            "cancelled": 0,
        },
        "total_attempts": 1,
        "attempt_states": {
            "queued": 0,
            "running": 0,
            "succeeded": 1,
            "failed": 0,
            "cancelled": 0,
        },
        "paid_requests": 1,
        "paid_cost_usd": "0.001500",
        "last_updated_at": "2026-08-11T11:59:00+00:00",
        "planner": {
            "mode": "shadow",
            "last_run_at": "2026-08-11T11:59:00+00:00",
            "scanned_terminal_events": 2,
            "scanned_missing_slots": 1,
            "planned_chains": 1,
            "planned_sources": 1,
            "skipped_events": 1,
        },
    }
    with (
        patch("app.routers.system.build_reliability_report", return_value=report),
        patch(
            "app.routers.system.ConvergenceStore.public_summary",
            return_value=convergence,
        ),
    ):
        response = client.get("/v1/system/parsing-reliability")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers
    body = response.json()
    assert body["data"] == {**report, "convergence": convergence}
    assert body["meta"] == {
        "source_id": "parser_reliability",
        "fetched_at": "2026-08-11T12:00:00+00:00",
        "stale": False,
        "count": 12,
    }
