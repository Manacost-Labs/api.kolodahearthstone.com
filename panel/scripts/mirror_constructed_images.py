#!/opt/wiki-hs-parser/.venv/bin/python
"""Складывает рендеры карт Стандарта и Вольного на свой диск.

API уже предпочитает local_image_url и local_gold_image_url, а на внешний CDN
падает только когда локальной копии нет. Поэтому достаточно скачать файл и
проставить путь — отдача переключится сама.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import email.utils
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from backfill_constructed_images import (
    connect_db,
    load_php_config,
    safe_card_id,
    utc_now,
)

APP_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = APP_ROOT / "var" / "sync" / "constructed-mirror-state.json"
NORMAL_DIRNAME = "constructed-related"
GOLDEN_DIRNAME = "constructed-golden"
USER_AGENT = "kolodahearthstone.com-image-mirror/1.0 (+https://kolodahearthstone.ru)"

# wiki.gg — небольшой community-хост, его грузим бережно; CloudFront выдержит
# и больше потоков.
HOST_LIMITS = {
    "hearthstone.wiki.gg": {"workers": 2, "delay": 0.35},
    "*": {"workers": 8, "delay": 0.0},
}

KINDS = {
    "normal": {
        "url_column": "image_url",
        "local_column": "local_image_url",
        "dirname": NORMAL_DIRNAME,
    },
    "golden": {
        "url_column": "image_gold_url",
        "local_column": "local_gold_image_url",
        "dirname": GOLDEN_DIRNAME,
    },
}

_throttle_lock = threading.Lock()
_next_slot: dict[str, float] = {}


def host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def limits_for(host: str) -> dict[str, Any]:
    return HOST_LIMITS.get(host, HOST_LIMITS["*"])


def throttle(host: str) -> None:
    """Разносит запросы к одному хосту, чтобы не долбить его пачкой.

    Интервал держится независимо от числа потоков: слот резервируется под
    замком, а ждёт поток уже сам по себе.
    """
    delay = limits_for(host)["delay"]
    if delay <= 0:
        return
    with _throttle_lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(host, now))
        _next_slot[host] = slot + delay
    pause = slot - time.monotonic()
    if pause > 0:
        time.sleep(pause)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "assets": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "assets": {}}
    if not isinstance(data, dict) or not isinstance(data.get("assets"), dict):
        return {"version": 1, "assets": {}}
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["version"] = 1
    state["updated_at"] = utc_now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_hash_from_url(url: str) -> str | None:
    """CDN Blizzard адресуется по sha256 содержимого — вытаскиваем его из пути."""
    name = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    return stem if len(stem) == 64 and all(ch in "0123456789abcdef" for ch in stem) else None


def remote_last_modified(url: str) -> float | None:
    throttle(host_of(url))
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            header = resp.headers.get("Last-Modified")
        return email.utils.parsedate_to_datetime(header).timestamp() if header else None
    except Exception:
        return None


def local_is_current(target: Path, source_url: str, entry: Any) -> bool:
    """Отвечает ли лежащий файл текущему адресу источника.

    Дёшевый путь — сверка запомненного адреса: он меняется всякий раз, когда
    меняется картинка. Если записи нет (файл скачан до появления состояния),
    подтверждаем иначе: у Blizzard хешем из пути, у остальных датой на сервере.
    """
    if isinstance(entry, dict) and entry.get("source_url") == source_url:
        return True
    expected = content_hash_from_url(source_url)
    if expected is not None:
        return sha256_file(target) == expected
    remote = remote_last_modified(source_url)
    # Хост не ответил — считаем копию годной, чтобы сбой связи не превратился
    # в перекачку всей библиотеки.
    return True if remote is None else remote <= target.stat().st_mtime


def candidates(conn, kind: str, force: bool) -> list[dict[str, Any]]:
    spec = KINDS[kind]
    local_col = spec["local_column"]
    url_col = spec["url_column"]
    where = f"c.{url_col} IS NOT NULL AND c.{url_col} <> ''"
    sql = f"""
        SELECT c.card_id, c.{url_col} AS source_url, c.{local_col} AS local_url
        FROM constructed_cards c
        WHERE {where}
        ORDER BY c.card_id ASC
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def download(url: str, target: Path) -> int:
    """Качает во временный файл рядом с целью и подменяет одним движением."""
    throttle(host_of(url))
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/png,image/*;q=0.8,*/*;q=0.5"},
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    os.close(handle)
    tmp_path = Path(tmp_name)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            payload = resp.read()
        if not payload:
            raise RuntimeError("empty response")
        tmp_path.write_bytes(payload)
        # Заглушка или HTML с ошибкой не должны осесть на диске под видом карты.
        subprocess.check_output(
            ["identify", "-format", "%m %w %h", str(tmp_path)],
            stderr=subprocess.STDOUT,
        )
        tmp_path.chmod(0o644)
        os.replace(tmp_path, target)
        return len(payload)
    finally:
        tmp_path.unlink(missing_ok=True)


