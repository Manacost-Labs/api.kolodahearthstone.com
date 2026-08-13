#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql

from backfill_constructed_images import connect_db, load_php_config


APP_ROOT = Path(os.environ.get("KOLODAHS_APP_ROOT") or Path(__file__).resolve().parents[1])
UPLOAD_ROOT = APP_ROOT / "uploads"
OUTPUT_DIRNAME = "horizontal-art"
OUTPUT_WIDTH = 320
OUTPUT_HEIGHT = 64
SOURCE_CROP_WIDTH = 243
SOURCE_CROP_HEIGHT = 64
VISIBLE_CROP_X = 52
VISIBLE_CROP_WIDTH = SOURCE_CROP_WIDTH - VISIBLE_CROP_X
ART_X = OUTPUT_WIDTH - VISIBLE_CROP_WIDTH
FADE_WIDTH = 70
RECIPE_VERSION = "1-official-crop-soft-black"
USER_AGENT = "db.kolodahs.ru-horizontal-art/1.0"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class Candidate:
    entity_type: str
    entity_id: str
    dbf: int | None
    source_url: str
    source_kind: str


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS horizontal_art_assets (
                entity_type VARCHAR(32) NOT NULL,
                entity_id VARCHAR(160) NOT NULL,
                source_url VARCHAR(1024) NOT NULL,
                source_kind VARCHAR(16) NOT NULL,
                source_signature CHAR(64) NOT NULL,
                local_image_url VARCHAR(512) DEFAULT NULL,
                image_sha256 CHAR(64) DEFAULT NULL,
                recipe_version VARCHAR(64) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'ready',
                last_error TEXT DEFAULT NULL,
                generated_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (entity_type, entity_id),
                KEY idx_horizontal_art_status (status),
                KEY idx_horizontal_art_generated (generated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    conn.commit()


def query_rows(conn, sql: str) -> list[dict[str, Any]]:
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return list(cur.fetchall())
    except pymysql.err.ProgrammingError as exc:
        if exc.args and int(exc.args[0]) == 1146:
            return []
        raise


def first_source(
    row: dict[str, Any],
    candidates: list[tuple[str, str]],
) -> tuple[str, str] | None:
    for column, source_kind in candidates:
        value = str(row.get(column) or "").strip()
        if value:
            return value, source_kind
    return None


def fetch_blizzard_crop_urls() -> dict[int, str]:
    client_id = os.environ.get("BLIZZARD_CLIENT_ID", "").strip()
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return {}

    token_request = urllib.request.Request(
        "https://oauth.battle.net/token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={
            "Authorization": "Basic "
            + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode(),
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(token_request, timeout=45) as response:
        token = str(json.load(response).get("access_token") or "")
    if not token:
        raise RuntimeError("Blizzard token response does not contain access_token")

    region = os.environ.get("BLIZZARD_REGION", "us")
    locale = os.environ.get("BLIZZARD_LOCALE", "ru_RU")
    result: dict[int, str] = {}
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {
                "locale": locale,
                "gameMode": "battlegrounds",
                "pageSize": 500,
                "page": page,
            }
        )
        request = urllib.request.Request(
            f"https://{region}.api.blizzard.com/hearthstone/cards?{query}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.load(response)
        for card in payload.get("cards", []):
            if not isinstance(card, dict):
                continue
            dbf = int(card.get("id") or 0)
            battlegrounds = card.get("battlegrounds")
            if not isinstance(battlegrounds, dict):
                battlegrounds = {}
            crop_url = battlegrounds.get("cropImage") or card.get("cropImage")
            if dbf and crop_url:
                result[dbf] = str(crop_url)
        if page >= int(payload.get("pageCount") or page):
            break
        page += 1
    return result


def collect_candidates(conn, blizzard_crops: dict[int, str]) -> list[Candidate]:
    candidates: list[Candidate] = []

    for row in query_rows(
        conn,
        """
        SELECT card_id, dbf, art_image, card_image
          FROM battlegrounds_cards
         WHERE COALESCE(art_image, card_image, '') <> ''
        """,
    ):
        dbf = int(row["dbf"]) if row.get("dbf") is not None else None
        source = (blizzard_crops[dbf], "crop") if dbf in blizzard_crops else first_source(
            row,
            [("art_image", "art"), ("card_image", "card")],
        )
        if source:
            candidates.append(Candidate("battleground_card", str(row["card_id"]), dbf, *source))

    for row in query_rows(
        conn,
        """
        SELECT card_id, dbf, local_crop_image_url, crop_image_url,
               local_wiki_full_art_url, wiki_full_art_url, local_image_url, image_url
          FROM constructed_cards
         WHERE COALESCE(local_crop_image_url, crop_image_url, local_wiki_full_art_url,
                        wiki_full_art_url, local_image_url, image_url, '') <> ''
        """,
    ):
        source = first_source(
            row,
            [
                ("local_crop_image_url", "crop"),
                ("crop_image_url", "crop"),
                ("local_wiki_full_art_url", "art"),
                ("wiki_full_art_url", "art"),
                ("local_image_url", "card"),
                ("image_url", "card"),
            ],
        )
        if source:
            dbf = int(row["dbf"]) if row.get("dbf") is not None else None
            candidates.append(Candidate("constructed_card", str(row["card_id"]), dbf, *source))

    for row in query_rows(
        conn,
        """
        SELECT library, card_id, dbf, crop_image_url, local_full_art_url,
               full_art_source_url, image_url
          FROM battlegrounds_library_cards
         WHERE COALESCE(crop_image_url, local_full_art_url, full_art_source_url, image_url, '') <> ''
        """,
    ):
        source = first_source(
            row,
            [
                ("crop_image_url", "crop"),
                ("local_full_art_url", "art"),
                ("full_art_source_url", "art"),
                ("image_url", "card"),
            ],
        )
        if source:
            dbf = int(row["dbf"]) if row.get("dbf") is not None else None
            entity_id = f"{row['library']}:{row['card_id']}"
            candidates.append(Candidate("library_card", entity_id, dbf, *source))

    for row in query_rows(
        conn,
        """
        SELECT card_id, dbf, hero_full_art_url, hero_image_url
          FROM battlegrounds_heroes
         WHERE COALESCE(hero_full_art_url, hero_image_url, '') <> ''
        """,
    ):
        dbf = int(row["dbf"]) if row.get("dbf") is not None else None
        source = (blizzard_crops[dbf], "crop") if dbf in blizzard_crops else first_source(
            row,
            [("hero_full_art_url", "art"), ("hero_image_url", "card")],
        )
        if source:
            candidates.append(Candidate("hero", str(row["card_id"]), dbf, *source))

    query_specs = [
        (
            "hero_skin",
            "SELECT card_id AS entity_id, dbf, full_art_url, static_image_url FROM hero_skins "
            "WHERE COALESCE(full_art_url, static_image_url, '') <> ''",
            [("full_art_url", "art"), ("static_image_url", "card")],
            lambda row: str(row["entity_id"]),
        ),
        (
            "pet",
            "SELECT variant_id, card_id, dbf, end_screen_background_url, card_image_url "
            "FROM hearthstone_pets WHERE COALESCE(end_screen_background_url, card_image_url, '') <> ''",
            [("end_screen_background_url", "art"), ("card_image_url", "card")],
            lambda row: str(row.get("card_id") or f"variant:{row['variant_id']}"),
        ),
        (
            "coin",
            "SELECT card_id AS entity_id, dbf, crop_image_url, wiki_image_url, image_url "
            "FROM hearthstone_coins WHERE COALESCE(crop_image_url, wiki_image_url, image_url, '') <> ''",
            [("crop_image_url", "crop"), ("wiki_image_url", "art"), ("image_url", "card")],
            lambda row: str(row["entity_id"]),
        ),
        (
            "timewarped_card",
            "SELECT card_id AS entity_id, dbf, art_image_url, card_image_url "
            "FROM battlegrounds_timewarped_cards WHERE COALESCE(art_image_url, card_image_url, '') <> ''",
            [("art_image_url", "art"), ("card_image_url", "card")],
            lambda row: str(row["entity_id"]),
        ),
    ]
    for entity_type, sql, source_fields, entity_id_for in query_specs:
        for row in query_rows(conn, sql):
            source = first_source(row, source_fields)
            if source:
                dbf = int(row["dbf"]) if row.get("dbf") is not None else None
                candidates.append(Candidate(entity_type, entity_id_for(row), dbf, *source))

    deduplicated = {(item.entity_type, item.entity_id): item for item in candidates}
    return sorted(deduplicated.values(), key=lambda item: (item.entity_type, item.entity_id))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def identify_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["identify", "-format", "%w %h", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    width, height = result.stdout.strip().split()
    return int(width), int(height)


def normalized_crop(source: Path, target: Path, source_kind: str) -> None:
    width, height = identify_size(source)
    if width < 2 or height < 2:
        raise RuntimeError(f"Source image is too small: {width}x{height}")

    if source_kind == "crop":
        command = [
            "convert",
            str(source),
            "-auto-orient",
            "-resize",
            f"{SOURCE_CROP_WIDTH}x{SOURCE_CROP_HEIGHT}^",
            "-gravity",
            "center",
            "-extent",
            f"{SOURCE_CROP_WIDTH}x{SOURCE_CROP_HEIGHT}",
            str(target),
        ]
    elif source_kind == "card":
        crop_width = min(width, max(1, round(width * 0.602)))
        crop_height = min(height, max(1, round(height * 0.115)))
        crop_x = min(width - crop_width, max(0, round(width * 0.198)))
        crop_y = min(height - crop_height, max(0, round(height * 0.125)))
        command = [
            "convert",
            str(source),
            "-auto-orient",
            "-crop",
            f"{crop_width}x{crop_height}+{crop_x}+{crop_y}",
            "+repage",
            "-resize",
            f"{SOURCE_CROP_WIDTH}x{SOURCE_CROP_HEIGHT}!",
            str(target),
        ]
    elif source_kind == "art":
        crop_width = min(width, max(1, width * 25 // 32))
        crop_height = max(1, round(crop_width * SOURCE_CROP_HEIGHT / SOURCE_CROP_WIDTH))
        if crop_height > height:
            crop_height = height
            crop_width = min(width, max(1, round(crop_height * SOURCE_CROP_WIDTH / SOURCE_CROP_HEIGHT)))

        if height * 100 >= width * 115:
            focus_percent = 29
        elif height * 100 >= width * 104:
            focus_percent = 34
        else:
            focus_percent = 42
        focus_y = height * focus_percent // 100
        crop_y = max(0, min(height - crop_height, focus_y - crop_height // 2))
        command = [
            "convert",
            str(source),
            "-auto-orient",
            "-crop",
            f"{crop_width}x{crop_height}+0+{crop_y}",
            "+repage",
            "-resize",
            f"{SOURCE_CROP_WIDTH}x{SOURCE_CROP_HEIGHT}!",
            str(target),
        ]
    else:
        raise ValueError(f"Unsupported source kind: {source_kind}")

    subprocess.run(command, check=True, capture_output=True, text=True)
    if identify_size(target) != (SOURCE_CROP_WIDTH, SOURCE_CROP_HEIGHT):
        raise RuntimeError("Normalized crop has unexpected dimensions")


def render_horizontal_art(source: Path, target: Path, source_kind: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="horizontal-art-") as tmp_dir:
        normalized = Path(tmp_dir) / "normalized.png"
        normalized_crop(source, normalized, source_kind)
        temporary = Path(tmp_dir) / "output.webp"
        transparent_width = OUTPUT_WIDTH - ART_X - FADE_WIDTH
        subprocess.run(
            [
                "convert",
                "-size",
                f"{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}",
                "xc:black",
                "(",
                str(normalized),
                "-crop",
                f"{VISIBLE_CROP_WIDTH}x{OUTPUT_HEIGHT}+{VISIBLE_CROP_X}+0",
                "+repage",
                ")",
                "-gravity",
                "northwest",
                "-geometry",
                f"+{ART_X}+0",
                "-compose",
                "over",
                "-composite",
                "(",
                "(",
                "-size",
                f"{ART_X}x{OUTPUT_HEIGHT}",
                "xc:rgba(0,0,0,1)",
                ")",
                "(",
                "-size",
                f"{FADE_WIDTH}x{OUTPUT_HEIGHT}",
                "gradient:rgba(0,0,0,1)-rgba(0,0,0,0)",
                "-rotate",
                "-90",
                "+repage",
                "-resize",
                f"{FADE_WIDTH}x{OUTPUT_HEIGHT}!",
                ")",
                "(",
                "-size",
                f"{transparent_width}x{OUTPUT_HEIGHT}",
                "xc:none",
                ")",
                "+append",
                ")",
                "-gravity",
                "northwest",
                "-compose",
                "over",
                "-composite",
                "-strip",
                "-quality",
                "88",
                "-define",
                "webp:method=6",
                str(temporary),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if identify_size(temporary) != (OUTPUT_WIDTH, OUTPUT_HEIGHT):
            raise RuntimeError("Horizontal art has unexpected dimensions")
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)


def output_filename(entity_id: str) -> str:
    if SAFE_ID_RE.fullmatch(entity_id):
        return entity_id + ".webp"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", entity_id).strip("_") or "asset"
    digest = hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:10]
    return f"{safe}-{digest}.webp"


def output_public_path(candidate: Candidate) -> str:
    return f"/uploads/{OUTPUT_DIRNAME}/{candidate.entity_type}/{output_filename(candidate.entity_id)}"


def local_upload_path(public_path: str) -> Path:
    if not public_path.startswith("/uploads/"):
        raise ValueError(f"Unexpected local upload path: {public_path}")
    resolved = (APP_ROOT / public_path.lstrip("/")).resolve()
    allowed = UPLOAD_ROOT.resolve()
    if allowed not in resolved.parents:
        raise ValueError(f"Upload path escapes media root: {public_path}")
    return resolved


def local_source_path(source_url: str) -> Path | None:
    parsed = urllib.parse.urlparse(source_url)
    public_path = source_url if source_url.startswith("/uploads/") else parsed.path
    if not public_path.startswith("/uploads/"):
        return None
    candidate = local_upload_path(public_path)
    return candidate if candidate.is_file() and candidate.stat().st_size > 0 else None


def source_signature(candidate: Candidate) -> str:
    local = local_source_path(candidate.source_url)
    if local is not None:
        stat = local.stat()
        value = f"local:{local}:{stat.st_size}:{stat.st_mtime_ns}:{candidate.source_kind}:{RECIPE_VERSION}"
    else:
        value = f"remote:{candidate.source_url}:{candidate.source_kind}:{RECIPE_VERSION}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def download_source(source_url: str, target: Path, max_bytes: int = 32 * 1024 * 1024) -> None:
    parsed = urllib.parse.urlparse(source_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Remote image URL must use http or https")
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*"},
    )
    total = 0
    with urllib.request.urlopen(request, timeout=90) as response, target.open("wb") as handle:
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        if content_type and not content_type.startswith("image/"):
            raise RuntimeError(f"Unexpected source content type: {content_type}")
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError("Source image exceeds 32 MiB")
            handle.write(chunk)
    if total == 0:
        raise RuntimeError("Source image is empty")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_existing_assets(conn) -> dict[tuple[str, str], dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM horizontal_art_assets")
        return {
            (str(row["entity_type"]), str(row["entity_id"])): row
            for row in cur.fetchall()
        }


def process_candidate(
    candidate: Candidate,
    existing: dict[str, Any] | None,
    *,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    public_path = output_public_path(candidate)
    target = local_upload_path(public_path)
    signature = source_signature(candidate)
    if (
        not force
        and existing is not None
        and str(existing.get("status") or "") == "ready"
        and str(existing.get("source_signature") or "") == signature
        and str(existing.get("recipe_version") or "") == RECIPE_VERSION
        and target.is_file()
        and target.stat().st_size > 0
    ):
        return {"action": "unchanged", "candidate": candidate}

    if dry_run:
        return {
            "action": "would_generate",
            "candidate": candidate,
            "source_signature": signature,
            "public_path": public_path,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o755)
    local_source = local_source_path(candidate.source_url)
    try:
        if local_source is not None:
            render_horizontal_art(local_source, target, candidate.source_kind)
        else:
            with tempfile.TemporaryDirectory(prefix="horizontal-source-") as tmp_dir:
                downloaded = Path(tmp_dir) / "source"
                download_source(candidate.source_url, downloaded)
                render_horizontal_art(downloaded, target, candidate.source_kind)
        target.chmod(0o644)
        return {
            "action": "generated",
            "candidate": candidate,
            "source_signature": signature,
            "public_path": public_path,
            "image_sha256": sha256_file(target),
        }
    except Exception as exc:
        return {"action": "error", "candidate": candidate, "error": str(exc)}


def persist_result(conn, result: dict[str, Any], existing: dict[str, Any] | None) -> None:
    action = str(result["action"])
    candidate: Candidate = result["candidate"]
    if action == "generated":
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO horizontal_art_assets (
                    entity_type, entity_id, source_url, source_kind, source_signature,
                    local_image_url, image_sha256, recipe_version, status,
                    last_error, generated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ready', NULL, %s)
                ON DUPLICATE KEY UPDATE
                    source_url = VALUES(source_url),
                    source_kind = VALUES(source_kind),
                    source_signature = VALUES(source_signature),
                    local_image_url = VALUES(local_image_url),
                    image_sha256 = VALUES(image_sha256),
                    recipe_version = VALUES(recipe_version),
                    status = 'ready',
                    last_error = NULL,
                    generated_at = VALUES(generated_at)
                """,
                (
                    candidate.entity_type,
                    candidate.entity_id,
                    candidate.source_url,
                    candidate.source_kind,
                    result["source_signature"],
                    result["public_path"],
                    result["image_sha256"],
                    RECIPE_VERSION,
                    utc_now(),
                ),
            )
    elif action == "error":
        error = str(result.get("error") or "Unknown horizontal art error")[:65535]
        if existing and str(existing.get("status") or "") == "ready":
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE horizontal_art_assets
                       SET last_error = %s
                     WHERE entity_type = %s AND entity_id = %s
                    """,
                    (error, candidate.entity_type, candidate.entity_id),
                )
        else:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO horizontal_art_assets (
                        entity_type, entity_id, source_url, source_kind, source_signature,
                        recipe_version, status, last_error
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'error', %s)
                    ON DUPLICATE KEY UPDATE
                        source_url = VALUES(source_url),
                        source_kind = VALUES(source_kind),
                        source_signature = VALUES(source_signature),
                        recipe_version = VALUES(recipe_version),
                        status = 'error',
                        last_error = VALUES(last_error)
                    """,
                    (
                        candidate.entity_type,
                        candidate.entity_id,
                        candidate.source_url,
                        candidate.source_kind,
                        source_signature(candidate),
                        RECIPE_VERSION,
                        error,
                    ),
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build horizontal artwork variants for database entities.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    conn = connect_db(load_php_config())
    try:
        ensure_schema(conn)
        try:
            blizzard_crops = fetch_blizzard_crop_urls()
            blizzard_error = None
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
            blizzard_crops = {}
            blizzard_error = str(exc)

        candidates = collect_candidates(conn, blizzard_crops)
        if args.limit is not None:
            candidates = candidates[: max(0, args.limit)]
        existing_assets = load_existing_assets(conn)
        results: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = [
                executor.submit(
                    process_candidate,
                    candidate,
                    existing_assets.get((candidate.entity_type, candidate.entity_id)),
                    force=args.force,
                    dry_run=args.dry_run,
                )
                for candidate in candidates
            ]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

        if not args.dry_run:
            for result in results:
                candidate = result["candidate"]
                persist_result(
                    conn,
                    result,
                    existing_assets.get((candidate.entity_type, candidate.entity_id)),
                )
            conn.commit()

        counts: dict[str, int] = {}
        errors = []
        by_type: dict[str, int] = {}
        for result in results:
            action = str(result["action"])
            counts[action] = counts.get(action, 0) + 1
            candidate = result["candidate"]
            by_type[candidate.entity_type] = by_type.get(candidate.entity_type, 0) + 1
            if action == "error":
                errors.append(
                    {
                        "entity_type": candidate.entity_type,
                        "entity_id": candidate.entity_id,
                        "error": result.get("error"),
                    }
                )
        summary = {
            "status": "ok" if not errors else "partial",
            "dry_run": args.dry_run,
            "recipe_version": RECIPE_VERSION,
            "candidates": len(candidates),
            "counts": counts,
            "by_type": dict(sorted(by_type.items())),
            "blizzard_crop_urls": len(blizzard_crops),
            "blizzard_error": blizzard_error,
            "errors": errors[:100],
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if not errors else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
