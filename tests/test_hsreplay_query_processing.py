from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.fetcher import _attach_failure_class, _preserve_cached_ok_status
from app.hsreplay_cards_api import fetch_hsreplay_ranked_cards
from app.hsreplay_client import (
    HsReplayQueryProcessingError,
    fetch_hsreplay_json,
    is_hsreplay_query_processing,
    reset_hsreplay_refresh_state,
)
from app.sources import SOURCE_BY_ID, Source
from app.stale_monitor import find_stale_sources
from app.storage import dataset_path, save_dataset, save_status, write_json

API_URL = (
    "https://hsreplay.net/analytics/query/card_list/"
    "?GameType=RANKED_WILD&TimeRange=CURRENT_PATCH&LeagueRankRange=PLATINUM"
)
PROCESSING = json.dumps({"msg": "Query is processing. Check back later."})
READY = json.dumps({"series": {"data": {"ALL": [{"dbf_id": 1}]}}})


@pytest.fixture(autouse=True)
def _reset_transport_state() -> None:
    reset_hsreplay_refresh_state()
    yield
    reset_hsreplay_refresh_state()


def _run_fetch(
    bodies: list[str],
    *,
    headers: dict[str, str] | None = None,
) -> tuple[object, MagicMock, AsyncMock, MagicMock, MagicMock]:
    calls = MagicMock(side_effect=bodies)

    async def body_for_channel(label: str, _url: str, **_kwargs: object) -> str:
        return calls(label)

    sleep = AsyncMock()
    cache_set = MagicMock()
    log = MagicMock()
    with (
        patch(
            "app.hsreplay_client._channel_urls",
            return_value=[("flaresolverr", API_URL), ("scrape_do", API_URL)],
        ),
        patch("app.hsreplay_client._channel_uses_residential_proxy", return_value=False),
        patch(
            "app.hsreplay_client._fetch_body_with_proxy_probe_gate",
            side_effect=body_for_channel,
        ),
        patch(
            "app.hsreplay_client._take_pending_target_headers",
            return_value=dict(headers or {}),
        ),
        patch("app.hsreplay_client.hsreplay_json_contract_for_source", return_value=None),
        patch("app.hsreplay_client.get_cached_hsreplay_json", return_value=None),
        patch("app.hsreplay_client.set_cached_hsreplay_json", cache_set),
        patch("app.hsreplay_client.asyncio.sleep", sleep),
        patch("app.hsreplay_client.log_action", log),
    ):
        try:
            result: object = asyncio.run(
                fetch_hsreplay_json(API_URL, source_id="hsreplay_cards_wild_platinum_patch")
            )
        except HsReplayQueryProcessingError as exc:
            result = exc
    return result, calls, sleep, cache_set, log


def test_processing_payload_is_detected_only_without_data() -> None:
    assert is_hsreplay_query_processing(json.loads(PROCESSING))
    assert not is_hsreplay_query_processing(json.loads(READY))
    assert not is_hsreplay_query_processing(
        {"msg": "Query is processing.", "series": {"data": {}}}
    )
    assert not is_hsreplay_query_processing([{"msg": "Query is processing."}])


def test_processing_202_is_polled_until_hsreplay_returns_data() -> None:
    result, calls, sleep, cache_set, _log = _run_fetch([PROCESSING, READY])

    assert result == json.loads(READY)
    # The same query worker answers every channel: no paid channel is tried.
    assert [call.args[0] for call in calls.call_args_list] == [
        "flaresolverr",
        "flaresolverr",
    ]
    sleep.assert_awaited_once_with(30.0)
    cache_set.assert_called_once()
    assert cache_set.call_args.args[1] == json.loads(READY)


def test_processing_202_until_attempts_run_out_raises_upstream_processing() -> None:
    result, calls, sleep, cache_set, log = _run_fetch([PROCESSING] * 4)

    assert isinstance(result, HsReplayQueryProcessingError)
    assert "upstream_processing" in str(result)
    assert result.failure_reason_code == "unavailable"
    assert result.upstream_state == "upstream_processing"
    assert calls.call_count == 4
    assert sleep.await_count == 3
    # A processing body is never cached as the refresh's JSON snapshot.
    cache_set.assert_not_called()
    route_fail = [
        call for call in log.call_args_list if call.args[0] == "api.route.fail"
    ]
    assert len(route_fail) == 1
    assert route_fail[0].kwargs["extra"]["reason"] == "upstream_processing"


