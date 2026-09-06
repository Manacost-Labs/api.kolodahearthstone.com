import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import battlegrounds_comps_parse as bg


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("limit", [0, 2, 40])
def test_detail_budget_does_not_truncate_listing(monkeypatch, fallback, limit):
    rows = [
        {"id": str(i), "url": f"https://hsreplay.net/battlegrounds/comps/{i}/test/"}
        for i in range(45)
    ]
    monkeypatch.setattr(bg, "parse_hsreplay_markdown", lambda *a, **kw: rows)
    monkeypatch.setattr(
        bg,
        "scrape_source",
        AsyncMock(
            return_value=SimpleNamespace(
                markdown="listing", final_url=bg.HSREPLAY_COMPS_URL
            )
        ),
    )
    detail = AsyncMock(return_value={"main_cards": [{"name": "Test"}]})
    monkeypatch.setattr(bg, "_firecrawl_detail", detail)
    if fallback:
        monkeypatch.setattr(
            bg,
            "fetch_battlegrounds_comps_firecrawl",
            AsyncMock(side_effect=RuntimeError("offline")),
        )
        monkeypatch.setattr(
            bg,
            "fetch_hsreplay_markdown",
            AsyncMock(return_value=("listing", "fixture")),
        )
        detail = AsyncMock(
            side_effect=lambda c, **kw: {**c, "detail_status": "fetched"}
        )
        monkeypatch.setattr(bg, "_enrich_comp_cards", detail)
    fetch = (
        bg.fetch_battlegrounds_comps
        if fallback
        else bg.fetch_battlegrounds_comps_firecrawl
    )
    result = asyncio.run(fetch(detail_limit=limit))
    assert [row["id"] for row in result["comps"]] == [str(i) for i in range(45)]
    assert detail.await_count == limit
    assert result["source"]["comps_total"] == 45
    assert result["source"]["details_not_requested"] == 45 - limit
    assert all(
        row["detail_status"] == "not_requested" for row in result["comps"][limit:]
    )


def test_failed_detail_keeps_listing_and_reports_failure(monkeypatch):
    monkeypatch.setattr(
        bg,
        "scrape_source",
        AsyncMock(
            return_value=SimpleNamespace(
                markdown="listing", final_url=bg.HSREPLAY_COMPS_URL
            )
        ),
    )
    monkeypatch.setattr(
        bg,
        "parse_hsreplay_markdown",
        lambda *a, **kw: [
            {"id": "1", "url": "https://hsreplay.net/battlegrounds/comps/1/test/"}
        ],
    )
    monkeypatch.setattr(
        bg, "_firecrawl_detail", AsyncMock(side_effect=RuntimeError("offline"))
    )
    result = asyncio.run(bg.fetch_battlegrounds_comps_firecrawl(detail_limit=1))
    assert result["comps"][0]["detail_status"] == "failed"
    assert result["comps"][0]["id"] == "1"


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_invalid_budget_fails_before_network(monkeypatch, limit):
    fetch = AsyncMock()
    monkeypatch.setattr(bg, "scrape_source", fetch)
    with pytest.raises(ValueError):
        asyncio.run(bg.fetch_battlegrounds_comps_firecrawl(detail_limit=limit))
    fetch.assert_not_called()
