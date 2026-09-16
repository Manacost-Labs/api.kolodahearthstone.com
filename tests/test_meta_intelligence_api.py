from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.meta_intelligence.store import Store
from meta_lab_fixtures import seed


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR",str(tmp_path))
    monkeypatch.setenv("HS_META_LAB_ENABLED","1")
    monkeypatch.setenv("HS_API_KEY","lab-offline-test-key")
    seed(Store())
    return TestClient(app)


HEADERS={"X-API-Key":"lab-offline-test-key"}
SCOPE={"format":"standard","rank":"legend","period":"past_week","patch":"patch_36.0.3"}
PREFIX="/v1/meta-intelligence"


@pytest.mark.parametrize("path",["health","budget","archetypes","cards","signals","signals/missing","archetypes/test/timeline","snapshots/missing"])
def test_all_private_endpoints_require_existing_key(client,path):
    response=client.get(f"{PREFIX}/{path}")
    assert response.status_code==401
    assert response.headers["cache-control"]=="private, no-store"
    assert "etag" not in response.headers
    assert client.get(f"{PREFIX}/{path}",headers={"X-API-Key":"wrong"}).status_code==401


def test_research_flow_and_no_public_etags(client):
    health=client.get(PREFIX+"/health",headers=HEADERS).json()
    assert health["scopes"] and health["demo"]
    rows=client.get(PREFIX+"/archetypes",params=SCOPE,headers=HEADERS).json()["data"]
    assert len(rows)==5
    response=client.get(PREFIX+"/archetypes/Дракон Воин",params=SCOPE,headers={**HEADERS,"If-None-Match":"*"})
    assert response.status_code==200 and "etag" not in response.headers
    detail=response.json()
    decks=detail["decks"]
    comparison=client.get(PREFIX+"/archetypes/Дракон Воин/compare",params={**SCOPE,"left":decks[0]["id"],"right":decks[1]["id"]},headers=HEADERS).json()
    assert comparison["changes"] and comparison["winner"] is None
    assert client.get(PREFIX+"/cards",params=SCOPE,headers=HEADERS).json()["data"]
    timeline=client.get(PREFIX+"/archetypes/Дракон Воин/timeline",params=SCOPE,headers=HEADERS).json()["data"]
    assert len(timeline)==4
    assert client.get(PREFIX+"/snapshots/"+timeline[0]["snapshot_id"],headers=HEADERS).status_code==200


def test_read_only_routes_do_not_collect_and_filters_validate(client,monkeypatch):
    def fail(*args,**kwargs):
        raise AssertionError("GET attempted collection")
    monkeypatch.setattr("app.meta_intelligence.collectors.collect_job",fail)
    assert client.post(PREFIX+"/archetypes",headers=HEADERS).status_code==405
    assert client.get(PREFIX+"/signals",params={"kind":"unknown"},headers=HEADERS).status_code==422
    assert client.get(PREFIX+"/signals",params={"since":"2026-09-01T00:00:00"},headers=HEADERS).status_code==422
    result=client.get(PREFIX+"/signals",params={"format":"standard","state":"confirmed","kind":"popularity_rise"},headers=HEADERS).json()
    assert result["total"]==1
    identity=result["data"][0]["id"]
    assert client.get(PREFIX+"/signals/"+identity,headers=HEADERS).json()["history"]


def test_feature_flag_and_empty_panel_shell(client,monkeypatch):
    monkeypatch.setenv("HS_META_LAB_ENABLED","0")
    assert client.get(PREFIX+"/health",headers=HEADERS).status_code==503
    shell=client.get("/ui/meta-intelligence")
    assert shell.status_code==200 and "Дракон Воин" not in shell.text
    assert "frame-ancestors 'none'" in shell.headers["content-security-policy"]
    assert "lab-offline-test-key" not in shell.text


def test_missing_server_key_fails_closed(client,monkeypatch):
    monkeypatch.delenv("HS_API_KEY")
    assert client.get(PREFIX+"/budget",headers=HEADERS).status_code==503


def test_expired_signal_is_not_returned_as_confirmed_without_background_worker(client,monkeypatch):
    monkeypatch.setattr("app.routers.meta_intelligence.with_freshness", lambda store,snap: {**snap,"freshness":{"fresh":False}})
    result=client.get(PREFIX+"/signals",params={"state":"confirmed"},headers=HEADERS).json()
    assert result["total"]==0
    observing=client.get(PREFIX+"/signals",params={"state":"observing"},headers=HEADERS).json()
    assert observing["data"][0]["historical_state"]=="confirmed"


def test_selected_patch_is_applied_to_signal_filter(client):
    result=client.get(PREFIX+"/signals",params={"patch":"unknown"},headers=HEADERS).json()
    assert result["data"]==[]
