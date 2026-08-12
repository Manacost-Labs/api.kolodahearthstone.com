from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import sync_constructed_related_wiki_art as wiki_art  # noqa: E402


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ScrapeDoFetchTest(unittest.TestCase):
    def test_proxies_target_without_rendering(self) -> None:
        target = "https://hearthstone.wiki.gg/api.php?action=query"
        with (
            patch.dict(os.environ, {"HS_SCRAPE_DO_TOKEN": "test-secret"}),
            patch.object(
                wiki_art.urllib.request,
                "urlopen",
                return_value=FakeResponse(b'{"query": {}}'),
            ) as urlopen,
        ):
            payload = wiki_art.scrape_do_fetch(
                target,
                headers={"Accept": "application/json"},
                timeout=90,
            )

        self.assertEqual(payload, b'{"query": {}}')
        request = urlopen.call_args.args[0]
        query = parse_qs(urlparse(request.full_url).query)
        self.assertTrue(request.full_url.startswith(wiki_art.SCRAPE_DO_ENDPOINT + "?"))
        self.assertEqual(query["token"], ["test-secret"])
        self.assertEqual(query["url"], [target])
        self.assertEqual(query["render"], ["false"])
        self.assertEqual(query["retryTimeout"], ["30000"])

    def test_never_leaks_the_token_in_http_errors(self) -> None:
        def fail(request, **_kwargs):
            raise HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {},
                io.BytesIO(),
            )

        with (
            patch.dict(os.environ, {"HS_SCRAPE_DO_TOKEN": "test-secret"}),
            patch.object(wiki_art.urllib.request, "urlopen", side_effect=fail),
            self.assertRaises(wiki_art.ScrapeDoFetchError) as caught,
        ):
            wiki_art.scrape_do_fetch(
                "https://hearthstone.wiki.gg/api.php?action=query",
                headers={"Accept": "application/json"},
                timeout=90,
            )

        self.assertEqual(caught.exception.status_code, 429)
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertNotIn("api.scrape.do", str(caught.exception))

    def test_page_image_lookup_uses_mediawiki_fifty_title_batches(self) -> None:
        requests: list[dict[str, object]] = []

        def fake_wiki_api(params):
            requests.append(params)
            return {"query": {"pages": []}}

        titles = [f"Card {index}" for index in range(51)]
        with patch.object(wiki_art, "wiki_api", side_effect=fake_wiki_api):
            wiki_art.fetch_page_images(titles)

        self.assertEqual(len(requests), 2)
        self.assertEqual(len(str(requests[0]["titles"]).split("|")), 50)
        self.assertEqual(len(str(requests[1]["titles"]).split("|")), 1)


if __name__ == "__main__":
    unittest.main()
