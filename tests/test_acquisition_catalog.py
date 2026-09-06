from app.acquisition_catalog import acquisition_catalog
from app.sources import SOURCES, Source


def test_every_registered_source_has_an_honest_capability_entry():
    rows = acquisition_catalog()
    assert len(rows) == len(SOURCES)
    assert {row["source_id"] for row in rows} == {source.id for source in SOURCES}
    assert all(row["full_traversal_verified"] is False for row in rows)
    for source, row in zip(SOURCES, rows, strict=True):
        assert row["kind"] == source.kind
        assert row["site"] == source.site
        if source.kind == "pipeline":
            assert row["acquisition_mode"] == "dedicated_pipeline"
            assert not row["history_collector"]


def test_new_sources_are_not_silently_certified():
    row = acquisition_catalog(
        (Source("new", "https://example.com/#tab", "unknown", "new"),)
    )[0]
    assert row["acquisition_mode"] == "source_specific"
    assert row["fragment_requires_adapter"]
    assert row["full_traversal_verified"] is False


def test_history_and_listing_fixes_are_not_confused_with_full_traversal():
    rows = {row["source_id"]: row for row in acquisition_catalog()}
    assert rows["hearthstone_decks"]["history_collector"] == "collect_wordpress_history"
    assert rows["hearthstone_decks"]["history_activation"] == "explicit_candidate_only"
    assert rows["hsreplay_battlegrounds_comps"][
        "listing_preserved_beyond_detail_budget"
    ]
