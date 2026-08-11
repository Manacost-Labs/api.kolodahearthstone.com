from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app.hsreplay_arena_api import (
    _pct,
    _region_name,
    _win_rate_sort_key,
    normalize_arena_card_row,
    normalize_class_row,
)


class ArenaParsingGuardsTest(unittest.TestCase):
    def test_pct_tolerates_non_numeric_values(self) -> None:
        self.assertEqual(_pct(51.234), "51.23%")
        self.assertEqual(_pct("48.5"), "48.50%")
        self.assertIsNone(_pct(None))
        self.assertIsNone(_pct("n/a"))
        self.assertIsNone(_pct({}))

    def test_win_rate_sort_key_tolerates_garbage(self) -> None:
        self.assertEqual(_win_rate_sort_key({"win_rate": 55.5}), 55.5)
        self.assertEqual(_win_rate_sort_key({"win_rate": "52.1"}), 52.1)
        self.assertEqual(_win_rate_sort_key({"win_rate": "n/a"}), 0.0)
        self.assertEqual(_win_rate_sort_key({}), 0.0)

    def test_region_name_tolerates_garbage(self) -> None:
        self.assertEqual(_region_name(2), "EU")
        self.assertEqual(_region_name("2"), "EU")
        self.assertIsNone(_region_name("unknown"))
        self.assertIsNone(_region_name(None))
        self.assertIsNone(_region_name(99))

    def test_normalize_class_row_tolerates_string_class_id(self) -> None:
        # deck_class приходит из upstream JSON; строка "2" не должна ронять refresh
        row = normalize_class_row({"deck_class": "2", "win_rate": 50.0})
        self.assertEqual(row["class"], "Druid")
        row = normalize_class_row({"deck_class": "garbage", "win_rate": 50.0})
        self.assertIsNone(row["class"])

    def test_normalize_arena_card_row_tolerates_bad_dbf_id(self) -> None:
        # card_id пуст, dbf_id мусорный: строка отбрасывается (None), не ValueError
        self.assertIsNone(normalize_arena_card_row({"dbf_id": "not-a-number"}))


class FirecrawlPerSourceCapTest(unittest.TestCase):
    def _run(self, source_id: str, reason: str = "api_quality_error:test"):
        from app import fetcher
        from app.sources import SOURCE_BY_ID

        source = SOURCE_BY_ID[source_id]
        return asyncio.run(
            fetcher._try_firecrawl_html(source, fetched_at="2026-07-02T00:00:00+00:00", reason=reason)
        )

    def test_one_source_cannot_exhaust_global_budget(self) -> None:
        from app import fetcher

        calls: list[str] = []

        async def fake_scrape(source, **_options):
            assert callable(_options.get("accept_result"))
            calls.append(source.id)
            raise RuntimeError("stop after budget accounting")

        fetcher._firecrawl_fallback_attempts = 0
        fetcher._firecrawl_fallback_attempts_by_source.clear()
        sid_a = "hsguru_meta_standard_legend"
        sid_b = "hsguru_meta_wild_legend"
        with (
            patch("app.firecrawl_backend.scrape_source", side_effect=fake_scrape),
            patch("app.fetcher.firecrawl_fallback_max_attempts_per_refresh", return_value=8),
            patch("app.fetcher.firecrawl_fallback_max_attempts_per_source", return_value=2),
            patch("app.fetcher.log_action"),
        ):
            # источник A: 2 попытки проходят до scrape, 3-я срезается per-source cap
            for _ in range(3):
                self._run(sid_a)
            self.assertEqual(calls.count(sid_a), 2)
            self.assertEqual(fetcher._firecrawl_fallback_attempts, 2)
            # источник B всё ещё получает fallback — бюджет не съеден источником A
            self._run(sid_b)
            self.assertEqual(calls.count(sid_b), 1)

        fetcher._firecrawl_fallback_attempts = 0
        fetcher._firecrawl_fallback_attempts_by_source.clear()


class AdminAuthTest(unittest.TestCase):
    def test_require_admin_timing_safe_paths(self) -> None:
        from fastapi import HTTPException

        from app.main import require_admin

        with patch("app.main.api_key", return_value="sekret"):
            self.assertIsNone(require_admin(x_api_key="sekret"))
            with self.assertRaises(HTTPException) as ctx:
                require_admin(x_api_key="wrong")
            self.assertEqual(ctx.exception.status_code, 401)
            with self.assertRaises(HTTPException) as ctx:
                require_admin(x_api_key=None)
            self.assertEqual(ctx.exception.status_code, 401)
        with patch("app.main.api_key", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                require_admin(x_api_key="anything")
            self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
