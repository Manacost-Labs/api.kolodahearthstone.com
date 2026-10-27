"""The Next panel's PHP adapter as the panel operator experiences it.

Regression: every read held PHP's exclusive session file lock until it
finished, so while one panel request waited on the database, every other
request of the same admin (layout session, card history, the next screen)
waited behind it.

Runs the real `next-data.php` on PHP's built-in server with a real file
session. The only fake is the external database: a socket that accepts the
connection and never answers, like a stalled MariaDB.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PANEL = Path(__file__).resolve().parents[1]
PHP = shutil.which("php")
SESSION_ID = "nextlocktest0123456789abcdef"
LOGOUT_TOKEN = "fixture-logout-token"
PARSER_TOKEN = "b" * 64


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def php_string(value: str) -> str:
    return f's:{len(value.encode())}:"{value}";'


@unittest.skipUnless(PHP, "php-cli is required")
class NextDataSessionLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="next-data-lock-"))
        root = self.tmp / "panel"
        shutil.copytree(PANEL / "lib", root / "lib")
        shutil.copytree(PANEL / "scripts" / "lib", root / "scripts" / "lib")
        shutil.copy(PANEL / "next-data.php", root / "next-data.php")
        self.sessions = self.tmp / "sessions"
        self.sessions.mkdir()

        self.database = socket.socket()
        self.database.bind(("127.0.0.1", 0))
        self.database.listen(8)
        self.stalled: list[socket.socket] = []
        threading.Thread(target=self._accept_and_stall, daemon=True).start()
        (root / "config.php").write_text(
            "<?php return ['db' => ['dsn' => 'mysql:host=127.0.0.1;port="
            f"{self.database.getsockname()[1]};dbname=panel', 'user' => 'panel', 'password' => 'fixture']];\n"
        )

        self.origin = f"http://127.0.0.1:{free_port()}"
        self.server = subprocess.Popen(
            [PHP, "-d", f"session.save_path={self.sessions}", "-S", self.origin[7:], "-t", str(root)],
            env={"PHP_CLI_SERVER_WORKERS": "4", "PATH": "/usr/bin:/bin"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", int(self.origin.rsplit(":", 1)[1])), 0.1).close()
                break
            except OSError:
                time.sleep(0.05)

    def tearDown(self) -> None:
        self.server.kill()
        self.server.wait(5)
        for connection in self.stalled:
            connection.close()
        self.database.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _accept_and_stall(self) -> None:
        while True:
            try:
                connection, _ = self.database.accept()
            except OSError:
                return
            self.stalled.append(connection)

    def _write_session(self, *, with_tokens: bool) -> None:
        now = int(time.time())
        payload = (
            'panel_auth|a:4:{s:2:"id";i:243011385;s:5:"login";s:7:"Zulut30";'
            f's:16:"authenticated_at";i:{now};s:12:"last_seen_at";i:{now};}}'
        )
        if with_tokens:
            payload += (
                "csrf|" + php_string("a" * 64)
                + "panel_logout_csrf|" + php_string(LOGOUT_TOKEN)
                + "parser_control_csrf|" + php_string(PARSER_TOKEN)
            )
        (self.sessions / f"sess_{SESSION_ID}").write_text(payload)

    def _get(self, query: str, timeout: float) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{self.origin}/next-data.php?{query}",
            headers={"Cookie": f"koloda_admin={SESSION_ID}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, {}

    def test_session_answers_while_catalog_waits_on_a_stalled_database(self) -> None:
        self._write_session(with_tokens=True)
        catalog: list[int] = []
        waiting = threading.Thread(target=lambda: catalog.append(self._get("action=list", 30)[0]))
        waiting.start()
        time.sleep(1.0)  # the catalog request is now blocked on the database

        started = time.monotonic()
        status, body = self._get("action=session", 5)
        elapsed = time.monotonic() - started

        self.assertEqual(status, 200)
        self.assertEqual(
            body,
            {
                "ok": True,
                "user": {"id": 243011385, "login": "Zulut30"},
                "logoutCsrf": LOGOUT_TOKEN,
                "parserCsrf": PARSER_TOKEN,
            },
        )
        self.assertLess(elapsed, 2.0)
        self.assertTrue(waiting.is_alive(), "the catalog request must still be waiting on the database")

        for connection in self.stalled:
            connection.close()
        waiting.join(30)
        self.assertEqual(catalog, [500])
        # The operator is still signed in after both requests.
        self.assertEqual(self._get("action=session", 5)[0], 200)

    def test_first_request_of_a_new_session_keeps_the_tokens_it_creates(self) -> None:
        self._write_session(with_tokens=False)

        first_status, first = self._get("action=session", 5)
        second_status, second = self._get("action=session", 5)

        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual(second["logoutCsrf"], first["logoutCsrf"])
        self.assertEqual(second["parserCsrf"], first["parserCsrf"])


if __name__ == "__main__":
    unittest.main()
