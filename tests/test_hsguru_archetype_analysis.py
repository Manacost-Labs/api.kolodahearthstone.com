from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.hsguru_archetype_analysis import (
    analysis_urls,
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


class HSGuruArchetypeAnalysisTest(unittest.TestCase):
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

    def test_builds_legend_past_week_urls_for_requested_format(self) -> None:
        urls = analysis_urls("Void Soul DH", "standard")

        self.assertIn("/archetype/Void%20Soul%20DH?", urls["matchups"])
        self.assertIn("format=2", urls["matchups"])
        self.assertIn("rank=legend", urls["matchups"])
        self.assertIn("period=past_week", urls["matchups"])
        self.assertIn("show_counts=yes", urls["cards"])

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
        self.assertEqual(negative_cache[0]["state"], "upstream_unavailable")
        checked_at = datetime.fromisoformat(negative_cache[0]["checked_at"])
        retry_after = datetime.fromisoformat(negative_cache[0]["retry_after"])
        self.assertEqual(retry_after - checked_at, timedelta(days=7))

    def test_refresh_skips_card_stats_while_negative_cache_is_fresh(self) -> None:
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
        self.assertEqual(
            saved["payload"]["data"]["structured"]["negative_cache"],
            [cached_gap],
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


if __name__ == "__main__":
    unittest.main()
