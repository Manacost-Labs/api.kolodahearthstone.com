from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CosmeticSyncContractTest(unittest.TestCase):
    def test_pet_cargo_queries_follow_all_pages(self):
        pets = load_script("sync_pets")
        offsets: list[int] = []

        def fake_http_json(params):
            offset = int(params.get("offset", 0))
            offsets.append(offset)
            size = 500 if offset == 0 else 1
            return {
                "cargoquery": [
                    {"title": {"id": str(offset + index)}}
                    for index in range(size)
                ]
            }

        pets.http_json = fake_http_json
        rows = pets.cargo_rows("Pet", "id")

        self.assertEqual(len(rows), 501)
        self.assertEqual(offsets, [0, 500])

    def test_pet_cargo_queries_reject_repeated_pages(self):
        pets = load_script("sync_pets")
        repeated_page = [
            {"title": {"id": str(index)}}
            for index in range(500)
        ]
        calls = 0

        def fake_http_json(_params):
            nonlocal calls
            calls += 1
            if calls > 2:
                raise AssertionError("pagination did not stop on a repeated page")
            return {"cargoquery": repeated_page}

        pets.http_json = fake_http_json

        with self.assertRaisesRegex(RuntimeError, "repeated page"):
            pets.cargo_rows("Pet", "id")

    def test_hero_skin_cli_separates_index_and_page_refresh(self):
        hero_skins = load_script("sync_hero_skins")
        parser = hero_skins.build_arg_parser()

        daily = parser.parse_args(["--refresh-index"])
        weekly = parser.parse_args(["--refresh-index", "--refresh-pages"])

        self.assertTrue(daily.refresh_index)
        self.assertFalse(daily.refresh_pages)
        self.assertTrue(weekly.refresh_index)
        self.assertTrue(weekly.refresh_pages)

    def test_job_runner_wires_all_cosmetic_jobs(self):
        runner = (ROOT / "scripts" / "run_sync_job.sh").read_text(encoding="utf-8")

        self.assertIn("hero-skins)", runner)
        self.assertIn("hero-skins-refresh)", runner)
        self.assertIn('sync_pets.py"', runner)
        self.assertIn('sync_coins.py"', runner)
        self.assertIn('"--refresh-index"', runner)

    def test_all_cosmetic_timers_are_declared(self):
        expected = {
            "kolodahs-sync-hero-skins.timer",
            "kolodahs-sync-hero-skins-refresh.timer",
            "kolodahs-sync-pets.timer",
            "kolodahs-sync-coins.timer",
        }

        declared = {path.name for path in (ROOT / "systemd").glob("*.timer")}
        self.assertTrue(expected.issubset(declared))


if __name__ == "__main__":
    unittest.main()
