from __future__ import annotations

import email.message
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_constructed_wiki_meta as wiki_meta


def too_many(retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://hearthstone.wiki.gg/api.php", 429, "Too Many Requests", headers, None)


class Parser:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures

    @staticmethod
    def safe_filename(title: str) -> str:
        return title

    def build_result(self, title: str, download: bool, output_dir: Path) -> dict:
        if self.failures:
            raise self.failures.pop(0)
        return {"page_title": title}


class RateLimitBackoffTest(unittest.TestCase):
    def fetch(self, parser: Parser, sleeps: list[float]):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(wiki_meta.time, "sleep", sleeps.append):
            return wiki_meta.load_or_fetch_page(Path(tmp), "Prince Keleseth", True, parser, retries=3)

    def test_waits_for_retry_after_then_succeeds(self) -> None:
        sleeps: list[float] = []
        result, source = self.fetch(Parser([too_many("90"), too_many()]), sleeps)
        self.assertEqual((result["page_title"], source), ("Prince Keleseth", "network"))
        self.assertEqual(sleeps, [90, 120])

    def test_gives_up_with_rate_limit_after_retries(self) -> None:
        sleeps: list[float] = []
        with self.assertRaises(wiki_meta.RateLimitedError):
            self.fetch(Parser([too_many("100000")] * 4), sleeps)
        self.assertEqual(sleeps, [wiki_meta.MAX_RATE_LIMIT_WAIT_SECONDS] * 3)


if __name__ == "__main__":
    unittest.main()
