#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor


APP_ROOT = Path(__file__).resolve().parents[1]


def load_php_config() -> dict[str, Any]:
    code = f"echo json_encode(require {json.dumps(str(APP_ROOT / 'config.php'))}, JSON_UNESCAPED_SLASHES);"
    output = subprocess.check_output(["php", "-r", code], text=True)
    config = json.loads(output)
    if not isinstance(config, dict) or "db" not in config:
        raise RuntimeError("config.php does not contain db settings")
    return config


def parse_mysql_dsn(dsn: str) -> dict[str, str]:
    if not dsn.startswith("mysql:"):
        raise RuntimeError(f"Unsupported DSN: {dsn}")
    parts: dict[str, str] = {}
    for part in dsn[len("mysql:") :].split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            parts[key] = value
    return parts


def connect_db(config: dict[str, Any]):
    db = config["db"]
    dsn = parse_mysql_dsn(str(db["dsn"]))
    kwargs: dict[str, Any] = {
        "host": dsn.get("host", "localhost"),
        "user": db["user"],
        "password": db["password"],
        "database": dsn.get("dbname"),
        "charset": dsn.get("charset", "utf8mb4"),
        "cursorclass": DictCursor,
        "autocommit": True,
    }
    if dsn.get("unix_socket"):
        kwargs.pop("host", None)
        kwargs["unix_socket"] = dsn["unix_socket"]
    if dsn.get("port"):
        kwargs["port"] = int(dsn["port"])
    return pymysql.connect(**kwargs)


def url_ok(url: str | None) -> bool:
    if not url:
        return False
    if url.startswith("/"):
        url = "https://api.kolodahearthstone.com" + url
    headers = {"User-Agent": "db.kolodahs.ru-library-image-audit/1.0"}
    for method, extra_headers in (("HEAD", {}), ("GET", {"Range": "bytes=0-16"})):
        try:
            req = urllib.request.Request(url, headers=headers | extra_headers, method=method)
            with urllib.request.urlopen(req, timeout=8) as resp:
                return 200 <= int(resp.status) < 400
        except Exception:
            continue
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Battlegrounds auxiliary library image URLs.")
    parser.add_argument("--library", default="all")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    where = ""
    params: tuple[Any, ...] = ()
    if args.library != "all":
        where = " WHERE library = %s"
        params = (args.library,)

    conn = connect_db(load_php_config())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT library, card_id, dbf, name_ru, image_url, local_full_art_url FROM battlegrounds_library_cards" + where,
                params,
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    broken: list[dict[str, Any]] = []
    broken_full_art: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(url_ok, row.get("image_url")): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            if not future.result():
                broken.append(row)
        full_art_futures = {
            executor.submit(url_ok, row.get("local_full_art_url")): row
            for row in rows
            if row.get("library") == "trinket"
        }
        for future in as_completed(full_art_futures):
            row = full_art_futures[future]
            if not future.result():
                broken_full_art.append(row)

    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        library = str(row.get("library") or "")
        counts.setdefault(library, {"total": 0, "with_image": 0, "with_full_art": 0})
        counts[library]["total"] += 1
        if row.get("image_url"):
            counts[library]["with_image"] += 1
        if row.get("local_full_art_url"):
            counts[library]["with_full_art"] += 1

    result = {
        "library": args.library,
        "counts": counts,
        "broken_count": len(broken),
        "broken": broken[:50],
        "broken_full_art_count": len(broken_full_art),
        "broken_full_art": broken_full_art[:50],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    return 1 if broken or broken_full_art else 0


if __name__ == "__main__":
    raise SystemExit(main())
