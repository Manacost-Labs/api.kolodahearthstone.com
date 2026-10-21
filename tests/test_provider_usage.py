from __future__ import annotations

import io
import json
import sqlite3
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import provider_usage
from app.sources import Source

HSGURU_URL = "https://www.hsguru.com/meta?format=2&rank=legend"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    for name in provider_usage.PROVIDERS:
        monkeypatch.delenv(f"HS_{name.upper()}_USD_PER_UNIT", raising=False)
    provider_usage._last_prune.clear()
    return tmp_path / provider_usage.DB_NAME


def rows(path) -> list[dict]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM calls ORDER BY id")]


def record(provider: str, *, duration_ms: int = 100, **kwargs) -> int | None:
    return provider_usage.record_call(
        provider,
        url=kwargs.pop("url", HSGURU_URL),
        started=time.monotonic() - duration_ms / 1000,
        **kwargs,
    )


def test_rows_keep_no_url_and_summary_matches_dashboard_fields(ledger) -> None:
    with provider_usage.usage_source("hsguru_meta_standard_legend"):
        record("decodo", status=200, content="x" * 1000, cost=1, duration_ms=200)
        record("decodo", status=200, content="y" * 500, cost=1, duration_ms=400)
        record("decodo", status=403, cost=0, duration_ms=1000)
    record(
        "scrape_do",
        status=200,
        content="z" * 2000,
        cost=5,
        source_id="hsreplay_cards",
    )

    stored = rows(ledger)
    assert all("rank=legend" not in json.dumps(row) for row in stored)
    assert stored[0]["host"] == "www.hsguru.com"
    assert len(stored[0]["url_hash"]) == 16
    assert stored[2]["outcome"] == "failed"
    assert stored[2]["reason"] == "target_403"

    report = provider_usage.summary(days=7)
    decodo = next(item for item in report["providers"] if item["provider"] == "decodo")
    assert decodo["total"] == 3
    assert decodo["success"] == 2
    assert decodo["failed"] == 1
    assert decodo["success_rate"] == 66.7
    assert decodo["bytes"] == 1500
    assert decodo["cost_units"] == 2
    assert decodo["cost_usd"] == 0.002
    assert decodo["usd_per_1k_success"] == 1.0
    assert decodo["p95_ms"] >= 1000
    assert 500 <= decodo["avg_ms"] < 600
    assert report["totals"]["total"] == 4
    assert [row["provider"] for row in report["daily"]] == ["decodo", "scrape_do"]
    assert report["failure_reasons"] == [
        {"provider": "decodo", "reason": "target_403", "count": 1}
    ]

    only_source = provider_usage.summary(days=7, source_id="hsreplay_cards")
    assert [item["provider"] for item in only_source["providers"]] == ["scrape_do"]
    only_decodo = provider_usage.summary(days=7, provider="decodo")
    assert only_decodo["totals"]["total"] == 3
    with pytest.raises(ValueError):
        provider_usage.summary(provider="brightdata")


def test_configured_price_overrides_default(ledger, monkeypatch) -> None:
    monkeypatch.setenv("HS_SCRAPE_DO_USD_PER_UNIT", "0.0001")
    record("scrape_do", status=200, content="ok", cost=25)
    report = provider_usage.summary()
    assert report["pricing"]["scrape_do"] == {
        "unit": "credit",
        "usd_per_unit": 0.0001,
        "basis": "configured",
    }
    assert report["providers"][0]["cost_usd"] == 0.0025


def test_mark_rejected_turns_only_success_into_failure(ledger) -> None:
    accepted = record("scrape_do", status=200, content="shell", cost=1)
    failed = record("scrape_do", status=502, reason="http_502")
    provider_usage.mark_rejected(accepted)
    provider_usage.mark_rejected(failed, "blocked_content")
    provider_usage.mark_rejected(None)

    stored = rows(ledger)
    assert (stored[0]["outcome"], stored[0]["reason"]) == ("failed", "content_rejected")
    assert stored[0]["cost"] == 1
    assert stored[1]["reason"] == "http_502"


def test_deleted_ledger_is_recreated(ledger) -> None:
    record("decodo", status=200, content="ok", cost=1)
    ledger.unlink()
    assert record("decodo", status=200, content="ok", cost=1) is not None
    assert len(rows(ledger)) == 1


def test_retention_prunes_rows_older_than_ninety_days(ledger) -> None:
    record("firecrawl", status=200, content="ok", cost=1)
    old_day = (datetime.now(UTC) - timedelta(days=91)).date().isoformat()
    with sqlite3.connect(ledger) as conn:
        conn.execute("UPDATE calls SET day=?", (old_day,))
    provider_usage._last_prune.clear()
    record("firecrawl", status=200, content="ok", cost=1)
    assert len(rows(ledger)) == 1


