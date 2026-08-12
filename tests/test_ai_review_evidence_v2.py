from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from app.ai_review_evidence import build_ai_review_evidence_v2, evidence_sha256
from app.sources import SOURCE_BY_ID, Source


def _hero_rows(count: int, *, marker: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        first = 20.0 + (index % 5) * 0.1
        rest = round((100.0 - first) / 7, 2)
        distribution = [f"{first:.2f}%", *([f"{rest:.2f}%"] * 6)]
        distribution.append(f"{100.0 - first - rest * 6:.2f}%")
        rows.append(
            {
                "hero": f"{marker}-hero-{index}",
                "name": f"{marker}-name-{index}",
                "dbfId": 50_000 + index,
                "pick_rate": f"{10 + index / 10:.2f}%",
                "avg_placement": f"{3.5 + index / 100:.2f}",
                "tier": ["S", "A", "B", "C"][index % 4],
                "placement_distribution": distribution,
                "url": f"https://private.example/{marker}/{index}",
                "deck_code": f"{marker}-deck-code-{index}",
                "cookie": f"session={marker}-{index}",
            }
        )
    return rows


def test_evidence_contains_trusted_validation_and_lkg_signals_only() -> None:
    source = SOURCE_BY_ID["hsreplay_battlegrounds_heroes"]
    secret = "RAW-PRIVATE-MARKER"
    parsed = {
        "source_id": source.id,
        "title": "Battlegrounds hero stats",
        "url": f"https://private.example/{secret}",
        "raw_html": f"<html>{secret}</html>",
        "cookie": f"session={secret}",
        "unknown_upstream_key": secret,
        "structured": {
            "type": "bg_heroes",
            "heroes": _hero_rows(40, marker=secret),
            "unknown_upstream_key": secret,
        },
    }
    lkg = {
        "data": {
            "structured": {
                "type": "bg_heroes",
                "heroes": _hero_rows(50, marker=f"LKG-{secret}"),
            },
            "url": f"https://lkg.example/{secret}",
        }
    }
    regression = {
        "detected": True,
        "reason_code": "row_count_drop",
        "extra": {
            "rows_before": 50,
            "rows_after": 40,
            "filled_before": 50,
            "filled_after": 40,
            "drop_ratio": 0.35,
            "prev_url": f"https://private.example/{secret}",
            "unknown_upstream_key": secret,
        },
    }
    quality = {
        "quality_score": 0.99,
        "semantic_score": 0.98,
        "rows_total": 40,
        "blocked_marker": False,
        "unknown_upstream_key": secret,
        "semantic_metrics": {"private_name": secret},
    }
    post_patch = {
        "data_phase": "post_patch_early",
        "provisional": True,
        "accepted_rows": 40,
        "baseline_rows": 50,
        "coverage_ratio": 0.8,
        "minimum_sample": 10,
        "patch_window": {"private": secret},
    }
    policy = SimpleNamespace(
        minimum_rows=20,
        minimum_classes=1,
        minimum_tier_fill_rate=0.8,
        minimum_sample=10,
    )

    with patch("app.ai_review_evidence.policy_for", return_value=policy):
        evidence = build_ai_review_evidence_v2(
            source,
            parsed,
            backend="hsreplay_cache",
            stage="candidate_validation",
            deterministic_ok=False,
            deterministic_extra={
                "reason_code": "regression",
                "raw_html": secret,
                "unknown_upstream_key": secret,
            },
            quality=quality,
            regression=regression,
            lkg=lkg,
            post_patch=post_patch,
        )

    assert evidence["schema_version"] == 2
    assert evidence["source"] == {
        "id": source.id,
        "registry_known": True,
        "registry_match": True,
    }
    assert evidence["identity"]["parsed_source_id_matches"] is True
    assert evidence["identity"]["expected_structured_type"] == "bg_heroes"
    assert evidence["identity"]["actual_structured_type"] == "bg_heroes"
    assert evidence["identity"]["structured_type_matches"] is True
    assert evidence["trusted_contract"]["minimum_rows"] == 30
    assert evidence["contract_validation"]["rows_total"] == 40
    assert evidence["contract_validation"]["field_fill_rates"]["hero"]["rate"] == 1.0
    assert evidence["semantic_validation"]["numeric_metrics"]["unique_names"] == 40
    assert evidence["deterministic_validation"]["pipeline_numeric_metrics"] == {
        "quality_score": 0.99,
        "rows_total": 40,
        "semantic_score": 0.98,
    }
    assert evidence["regression"]["reason_code"] == "row_count_drop"
    assert evidence["regression"]["row_delta"] == -10
    assert evidence["lkg_comparison"]["row_delta"] == -10
    assert evidence["lkg_comparison"]["row_retention_ratio"] == 0.8
    assert evidence["post_patch"]["policy_active"] is True
    assert evidence["post_patch"]["provisional"] is True
    assert evidence["post_patch"]["low_sample_expected"] is True
    assert evidence["evidence_hash"] == evidence_sha256(evidence)
    assert len(evidence["evidence_hash"]) == 64

    encoded = json.dumps(evidence, ensure_ascii=False)
    assert secret not in encoded
    assert "private.example" not in encoded
    assert "unknown_upstream_key" not in encoded
    assert "raw_html" not in encoded
    assert "patch_window" not in encoded


def test_unknown_values_are_collapsed_instead_of_copied() -> None:
    secret = "sk-private-token-marker-123456789"
    source = Source(
        id="unregistered_source",
        url=f"https://private.example/{secret}",
        site="private-site",
        category="private-category",
    )
    parsed = {
        "source_id": f"https://private.example/{secret}",
        "title": f"Just a moment {secret}",
        "structured": {
            "type": f"https://private.example/{secret}",
            "name": secret,
            "deck_code": secret,
            "cookie": secret,
        },
    }

    evidence = build_ai_review_evidence_v2(
        source,
        parsed,
        backend=secret,
        stage=secret,
        deterministic_ok=False,
        deterministic_extra={"reason_code": secret, "private": secret},
        quality={"rows_total": 10**10_000, "private": secret},
        regression={"detected": True, "reason_code": secret, "private": secret},
        post_patch={"data_phase": secret, "private": secret},
    )

    assert evidence["source"]["id"] == "unregistered"
    assert evidence["fetch"]["backend"] == "unknown"
    assert evidence["stage"] == "unknown"
    assert evidence["identity"]["parsed_source_id_matches"] is False
    assert evidence["identity"]["actual_structured_type"] == "unknown"
    assert evidence["regression"]["reason_code"] == "unknown"
    assert evidence["post_patch"]["data_phase"] == "unknown"
    assert evidence["identity"]["challenge_detected"] is True
    assert evidence["deterministic_validation"]["pipeline_numeric_metrics"] == {}
    encoded = json.dumps(evidence, ensure_ascii=False)
    assert secret not in encoded
    assert "private.example" not in encoded


def test_fetch_stage_does_not_invent_schema_or_semantic_failure() -> None:
    source = SOURCE_BY_ID["hsreplay_battlegrounds_heroes"]

    evidence = build_ai_review_evidence_v2(
        source,
        {},
        backend="scrape_do",
        stage="fetch",
        deterministic_ok=False,
        deterministic_extra={"reason_code": "http_5xx"},
    )

    assert evidence["contract_validation"]["evaluated"] is False
    assert evidence["contract_validation"]["passed"] is None
    assert evidence["semantic_validation"]["evaluated"] is False
    assert evidence["semantic_validation"]["issue_codes"] == []
    assert evidence["semantic_validation"]["error_count"] == 0
    assert evidence["lkg_comparison"]["evaluated"] is False
    assert evidence["deterministic_validation"]["issue_codes"] == [
        "deterministic.http_5xx"
    ]


def test_hash_is_canonical_and_tracks_only_safe_signal_changes() -> None:
    source = SOURCE_BY_ID["hsreplay_battlegrounds_heroes"]
    parsed = {
        "source_id": source.id,
        "structured": {
            "heroes": _hero_rows(40, marker="safe-hash-test"),
            "type": "bg_heroes",
        },
    }
    reordered = {
        "structured": {
            "type": "bg_heroes",
            "heroes": list(parsed["structured"]["heroes"]),
        },
        "source_id": source.id,
    }

    first = build_ai_review_evidence_v2(
        source,
        parsed,
        backend="direct",
        stage="candidate_validation",
        deterministic_ok=True,
    )
    second = build_ai_review_evidence_v2(
        source,
        reordered,
        backend="direct",
        stage="candidate_validation",
        deterministic_ok=True,
    )
    failed = build_ai_review_evidence_v2(
        source,
        parsed,
        backend="direct",
        stage="candidate_validation",
        deterministic_ok=False,
    )

    assert first == second
    assert first["evidence_hash"] == second["evidence_hash"]
    assert first["evidence_hash"] != failed["evidence_hash"]
    assert evidence_sha256(first) == first["evidence_hash"]


def test_regression_collection_details_are_contract_allowlisted() -> None:
    source = SOURCE_BY_ID["firestone_standard"]
    regression = {
        "detected": True,
        "reason_code": "collection_drop",
        "extra": {
            "collections": {
                "decks": {"before": 20, "after": 8, "threshold": 10},
                "archetypes": {"before": 20, "after": 18, "threshold": 10},
                "private_collection": {
                    "before": 1,
                    "after": 1,
                    "threshold": 1,
                },
            }
        },
    }

    evidence = build_ai_review_evidence_v2(
        source,
        {"structured": {"type": "firestone_standard"}},
        backend="firestone_api",
        stage="regression_check",
        deterministic_ok=False,
        regression=regression,
    )

    collections = evidence["regression"]["collections"]
    assert [item["collection"] for item in collections] == ["archetypes", "decks"]
    assert collections[1]["delta"] == -12
    assert collections[1]["threshold_met"] is False
    assert "private_collection" not in json.dumps(evidence)
