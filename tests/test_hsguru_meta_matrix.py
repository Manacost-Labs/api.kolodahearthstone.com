from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


HSGURU_TABLE = """
<html><body><table>
  <thead><tr>
    <th>Archetype</th><th>Winrate</th><th>Popularity</th>
    <th>Turns</th><th>Duration</th><th>Climbing Speed</th>
  </tr></thead>
  <tbody>
    <tr><td>Big Shaman</td><td>55.4%</td><td>1.7% (5,321)</td><td>8.2</td><td>7.6 min</td><td>+2.4</td></tr>
    <tr><td>Quest Mage</td><td>51.1%</td><td>0.8% (742)</td><td>9.1</td><td>8.0 min</td><td>+0.4</td></tr>
  </tbody>
</table></body></html>
"""


def test_matrix_has_108_remote_slices_and_six_local_min_game_filters() -> None:
    from app.hsguru_meta_matrix import MIN_GAMES, iter_slice_specs

    specs = list(iter_slice_specs())

    assert MIN_GAMES == (100, 250, 500, 1000, 2500, 5000)
    assert 7500 not in MIN_GAMES
    assert len(specs) == 108
    assert len({spec.key for spec in specs}) == 108
    assert all("min_games=100" in spec.url for spec in specs)
    assert all("7500" not in spec.url for spec in specs)
    assert {spec.period for spec in specs} == {
        "past_day",
        "past_3_days",
        "past_week",
        "past_2_weeks",
        "patch_36.2.0",
        "violet_hold",
    }
    assert {spec.rank for spec in specs} == {
        "all",
        "diamond",
        "diamond_4to1",
        "diamond_to_legend",
        "legend",
        "top_5k",
        "top_legend",
        "top_500",
        "top_100",
    }
    assert {spec.coin for spec in specs} == {"any_player"}
    assert all("player_has_coin=" not in spec.url for spec in specs)


def test_hsguru_table_parser_preserves_game_count_for_local_filtering() -> None:
    from app.hsguru_meta_matrix import parse_meta_rows

    rows = parse_meta_rows(HSGURU_TABLE)

    assert rows == [
        {
            "archetype": "Big Shaman",
            "winrate": 55.4,
            "popularity": 1.7,
            "games": 5321,
            "turns": 8.2,
            "duration_minutes": 7.6,
            "climbing_speed": 2.4,
        },
        {
            "archetype": "Quest Mage",
            "winrate": 51.1,
            "popularity": 0.8,
            "games": 742,
            "turns": 9.1,
            "duration_minutes": 8.0,
            "climbing_speed": 0.4,
        },
    ]


def test_hsguru_table_parser_maps_statistics_by_header_not_column_position() -> None:
    from app.hsguru_meta_matrix import parse_meta_rows

    reordered = """
    <table><thead><tr>
      <th>Popularity</th><th>Archetype</th><th>Climbing Speed</th>
      <th>Duration</th><th>Winrate↓</th><th>Turns</th>
    </tr></thead><tbody><tr>
      <td>1.7% (5,321)</td><td>Big Shaman</td><td>+2.4⭐/h</td>
      <td>7.6 min</td><td>55.4</td><td>8.2</td>
    </tr></tbody></table>
    """

    assert parse_meta_rows(reordered) == [{
        "archetype": "Big Shaman",
        "winrate": 55.4,
        "popularity": 1.7,
        "games": 5321,
        "turns": 8.2,
        "duration_minutes": 7.6,
        "climbing_speed": 2.4,
    }]


def test_hsguru_table_parser_rejects_incomplete_statistics() -> None:
    from app.hsguru_meta_matrix import parse_meta_rows

    broken = HSGURU_TABLE.replace("<td>55.4%</td>", "<td>—</td>", 1)

    with pytest.raises(ValueError, match="invalid statistics"):
        parse_meta_rows(broken)


def test_hsguru_table_parser_merges_same_identity_duplicate_archetypes() -> None:
    from app.hsguru_meta_matrix import parse_meta_table

    duplicated = """
    <table><thead><tr>
      <th>Archetype</th><th>Winrate</th><th>Popularity</th>
      <th>Turns</th><th>Duration</th><th>Climbing Speed</th>
    </tr></thead><tbody>
      <tr>
        <td class="decklist-info deathknight"><a href="/archetype/Thal%27ena%20DK?period=past_week&amp;rank=all">Thal'ena DK</a></td>
        <td>41.1</td><td>0.1% (3378)</td><td>8.8</td><td>8.8</td><td>-1.22⭐/h</td>
      </tr>
      <tr>
        <td class="decklist-info deathknight"><a href="https://www.hsguru.com/archetype/Thal%27ena%20DK?rank=all&amp;period=past_week">Thal'ena DK</a></td>
        <td>26.9</td><td>0.0% (145)</td><td>9.1</td><td>8.6</td><td>-3.21⭐/h</td>
      </tr>
    </tbody></table>
    """

    parsed = parse_meta_table(duplicated)

    assert len(parsed.rows) == 1
    assert parsed.rows[0] == {
        "archetype": "Thal'ena DK",
        "winrate": pytest.approx(40.515555),
        "popularity": pytest.approx(0.1),
        "games": 3523,
        "turns": pytest.approx(8.812347),
        "duration_minutes": pytest.approx(8.791768),
        "climbing_speed": pytest.approx(-1.301904),
    }
    assert parsed.duplicate_rows_merged == 1
    assert parsed.duplicate_groups == [{
        "archetype": "Thal'ena DK",
        "rows": 2,
        "games": 3523,
        "class": "deathknight",
        "source_url": (
            "https://www.hsguru.com/archetype/Thal%27ena%20DK"
            "?period=past_week&rank=all"
        ),
    }]


