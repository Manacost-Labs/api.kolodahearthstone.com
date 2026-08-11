from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import DEFAULT_HSREPLAY_JSON_CHANNELS
from app.hsreplay_client import (
    _channel_urls,
    extract_json_payload,
    jina_url,
)


class HsreplayClientTest(unittest.TestCase):
    def test_extract_json_from_jina_markdown(self) -> None:
        body = "Title: x\n\nMarkdown Content:\n\n{\"cards\": []}"
        payload = extract_json_payload(body)
        self.assertIsInstance(payload, dict)
        self.assertIn("cards", payload)

    def test_extract_json_top_level_array(self) -> None:
        payload = extract_json_payload('[{"id": 1}, {"id": 2}]')
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[1]["id"], 2)

    def test_jina_url_prefix(self) -> None:
        self.assertTrue(jina_url("https://hsreplay.net/api/v1/x").startswith("https://r.jina.ai/"))

    def test_default_json_channels_use_cost_first_order(self) -> None:
        self.assertEqual(
            DEFAULT_HSREPLAY_JSON_CHANNELS,
            "flaresolverr,scrape_do,curl_cffi",
        )

    @patch("app.hsreplay_client.hsreplay_json_channels", return_value=["direct", "jina"])
    def test_channel_urls_order(self, _mock: object) -> None:
        api = "https://hsreplay.net/api/v1/arena/card_stats/"
        labels = [label for label, _ in _channel_urls(api)]
        self.assertEqual(labels, ["direct", "jina"])
        self.assertEqual(_channel_urls(api)[1][1], jina_url(api))


if __name__ == "__main__":
    unittest.main()
