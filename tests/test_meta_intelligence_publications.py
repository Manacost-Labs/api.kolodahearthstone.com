from copy import deepcopy
from datetime import UTC, datetime, timedelta

from app.meta_intelligence.publications import catalog_publication, parts
from app.meta_intelligence.snapshots import ingest
from app.meta_intelligence.store import Store
from meta_lab_fixtures import dataset, publish


def test_matrix_slices_keep_scope_dates_and_reject_cached_slice(tmp_path):
    at = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    payload = dataset(at)
    rows = payload["data"]["structured"]["strategies"]
    # Source meta validator expects at least five rows.
    rows = [dict(deepcopy(rows[0]), archetype=f"Archetype {i}") for i in range(5)]
    base = {"format": "standard", "rank": "legend", "period": "patch_36.6.0",
            "coin": "any_player", "fetched_at": at, "rows": rows}
    payload["data"]["structured"] = {"type": "hsguru_meta_matrix", "slices": [
        base, {**base, "rank": "all", "quality": {"serving_cached_slice": True}},
    ]}
    store = Store(tmp_path / "lab.sqlite3")
    result = ingest(store, "hsguru_meta_matrix", payload, {"state": "ok", "fetched_at": at})
    assert len(result["snapshots"]) == 1
    assert "cached_evidence" in result["rejected"][0]["reason"]
    snap = store.snapshots()[0]
    assert (snap["rank"], snap["patch"], snap["fetched_at"]) == ("legend", "patch_36.6.0", at)
    ingest(store, "hsguru_meta_matrix", payload, {"state": "ok", "fetched_at": at})
    assert len(store.snapshots()) == 1


def test_analysis_projection_uses_component_dates_and_separate_archetypes():
    payload = dataset(details=True)
    structured = payload["data"]["structured"]
    structured.update(type="hsguru_archetype_analysis", schema_version=2)
    older = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    structured["archetypes"][0]["matchups_updated_at"] = older
    result = parts(payload)
    assert result[0][1]["fetched_at"] == older
    assert len({suffix for suffix, _, _ in result}) == len(result)
    assert result[0][1]["data"]["structured"]["archetypes"][0]["card_stats_updated_at"] != older


def test_zero_observations_allow_missing_impact_without_inventing_value(tmp_path):
    payload = dataset(details=True)
    card = payload["data"]["structured"]["archetypes"][0]["card_stats"][0]
    card.update(kept_count=0, kept_impact=None)
    store = Store(tmp_path / "lab.sqlite3")
    assert publish(store, payload)["snapshots"]
    assert store.snapshots()[0]["payload"]["archetypes"][0]["cards"][0]["kept_impact"] is None


def test_catalog_with_list_data_is_skipped_without_crashing(tmp_path):
    result = ingest(Store(tmp_path / "lab.sqlite3"), "hsguru_deck_catalog", {"data": []}, {})
    assert result["rejected"][0]["reason"] == "unsupported_dataset"


def test_atomic_catalog_keeps_build_dates_and_rejects_partial_publication(tmp_path):
    source = "hsguru_deck_catalog_standard_legend"
    detail = dataset(details=True)
    deck = detail["data"]["structured"]["archetypes"][0]["decks"][0]
    deck.update(archetype="Test Mage", sample_rank="legend", sample_period="most_wanted")
    catalog = {"source_id": source, "state": "ok", "sample_state": "complete",
               "fetched_at": detail["fetched_at"], "data": [deck],
               "criteria": {"format": "standard", "rank": "legend", "period": "most_wanted"}}
    payload, status = catalog_publication(catalog)
    store = Store(tmp_path / "lab.sqlite3")
    assert ingest(store, source, payload, status)["snapshots"]
    saved = store.snapshots()[0]
    assert saved["period"] == "most_wanted"
    assert saved["patch"] == "unknown"
    assert saved["payload"]["archetypes"][0]["decks"][0]["fetched_at"] == deck["updated_at"]
    catalog["sample_state"] = "partial"
    payload, status = catalog_publication(catalog)
    assert not ingest(store, source, payload, status)["snapshots"]


def test_build_selection_uses_measurement_date_not_parent_publication(tmp_path):
    from app.meta_intelligence.service import detail

    now = datetime.now(UTC)
    fresh = dataset((now-timedelta(minutes=5)).isoformat(), details=True)
    stale = deepcopy(fresh)
    stale["fetched_at"] = (now-timedelta(minutes=1)).isoformat()
    for row in stale["data"]["structured"]["archetypes"]:
        for deck in row["decks"]:
            deck["updated_at"] = (now-timedelta(days=20)).isoformat()
            deck["win_rate"] = 99
    store = Store(tmp_path / "lab.sqlite3")
    publish(store, fresh, source="hsguru_lab_fresh")
    publish(store, stale, source="hsguru_lab_old_builds_new_publication")
    name = fresh["data"]["structured"]["archetypes"][0]["archetype"]
    scope = fresh["data"]["structured"]["criteria"]
    result = detail(store, name, **{k: scope[k] for k in ("format", "rank", "period", "patch")})
    assert result["decks"][0]["winrate"] != 99
    assert result["decks"][0]["fresh"]
