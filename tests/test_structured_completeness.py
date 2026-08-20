from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.fetcher import (
    _dedupe_streamer_decks_parsed,
    _enrich_streamer_deck_codes_with_parsesunix,
)
from app.parser import parse_html
from app.source_contracts import contract_quality_report
from app.sources import SOURCE_BY_ID
from app.structured import build_structured


def test_hsguru_meta_emits_reconciled_completeness_evidence() -> None:
    rows = [
        {
            "Archetype": f"Deck {index}",
            "Winrate↓": "51%",
            "Popularity": "2%",
        }
        for index in range(10)
    ]

    structured = build_structured(
        SOURCE_BY_ID["hsguru_meta_standard_legend"],
        {
            "tables": [{"objects": rows}],
            "text_preview": [],
            "links": [],
        },
    )
    report = contract_quality_report(
        "hsguru_meta_standard_legend",
        structured,
    )

    assert structured["completeness_schema_version"] == 1
    assert structured["row_retrieval"] == {
        "raw_rows": 10,
        "eligible_rows": 10,
        "normalized_rows": 10,
        "explained_drops": 0,
        "unexplained_drops": 0,
        "drop_reasons": {"explained": {}, "unexplained": {}},
        "scope": "hsguru_meta_tables",
    }
    assert report["ok"] is True
    assert report["retrieval_complete"] is True
    assert report["retrieval_completeness_score"] == 1.0


def test_hsguru_streamer_emits_reconciled_completeness_evidence() -> None:
    rows = [
        {
            "Deck": "Fresh deck",
            "Streamer": "Streamer",
            "deck_code": (
                "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63p"
                "BdCeBu6hBom1BoSZB+C+B43cBwAA"
            ),
        }
    ]
    source = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]

    structured = build_structured(
        source,
        {"tables": [{"objects": rows}], "text_preview": [], "links": []},
    )
    report = contract_quality_report(source.id, structured)

    assert structured["completeness_schema_version"] == 1
    assert structured["row_retrieval"] == {
        "raw_rows": 1,
        "eligible_rows": 1,
        "normalized_rows": 1,
        "explained_drops": 0,
        "unexplained_drops": 0,
        "drop_reasons": {"explained": {}, "unexplained": {}},
        "scope": "hsguru_streamer_first_table",
    }
    assert report["ok"] is True
    assert report["low_activity"] is True
    assert report["retrieval_complete"] is True


def test_hsguru_streamer_rejects_non_object_table_rows() -> None:
    source = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]
    structured = build_structured(
        source,
        {"tables": [{"objects": ["broken"]}], "text_preview": [], "links": []},
    )
    report = contract_quality_report(source.id, structured)

    assert structured["row_retrieval"]["unexplained_drops"] == 1
    assert report["retrieval_complete"] is False


def test_hsguru_streamer_deduplication_reconciles_retrieval_evidence() -> None:
    deck_code = (
        "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63p"
        "BdCeBu6hBom1BoSZB+C+B43cBwAA"
    )
    duplicate_rows = [
        {"Deck": "Fresh deck", "Streamer": "Streamer", "deck_code": deck_code},
        {"Deck": "Fresh deck", "Streamer": "Streamer", "deck_code": deck_code},
    ]
    source = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]
    structured = build_structured(
        source,
        {
            "tables": [{"objects": duplicate_rows}],
            "text_preview": [],
            "links": [],
        },
    )
    parsed = {
        "tables": [{"headers": list(duplicate_rows[0]), "objects": duplicate_rows}],
        "structured": structured,
    }

    deduped = _dedupe_streamer_decks_parsed(parsed)
    deduped_structured = deduped["structured"]
    report = contract_quality_report(source.id, deduped_structured)

    assert len(deduped_structured["rows"]) == 1
    assert deduped_structured["row_retrieval"] == {
        "raw_rows": 2,
        "eligible_rows": 2,
        "normalized_rows": 1,
        "explained_drops": 1,
        "unexplained_drops": 0,
        "drop_reasons": {
            "explained": {"duplicate_streamer_deck": 1},
            "unexplained": {},
        },
        "scope": "hsguru_streamer_first_table",
    }
    assert report["ok"], report["warnings"]
    assert report["retrieval_complete"] is True


