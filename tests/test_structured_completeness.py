from __future__ import annotations

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


def _matchup_table(*, missing_non_self: bool = False) -> dict:
    archetypes = ["Deck A", "Deck B", "Deck C"]
    rows = []
    for row_index, archetype in enumerate(archetypes):
        values: list[str] = []
        for column_index, _opponent in enumerate(archetypes):
            if row_index == column_index or (
                missing_non_self and row_index == 0 and column_index == 1
            ):
                values.append("")
            else:
                values.append("51%")
        rows.append(["", archetype, *values])
    return {"headers": ["Rank", "Archetype", *archetypes], "rows": rows}


def test_hsguru_matchups_explains_only_self_matchup_cells() -> None:
    source = SOURCE_BY_ID["hsguru_matchups_legend"]
    complete = build_structured(
        source,
        {"tables": [_matchup_table()], "text_preview": [], "links": []},
    )
    incomplete = build_structured(
        source,
        {
            "tables": [_matchup_table(missing_non_self=True)],
            "text_preview": [],
            "links": [],
        },
    )

    complete_report = contract_quality_report(source.id, complete)
    incomplete_report = contract_quality_report(source.id, incomplete)

    assert complete["row_retrieval"]["raw_rows"] == 9
    assert complete["row_retrieval"]["normalized_rows"] == 6
    assert complete["row_retrieval"]["explained_drops"] == 3
    assert complete["row_retrieval"]["unexplained_drops"] == 0
    assert complete_report["retrieval_complete"] is True
    assert complete_report["identity_checks"]["matchups"]["complete"] is True
    assert incomplete["row_retrieval"]["unexplained_drops"] == 1
    assert incomplete_report["retrieval_complete"] is False
