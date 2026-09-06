"""Cross-language report/normalizer verification, isolated files and no HTTP."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


class AcquisitionBridgeTest(unittest.TestCase):
    def test_bridge_runs_without_checkout_local_virtualenv(self):
        with tempfile.TemporaryDirectory(prefix="acquisition-ci-layout-") as directory:
            root = Path(directory)
            test_dir = root / "panel/tests"
            test_dir.mkdir(parents=True)
            target = test_dir / Path(__file__).name
            shutil.copyfile(__file__, target)
            (root / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
            (root / "panel/lib").symlink_to(
                ROOT / "panel/lib", target_is_directory=True
            )
            self.assertFalse((root / ".venv").exists())
            result = subprocess.run(
                [
                    sys.executable,
                    str(target),
                    "AcquisitionBridgeTest.test_python_export_feeds_php_full_registry_without_fabricating_success",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)

    def php(self, code, *, payload="", env=None):
        return subprocess.run(
            ["php", "-r", "require 'panel/lib/analytics.php'; " + code],
            cwd=ROOT,
            input=payload,
            text=True,
            capture_output=True,
            check=True,
            env=env,
        )

    def test_python_export_feeds_php_full_registry_without_fabricating_success(self):
        result = subprocess.run(
            [str(PYTHON), "scripts/export_acquisition_panel.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        snapshot = json.loads(result.stdout)
        code = """
        $p = json_decode(stream_get_contents(STDIN), true, 32, JSON_THROW_ON_ERROR);
        $fetch = ['payload' => ['sources' => $p['sources']], 'cached' => false, 'stale_cache' => false, 'cache_age' => 0];
        echo json_encode(analytics_acquisition_normalize(analytics_module_registry()['acquisition'], $fetch, ['state'=>'available', 'payload'=>$p], []));
        """
        rendered = json.loads(self.php(code, payload=result.stdout).stdout)
        self.assertEqual(len(snapshot["sources"]), len(rendered["rows"]))
        self.assertEqual("available", rendered["meta"]["report_state"])
        self.assertTrue(all(row["state_code"] == "unknown" for row in rendered["rows"]))

    def test_export_is_atomic_private_and_requires_explicit_replace(self):
        with tempfile.TemporaryDirectory(prefix="acquisition-panel-test-") as temporary:
            path = Path(temporary) / "report.json"
            command = [
                str(PYTHON),
                "scripts/export_acquisition_panel.py",
                "--output",
                str(path),
            ]
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
            before = path.read_bytes()
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            failed = subprocess.run(command, cwd=ROOT, capture_output=True, check=False)
            self.assertNotEqual(0, failed.returncode)
            self.assertEqual(before, path.read_bytes())
            invalid = Path(temporary) / "invalid.json"
            invalid.write_text('[{"secret":"never-print"}]')
            failed = subprocess.run(
                command + ["--replace", "--observations", str(invalid)],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, failed.returncode)
            self.assertNotIn(b"never-print", failed.stderr)
            self.assertEqual(before, path.read_bytes())
            self.assertEqual([], list(Path(temporary).glob(".acquisition-*")))

    def test_reader_is_bounded_and_does_not_follow_links(self):
        with tempfile.TemporaryDirectory(prefix="acquisition-panel-read-") as temporary:
            path = Path(temporary) / "report.json"
            path.write_bytes(b"x" * (1048576 + 1))
            env = {**os.environ, "HS_ACQUISITION_PANEL_REPORT": str(path)}
            code = "echo json_encode(analytics_acquisition_read());"
            self.assertEqual(
                "unavailable", json.loads(self.php(code, env=env).stdout)["state"]
            )
            path.write_text('{"schema_version":1}')
            alias = Path(temporary) / "link.json"
            alias.symlink_to(path)
            env["HS_ACQUISITION_PANEL_REPORT"] = str(alias)
            self.assertEqual(
                "unavailable", json.loads(self.php(code, env=env).stdout)["state"]
            )

    def test_real_bg_queue_report_reaches_panel_as_partial(self):
        producer = """
