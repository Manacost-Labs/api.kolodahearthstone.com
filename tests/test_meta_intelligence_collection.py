from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import subprocess
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.meta_intelligence.budget import BudgetExceeded, acquisition_scope, opening_balance, summary
from app.meta_intelligence.collectors import collect_job, next_deck_page
from app.meta_intelligence.models import Settings
from app.meta_intelligence.selectors import Job
from app.meta_intelligence.store import Store
from app.scrape_do_backend import ScrapeDoTransientError, scrape_url_sync
from meta_lab_fixtures import dataset,publish
from meta_lab_fixtures import META_HTML


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR",str(tmp_path/"source"))
    monkeypatch.setenv("HS_META_LAB_ENABLED","1")
    monkeypatch.setenv("HS_SCRAPE_DO_TOKEN","offline-test-token")
    result=Store()
    opening_balance(result,datetime.now(UTC).strftime("%Y-%m"),400000)
    return result


def test_actual_error_cost_is_recorded_without_url_or_token(store):
    exc=urllib.error.HTTPError("https://api.scrape.do/?token=offline-test-token",503,"failed",{"scrape.do-request-cost":"5"},io.BytesIO(b"failed"))
    with acquisition_scope("hsguru_source"),patch("urllib.request.urlopen",side_effect=exc),pytest.raises(ScrapeDoTransientError):
        scrape_url_sync("https://www.hsguru.com/meta?format=2&rank=legend&period=past_week")
    with store.connect() as conn:
        rows=[dict(r) for r in conn.execute("SELECT * FROM attempts")]
    assert rows[0]["credits"]==5 and rows[0]["cost_basis"]=="reported"
    assert rows[0]["duration_ms"]>=0 and rows[0]["source_id"]=="hsguru_source"
    assert "offline-test-token" not in json.dumps(rows)
    assert "https://" not in json.dumps(rows)


def test_success_without_cost_header_is_explicitly_estimated(store):
    class Response:
        status=200
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return b"<html>valid body</html>"
    with patch("urllib.request.urlopen",return_value=Response()):
        result=scrape_url_sync("https://www.hsguru.com/meta",super_proxy=True)
    assert result.acquisition_id and result.cost_basis=="estimated"
    assert summary(store,Settings())["estimated_credits"]==25


def test_failed_collector_preserves_good_snapshot_and_marks_cached(store):
    job=Job("meta","standard","legend")
    publish(store,dataset(),source=job.source_id)
    from app.storage import save_dataset
    save_dataset(job.source_id,dataset())
    async def fetch(*args,**kwargs):
        return SimpleNamespace(html="<html>challenge</html>",metadata={},backend="scrape_do")
    result=asyncio.run(collect_job(job,"patch_36.0.3",store,Settings(enabled=True),fetch=fetch))
    assert result["state"]=="failed" and len(store.snapshots())==1
    from app.meta_intelligence.freshness import with_freshness
    assert with_freshness(store,store.snapshots()[0])["freshness"]["state"]=="cached"


def test_collector_reuses_parser_and_publishes_separate_slice(store):
    html=META_HTML
    # Five normal archetypes meet the existing source validator.
    row=html[html.index('  <tr>\n    <td>Quest Mage'):html.index('  <tr>\n    <td>Tiny Rogue')]
    html=html.replace(row,''.join(row.replace('Quest Mage',f'Quest Mage {i}') for i in range(5)))
    async def fetch(*args,**kwargs):
        return SimpleNamespace(html=html,metadata={},backend="scrape_do")
    job=Job("meta","standard","legend")
    result=asyncio.run(collect_job(job,"patch_36.0.3",store,Settings(enabled=True),fetch=fetch))
    assert result["state"]=="ok" and len(store.snapshots())==1
    assert store.snapshots()[0]["source_id"]==job.source_id
    assert not (store.path.parent.parent/"datasets/hsguru_meta_matrix.json").exists()


def test_pagination_never_follows_foreign_origin_or_changed_slice():
    url="https://www.hsguru.com/decks?format=2&rank=legend&period=past_week"
    assert next_deck_page('<a rel="next" href="?page=2">Next</a>',url)==2
    assert next_deck_page('<a rel="next" href="https://evil.example/decks?page=2">Next</a>',url) is None
    assert next_deck_page('<a rel="next" href="?page=2&format=1">Next</a>',url) is None


def test_budget_denial_cannot_switch_to_a_different_paid_provider(store):
    from app.firecrawl_backend import _scrape_sync
    from app.sources import SOURCE_BY_ID
    with patch("app.firecrawl_backend._scrape_via_scrape_do",side_effect=BudgetExceeded("stop")), patch("app.firecrawl_backend._scrape_via_firecrawl") as firecrawl, patch("app.firecrawl_backend._scrape_via_scrapfly") as scrapfly:
        with pytest.raises(BudgetExceeded):
            _scrape_sync(SOURCE_BY_ID["hsguru_meta_standard_legend"])
        firecrawl.assert_not_called()
        scrapfly.assert_not_called()


def test_event_detail_queue_deduplicates_and_honors_recent_work(store):
    from test_meta_intelligence import sequence
    from app.meta_intelligence.collectors import enqueue_details
    from app.meta_intelligence.signals import recompute
    sequence(store)
    recompute(store)
    assert enqueue_details(store,"patch_36.0.3",Settings())==1
    assert enqueue_details(store,"patch_36.0.3",Settings())==0
    job=Job("details","standard","legend",archetype="Дракон Воин")
    publish(store,dataset(details=True),source=job.source_id)
    assert enqueue_details(store,"patch_36.0.3",Settings())==0


def test_migration_bundle_restores_lab_and_refuses_overwrite(store,tmp_path):
    publish(store,dataset())
    archive=tmp_path/"bundle.tar.gz"
    repo=Path(__file__).resolve().parents[1]
    env={**os.environ,"HS_API_DATA_DIR":str(store.path.parent.parent),"HS_ENV_FILE":str(tmp_path/"absent.env")}
    subprocess.run(["bash",str(repo/"scripts/export-bundle.sh"),str(archive)],env=env,check=True,capture_output=True)
    restored=tmp_path/"restored"
    env["HS_API_DATA_DIR"]=str(restored)
    subprocess.run(["bash",str(repo/"scripts/import-bundle.sh"),str(archive)],env=env,check=True,capture_output=True)
    restored_store=Store(restored/"meta-intelligence/lab.sqlite3")
    assert len(restored_store.snapshots())==1
    second=subprocess.run(["bash",str(repo/"scripts/import-bundle.sh"),str(archive)],env=env,capture_output=True)
    assert second.returncode==1 and b"Refusing to overwrite" in second.stdout
