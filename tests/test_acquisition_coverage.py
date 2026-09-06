import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.acquisition_coverage import (
    ListingEvidence,
    coverage_report,
    registry_coverage,
    source_view,
)
from app.sources import SOURCES, Source


def view(**kwargs):
    return source_view(
        Source("fixture", "https://example.com/?rank=legend#tab", "fixture", "meta"),
        snapshot_id="capture-1",
        **kwargs,
    )


def test_registry_unknown_is_not_zero_or_complete():
    rows = registry_coverage(snapshot_id="capture-1")
    assert len(rows) == len(SOURCES)
    assert {row["source_id"] for row in rows} == {source.id for source in SOURCES}
    assert all(row["status"] == "unknown" and row["found"] is None for row in rows)
    assert all(
        row["expected_count"] is None and row["listing_percent"] is None for row in rows
    )


def test_scope_preserves_filters_fragments_patch_and_snapshot():
    original = view(patch_id="patch-a")
    assert original.scope_id != view(patch_id="patch-b").scope_id
    for url in [
        "https://example.com/?rank=diamond#tab",
        "https://example.com/?rank=legend#other",
    ]:
        other = source_view(
            Source("fixture", url, "fixture", "meta"),
            snapshot_id="capture-1",
            patch_id="patch-a",
        )
        assert original.scope_id != other.scope_id
    assert (
        original.scope_id
        != source_view(
            original.source, snapshot_id="capture-2", patch_id="patch-a"
        ).scope_id
    )
    assert original.scope_id == view(patch_id="patch-a").scope_id


def test_counts_distinguish_failures_absence_and_unrequested():
    selected = view()
    listing = ListingEvidence(
        selected.scope_id,
        ("1", "2", "3", "4", "5"),
        exhausted=True,
        expected_count=5,
        view_confirmed=True,
    )
    report = coverage_report(
        selected,
        listing=listing,
        detail_states={"1": "succeeded", "2": "absent", "3": "failed", "4": "retry"},
    )
    assert report["status"] == "partial"
    assert report["details"] == {
        "succeeded": 1,
        "absent": 1,
        "failed": 1,
        "retry": 1,
        "running": 0,
        "pending": 0,
        "not_requested": 1,
    }
    assert report["unresolved_details"] == 3
    assert report["listing_percent"] == 100.0
    assert report["patch_id"] is None


def test_count_alone_does_not_prove_end_or_selected_view():
    selected = view()
    for exhausted, confirmed in [(False, True), (True, False)]:
        report = coverage_report(
            selected,
            listing=ListingEvidence(
                selected.scope_id,
                ("1",),
                exhausted=exhausted,
                expected_count=1,
                view_confirmed=confirmed,
            ),
            detail_states={"1": "succeeded"},
        )
        assert report["status"] == "partial"


def test_explicit_end_can_complete_view_without_inventing_denominator():
    selected = view()
    report = coverage_report(
        selected,
        listing=ListingEvidence(
            selected.scope_id, ("1",), exhausted=True, view_confirmed=True
        ),
        detail_states={"1": "succeeded"},
    )
    assert report["status"] == "complete_for_view"
    assert report["listing_percent"] is None
    assert report["expected_count"] is None


@pytest.mark.parametrize(
    "listing",
    [
        lambda s: ListingEvidence("foreign", ("1",)),
        lambda s: ListingEvidence(s, ("1", "1")),
        lambda s: ListingEvidence(s, ("1",), expected_count=True),
        lambda s: ListingEvidence(s, ("1",), expected_count=0),
    ],
)
def test_inconsistent_evidence_is_rejected(listing):
    with pytest.raises(ValueError):
        coverage_report(view(), listing=listing(view().scope_id))


def test_foreign_detail_id_and_unknown_status_rejected():
    selected = view()
    listing = ListingEvidence(selected.scope_id, ("1",))
    for states in [{"foreign": "succeeded"}, {"1": "green"}]:
        with pytest.raises(ValueError):
            coverage_report(selected, listing=listing, detail_states=states)


def test_readonly_cli_lists_registry_and_checks_scope(tmp_path):
    root = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable,
        str(root / "scripts/acquisition_coverage_report.py"),
        "--snapshot-id",
        "capture-1",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)) == len(SOURCES)
    evidence = tmp_path / "foreign.json"
    evidence.write_text(
        json.dumps({"scope_id": "foreign", "entity_ids": []}), encoding="utf-8"
    )
    result = subprocess.run(
        cmd + ["--source-id", SOURCES[0].id, "--evidence", str(evidence)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not result.stdout