import json, tempfile
from datetime import UTC, datetime
from pathlib import Path
from app.acquisition_coverage import source_view, ListingEvidence
from app.acquisition_detail_queue import DetailQueue
from app.acquisition_panel import build_panel_snapshot
from app.bg_detail_acquisition import prepare_bg_details, bg_detail_report
from app.sources import SOURCE_BY_ID
with tempfile.TemporaryDirectory(prefix="panel-bg-bridge-") as directory:
    queue = DetailQueue(Path(directory) / "isolated.sqlite")
    view = source_view(SOURCE_BY_ID["hsreplay_battlegrounds_comps"], snapshot_id="test-capture")
    rows = [{"id": "hsreplay-1", "comp_id": 1, "url": "https://hsreplay.net/battlegrounds/comps/1/mechs/", "main_cards": [], "how_to_play": ""}]
    prepare_bg_details(queue, view, rows)
    listing = ListingEvidence(view.scope_id, ("hsreplay-1",), exhausted=True, expected_count=1, view_confirmed=True)
    report = bg_detail_report(queue, view, rows, listing=listing)
    report["observed_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(build_panel_snapshot([report])))
"""
        snapshot = subprocess.run(
            [str(PYTHON), "-c", producer],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        code = """
        $p = json_decode(stream_get_contents(STDIN), true, 32, JSON_THROW_ON_ERROR);
        $fetch = ['payload'=>['sources'=>$p['sources']], 'cached'=>false, 'stale_cache'=>false, 'cache_age'=>0];
        echo json_encode(analytics_acquisition_normalize(analytics_module_registry()['acquisition'], $fetch, ['state'=>'available','payload'=>$p], ['coverage_state'=>'partial']));
        """
        result = json.loads(self.php(code, payload=snapshot).stdout)
        self.assertEqual(1, len(result["rows"]))
        self.assertEqual("hsreplay_battlegrounds_comps", result["rows"][0]["source"])
        self.assertEqual(1, result["rows"][0]["detail_states"]["pending"])
        self.assertEqual(1, result["rows"][0]["unresolved_details"])

    def test_fractional_percentage_survives_python_php_rounding(self):
        for found, expected in [(1, 32), (107, 4000), (1, 128)]:
            with self.subTest(found=found, expected=expected):
                producer = f"""
import json
from datetime import UTC, datetime
from app.acquisition_coverage import source_view, ListingEvidence, coverage_report
from app.acquisition_panel import build_panel_snapshot
from app.sources import SOURCES
view = source_view(SOURCES[0], snapshot_id="fractional-test")
ids = tuple(str(i) for i in range({found}))
coverage = coverage_report(view, listing=ListingEvidence(view.scope_id, ids, expected_count={expected}, view_confirmed=True), detail_states={{i: "succeeded" for i in ids}})
print(json.dumps(build_panel_snapshot([{{"coverage": coverage, "observed_at": datetime.now(UTC).isoformat()}}])))
"""
                snapshot = subprocess.run(
                    [str(PYTHON), "-c", producer],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                result = self.php(
                    """
                $p = json_decode(stream_get_contents(STDIN), true, 32, JSON_THROW_ON_ERROR);
                $index = acquisition_observation_index(['state'=>'available', 'payload'=>$p], time());
                echo json_encode($index === null ? null : count($index));
                """,
                    payload=snapshot,
                )
                self.assertEqual(
                    len(json.loads(snapshot)["sources"]), json.loads(result.stdout)
                )

    def test_navigation_markup_preserves_coverage_filter_and_escapes_search(self):
        output = self.php("""
        function h($v) { return htmlspecialchars((string)$v, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }
        $_GET = ['stats'=>'acquisition', 'stats_coverage'=>'partial', 'stats_q'=>'<script>bad()</script>'];
        require 'panel/partials/analytics-dashboard.php';
        """).stdout
        self.assertIn('data-analytics-module="acquisition"', output)
        self.assertIn('value="partial" selected', output)
        self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", output)
        self.assertNotIn("<script>bad()", output)
        self.assertIn("data-analytics-detail-drawer", output)


if __name__ == "__main__":
    unittest.main()