def test_hsguru_table_parser_rejects_same_name_with_different_identity() -> None:
    from app.hsguru_meta_matrix import HSGuruMetaDataError, parse_meta_rows

    ambiguous = """
    <table><thead><tr>
      <th>Archetype</th><th>Winrate</th><th>Popularity</th>
      <th>Turns</th><th>Duration</th><th>Climbing Speed</th>
    </tr></thead><tbody>
      <tr>
        <td class="decklist-info deathknight"><a href="/archetype/thalena-one">Thal'ena DK</a></td>
        <td>41.1</td><td>0.1% (3378)</td><td>8.8</td><td>8.8</td><td>-1.22</td>
      </tr>
      <tr>
        <td class="decklist-info deathknight"><a href="/archetype/thalena-two">Thal'ena DK</a></td>
        <td>26.9</td><td>0.0% (145)</td><td>9.1</td><td>8.6</td><td>-3.21</td>
      </tr>
    </tbody></table>
    """

    with pytest.raises(HSGuruMetaDataError, match="conflicting identities.*Thal'ena DK"):
        parse_meta_rows(ambiguous)


def test_hsguru_table_parser_distinguishes_missing_from_empty_table() -> None:
    from app.hsguru_meta_matrix import HSGuruMetaSchemaError, parse_meta_rows

    with pytest.raises(HSGuruMetaSchemaError, match="meta table was not found"):
        parse_meta_rows("<html><body><p>Cloudflare challenge</p></body></html>")

    empty = """
    <table><thead><tr>
      <th>Archetype</th><th>Winrate</th><th>Popularity</th>
      <th>Turns</th><th>Duration</th><th>Climbing Speed</th>
    </tr></thead><tbody></tbody></table>
    """
    assert parse_meta_rows(empty) == []


def test_current_archetypes_reuse_cached_deck_catalog_without_scraping() -> None:
    from app.hsguru_meta_matrix import enrich_current_rows_with_cached_decks

    rows = [{
        "format": "wild",
        "archetype": "Thief Priest",
        "games": 31959,
        "decks": [],
    }]
    builds = [
        {
            "archetype": "Thief Priest",
            "format": "Wild",
            "deck_code": "AAECAa0GAValidDeckCodeOne",
            "games": 250,
            "win_rate": 58.1,
            "url": "https://www.hsguru.com/deck/1",
        },
        {
            "archetype": "Thief Priest",
            "format": "Wild",
            "deck_code": "AAECAa0GAValidDeckCodeTwo",
            "games": 900,
            "win_rate": 61.2,
            "url": "https://www.hsguru.com/deck/2",
        },
    ]

    with patch(
        "app.hsguru_meta_matrix.cached_hsguru_catalog_decks",
        return_value=builds,
    ):
        enrich_current_rows_with_cached_decks(rows)

    assert rows[0]["has_decks"] is True
    assert rows[0]["deck_count"] == 2
    assert [deck["games"] for deck in rows[0]["decks"]] == [900, 250]
    assert all(deck["sample_rank"] == "all" for deck in rows[0]["decks"])
    assert all(deck["sample_period"] == "past_30_days" for deck in rows[0]["decks"])


def test_deck_catalog_refresh_can_rejoin_builds_without_refetching_meta() -> None:
    from app.hsguru_meta_matrix import refresh_current_catalog_deck_join

    dataset = {
        "source_id": "hsguru_meta_matrix",
        "data": {
            "structured": {
                "schema_version": 6,
                "current_catalog": {
                    "archetypes": [{
                        "format": "standard",
                        "archetype": "Face Hunter",
                        "games": 1000,
                        "decks": [],
                    }]
                },
            }
        },
    }

    def attach(rows, _cached):
        rows[0]["decks"] = [{"deck_code": "AAECAValidDeckCode"}]
        rows[0]["deck_count"] = 1
        rows[0]["has_decks"] = True

    with (
        patch("app.hsguru_meta_matrix.load_dataset", return_value=dataset),
        patch(
            "app.hsguru_meta_matrix.enrich_current_rows_with_cached_decks",
            side_effect=attach,
        ),
        patch("app.hsguru_meta_matrix.save_dataset") as save_dataset,
    ):
        result = refresh_current_catalog_deck_join()

    assert result["with_decks"] == 1
    assert result["decks"] == 1
    assert result["coverage"]["standard"]["games"] == 1000
    assert dataset["data"]["structured"]["schema_version"] == 7
    save_dataset.assert_called_once_with("hsguru_meta_matrix", dataset)