def test_hsguru_streamer_preserves_detail_url_and_hydrates_missing_code() -> None:
    deck_code = (
        "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63p"
        "BdCeBu6hBom1BoSZB+C+B43cBwAA"
    )
    source = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]
    html = """
    <html><head><title>Streamer decks</title></head><body>
      <table>
        <tr><th>Deck</th><th>Streamer</th></tr>
        <tr><td><a href="/deck/41520944">Fresh deck</a></td><td>Streamer</td></tr>
      </table>
    </body></html>
    """
    parsed = parse_html(source, html)

    with patch(
        "app.fetcher.fetch_direct_with_parsesunix",
        new=AsyncMock(
            return_value=SimpleNamespace(
                transport_validated=True,
                body=f"<html><body><main>{deck_code}</main></body></html>",
            )
        ),
    ) as fetch_detail:
        enriched = asyncio.run(
            _enrich_streamer_deck_codes_with_parsesunix(parsed)
        )

    row = enriched["structured"]["rows"][0]
    assert row["Deck_url"] == "https://www.hsguru.com/deck/41520944"
    assert row["deck_code"] == deck_code
    assert enriched["counts"]["deck_codes"] == 1
    fetch_detail.assert_awaited_once()


def test_hsguru_streamer_reads_deck_code_from_copy_attribute_without_hydration() -> None:
    deck_code = (
        "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63p"
        "BdCeBu6hBom1BoSZB+C+B43cBwAA"
    )
    source = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]
    html = f"""
    <html><body><table>
      <tr><th>Deck</th><th>Streamer</th></tr>
      <tr>
        <td><a href="/deck/41520944">Fresh deck</a>
          <button data-clipboard-text="{deck_code}">Copy</button>
        </td>
        <td>Streamer</td>
      </tr>
    </table></body></html>
    """

    parsed = parse_html(source, html)

    assert parsed["structured"]["rows"][0]["deck_code"] == deck_code


def _matchup_table(
    *,
    empty_non_self: bool = False,
    truncated_non_self: bool = False,
) -> dict:
    archetypes = ["Deck A", "Deck B", "Deck C"]
    rows = []
    for row_index, archetype in enumerate(archetypes):
        values: list[str] = []
        for column_index, _opponent in enumerate(archetypes):
            if row_index == column_index or (
                empty_non_self and row_index == 0 and column_index == 1
            ):
                values.append("")
            else:
                values.append("51%")
        if truncated_non_self and row_index == 0:
            values.pop()
        rows.append(["", archetype, *values])
    return {"headers": ["Rank", "Archetype", *archetypes], "rows": rows}


def test_hsguru_matchups_distinguishes_upstream_empty_from_truncated_cells() -> None:
    source = SOURCE_BY_ID["hsguru_matchups_legend"]
    complete = build_structured(
        source,
        {"tables": [_matchup_table()], "text_preview": [], "links": []},
    )
    explicit_upstream_empty = build_structured(
        source,
        {
            "tables": [_matchup_table(empty_non_self=True)],
            "text_preview": [],
            "links": [],
        },
    )
    truncated = build_structured(
        source,
        {
            "tables": [_matchup_table(truncated_non_self=True)],
            "text_preview": [],
            "links": [],
        },
    )

    complete_report = contract_quality_report(source.id, complete)
    explicit_empty_report = contract_quality_report(
        source.id,
        explicit_upstream_empty,
    )
    truncated_report = contract_quality_report(source.id, truncated)

    assert complete["row_retrieval"]["raw_rows"] == 9
    assert complete["row_retrieval"]["normalized_rows"] == 6
    assert complete["row_retrieval"]["explained_drops"] == 3
    assert complete["row_retrieval"]["unexplained_drops"] == 0
    assert complete_report["retrieval_complete"] is True
    assert complete_report["identity_checks"]["matchups"]["complete"] is True
    assert explicit_upstream_empty["row_retrieval"]["explained_drops"] == 4
    assert explicit_upstream_empty["row_retrieval"]["unexplained_drops"] == 0
    assert explicit_empty_report["retrieval_complete"] is True
    assert truncated["row_retrieval"]["unexplained_drops"] == 1
    assert truncated_report["retrieval_complete"] is False
