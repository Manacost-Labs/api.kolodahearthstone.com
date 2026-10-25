"""``python -m app.balance_radar run|show|runs``: the timer entrypoint.

Exit codes follow the scheduled jobs: 0 ok, 10 degraded (one format failed or
a social source was unavailable), 1 failed.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import sys

from ..storage import root_dir
from . import store
from .pipeline import FORMATS, run_balance_radar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.balance_radar")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="collect signals, analyse and store one run per format"
    )
    run.add_argument("--format", action="append", choices=FORMATS, dest="formats")
    run.add_argument(
        "--force", action="store_true", help="analyse even when inputs are unchanged"
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="build model inputs without calling Claude",
    )
    run.add_argument(
        "--no-social", action="store_true", help="skip the paid social APIs"
    )
    run.add_argument("--dump-dir", help="with --dry-run, write model inputs here")
    show = commands.add_parser("show", help="print the latest successful run")
    show.add_argument("--format", choices=FORMATS, default="standard")
    runs = commands.add_parser("runs", help="list recent runs")
    runs.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    if args.command == "show":
        latest = store.latest_ok(args.format)
        if latest is None:
            print("no successful run", file=sys.stderr)
            return 1
        print(json.dumps(latest["payload"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "runs":
        rows, _total = store.list_runs(limit=args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    with (root_dir() / "balance-radar.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("balance radar: another run is in progress", file=sys.stderr)
            return 10
        summary = run_balance_radar(
            tuple(args.formats or FORMATS),
            dry_run=args.dry_run,
            force=args.force,
            collect=not args.no_social,
            dry_run_dir=args.dump_dir,
        )
    print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))
    return summary.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
