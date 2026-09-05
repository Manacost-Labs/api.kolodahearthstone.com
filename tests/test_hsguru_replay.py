"""Saved synthetic HTML replay through the real parser and publication gate."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.parser import parse_html
from app.post_patch_policy import capture_publication_policy
from app.scrapers.quality import validate_parsed_data
from app.source_validators import validate_structured
from app.sources import SOURCE_BY_ID

ROOT = Path(__file__).parent / "fixtures" / "hsguru_replay"
CORPUS = json.loads((ROOT / "corpus.json").read_text())


def test_corpus_has_explicit_unique_cases_and_local_fixtures():
    assert CORPUS["schema_version"] == 1
    cases = CORPUS["cases"]
    assert cases
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        path = (ROOT / case["fixture"]).resolve()
        assert path.is_relative_to(ROOT.resolve()) and path.is_file()
        assert isinstance(case["accepted"], bool)


@pytest.mark.parametrize("mode", ["stable", "early"])
@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["id"])
def test_saved_html_matches_approved_decision(case, mode):
    source = SOURCE_BY_ID["hsguru_matchups_legend"]
    with (
        patch(
            "app.parser_control.publication_policy_context",
            return_value={
                "effectiveMode": mode,
                "token": mode,
                "revision": 1,
                "capturedAt": "2026-01-03T00:00:00Z",
                "window": None,
            },
        ),
        capture_publication_policy(source.id),
    ):
        parsed = parse_html(source, (ROOT / case["fixture"]).read_text())
        structured = parsed["structured"]
        ok, reason = validate_parsed_data(source, parsed, emit_telemetry=False)
        report = validate_structured(source.id, structured)
    assert len(structured["matchups"]) == case["records"]
    assert ok is case["accepted"], reason
    assert report.ok is case["accepted"], report.reason
    assert {issue.code for issue in report.issues} == set(case["issue_codes"])