def test_refresh_publishes_one_unified_dataset_after_108_firecrawl_pages() -> None:
    from app.firecrawl_backend import FirecrawlScrape
    from app.hsguru_meta_matrix import refresh_hsguru_meta_matrix

    calls: list[str] = []

    async def scrape(spec):
        calls.append(spec.url)
        return FirecrawlScrape(
            html=HSGURU_TABLE,
            markdown="",
            screenshot=None,
            metadata={"creditsUsed": 1},
            status_code=200,
            final_url=spec.url,
        )

    async def scrape_current(format_name, period):
        return [
            {
                "format": format_name,
                "format_id": 2 if format_name == "standard" else 1,
                "archetype": "Quest Mage",
                "games": 742,
                "winrate": 51.1,
                "popularity_pct": 0.8,
                "avg_turns": 9.1,
                "avg_duration_minutes": 8.0,
                "climbing_speed_stars_per_hour": 0.4,
                "period": period,
                "rank": "all",
                "decks": [],
            }
        ], {
            "format": format_name,
            "backend": "firecrawl",
            "request_credits": 1,
            "rows": 1,
        }

    async def discover_patch(_cached):
        return "patch_36.0.3", None

    with (
        patch("app.hsguru_meta_matrix.load_dataset", return_value=None),
        patch(
            "app.hsguru_meta_matrix.resolve_current_patch_period",
            return_value="patch_36.0.3",
        ),
        patch("app.hsguru_meta_matrix._record_current_history"),
        patch("app.hsguru_meta_matrix.enrich_current_rows_with_cached_decks"),
        patch("app.hsguru_meta_matrix.save_dataset") as save_dataset,
        patch("app.hsguru_meta_matrix.save_status") as save_status,
    ):
        result = asyncio.run(
            refresh_hsguru_meta_matrix(
                concurrency=5,
                attempts=1,
                scrape=scrape,
                scrape_current=scrape_current,
                discover_patch=discover_patch,
            )
        )

    assert result["ok"] is True
    assert result["base_slices"] == 108
    assert result["logical_slices"] == 648
    assert result["firecrawl_credits_used"] == 110
    assert result["current_catalog_archetypes"] == 2
    assert len(calls) == 108
    save_dataset.assert_called_once()
    dataset = save_dataset.call_args.args[1]
    assert dataset["data"]["structured"]["dimensions"]["min_games"] == [
        100, 250, 500, 1000, 2500, 5000
    ]
    assert dataset["data"]["structured"]["dimensions"]["coins"] == ["any_player"]
    assert dataset["data"]["structured"]["current_catalog"]["criteria"] == {
        "period": "patch_36.0.3",
        "rank": "all",
        "minimum_games": 50,
        "formats": ["standard", "wild"],
    }
    save_status.assert_called_once()
    status = save_status.call_args.args[1]
    assert status["rows_total"] == 648
    assert status["base_slices"] == 108
    assert status["fresh_base_slices"] == 108
    assert status["cached_base_slices"] == 0


def test_matrix_slice_uses_shared_provider_cascade_once_in_exact_order() -> None:
    from app.firecrawl_backend import FirecrawlScrape
    from app.hsguru_meta_matrix import SliceSpec, _default_scrape

    spec = SliceSpec(
        "standard",
        "legend",
        "past_day",
        "any_player",
        "standard|legend|past_day|any_player",
        "https://www.hsguru.com/meta?format=2&rank=legend&period=past_day",
    )
    fallback_result = FirecrawlScrape(
        html=HSGURU_TABLE,
        markdown="",
        screenshot=None,
        metadata={"backend": "scrapfly", "scrapflyCreditsUsed": 3},
        status_code=200,
        final_url=spec.url,
    )
    provider_calls: list[str] = []

    def fail_scrape_do(*_args, **_kwargs):
        provider_calls.append("scrape_do")
        raise RuntimeError("Scrape.do unavailable")

    def fail_firecrawl(*_args, **_kwargs):
        provider_calls.append("firecrawl")
        raise RuntimeError("Firecrawl unavailable")

    def use_scrapfly(*_args, **_kwargs):
        provider_calls.append("scrapfly")
        return fallback_result

    with (
        patch("app.firecrawl_backend.scrape_do_token", return_value="configured"),
        patch("app.firecrawl_backend.scrapfly_configured", return_value=True),
        patch(
            "app.firecrawl_backend._scrape_via_scrape_do",
            side_effect=fail_scrape_do,
        ) as scrape_do,
        patch(
            "app.firecrawl_backend._scrape_via_firecrawl",
            side_effect=fail_firecrawl,
        ) as firecrawl,
        patch(
            "app.firecrawl_backend._scrape_via_scrapfly",
            side_effect=use_scrapfly,
        ) as scrapfly,
        patch(
            "app.hsguru_meta_matrix.scrape_url",
            new=AsyncMock(side_effect=RuntimeError("legacy direct call")),
            create=True,
        ) as direct_scrape_do,
        patch(
            "app.hsguru_meta_matrix.scrape_do_token",
            return_value="configured",
            create=True,
        ),
    ):
        result = asyncio.run(_default_scrape(spec))

    assert result.backend == "scrapfly"
    assert provider_calls == ["scrape_do", "firecrawl", "scrapfly"]
    assert scrape_do.call_count == 1
    assert firecrawl.call_count == 1
    assert scrapfly.call_count == 1
    direct_scrape_do.assert_not_awaited()