@pytest.mark.parametrize(("retry_after", "expected"), [("5", 5.0), ("600", 60.0)])
def test_processing_wait_honours_bounded_retry_after(
    retry_after: str,
    expected: float,
) -> None:
    _result, _calls, sleep, _cache_set, _log = _run_fetch(
        [PROCESSING, READY],
        headers={"retry-after": retry_after},
    )

    sleep.assert_awaited_once_with(expected)


def test_processing_waits_stop_at_refresh_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HS_HSREPLAY_QUERY_PROCESSING_BUDGET_SECONDS", "0")

    result, calls, sleep, _cache_set, _log = _run_fetch([PROCESSING] * 4)

    assert isinstance(result, HsReplayQueryProcessingError)
    assert calls.call_count == 1
    sleep.assert_not_awaited()


def test_cards_processing_skips_paid_fallback_and_sparse_gate() -> None:
    source = SOURCE_BY_ID["hsreplay_cards_wild_platinum_patch"]
    period_fallback = AsyncMock()
    log = MagicMock()

    with (
        patch(
            "app.hsreplay_client.fetch_hsreplay_json",
            AsyncMock(side_effect=HsReplayQueryProcessingError("upstream_processing")),
        ),
        patch(
            "app.hsreplay_card_periods.fetch_hsreplay_card_period_json",
            period_fallback,
        ),
        patch("app.refresh_log.log_action", log),
        pytest.raises(HsReplayQueryProcessingError),
    ):
        asyncio.run(fetch_hsreplay_ranked_cards(source))

    period_fallback.assert_not_awaited()
    assert all(call.args[0] != "api.validate.fail" for call in log.call_args_list)


def test_processing_failure_keeps_lkg_with_upstream_processing_reason() -> None:
    source = Source(
        id="hsguru_meta_standard_legend",
        site="hsguru",
        category="meta",
        url="https://www.hsguru.com/meta",
    )
    failed = _attach_failure_class(
        {
            "source_id": source.id,
            "state": "fetch_error",
            "fetched_at": "2026-10-21T07:07:29+00:00",
            "detail": "HSReplay analytics query still processing (upstream_processing)",
        },
        HsReplayQueryProcessingError("upstream_processing"),
    )
    assert failed["failure_reason_code"] == "unavailable"
    assert failed["upstream_state"] == "upstream_processing"

    with TemporaryDirectory() as td, patch("app.storage.data_dir", return_value=Path(td)):
        save_dataset(
            source.id,
            {
                "fetched_at": "2026-10-18T10:00:00+00:00",
                "backend": "flaresolverr",
                "data": {
                    "title": "HSGuru Meta",
                    "tables": [{"objects": [{"deck": d} for d in "ABCDE"]}],
                },
            },
        )
        preserved = _preserve_cached_ok_status(source, failed)

    assert preserved is not None
    assert preserved["state"] == "ok"
    assert preserved["serving_cached_dataset"] is True
    assert preserved["failure_reason_code"] == "unavailable"
    assert preserved["last_refresh_upstream_state"] == "upstream_processing"


SCREENSHOT = SOURCE_BY_ID["hsreplay_battlegrounds_compositions_screenshot"]


def test_status_only_source_rejects_dataset_writes() -> None:
    with (
        TemporaryDirectory() as td,
        patch("app.storage.data_dir", return_value=Path(td)),
        pytest.raises(ValueError, match="publishes no dataset"),
    ):
        save_dataset(SCREENSHOT.id, {"fetched_at": "2026-10-21T00:00:00+00:00"})


def test_status_only_freshness_ignores_orphaned_legacy_dataset() -> None:
    captured_at = datetime.now(UTC) - timedelta(minutes=5)
    orphan_at = datetime.now(UTC) - timedelta(days=10)

    with (
        TemporaryDirectory() as directory,
        patch.dict(os.environ, {"HS_API_DATA_DIR": directory}, clear=False),
        patch("app.stale_monitor.SOURCES", [SCREENSHOT]),
        patch("app.demo.SOURCES", (SCREENSHOT,)),
    ):
        write_json(
            dataset_path(SCREENSHOT.id),
            {
                "source_id": SCREENSHOT.id,
                "fetched_at": orphan_at.isoformat(),
                "data": {"structured": {"type": "bg_compositions_screenshot"}},
            },
        )
        save_status(
            SCREENSHOT.id,
            {"state": "ok", "fetched_at": captured_at.isoformat()},
        )
        stale = find_stale_sources(include_ok=True)
        from app.demo import build_overview

        row = build_overview()["sources"][0]

    assert stale == []
    assert row["fetched_at"] == captured_at.isoformat()
    assert row["stale"] is False
