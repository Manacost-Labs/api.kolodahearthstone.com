#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor


APP_ROOT = Path(__file__).resolve().parents[1]
CROP_DIRNAME = "constructed-crops"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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
        "autocommit": False,
    }
    if dsn.get("unix_socket"):
        kwargs.pop("host", None)
        kwargs["unix_socket"] = dsn["unix_socket"]
    if dsn.get("port"):
        kwargs["port"] = int(dsn["port"])
    return pymysql.connect(**kwargs)


def safe_card_id(card_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", card_id).strip("_") or "card"


def http_download(url: str, target: Path) -> None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "db.kolodahs.ru-constructed-image-backfill/1.0",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        target.write_bytes(resp.read())


def image_size(path: Path) -> tuple[int, int]:
    output = subprocess.check_output(["identify", "-format", "%w %h", str(path)], text=True)
    width, height = output.strip().split()
    return int(width), int(height)


def make_card_crop(source: Path, target: Path) -> None:
    width, height = image_size(source)
    crop_w = min(width, max(1, round(width * 0.602)))
    crop_h = min(height, max(1, round(height * 0.115)))
    crop_x = min(width - crop_w, max(0, round(width * 0.198)))
    crop_y = min(height - crop_h, max(0, round(height * 0.125)))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o755)
    subprocess.check_call(
        [
            "convert",
            str(source),
            "-alpha",
            "remove",
            "-alpha",
            "off",
            "-crop",
            f"{crop_w}x{crop_h}+{crop_x}+{crop_y}",
            "-resize",
            "243x64!",
            "-quality",
            "88",
            str(target),
        ]
    )


def backfill_golden(conn) -> int:
    now = utc_now()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE constructed_cards c
            INNER JOIN constructed_card_wiki_meta wm ON wm.card_id = c.card_id
            SET c.image_gold_url = JSON_UNQUOTE(JSON_EXTRACT(wm.golden_cards_json, '$[0].file_url')),
                c.changed_at = %s
            WHERE (c.image_gold_url IS NULL OR c.image_gold_url = '')
              AND JSON_LENGTH(wm.golden_cards_json) > 0
              AND JSON_UNQUOTE(JSON_EXTRACT(wm.golden_cards_json, '$[0].file_url')) IS NOT NULL
              AND JSON_UNQUOTE(JSON_EXTRACT(wm.golden_cards_json, '$[0].file_url')) <> ''
            """,
            (now,),
        )
        return int(cur.rowcount)


def crop_candidates(conn, limit: int | None) -> list[dict[str, Any]]:
    sql = """
        SELECT card_id, image_url
        FROM constructed_cards
        WHERE (local_crop_image_url IS NULL OR local_crop_image_url = '')
          AND (crop_image_url IS NULL OR crop_image_url = '')
          AND image_url IS NOT NULL AND image_url <> ''
        ORDER BY card_id ASC
    """
    params: list[Any] = []
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def backfill_crops(conn, config: dict[str, Any], limit: int | None, dry_run: bool) -> dict[str, int]:
    upload_dir = Path(str(config.get("upload_dir") or APP_ROOT / "uploads"))
    upload_url = str(config.get("upload_url") or "/uploads").rstrip("/")
    target_dir = upload_dir / CROP_DIRNAME
    rows = crop_candidates(conn, limit)
    stats = {"scanned": len(rows), "created": 0, "errors": 0}
    for row in rows:
        card_id = str(row["card_id"])
        rel_url = f"{upload_url}/{CROP_DIRNAME}/{safe_card_id(card_id)}.jpg"
        target = target_dir / f"{safe_card_id(card_id)}.jpg"
        if dry_run:
            print(f"{card_id} would_create {rel_url}")
            continue
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                source = Path(tmpdir) / "card.png"
                http_download(str(row["image_url"]), source)
                make_card_crop(source, target)
            target.chmod(0o644)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE constructed_cards
                    SET local_crop_image_url = %s,
                        changed_at = %s
                    WHERE card_id = %s
                    """,
                    (rel_url, utc_now(), card_id),
                )
            stats["created"] += 1
            print(f"{card_id} created {rel_url}")
        except Exception as exc:
            stats["errors"] += 1
            print(f"{card_id} error {exc}")
    return stats


def main() -> int:
    argp = argparse.ArgumentParser(description="Backfill constructed card crop and golden images.")
    argp.add_argument("--limit", type=int)
    argp.add_argument("--golden-only", action="store_true")
    argp.add_argument("--crop-only", action="store_true")
    argp.add_argument("--dry-run", action="store_true")
    args = argp.parse_args()

    config = load_php_config()
    conn = connect_db(config)
    try:
        summary: dict[str, Any] = {}
        if not args.crop_only:
            summary["golden_updated"] = backfill_golden(conn)
        if not args.golden_only:
            summary["crops"] = backfill_crops(conn, config, args.limit, args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if not summary.get("crops", {}).get("errors") else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
