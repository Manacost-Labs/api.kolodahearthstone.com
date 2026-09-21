from __future__ import annotations

from copy import deepcopy

import pytest

from app.ai_review import AIFailureDiagnosis, _parse_typesafe_response
from app.ai_review_evidence import _post_patch_signals
from app.post_patch_policy import PostPatchPolicy
from app.sources import SOURCE_BY_ID
from app.typesafe_diagnosis import (
    DOMAIN_CRITERIA,
    diagnosis_request,
    diagnosis_response,
)


@pytest.fixture
def sparse_evidence():
    return {
        "stage": "regression_rejection",
        "source": {"registry_known": True, "registry_match": True},
        "identity": {
            "structured_type_matches": True,
            "parsed_source_id_matches": True,
            "challenge_detected": False,
            "login_wall_detected": False,
        },
        "post_patch": {"policy_active": True, "low_sample_expected": True},
        "contract_validation": {"passed": True},
        "semantic_validation": {"passed": True},
        "regression": {
            "detected": True,
            "reason_code": "row_count_drop",
            "rows_before": 1000,
            "rows_after": 30,
            "filled_before": 1000,
            "filled_after": 30,
        },
    }


def sparse_answer(confidence=0.99):
    return {
        "answers": {
            "failure_domain": {
                "type": "choice",
                "choice": "expected_post_patch_sparse",
                "confidence": confidence,
                "probabilities": {
                    key: float(key == "expected_post_patch_sparse")
                    for key in DOMAIN_CRITERIA
                },
            }
        }
    }


def test_expected_volume_drop_is_not_a_broken_parser_diagnosis(sparse_evidence):
    original = deepcopy(sparse_evidence)
    request = diagnosis_request(sparse_evidence, model="typesafe/jev-1.13")
    assert request["state"]["checks"]["expected_post_patch_sparse_supported"] is True
    result, metadata, error = _parse_typesafe_response(
        sparse_answer(), model="typesafe/jev-1.13", evidence=sparse_evidence
    )
    assert error is None
    assert isinstance(result, AIFailureDiagnosis)
    assert result.classification == "healthy"
    assert result.failure_domain == "none"
    assert result.evidence_codes == ["post_patch_sparse_expected"]
    assert result.recommended_action == "none"
    assert metadata["diagnosis_confidence"] == 0.99
    assert sparse_evidence == original


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("post_patch", "policy_active", False),
        ("post_patch", "low_sample_expected", False),
        ("source", "registry_match", False),
        ("identity", "structured_type_matches", False),
        ("identity", "challenge_detected", True),
        ("identity", "login_wall_detected", True),
        ("semantic_validation", "passed", False),
        ("contract_validation", "passed", False),
        ("regression", "reason_code", "filled_metric_drop"),
        ("regression", "reason_code", "policy_changed"),
        ("regression", "reason_code", "unknown"),
        ("regression", "rows_after", 0),
        ("regression", "rows_after", None),
        ("regression", "rows_after", True),
        ("regression", "rows_after", 1200),
        ("regression", "filled_after", 10),
    ],
)
def test_model_cannot_label_other_faults_expected_sparse(
    sparse_evidence, section, key, value
):
    sparse_evidence[section][key] = value
    request = diagnosis_request(sparse_evidence, model="typesafe/jev-1.13")
    assert request["state"]["checks"]["expected_post_patch_sparse_supported"] is False
    result, _ = diagnosis_response(
        sparse_answer(), min_confidence=0.95, evidence=sparse_evidence
    )
    assert result["classification"] == "inconclusive"
    assert result["failure_domain"] == "unknown"
    assert result["recommended_action"] == "none"


def test_low_confidence_and_prevalidation_do_not_explain_away_rejection(
    sparse_evidence,
):
    result, _ = diagnosis_response(
        sparse_answer(0.5), min_confidence=0.95, evidence=sparse_evidence
    )
    assert result["classification"] == "inconclusive"
    sparse_evidence["stage"] = "contract_validation"
    result, _ = diagnosis_response(
        sparse_answer(), min_confidence=0.95, evidence=sparse_evidence
    )
    assert result["classification"] == "inconclusive"


def test_active_patch_context_exists_before_provisional_publication(monkeypatch):
    source = SOURCE_BY_ID["hsguru_meta_standard_legend"]
    monkeypatch.setattr(
        "app.ai_review_evidence.policy_for", lambda _: PostPatchPolicy(source.id)
    )
    signals = _post_patch_signals(source, {}, {}, regression_bypass=False)
    assert signals["policy_active"] is True
    assert signals["data_phase"] == "post_patch_early"
    assert signals["low_sample_expected"] is True
    assert signals["provisional"] is False


def test_empty_publication_metadata_does_not_hide_candidate_phase(monkeypatch):
    source = SOURCE_BY_ID["hsguru_meta_standard_legend"]
    monkeypatch.setattr(
        "app.ai_review_evidence.policy_for", lambda _: PostPatchPolicy(source.id)
    )
    signals = _post_patch_signals(
        source,
        {"data_phase": "stable", "accepted_rows": 30},
        {},
        regression_bypass=False,
    )
    assert signals["data_phase"] == "stable"
    assert signals["accepted_rows"] == 30
    assert signals["low_sample_expected"] is False


def test_expired_patch_cannot_be_reactivated_by_candidate_metadata(monkeypatch):
    source = SOURCE_BY_ID["hsguru_meta_standard_legend"]
    monkeypatch.setattr("app.ai_review_evidence.policy_for", lambda _: None)
    signals = _post_patch_signals(
        source,
        {"data_phase": "post_patch_early", "provisional": True},
        {},
        regression_bypass=False,
    )
    assert signals["policy_active"] is False
    assert signals["low_sample_expected"] is False
