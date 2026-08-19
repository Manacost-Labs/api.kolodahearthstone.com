#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import email.utils
import json
import os
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
# Создание кропа тем же запросом двигает changed_at, поэтому у свежесобранного
# файла обе отметки совпадают с точностью до секунды. Запас отсекает это
# самосрабатывание, оставляя видимыми настоящие правки карт.
STALE_TOLERANCE_SECONDS = 120


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


def remote_last_modified(url: str) -> float | None:
    """Когда CDN выложил этот рендер, или None если спросить не вышло."""
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "db.kolodahs.ru-constructed-image-backfill/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            header = resp.headers.get("Last-Modified")
    except Exception:
        return None
    if not header:
        return None
    try:
        return email.utils.parsedate_to_datetime(header).timestamp()
    except (TypeError, ValueError):
        return None


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
    # Пересборка затирает уже опубликованный кроп, поэтому convert пишет во
    # временный файл рядом с целью: сорванный запуск оставит прежнюю картинку
    # нетронутой вместо наполовину обрезанной.
    handle, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp.jpg")
    os.close(handle)
    tmp_path = Path(tmp_name)
    try:
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
                str(tmp_path),
            ]
        )
        if tmp_path.stat().st_size <= 0:
            raise RuntimeError("convert produced an empty crop")
        tmp_path.chmod(0o644)
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)


def backfill_golden(conn, refresh: bool) -> int:
    now = utc_now()
    wiki_url = "JSON_UNQUOTE(JSON_EXTRACT(wm.golden_cards_json, '$[0].file_url'))"
    # По умолчанию трогаем только пустые ссылки. С --refresh-golden адрес
    # переписывается и там, где вики уже отдаёт другой файл.
    target_filter = (
        f"c.image_gold_url IS NULL OR c.image_gold_url = '' OR c.image_gold_url <> {wiki_url}"
        if refresh
        else "c.image_gold_url IS NULL OR c.image_gold_url = ''"
    )
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE constructed_cards c
            INNER JOIN constructed_card_wiki_meta wm ON wm.card_id = c.card_id
            SET c.image_gold_url = {wiki_url},
                c.changed_at = %s
            WHERE ({target_filter})
              AND JSON_LENGTH(wm.golden_cards_json) > 0
              AND {wiki_url} IS NOT NULL
              AND {wiki_url} <> ''
            """,
            (now,),
        )
        return int(cur.rowcount)


def crop_rows(conn) -> list[dict[str, Any]]:
    sql = """
        SELECT card_id, image_url, local_crop_image_url, changed_at
        FROM constructed_cards
        WHERE (crop_image_url IS NULL OR crop_image_url = '')
          AND image_url IS NOT NULL AND image_url <> ''
        ORDER BY card_id ASC
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def changed_at_epoch(value: Any) -> float | None:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    return None


def crop_reason(row: dict[str, Any], target: Path, refresh: bool, force: bool) -> str | None:
    """Зачем пересобирать кроп этой карты — или None, если он уже актуален.

    Ответ "maybe_stale" ещё не окончательный: его подтверждает CDN, см.
    confirm_stale.
    """
    if not str(row.get("local_crop_image_url") or "").strip():
        return "missing_url"
    if not target.is_file() or target.stat().st_size <= 0:
        return "missing_file"
    if force:
        return "forced"
    if not refresh:
        return None
    changed = changed_at_epoch(row.get("changed_at"))
    if changed is None:
        return None
    # Карту правили уже после сборки кропа. Само по себе это ещё ничего не
    # значит: массовые заливки полей двигают changed_at тысячам карт, у которых
    # арт не трогали. Поэтому дальше спрашиваем CDN.
    if changed > target.stat().st_mtime + STALE_TOLERANCE_SECONDS:
        return "maybe_stale"
    return None


def confirm_stale(row: dict[str, Any], target: Path) -> str | None:
    """Отсеивает кандидатов, у которых на CDN лежит всё тот же старый рендер."""
    remote = remote_last_modified(str(row["image_url"]))
    if remote is None:
        # CDN не ответил или не отдал дату. Молча пересобирать нельзя: при сбое
        # это превратится в лавину закачек, поэтому пропускаем до следующего раза.
        return None
    if remote > target.stat().st_mtime:
        return "art_changed"
    return None


