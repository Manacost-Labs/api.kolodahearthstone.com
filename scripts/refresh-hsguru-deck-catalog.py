#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.hsguru_deck_catalog_refresh import refresh_all_deck_catalogs  # noqa: E402


async def main() -> None:
    result = await refresh_all_deck_catalogs()
    print(json.dumps(result))
    if result["state"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
