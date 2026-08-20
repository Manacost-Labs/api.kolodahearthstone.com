#!/usr/bin/env python3
"""Compatibility entrypoint for the scheduled HSGuru streamer-decks job.

Keep the historic filename because installed systemd units reference it. The
actual provider chain, parsing, quality gates, publication, and locking live in
the shared application refresh path and must not be duplicated here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SOURCE_ID = "hsguru_streamer_decks_legend_1000"


def cli_main(argv: list[str]) -> int:
    from app.cli import main

    return main(argv)


def _refresh_derived_fun_decks() -> dict[str, Any]:
    from app.fun_decks import refresh_fun_decks

    try:
        return refresh_fun_decks(scheduled=True)
    except Exception as exc:  # noqa: BLE001 - derived dataset is best effort
        return {
            "ok": False,
            "error": "derived_fun_decks_refresh_failed",
            "error_type": type(exc).__name__,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule-id")
    args = parser.parse_args([] if argv is None else argv)
    refresh_args = [
        "refresh",
        "--source",
        SOURCE_ID,
        "--scheduled",
        "--require-all-ok",
    ]
    if args.schedule_id:
        refresh_args.extend(("--schedule-id", args.schedule_id))
    exit_code = cli_main(refresh_args)
    if exit_code != 0:
        return exit_code

    fun_decks = _refresh_derived_fun_decks()
    print(
        json.dumps(
            {"source_id": SOURCE_ID, "fun_decks": fun_decks},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if fun_decks.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
