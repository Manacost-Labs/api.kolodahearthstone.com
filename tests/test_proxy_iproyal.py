from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from app.config import fetch_proxy_url, fetch_require_proxy
from app.scrapers import proxy


class IPRoyalProxyUrlTest(unittest.TestCase):
    def test_leftover_proxy_url_is_ignored_without_explicit_enable(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "HS_FETCH_PROXY_URL": "http://user:pass@geo.iproyal.com:12321",
                "HS_FETCH_REQUIRE_PROXY": "true",
            },
            clear=True,
        ):
            self.assertIsNone(fetch_proxy_url())
            self.assertFalse(fetch_require_proxy())

    def test_residential_proxy_requires_explicit_enable(self) -> None:
        url = "http://user:pass@geo.iproyal.com:12321"
        with patch.dict(
            "os.environ",
            {
                "HS_RESIDENTIAL_PROXY_ENABLED": "true",
                "HS_FETCH_PROXY_URL": url,
            },
            clear=True,
        ):
            self.assertEqual(fetch_proxy_url(), url)
            self.assertFalse(fetch_require_proxy())

    def test_session_is_added_to_password(self) -> None:
        base = "http://user:pass_country-us@geo.iproyal.com:12321"

        with patch.object(proxy, "iproyal_session_lifetime", return_value="30m"):
            url = proxy._iproyal_url_with_session(base, "hsreplay.net")

        parsed = urlparse(url)
        self.assertEqual(parsed.username, "user")
        self.assertIn("_session-", parsed.password or "")
        self.assertIn("_lifetime-30m", parsed.password or "")
        self.assertNotIn("_session-", parsed.username or "")

    def test_existing_session_is_replaced(self) -> None:
        base = "http://user:pass_country-us_session-old_lifetime-2h@geo.iproyal.com:12321"

        with patch.object(proxy, "iproyal_session_lifetime", return_value="30m"):
            url = proxy._iproyal_url_with_session(base, "hsguru.com")

        parsed = urlparse(url)
        self.assertTrue((parsed.password or "").startswith("pass_country-us_session-"))
        self.assertIn("_lifetime-30m", parsed.password or "")
        self.assertNotIn("old", parsed.password or "")

    def test_burned_session_key_changes_token(self) -> None:
        self.assertNotEqual(
            proxy._iproyal_session_token("hsguru.com"),
            proxy._iproyal_session_token("hsguru.com_burn1"),
        )

if __name__ == "__main__":
    unittest.main()