def test_current_page_does_not_repeat_paid_providers_after_shared_cascade_fails() -> (
    None
):
    from app.hsguru_meta_matrix import _scrape_current_page

    with (
        patch(
            "app.hsguru_meta_matrix.scrape_source_with_options",
            new=AsyncMock(side_effect=RuntimeError("all shared providers failed")),
        ) as cascade,
        patch(
            "app.hsguru_meta_matrix.scrape_url",
            new=AsyncMock(side_effect=RuntimeError("legacy direct retry")),
            create=True,
        ) as direct_scrape_do,
        patch(
            "app.hsguru_meta_matrix.scrape_do_token",
            return_value="configured",
            create=True,
        ),
        pytest.raises(RuntimeError, match="all shared providers failed"),
    ):
        asyncio.run(_scrape_current_page("wild", "patch_36.0.3"))

    cascade.assert_awaited_once()
    direct_scrape_do.assert_not_awaited()


def test_duplicate_matrix_refresh_is_skipped_before_heartbeat_or_storage(
    tmp_path,
) -> None:
    from app.hsguru_meta_matrix import SOURCE_ID, refresh_hsguru_meta_matrix
    from app.job_run import JobRunContext
    from app.resource_locks import ResourceLockSet

    lock_dir = tmp_path / ".locks"
    with (
        ResourceLockSet(
            [SOURCE_ID],
            lock_dir=lock_dir,
            metadata={"run_id": "existing-run"},
        ),
        patch("app.resource_locks.data_dir", return_value=tmp_path),
        patch.object(
            JobRunContext,
            "start",
            side_effect=AssertionError("heartbeat must not start"),
        ) as start_run,
        patch("app.hsguru_meta_matrix.save_dataset") as save_dataset,
        patch("app.hsguru_meta_matrix.save_status") as save_status,
    ):
        result = asyncio.run(refresh_hsguru_meta_matrix())

    assert result["ok"] is True
    assert result["published"] is False
    assert result["source_id"] == SOURCE_ID
    assert result["state"] == "locked"
    assert result["skipped"] is True
    assert result["reason"] == "resource_locked"
    assert result["locked_resource"] == SOURCE_ID
    assert result["owner"]["run_id"] == "existing-run"
    start_run.assert_not_called()
    save_dataset.assert_not_called()
    save_status.assert_not_called()


