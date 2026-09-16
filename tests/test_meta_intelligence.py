from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.meta_intelligence.budget import BudgetExceeded, acquisition_scope, allowed, begin_attempt, budget_state, finish_attempt, opening_balance, summary
from app.meta_intelligence.freshness import evaluate, with_freshness
from app.meta_intelligence.models import Settings, now_iso
from app.meta_intelligence.selectors import detail_jobs, hot_jobs
from app.meta_intelligence.service import compare, detail, winrate_interval
from app.meta_intelligence.signals import evaluate_sequence, recompute
from app.meta_intelligence.snapshots import ingest, normalize_deck
from app.meta_intelligence.store import Store
from meta_lab_fixtures import dataset, publish


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR",str(tmp_path))
    return Store()


@pytest.mark.parametrize("spent,state", [(559999,"normal"),(560000,"soft_alert"),(619999,"soft_alert"),(620000,"deep_analysis_throttled"),(700000,"hard_limit_reached")])
def test_budget_thresholds(spent,state):
    assert budget_state(spent,Settings()) == state
    assert allowed("critical",spent,Settings())
    assert allowed("detail",spent,Settings()) == (spent<620000)


def test_ledger_includes_failures_estimates_unknown_and_restart(store):
    settings=Settings(enabled=True)
    opening_balance(store,datetime.now(UTC).strftime("%Y-%m"),400000)
    for cost,basis,outcome in ((25,"reported","response"),(5,"reported","error"),(25,"estimated","response"),(None,"unknown","error")):
        with acquisition_scope("hsguru_test"):
            identity=begin_attempt("https://www.hsguru.com/meta?format=2&rank=legend&period=past_week",store=store,settings=settings)
        finish_attempt(identity,credits=cost,basis=basis,duration_ms=123,outcome=outcome,store=store)
    data=summary(Store(store.path),settings)
    assert data["monthly_spent_hsguru"] == 400055
    assert data["budget_exposure"] == 400085
    assert data["cost_by_source"]["hsguru_test"] == 85
    assert data["unknown_cost_requests"] == 1
    assert data["cost_per_confirmed_signal"] is None


def test_reservations_block_parallel_noncritical_but_not_critical(store):
    settings=Settings(enabled=True,soft=50,throttle=70,hard=100,reserve=30)
    opening_balance(store,datetime.now(UTC).strftime("%Y-%m"),50)
    with acquisition_scope("hot",job_class="hot"):
        assert begin_attempt("https://www.hsguru.com/meta",store=store,settings=settings)
        with pytest.raises(BudgetExceeded):
            begin_attempt("https://www.hsguru.com/meta",store=store,settings=settings)
    assert begin_attempt("https://www.hsguru.com/meta",store=store,settings=settings)


def test_untracked_month_requires_opening_balance(store):
    with acquisition_scope("hot",job_class="hot"), pytest.raises(BudgetExceeded):
        begin_attempt("https://www.hsguru.com/meta",store=store,settings=Settings(enabled=True))


def test_ledger_month_rollover(store):
    with store.connect(write=True) as conn:
        conn.execute("INSERT INTO lab_meta VALUES('tracking_started_at','2026-01-01T00:00:00+00:00')")
        conn.execute("INSERT INTO lab_meta VALUES('opening:2026-01','400000')")
    data=summary(store,Settings(),datetime(2026,2,1,tzinfo=UTC))
    assert data["monthly_spent_hsguru"] == 0 and data["month_complete"]


def test_snapshots_append_idempotently_and_cannot_be_overwritten(store,tmp_path):
    first=dataset()
    assert len(publish(store,first)["snapshots"]) == 1
    publish(store,first)
    second=dataset((datetime.now(UTC)-timedelta(hours=4)).isoformat())
    publish(store,second)
    assert len(store.snapshots()) == 2
    with store.connect(write=True) as conn,pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE snapshots SET patch='changed'")
    with store.connect(write=True) as conn,pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM snapshots")
    target=tmp_path/"backup.sqlite3"
    store.backup(target)
    assert len(Store(target).snapshots()) == 2


@pytest.mark.parametrize("mutation", ["partial","invalid","cached","missing_metric","duplicate"])
def test_invalid_data_cannot_enter_history(store,mutation):
    payload=dataset()
    if mutation == "partial": payload["state"]="partial"
    if mutation == "invalid": payload["data"]["structured"]["strategies"]=[]
    if mutation == "cached": payload["backend"]="cache"
    if mutation == "missing_metric": payload["data"]["structured"]["strategies"][0]["games"]=None
    if mutation == "duplicate": payload["data"]["structured"]["strategies"][1]=deepcopy(payload["data"]["structured"]["strategies"][0])
    assert publish(store,payload)["rejected"]
    assert store.snapshots() == []


def test_freshness_is_per_slice_and_failure_marks_lkg(store):
    fresh=dataset()
    publish(store,fresh)
    old=dataset((datetime.now(UTC)-timedelta(hours=5)).isoformat(),format="wild")
    publish(store,old,source="hsguru_wild")
    snaps=store.snapshots()
    assert evaluate(next(s for s in snaps if s["format"]=="standard"))["fresh"]
    assert evaluate(next(s for s in snaps if s["format"]=="wild"))["state"] == "stale"
    ingest(store,"hsguru_lab_meta_standard_legend_past_week",fresh,{"state":"fetch_error","fetched_at":now_iso()})
    assert with_freshness(store,store.snapshots(format="standard")[0])["freshness"]["state"] == "cached"


def test_legacy_ok_cached_status_is_never_fresh(store):
    payload=dataset()
    publish(store,payload)
    result=ingest(store,"hsguru_lab_meta_standard_legend_past_week",payload,{
        "state":"ok","fetched_at":payload["fetched_at"],"serving_cached_dataset":True,
        "effective_state":"ok_cached","last_refresh_state":"fetch_error",
    })
    assert result["rejected"] and len(store.snapshots())==1
    assert with_freshness(store,store.snapshots()[0])["freshness"]["state"]=="cached"


