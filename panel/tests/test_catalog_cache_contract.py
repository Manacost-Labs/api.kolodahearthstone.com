from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CatalogCacheContract(unittest.TestCase):
    def test_public_cache_revision_and_scope(self):
        api = (ROOT / 'api/index.php').read_text()
        nginx = (ROOT / 'nginx/api.kolodahearthstone.com.conf').read_text()
        version = re.search(r"const API_VERSION = '([^']+)'", api).group(1)
        public = nginx.split('location ~ ^/api(?:/?$|/v1(?:/|$)) {', 1)[1].split('\n    }', 1)[0]
        self.assertIn(f'rest-{version}|$request_method|$request_uri', public)
        self.assertIn('fastcgi_cache_valid 200 30s;', public)
        self.assertIn('fastcgi_cache_lock on;', public)
        self.assertIn('$http_authorization $cookie_koloda_admin $koloda_rest_refresh', public)
        self.assertIn('$koloda_rest_no_store $koloda_rest_private $upstream_http_set_cookie', public)
        self.assertEqual(nginx.count('fastcgi_cache koloda_rest;'), 1)


if __name__ == '__main__':
    unittest.main()
