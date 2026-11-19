import fcntl
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_card_renders as sync

T0 = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)
Usage = namedtuple("Usage", "total used free")
GB = 1024**3


def png(width=512):
    out = io.BytesIO()
    Image.new("RGBA", (width, 8), (10, 20, 30, 128)).save(out, format="PNG")
    return out.getvalue()


def row(card_id, locale="plPL", h="h1", collectible=1, card_type="MINION"):
    return {"card_id": card_id, "locale": locale, "source_hash": h,
            "collectible": collectible, "card_type": card_type}


class Env:
    def __init__(self, test, rows, responses=None):
        self.tmp = Path(tempfile.mkdtemp())
        test.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.uploads = self.tmp / "uploads"
        self.uploads.mkdir()
        self.var = self.tmp / "var"
        self.rows = rows
        self.responses = responses or {}
        self.calls = []
        self.now = 0.0
        self.sleeps = []
        self.free = 100 * GB
        self.utc = T0

    def fetch(self, url):
        self.calls.append(url)
        card = url.rsplit("/", 1)[1][:-4]
        resp = self.responses.get(card, (200, png(), None))
        if isinstance(resp, Exception):
            raise resp
        return resp

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def run(self, *extra, apply=True, locales="plPL"):
        rows_file = self.tmp / "rows.json"
        rows_file.write_text(json.dumps(self.rows))
        argv = ["--rows-file", str(rows_file), "--uploads-dir", str(self.uploads),
                "--var-dir", str(self.var), "--locales", locales, *extra]
        if apply:
            argv.append("--apply")
        with patch.object(sync.shutil, "disk_usage",
                          lambda _p: Usage(200 * GB, 0, self.free)):
            return sync.main(argv, fetch=self.fetch, clock=self.clock,
                             sleep=self.sleep, utcnow=lambda: self.utc)

    def index(self, loc="plPL"):
        return json.loads((self.var / f"{loc}.index.json").read_text())

    def last(self):
        return json.loads((self.var / "last-run.json").read_text())