def test_refresh_does_not_retry_data_errors_and_counts_failed_parse_request() -> None:
    from app.firecrawl_backend import FirecrawlScrape
    from app.hsguru_meta_matrix import SliceSpec, refresh_hsguru_meta_matrix

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
    calls: list[str] = []

    async def scrape(spec):
        calls.append(spec.key)
        return FirecrawlScrape(
            html=conflicting if spec.rank == "all" else HSGURU_TABLE,
            markdown="",
            screenshot=None,
            metadata={"creditsUsed": 1},
            status_code=200,
            final_url=spec.url,
        )

    async def scrape_current(format_name, period):
        return [{
            "format": format_name,
            "archetype": f"Current {format_name}",
            "games": 100,
            "period": period,
            "rank": "all",
            "decks": [],
        }], {
            "format": format_name,
            "backend": "firecrawl",
            "request_credits": 1,
            "rows": 1,
        }

    async def discover_patch(_cached):
        return "patch_36.0.3", None

    with (
        patch("app.hsguru_meta_matrix.iter_slice_specs", return_value=specs),
        patch("app.hsguru_meta_matrix.load_dataset", return_value={}),
        patch("app.hsguru_meta_matrix.enrich_current_rows_with_cached_decks"),
        patch("app.hsguru_meta_matrix.save_dataset") as save_dataset,
        patch("app.hsguru_meta_matrix.save_status") as save_status,
        patch("app.hsguru_meta_matrix.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = asyncio.run(
            refresh_hsguru_meta_matrix(
                concurrency=2,
                attempts=3,
                scrape=scrape,
                scrape_current=scrape_current,
                discover_patch=discover_patch,
            )
        )

    assert result["ok"] is False
    assert result["state"] == "partial"
    assert result["job_run"]["progress"]["phase"] == "partial"
    assert calls.count("standard|all|past_week|any_player") == 1
    assert result["firecrawl_credits_used"] == 4
    save_dataset.assert_not_called()
    status = save_status.call_args.args[1]
    assert status["firecrawl_requests"] == 4
    assert "conflicting identities" in status["errors"][0]["error"]


def test_refresh_timeout_stops_new_slices_and_preserves_last_known_good() -> None:
    from datetime import timedelta

    from app.firecrawl_backend import FirecrawlScrape
    from app.hsguru_meta_matrix import SliceSpec, refresh_hsguru_meta_matrix
    from app.job_run import JobRunContext

    started_at = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)

    class MutableClock:
        def __init__(self) -> None:
            self.value = started_at

        def __call__(self) -> datetime:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += timedelta(seconds=seconds)

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
            "wild",
            "all",
            "past_week",
            "any_player",
            "wild|all|past_week|any_player",
            "https://www.hsguru.com/meta?format=1&rank=all&period=past_week&min_games=100",
        ),
    )
    cached = {
        "source_id": "hsguru_meta_matrix",
        "fetched_at": "2026-08-09T20:00:00+00:00",
        "data": {
            "structured": {
                "slices": [
                    {
                        "key": spec.key,
                        "format": spec.format,
                        "rank": spec.rank,
                        "period": spec.period,
                        "coin": spec.coin,
                        "fetched_at": "2026-08-09T20:00:00+00:00",
                        "rows": [{"archetype": "Cached Mage", "games": 500}],
                    }
                    for spec in specs
                ],
                "current_catalog": {
                    "criteria": {"period": "patch_36.0.3"},
                    "archetypes": [
                        {
                            "format": format_name,
                            "archetype": f"Cached {format_name}",
                            "games": 500,
                            "period": "patch_36.0.3",
                            "rank": "all",
                        }
                        for format_name in ("standard", "wild")
                    ],
                },
            }
        },
    }
    clock = MutableClock()
    heartbeat_snapshots: list[dict] = []

    class RecordingWriter:
        def write(self, snapshot):
            heartbeat_snapshots.append(snapshot)

    run = JobRunContext.start(
        run_id="matrix-timeout-run",
        timeout_seconds=1,
        total_slices=0,
        clock=clock,
        snapshot_writer=RecordingWriter(),
        heartbeat_interval_seconds=30,
    )
    slice_calls: list[str] = []
    current_calls: list[str] = []

    async def scrape(spec):
        slice_calls.append(spec.key)
        clock.advance(2)
        return FirecrawlScrape(
            html=HSGURU_TABLE,
            markdown="",
            screenshot=None,
            metadata={"creditsUsed": 1},
            status_code=200,
            final_url=spec.url,
        )

    async def scrape_current(format_name, period):
        current_calls.append(format_name)
        return [], {"format": format_name, "backend": "firecrawl", "rows": 0}

    async def discover_patch(_cached):
        return "patch_36.0.3", None

    with (
        patch("app.hsguru_meta_matrix.iter_slice_specs", return_value=specs),
        patch("app.hsguru_meta_matrix.load_dataset", return_value=cached),
        patch("app.hsguru_meta_matrix.enrich_current_rows_with_cached_decks"),
        patch("app.hsguru_meta_matrix._record_current_history") as record_history,
        patch("app.hsguru_meta_matrix.save_dataset") as save_dataset,
        patch("app.hsguru_meta_matrix.save_status") as save_status,
    ):
        result = asyncio.run(
            refresh_hsguru_meta_matrix(
                concurrency=1,
                attempts=1,
                scrape=scrape,
                scrape_current=scrape_current,
                discover_patch=discover_patch,
                run_context=run,
            )
        )

    assert slice_calls == [specs[0].key]
    assert current_calls == []
    save_dataset.assert_not_called()
    record_history.assert_not_called()
    assert result["ok"] is False
    assert result["published"] is False
    assert result["state"] == "timed_out"
    assert result["timed_out"] is True
    assert result["serving_cached_dataset"] is True
    assert result["job_run"] == {
        "run_id": "matrix-timeout-run",
        "started_at": "2026-08-10T20:00:00+00:00",
        "deadline_at": "2026-08-10T20:00:01+00:00",
        "heartbeat_at": "2026-08-10T20:00:02+00:00",
        "timed_out": True,
        "progress": {
            "phase": "timed_out",
            "total_slices": 4,
            "started_slices": 1,
            "completed_slices": 1,
            "succeeded_slices": 1,
            "failed_slices": 0,
            "skipped_slices": 3,
        },
    }
    status = save_status.call_args.args[1]
    assert status["state"] == "timed_out"
    assert status["timed_out"] is True
    assert status["published"] is False
    assert status["job_run"] == result["job_run"]
    assert len(heartbeat_snapshots) == 2
    assert heartbeat_snapshots[0]["progress"]["phase"] == "starting"
    assert heartbeat_snapshots[-1] == result["job_run"]


