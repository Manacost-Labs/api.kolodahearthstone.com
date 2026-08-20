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