def test_ledger_failure_never_reaches_the_caller(ledger, caplog) -> None:
    with patch.object(provider_usage, "db_path", lambda: ledger.parent):
        assert record("decodo", status=200, content="ok", cost=1) is None
        provider_usage.mark_rejected(1)
    assert "provider usage write skipped" in caplog.text
    assert record("decodo", status=object(), content=object(), cost=object()) is not None
    assert rows(ledger)[0]["reason"] == "no_status"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_decodo_calls_are_recorded_with_billable_success(ledger, monkeypatch) -> None:
    from app import decodo_backend

    monkeypatch.setenv("HS_DECODO_API_KEY", "test-key")
    body = {"results": [{"content": "<html>meta</html>", "status_code": 200}]}
    responses = [
        _Response(json.dumps(body).encode()),
        urllib.error.HTTPError(decodo_backend.DECODO_URL, 429, "busy", {}, None),
        _Response(
            json.dumps(
                {"results": [{"content": "<html>no</html>", "status_code": 403}]}
            ).encode()
        ),
    ]

    def urlopen(request, timeout):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(decodo_backend.urllib.request, "urlopen", urlopen)
    with provider_usage.usage_source("hsguru_meta_standard_legend"):
        scraped = decodo_backend.scrape_url_sync(HSGURU_URL, render=True)
        with pytest.raises(decodo_backend.DecodoRequestError):
            decodo_backend.scrape_url_sync(HSGURU_URL)
        decodo_backend.scrape_url_sync(HSGURU_URL)

    stored = rows(ledger)
    assert scraped.usage_call_id == stored[0]["id"]
    assert [(r["outcome"], r["reason"], r["cost"]) for r in stored] == [
        ("success", None, 1.0),
        ("failed", "http_429", 0.0),
        ("failed", "target_403", 0.0),
    ]
    assert stored[0]["profile"] == "standard+render"
    assert stored[0]["source_id"] == "hsguru_meta_standard_legend"
    assert "test-key" not in json.dumps(stored)


def test_scrape_do_credits_come_from_response_headers(ledger, monkeypatch) -> None:
    from app import scrape_do_backend

    monkeypatch.setenv("HS_SCRAPE_DO_TOKEN", "test-token")

    class Response(_Response):
        status = 200
        headers = {"scrape.do-request-cost": "5", "scrape.do-remaining-credits": "9"}

    monkeypatch.setattr(
        scrape_do_backend.urllib.request,
        "urlopen",
        lambda request, timeout: Response(b"<html>cards</html>"),
    )
    with patch("app.meta_intelligence.budget.begin_attempt", return_value=None):
        scraped = scrape_do_backend.scrape_url_sync(HSGURU_URL, render=True)

    [stored] = rows(ledger)
    assert scraped.usage_call_id == stored["id"]
    assert (stored["outcome"], stored["cost"], stored["cost_basis"]) == (
        "success",
        5.0,
        "reported",
    )
    assert stored["profile"] == "standard+render"
    assert "test-token" not in json.dumps(stored)


def test_cascade_marks_content_rejection_and_attributes_source(ledger) -> None:
    from app.firecrawl_backend import _scrape_sync
    from app.scrape_do_backend import ScrapeDoScrape

    source = Source(
        id="provider_usage_cascade_test",
        url="https://example.com/page",
        site="example",
        category="test",
    )

    def raw_scrape(url, **kwargs):
        return ScrapeDoScrape(
            html="<html>login</html>",
            status_code=200,
            final_url=url,
            request_cost=1,
            credits_remaining=None,
            super_proxy=bool(kwargs.get("super_proxy")),
            cost_basis="reported",
        )

    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch("app.scrape_do_backend.scrape_do_token", return_value="configured"),
        patch("app.scrape_do_backend._scrape_url_sync", side_effect=raw_scrape),
        patch("app.meta_intelligence.budget.begin_attempt", return_value=None),
        pytest.raises(RuntimeError),
    ):
        _scrape_sync(
            source,
            formats=["html"],
            skip_providers={"firecrawl", "brightdata", "scrapfly", "decodo"},
            brightdata_accept_html=lambda html: False,
        )

    stored = rows(ledger)
    assert stored
    assert {row["source_id"] for row in stored} == {source.id}
    assert {(row["outcome"], row["reason"]) for row in stored} == {
        ("failed", "content_rejected")
    }


def test_ops_endpoint_requires_admin(ledger, monkeypatch) -> None:
    from app import main

    record("decodo", status=200, content="ok", cost=1)
    monkeypatch.setattr(main, "api_key", lambda: "secret")
    client = TestClient(main.app)
    assert client.get("/ops/provider-usage").status_code == 401
    response = client.get(
        "/ops/provider-usage",
        params={"days": 7, "provider": "decodo"},
        headers={"X-API-Key": "secret"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["providers"][0]["success"] == 1
    bad = client.get(
        "/ops/provider-usage",
        params={"provider": "unknown"},
        headers={"X-API-Key": "secret"},
    )
    assert bad.status_code == 422