def test_last_current_slice_finishing_after_deadline_is_not_published() -> None:
    from datetime import timedelta

    from app.hsguru_meta_matrix import refresh_hsguru_meta_matrix
    from app.job_run import JobRunContext

    started_at = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)

    class MutableClock:
        def __init__(self) -> None:
            self.value = started_at

        def __call__(self) -> datetime:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += timedelta(seconds=seconds)

    clock = MutableClock()
    run = JobRunContext.start(
        run_id="late-current-slice-run",
        timeout_seconds=1,
        total_slices=0,
        clock=clock,
    )
    current_calls: list[str] = []

    async def scrape_current(format_name, period):
        current_calls.append(format_name)
        if format_name == "wild":
            clock.advance(2)
        return [
            {
                "format": format_name,
                "archetype": f"Current {format_name}",
                "games": 100,
                "period": period,
                "rank": "all",
                "decks": [],
            }
        ], {
            "format": format_name,
            "backend": "firecrawl",
            "request_credits": 1,
            "rows": 1,
        }

    async def discover_patch(_cached):
        return "patch_36.0.3", None

    with (
        patch("app.hsguru_meta_matrix.iter_slice_specs", return_value=()),
        patch("app.hsguru_meta_matrix.load_dataset", return_value=None),
        patch("app.hsguru_meta_matrix.enrich_current_rows_with_cached_decks"),
        patch("app.hsguru_meta_matrix._record_current_history") as record_history,
        patch("app.hsguru_meta_matrix.save_dataset") as save_dataset,
        patch("app.hsguru_meta_matrix.save_status") as save_status,
    ):
        result = asyncio.run(
            refresh_hsguru_meta_matrix(
                attempts=1,
                scrape_current=scrape_current,
                discover_patch=discover_patch,
                run_context=run,
            )
        )

    assert current_calls == ["standard", "wild"]
    assert result["state"] == "timed_out"
    assert result["timed_out"] is True
    assert result["published"] is False
    assert result["job_run"]["progress"]["completed_slices"] == 2
    save_dataset.assert_not_called()
    record_history.assert_not_called()
    assert save_status.call_args.args[1]["published"] is False


def test_missing_slice_can_be_carried_forward_from_last_stable_dataset() -> None:
    from app.hsguru_meta_matrix import SliceSpec, _carry_forward_missing_slices

    spec = SliceSpec(
        "standard",
        "all",
        "past_week",
        "any_player",
        "standard|all|past_week|any_player",
        "https://www.hsguru.com/meta?format=2&rank=all&period=past_week&min_games=100",
    )
    cached = {
        "fetched_at": "2026-07-25T10:00:03+00:00",
        "data": {
            "structured": {
                "slices": [{
                    "key": spec.key,
                    "format": "standard",
                    "rank": "all",
                    "period": "past_week",
                    "coin": "any_player",
                    "rows": [{"archetype": "Cached Mage", "games": 500}],
                }]
            }
        },
    }
    errors = [{
        "key": spec.key,
        "error": "HSGuruMetaDataError: conflicting identities",
    }]

    slices, carried = _carry_forward_missing_slices(
        specs=(spec,),
        fresh_slices=[],
        cached_dataset=cached,
        errors=errors,
    )

    assert carried == 1
    assert slices[0]["rows"] == [{"archetype": "Cached Mage", "games": 500}]
    assert slices[0]["fetched_at"] == "2026-07-25T10:00:03+00:00"
    assert slices[0]["quality"]["serving_cached_slice"] is True
    assert slices[0]["quality"]["last_refresh_error"] == (
        "HSGuruMetaDataError: conflicting identities"
    )


def test_failed_current_format_uses_same_period_cached_catalog() -> None:
    from app.hsguru_meta_matrix import _carry_forward_current_catalog

    cached = {
        "data": {
            "structured": {
                "current_catalog": {
                    "criteria": {"period": "patch_36.0.3"},
                    "archetypes": [
                        {
                            "format": "wild",
                            "archetype": "Cached Wild Deck",
                            "games": 321,
                            "period": "patch_36.0.3",
                            "rank": "all",
                        },
                        {
                            "format": "standard",
                            "archetype": "Old Standard Deck",
                            "games": 123,
                            "period": "patch_36.0.3",
                            "rank": "all",
                        },
                    ],
                }
            }
        }
    }

    rows, acquisitions, cached_formats = _carry_forward_current_catalog(
        current_period="patch_36.0.3",
        fresh_rows=[{
            "format": "standard",
            "archetype": "Fresh Standard Deck",
            "games": 999,
            "period": "patch_36.0.3",
            "rank": "all",
        }],
        acquisitions=[{"format": "standard", "backend": "scrape_do"}],
        cached_dataset=cached,
    )

    assert [row["archetype"] for row in rows] == [
        "Fresh Standard Deck",
        "Cached Wild Deck",
    ]
    assert cached_formats == ["wild"]
    assert acquisitions[-1]["backend"] == "cache"
    assert acquisitions[-1]["serving_cached_catalog"] is True


