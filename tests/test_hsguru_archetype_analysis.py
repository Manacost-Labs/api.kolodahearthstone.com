from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from app.hsguru_archetype_analysis import (
    ANALYSIS_TIMEOUT_MS,
    ANALYSIS_WAIT_MS,
    CHECKPOINT_SCHEMA_VERSION,
    _active_archetypes,
    _fetch_html,
    _load_refresh_checkpoint,
    _target_signature,
    analysis_urls,
    parse_card_stats_games,
    parse_card_stats_html,
    parse_class_matchups_html,
    refresh_hsguru_archetype_analysis,
)

MATCHUPS_HTML = """
<table>
  <thead><tr><th>Class</th><th>Winrate</th><th>Total Games</th></tr></thead>
  <tbody>
    <tr><td>Death Knight</td><td>59.5</td><td>158 (2.7%)</td></tr>
    <tr><td>Demon Hunter</td><td>46.4%</td><td>1,256 (21.6%)</td></tr>
    <tr><td>Total</td><td>37.8</td><td>5,792</td></tr>
  </tbody>
</table>
"""

CARD_STATS_HTML = """
<table>
  <thead>
    <tr>
      <th>Card</th>
      <th>Mulligan Impact</th><th>Mulligan Count</th>
      <th>Drawn Impact</th><th>Drawn Count</th>
      <th>Kept Impact</th><th>Kept Count</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>
        <a href="/card/123/TOY_330" data-dbf-id="123">
          <img alt="Гость из Бездны" src="https://art.hearthstonejson.com/v1/tiles/TOY_330.webp">
        </a>
      </td>
      <td>+4.8%</td><td>12,345</td>
      <td>-1.2%</td><td>9,876</td>
      <td>+6.1%</td><td>7,654</td>
    </tr>
  </tbody>
</table>
"""

CARD_STATS_LIVE_CELL_HTML = """
<table>
  <thead>
    <tr>
      <th>Card</th>
      <th>Mulligan Impact</th><th>Mulligan Count</th>
      <th>Drawn Impact</th><th>Drawn Count</th>
      <th>Kept Impact</th><th>Kept Count</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>
        <a href="https://www.hsguru.com/card/126252">
          <span class="card-number">4</span>
          <div class="card-name">
            <span style="font-size: 0"># ↑x (4)</span>
            Tower of Ghouls
          </div>
          <span class="card-number">↑</span>
        </a>
      </td>
      <td>4.6</td><td>606</td>
      <td>1.7</td><td>2,137</td>
      <td>4.8</td><td>399</td>
    </tr>
  </tbody>
</table>
"""

CARD_STATS_SPARSE_HTML = (
    '<span class="tw-font-mono">Games: 1,234</span>'
    + CARD_STATS_HTML.replace("12,345", "12").replace("9,876", "20")
)


