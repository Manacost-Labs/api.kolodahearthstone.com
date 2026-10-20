from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from .store import Store


def main() -> int:
    parser = argparse.ArgumentParser(description="Meta Intelligence Lab maintenance; never publishes to WordPress")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("sync", help="Import existing published datasets without network requests")
    sub.add_parser("signals", help="Recompute rules without network requests")
    backup = sub.add_parser("backup")
    backup.add_argument("destination", type=Path)
    balance = sub.add_parser("opening-balance", help="Record verified HSGuru credits spent before ledger tracking; once per month")
    balance.add_argument("--month", required=True)
    balance.add_argument("--credits", type=int, required=True)
    for name in ("hot", "details"):
        command = sub.add_parser(name)
        command.add_argument("--patch", default=os.getenv("HS_META_LAB_PATCH"))
    args = parser.parse_args()
    store = Store()
    if args.command == "migrate":
        with store.connect(write=True):
            pass
        result = {"schema_version": 1}
    elif args.command == "backup":
        store.backup(args.destination)
        result = {"backup": str(args.destination), "integrity": "ok"}
    elif args.command == "opening-balance":
        from .budget import opening_balance
        opening_balance(store, args.month, args.credits)
        result = {"month": args.month, "opening_credits": args.credits}
    elif args.command == "sync":
        from .collectors import sync_existing
        result = sync_existing(store)
    elif args.command == "signals":
        from .signals import recompute
        result = {"signals": recompute(store)}
    else:
        from .collectors import run
        result = asyncio.run(run(args.command, args.patch, store))
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("state") == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
