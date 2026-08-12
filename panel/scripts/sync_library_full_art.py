#!/opt/wiki-hs-parser/.venv/bin/python
"""Download original full art for Battlegrounds auxiliary libraries.

The framed card image and the square illustration are deliberately stored in
separate fields.  HearthstoneJSON's ``v1/orig`` endpoint returns the original
512x512 illustration without card frame, text, or locale-dependent overlays.
Files are mirrored locally so the public API never depends on the availability
or network route of a third-party image host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from sync_library_cards import connect_db, load_php_config, utc_now


APP_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = APP_ROOT / "uploads" / "library-full-art"
UPLOAD_URL = "/uploads/library-full-art"
HSJ_ORIG_BASE = "https://art.hearthstonejson.com/v1/orig/"
USER_AGENT = "db.kolodahs.ru-library-full-art-sync/1.0"
SUPPORTED_LIBRARIES = {"trinket"}


def ensure_schema(conn) -> None:
    """Add full-art metadata without changing the existing card-image fields."""
    definitions = {
        "full_art_source": "VARCHAR(32) DEFAULT NULL AFTER crop_image_url",
        "full_art_source_url": "VARCHAR(512) DEFAULT NULL AFTER full_art_source",
        "local_full_art_url": "VARCHAR(512) DEFAULT NULL AFTER full_art_source_url",
        "full_art_width": "SMALLINT UNSIGNED DEFAULT NULL AFTER local_full_art_url",
        "full_art_height": "SMALLINT UNSIGNED DEFAULT NULL AFTER full_art_width",
        "full_art_size": "INT UNSIGNED DEFAULT NULL AFTER full_art_height",
        "full_art_sha1": "CHAR(40) DEFAULT NULL AFTER full_art_size",
        "full_art_mime": "VARCHAR(64) DEFAULT NULL AFTER full_art_sha1",
        "full_art_fetched_at": "TIMESTAMP NULL DEFAULT NULL AFTER full_art_mime",
    }
    with conn.cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM battlegrounds_library_cards")
        columns = {str(row["Field"]) for row in cursor.fetchall()}
        for column, definition in definitions.items():
            if column not in columns:
                cursor.execute(
                    f"ALTER TABLE battlegrounds_library_cards ADD COLUMN `{column}` {definition}"
                )
    conn.commit()


def safe_card_id(card_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", card_id).strip("._")
    if not safe:
        raise ValueError(f"Unsafe empty card id: {card_id!r}")
    return safe


def png_dimensions(content: bytes) -> tuple[int, int]:
    if len(content) < 24 or not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("The full-art response is not a PNG image")
    width, height = struct.unpack(">II", content[16:24])
    if width < 256 or height < 256:
        raise RuntimeError(f"The full-art image is unexpectedly small: {width}x{height}")
    if not 0.8 <= width / height <= 1.25:
        raise RuntimeError(f"The full-art image is not square enough: {width}x{height}")
    return width, height


def read_local(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    width, height = png_dimensions(content)
    return {
        "width": width,
        "height": height,
        "size": len(content),
        "sha1": hashlib.sha1(content).hexdigest(),
        "mime": "image/png",
    }


def download(card: dict[str, Any], refresh: bool, dry_run: bool) -> dict[str, Any]:
    card_id = str(card["card_id"])
    filename = safe_card_id(card_id) + ".png"
    destination = UPLOAD_DIR / filename
    local_url = f"{UPLOAD_URL}/{urllib.parse.quote(filename)}"
    source_url = HSJ_ORIG_BASE + urllib.parse.quote(card_id, safe="") + ".png"

    if destination.exists() and not refresh:
        metadata = read_local(destination)
        return {
            "card_id": card_id,
            "source": "hearthstonejson",
            "source_url": source_url,
            "local_url": local_url,
            "downloaded": False,
            **metadata,
        }

    if dry_run:
        return {
            "card_id": card_id,
            "source": "hearthstonejson",
            "source_url": source_url,
            "local_url": local_url,
            "downloaded": not destination.exists(),
            "width": 512,
            "height": 512,
            "size": None,
            "sha1": None,
            "mime": "image/png",
        }

    last_error: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(
            source_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "image/png,image/*;q=0.8",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read()
            width, height = png_dimensions(content)
            break
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt == 2:
                raise RuntimeError(f"Could not download {card_id}: {last_error}") from exc
    else:  # pragma: no cover - the loop either breaks or raises.
        raise RuntimeError(f"Could not download {card_id}: {last_error}")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.chmod(0o755)
    temporary = destination.with_suffix(".png.tmp")
    temporary.write_bytes(content)
    temporary.chmod(0o644)
    temporary.replace(destination)
    destination.chmod(0o644)
    return {
        "card_id": card_id,
        "source": "hearthstonejson",
        "source_url": source_url,
        "local_url": local_url,
        "downloaded": True,
        "width": width,
        "height": height,
        "size": len(content),
        "sha1": hashlib.sha1(content).hexdigest(),
        "mime": "image/png",
    }


def needs_database_update(card: dict[str, Any], result: dict[str, Any]) -> bool:
    comparisons = {
        "full_art_source": result["source"],
        "full_art_source_url": result["source_url"],
        "local_full_art_url": result["local_url"],
        "full_art_width": result["width"],
        "full_art_height": result["height"],
        "full_art_size": result["size"],
        "full_art_sha1": result["sha1"],
        "full_art_mime": result["mime"],
    }
    return any(card.get(column) != value for column, value in comparisons.items())


def save_result(conn, card: dict[str, Any], result: dict[str, Any], dry_run: bool) -> bool:
    changed = needs_database_update(card, result)
    if dry_run or not changed:
        return changed
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE battlegrounds_library_cards
            SET full_art_source = %s,
                full_art_source_url = %s,
                local_full_art_url = %s,
                full_art_width = %s,
                full_art_height = %s,
                full_art_size = %s,
                full_art_sha1 = %s,
                full_art_mime = %s,
                full_art_fetched_at = %s
            WHERE library = %s AND card_id = %s
            """,
            (
                result["source"],
                result["source_url"],
                result["local_url"],
                result["width"],
                result["height"],
                result["size"],
                result["sha1"],
                result["mime"],
                utc_now(),
                card["library"],
                card["card_id"],
            ),
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Mirror original full art for Battlegrounds libraries.")
    parser.add_argument("--library", choices=sorted(SUPPORTED_LIBRARIES), default="trinket")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--refresh", action="store_true", help="Redownload existing local images.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = connect_db(load_php_config())
    try:
        ensure_schema(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM battlegrounds_library_cards WHERE library = %s ORDER BY card_id",
                (args.library,),
            )
            cards = list(cursor.fetchall())
        if args.limit is not None:
            cards = cards[: max(0, args.limit)]

        results: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {
                executor.submit(download, card, args.refresh, args.dry_run): card
                for card in cards
            }
            for future in as_completed(futures):
                card = futures[future]
                try:
                    results[str(card["card_id"])] = future.result()
                except Exception as exc:
                    errors.append({"card_id": str(card["card_id"]), "error": str(exc)})

        updated = 0
        downloaded = 0
        for card in cards:
            result = results.get(str(card["card_id"]))
            if not result:
                continue
            downloaded += int(bool(result["downloaded"]))
            updated += int(save_result(conn, card, result, args.dry_run))
        if not args.dry_run:
            conn.commit()

        summary = {
            "library": args.library,
            "requested": len(cards),
            "available": len(results),
            "downloaded": downloaded,
            "database_updated": updated,
            "errors": errors,
            "dry_run": args.dry_run,
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 1 if errors else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
