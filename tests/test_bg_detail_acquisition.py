import asyncio
from unittest.mock import AsyncMock

import pytest

from app import bg_detail_acquisition as bg
from app.acquisition_coverage import ListingEvidence, source_view
from app.acquisition_detail_queue import DetailQueue
from app.scrape_do_backend import ScrapeDoAccountError, ScrapeDoScrape
from app.sources import SOURCE_BY_ID


def selected():
    return source_view(
        SOURCE_BY_ID["hsreplay_battlegrounds_comps"],
        snapshot_id="capture-1",
        patch_id="patch-a",
    )


def row(i, *, ready=False):
    return {
        "id": f"hsreplay-{i}",
        "comp_id": i,
        "tier": "S",
        "url": f"https://hsreplay.net/battlegrounds/comps/{i}/mechs/",
        "main_cards": [{"card_id": "BG_TEST", "name": "Test"}] if ready else [],
        "how_to_play": "Play these cards" if ready else "",
    }


def success_html():
    return '<h1>Mechs Comp Season 13</h1><h2>Core Cards for Mechs</h2><img alt="Test" src="https://art.hearthstonejson.com/v1/256x/BG_TEST.png"><h2>How to Play Mechs</h2><p>Play these cards.</p>'


@pytest.fixture(autouse=True)
def offline_cards(monkeypatch):
    monkeypatch.setattr("app.battlegrounds_comps_parse.cards_by_id", dict)
    monkeypatch.setattr("app.battlegrounds_comps_parse.cards_by_dbfid", dict)


def response(url, **kwargs):
    return ScrapeDoScrape(
        html=success_html(),
        status_code=200,
        final_url=url,
        request_cost=5,
        credits_remaining=100,
        super_proxy=False,
    )


def test_only_missing_details_are_fetched_and_survive_restart(tmp_path, monkeypatch):
    queue = DetailQueue(tmp_path / "details.sqlite")
    rows = [row(1, ready=True), row(2), row(3)]
    fetch = AsyncMock(side_effect=response)
    monkeypatch.setattr(bg, "scrape_url", fetch)
    assert bg.prepare_bg_details(queue, selected(), rows) == 2
    result = asyncio.run(bg.drain_bg_details(queue, selected(), max_requests=1))
    assert result["requests"] == 1
    restarted = DetailQueue(tmp_path / "details.sqlite")
    assert bg.prepare_bg_details(restarted, selected(), rows) == 0
    asyncio.run(bg.drain_bg_details(restarted, selected()))
    assert [call.args[0] for call in fetch.await_args_list] == [
        row(2)["url"],
        row(3)["url"],
    ]
    evidence = ListingEvidence(
        selected().scope_id,
        tuple(r["id"] for r in rows),
        exhausted=True,
        expected_count=3,
        view_confirmed=True,
    )
    result = bg.bg_detail_report(restarted, selected(), rows, listing=evidence)
    assert result["coverage"]["status"] == "complete_for_view"
    assert result["coverage"]["details"]["succeeded"] == 3
    assert result["rows"][1]["tier"] == "S"
    assert rows[1]["main_cards"] == []
    assert result["credits_spent"] == 10


def test_empty_response_is_retry_not_confirmed_absence(tmp_path, monkeypatch):
    queue = DetailQueue(tmp_path / "details.sqlite")
    bg.prepare_bg_details(queue, selected(), [row(1)])
    monkeypatch.setattr(
        bg,
        "scrape_url",
        AsyncMock(
            return_value=ScrapeDoScrape(
                html="<html>Loading</html>",
                status_code=200,
                final_url=row(1)["url"],
                request_cost=5,
                credits_remaining=100,
                super_proxy=False,
            )
        ),
    )
    asyncio.run(bg.drain_bg_details(queue, selected()))
    state = queue.items(selected().scope_id)[0]
    assert state["status"] == "retry"
    assert state["error_code"] == "invalid_detail"
    assert state["credits_spent"] == 5