def test_runtime_periods_replace_previous_patch_with_discovered_patch() -> None:
    from app.hsguru_meta_matrix import matrix_periods, patch_periods_from_html

    assert matrix_periods("patch_36.0.4") == (
        "past_day",
        "past_3_days",
        "past_week",
        "past_2_weeks",
        "patch_36.0.4",
        "violet_hold",
    )
    html = """
    <a href="/meta?format=2&amp;period=patch_36.0.3&amp;rank=all">36.0.3</a>
    <a href="/meta?format=2&amp;period=patch_36.0.10&amp;rank=all">36.0.10</a>
    <a href="/meta?format=2&amp;period=violet_hold&amp;rank=all">Violet Hold</a>
    """
    assert patch_periods_from_html(html) == (
        "patch_36.0.3",
        "patch_36.0.10",
    )


def test_v1_hsguru_meta_filters_unified_dataset_by_min_games() -> None:
    fetched_at = datetime.now(UTC).isoformat()
    dataset = {
        "source_id": "hsguru_meta_matrix",
        "fetched_at": fetched_at,
        "backend": "firecrawl",
        "data": {
            "structured": {
                "type": "hsguru_meta_matrix",
                "schema_version": 1,
                "dimensions": {
                    "formats": ["standard", "wild"],
                    "ranks": [
                        "all",
                        "diamond",
                        "diamond_4to1",
                        "diamond_to_legend",
                        "legend",
                        "top_5k",
                        "top_legend",
                        "top_500",
                        "top_100",
                    ],
                    "periods": ["past_day", "past_3_days", "past_week", "past_2_weeks"],
                    "coins": ["any_player"],
                    "min_games": [100, 250, 500, 1000, 2500, 5000],
                },
                "slices": [
                    {
                        "key": "standard|legend|past_day|any_player",
                        "format": "standard",
                        "rank": "legend",
                        "period": "past_day",
                        "coin": "any_player",
                        "source_url": "https://www.hsguru.com/meta?format=2&rank=legend&period=past_day&min_games=100",
                        "rows": [
                            {"archetype": "Big Shaman", "games": 5321, "winrate": 55.4},
                            {"archetype": "Quest Mage", "games": 742, "winrate": 51.1},
                        ],
                    }
                ],
            }
        },
    }

    with patch("app.routers.hsguru_meta.load_dataset", return_value=dataset):
        response = client.get(
            "/v1/hsguru/meta?format=standard&rank=legend&period=past_day&coin=any_player&min_games=2500"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"] == [
        {"archetype": "Big Shaman", "games": 5321, "winrate": 55.4}
    ]
    assert body["data"]["min_games"] == 2500
    assert body["meta"]["source_id"] == "hsguru_meta_matrix"
    assert body["meta"]["count"] == 1
    assert body["meta"]["available_periods"] == [
        "past_day",
        "past_3_days",
        "past_week",
        "past_2_weeks",
    ]
    assert body["meta"]["current_patch_period"] is None


def test_v1_hsguru_archetypes_returns_current_patch_catalog() -> None:
    fetched_at = datetime.now(UTC).isoformat()
    dataset = {
        "source_id": "hsguru_meta_matrix",
        "fetched_at": fetched_at,
        "data": {
            "structured": {
                "current_catalog": {
                    "criteria": {
                        "period": "patch_36.0.3",
                        "rank": "all",
                        "minimum_games": 50,
                    },
                    "coverage": {
                        "standard": {"archetypes": 1},
                        "wild": {"archetypes": 1},
                    },
                    "archetypes": [
                        {
                            "format": "standard",
                            "archetype": "Quest Mage",
                            "games": 742,
                            "winrate": 51.1,
                            "popularity_pct": 0.8,
                            "decks": [],
                        },
                        {
                            "format": "wild",
                            "archetype": "Pirate Rogue",
                            "games": 98,
                            "winrate": 50.0,
                            "popularity_pct": 0.2,
                            "decks": [],
                        },
                    ],
                }
            }
        },
    }

    with patch("app.routers.hsguru_meta.load_dataset", return_value=dataset):
        response = client.get(
            "/v1/hsguru/archetypes?format=wild&min_games=50"
        )

    assert response.status_code == 200
    body = response.json()
    assert [row["archetype"] for row in body["data"]] == ["Pirate Rogue"]
    assert body["criteria"]["period"] == "patch_36.0.3"


def test_v1_hsguru_archetypes_can_filter_to_rows_with_builds() -> None:
    fetched_at = datetime.now(UTC).isoformat()
    dataset = {
        "source_id": "hsguru_meta_matrix",
        "fetched_at": fetched_at,
        "data": {
            "structured": {
                "current_catalog": {
                    "criteria": {"period": "patch_36.0.3", "minimum_games": 50},
                    "archetypes": [
                        {
                            "format": "wild",
                            "archetype": "Thief Priest",
                            "games": 31959,
                            "decks": [{"deck_code": "AAECAa0GAValidDeckCode"}],
                        },
                        {
                            "format": "wild",
                            "archetype": "Unused Test Archetype",
                            "games": 51,
                            "decks": [],
                        },
                    ],
                }
            }
        },
    }

    with patch("app.routers.hsguru_meta.load_dataset", return_value=dataset):
        response = client.get(
            "/v1/hsguru/archetypes?format=wild&min_games=50&has_decks=true"
        )

    assert response.status_code == 200
    assert [row["archetype"] for row in response.json()["data"]] == ["Thief Priest"]


