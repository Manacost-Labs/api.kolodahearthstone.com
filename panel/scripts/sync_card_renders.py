#!/usr/bin/env python3
"""Download localized HearthstoneJSON card renders as WebP (E24, api#121).

Plan-only without --apply (no network, no writes). The plan is built from the
state files only: rendered files are never stat()-ed, because after the S3
offload they are symlinks into an S3 mount. The index is the source of truth.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import signal
import sys
import time
import urllib.error
import urllib.request
import uuid
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

APP_ROOT = Path(__file__).resolve().parents[1]
LOCALES = ("ruRU", "enUS", "plPL", "esES")
SIZE = "512x"
SKIP_TYPES = (
    "ENCHANTMENT",
    "PET",
    "MOVE_MINION_HOVER_TARGET",
    "BATTLEGROUND_HERO_BUDDY",
    "",
)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
URL = "https://art.hearthstonejson.com/v1/render/latest/{locale}/{size}/{card_id}.png"
USER_AGENT = "KolodaCardRenders/1.0"
TIMEOUT = 30
MAX_BODY = 8 * 1024 * 1024
FLUSH_EVERY = 500
MAX_CONSECUTIVE_ERRORS = 20
MAX_RETRY_AFTER = 24 * 3600
EXIT_UPSTREAM = 75
GB = 1024**3
SQL = """SELECT l.card_id, l.locale, l.source_hash, c.collectible, c.card_type
FROM hearthstone_card_localizations l
JOIN hearthstone_card_catalog c ON c.card_id = l.card_id
WHERE l.locale IN ({marks})"""


class FetchError(Exception):
    """Network failure, timeout or unusable response (not a 404)."""


class BadImage(Exception):
    """HSJ answered 200 but the body is not a usable 512 px PNG."""


def utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def rate_arg(value: str) -> float:
    try:
        rate = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--rate must be a number") from exc
    if not 0.5 <= rate <= 5:
        raise argparse.ArgumentTypeError("--rate must be between 0.5 and 5")
    return rate


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{uuid.uuid4().hex}-{path.name}")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def retry_after_seconds(value: str | None, now: datetime) -> float:
    """Retry-After as delta-seconds or HTTP-date, clamped to 0..24 h."""
    if not value:
        return 0.0
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value.strip()) - now).total_seconds()
        except (TypeError, ValueError):
            return 0.0
    if delay != delay:  # NaN
        return 0.0
    return min(max(delay, 0.0), float(MAX_RETRY_AFTER))


def http_fetch(url: str) -> tuple[int, bytes, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_BODY + 1), None
    except urllib.error.HTTPError as exc:
        return exc.code, b"", exc.headers.get("Retry-After")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FetchError(str(exc)) from exc


def load_rows_from_db(locales: tuple[str, ...]) -> list[dict[str, Any]]:
    from sync_constructed_related_cards import connect_db, load_php_config

    conn = connect_db(load_php_config())
    try:
        with conn.cursor() as cursor:
            cursor.execute(SQL.format(marks=",".join(["%s"] * len(locales))), locales)
            return list(cursor.fetchall())
    finally:
        conn.close()


def to_webp(body: bytes) -> bytes:
    from PIL import Image

    if len(body) > MAX_BODY:
        raise BadImage("response too large")
    try:
        image = Image.open(io.BytesIO(body))
        image.load()
    except Exception as exc:
        raise BadImage(f"invalid image: {exc}") from exc
    if image.format != "PNG" or image.width != 512:
        raise BadImage(f"unexpected image {image.format} width {image.width}")
    out = io.BytesIO()
    image.convert("RGBA").save(out, format="WEBP", quality=85, method=4)
    return out.getvalue()


def write_render(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{uuid.uuid4().hex}.webp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.chmod(tmp, 0o644)
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_plan(
    rows: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
    now: datetime,
    recheck_days: float,
    locales: tuple[str, ...],
) -> tuple[list[tuple[str, str, str, str]], dict[str, dict[str, int]]]:
    """Return [(card_id, locale, source_hash, reason)] and per-locale counters."""
    counts = {
        loc: {"new": 0, "changed": 0, "recheck_missing": 0, "up_to_date": 0,
              "skipped_type": 0, "skipped_invalid_id": 0}
        for loc in locales
    }
    ordered = []
    seen = set()
    for row in rows:
        locale = row["locale"]
        card_id = str(row["card_id"])
        if locale not in counts or (card_id, locale) in seen:
            continue
        seen.add((card_id, locale))
        if not ID_RE.match(card_id):
            counts[locale]["skipped_invalid_id"] += 1
            continue
        if (row.get("card_type") or "") in SKIP_TYPES:
            counts[locale]["skipped_type"] += 1
            continue
        item = states[locale]["items"].get(card_id)
        source_hash = str(row["source_hash"])
        reason = None
        if item is None:
            reason = "new"
        elif item.get("status") in ("bad", 404):
            checked = parse_iso(item["checked_at"])
            if item.get("source_hash") != source_hash:
                reason = "recheck_missing"
            elif now - checked >= timedelta(days=recheck_days):
                reason = "recheck_missing"
        elif item.get("source_hash") != source_hash:
            reason = "changed"
        if reason is None:
            counts[locale]["up_to_date"] += 1
            continue
        counts[locale][reason] += 1
        collectible = 0 if int(row.get("collectible") or 0) else 1
        ordered.append((collectible, card_id, locales.index(locale), source_hash, reason))
    ordered.sort(key=lambda t: t[:3])
    return [(c, locales[li], h, r) for _, c, li, h, r in ordered], counts


def build_index(locale: str, state: dict[str, Any], now: datetime) -> dict[str, Any]:
    items = state["items"]
    return {
        "version": 1,
        "locale": locale,
        "size": SIZE,
        "updated_at": utc_iso(now),
        "skip_types": list(SKIP_TYPES),
        "renders": {i: v["v"] for i, v in sorted(items.items()) if "v" in v},
        "missing": sorted(i for i, v in items.items() if v.get("status") == 404),
    }


def main(
    argv: list[str] | None = None,
    *,
    fetch: Callable[[str], tuple[int, bytes, str | None]] = http_fetch,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="download; default is a dry-run plan")
    parser.add_argument("--rows-file", help="JSON rows instead of the database")
    parser.add_argument("--rate", type=rate_arg, default=3.0, help="requests/s, 0.5-5")
    parser.add_argument("--max-requests", type=int, default=6000)
    parser.add_argument("--max-seconds", type=float, default=2400.0,
                        help="wall-clock budget of a run (default 40 min)")
    parser.add_argument("--min-free-gb", type=float, default=20.0)
    parser.add_argument("--recheck-missing-days", type=float, default=30.0)
    parser.add_argument("--locales", default=",".join(LOCALES))
    parser.add_argument("--uploads-dir", type=Path, default=APP_ROOT / "uploads")
    parser.add_argument("--var-dir", type=Path, default=APP_ROOT / "var" / "card-renders")
    args = parser.parse_args(argv)
    locales = tuple(x for x in args.locales.split(",") if x)
    if not locales or any(x not in LOCALES for x in locales):
        parser.error(f"--locales must be a subset of {','.join(LOCALES)}")
    var_dir: Path = args.var_dir
    started = utcnow()

    if args.rows_file:
        rows = json.loads(Path(args.rows_file).read_text(encoding="utf-8"))
    else:
        rows = load_rows_from_db(locales)

    lock_handle = None
    if args.apply:
        var_dir.mkdir(parents=True, exist_ok=True)
        lock_handle = open(var_dir / ".lock", "a+")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("card renders: another run holds the lock, nothing to do")
            lock_handle.close()
            return 0
    try:
        return run(args, rows, locales, started, fetch, clock, sleep, utcnow)
    finally:
        if lock_handle:
            lock_handle.close()


def run(args, rows, locales, started, fetch, clock, sleep, utcnow) -> int:
    var_dir: Path = args.var_dir
    states = {}
    for loc in locales:
        state = read_json(var_dir / f"{loc}.state.json", None) or {}
        state.setdefault("version", 1)
        state["locale"] = loc
        state.setdefault("items", {})
        states[loc] = state
    plan, counts = build_plan(rows, states, started, args.recheck_missing_days, locales)

    if not args.apply:
        print(f"card renders plan (dry-run): {len(plan)} requests, no network, no writes")
        for loc in locales:
            print(f"  {loc}: " + " ".join(f"{k}={v}" for k, v in counts[loc].items()))
        return 0

    stats = {"requests": 0, "downloaded": 0, "not_found": 0, "bad_image": 0, "errors": 0,
             "bytes_written": 0}
    skipped_invalid = sum(c["skipped_invalid_id"] for c in counts.values())
    last_run_path = var_dir / "last-run.json"
    result: dict[str, Any] = {"stopped_reason": "done"}
    dirty: set[str] = set()

    def write_last_run() -> None:
        finished = utcnow()
        write_json_atomic(last_run_path, {
            "started_at": utc_iso(started),
            "finished_at": utc_iso(finished),
            **stats,
            "skipped_invalid_id": skipped_invalid,
            "planned": len(plan),
            "avg_webp_bytes": (stats["bytes_written"] // stats["downloaded"]
                               if stats["downloaded"] else 0),
            **result,
        })

    def flush() -> None:
        now = utcnow()
        for loc in sorted(dirty):
            # Index first: a crash between the two files only causes a re-download.
            write_json_atomic(var_dir / f"{loc}.index.json", build_index(loc, states[loc], now))
            write_json_atomic(var_dir / f"{loc}.state.json", states[loc])
        dirty.clear()

    def low_disk() -> bool:
        return shutil.disk_usage(args.uploads_dir).free < args.min_free_gb * GB

    # Retry-After from a previous 429 is honoured across runs.
    previous = read_json(last_run_path, {})
    until = previous.get("retry_after_until")
    if until and parse_iso(until) > started:
        result = {"stopped_reason": "upstream_errors", "retry_after_until": until}
        write_last_run()
        print(f"card renders: Retry-After until {until}, skipping run")
        return EXIT_UPSTREAM

    if not args.uploads_dir.is_dir():
        result = {"stopped_reason": "bad_uploads_dir"}
        write_last_run()
        print(f"card renders: --uploads-dir {args.uploads_dir} is not a directory",
              file=sys.stderr)
        return 2

    if low_disk():
        result = {"stopped_reason": "low_disk"}
        write_last_run()
        print(f"card renders: less than {args.min_free_gb} GB free, stopping")
        return 1

    for loc in locales:  # make sure every locale has an index
        if not (var_dir / f"{loc}.index.json").exists():
            dirty.add(loc)

    interrupted: list[int] = []

    def on_signal(signum, _frame):
        interrupted.append(signum)

    old_handlers = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            old_handlers[sig] = signal.signal(sig, on_signal)
        except ValueError:  # not the main thread
            pass
    run_started = clock()
    next_at = run_started
    consecutive = 0
    processed = 0
    code = 0
    try:
        for card_id, loc, source_hash, _reason in plan:
            if interrupted:
                result = {"stopped_reason": "signal"}
                break
            if stats["requests"] >= args.max_requests:
                result = {"stopped_reason": "budget"}
                break
            now_m = clock()
            if next_at > now_m:
                sleep(next_at - now_m)
                now_m = next_at
            if now_m - run_started >= args.max_seconds:
                result = {"stopped_reason": "time"}
                break
            next_at = now_m + 1.0 / args.rate
            stats["requests"] += 1
            stop = None
            try:
                status, body, retry_after = fetch(
                    URL.format(locale=loc, size=SIZE, card_id=card_id))
                if status == 429:
                    stats["errors"] += 1
                    delay = retry_after_seconds(retry_after, utcnow())
                    stop = {"stopped_reason": "upstream_errors"}
                    if delay:
                        stop["retry_after_until"] = utc_iso(utcnow() + timedelta(seconds=delay))
                elif status == 404:
                    states[loc]["items"][card_id] = {
                        "status": 404, "checked_at": utc_iso(utcnow()),
                        "source_hash": source_hash}
                    stats["not_found"] += 1
                    consecutive = 0
                    dirty.add(loc)
                elif status == 200:
                    webp = to_webp(body)
                    write_render(args.uploads_dir / "card-renders" / loc / SIZE / f"{card_id}.webp", webp)
                    states[loc]["items"][card_id] = {
                        "source_hash": source_hash,
                        "v": hashlib.sha256(webp).hexdigest()[:12],
                        "bytes": len(webp), "fetched_at": utc_iso(utcnow())}
                    stats["downloaded"] += 1
                    stats["bytes_written"] += len(webp)
                    consecutive = 0
                    dirty.add(loc)
                else:
                    raise FetchError(f"HTTP {status}")
            except BadImage as exc:
                # Not an upstream outage: remember it like a 404 (backoff), do not
                # count towards the consecutive-error stop.
                states[loc]["items"][card_id] = {
                    "status": "bad", "checked_at": utc_iso(utcnow()),
                    "source_hash": source_hash}
                stats["bad_image"] += 1
                consecutive = 0
                dirty.add(loc)
                print(f"card renders: {loc}/{card_id}: {exc}", file=sys.stderr)
            except FetchError as exc:
                stats["errors"] += 1
                consecutive += 1
                print(f"card renders: {loc}/{card_id}: {exc}", file=sys.stderr)
                if consecutive >= MAX_CONSECUTIVE_ERRORS:
                    stop = {"stopped_reason": "upstream_errors"}
            if stop:
                result = stop
                code = EXIT_UPSTREAM
                break
            processed += 1
            if processed % FLUSH_EVERY == 0:
                flush()
                if low_disk():
                    result = {"stopped_reason": "low_disk"}
                    code = 1
                    break
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        flush()
    write_last_run()
    print("card renders: " + json.dumps({**stats, **result}))
    return code


if __name__ == "__main__":
    sys.exit(main())
