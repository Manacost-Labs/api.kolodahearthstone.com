#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import html
import pymysql
from pymysql.cursors import DictCursor

from wiki_release_dates import fetch_release_dates


APP_ROOT = Path(__file__).resolve().parents[1]
SOURCE_WIKI = "hearthstone.wiki.gg"
USER_AGENT = "db.kolodahs.ru-pet-sync/1.0"
PET_CARD_UPLOAD_DIR = APP_ROOT / "uploads" / "pets" / "cards"
PET_CARD_UPLOAD_URL = "/uploads/pets/cards"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json_dump(value).encode("utf-8")).hexdigest()


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


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS hearthstone_pets (
                variant_id INT UNSIGNED NOT NULL,
                pet_id INT UNSIGNED NOT NULL,
                pet_name VARCHAR(180) NOT NULL,
                variant_name VARCHAR(180) NOT NULL,
                level SMALLINT UNSIGNED DEFAULT NULL,
                dbf INT UNSIGNED DEFAULT NULL,
                release_date DATE DEFAULT NULL,
                card_id VARCHAR(64) DEFAULT NULL,
                card_image_url VARCHAR(512) DEFAULT NULL,
                end_screen_background_url VARCHAR(512) DEFAULT NULL,
                gallery_json JSON DEFAULT NULL,
                page_title VARCHAR(255) DEFAULT NULL,
                page_url VARCHAR(512) DEFAULT NULL,
                status VARCHAR(24) NOT NULL DEFAULT 'ok',
                error TEXT DEFAULT NULL,
                source VARCHAR(64) NOT NULL,
                source_payload_json JSON DEFAULT NULL,
                source_hash CHAR(64) DEFAULT NULL,
                fetched_at TIMESTAMP NULL DEFAULT NULL,
                changed_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (variant_id),
                UNIQUE KEY uniq_pet_card_id (card_id),
                KEY idx_pet_id (pet_id),
                KEY idx_pet_dbf (dbf),
                KEY idx_pet_level (pet_id, level),
                KEY idx_changed_at (changed_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        try:
            cur.execute(
                "ALTER TABLE hearthstone_pets "
                "ADD COLUMN release_date DATE DEFAULT NULL AFTER dbf"
            )
        except pymysql.err.OperationalError as exc:
            if exc.args[0] != 1060:
                raise
    conn.commit()


def http_json(params: dict[str, Any]) -> dict[str, Any]:
    url = "https://hearthstone.wiki.gg/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=80) as resp:
        return json.load(resp)


def cargo_rows(table: str, fields: str, **extra: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    offset = 0
    while True:
        data = http_json({
            "action": "cargoquery",
            "format": "json",
            "limit": "500",
            "offset": str(offset),
            "tables": table,
            "fields": fields,
            **extra,
        })
        rows = data.get("cargoquery")
        if not isinstance(rows, list):
            raise RuntimeError(f"Invalid cargo response for {table}: {data}")
        page_signature = stable_hash(rows)
        if page_signature in seen_pages:
            raise RuntimeError(f"Cargo pagination returned a repeated page for {table} at offset {offset}")
        seen_pages.add(page_signature)
        result.extend(row.get("title", {}) for row in rows if isinstance(row.get("title"), dict))
        if len(rows) < 500:
            return result
        offset += 500


def wiki_page_url(title: str | None) -> str | None:
    if not title:
        return None
    return "https://hearthstone.wiki.gg/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe="/()_',.!:")


def file_url(file_name: str | None) -> str | None:
    if not file_name:
        return None
    file_name = str(file_name).replace(" ", "_")
    return "https://hearthstone.wiki.gg/wiki/Special:Redirect/file/" + urllib.parse.quote(file_name, safe="._-()'!,@")


def url_exists(url: str | None) -> bool:
    if not url:
        return False
    req = urllib.request.Request(str(url), headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            return 200 <= resp.status < 400 and content_type.startswith("image/")
    except Exception:
        return False


def extension_from_response(url: str, content_type: str | None) -> str:
    guessed = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip()) if content_type else None
    if guessed in {".jpeg", ".jpe"}:
        return ".jpg"
    if guessed:
        return guessed
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".png"


def cache_pet_card_image(source_url: str | None, card_id: str | None) -> str | None:
    if not source_url or not card_id:
        return source_url
    PET_CARD_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (APP_ROOT / "uploads").chmod(0o755)
    (APP_ROOT / "uploads" / "pets").chmod(0o755)
    PET_CARD_UPLOAD_DIR.chmod(0o755)

    req = urllib.request.Request(str(source_url), headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
    try:
        with urllib.request.urlopen(req, timeout=80) as resp:
            content_type = resp.headers.get("Content-Type") or ""
            if not content_type.lower().startswith("image/"):
                return source_url
            data = resp.read()
            ext = extension_from_response(resp.geturl(), content_type)
    except Exception:
        return source_url

    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(card_id)).strip("_") or hashlib.sha1(str(source_url).encode()).hexdigest()[:12]
    target = PET_CARD_UPLOAD_DIR / f"{safe_id}{ext}"
    target.write_bytes(data)
    target.chmod(0o644)
    return f"{PET_CARD_UPLOAD_URL}/{target.name}"


def parse_page(page_title: str) -> tuple[str, str, list[str]]:
    data = http_json({"action": "parse", "page": page_title, "prop": "text|wikitext|images", "format": "json"})
    parsed = data["parse"]
    return parsed.get("text", {}).get("*", ""), parsed.get("wikitext", {}).get("*", ""), [str(name) for name in parsed.get("images", []) if name]


def allimages_by_prefix(prefix: str) -> list[str]:
    prefix = prefix.strip().replace(" ", "_")
    if not prefix:
        return []
    data = http_json({"action": "query", "list": "allimages", "aiprefix": prefix, "ailimit": "50", "format": "json"})
    images = data.get("query", {}).get("allimages", [])
    if not isinstance(images, list):
        return []
    return [str(item.get("name")) for item in images if isinstance(item, dict) and item.get("name")]


def best_pet_fallback_file(entry: dict[str, Any], images: list[str]) -> str | None:
    card_id = str(entry.get("card_id") or "")
    names = [str(entry.get("variant_name") or ""), str(entry.get("pet_name") or ""), str(entry.get("page_title") or "")]

    candidates = [image for image in images if image and image != f"{card_id}.png"]
    if not candidates:
        prefixes: list[str] = []
        for name in names:
            if not name:
                continue
            prefixes.append(name)
            if "," in name:
                prefixes.append(name.split(",", 1)[0])
        seen_prefixes: set[str] = set()
        for prefix in prefixes:
            normalized = prefix.strip().replace(" ", "_")
            if not normalized or normalized.lower() in seen_prefixes:
                continue
            seen_prefixes.add(normalized.lower())
            candidates.extend(allimages_by_prefix(normalized))

    usable = [item for item in candidates if item.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
    if not usable:
        return None

    def score(file_name: str) -> tuple[int, str]:
        lower = file_name.lower()
        if "_full." in lower:
            return (0, lower)
        if "full" in lower:
            return (1, lower)
        if "wow" in lower:
            return (8, lower)
        return (4, lower)

    usable.sort(key=score)
    return usable[0]


def resolve_card_image_url(entry: dict[str, Any], images: list[str]) -> str | None:
    card_id = str(entry.get("card_id") or "")
    candidate = file_url(f"{card_id}.png") if card_id else None
    if candidate and url_exists(candidate):
        return cache_pet_card_image(candidate, card_id)
    fallback_file = best_pet_fallback_file(entry, images)
    fallback_url = file_url(fallback_file)
    if fallback_url and url_exists(fallback_url):
        return cache_pet_card_image(fallback_url, card_id or str(entry.get("variant_id") or fallback_file))
    return None


def resolve_pet_card_id(entry: dict[str, Any], images: list[str]) -> str | None:
    """Resolve pet IDs even while the Wiki Cargo Card table is catching up."""
    current = str(entry.get("card_id") or "").strip()
    if current:
        return current

    pet_id = entry.get("pet_id")
    level = entry.get("level")
    preferred = f"PET_{pet_id}_{level}" if pet_id is not None and level is not None else ""
    candidates: list[str] = []
    for image in images:
        stem = Path(str(image).replace(" ", "_")).stem
        if re.fullmatch(r"PET_[0-9]+_[0-9]+", stem, flags=re.I):
            candidates.append(stem)
    if preferred:
        exact = next((candidate for candidate in candidates if candidate.casefold() == preferred.casefold()), None)
        if exact:
            return exact
    return candidates[0] if len(candidates) == 1 else None


def first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.I)
    return match.group(1).strip() if match else None


def image_from_src(src: str | None) -> str | None:
    if not src:
        return None
    src = str(src)
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://hearthstone.wiki.gg" + src
    return src


def extract_gallery(page_html: str, all_images: list[str], background_file: str | None, card_id: str | None) -> list[dict[str, Any]]:
    start = page_html.find('id="Gallery"')
    end_candidates = [
        page_html.find(f'id="{next_id}"', start + 1)
        for next_id in ("Patch_changes", "External_links", "References")
        if start >= 0
    ]
    end_candidates = [pos for pos in end_candidates if pos > start]
    end = min(end_candidates) if end_candidates else -1
    section_html = page_html[start : end if end > start else len(page_html)] if start >= 0 else ""
    section = html.fromstring("<div>" + section_html + "</div>") if section_html else None
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for img in section.xpath(".//img") if section is not None else []:
        alt = " ".join((img.get("alt") or "").split())
        if "logo" in alt.lower():
            continue
        src = image_from_src(img.get("src"))
        if not src or src in seen:
            continue
        seen.add(src)
        items.append({"caption": alt or "Gallery image", "file_title": alt or None, "file_url": src})

    if not items:
        skip = {
            "Neutral_icon.png",
            "Icon_MercWorkshop.png",
            "TLCOU_Logo.png",
            "TLC_Logo_Adventure.png",
            background_file,
            f"{card_id}.png" if card_id else None,
        }
        for image in all_images:
            if image in skip or image.endswith("_icon.png") or (image.startswith("PET_") and image.endswith(".png")):
                continue
            url = file_url(image)
            if not url or url in seen:
                continue
            seen.add(url)
            caption = image.rsplit(".", 1)[0].replace("_", " ")
            items.append({"caption": caption, "file_title": image, "file_url": url})
    return items


def load_pet_index() -> list[dict[str, Any]]:
    pets = {int(row["id"]): row for row in cargo_rows("Pet", "id,name") if row.get("id")}
    variants = cargo_rows("PetVariant", "id,name,petId,cardId")
    levels = {
        int(row["petVariantUnlockedId"]): int(row["level"])
        for row in cargo_rows("PetLevel", "petId,level,petVariantUnlockedId")
        if row.get("petVariantUnlockedId") and row.get("level")
    }
    cards_by_dbf = {
        int(row["dbfId"]): row
        for row in cargo_rows("Card", "id,dbfId,name", where='Card.id LIKE "PET\\_%"')
        if row.get("dbfId")
    }
    dbfs = ",".join(str(int(row["cardId"])) for row in variants if row.get("cardId"))
    release_dates = fetch_release_dates(
        (row.get("cardId") for row in variants),
        user_agent=USER_AGENT,
    )
    custom_by_dbf: dict[int, dict[str, Any]] = {}
    if dbfs:
        for row in cargo_rows("CustomCard", "_pageName,dbfId", where=f"CustomCard.dbfId IN ({dbfs})"):
            if row.get("dbfId"):
                custom_by_dbf[int(row["dbfId"])] = row

    result: list[dict[str, Any]] = []
    for row in variants:
        if not row.get("id") or not row.get("petId") or not row.get("cardId"):
            continue
        variant_id = int(row["id"])
        pet_id = int(row["petId"])
        dbf = int(row["cardId"])
        pet = pets.get(pet_id, {})
        card = cards_by_dbf.get(dbf, {})
        custom = custom_by_dbf.get(dbf, {})
        page_title = str(custom.get("_pageName") or row.get("name") or "")
        card_id = str(card.get("id") or "")
        result.append({
            "variant_id": variant_id,
            "variant_name": str(row.get("name") or ""),
            "pet_id": pet_id,
            "pet_name": str(pet.get("name") or ""),
            "level": levels.get(variant_id),
            "dbf": dbf,
            "release_date": release_dates.get(dbf),
            "card_id": card_id,
            "page_title": page_title,
        })
    return sorted(result, key=lambda item: (item["pet_id"], item.get("level") or 99, item["variant_id"]))


def current_hash(conn, variant_id: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT source_hash FROM hearthstone_pets WHERE variant_id = %s", (variant_id,))
        row = cur.fetchone()
    return row["source_hash"] if row else None


def save_payload(conn, payload: dict[str, Any], dry_run: bool) -> str:
    new_hash = stable_hash(payload)
    old_hash = current_hash(conn, int(payload["variant_id"]))
    changed = old_hash != new_hash
    if dry_run:
        return "changed" if changed else "unchanged"
    now = utc_now()
    params = {
        **{key: value for key, value in payload.items() if key != "gallery"},
        "gallery_json": json_dump(payload["gallery"]),
        "source_payload_json": json_dump(payload),
        "source_hash": new_hash,
        "fetched_at": now,
        "changed_at": now,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO hearthstone_pets (
                variant_id, pet_id, pet_name, variant_name, level, dbf, release_date, card_id,
                card_image_url, end_screen_background_url, gallery_json,
                page_title, page_url, status, error, source, source_payload_json,
                source_hash, fetched_at, changed_at
            ) VALUES (
                %(variant_id)s, %(pet_id)s, %(pet_name)s, %(variant_name)s, %(level)s, %(dbf)s, %(release_date)s, %(card_id)s,
                %(card_image_url)s, %(end_screen_background_url)s, %(gallery_json)s,
                %(page_title)s, %(page_url)s, %(status)s, %(error)s, %(source)s, %(source_payload_json)s,
                %(source_hash)s, %(fetched_at)s, %(changed_at)s
            )
            ON DUPLICATE KEY UPDATE
                pet_id = VALUES(pet_id),
                pet_name = VALUES(pet_name),
                variant_name = VALUES(variant_name),
                level = VALUES(level),
                dbf = VALUES(dbf),
                release_date = VALUES(release_date),
                card_id = VALUES(card_id),
                card_image_url = VALUES(card_image_url),
                end_screen_background_url = VALUES(end_screen_background_url),
                gallery_json = VALUES(gallery_json),
                page_title = VALUES(page_title),
                page_url = VALUES(page_url),
                status = VALUES(status),
                error = VALUES(error),
                source = VALUES(source),
                source_payload_json = VALUES(source_payload_json),
                fetched_at = VALUES(fetched_at),
                changed_at = IF(
                    hearthstone_pets.source_hash <> VALUES(source_hash)
                    OR hearthstone_pets.source_hash IS NULL,
                    VALUES(changed_at),
                    changed_at
                ),
                source_hash = VALUES(source_hash)
            """,
            params,
        )
    return "changed" if changed else "unchanged"


def build_payload(entry: dict[str, Any]) -> dict[str, Any]:
    status = "ok"
    error = None
    page_html = ""
    wikitext = ""
    images: list[str] = []
    try:
        page_html, wikitext, images = parse_page(str(entry["page_title"]))
    except Exception as exc:
        status = "partial"
        error = str(exc)
    card_id = resolve_pet_card_id(entry, images)
    resolved_entry = {**entry, "card_id": card_id}
    background_file = first_match(r"\|custom_petBackgroundImage\s*=\s*([^\n\r|]+)", wikitext)
    return {
        **resolved_entry,
        "card_image_url": resolve_card_image_url(resolved_entry, images),
        "end_screen_background_url": file_url(background_file),
        "gallery": extract_gallery(page_html, images, background_file, card_id),
        "page_url": wiki_page_url(str(entry["page_title"])),
        "status": status,
        "error": error,
        "source": SOURCE_WIKI,
    }


def sync_pets(conn, dry_run: bool) -> dict[str, int]:
    entries = load_pet_index()
    stats = {"scanned": len(entries), "changed": 0, "unchanged": 0, "errors": 0}
    for entry in entries:
        payload = build_payload(entry)
        if payload["status"] == "error":
            stats["errors"] += 1
            print(json_dump({"pet_error": payload["variant_name"], "error": payload["error"]}), file=sys.stderr)
        outcome = save_payload(conn, payload, dry_run)
        stats[outcome] += 1
    return stats


def main() -> int:
    argp = argparse.ArgumentParser(description="Sync Hearthstone pets from hearthstone.wiki.gg.")
    argp.add_argument("--dry-run", action="store_true")
    args = argp.parse_args()
    conn = connect_db(load_php_config())
    try:
        ensure_schema(conn)
        stats = sync_pets(conn, args.dry_run)
        print(json_dump({"dry_run": args.dry_run, "pets": stats}))
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