def test_hot_selector_has_exactly_eight_slices_and_no_full_matrix():
    settings=Settings()
    jobs=hot_jobs(0,settings)
    assert len(jobs)==8 and all(j.rank!="all" for j in jobs)
    assert len(hot_jobs(620000,settings))==8
    assert hot_jobs(700000,settings)==[]
    candidates=[{"format":fmt,"archetype":f"A{i}","games":10000-i} for fmt in ("standard","wild") for i in range(50)]
    selected=detail_jobs(candidates,0,settings)
    assert len(selected)==40 and sum(j.format=="wild" for j in selected)==20
    assert detail_jobs(candidates,620000,settings)==[]


def sequence(store, *, delta=1.5):
    now=datetime.now(UTC)-timedelta(minutes=1)
    for index,hours in enumerate((8,4,0)):
        payload=dataset((now-timedelta(hours=hours)).isoformat())
        payload["data"]["structured"]["strategies"][0]["popularity_pct"]=5+index*delta
        publish(store,payload)
    snapshots=sorted(store.snapshots(),key=lambda s:s["fetched_at"])
    snapshots[-1]=with_freshness(store,snapshots[-1])
    return snapshots


@pytest.mark.parametrize("delta,kind", [(1.5,"popularity_rise"),(-1.5,"popularity_drop")])
def test_popularity_requires_repeated_valid_evidence(store,delta,kind):
    snapshots=sequence(store,delta=delta)
    results=evaluate_sequence(snapshots,Settings())
    assert next(s for s in results if s["kind"]==kind)["state"]=="confirmed"
    assert evaluate_sequence(snapshots[-1:],Settings())==[]


@pytest.mark.parametrize("problem",["stale","low_sample","unknown_patch","same_timestamp","gap","mixed_patch_window"])
def test_unreliable_evidence_never_confirms(store,problem):
    snaps=sequence(store)
    if problem=="stale": snaps[-1]["freshness"]["fresh"]=False
    if problem=="low_sample": snaps[0]["payload"]["archetypes"][0]["games"]=10
    if problem=="unknown_patch":
        for s in snaps:s["patch"]="unknown"
    if problem=="same_timestamp": snaps[1]["fetched_at"]=snaps[0]["fetched_at"]
    if problem=="gap": snaps[0]["fetched_at"]=(datetime.now(UTC)-timedelta(days=5)).isoformat()
    if problem=="mixed_patch_window": snaps[0]["payload"]["patch_scope"]="unverified"
    assert not any(s["state"]=="confirmed" for s in evaluate_sequence(snaps,Settings()))


def test_other_signal_rules_do_not_fake_significance(store):
    snaps=sequence(store)
    snaps[0]["payload"]["archetypes"][0]["winrate"]=45
    snaps[-1]["payload"]["archetypes"][0]["decks"]=[{"id":"new","composition_complete":True,"fetched_at":snaps[-1]["fetched_at"]}]
    snaps[0]["payload"]["matchups"]=[{"archetype":"A","vs":"B","winrate":"45%"}]
    snaps[-1]["payload"]["matchups"]=[{"archetype":"A","vs":"B","winrate":"55%"}]
    snaps[-1]["payload"]["streamers"]=[{"archetype":"A","author_reference":f"author{i}","canonical_url":f"https://example.org/{i}"} for i in range(3)]
    signals=evaluate_sequence(snaps,Settings())
    by_kind={s["kind"]:s for s in signals}
    assert {"winrate_shift","matchup_shift","deck_variant_detected","streamer_burst"} <= by_kind.keys()
    assert all(by_kind[k]["state"]!="confirmed" for k in ("winrate_shift","matchup_shift","deck_variant_detected","streamer_burst"))
    snaps[-1]["payload"]["streamers"]*=3
    for row in snaps[-1]["payload"]["streamers"]: row["author_reference"]="same-author"
    assert not any(s["kind"]=="streamer_burst" for s in evaluate_sequence(snaps,Settings()))


def test_failed_source_rejects_previously_confirmed_signal(store):
    sequence(store)
    recompute(store)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM signals WHERE state='confirmed'").fetchone()[0]
    payload=dataset()
    ingest(store,"hsguru_lab_meta_standard_legend_past_week",payload,{"state":"partial","fetched_at":payload["fetched_at"]})
    recompute(store)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM signals WHERE state='confirmed'").fetchone()[0]==0
        assert conn.execute("SELECT COUNT(*) FROM signal_events WHERE state='rejected'").fetchone()[0]


def test_deck_comparison_preserves_scope_and_sideboards(store):
    payload=dataset(details=True)
    publish(store,payload)
    scope={"format":"standard","rank":"legend","period":"past_week","patch":"patch_36.0.3"}
    info=detail(store,"Дракон Воин",**scope)
    a,b=info["decks"][:2]
    result=compare(store,"Дракон Воин",a["id"],b["id"],**scope)
    assert len(result["changes"])==2
    assert result["winner"] is None and result["comparable"]
    assert compare(store,"Дракон Воин",a["id"],b["id"],**{**scope,"rank":"all"}) is None
    assert info["cards"][0]["scope"]=="archetype"
    assert winrate_interval(65,40)[1]-winrate_interval(65,40)[0] > winrate_interval(55,4000)[1]-winrate_interval(55,4000)[0]


def test_reading_empty_store_creates_no_persistent_files(tmp_path):
    store=Store(tmp_path/"absent"/"lab.sqlite3")
    assert store.snapshots()==[]
    assert not store.path.parent.exists()