def backfill_crops(
    conn,
    config: dict[str, Any],
    limit: int | None,
    dry_run: bool,
    refresh: bool,
    force: bool,
) -> dict[str, Any]:
    upload_dir = Path(str(config.get("upload_dir") or APP_ROOT / "uploads"))
    upload_url = str(config.get("upload_url") or "/uploads").rstrip("/")
    target_dir = upload_dir / CROP_DIRNAME

    rows = crop_rows(conn)
    work: list[tuple[dict[str, Any], Path, str, str]] = []
    unchanged_art = 0
    for row in rows:
        card_id = str(row["card_id"])
        rel_url = f"{upload_url}/{CROP_DIRNAME}/{safe_card_id(card_id)}.jpg"
        target = target_dir / f"{safe_card_id(card_id)}.jpg"
        reason = crop_reason(row, target, refresh, force)
        if reason == "maybe_stale":
            reason = confirm_stale(row, target)
            if reason is None:
                unchanged_art += 1
                if not dry_run:
                    # Массовые заливки полей оставили тысячам карт changed_at
                    # новее их кропов. Без отметки о проверке эти карты
                    # дёргали бы CDN в каждом запуске. Сдвиг mtime говорит
                    # "сверено с CDN вот тогда" и гасит ложный кандидат.
                    os.utime(target)
        if reason is not None:
            work.append((row, target, rel_url, reason))

    truncated = 0
    if limit is not None and len(work) > limit:
        truncated = len(work) - limit
        work = work[:limit]

    stats: dict[str, Any] = {
        "scanned": len(rows),
        "created": 0,
        "refreshed": 0,
        "errors": 0,
        "skipped_by_limit": truncated,
        "art_unchanged": unchanged_art,
    }
    if truncated:
        print(f"limit reached, {truncated} more crops still pending")

    for row, target, rel_url, reason in work:
        card_id = str(row["card_id"])
        rebuild = reason not in ("missing_url", "missing_file")
        if dry_run:
            print(f"{card_id} would_{'refresh' if rebuild else 'create'} {rel_url} ({reason})")
            continue
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                source = Path(tmpdir) / "card.png"
                http_download(str(row["image_url"]), source)
                make_card_crop(source, target)
            # Пересборка не меняет адрес, поэтому строку трогаем только когда
            # ссылка действительно новая — иначе карта без правок всплывёт в
            # панели как «изменённая сегодня».
            if str(row.get("local_crop_image_url") or "") != rel_url:
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
            if rebuild:
                stats["refreshed"] += 1
            else:
                stats["created"] += 1
            print(f"{card_id} {'refreshed' if rebuild else 'created'} {rel_url} ({reason})")
        except Exception as exc:
            stats["errors"] += 1
            print(f"{card_id} error {exc}")
    return stats


def main() -> int:
    argp = argparse.ArgumentParser(description="Backfill and refresh constructed card crop and golden images.")
    argp.add_argument("--limit", type=int, help="Maximum number of crops to build in one run.")
    argp.add_argument("--golden-only", action="store_true")
    argp.add_argument("--crop-only", action="store_true")
    argp.add_argument("--dry-run", action="store_true")
    argp.add_argument(
        "--no-refresh",
        dest="refresh",
        action="store_false",
        help="Only build missing crops, leave stale ones from before a card change alone.",
    )
    argp.add_argument(
        "--force",
        action="store_true",
        help="Rebuild every crop regardless of how fresh it is.",
    )
    argp.add_argument(
        "--refresh-golden",
        action="store_true",
        help="Also repoint golden URLs that already have a value but differ from the wiki.",
    )
    args = argp.parse_args()

    config = load_php_config()
    conn = connect_db(config)
    try:
        summary: dict[str, Any] = {}
        if not args.crop_only:
            summary["golden_updated"] = backfill_golden(conn, args.refresh_golden)
        if not args.golden_only:
            summary["crops"] = backfill_crops(
                conn, config, args.limit, args.dry_run, args.refresh, args.force
            )
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
