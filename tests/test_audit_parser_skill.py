from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = (
    Path(__file__).parents[1]
    / ".agents/skills/audit-parser-system/scripts/audit_reliability.py"
)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audit_reliability", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_distinguishes_full_fresh_from_availability() -> None:
    audit = _module().build_audit(
        {
            "data": {
                "generated_at": "2026-08-20T10:00:00Z",
                "windows": [
                    {
                        "window": "24h",
                        "measurement_status": "collecting",
                        "coverage_ratio": 0.5,
                        "eligible_attempts": 100,
                        "counts": {
                            "fresh_published": 80,
                            "provisional": 10,
                            "lkg_served": 9,
                            "failed": 1,
                            "timed_out": 0,
                            "skipped": 2,
                        },
                        "full_fresh_rate_pct": 80.0,
                        "accepted_fresh_rate_pct": 90.0,
                        "data_available_rate_pct": 99.0,
                        "failure_reasons": {"unknown": 1},
                        "verified_completeness": {
                            "source_catalog_coverage_pct": 4.0,
                            "instrumented_sources": 4,
                            "observed_instrumented_sources": 1,
                            "instrumented_source_observation_coverage_pct": 25.0,
                            "sources_without_observations": 3,
                            "tracked_attempts": 20,
                            "coverage_of_all_parser_attempts_pct": 20.0,
                        },
                        "scheduled_reliability": {
                            "schedule_coverage_ratio": 0.2,
                        },
                        "parsesunix_rollout": {"observed_attempts": 0},
                    }
                ],
            }
        }
    )

    window = audit["windows"][0]
    assert window["fresh_published"] == 80
    assert window["bad_attempts"] == 20
    assert window["allowed_bad_attempts"] == 1.0
    assert {finding["code"] for finding in audit["findings"]} >= {
        "freshness_below_target",
        "measurement_incomplete",
        "provisional_candidates",
        "lkg_dependency",
        "unknown_failures",
        "completeness_catalog_gap",
        "completeness_observation_gap",
        "completeness_attempt_coverage_gap",
        "schedule_ledger_gap",
        "parsesunix_unobserved",
    }


def test_audit_accepts_unwrapped_report_and_exact_target() -> None:
    audit = _module().build_audit(
        {
            "windows": [
                {
                    "window": "30d",
                    "measurement_status": "observed",
                    "coverage_ratio": 1.0,
                    "eligible_attempts": 100,
                    "counts": {"fresh_published": 99},
                    "full_fresh_rate_pct": 99.0,
                    "verified_completeness": {
                        "source_catalog_coverage_pct": 100.0,
                        "instrumented_sources": 1,
                        "observed_instrumented_sources": 1,
                        "instrumented_source_observation_coverage_pct": 100.0,
                        "sources_without_observations": 0,
                        "tracked_attempts": 100,
                        "coverage_of_all_parser_attempts_pct": 100.0,
                    },
                    "scheduled_reliability": {"schedule_coverage_ratio": 1.0},
                    "parsesunix_rollout": {"observed_attempts": 1},
                }
            ]
        }
    )

    assert audit["windows"][0]["bad_attempts"] == 1
    assert not audit["findings"]


def test_audit_does_not_confuse_catalog_coverage_with_observed_evidence() -> None:
    audit = _module().build_audit(
        {
            "windows": [
                {
                    "window": "24h",
                    "measurement_status": "observed",
                    "coverage_ratio": 1.0,
                    "eligible_attempts": 534,
                    "counts": {"fresh_published": 534},
                    "full_fresh_rate_pct": 100.0,
                    "verified_completeness": {
                        "source_catalog_coverage_pct": 100.0,
                        "instrumented_sources": 98,
                        "observed_instrumented_sources": 4,
                        "instrumented_source_observation_coverage_pct": 4.08,
                        "sources_without_observations": 94,
                        "tracked_attempts": 10,
                        "coverage_of_all_parser_attempts_pct": 1.87,
                    },
                    "scheduled_reliability": {"schedule_coverage_ratio": 1.0},
                    "parsesunix_rollout": {"observed_attempts": 1},
                }
            ]
        }
    )

    findings = {finding["code"]: finding for finding in audit["findings"]}
    assert "completeness_catalog_gap" not in findings
    assert "completeness_observation_gap" in findings
    assert "4/98" in findings["completeness_observation_gap"]["message"]
    assert "completeness_attempt_coverage_gap" in findings
    assert "10/534" in findings["completeness_attempt_coverage_gap"]["message"]

    completeness = audit["windows"][0]["verified_completeness"]
    assert completeness["source_catalog_coverage_pct"] == 100.0
    assert completeness["instrumented_source_observation_coverage_pct"] == 4.08
    assert completeness["coverage_of_all_parser_attempts_pct"] == 1.87
