from __future__ import annotations

import asyncio
import unittest

from app.hearthstone_decks import extract_deck_code_from_html
from app.hsguru_api import discover_hsguru_api_candidates


class HsGuruDecksImprovementsTest(unittest.TestCase):
    def test_extract_deck_code_from_copy_button(self) -> None:
        code = "AAECAf0GBMABCD1234567890abcdefghijklmnop=="
        html = f'<button data-clipboard-text="{code}">Copy deck</button>'

        self.assertEqual(extract_deck_code_from_html(html), code)

    def test_extract_deck_code_from_script_payload(self) -> None:
        code = "AAECAQcGXYZ1234567890abcdefghijklmnopqr=="
        html = f"<script>window.deck = {{ code: '{code}' }};</script>"

        self.assertEqual(extract_deck_code_from_html(html), code)

    def test_hsguru_recon_discovers_embedded_json_and_api_candidates(self) -> None:
        html = """
        <html>
          <head><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{}}}</script></head>
          <body><script>fetch("/api/meta?format=2&rank=legend")</script></body>
        </html>
        """

        result = discover_hsguru_api_candidates(html, page_url="https://www.hsguru.com/meta")

        self.assertTrue(result["ok"])
        self.assertEqual(result["embedded_json"][0]["id"], "__NEXT_DATA__")
        self.assertIn("https://www.hsguru.com/api/meta?format=2&rank=legend", result["api_candidates"])

    def test_catalog_refresh_attempts_every_dataset_after_one_failure(self) -> None:
        from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs

        calls: list[tuple[str, str]] = []

        async def refresh(format_name: str, rank: str = "legend") -> list[dict[str, str]]:
            calls.append((format_name, rank))
            if (format_name, rank) == ("standard", "legend"):
                raise RuntimeError("temporary upstream failure")
            return [{"deck_code": f"{format_name}-{rank}"}]

        result = asyncio.run(refresh_all_deck_catalogs(refresh=refresh, join=lambda: {"ok": True}))

        self.assertEqual(
            calls,
            [
                ("standard", "legend"),
                ("wild", "legend"),
                ("standard", "all"),
                ("wild", "all"),
            ],
        )
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["datasets"]["wild_legend"]["decks"], 1)
        self.assertIn("standard_legend", result["errors"])
        self.assertEqual(result["archetype_join"], {"ok": True})


if __name__ == "__main__":
    unittest.main()