def mirror(conn, config: dict[str, Any], kind: str, limit: int | None,
           dry_run: bool, force: bool, state: dict[str, Any],
           seed_state: bool) -> dict[str, Any]:
    spec = KINDS[kind]
    upload_dir = Path(str(config.get("upload_dir") or APP_ROOT / "uploads"))
    upload_url = str(config.get("upload_url") or "/uploads").rstrip("/")
    target_dir = upload_dir / spec["dirname"]

    rows = candidates(conn, kind, force)
    work: list[tuple[dict[str, Any], Path, str, bool]] = []
    fresh = 0
    for row in rows:
        card_id = str(row["card_id"])
        source_url = str(row["source_url"])
        rel_url = f"{upload_url}/{spec['dirname']}/{safe_card_id(card_id)}.png"
        target = target_dir / f"{safe_card_id(card_id)}.png"
        key = f"{kind}:{card_id}"
        on_disk = target.is_file() and target.stat().st_size > 0

        if force or not on_disk:
            work.append((row, target, rel_url, False))
            continue
        if seed_state:
            # Файл только что скачан по этому адресу, проверять нечего.
            state["assets"][key] = {"source_url": source_url, "local_url": rel_url}
            if str(row["local_url"] or "") != rel_url:
                work.append((row, target, rel_url, True))
            continue
        if local_is_current(target, source_url, state["assets"].get(key)):
            state["assets"][key] = {"source_url": source_url, "local_url": rel_url}
            fresh += 1
            # В базе путь мог быть не проставлен — тогда всё же дозаписываем.
            if str(row["local_url"] or "") != rel_url:
                work.append((row, target, rel_url, True))
            continue
        # Источник отдаёт уже другую картинку: локальная копия устарела.
        work.append((row, target, rel_url, False))

    truncated = 0
    if limit is not None and len(work) > limit:
        truncated = len(work) - limit
        work = work[:limit]

    stats: dict[str, Any] = {
        "candidates": len(rows),
        "up_to_date": fresh,
        "downloaded": 0,
        "already_on_disk": 0,
        "errors": 0,
        "bytes": 0,
        "skipped_by_limit": truncated,
    }
    if dry_run:
        for row, _target, rel_url, on_disk in work:
            print(f"{row['card_id']} {'would_link' if on_disk else 'would_download'} {rel_url}")
        return stats
    if not work:
        return stats

    source_by_card = {str(row["card_id"]): str(row["source_url"]) for row, *_ in work}
    hosts = {host_of(str(row["source_url"])) for row, *_ in work}
    workers = max(limits_for(h)["workers"] for h in hosts)
    done: list[tuple[str, str]] = []

    def handle(item):
        row, target, rel_url, on_disk = item
        card_id = str(row["card_id"])
        if on_disk:
            return ("linked", card_id, rel_url, 0, None)
        try:
            return ("downloaded", card_id, rel_url, download(str(row["source_url"]), target), None)
        except Exception as exc:
            return ("error", card_id, rel_url, 0, str(exc))

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for outcome, card_id, rel_url, size, error in pool.map(handle, work):
            if outcome == "error":
                stats["errors"] += 1
                print(f"{card_id} error {error}")
                continue
            if outcome == "linked":
                stats["already_on_disk"] += 1
            else:
                stats["downloaded"] += 1
                stats["bytes"] += size
            state["assets"][f"{kind}:{card_id}"] = {
                "source_url": source_by_card[card_id],
                "local_url": rel_url,
            }
            done.append((card_id, rel_url))
            print(f"{card_id} {outcome} {rel_url}")

    local_col = spec["local_column"]
    with conn.cursor() as cur:
        for i in range(0, len(done), 200):
            batch = done[i : i + 200]
            cur.executemany(
                f"UPDATE constructed_cards SET {local_col} = %s, updated_at = %s WHERE card_id = %s",
                [(rel_url, utc_now(), card_id) for card_id, rel_url in batch],
            )
            conn.commit()
    return stats


def main() -> int:
    argp = argparse.ArgumentParser(description="Mirror constructed card images onto local storage.")
    argp.add_argument("--kind", choices=["normal", "golden", "all"], default="all")
    argp.add_argument("--limit", type=int)
    argp.add_argument("--dry-run", action="store_true")
    argp.add_argument("--force", action="store_true",
                      help="Re-download even when a local copy already exists.")
    argp.add_argument("--seed-state", action="store_true",
                      help="Trust the files already on disk and just record their source "
                           "URLs, without verifying anything over the network.")
    args = argp.parse_args()

    config = load_php_config()
    state = load_state(STATE_FILE)
    conn = connect_db(config)
    try:
        kinds = ["normal", "golden"] if args.kind == "all" else [args.kind]
        summary: dict[str, Any] = {}
        for kind in kinds:
            summary[kind] = mirror(conn, config, kind, args.limit, args.dry_run,
                                   args.force, state, args.seed_state)
        if not args.dry_run:
            save_state(STATE_FILE, state)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if not any(s["errors"] for s in summary.values()) else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
