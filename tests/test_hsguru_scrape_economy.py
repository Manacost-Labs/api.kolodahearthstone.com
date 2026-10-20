"""Scrape.do economy regressions for HSGuru acquisition.

A verified empty streamer window is a correct page: it must not trigger paid
escalation, AI diagnosis or ten-minute recovery, yet it is never published.
Matrix slice attempts must leave the Meta Lab ``pending`` state.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.parser import parse_html
from app.recovery_condition import recovery_decision
from app.source_validators import validate_structured
from app.sources import SOURCE_BY_ID

STREAMER = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]
HEADERS = "".join(
    f"<th>{name}</th>"
    for name in (
        "Deck",
        "Streamer",
        "Format",
        "Peak",
        "Latest",
        "Worst",
        "Win - Loss",
        "Links",
        "Last Played",
    )
)


def _streamer_page(body: str | None, *, headers: str = HEADERS) -> str:
    tbody = "" if body is None else f"<tbody class='tw-divide-y'>{body}</tbody>"
    return (
        "<html><head><title>Streamer decks</title></head><body><main>"
        "<div id='streamer_decks_table'><table>"
        f"<thead><tr>{headers}</tr></thead>{tbody}"
        "</table></div></main></body></html>"
    )


EMPTY_WINDOW = _streamer_page("")


def test_intact_empty_streamer_table_is_a_verified_empty_window() -> None:
    structured = parse_html(STREAMER, EMPTY_WINDOW)["structured"]

    assert structured["rows"] == []
    assert structured["empty_window_verified"] is True
    report = validate_structured(STREAMER.id, structured)
    assert report.metrics["empty_window_verified"] is True
    # Still never published: the empty-window issue remains.
    assert "hsguru_streamer_decks.empty_window" in {
        issue.code for issue in report.issues
    }


@pytest.mark.parametrize(
    "html",
    [
        _streamer_page(None),
        _streamer_page("", headers="<th>Deck</th><th>Format</th>"),
        "<html><body><main>Something went wrong</main></body></html>",
        EMPTY_WINDOW.replace(
            "</main>", "<div id='deck_stats-1'>Aggro Paladin</div></main>"
        ),
        EMPTY_WINDOW.replace(
            "</main>", "<p>AAECAZ8FBrSBB8ODB9roB5f6B4GKCMOLCAzunwSS1ATP</p></main>"
        ),
    ],
    ids=[
        "missing-body",
        "missing-headers",
        "missing-table",
        "card-layout",
        "deckstring",
    ],
)
def test_incomplete_streamer_page_is_not_an_empty_window(html: str) -> None:
    structured = parse_html(STREAMER, html)["structured"]

    assert not structured.get("empty_window_verified")
    report = validate_structured(STREAMER.id, structured)
    assert report.metrics["empty_window_verified"] is False


def test_browser_empty_window_skips_paid_escalation_and_ai() -> None:
    from app import fetcher

    fetched = SimpleNamespace(
        body=EMPTY_WINDOW,
        http_status=200,
        final_url=STREAMER.url,
        backend="flaresolverr",
        page_snapshot=None,
        parsesunix_mode=None,
        parsesunix_observation=None,
    )
    preserved = {"source_id": STREAMER.id, "state": "ok"}

    with (
        patch("app.fetcher._fetch_generic_html", new=AsyncMock(return_value=fetched)),
        patch(
            "app.fetcher._enrich_streamer_deck_codes_with_parsesunix",
            new=AsyncMock(side_effect=lambda parsed: parsed),
        ),
        patch(
            "app.fetcher._try_firecrawl_html",
            new=AsyncMock(side_effect=AssertionError("paid fallback must be skipped")),
        ) as paid,
        patch("app.fetcher._diagnose_candidate_with_ai", new=AsyncMock()) as ai,
        patch("app.fetcher._save_failure_status", return_value=preserved) as save_failed,
        patch("app.fetcher.firecrawl_primary_source_ids", return_value=set()),
        patch("app.fetcher.send_telegram_alert", new=AsyncMock()),
        patch("app.fetcher.log_action"),
    ):
        result = asyncio.run(fetcher.fetch_source(None, STREAMER))

    assert result["state"] == "ok"
    paid.assert_not_awaited()
    ai.assert_not_awaited()
    failed_status = save_failed.call_args.args[1]
    assert failed_status["state"] == "quality_error"
    assert failed_status["empty_window_verified"] is True


def test_paid_provider_stops_on_verified_empty_window() -> None:
    from app import fetcher
    from app.firecrawl_backend import FirecrawlScrape

    scraped = FirecrawlScrape(
        html=EMPTY_WINDOW,
        markdown="",
        screenshot=None,
        metadata={"backend": "scrape_do", "labAcquisitionId": "attempt-1"},
        status_code=200,
        final_url=STREAMER.url,
    )
    accepted: list[bool] = []

    def scrape_source(source, *, accept_result):
        # The provider chain escalates only when this callback rejects.
        accepted.append(accept_result(scraped))
        return scraped

    with (
        patch(
            "app.firecrawl_backend.scrape_source",
            new=AsyncMock(side_effect=scrape_source),
        ),
        patch("app.meta_intelligence.budget.validate_attempt") as verdict,
        patch("app.fetcher._diagnose_candidate_with_ai", new=AsyncMock()) as ai,
        patch("app.fetcher._save_failure_status", side_effect=lambda _s, status: status),
        patch("app.fetcher.firecrawl_primary_source_ids", return_value=set()),
        patch("app.fetcher.firecrawl_fallback_source_ids", return_value={STREAMER.id}),
        patch("app.fetcher.log_action"),
    ):
        status = asyncio.run(
            fetcher._try_firecrawl_html(
                STREAMER,
                fetched_at="2026-10-20T05:00:00+00:00",
                reason="quality_error:empty",
            )
        )

    assert accepted == [True]
    verdict.assert_called_once_with("attempt-1", False, empty=True)
    ai.assert_not_awaited()
    assert status["state"] == "quality_error"
    assert status["empty_window_verified"] is True


def test_cached_status_keeps_the_empty_window_marker() -> None:
    from app import fetcher

    dataset = {
        "fetched_at": "2026-10-20T03:00:00+00:00",
        "backend": "scrape_do",
        "data": {"structured": {"type": "streamer_decks", "rows": [{}]}},
    }
    failed = {
        "state": "quality_error",
        "fetched_at": "2026-10-20T05:00:00+00:00",
        "detail": "source contract failed: too few rows (0 < 3)",
        "empty_window_verified": True,
    }
    with (
        patch("app.parser_control.load_resolved_public_dataset", return_value=dataset),
        patch(
            "app.fetcher.validate_existing_publication_for_serving",
            return_value=SimpleNamespace(ok=True, reason="", extra={}),
        ),
        patch("app.fetcher.quality_metrics", return_value={}),
        patch("app.fetcher.save_status"),
        patch("app.fetcher.log_action"),
    ):
        status = fetcher._preserve_cached_ok_status(STREAMER, failed)

    assert status["serving_cached_dataset"] is True
    assert status["last_refresh_empty_window_verified"] is True


NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)


def test_recovery_waits_for_schedule_after_verified_empty_window() -> None:
    lkg = {
        "state": "ok",
        "serving_cached_dataset": True,
        "last_refresh_state": "quality_error",
        "last_refresh_at": (NOW - timedelta(hours=2)).isoformat(),
        "last_refresh_empty_window_verified": True,
    }
    cold = {
        "state": "quality_error",
        "fetched_at": (NOW - timedelta(hours=2)).isoformat(),
        "empty_window_verified": True,
    }

    for status in (lkg, cold):
        decision = recovery_decision(status, now=NOW, min_age_seconds=300)
        assert decision["run"] is False
        assert decision["reason"] == "empty_window_waits_for_schedule"

    failed = {key: value for key, value in lkg.items() if "empty" not in key}
    assert recovery_decision(failed, now=NOW, min_age_seconds=300)["run"] is True


def test_lab_records_empty_verdict(tmp_path, monkeypatch) -> None:
    from app.meta_intelligence.budget import (
        acquisition_scope,
        begin_attempt,
        validate_attempt,
    )
    from app.meta_intelligence.models import Settings
    from app.meta_intelligence.store import Store

    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    store = Store()
    settings = Settings(enabled=True)
    ids = []
    for valid, empty in ((True, False), (False, True), (False, False)):
        with acquisition_scope("hsguru_test"):
            identity = begin_attempt(STREAMER.url, store=store, settings=settings)
        validate_attempt(identity, valid, store, empty=empty)
        ids.append(identity)

    with store.connect() as conn:
        verdicts = [
            conn.execute("SELECT validation FROM attempts WHERE id=?", (i,)).fetchone()[0]
            for i in ids
        ]
    assert verdicts == ["valid", "empty", "invalid"]


def test_matrix_slices_record_lab_verdicts() -> None:
    from app.firecrawl_backend import FirecrawlScrape
    from app.hsguru_meta_matrix import SliceSpec, refresh_hsguru_meta_matrix
    from test_hsguru_meta_matrix import (
        HSGURU_TABLE,
        _valid_current_acquisition,
        _valid_current_row,
    )

    specs = (
        SliceSpec(
            "standard",
            "all",
            "past_week",
            "any_player",
            "standard|all|past_week|any_player",
            "https://www.hsguru.com/meta?format=2&rank=all&period=past_week&min_games=100",
        ),
        SliceSpec(
            "standard",
            "legend",
            "past_week",
            "any_player",
            "standard|legend|past_week|any_player",
            "https://www.hsguru.com/meta?format=2&rank=legend&period=past_week&min_games=100",
        ),
    )

    conflicting = """
    <table><thead><tr>
      <th>Archetype</th><th>Winrate</th><th>Popularity</th>
      <th>Turns</th><th>Duration</th><th>Climbing Speed</th>
    </tr></thead><tbody>
      <tr>
        <td class="deathknight"><a href="/archetype/one">Thal'ena DK</a></td>
        <td>41.1</td><td>0.1% (3378)</td><td>8.8</td><td>8.8</td><td>-1.22</td>
      </tr>
      <tr>
        <td class="deathknight"><a href="/archetype/two">Thal'ena DK</a></td>
        <td>26.9</td><td>0.0% (145)</td><td>9.1</td><td>8.6</td><td>-3.21</td>
      </tr>
    </tbody></table>
    """

    async def scrape(spec):
        return FirecrawlScrape(
            html=conflicting if spec.rank == "all" else HSGURU_TABLE,
            markdown="",
            screenshot=None,
            metadata={"creditsUsed": 1, "labAcquisitionId": f"lab-{spec.rank}"},
            status_code=200,
            final_url=spec.url,
        )

    async def scrape_current(format_name, period):
        return [_valid_current_row(format_name, period)], _valid_current_acquisition(
            format_name
        )

    async def discover_patch(_cached):
        return "patch_36.0.3", None

    with (
        patch("app.hsguru_meta_matrix.iter_slice_specs", return_value=specs),
        patch("app.hsguru_meta_matrix.load_dataset", return_value={}),
        patch("app.hsguru_meta_matrix.enrich_current_rows_with_cached_decks"),
        patch("app.hsguru_meta_matrix.save_dataset"),
        patch("app.hsguru_meta_matrix.save_status"),
        patch("app.hsguru_meta_matrix.asyncio.sleep", new_callable=AsyncMock),
        patch("app.hsguru_meta_matrix.validate_attempt") as verdict,
    ):
        asyncio.run(
            refresh_hsguru_meta_matrix(
                concurrency=1,
                attempts=1,
                scrape=scrape,
                scrape_current=scrape_current,
                discover_patch=discover_patch,
            )
        )

    calls = {call.args for call in verdict.call_args_list}
    assert ("lab-legend", True) in calls
    assert ("lab-all", False) in calls


def test_primary_scrape_keeps_the_empty_window_verdict() -> None:
    from app import fetcher
    from app.firecrawl_backend import FirecrawlScrape

    scraped = FirecrawlScrape(
        html=EMPTY_WINDOW,
        markdown="",
        screenshot=None,
        metadata={"backend": "scrape_do"},
        status_code=200,
        final_url=STREAMER.url,
    )

    def scrape_source(source, *, accept_result):
        assert accept_result(scraped) is True
        return scraped

    with (
        patch(
            "app.firecrawl_backend.scrape_source",
            new=AsyncMock(side_effect=scrape_source),
        ),
        patch("app.fetcher._diagnose_candidate_with_ai", new=AsyncMock()) as ai,
        patch("app.fetcher._save_failure_status", side_effect=lambda _s, status: status),
        patch("app.fetcher.firecrawl_primary_source_ids", return_value={STREAMER.id}),
        patch("app.fetcher.log_action"),
    ):
        status = asyncio.run(
            fetcher._try_firecrawl_html(
                STREAMER,
                fetched_at="2026-10-20T05:00:00+00:00",
                reason="primary",
            )
        )

    # The primary route returns the verdict instead of a second paid fetch.
    assert status is not None
    assert status["empty_window_verified"] is True
    ai.assert_not_awaited()


def test_lab_verdict_failure_does_not_fail_the_matrix() -> None:
    from app.hsguru_meta_matrix import _record_lab_verdict

    with patch(
        "app.hsguru_meta_matrix.validate_attempt",
        side_effect=RuntimeError("database is locked"),
    ):
        _record_lab_verdict("lab-1", True)
