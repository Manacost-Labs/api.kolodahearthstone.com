import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from wiki_http import fetch_bytes


class WikiRetryTest(unittest.TestCase):
    def test_retry_after_is_respected(self):
        opener = Mock(side_effect=[HTTPError("url", 429, "limited", {"Retry-After": "7"}, io.BytesIO()), io.BytesIO(b"ok")])
        sleep = Mock()
        self.assertEqual(fetch_bytes("url", opener=opener, sleep=sleep), b"ok")
        sleep.assert_called_once_with(7)

    def test_three_attempt_limit(self):
        opener = Mock(side_effect=lambda *a, **k: (_ for _ in ()).throw(HTTPError("url", 503, "down", {}, io.BytesIO())))
        with self.assertRaises(HTTPError):
            fetch_bytes("url", opener=opener, sleep=Mock())
        self.assertEqual(opener.call_count, 3)

    def test_does_not_retry_404_or_long_cooldown(self):
        for code, headers in ((404, {}), (429, {"Retry-After": "3600"})):
            opener = Mock(side_effect=HTTPError("url", code, "error", headers, io.BytesIO()))
            sleep = Mock()
            with self.assertRaises(HTTPError):
                fetch_bytes("url", opener=opener, sleep=sleep)
            self.assertEqual(opener.call_count, 1)
            sleep.assert_not_called()
