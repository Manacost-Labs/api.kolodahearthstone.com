"""Expose collection evidence without inventing an upstream population oracle."""

from __future__ import annotations

from typing import Any


def completeness_view(document: dict[str, Any]) -> dict[str, Any]:
    data = document.get("data") or {}
    structured = data.get("structured") or data.get("hsreplay_extracted") or {}
    if not isinstance(structured, dict):
        structured = {}
    criteria = structured.get("criteria") or {}
    scope = {
        key: criteria[key]
        for key in ("format", "formats", "rank", "period", "min_games", "target_source")
        if key in criteria
    }
    result: dict[str, Any] = {
        "state": "unknown",
        "population_state": "unknown",
        "expected": None,
        "received": None,
        "verified_empty": None,
        "unverified": None,
        "patch": structured.get("patch") or criteria.get("patch"),
        "filters": scope,
        "observed_at": document.get("fetched_at"),
        "upstream_timestamp": None,
        "retrieval_state": "unknown",
    }
    evidence = structured.get("row_retrieval") or {}
    if isinstance(evidence, dict):
        result["received"] = evidence.get("normalized_rows")
        counts = [
            evidence.get(k)
            for k in (
                "raw_rows",
                "eligible_rows",
                "normalized_rows",
                "explained_drops",
                "unexplained_drops",
            )
        ]
        if all(type(value) is int and value >= 0 for value in counts):
            raw, eligible, normalized, explained, unexplained = counts
            reconciled = (
                normalized <= eligible <= raw
                and raw - normalized == explained + unexplained
            )
            result["retrieval_state"] = (
                "complete" if reconciled and unexplained == 0 else "incomplete"
            )
    freshness = structured.get("upstream_freshness") or {}
    if isinstance(freshness, dict):
        result["upstream_timestamp"] = freshness.get("body_as_of") or freshness.get(
            "upstream_timestamp"
        )
    result["upstream_timestamp"] = result["upstream_timestamp"] or structured.get(
        "as_of"
    )
    if structured.get("type") == "hsguru_archetype_analysis":
        rows = [r for r in structured.get("archetypes") or [] if isinstance(r, dict)]
        targets = structured.get("expected_targets") or []
        identities = {(r.get("format"), r.get("archetype")) for r in rows}
        expected_ids = {
            (r.get("format"), r.get("archetype"))
            for r in targets
            if isinstance(r, dict)
        }
        complete = sum(
            r.get("matchups_state") in {"complete", "sparse_valid"}
            and r.get("card_stats_state") in {"complete", "sparse_valid"}
            for r in rows
        )
        expected = structured.get("expected_targets_total")
        # Certify only reconciliation against this selected target set.
        reconciled = (
            bool(expected_ids)
            and identities == expected_ids
            and len(rows) == len(identities)
            and len(targets) == len(expected_ids)
            and type(expected) is int
            and len(expected_ids) == expected
        )
        result.update(
            state="complete"
            if reconciled and complete == expected
            else "incomplete"
            if expected_ids
            else "unknown",
            expected=expected,
            received=len(rows),
            verified_empty=sum(
                r.get("card_stats_state") == "sparse_valid" and not r.get("card_stats")
                for r in rows
            ),
            unverified=max(0, len(expected_ids - identities) + len(rows) - complete)
            if expected_ids
            else None,
            evidence_scope="selected_targets",
            verified_empty_scope="card_stats",
            retrieval_state="complete"
            if reconciled and complete == expected
            else "incomplete",
        )
    return result
