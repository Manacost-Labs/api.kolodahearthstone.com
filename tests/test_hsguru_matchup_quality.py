from __future__ import annotations

from html import escape
from unittest.mock import patch

import pytest

from app.parser import parse_html
from app.post_patch_policy import capture_publication_policy
from app.scrapers.quality import validate_parsed_data
from app.source_validators import validate_structured
from app.sources import SOURCE_BY_ID
from app.structured import parse_hsguru_matchups


def _rows(value: object = "50%") -> list[dict]:
    return [
        {"archetype": f"Deck {index}", "vs": "Other Deck", "winrate": value}
        for index in range(3)
    ]


@pytest.fixture(params=["stable", "early"])
def mode(request):
    with (
        patch(
            "app.parser_control.publication_policy_context",
            return_value={
                "effectiveMode": request.param,
                "token": request.param,
                "revision": 1,
                "capturedAt": "2026-09-04T12:00:00Z",
                "window": None,
            },
        ),
        capture_publication_policy("hsguru_matchups_legend"),
    ):
        yield request.param


@pytest.mark.parametrize(
    "value",
    [
        "999%",
        -1,
        "-1%",
        True,
        False,
        float("nan"),
        float("inf"),
        "NaN%",
        "oops",
        "50%oops",
        None,
    ],
)
def test_every_matchup_metric_must_be_valid(mode, value: object) -> None:
    rows = _rows() + [
        {"archetype": "Fourth Deck", "vs": "Other Deck", "winrate": value}
    ]
    report = validate_structured(
        "hsguru_matchups_legend", {"type": "matchups", "matchups": rows}
    )
    assert not report.ok
    assert "hsguru_matchups.invalid_winrate" in {issue.code for issue in report.issues}


@pytest.mark.parametrize("value", [0, 100, "0%", "100%", "50,5%", "50.5", " 50.5 % "])
def test_valid_percent_boundaries_are_not_missing(mode, value: object) -> None:
    report = validate_structured(
        "hsguru_matchups_legend", {"type": "matchups", "matchups": _rows(value)}
    )
    assert report.ok, report.reason


@pytest.mark.parametrize(
    "row",
    [
        {"archetype": "Deck 0", "vs": "Other Deck", "winrate": "51%"},
        {"archetype": "Fourth Deck", "vs": "Fourth Deck", "winrate": "51%"},
        {"archetype": "", "vs": "Other Deck", "winrate": "51%"},
        {"archetype": True, "vs": "Other Deck", "winrate": "51%"},
        "not an object",
    ],
)
def test_invalid_identity_or_row_is_not_silently_dropped(mode, row: object) -> None:
    report = validate_structured(
        "hsguru_matchups_legend", {"type": "matchups", "matchups": _rows() + [row]}
    )
    assert not report.ok


@pytest.mark.parametrize(
    "value,accepted",
    [("999%", False), ("0%", True), ("100%", True), ("invalid", False)],
)
def test_html_to_publication_gate_checks_every_metric(
    mode, value: str, accepted: bool
) -> None:
    source = SOURCE_BY_ID["hsguru_matchups_legend"]
    html = "<html><title>HSGuru matchups</title><body><table><tr><th>Class</th><th>Archetype</th><th>Other Deck</th></tr>"
    html += "".join(
        f"<tr><td>Mage</td><td>Deck {i}</td><td>{escape(value)}</td></tr>"
        for i in range(3)
    )
    parsed = parse_html(source, html + "</table></body></html>")
    assert parsed["structured"]["completeness_schema_version"] >= 1
    assert len(parsed["structured"]["matchups"]) == 3
    ok, reason = validate_parsed_data(source, parsed, emit_telemetry=False)
    assert ok is accepted, reason


def test_parser_keeps_invalid_cell_evidence_instead_of_appending_percent() -> None:
    table = {
        "headers": ["Class", "Archetype", "Other Deck"],
        "rows": [["Mage", "Deck", "invalid"]],
    }
    assert parse_hsguru_matchups([table])[0]["winrate"] == "invalid"


@pytest.mark.parametrize("last_value", ["999%", "-1%", "invalid"])
def test_html_gate_rejects_one_bad_cell_among_valid_rows(mode, last_value: str) -> None:
    source = SOURCE_BY_ID["hsguru_matchups_legend"]
    cells = ["0%", "100%", "50,5%", last_value]
    html = "<html><title>HSGuru matchups</title><table><tr><th>Class</th><th>Archetype</th><th>Other Deck</th></tr>"
    html += "".join(
        f"<tr><td>Mage</td><td>Deck {i}</td><td>{escape(value)}</td></tr>"
        for i, value in enumerate(cells)
    )
    parsed = parse_html(source, html + "</table></html>")
    assert len(parsed["structured"]["matchups"]) == 4
    ok, reason = validate_parsed_data(source, parsed, emit_telemetry=False)
    assert not ok
    assert "finite winrate" in reason


def test_upstream_empty_and_self_cells_keep_explained_omissions(mode) -> None:
    source = SOURCE_BY_ID["hsguru_matchups_legend"]
    html = "<html><title>HSGuru matchups</title><table><tr><th>Class</th><th>Archetype</th><th>Other Deck</th><th>Deck 0</th></tr>"
    html += "".join(
        f"<tr><td>Mage</td><td>Deck {i}</td><td>50%</td><td></td></tr>"
        for i in range(3)
    )
    parsed = parse_html(source, html + "</table></html>")
    ok, reason = validate_parsed_data(source, parsed, emit_telemetry=False)
    assert ok, reason
