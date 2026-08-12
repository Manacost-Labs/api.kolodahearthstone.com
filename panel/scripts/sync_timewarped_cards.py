#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARSER_PATH = Path("/opt/wiki-hs-parser")
DEFAULT_CACHE_DIR = APP_ROOT / "var" / "wiki-hs-cache"
SOURCE = "hearthstone.wiki.gg"
CARDS_BY_POOL_TITLE = "Battlegrounds/Timewarped Tavern/Cards by pool"
HSJ_RU_URL = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.json"
HSJ_EN_URL = "https://api.hearthstonejson.com/v1/latest/enUS/cards.json"
HSJ_BGS_RENDER_RU = "https://art.hearthstonejson.com/v1/bgs/latest/ruRU/512x/"
HSJ_ORIG_BASE = "https://art.hearthstonejson.com/v1/orig/"


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
            CREATE TABLE IF NOT EXISTS battlegrounds_timewarped_cards (
                card_id VARCHAR(64) NOT NULL,
                dbf INT UNSIGNED DEFAULT NULL,
                name_en VARCHAR(180) NOT NULL,
                name_ru VARCHAR(180) DEFAULT NULL,
                text_en TEXT DEFAULT NULL,
                text_ru TEXT DEFAULT NULL,
                flavor TEXT DEFAULT NULL,
                card_type VARCHAR(64) DEFAULT NULL,
                card_type_id SMALLINT UNSIGNED DEFAULT NULL,
                cost SMALLINT DEFAULT NULL,
                tavern_tier TINYINT UNSIGNED DEFAULT NULL,
                attack SMALLINT DEFAULT NULL,
                health SMALLINT DEFAULT NULL,
                minion_type VARCHAR(128) DEFAULT NULL,
                race VARCHAR(128) DEFAULT NULL,
                artist VARCHAR(255) DEFAULT NULL,
                card_image_url VARCHAR(512) DEFAULT NULL,
                art_image_url VARCHAR(512) DEFAULT NULL,
                golden_card_id VARCHAR(64) DEFAULT NULL,
                golden_dbf INT UNSIGNED DEFAULT NULL,
                golden_name_en VARCHAR(180) DEFAULT NULL,
                golden_name_ru VARCHAR(180) DEFAULT NULL,
                golden_text_en TEXT DEFAULT NULL,
                golden_text_ru TEXT DEFAULT NULL,
                golden_image_url VARCHAR(512) DEFAULT NULL,
                wiki_page_title VARCHAR(255) DEFAULT NULL,
                wiki_page_url VARCHAR(512) DEFAULT NULL,
                wiki_mechanics_json JSON DEFAULT NULL,
                wiki_tags_json JSON DEFAULT NULL,
                availability_json JSON DEFAULT NULL,
                related_cards_json JSON DEFAULT NULL,
                related_card_ids_json JSON DEFAULT NULL,
                sounds_json JSON DEFAULT NULL,
                gallery_json JSON DEFAULT NULL,
                card_changes_json JSON DEFAULT NULL,
                external_links_json JSON DEFAULT NULL,
                full_tags_json JSON DEFAULT NULL,
                source_payload_json JSON DEFAULT NULL,
                source_hash CHAR(64) DEFAULT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'ok',
                error TEXT DEFAULT NULL,
                fetched_at TIMESTAMP NULL DEFAULT NULL,
                changed_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (card_id),
                UNIQUE KEY uniq_dbf (dbf),
                KEY idx_card_type (card_type),
                KEY idx_tavern_tier (tavern_tier),
                KEY idx_status (status),
                KEY idx_changed_at (changed_at),
                KEY idx_fetched_at (fetched_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS battlegrounds_timewarped_related (
                id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                card_id VARCHAR(64) NOT NULL,
                related_card_id VARCHAR(64) DEFAULT NULL,
                heading VARCHAR(255) DEFAULT NULL,
                title VARCHAR(255) DEFAULT NULL,
                caption VARCHAR(255) DEFAULT NULL,
                url VARCHAR(512) DEFAULT NULL,
                image_url VARCHAR(512) DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_card_id (card_id),
                KEY idx_related_card_id (related_card_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    conn.commit()


def import_parser(parser_path: Path):
    sys.path.insert(0, str(parser_path))
    import wiki_hs_lookup as lookup
    import wiki_hs_parser as parser

    return lookup, parser


def mediawiki_api(params: dict[str, Any]) -> dict[str, Any]:
    url = "https://hearthstone.wiki.gg/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "db.kolodahs.ru-timewarped-sync/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.load(resp)


def cards_by_pool_titles() -> list[str]:
    data = mediawiki_api(
        {
            "action": "parse",
            "page": CARDS_BY_POOL_TITLE,
            "prop": "links",
            "format": "json",
        }
    )
    links = data.get("parse", {}).get("links", [])
    titles: list[str] = []
    seen: set[str] = set()
    for link in links:
        if not isinstance(link, dict) or link.get("ns") != 0:
            continue
        title = str(link.get("*") or "")
        if "(golden)" in title:
            continue
        if not (
            title.startswith("Battlegrounds/Timewarped")
            or title.startswith("Battlegrounds/Power of")
        ):
            continue
        if title.startswith("Battlegrounds/Timewarped Tavern"):
            continue
        if title not in seen:
            seen.add(title)
            titles.append(title)
    return titles


def page_url(title: str) -> str:
    return "https://hearthstone.wiki.gg/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe="/()_',.!:")


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[idx : idx + size] for idx in range(0, len(values), size)]


def load_pageimages(titles: list[str]) -> dict[str, dict[str, str | None]]:
    found: dict[str, dict[str, str | None]] = {}
    for batch in chunks(sorted({title for title in titles if title}), 50):
        data = mediawiki_api(
            {
                "action": "query",
                "titles": "|".join(batch),
                "prop": "pageimages",
                "pithumbsize": "512",
                "format": "json",
            }
        )
        pages = data.get("query", {}).get("pages", {})
        if not isinstance(pages, dict):
            continue
        for page in pages.values():
            if not isinstance(page, dict) or page.get("missing") is not None:
                continue
            title = str(page.get("title") or "")
            thumbnail = page.get("thumbnail")
            image = thumbnail.get("source") if isinstance(thumbnail, dict) else None
            if title and image:
                found[title] = {
                    "image": str(image),
                    "page_title": title,
                    "page_url": page_url(title),
                }
    return found


def load_hsj_cards(url: str) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    req = urllib.request.Request(url, headers={"User-Agent": "db.kolodahs.ru-timewarped-sync/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=70) as resp:
            cards = json.load(resp)
    except Exception:
        return {}, {}
    by_dbf: dict[int, dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for card in cards if isinstance(cards, list) else []:
        if not isinstance(card, dict):
            continue
        if card.get("dbfId") is not None:
            by_dbf[int(card["dbfId"])] = card
        if card.get("id"):
            by_id[str(card["id"])] = card
    return by_dbf, by_id


def cache_path(cache_dir: Path, safe_filename, page_title: str) -> Path:
    return cache_dir / "timewarped-pages" / f"{safe_filename(page_title)}.json"


def load_or_fetch_page(cache_dir: Path, parser, safe_filename, page_title: str, refresh: bool) -> tuple[dict[str, Any], str]:
    path = cache_path(cache_dir, safe_filename, page_title)
    if not refresh and path.exists():
        return json.loads(path.read_text(encoding="utf-8")), "cache"

    result = parser.build_result(page_title, download=False, output_dir=cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, "network"


def normalize_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"label": item.get("label"), "url": item.get("url")} for item in links or []]


def normalize_gallery(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "caption": item.get("caption"),
            "file_title": item.get("file_title"),
            "file_url": item.get("file_url"),
            "thumb_url": item.get("thumb_url"),
            "file_page_url": item.get("file_page_url"),
        }
        for item in images or []
    ]


def normalize_sounds(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for group in groups or []:
        clips = []
        for clip in group.get("clips", []) or []:
            clips.append(
                {
                    "group": clip.get("group"),
                    "file_title": clip.get("file_title"),
                    "file_url": clip.get("file_url"),
                    "description": clip.get("description"),
                }
            )
        normalized.append({"heading": group.get("heading"), "clips": clips})
    return normalized


def normalize_related(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for group in groups or []:
        cards = []
        for card in group.get("cards", []) or []:
            cards.append(
                {
                    "card_id": card.get("card_code"),
                    "title": card.get("title"),
                    "caption": card.get("caption"),
                    "url": card.get("href"),
                    "image_alt": card.get("image_alt"),
                    "image_url": card.get("image_url"),
                }
            )
        normalized.append({"heading": group.get("heading"), "cards": cards})
    return normalized


def related_card_ids(groups: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for group in groups:
        for card in group.get("cards", []) or []:
            card_id = str(card.get("card_id") or "").strip()
            if card_id and card_id not in seen:
                seen.add(card_id)
                ids.append(card_id)
    return ids


def normalize_card_changes(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for group in groups or []:
        entries = []
        for entry in group.get("entries", []) or []:
            entries.append(
                {
                    "patch": entry.get("patch"),
                    "patch_url": entry.get("patch_url"),
                    "date": entry.get("date"),
                    "items": entry.get("items") or [],
                }
            )
        normalized.append({"heading": group.get("heading"), "entries": entries})
    return normalized


def normalize_availability(card_data: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    availability = card_data.get("availability") if isinstance(card_data.get("availability"), dict) else {}
    return {
        "formats": availability.get("formats", []),
        "exclusions": availability.get("exclusions", []),
        "notes": availability.get("notes") or card_data.get("availability_notes") or [],
        "page_entries": [
            {
                "text": entry.get("text"),
                "links": [
                    {
                        "title": link.get("title"),
                        "card_id": link.get("card_code"),
                        "url": link.get("href"),
                        "caption": link.get("caption"),
                    }
                    for link in (entry.get("links", []) or [])
                ],
            }
            for entry in (result.get("page_availability", []) or [])
        ],
    }


def card_image(card_id: str | None) -> str | None:
    if not card_id:
        return None
    return HSJ_BGS_RENDER_RU + urllib.parse.quote(str(card_id), safe="") + ".png"


def art_image(card_id: str | None) -> str | None:
    if not card_id:
        return None
    return HSJ_ORIG_BASE + urllib.parse.quote(str(card_id), safe="") + ".png"


def card_type_slug(card_type: str | None, type_id: int | None) -> str | None:
    normalized = (card_type or "").strip().lower()
    if normalized in {"minion", "tavern spell", "spell", "hero power"}:
        return "spell" if normalized in {"tavern spell", "spell"} else normalized.replace(" ", "_")
    if type_id == 4:
        return "minion"
    if type_id == 42:
        return "spell"
    return normalized or None


def index_entries_by_title(indexes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_title: dict[str, dict[str, Any]] = {}
    for index in indexes:
        for entry in index.get("entries", []) or []:
            title = entry.get("page_title")
            card_id = str(entry.get("card_id") or "")
            if not title or "(golden)" in str(title) or card_id.endswith("_G"):
                continue
            by_title.setdefault(str(title), entry)
    return by_title


def discover_entries(lookup, cache_dir: Path, refresh_index: bool) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    indexes: list[dict[str, Any]] = []
    refreshed: dict[str, bool] = {}
    for scope in ("card", "bg-minion"):
        path = cache_dir / f"wiki-card-index-{scope}.json"
        index, was_refreshed = lookup.load_or_refresh_index(path, scope, refresh_index)
        indexes.append(index)
        refreshed[scope] = bool(was_refreshed)

    by_title = index_entries_by_title(indexes)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    missing_titles: list[str] = []
    for title in cards_by_pool_titles():
        entry = by_title.get(title)
        if not entry:
            missing_titles.append(title)
            continue
        card_id = str(entry.get("card_id") or "")
        if not card_id or card_id in seen:
            continue
        seen.add(card_id)
        entries.append(entry)
    if missing_titles:
        print(json_dump({"missing_titles": missing_titles}), file=sys.stderr)
    return entries, refreshed


def current_hash(conn, card_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT source_hash FROM battlegrounds_timewarped_cards WHERE card_id = %s", (card_id,))
        row = cur.fetchone()
    return row["source_hash"] if row else None


def build_payload(
    entry: dict[str, Any],
    result: dict[str, Any],
    hsj_ru_by_dbf: dict[int, dict[str, Any]],
    hsj_en_by_dbf: dict[int, dict[str, Any]],
    pageimages: dict[str, dict[str, str | None]],
) -> dict[str, Any]:
    card_data = result.get("card_data") if isinstance(result.get("card_data"), dict) else {}
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    dbf = card_data.get("dbf_id") or entry.get("dbf_id")
    dbf_int = int(dbf) if dbf is not None else None
    card_id = str(card_data.get("card_code") or entry.get("card_id") or "")
    ru_card = hsj_ru_by_dbf.get(dbf_int) if dbf_int is not None else None
    en_card = hsj_en_by_dbf.get(dbf_int) if dbf_int is not None else None

    alternate = card_data.get("alternate_card") if isinstance(card_data.get("alternate_card"), dict) else {}
    golden_dbf = alternate.get("dbf_id") or card_data.get("battlegrounds_premium_dbf_id") or details.get("premium_dbf_id")
    golden_dbf_int = int(golden_dbf) if golden_dbf is not None else None
    golden_ru = hsj_ru_by_dbf.get(golden_dbf_int) if golden_dbf_int is not None else None
    golden_en = hsj_en_by_dbf.get(golden_dbf_int) if golden_dbf_int is not None else None
    golden_card_id = alternate.get("card_code") or (golden_en or {}).get("id") or (golden_ru or {}).get("id")
    golden_page_title = f"{result.get('page_title') or entry.get('page_title')} (golden)"
    golden_pageimage = pageimages.get(golden_page_title, {})

    related = normalize_related(result.get("related_cards", []) or [])
    arts = result.get("arts", []) or []
    type_id = details.get("type_id")
    if type_id is None:
        type_id = card_data.get("card_type_id")

    return {
        "source": SOURCE,
        "card_id": card_id,
        "dbf": dbf_int,
        "name_en": card_data.get("name") or (en_card or {}).get("name") or entry.get("name"),
        "name_ru": (ru_card or {}).get("name"),
        "text_en": card_data.get("full_text") or (en_card or {}).get("text"),
        "text_ru": (ru_card or {}).get("text"),
        "flavor": card_data.get("flavor") or (en_card or {}).get("flavorText"),
        "card_type": card_type_slug(card_data.get("card_type") or (en_card or {}).get("type"), int(type_id) if type_id is not None else None),
        "card_type_label": card_data.get("card_type") or (en_card or {}).get("type"),
        "card_type_id": int(type_id) if type_id is not None else None,
        "cost": card_data.get("cost") if card_data.get("cost") is not None else details.get("cost"),
        "tavern_tier": card_data.get("battlegrounds_tier") or details.get("tier"),
        "attack": card_data.get("attack") if card_data.get("attack") is not None else details.get("attack"),
        "health": card_data.get("health") if card_data.get("health") is not None else details.get("health"),
        "minion_type": card_data.get("minion_type"),
        "race": card_data.get("race"),
        "artist": card_data.get("artist") or details.get("artist"),
        "card_image_url": card_image(card_id) or (arts[0].get("file_url") if arts else None),
        "art_image_url": art_image(card_id),
        "golden_card_id": golden_card_id,
        "golden_dbf": golden_dbf_int,
        "golden_name_en": (golden_en or {}).get("name") or alternate.get("name"),
        "golden_name_ru": (golden_ru or {}).get("name"),
        "golden_text_en": (golden_en or {}).get("text") or alternate.get("full_text"),
        "golden_text_ru": (golden_ru or {}).get("text"),
        "golden_image_url": golden_pageimage.get("image") or card_image(str(golden_card_id) if golden_card_id else None),
        "wiki_page_title": result.get("page_title") or entry.get("page_title"),
        "wiki_page_url": result.get("page_url") or entry.get("page_url"),
        "wiki_mechanics": card_data.get("wiki_mechanics") or [],
        "wiki_tags": card_data.get("wiki_tags") or [],
        "availability": normalize_availability(card_data, result),
        "related_cards": related,
        "related_card_ids": related_card_ids(related),
        "sounds": normalize_sounds(result.get("sounds", []) or []),
        "gallery": normalize_gallery(result.get("gallery_images", []) or []),
        "card_changes": normalize_card_changes(result.get("patch_changes", []) or []),
        "external_links": normalize_links(result.get("external_links", []) or []),
        "full_tags": card_data.get("full_tags") or [],
        "source_result": result,
    }


def save_related(conn, card_id: str, related_groups: list[dict[str, Any]]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM battlegrounds_timewarped_related WHERE card_id = %s", (card_id,))
        rows = []
        for group in related_groups:
            heading = group.get("heading")
            for card in group.get("cards", []) or []:
                rows.append(
                    (
                        card_id,
                        card.get("card_id"),
                        heading,
                        card.get("title"),
                        card.get("caption"),
                        card.get("url"),
                        card.get("image_url"),
                    )
                )
        if rows:
            cur.executemany(
                """
                INSERT INTO battlegrounds_timewarped_related
                (card_id, related_card_id, heading, title, caption, url, image_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )


def save_payload(conn, payload: dict[str, Any], dry_run: bool) -> str:
    source_payload = dict(payload)
    new_hash = stable_hash(source_payload)
    old_hash = current_hash(conn, payload["card_id"])
    changed = old_hash != new_hash
    if dry_run:
        return "changed" if changed else "unchanged"

    now = utc_now()
    params = {
        "card_id": payload["card_id"],
        "dbf": payload["dbf"],
        "name_en": payload["name_en"],
        "name_ru": payload["name_ru"],
        "text_en": payload["text_en"],
        "text_ru": payload["text_ru"],
        "flavor": payload["flavor"],
        "card_type": payload["card_type"],
        "card_type_id": payload["card_type_id"],
        "cost": payload["cost"],
        "tavern_tier": payload["tavern_tier"],
        "attack": payload["attack"],
        "health": payload["health"],
        "minion_type": payload["minion_type"],
        "race": payload["race"],
        "artist": payload["artist"],
        "card_image_url": payload["card_image_url"],
        "art_image_url": payload["art_image_url"],
        "golden_card_id": payload["golden_card_id"],
        "golden_dbf": payload["golden_dbf"],
        "golden_name_en": payload["golden_name_en"],
        "golden_name_ru": payload["golden_name_ru"],
        "golden_text_en": payload["golden_text_en"],
        "golden_text_ru": payload["golden_text_ru"],
        "golden_image_url": payload["golden_image_url"],
        "wiki_page_title": payload["wiki_page_title"],
        "wiki_page_url": payload["wiki_page_url"],
        "wiki_mechanics_json": json_dump(payload["wiki_mechanics"]),
        "wiki_tags_json": json_dump(payload["wiki_tags"]),
        "availability_json": json_dump(payload["availability"]),
        "related_cards_json": json_dump(payload["related_cards"]),
        "related_card_ids_json": json_dump(payload["related_card_ids"]),
        "sounds_json": json_dump(payload["sounds"]),
        "gallery_json": json_dump(payload["gallery"]),
        "card_changes_json": json_dump(payload["card_changes"]),
        "external_links_json": json_dump(payload["external_links"]),
        "full_tags_json": json_dump(payload["full_tags"]),
        "source_payload_json": json_dump(source_payload),
        "source_hash": new_hash,
        "fetched_at": now,
        "changed_at": now,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO battlegrounds_timewarped_cards (
                card_id, dbf, name_en, name_ru, text_en, text_ru, flavor,
                card_type, card_type_id, cost, tavern_tier, attack, health,
                minion_type, race, artist, card_image_url, art_image_url,
                golden_card_id, golden_dbf, golden_name_en, golden_name_ru,
                golden_text_en, golden_text_ru, golden_image_url,
                wiki_page_title, wiki_page_url, wiki_mechanics_json, wiki_tags_json,
                availability_json, related_cards_json, related_card_ids_json,
                sounds_json, gallery_json, card_changes_json, external_links_json,
                full_tags_json, source_payload_json, source_hash, status, error,
                fetched_at, changed_at
            ) VALUES (
                %(card_id)s, %(dbf)s, %(name_en)s, %(name_ru)s, %(text_en)s, %(text_ru)s, %(flavor)s,
                %(card_type)s, %(card_type_id)s, %(cost)s, %(tavern_tier)s, %(attack)s, %(health)s,
                %(minion_type)s, %(race)s, %(artist)s, %(card_image_url)s, %(art_image_url)s,
                %(golden_card_id)s, %(golden_dbf)s, %(golden_name_en)s, %(golden_name_ru)s,
                %(golden_text_en)s, %(golden_text_ru)s, %(golden_image_url)s,
                %(wiki_page_title)s, %(wiki_page_url)s, %(wiki_mechanics_json)s, %(wiki_tags_json)s,
                %(availability_json)s, %(related_cards_json)s, %(related_card_ids_json)s,
                %(sounds_json)s, %(gallery_json)s, %(card_changes_json)s, %(external_links_json)s,
                %(full_tags_json)s, %(source_payload_json)s, %(source_hash)s, 'ok', NULL,
                %(fetched_at)s, %(changed_at)s
            )
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf),
                name_en = VALUES(name_en),
                name_ru = VALUES(name_ru),
                text_en = VALUES(text_en),
                text_ru = VALUES(text_ru),
                flavor = VALUES(flavor),
                card_type = VALUES(card_type),
                card_type_id = VALUES(card_type_id),
                cost = VALUES(cost),
                tavern_tier = VALUES(tavern_tier),
                attack = VALUES(attack),
                health = VALUES(health),
                minion_type = VALUES(minion_type),
                race = VALUES(race),
                artist = VALUES(artist),
                card_image_url = VALUES(card_image_url),
                art_image_url = VALUES(art_image_url),
                golden_card_id = VALUES(golden_card_id),
                golden_dbf = VALUES(golden_dbf),
                golden_name_en = VALUES(golden_name_en),
                golden_name_ru = VALUES(golden_name_ru),
                golden_text_en = VALUES(golden_text_en),
                golden_text_ru = VALUES(golden_text_ru),
                golden_image_url = VALUES(golden_image_url),
                wiki_page_title = VALUES(wiki_page_title),
                wiki_page_url = VALUES(wiki_page_url),
                wiki_mechanics_json = VALUES(wiki_mechanics_json),
                wiki_tags_json = VALUES(wiki_tags_json),
                availability_json = VALUES(availability_json),
                related_cards_json = VALUES(related_cards_json),
                related_card_ids_json = VALUES(related_card_ids_json),
                sounds_json = VALUES(sounds_json),
                gallery_json = VALUES(gallery_json),
                card_changes_json = VALUES(card_changes_json),
                external_links_json = VALUES(external_links_json),
                full_tags_json = VALUES(full_tags_json),
                source_payload_json = VALUES(source_payload_json),
                status = 'ok',
                error = NULL,
                fetched_at = VALUES(fetched_at),
                changed_at = IF(
                    battlegrounds_timewarped_cards.source_hash <> VALUES(source_hash)
                    OR battlegrounds_timewarped_cards.source_hash IS NULL,
                    VALUES(changed_at),
                    changed_at
                ),
                source_hash = VALUES(source_hash)
            """,
            params,
        )
    save_related(conn, payload["card_id"], payload["related_cards"])
    return "changed" if changed else "unchanged"


def save_error(conn, entry: dict[str, Any], error: str, dry_run: bool) -> None:
    if dry_run:
        return
    now = utc_now()
    card_id = str(entry.get("card_id") or "")
    error_payload = {
        "source": SOURCE,
        "card_id": card_id,
        "dbf": entry.get("dbf_id"),
        "status": "error",
        "error": error,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO battlegrounds_timewarped_cards
            (card_id, dbf, name_en, source_payload_json, source_hash, status, error, fetched_at, changed_at)
            VALUES (%s, %s, %s, %s, %s, 'error', %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf),
                source_payload_json = VALUES(source_payload_json),
                source_hash = VALUES(source_hash),
                status = 'error',
                error = VALUES(error),
                fetched_at = VALUES(fetched_at),
                changed_at = VALUES(changed_at)
            """,
            (
                card_id,
                entry.get("dbf_id"),
                entry.get("name") or card_id,
                json_dump(error_payload),
                stable_hash(error_payload),
                error[:65535],
                now,
                now,
            ),
        )


def sleep_between_requests(delay_seconds: float, jitter_seconds: float) -> None:
    wait = max(0.0, delay_seconds)
    if jitter_seconds > 0:
        wait += random.uniform(0, jitter_seconds)
    if wait:
        time.sleep(wait)


def main() -> int:
    argp = argparse.ArgumentParser(description="Sync Timewarped Tavern cards from hearthstone.wiki.gg.")
    argp.add_argument("--parser-path", default=str(DEFAULT_PARSER_PATH))
    argp.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    argp.add_argument("--refresh-index", action="store_true")
    argp.add_argument("--refresh-pages", action="store_true")
    argp.add_argument("--card-id")
    argp.add_argument("--card-type", choices=["all", "minion", "spell", "hero_power"], default="all")
    argp.add_argument("--limit", type=int)
    argp.add_argument("--delay-seconds", type=float, default=0.8)
    argp.add_argument("--jitter-seconds", type=float, default=0.25)
    argp.add_argument("--commit-every", type=int, default=10)
    argp.add_argument("--dry-run", action="store_true")
    args = argp.parse_args()

    parser_path = Path(args.parser_path)
    cache_dir = Path(args.cache_dir)
    if not parser_path.exists():
        raise RuntimeError(f"Parser path does not exist: {parser_path}")

    lookup, parser = import_parser(parser_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    conn = connect_db(load_php_config())
    stats = {"scanned": 0, "changed": 0, "unchanged": 0, "errors": 0, "network": 0, "cache": 0}
    try:
        ensure_schema(conn)
        entries, refreshed = discover_entries(lookup, cache_dir, args.refresh_index)
        if args.card_id:
            entries = [entry for entry in entries if str(entry.get("card_id")) == args.card_id]
        if args.limit is not None:
            entries = entries[: args.limit]
        golden_titles = [f"{entry.get('page_title')} (golden)" for entry in entries if entry.get("page_title")]
        pageimages = load_pageimages(golden_titles)
        hsj_ru_by_dbf, _ = load_hsj_cards(HSJ_RU_URL)
        hsj_en_by_dbf, _ = load_hsj_cards(HSJ_EN_URL)
        print(json_dump({
            "cards": len(entries),
            "index_refreshed": refreshed,
            "golden_pageimages": len(pageimages),
            "hsj_ru": len(hsj_ru_by_dbf),
            "hsj_en": len(hsj_en_by_dbf),
            "dry_run": args.dry_run,
        }))
        for idx, entry in enumerate(entries, start=1):
            stats["scanned"] += 1
            try:
                result, source = load_or_fetch_page(
                    cache_dir,
                    parser,
                    parser.safe_filename,
                    str(entry["page_title"]),
                    args.refresh_pages,
                )
                stats[source] += 1
                payload = build_payload(entry, result, hsj_ru_by_dbf, hsj_en_by_dbf, pageimages)
                if args.card_type != "all" and payload.get("card_type") != args.card_type:
                    continue
                outcome = save_payload(conn, payload, args.dry_run)
                stats[outcome] += 1
                print(f"[{idx}/{len(entries)}] {payload['card_id']} {outcome} ({source})")
            except Exception as exc:
                stats["errors"] += 1
                save_error(conn, entry, repr(exc), args.dry_run)
                print(f"[{idx}/{len(entries)}] {entry.get('card_id')} error: {exc}", file=sys.stderr)
            if not args.dry_run and idx % max(1, args.commit_every) == 0:
                conn.commit()
            sleep_between_requests(args.delay_seconds, args.jitter_seconds)
        if not args.dry_run:
            conn.commit()
        print(json_dump(stats))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