def test_v1_hsguru_meta_rejects_removed_min_games_value() -> None:
    response = client.get(
        "/v1/hsguru/meta?format=standard&rank=legend&period=past_day&coin=any_player&min_games=7500"
    )

    assert response.status_code == 422


@pytest.mark.parametrize("coin", ["going_first", "on_coin"])
def test_v1_hsguru_meta_rejects_removed_coin_modes(coin: str) -> None:
    response = client.get(
        f"/v1/hsguru/meta?format=standard&rank=legend&period=past_day&coin={coin}&min_games=100"
    )

    assert response.status_code == 422


def test_v1_hsguru_meta_accepts_all_ranks_and_any_player() -> None:
    fetched_at = datetime.now(UTC).isoformat()
    dataset = {
        "source_id": "hsguru_meta_matrix",
        "fetched_at": fetched_at,
        "data": {"structured": {"slices": [{
            "key": "standard|all|past_day|any_player",
            "source_url": "https://www.hsguru.com/meta?format=2&rank=all&period=past_day&min_games=100",
            "rows": [{"archetype": "Big Shaman", "games": 5321, "winrate": 55.4}],
        }]}},
    }

    with patch("app.routers.hsguru_meta.load_dataset", return_value=dataset):
        response = client.get(
            "/v1/hsguru/meta?format=standard&rank=all&period=past_day&coin=any_player&min_games=100"
        )

    assert response.status_code == 200
    assert response.json()["data"]["rank"] == "all"
    assert response.json()["data"]["coin"] == "any_player"


@pytest.mark.parametrize("rank", ["diamond", "diamond_to_legend"])
def test_v1_hsguru_meta_accepts_extended_diamond_ranks(rank: str) -> None:
    fetched_at = datetime.now(UTC).isoformat()
    dataset = {
        "source_id": "hsguru_meta_matrix",
        "fetched_at": fetched_at,
        "data": {"structured": {"slices": [{
            "key": f"standard|{rank}|past_day|any_player",
            "source_url": (
                "https://www.hsguru.com/meta?"
                f"format=2&rank={rank}&period=past_day&min_games=100"
            ),
            "rows": [{"archetype": "Big Shaman", "games": 5321, "winrate": 55.4}],
        }]}},
    }

    with patch("app.routers.hsguru_meta.load_dataset", return_value=dataset):
        response = client.get(
            f"/v1/hsguru/meta?format=standard&rank={rank}"
            "&period=past_day&coin=any_player&min_games=100"
        )

    assert response.status_code == 200
    assert response.json()["data"]["rank"] == rank


@pytest.mark.parametrize("period", ["patch_36.0.3", "violet_hold"])
def test_v1_hsguru_meta_accepts_extended_periods(period: str) -> None:
    fetched_at = datetime.now(UTC).isoformat()
    dataset = {
        "source_id": "hsguru_meta_matrix",
        "fetched_at": fetched_at,
        "data": {"structured": {
            "dimensions": {"periods": ["past_day", "patch_36.0.3", "violet_hold"]},
            "patch_discovery": {"selected_period": "patch_36.0.3"},
            "slices": [{
                "key": f"standard|legend|{period}|any_player",
                "source_url": (
                    "https://www.hsguru.com/meta?"
                    f"format=2&rank=legend&period={period}&min_games=100"
                ),
                "rows": [{"archetype": "Quest Mage", "games": 742, "winrate": 51.1}],
            }],
        }},
    }

    with patch("app.routers.hsguru_meta.load_dataset", return_value=dataset):
        response = client.get(
            f"/v1/hsguru/meta?format=standard&rank=legend&period={period}"
            "&coin=any_player&min_games=100"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["period"] == period
    assert body["meta"]["available_periods"] == [
        "past_day",
        "patch_36.0.3",
        "violet_hold",
    ]
    assert body["meta"]["current_patch_period"] == "patch_36.0.3"


def test_v1_hsguru_meta_rejects_period_outside_published_dimensions() -> None:
    fetched_at = datetime.now(UTC).isoformat()
    dataset = {
        "source_id": "hsguru_meta_matrix",
        "fetched_at": fetched_at,
        "data": {"structured": {
            "dimensions": {"periods": ["past_day", "patch_36.0.3"]},
            "slices": [],
        }},
    }

    with patch("app.routers.hsguru_meta.load_dataset", return_value=dataset):
        response = client.get(
            "/v1/hsguru/meta?format=standard&rank=legend&period=patch_99.0.0"
            "&coin=any_player&min_games=100"
        )

    assert response.status_code == 422
    assert response.json()["detail"]["allowed_periods"] == [
        "past_day",
        "patch_36.0.3",
    ]
