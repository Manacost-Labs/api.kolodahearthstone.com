#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.exit_codes import ExitCode  # noqa: E402
from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs  # noqa: E402


async def main() -> int:
    result = await refresh_all_deck_catalogs()
    print(json.dumps(result))
    if result["state"] == "ok":
        return int(ExitCode.OK)
    if result.get("datasets"):
        return int(ExitCode.DEGRADED)
    return int(ExitCode.ERROR)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