class SyncCardRendersTests(unittest.TestCase):
    def test_dry_run_prints_plan_without_network_or_writes(self):
        env = Env(self, [row("A", loc) for loc in sync.LOCALES] + [row("E", card_type="ENCHANTMENT")])
        with patch("builtins.print") as printed:
            self.assertEqual(env.run(apply=False, locales=",".join(sync.LOCALES)), 0)
        self.assertEqual(env.calls, [])
        self.assertFalse(env.var.exists())
        self.assertEqual(list(env.uploads.iterdir()), [])
        text = "\n".join(str(c.args[0]) for c in printed.call_args_list)
        for loc in sync.LOCALES:
            self.assertIn(f"{loc}: new=1", text)

    def test_apply_writes_webp_index_and_skips_types(self):
        env = Env(self, [row("A"), row("B"), row("E", card_type="ENCHANTMENT"),
                         row("P", card_type="PET"), row("bad id!"), row("N", card_type=None)],
                  {"B": (404, b"", None)})
        self.assertEqual(env.run(), 0)
        self.assertEqual(len(env.calls), 2)
        self.assertFalse(any("/E.png" in c or "/P.png" in c for c in env.calls))
        path = env.uploads / "card-renders/plPL/512x/A.webp"
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o644")
        image = Image.open(path)
        self.assertEqual((image.format, image.width), ("WEBP", 512))
        self.assertIn("A", image.getbands())
        self.assertEqual(image.convert("RGBA").getpixel((0, 0))[3] // 8, 128 // 8)
        index = env.index()
        self.assertEqual(index["renders"], {"A": hashlib.sha256(path.read_bytes()).hexdigest()[:12]})
        self.assertEqual(index["missing"], ["B"])
        self.assertEqual((index["version"], index["size"], index["locale"]), (1, "512x", "plPL"))
        self.assertEqual(index["skip_types"], list(sync.SKIP_TYPES))
        self.assertFalse([p for p in path.parent.iterdir() if p.name.startswith(".tmp-")])
        last = env.last()
        self.assertEqual((last["requests"], last["downloaded"], last["not_found"],
                          last["skipped_invalid_id"], last["stopped_reason"]), (2, 1, 1, 1, "done"))
        self.assertGreater(last["avg_webp_bytes"], 0)

    def test_idempotent_and_hash_change_and_404_recheck(self):
        env = Env(self, [row("A"), row("B")], {"B": (404, b"", None)})
        env.run()
        env.calls.clear()
        env.run()
        self.assertEqual(env.calls, [])
        env.rows = [row("A", h="h2"), row("B")]
        env.run()
        self.assertEqual(len(env.calls), 1)
        env.calls.clear()
        env.rows = [row("A", h="h2"), row("B", h="h2")]  # 404 + hash changed
        env.run()
        self.assertEqual(len(env.calls), 1)
        env.calls.clear()
        env.utc = T0 + timedelta(days=29)
        env.run()
        self.assertEqual(env.calls, [])
        env.utc = T0 + timedelta(days=31)
        env.run()
        self.assertEqual(len(env.calls), 1)

    def test_collectible_first_then_card_id_all_locales(self):
        rows = [row("Z", loc, collectible=1) for loc in ("plPL", "enUS")]
        rows += [row("A", loc, collectible=0) for loc in ("plPL", "enUS")]
        rows += [row("M", loc, collectible=1) for loc in ("plPL", "enUS")]
        env = Env(self, rows)
        env.run("--max-requests", "3", locales="enUS,plPL")
        self.assertEqual([c.split("latest/")[1][:-4] for c in env.calls],
                         ["enUS/512x/M", "plPL/512x/M", "enUS/512x/Z"])
        self.assertEqual(env.last()["stopped_reason"], "budget")

    def test_rate_limit_and_validation(self):
        env = Env(self, [row(f"C{i}") for i in range(4)])
        env.run("--rate", "2")
        self.assertEqual(env.sleeps, [0.5, 0.5, 0.5])
        for bad in ("0.4", "5.1", "x"):
            with self.assertRaises(SystemExit):
                sync.main(["--rate", bad, "--rows-file", "x"])

    def test_429_stops_with_75_and_keeps_downloaded(self):
        env = Env(self, [row("A"), row("B"), row("C")], {"B": (429, b"", "120")})
        self.assertEqual(env.run(), 75)
        self.assertEqual(len(env.calls), 2)
        last = env.last()
        self.assertEqual(last["stopped_reason"], "upstream_errors")
        self.assertIn("A", env.index()["renders"])
        self.assertEqual(env.index()["missing"], [])
        # Retry-After is honoured by the next run
        env.calls.clear()
        self.assertEqual(env.run(), 75)
        self.assertEqual(env.calls, [])
        env.utc = T0 + timedelta(seconds=200)
        self.assertEqual(env.run(), 75)  # B still returns 429
        self.assertEqual(len(env.calls), 1)

    def test_twenty_consecutive_errors_stop_run(self):
        rows = [row(f"E{i:02d}") for i in range(25)]
        env = Env(self, rows, {r["card_id"]: (503, b"", None) for r in rows})
        self.assertEqual(env.run(), 75)
        self.assertEqual(len(env.calls), 20)
        self.assertEqual(env.index()["missing"], [])
        self.assertEqual(env.last()["stopped_reason"], "upstream_errors")

    def test_bad_images_back_off_like_404_and_never_stop_the_run(self):
        rows = [row(f"B{i:02d}") for i in range(25)] + [row("Z")]
        env = Env(self, rows, {r["card_id"]: (200, b"not a png", None) for r in rows[:24]})
        env.responses["B24"] = (200, png(256), None)
        self.assertEqual(env.run(), 0)
        last = env.last()
        self.assertEqual((last["bad_image"], last["errors"], last["downloaded"]), (25, 0, 1))
        self.assertEqual(last["stopped_reason"], "done")
        self.assertEqual(env.index()["missing"], [])  # API falls back to the HSJ link
        env.calls.clear()
        env.run()
        self.assertEqual(env.calls, [])
        env.rows = [row("B00", h="h2")] + rows[1:]
        env.run()
        self.assertEqual(len(env.calls), 1)
        env.calls.clear()
        env.utc = T0 + timedelta(days=31)
        env.run()
        self.assertEqual(len(env.calls), 25)

    def test_time_budget_stops_with_code_zero(self):
        env = Env(self, [row(f"T{i}") for i in range(10)])
        real = env.fetch

        def slow(url):
            env.now += 10  # each request takes 10 s on the fake clock
            return real(url)

        env.fetch = slow
        self.assertEqual(env.run("--max-seconds", "35"), 0)
        self.assertEqual(env.last()["stopped_reason"], "time")
        self.assertEqual(len(env.calls), 4)
        self.assertEqual(len(env.index()["renders"]), 4)

    def test_sigterm_flushes_index_state_and_last_run(self):
        import signal
        env = Env(self, [row(f"S{i}") for i in range(10)])
        real = env.fetch

        def fetch(url):
            if len(env.calls) == 4:
                os.kill(os.getpid(), signal.SIGTERM)
            return real(url)

        env.fetch = fetch
        previous = signal.getsignal(signal.SIGTERM)
        self.assertEqual(env.run(), 0)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        self.assertEqual(env.last()["stopped_reason"], "signal")
        self.assertEqual(len(env.index()["renders"]), 5)
        env.calls.clear()
        env.fetch = real
        env.run()
        self.assertEqual(len(env.calls), 5)  # only the rest, nothing downloaded twice

    def test_missing_uploads_dir_is_a_clear_error_and_writes_last_run(self):
        env = Env(self, [row("A")])
        self.assertEqual(env.run("--uploads-dir", str(env.tmp / "nope")), 2)
        self.assertEqual(env.calls, [])
        self.assertEqual(env.last()["stopped_reason"], "bad_uploads_dir")

    def test_retry_after_http_date_and_clamp(self):
        now = T0
        date = "Thu, 19 Nov 2026 12:05:00 GMT"
        self.assertEqual(sync.retry_after_seconds(date, now), 300)
        self.assertEqual(sync.retry_after_seconds("Wed, 18 Nov 2026 12:00:00 GMT", now), 0)
        self.assertEqual(sync.retry_after_seconds("Sun, 06 Dec 2026 12:00:00 GMT", now), 86400)
        self.assertEqual(sync.retry_after_seconds("999999", now), 86400)
        self.assertEqual(sync.retry_after_seconds("garbage", now), 0)
        env = Env(self, [row("A")], {"A": (429, b"", date)})
        self.assertEqual(env.run(), 75)
        self.assertEqual(env.last()["retry_after_until"], "2026-11-19T12:05:00Z")
        env.calls.clear()
        self.assertEqual(env.run(), 75)
        self.assertEqual(env.calls, [])

    def test_low_disk_exits_1_without_network(self):
        env = Env(self, [row("A")])
        env.free = 5 * GB
        self.assertEqual(env.run(), 1)
        self.assertEqual(env.calls, [])
        self.assertEqual(env.last()["stopped_reason"], "low_disk")

    def test_low_disk_midway_stops(self):
        rows = [row(f"C{i:04d}") for i in range(sync.FLUSH_EVERY + 5)]
        env = Env(self, rows)
        real = env.fetch

        def fetch(url):
            if len(env.calls) == sync.FLUSH_EVERY - 1:
                env.free = GB
            return real(url)

        env.fetch = fetch
        self.assertEqual(env.run("--rate", "5"), 1)
        self.assertEqual(len(env.calls), sync.FLUSH_EVERY)
        self.assertEqual(env.last()["stopped_reason"], "low_disk")
        self.assertEqual(len(env.index()["renders"]), sync.FLUSH_EVERY)

    def test_renders_are_never_stat_ed(self):
        env = Env(self, [row("A"), row("B")])
        env.run()
        link = env.uploads / "card-renders/plPL/512x/A.webp"
        link.unlink()
        link.symlink_to(env.tmp / "gone-s3-object")  # dangling, like an offline S3 mount
        env.calls.clear()
        self.assertEqual(env.run(), 0)
        self.assertEqual(env.calls, [])
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(env.tmp / "gone-s3-object"))

    def test_changed_card_replaces_offload_symlink(self):
        env = Env(self, [row("A")])
        env.run()
        link = env.uploads / "card-renders/plPL/512x/A.webp"
        target = env.tmp / "s3-object"
        target.write_bytes(link.read_bytes())
        link.unlink()
        link.symlink_to(target)
        env.rows = [row("A", h="h2")]
        env.run()
        self.assertFalse(link.is_symlink())
        self.assertTrue(target.exists())

    def test_json_write_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.index.json"
            sync.write_json_atomic(path, {"ok": 1})
            with patch.object(sync.os, "fsync", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    sync.write_json_atomic(path, {"ok": 2})
            self.assertEqual(json.loads(path.read_text()), {"ok": 1})
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["x.index.json"])
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o644")

    def test_busy_lock_exits_zero(self):
        env = Env(self, [row("A")])
        env.var.mkdir()
        with open(env.var / ".lock", "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self.assertEqual(env.run(), 0)
        self.assertEqual(env.calls, [])
        self.assertFalse((env.var / "last-run.json").exists())

    def test_systemd_units_match_contract(self):
        root = Path(__file__).resolve().parents[1] / "systemd"
        service = (root / "kolodahs-card-renders.service").read_text()
        timer = (root / "kolodahs-card-renders.timer").read_text()
        for text in (service, timer):
            self.assertNotIn("global.lock", text)
            self.assertNotIn("sync.lock", text)
        for line in ("Type=oneshot", "User=koloda", "SuccessExitStatus=75",
                     "ProtectSystem=strict", "TimeoutStartSec=50min",
                     "OnFailure=notify-failure@%n.service"):
            self.assertIn(line, service)
        for line in ("OnCalendar=hourly", "RandomizedDelaySec=10min", "Persistent=true"):
            self.assertIn(line, timer)


if __name__ == "__main__":
    unittest.main()
