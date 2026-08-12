#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402


README = ROOT / "README.md"
METHODS = {"get", "post", "put", "patch", "delete", "head"}
ROW_PATTERN = re.compile(
    r"^\|\s*(GET|POST|PUT|PATCH|DELETE|HEAD)\s*\|\s*`([^`]+)`\s*\|",
    re.MULTILINE,
)


def main() -> int:
    text = README.read_text(encoding="utf-8")
    documented = {(method, path) for method, path in ROW_PATTERN.findall(text)}
    expected = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method in METHODS
    }
    expected.add(("HEAD", "/datasets/{source_id}"))

    missing = sorted(expected - documented)
    if missing:
        print("README.md is missing API endpoints:", file=sys.stderr)
        for method, path in missing:
            print(f"  {method} {path}", file=sys.stderr)
        return 1

    print(f"README endpoint inventory: ok ({len(expected)} OpenAPI/hidden operations).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