class HSGuruArchetypeAnalysisTest(unittest.TestCase):
    def test_checkpoint_loader_uses_the_requested_recovery_ttl(self) -> None:
        now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
        targets = [{"format": "standard", "archetype": "Checkpoint Mage"}]
        checkpoint = {
            "state": "in_progress",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "rank": "legend",
            "period": "past_week",
            "target_signature": _target_signature(targets),
            "targets_total": 1,
            "targets": targets,
            "started_at": (now - timedelta(hours=3)).isoformat(),
        }
        with patch(
            "app.hsguru_archetype_analysis.load_baseline",
            return_value=checkpoint,
        ):
            self.assertIsNone(
                _load_refresh_checkpoint(
                    target_signature=_target_signature(targets),
                    now=now,
                )
            )
            self.assertEqual(
                _load_refresh_checkpoint(
                    target_signature=_target_signature(targets),
                    now=now,
                    max_age=timedelta(hours=12),
                ),
                checkpoint,
            )

    def test_checkpoint_loader_uses_saved_at_with_an_absolute_age_limit(
        self,
    ) -> None:
        now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
        targets = [{"format": "standard", "archetype": "Long Recovery Mage"}]
        checkpoint = {
            "state": "in_progress",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "rank": "legend",
            "period": "past_week",
            "target_signature": _target_signature(targets),
            "targets_total": 1,
            "targets": targets,
            "started_at": (now - timedelta(hours=13)).isoformat(),
            "saved_at": (now - timedelta(hours=1)).isoformat(),
        }
        expired_checkpoint = {
            **checkpoint,
            "started_at": (now - timedelta(hours=25)).isoformat(),
        }

        with patch(
            "app.hsguru_archetype_analysis.load_baseline",
            side_effect=[checkpoint, expired_checkpoint],
        ):
            self.assertEqual(
                _load_refresh_checkpoint(
                    target_signature=_target_signature(targets),
                    now=now,
                    max_age=timedelta(hours=12),
                    absolute_max_age=timedelta(hours=24),
                ),
                checkpoint,
            )
            self.assertIsNone(
                _load_refresh_checkpoint(
                    target_signature=_target_signature(targets),
                    now=now,
                    max_age=timedelta(hours=12),
                    absolute_max_age=timedelta(hours=24),
                )
            )

    def test_checkpoint_loader_rejects_legacy_or_corrupt_target_lists(self) -> None:
        now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
        targets = [{"format": "standard", "archetype": "Checkpoint Mage"}]
        valid = {
            "state": "in_progress",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "rank": "legend",
            "period": "past_week",
            "target_signature": _target_signature(targets),
            "targets_total": 1,
            "targets": targets,
            "started_at": (now - timedelta(hours=1)).isoformat(),
        }
        invalid_checkpoints = {
            "legacy_v2": {**valid, "schema_version": 2, "targets": None},
            "missing_targets": {
                key: value for key, value in valid.items() if key != "targets"
            },
            "missing_descriptor_field": {
                **valid,
                "targets": [{"format": "standard"}],
            },
            "invalid_format": {
                **valid,
                "targets": [{"format": "arena", "archetype": "Checkpoint Mage"}],
            },
            "duplicate_descriptor": {
                **valid,
                "targets_total": 2,
                "targets": [*targets, *targets],
            },
            "signature_mismatch": {**valid, "target_signature": "not-the-targets"},
        }

        for label, checkpoint in invalid_checkpoints.items():
            with self.subTest(label=label), patch(
                "app.hsguru_archetype_analysis.load_baseline",
                return_value=checkpoint,
            ):
                self.assertIsNone(
                    _load_refresh_checkpoint(target_signature=None, now=now)
                )

    def test_fetch_html_falls_back_to_firecrawl_then_brightdata(self) -> None:
        from app import firecrawl_backend

        provider_calls: list[str] = []

        def fail_provider(name: str):
            def fail(*_args: object, **_kwargs: object):
                provider_calls.append(name)
                raise RuntimeError(f"{name} unavailable")

            return fail

        def brightdata_success(*_args: object, **_kwargs: object):
            provider_calls.append("brightdata")
            return SimpleNamespace(
                html=MATCHUPS_HTML,
                status_code=200,
                final_url="https://www.hsguru.com/archetype/example",
                billable_requests=1,
                request_id="test-request",
                rendered=False,
                budget_remaining=99,
            )

        with (
            patch(
                "app.hsguru_archetype_analysis._firecrawl_headers",
                return_value={"Cookie": "session=test-only"},
            ),
            patch.object(
                firecrawl_backend, "scrape_do_token", return_value="configured"
            ),
            patch.object(
                firecrawl_backend,
                "brightdata_configured_for_source",
                return_value=True,
            ),
            patch.object(firecrawl_backend, "scrapfly_configured", return_value=True),
            patch.object(
                firecrawl_backend,
                "_scrape_via_scrape_do",
                side_effect=fail_provider("scrape_do"),
            ),
            patch.object(
                firecrawl_backend,
                "_scrape_via_firecrawl",
                side_effect=fail_provider("firecrawl"),
            ),
            patch.object(
                firecrawl_backend,
                "brightdata_scrape_url_sync",
                side_effect=brightdata_success,
            ),
            patch.object(
                firecrawl_backend,
                "_scrape_via_scrapfly",
                side_effect=fail_provider("scrapfly"),
            ),
        ):
            html, metadata = asyncio.run(
                _fetch_html("https://www.hsguru.com/archetype/example")
            )

        self.assertEqual(provider_calls, ["scrape_do", "firecrawl", "brightdata"])
        self.assertEqual(html, MATCHUPS_HTML)
        self.assertEqual(metadata["backend"], "brightdata_web_unlocker")

    def test_fetch_html_uses_ssr_wait_timeout_and_schema_validator(self) -> None:
        html = '<span class="tw-font-mono">Games: 0</span>' + CARD_STATS_HTML
        result = SimpleNamespace(
            html=html,
            backend="scrape_do_super",
            request_credits=25,
            final_url="https://www.hsguru.com/card-stats",
        )
        scrape = AsyncMock(return_value=result)
        with patch(
            "app.hsguru_archetype_analysis.scrape_source_with_options",
            new=scrape,
        ):
            asyncio.run(_fetch_html("https://www.hsguru.com/card-stats?format=2"))

        options = scrape.await_args.kwargs
        self.assertEqual(options["wait_ms"], ANALYSIS_WAIT_MS)
        self.assertEqual(options["timeout_ms"], ANALYSIS_TIMEOUT_MS)
        self.assertEqual(options["max_age_ms"], 0)
        self.assertEqual(options["skip_providers"], {"scrapfly"})
        self.assertTrue(options["brightdata_anonymous_fallback"])
        self.assertTrue(options["accept_result"](result))
        self.assertFalse(
            options["accept_result"](
                SimpleNamespace(html="<html><body>challenge</body></html>")
            )
        )

    def test_parses_class_matchups_and_excludes_total(self) -> None:
        rows = parse_class_matchups_html(MATCHUPS_HTML)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["class_key"], "deathknight")
        self.assertEqual(rows[0]["games"], 158)
        self.assertEqual(rows[0]["share_pct"], 2.7)
        self.assertEqual(rows[1]["class_key"], "demonhunter")
        self.assertEqual(rows[1]["games"], 1256)
        self.assertEqual(rows[1]["winrate"], 46.4)

    def test_parses_card_impacts_counts_and_identity(self) -> None:
        rows = parse_card_stats_html(CARD_STATS_HTML)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["card_id"], "TOY_330")
        self.assertEqual(rows[0]["dbf_id"], 123)
        self.assertEqual(rows[0]["card_name"], "Гость из Бездны")
        self.assertEqual(rows[0]["mulligan_impact"], 4.8)
        self.assertEqual(rows[0]["mulligan_count"], 12345)
        self.assertEqual(rows[0]["drawn_impact"], -1.2)
        self.assertEqual(rows[0]["kept_count"], 7654)

    def test_cleans_live_card_cell_and_treats_numeric_path_as_dbf_id(self) -> None:
        rows = parse_card_stats_html(CARD_STATS_LIVE_CELL_HTML)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dbf_id"], 126252)
        self.assertEqual(rows[0]["card_name"], "Tower of Ghouls")
        self.assertNotIn("↑", rows[0]["card_name"])
        self.assertNotEqual(rows[0]["card_id"], "126252")

    def test_parses_card_stats_sample_games(self) -> None:
        self.assertEqual(parse_card_stats_games(CARD_STATS_SPARSE_HTML), 1234)
        self.assertIsNone(parse_card_stats_games(CARD_STATS_HTML))

    def test_builds_legend_past_week_urls_for_requested_format(self) -> None:
        urls = analysis_urls("Void Soul DH", "standard")
        card_filters = parse_qs(urlparse(urls["cards"]).query)

        self.assertIn("/archetype/Void%20Soul%20DH?", urls["matchups"])
        self.assertIn("format=2", urls["matchups"])
        self.assertIn("rank=legend", urls["matchups"])
        self.assertIn("period=past_week", urls["matchups"])
        self.assertIn("show_counts=yes", urls["cards"])
        self.assertEqual(card_filters["min_mull_count"], ["0"])
        self.assertEqual(card_filters["min_drawn_count"], ["0"])

    def test_active_archetypes_come_from_matching_legend_week_slices(self) -> None:
        dataset = {
            "data": {
                "structured": {
                    "slices": [
                        {
                            "key": "standard|legend|past_week|any_player",
                            "rows": [{"archetype": "Legend Mage"}],
                        },
                        {
                            "key": "wild|legend|past_week|any_player",
                            "rows": [{"archetype": "Wild Rogue"}],
                        },
                        {
                            "key": "standard|all|past_week|any_player",
                            "rows": [{"archetype": "Wrong Rank"}],
                        },
                    ],
                    "current_catalog": {
                        "archetypes": [
                            {
                                "format": "standard",
                                "archetype": "Wrong Period",
                                "has_decks": True,
                            }
                        ]
                    },
                }
            }
        }
        with patch(
            "app.hsguru_archetype_analysis.load_dataset",
            return_value=dataset,
        ):
            rows = _active_archetypes()

        self.assertEqual(
            rows,
            [
                {"format": "standard", "archetype": "Legend Mage"},
                {"format": "wild", "archetype": "Wild Rogue"},
            ],
        )

    def test_refresh_publishes_both_analysis_surfaces(self) -> None:
        async def fetch_html(url: str):
            if "/archetype/" in url:
                return MATCHUPS_HTML, {"backend": "firecrawl", "request_credits": 1}
            return CARD_STATS_HTML, {"backend": "scrape_do", "request_credits": 5}

        saved = {}
        with (
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis.save_dataset",
                side_effect=lambda source_id, payload: saved.update(
                    {"source_id": source_id, "payload": payload}
                ),
            ),
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            import asyncio

            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    archetypes=[
                        {"format": "standard", "archetype": "Void Soul DH"}
                    ],
                    fetch_html=fetch_html,
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["firecrawl_credits_used"], 1)
        self.assertEqual(result["scrape_do_credits_used"], 5)
        rows = saved["payload"]["data"]["structured"]["archetypes"]
        self.assertEqual(rows[0]["state"], "ok")
        self.assertEqual(len(rows[0]["class_matchups"]), 2)
        self.assertEqual(len(rows[0]["card_stats"]), 1)

    def test_refresh_treats_missing_upstream_card_table_as_available_gap(self) -> None:
        async def fetch_html(url: str):
            if "/archetype/" in url:
                return MATCHUPS_HTML, {"backend": "firecrawl", "request_credits": 1}
            return "<html><body>No card stats for this sample</body></html>", {
                "backend": "firecrawl",
                "request_credits": 1,
            }

        saved = {}
        with (
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis.save_dataset",
                side_effect=lambda source_id, payload: saved.update(
                    {"source_id": source_id, "payload": payload}
                ),
            ),
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            import asyncio

            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    archetypes=[
                        {"format": "wild", "archetype": "Small Sample Priest"}
                    ],
                    fetch_html=fetch_html,
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["unavailable"]), 1)
        self.assertEqual(result["firecrawl_credits_used"], 2)
        row = saved["payload"]["data"]["structured"]["archetypes"][0]
        self.assertEqual(row["state"], "partial")
        self.assertEqual(len(row["class_matchups"]), 2)
        self.assertEqual(row["card_stats"], [])
        negative_cache = saved["payload"]["data"]["structured"]["negative_cache"]
        self.assertEqual(len(negative_cache), 1)
        self.assertEqual(negative_cache[0]["kind"], "card_stats")
        self.assertEqual(
            negative_cache[0]["state"], "upstream_card_tallies_missing"
        )
        checked_at = datetime.fromisoformat(negative_cache[0]["checked_at"])
        retry_after = datetime.fromisoformat(negative_cache[0]["retry_after"])
        self.assertEqual(retry_after - checked_at, timedelta(hours=1))

    def test_refresh_treats_locally_filtered_rows_as_valid_sparse_data(self) -> None:
        async def fetch_html(url: str):
            html = MATCHUPS_HTML if "/archetype/" in url else CARD_STATS_SPARSE_HTML
            return html, {"backend": "scrape_do_super", "request_credits": 25}

        saved = {}
        with (
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis.save_dataset",
                side_effect=lambda _source_id, payload: saved.update(payload=payload),
            ),
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    archetypes=[
                        {"format": "standard", "archetype": "Sparse Mage"}
                    ],
                    fetch_html=fetch_html,
                )
            )

        row = saved["payload"]["data"]["structured"]["archetypes"][0]
        self.assertTrue(result["ok"])
        self.assertEqual(result["unavailable"], [])
        self.assertEqual(row["state"], "ok")
        self.assertEqual(row["card_stats_state"], "sparse_valid")
        self.assertEqual(row["card_stats"], [])
        self.assertEqual(
            saved["payload"]["data"]["structured"]["negative_cache"], []
        )

    def test_cached_negative_gap_is_not_published_as_a_fresh_complete_result(
        self,
    ) -> None:
        now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
        calls: list[str] = []

        async def fetch_html(url: str):
            calls.append(url)
            return MATCHUPS_HTML, {"backend": "firecrawl", "request_credits": 1}

        cached_gap = {
            "format": "wild",
            "archetype": "Small Sample Priest",
            "kind": "card_stats",
            "state": "upstream_unavailable",
            "cache_version": 2,
            "min_mull_count": 25,
            "min_drawn_count": 25,
            "checked_at": (now - timedelta(days=1)).isoformat(),
            "retry_after": (now + timedelta(days=6)).isoformat(),
            "reason": "HSGuru card_stats page has no data for the requested sample",
        }
        saved = {}
        with (
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={
                    ("wild", "small sample priest", "card_stats"): cached_gap
                },
            ),
            patch(
                "app.hsguru_archetype_analysis._utc_now",
                return_value=now,
            ),
            patch(
                "app.hsguru_archetype_analysis.save_dataset",
                side_effect=lambda source_id, payload: saved.update(
                    {"source_id": source_id, "payload": payload}
                ),
            ),
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            import asyncio

            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    archetypes=[
                        {"format": "wild", "archetype": "Small Sample Priest"}
                    ],
                    fetch_html=fetch_html,
                )
            )

        self.assertEqual(len(calls), 1)
        self.assertIn("/archetype/", calls[0])
        self.assertEqual(result["card_stats_requests_skipped"], 1)
        self.assertEqual(result["firecrawl_credits_used"], 1)
        self.assertFalse(result["published"])
        self.assertEqual(result["failure_reason_code"], "contract")
        self.assertEqual(saved, {})

    def test_refresh_retries_negative_cache_from_stricter_card_filters(self) -> None:
        now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
        calls: list[str] = []

        async def fetch_html(url: str):
            calls.append(url)
            if "/archetype/" in url:
                return MATCHUPS_HTML, {"backend": "firecrawl", "request_credits": 1}
            return CARD_STATS_HTML, {"backend": "firecrawl", "request_credits": 1}

        legacy_gap = {
            "format": "wild",
            "archetype": "Small Sample Priest",
            "kind": "card_stats",
            "state": "upstream_unavailable",
            "checked_at": (now - timedelta(days=1)).isoformat(),
            "retry_after": (now + timedelta(days=6)).isoformat(),
            "reason": "HSGuru card_stats page has no data for the requested sample",
        }
        saved = {}
        with (
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={
                    ("wild", "small sample priest", "card_stats"): legacy_gap
                },
            ),
            patch(
                "app.hsguru_archetype_analysis._utc_now",
                return_value=now,
            ),
            patch(
                "app.hsguru_archetype_analysis.save_dataset",
                side_effect=lambda source_id, payload: saved.update(
                    {"source_id": source_id, "payload": payload}
                ),
            ),
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            import asyncio

            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    archetypes=[
                        {"format": "wild", "archetype": "Small Sample Priest"}
                    ],
                    fetch_html=fetch_html,
                )
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["card_stats_requests_skipped"], 0)
        row = saved["payload"]["data"]["structured"]["archetypes"][0]
        self.assertEqual(len(row["card_stats"]), 1)
        self.assertEqual(
            saved["payload"]["data"]["structured"]["negative_cache"],
            [],
        )

    def test_refresh_retries_expired_negative_cache_and_clears_it_on_success(self) -> None:
        now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
        calls: list[str] = []

        async def fetch_html(url: str):
            calls.append(url)
            if "/archetype/" in url:
                return MATCHUPS_HTML, {"backend": "firecrawl", "request_credits": 1}
            return CARD_STATS_HTML, {"backend": "firecrawl", "request_credits": 1}

        cached_gap = {
            "format": "standard",
            "archetype": "Void Soul DH",
            "kind": "card_stats",
            "state": "upstream_unavailable",
            "checked_at": (now - timedelta(days=8)).isoformat(),
            "retry_after": (now - timedelta(days=1)).isoformat(),
            "reason": "HSGuru card_stats page has no data for the requested sample",
        }
        saved = {}
        with (
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={
                    ("standard", "void soul dh", "card_stats"): cached_gap
                },
            ),
            patch(
                "app.hsguru_archetype_analysis._utc_now",
                return_value=now,
            ),
            patch(
                "app.hsguru_archetype_analysis.save_dataset",
                side_effect=lambda source_id, payload: saved.update(
                    {"source_id": source_id, "payload": payload}
                ),
            ),
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            import asyncio

            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    archetypes=[
                        {"format": "standard", "archetype": "Void Soul DH"}
                    ],
                    fetch_html=fetch_html,
                )
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["card_stats_requests_skipped"], 0)
        self.assertEqual(
            saved["payload"]["data"]["structured"]["negative_cache"],
            [],
        )

    def test_provider_circuit_stops_fanout_and_preserves_canonical_dataset(self) -> None:
        calls = 0

        async def fetch_html(_url: str):
            nonlocal calls
            calls += 1
            raise RuntimeError("provider cascade failed")

        cached_row = {
            "format": "standard",
            "archetype": "Cached Mage",
            "state": "ok",
            "class_matchups": [{"class_key": "mage"}],
            "card_stats": [{"card_name": "Fireball"}],
        }
        saved_status = {}
        targets = [
            {"format": "standard", "archetype": f"Target {index}"}
            for index in range(10)
        ]
        with (
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={
                    ("standard", "cached mage"): cached_row
                },
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch("app.hsguru_archetype_analysis.save_dataset") as save_dataset,
            patch(
                "app.hsguru_archetype_analysis.save_status",
                side_effect=lambda _source_id, payload: saved_status.update(payload),
            ),
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    archetypes=targets,
                    concurrency=2,
                    fetch_html=fetch_html,
                )
            )

        self.assertFalse(result["ok"])
        self.assertFalse(result["published"])
        self.assertTrue(result["serving_cached_dataset"])
        self.assertTrue(result["provider_circuit_open"])
        self.assertLessEqual(calls, 6)
        self.assertGreater(result["targets_remaining"], 0)
        self.assertEqual(saved_status["errors_total"], len(result["errors"]))
        save_dataset.assert_not_called()

    def test_card_failures_open_circuit_despite_successful_matchups(self) -> None:
        matchup_calls = 0
        card_calls = 0

        async def fetch_html(url: str):
            nonlocal card_calls, matchup_calls
            if "/archetype/" in url:
                matchup_calls += 1
                return MATCHUPS_HTML, {
                    "backend": "scrape_do_super",
                    "request_credits": 25,
                }
            card_calls += 1
            raise RuntimeError("card provider path failed")

        targets = [
            {"format": "standard", "archetype": f"Mixed {index}"}
            for index in range(12)
        ]
        with (
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch("app.hsguru_archetype_analysis.save_dataset") as save_dataset,
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    archetypes=targets,
                    concurrency=2,
                    fetch_html=fetch_html,
                )
            )

        self.assertTrue(result["provider_circuit_open"])
        self.assertEqual(result["provider_circuit_kind"], "card_stats")
        self.assertLessEqual(card_calls, 4)
        self.assertLess(matchup_calls, len(targets))
        save_dataset.assert_not_called()

    def test_full_refresh_resumes_only_completed_sidecar_targets(self) -> None:
        targets = [
            {"format": "standard", "archetype": f"Resume {index}"}
            for index in range(6)
        ]
        first_calls = 0

        async def first_fetch(url: str):
            nonlocal first_calls
            first_calls += 1
            if first_calls > 4:
                raise RuntimeError("Scrape.do HTTP 502 ErrorCode 90 ROTATION_FAILED")
            if "/archetype/" in url:
                return MATCHUPS_HTML, {
                    "backend": "scrape_do_super",
                    "request_credits": 25,
                }
            return CARD_STATS_HTML, {
                "backend": "scrape_do_super",
                "request_credits": 25,
            }

        second_calls = 0

        async def second_fetch(url: str):
            nonlocal second_calls
            second_calls += 1
            if "/archetype/" in url:
                return MATCHUPS_HTML, {
                    "backend": "scrape_do_super",
                    "request_credits": 25,
                }
            return CARD_STATS_HTML, {
                "backend": "scrape_do_super",
                "request_credits": 25,
            }

        with (
            patch(
                "app.hsguru_archetype_analysis._active_archetypes",
                return_value=targets,
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch("app.hsguru_archetype_analysis.save_dataset") as save_dataset,
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            first = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    concurrency=1,
                    fetch_html=first_fetch,
                )
            )
            second = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    concurrency=1,
                    fetch_html=second_fetch,
                )
            )

        self.assertFalse(first["published"])
        self.assertEqual(first["failure_reason_code"], "upstream_5xx")
        self.assertEqual(first["targets_completed"], 2)
        self.assertTrue(second["published"])
        self.assertEqual(first["refresh_window_id"], second["refresh_window_id"])
        self.assertEqual(second["resumed_targets"], 2)
        self.assertEqual(second_calls, 8)
        save_dataset.assert_called_once()

    def test_checkpoint_combines_components_succeeded_on_separate_attempts(
        self,
    ) -> None:
        targets = [{"format": "wild", "archetype": "Split Success Priest"}]

        async def first_fetch(url: str):
            if "/archetype/" in url:
                raise RuntimeError("matchups temporarily unavailable")
            return CARD_STATS_HTML, {
                "backend": "scrape_do_super",
                "request_credits": 25,
            }

        async def second_fetch(url: str):
            if "/card-stats" in url:
                raise RuntimeError("card stats temporarily unavailable")
            return MATCHUPS_HTML, {
                "backend": "scrape_do_super",
                "request_credits": 25,
            }

        with (
            patch(
                "app.hsguru_archetype_analysis._active_archetypes",
                return_value=targets,
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch("app.hsguru_archetype_analysis.save_dataset") as save_dataset,
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            first = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    concurrency=1,
                    fetch_html=first_fetch,
                )
            )
            second = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    concurrency=1,
                    fetch_html=second_fetch,
                )
            )

        self.assertFalse(first["published"])
        self.assertEqual(first["targets_completed"], 0)
        self.assertTrue(second["published"])
        self.assertEqual(second["targets_completed"], 1)
        save_dataset.assert_called_once()

    def test_checkpoint_recovery_skips_when_no_incomplete_checkpoint_exists(
        self,
    ) -> None:
        fetch_html = AsyncMock(side_effect=AssertionError("upstream must not run"))
        with (
            patch(
                "app.hsguru_archetype_analysis._active_archetypes",
                return_value=[{"format": "standard", "archetype": "No Recovery Mage"}],
            ),
            patch(
                "app.hsguru_archetype_analysis._load_refresh_checkpoint",
                return_value=None,
            ),
            patch("app.hsguru_archetype_analysis.save_dataset") as save_dataset,
            patch("app.hsguru_archetype_analysis.save_status") as save_status,
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    checkpoint_recovery=True,
                    fetch_html=fetch_html,
                )
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "checkpoint_not_available")
        fetch_html.assert_not_awaited()
        save_dataset.assert_not_called()
        save_status.assert_not_called()

    def test_checkpoint_recovery_observes_checkpoint_cooldown(self) -> None:
        now = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)
        checkpoint = {
            "state": "in_progress",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "rank": "legend",
            "period": "past_week",
            "started_at": (now - timedelta(hours=1)).isoformat(),
            "saved_at": (now - timedelta(minutes=10)).isoformat(),
            "target_signature": _target_signature(
                [{"format": "standard", "archetype": "Cooldown Mage"}]
            ),
            "targets_total": 1,
            "targets": [{"format": "standard", "archetype": "Cooldown Mage"}],
            "completed": [],
        }
        fetch_html = AsyncMock(side_effect=AssertionError("upstream must not run"))
        with (
            patch(
                "app.hsguru_archetype_analysis._active_archetypes",
                return_value=[{"format": "standard", "archetype": "Cooldown Mage"}],
            ),
            patch(
                "app.hsguru_archetype_analysis._load_refresh_checkpoint",
                return_value=checkpoint,
            ),
            patch("app.hsguru_archetype_analysis._utc_now", return_value=now),
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    checkpoint_recovery=True,
                    fetch_html=fetch_html,
                )
            )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "checkpoint_recovery_cooldown")
        self.assertEqual(
            result["next_retry_at"],
            (now + timedelta(minutes=20)).isoformat(),
        )
        fetch_html.assert_not_awaited()

    def test_checkpoint_recovery_only_fetches_a_bounded_remaining_batch(self) -> None:
        now = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)
        targets = [
            {"format": "standard", "archetype": f"Recovery {index}"}
            for index in range(6)
        ]
        completed = targets[:2]
        checkpoint_rows = [
            {
                **target,
                "state": "ok",
                "class_matchups": [{"class_key": "mage"}],
                "card_stats": [{"card_id": "TEST"}],
            }
            for target in completed
        ]
        checkpoint = {
            "state": "in_progress",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "rank": "legend",
            "period": "past_week",
            "started_at": (now - timedelta(hours=3)).isoformat(),
            "saved_at": (now - timedelta(hours=1)).isoformat(),
            "target_signature": _target_signature(targets),
            "targets_total": len(targets),
            "targets": targets,
            "completed": completed,
            "rows": checkpoint_rows,
            "negative_cache": [],
            "unavailable": [],
            "acquisitions": [],
        }
        previous = {
            (target["format"], target["archetype"].casefold()): {
                **target,
                "state": "ok",
                "class_matchups": [{"class_key": "mage"}],
                "card_stats": [{"card_id": "TEST"}],
            }
            for target in targets
        }
        calls: list[str] = []

        async def fetch_html(url: str):
            calls.append(url)
            html = MATCHUPS_HTML if "/archetype/" in url else CARD_STATS_HTML
            return html, {"backend": "scrape_do_super", "request_credits": 25}

        with (
            patch(
                "app.hsguru_archetype_analysis._active_archetypes",
                return_value=targets,
            ),
            patch(
                "app.hsguru_archetype_analysis._load_refresh_checkpoint",
                return_value=checkpoint,
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value=previous,
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch("app.hsguru_archetype_analysis._utc_now", return_value=now),
            patch("app.hsguru_archetype_analysis.save_baseline"),
            patch("app.hsguru_archetype_analysis.save_dataset") as save_dataset,
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    checkpoint_recovery=True,
                    recovery_max_targets=2,
                    concurrency=1,
                    fetch_html=fetch_html,
                )
            )

        self.assertFalse(result["published"])
        self.assertTrue(result["recovery_batch_complete"])
        self.assertEqual(result["resumed_targets"], 2)
        self.assertEqual(result["targets_completed"], 4)
        self.assertEqual(result["targets_remaining"], 2)
        self.assertEqual(result["recovery_targets_deferred"], 2)
        self.assertEqual(len(calls), 4)
        save_dataset.assert_not_called()

    def test_checkpoint_recovery_has_a_per_run_provider_failure_budget(self) -> None:
        now = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)
        targets = [
            {"format": "standard", "archetype": f"Failing Recovery {index}"}
            for index in range(5)
        ]
        checkpoint = {
            "state": "in_progress",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "rank": "legend",
            "period": "past_week",
            "started_at": (now - timedelta(hours=2)).isoformat(),
            "saved_at": (now - timedelta(hours=1)).isoformat(),
            "target_signature": _target_signature(targets),
            "targets_total": len(targets),
            "targets": targets,
            "completed": [],
            "rows": [],
            "negative_cache": [],
            "unavailable": [],
            "acquisitions": [],
        }
        calls = 0

        async def fetch_html(_url: str):
            nonlocal calls
            calls += 1
            raise RuntimeError("HTTP 502")

        with (
            patch(
                "app.hsguru_archetype_analysis._active_archetypes",
                return_value=targets,
            ),
            patch(
                "app.hsguru_archetype_analysis._load_refresh_checkpoint",
                return_value=checkpoint,
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch("app.hsguru_archetype_analysis._utc_now", return_value=now),
            patch("app.hsguru_archetype_analysis.save_baseline"),
            patch("app.hsguru_archetype_analysis.save_dataset"),
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    checkpoint_recovery=True,
                    recovery_provider_failure_budget=1,
                    concurrency=2,
                    fetch_html=fetch_html,
                )
            )

        self.assertTrue(result["provider_circuit_open"])
        self.assertEqual(result["provider_failures_this_run"], 1)
        self.assertIn("failure budget exhausted", result["provider_circuit_reason"])
        self.assertEqual(calls, 1)

    def test_checkpoint_recovery_uses_saved_targets_when_live_matrix_drifts(self) -> None:
        now = datetime(2026, 8, 12, 6, 0, tzinfo=UTC)
        saved_targets = [
            {"format": "standard", "archetype": "Saved Mage"},
            {"format": "wild", "archetype": "Saved Priest"},
        ]
        checkpoint = {
            "state": "in_progress",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "rank": "legend",
            "period": "past_week",
            "started_at": (now - timedelta(hours=3)).isoformat(),
            "saved_at": (now - timedelta(hours=1)).isoformat(),
            "target_signature": _target_signature(saved_targets),
            "targets_total": len(saved_targets),
            "targets": saved_targets,
            "completed": [],
            "rows": [],
            "negative_cache": [],
            "unavailable": [],
            "acquisitions": [],
        }
        calls: list[str] = []

        async def fetch_html(url: str):
            calls.append(url)
            html = MATCHUPS_HTML if "/archetype/" in url else CARD_STATS_HTML
            return html, {"backend": "scrape_do_super", "request_credits": 25}

        with (
            patch(
                "app.hsguru_archetype_analysis._active_archetypes",
                side_effect=AssertionError("recovery must not read the live matrix"),
            ),
            patch(
                "app.hsguru_archetype_analysis.load_baseline",
                return_value=checkpoint,
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch("app.hsguru_archetype_analysis._utc_now", return_value=now),
            patch("app.hsguru_archetype_analysis.save_baseline"),
            patch("app.hsguru_archetype_analysis.save_dataset"),
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    checkpoint_recovery=True,
                    recovery_max_targets=1,
                    concurrency=1,
                    fetch_html=fetch_html,
                )
            )

        self.assertTrue(result["recovery_batch_complete"])
        self.assertEqual(result["targets"], 2)
        self.assertEqual(result["targets_completed"], 1)
        self.assertEqual(result["targets_remaining"], 1)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("Saved%20Mage" in url for url in calls))

    def test_fresh_run_persists_deterministic_checkpoint_targets(self) -> None:
        active_targets = [
            {"format": "wild", "archetype": "Wild Priest"},
            {"format": "standard", "archetype": "Standard Mage"},
        ]
        expected_targets = list(reversed(active_targets))
        saved_checkpoints: list[dict[str, object]] = []

        async def fetch_html(url: str):
            html = MATCHUPS_HTML if "/archetype/" in url else CARD_STATS_HTML
            return html, {"backend": "scrape_do_super", "request_credits": 25}

        with (
            patch(
                "app.hsguru_archetype_analysis._active_archetypes",
                return_value=active_targets,
            ),
            patch(
                "app.hsguru_archetype_analysis._load_refresh_checkpoint",
                return_value=None,
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis.save_baseline",
                side_effect=lambda _source, _label, payload: saved_checkpoints.append(
                    payload
                ),
            ),
            patch("app.hsguru_archetype_analysis.save_dataset"),
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    concurrency=1,
                    fetch_html=fetch_html,
                )
            )

        self.assertTrue(result["published"])
        self.assertGreaterEqual(len(saved_checkpoints), 2)
        for checkpoint in saved_checkpoints:
            self.assertEqual(checkpoint["schema_version"], CHECKPOINT_SCHEMA_VERSION)
            self.assertEqual(checkpoint["targets"], expected_targets)
            self.assertEqual(checkpoint["targets_total"], len(expected_targets))
            self.assertEqual(
                checkpoint["target_signature"],
                _target_signature(expected_targets),
            )

    def test_complete_refresh_rejects_a_candidate_that_fails_the_publish_gate(
        self,
    ) -> None:
        targets = [{"format": "standard", "archetype": "Contract Mage"}]

        async def fetch_html(url: str):
            html = MATCHUPS_HTML if "/archetype/" in url else CARD_STATS_HTML
            return html, {"backend": "scrape_do_super", "request_credits": 25}

        with (
            patch(
                "app.hsguru_archetype_analysis._active_archetypes",
                return_value=targets,
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis.validate_candidate_for_publish",
                return_value=SimpleNamespace(ok=False, reason="contract mismatch"),
                create=True,
            ),
            patch("app.hsguru_archetype_analysis.save_dataset") as save_dataset,
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(
                    concurrency=1,
                    fetch_html=fetch_html,
                )
            )

        self.assertFalse(result["published"])
        self.assertEqual(result["failure_reason_code"], "contract")
        save_dataset.assert_not_called()

    def test_expired_checkpoint_gap_is_retried_and_stale_unavailable_is_cleared(
        self,
    ) -> None:
        now = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
        target = {"format": "standard", "archetype": "Recovered Mage"}
        checkpoint_row = {
            **target,
            "state": "partial",
            "class_matchups": [{"class_key": "mage"}],
            "card_stats": [],
            "matchups_state": "complete",
            "card_stats_state": "source_no_data",
        }
        checkpoint = {
            "state": "in_progress",
            "schema_version": 2,
            "rank": "legend",
            "period": "past_week",
            "started_at": (now - timedelta(minutes=90)).isoformat(),
            "completed": [target],
            "rows": [checkpoint_row],
            "negative_cache": [
                {
                    **target,
                    "kind": "card_stats",
                    "state": "source_no_data",
                    "cache_version": 2,
                    "min_mull_count": 25,
                    "min_drawn_count": 25,
                    "checked_at": (now - timedelta(minutes=90)).isoformat(),
                    "retry_after": (now - timedelta(minutes=30)).isoformat(),
                }
            ],
            "unavailable": [
                {
                    **target,
                    "kind": "card_stats",
                    "state": "source_no_data",
                    "reason": "old sparse result",
                }
            ],
        }
        calls = 0

        async def fetch_html(url: str):
            nonlocal calls
            calls += 1
            if "/archetype/" in url:
                return MATCHUPS_HTML, {"backend": "scrape_do_super"}
            return CARD_STATS_HTML, {"backend": "scrape_do_super"}

        with (
            patch(
                "app.hsguru_archetype_analysis._active_archetypes",
                return_value=[target],
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_analysis",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._previous_negative_cache",
                return_value={},
            ),
            patch(
                "app.hsguru_archetype_analysis._load_refresh_checkpoint",
                return_value=checkpoint,
            ),
            patch("app.hsguru_archetype_analysis._utc_now", return_value=now),
            patch("app.hsguru_archetype_analysis.save_baseline"),
            patch("app.hsguru_archetype_analysis.save_dataset"),
            patch("app.hsguru_archetype_analysis.save_status"),
        ):
            result = asyncio.run(
                refresh_hsguru_archetype_analysis(fetch_html=fetch_html)
            )

        self.assertTrue(result["published"])
        self.assertEqual(result["resumed_targets"], 0)
        self.assertEqual(result["unavailable"], [])
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