def test_account_error_stops_batch_without_raw_error_storage(tmp_path, monkeypatch):
    queue = DetailQueue(tmp_path / "details.sqlite")
    bg.prepare_bg_details(queue, selected(), [row(1), row(2)])
    fetch = AsyncMock(side_effect=ScrapeDoAccountError("secret-token", status_code=401))
    monkeypatch.setattr(bg, "scrape_url", fetch)
    asyncio.run(bg.drain_bg_details(queue, selected()))
    assert fetch.await_count == 1
    assert "secret-token" not in str(queue.items(selected().scope_id))
    assert queue.items(selected().scope_id)[1]["status"] == "pending"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/comps/1",
        "https://hsreplay.net/battlegrounds/comps/2/mechs/",
        "https://hsreplay.net/battlegrounds/comps/1/mechs/?token=secret",
    ],
)
def test_invalid_listing_is_rejected_before_enqueue(tmp_path, url):
    queue = DetailQueue(tmp_path / "details.sqlite")
    with pytest.raises(ValueError):
        bg.prepare_bg_details(queue, selected(), [row(2), {**row(1), "url": url}])
    assert queue.items(selected().scope_id) == []


def test_final_url_scope_mismatch_is_not_success(tmp_path, monkeypatch):
    queue = DetailQueue(tmp_path / "details.sqlite")
    bg.prepare_bg_details(queue, selected(), [row(1)])
    monkeypatch.setattr(
        bg, "scrape_url", AsyncMock(return_value=response(row(2)["url"]))
    )
    asyncio.run(bg.drain_bg_details(queue, selected()))
    assert (
        queue.items(selected().scope_id)[0]["error_code"] == "response_scope_mismatch"
    )


def test_timeout_counts_attempt_and_unknown_cost(tmp_path, monkeypatch):
    queue = DetailQueue(tmp_path / "details.sqlite")
    bg.prepare_bg_details(queue, selected(), [row(1), row(2)])
    fetch = AsyncMock(side_effect=TimeoutError("secret"))
    monkeypatch.setattr(bg, "scrape_url", fetch)
    result = asyncio.run(bg.drain_bg_details(queue, selected()))
    assert result["requests"] == 1
    assert queue.items(selected().scope_id)[0]["unknown_cost_attempts"] == 1
    assert queue.items(selected().scope_id)[1]["status"] == "pending"


@pytest.mark.parametrize(
    "identity", [{"comp_id": 999}, {"comp_id": "1"}, {"comp_id": True}, {}]
)
def test_comp_id_must_match_listing_identity_before_enqueue(tmp_path, identity):
    queue = DetailQueue(tmp_path / "details.sqlite")
    invalid = row(1)
    del invalid["comp_id"]
    invalid.update(identity)
    with pytest.raises(ValueError):
        bg.prepare_bg_details(queue, selected(), [row(2), invalid])
    assert queue.items(selected().scope_id) == []
    with pytest.raises(ValueError):
        bg.bg_detail_report(queue, selected(), [invalid])


def test_detail_payload_cannot_replace_listing_identity(tmp_path):
    queue = DetailQueue(tmp_path / "details.sqlite")
    bg.prepare_bg_details(queue, selected(), [row(1)])
    claim = queue.claim(selected().scope_id)
    queue.finish(claim, status="succeeded", payload=row(999, ready=True))
    result = bg.bg_detail_report(queue, selected(), [row(1)])["rows"][0]
    assert result["id"] == "hsreplay-1"
    assert result["url"] == row(1)["url"]
    assert result["comp_id"] == 1


def test_unexpected_provider_error_is_redacted_and_retryable(tmp_path, monkeypatch):
    queue = DetailQueue(tmp_path / "details.sqlite")
    bg.prepare_bg_details(queue, selected(), [row(1)])
    monkeypatch.setattr(
        bg, "scrape_url", AsyncMock(side_effect=ValueError("private-provider-token"))
    )
    asyncio.run(bg.drain_bg_details(queue, selected()))
    state = queue.items(selected().scope_id)[0]
    assert state["status"] == "retry"
    assert state["error_code"] == "fetch_error"
    assert "private-provider-token" not in str(state)
    assert state["unknown_cost_attempts"] == 1
