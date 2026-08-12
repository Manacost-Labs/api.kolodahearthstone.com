#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import hashlib
import json
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


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = APP_ROOT / "var" / "wiki-hs-cache"
HSJ_RU_URL = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.json"
HSJ_EN_URL = "https://api.hearthstonejson.com/v1/latest/enUS/cards.json"
HSJ_BGS_RU = "https://art.hearthstonejson.com/v1/bgs/latest/ruRU/512x/"
HSJ_RENDER_RU = "https://art.hearthstonejson.com/v1/render/latest/ruRU/512x/"
HSJ_ORIG_BASE = "https://art.hearthstonejson.com/v1/orig/"
SOURCE_WIKI = "hearthstone.wiki.gg"
SOURCE_BLIZZARD = "hearthstone.blizzard.com"
SOURCE_HSJ = "hearthstonejson.com"
URL_OK_CACHE: dict[str, bool] = {}

LIBRARIES = {
    "dark_gift": {
        "name_ru": "Темные дары",
        "wiki_page": None,
        "active_section": None,
        "removed_section": None,
        "official_bg_type": None,
    },
    "anomaly": {
        "name_ru": "Аномалии",
        "wiki_page": "Battlegrounds/Anomaly",
        "active_section": ("Anomalies", "Unused_Anomalies"),
        "removed_section": ("Unused_Anomalies", "Gallery"),
        "official_bg_type": "anomaly",
    },
    "quest": {
        "name_ru": "Квесты",
        "wiki_page": "Battlegrounds/Quest",
        "active_section": ("Quests", "Removed_Quests"),
        "removed_section": ("Removed_Quests", "Patch_changes"),
        "official_bg_type": "quest",
    },
    "darkmoon_prize": {
        "name_ru": "Призы Ярмарки Новолуния",
        "wiki_page": "Battlegrounds/Darkmoon Prize",
        "active_section": ("Darkmoon_Prizes", "Removed_Darkmoon_Prizes"),
        "active_tier_sections": [
            (1, "tier_1", "Тир 1", "Prize_Turn_1", "Prize_Turn_2"),
            (2, "tier_2", "Тир 2", "Prize_Turn_2", "Prize_Turn_3"),
            (3, "tier_3", "Тир 3", "Prize_Turn_3", "Prize_Turn_4"),
            (4, "tier_4", "Тир 4", "Prize_Turn_4", "Removed_Darkmoon_Prizes"),
        ],
        "removed_section": ("Removed_Darkmoon_Prizes", "Patch_changes"),
        "official_bg_type": None,
    },
    "reward": {
        "name_ru": "Награды",
        "wiki_page": None,
        "active_section": None,
        "removed_section": None,
        "official_bg_type": "reward",
    },
    "trinket": {
        "name_ru": "Аксессуары",
        "wiki_page": "Battlegrounds/Trinket",
        "active_group_sections": [
            ("lesser", "Малый аксессуар", "Lesser_Trinkets", "Greater_Trinkets"),
            ("greater", "Большой аксессуар", "Greater_Trinkets", "Related_cards"),
        ],
        "removed_section": ("Removed_Trinkets", "Trivia"),
        "official_bg_type": "trinket",
    },
}

MANUAL_FALLBACKS = {
    "BG27_Anomaly_300": {
        "name_ru": "Заросшая арена Эонар",
        "text_ru": "В таверне появляются только существа с нечетным уровнем таверны.",
    },
    "BG27_Anomaly_753": {
        "name_ru": "Ключи к победе",
        "text_ru": "В таверне появляются только существа с четным уровнем таверны.",
    },
    "BGS_Treasures_036": {
        "name_ru": "Выгодная сделка",
        "text_ru": "Снижает стоимость улучшения таверны.",
    },
}


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
            CREATE TABLE IF NOT EXISTS battlegrounds_library_cards (
                library VARCHAR(32) NOT NULL,
                card_id VARCHAR(64) NOT NULL,
                dbf INT UNSIGNED DEFAULT NULL,
                name_ru VARCHAR(180) NOT NULL,
                name_en VARCHAR(180) DEFAULT NULL,
                text_ru TEXT DEFAULT NULL,
                text_en TEXT DEFAULT NULL,
                image_url VARCHAR(512) DEFAULT NULL,
                image_gold_url VARCHAR(512) DEFAULT NULL,
                crop_image_url VARCHAR(512) DEFAULT NULL,
                full_art_source VARCHAR(32) DEFAULT NULL,
                full_art_source_url VARCHAR(512) DEFAULT NULL,
                local_full_art_url VARCHAR(512) DEFAULT NULL,
                full_art_width SMALLINT UNSIGNED DEFAULT NULL,
                full_art_height SMALLINT UNSIGNED DEFAULT NULL,
                full_art_size INT UNSIGNED DEFAULT NULL,
                full_art_sha1 CHAR(40) DEFAULT NULL,
                full_art_mime VARCHAR(64) DEFAULT NULL,
                full_art_fetched_at TIMESTAMP NULL DEFAULT NULL,
                artist VARCHAR(255) DEFAULT NULL,
                card_type VARCHAR(64) DEFAULT NULL,
                card_type_id SMALLINT UNSIGNED DEFAULT NULL,
                mana_cost SMALLINT DEFAULT NULL,
                in_pool TINYINT(1) NOT NULL DEFAULT 1,
                pool_status VARCHAR(24) NOT NULL DEFAULT 'available',
                group_slug VARCHAR(64) DEFAULT NULL,
                group_name_ru VARCHAR(180) DEFAULT NULL,
                tier_value SMALLINT UNSIGNED DEFAULT NULL,
                tier_slug VARCHAR(64) DEFAULT NULL,
                tier_name_ru VARCHAR(180) DEFAULT NULL,
                sort_order INT UNSIGNED DEFAULT NULL,
                wiki_page_title VARCHAR(255) DEFAULT NULL,
                wiki_page_url VARCHAR(512) DEFAULT NULL,
                source VARCHAR(64) NOT NULL,
                source_payload_json JSON DEFAULT NULL,
                source_hash CHAR(64) DEFAULT NULL,
                fetched_at TIMESTAMP NULL DEFAULT NULL,
                changed_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (library, card_id),
                KEY idx_library_pool (library, in_pool),
                KEY idx_library_status (library, pool_status),
                KEY idx_library_group (library, group_slug),
                KEY idx_library_tier (library, tier_value),
                KEY idx_dbf (dbf),
                KEY idx_changed_at (changed_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cur.execute("SHOW COLUMNS FROM battlegrounds_library_cards")
        columns = {str(row["Field"]) for row in cur.fetchall()}
        if "group_slug" not in columns:
            cur.execute("ALTER TABLE battlegrounds_library_cards ADD COLUMN group_slug VARCHAR(64) DEFAULT NULL AFTER pool_status")
        full_art_columns = {
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
        for column, definition in full_art_columns.items():
            if column not in columns:
                cur.execute(f"ALTER TABLE battlegrounds_library_cards ADD COLUMN `{column}` {definition}")
        if "group_name_ru" not in columns:
            cur.execute("ALTER TABLE battlegrounds_library_cards ADD COLUMN group_name_ru VARCHAR(180) DEFAULT NULL AFTER group_slug")
        if "tier_value" not in columns:
            cur.execute("ALTER TABLE battlegrounds_library_cards ADD COLUMN tier_value SMALLINT UNSIGNED DEFAULT NULL AFTER group_name_ru")
        if "tier_slug" not in columns:
            cur.execute("ALTER TABLE battlegrounds_library_cards ADD COLUMN tier_slug VARCHAR(64) DEFAULT NULL AFTER tier_value")
        if "tier_name_ru" not in columns:
            cur.execute("ALTER TABLE battlegrounds_library_cards ADD COLUMN tier_name_ru VARCHAR(180) DEFAULT NULL AFTER tier_slug")
        cur.execute("SHOW INDEX FROM battlegrounds_library_cards WHERE Key_name = 'idx_library_group'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE battlegrounds_library_cards ADD KEY idx_library_group (library, group_slug)")
        cur.execute("SHOW INDEX FROM battlegrounds_library_cards WHERE Key_name = 'idx_library_tier'")
        if not cur.fetchone():
            cur.execute("ALTER TABLE battlegrounds_library_cards ADD KEY idx_library_tier (library, tier_value)")
    conn.commit()


def http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "db.kolodahs.ru-library-sync/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=80) as resp:
        return json.load(resp)


def url_ok(url: str | None) -> bool:
    if not url:
        return False
    url = str(url)
    if url in URL_OK_CACHE:
        return URL_OK_CACHE[url]

    headers = {"User-Agent": "db.kolodahs.ru-library-sync/1.0"}
    for method, extra_headers in (("HEAD", {}), ("GET", {"Range": "bytes=0-16"})):
        try:
            req = urllib.request.Request(url, headers=headers | extra_headers, method=method)
            with urllib.request.urlopen(req, timeout=8) as resp:
                ok = 200 <= int(resp.status) < 400
                URL_OK_CACHE[url] = ok
                return ok
        except Exception:
            continue
    URL_OK_CACHE[url] = False
    return False


def mediawiki_parse_html(page_title: str) -> str:
    url = "https://hearthstone.wiki.gg/api.php?" + urllib.parse.urlencode(
        {
            "action": "parse",
            "page": page_title,
            "prop": "text",
            "format": "json",
        }
    )
    return http_json(url)["parse"]["text"]["*"]


def load_wiki_page_images(page_titles: list[str]) -> dict[str, str]:
    images: dict[str, str] = {}
    titles = [title for title in dict.fromkeys(page_titles) if title]
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        url = "https://hearthstone.wiki.gg/api.php?" + urllib.parse.urlencode(
            {
                "action": "query",
                "prop": "pageimages",
                "piprop": "original",
                "titles": "|".join(batch),
                "format": "json",
            }
        )
        data = http_json(url)
        pages = ((data.get("query") or {}).get("pages") or {}) if isinstance(data, dict) else {}
        for page in pages.values():
            if not isinstance(page, dict):
                continue
            title = str(page.get("title") or "")
            original = page.get("original") if isinstance(page.get("original"), dict) else {}
            source = str(original.get("source") or "")
            if title and source:
                images[title] = source
    return images


def wiki_page_url(title: str | None) -> str | None:
    if not title:
        return None
    return "https://hearthstone.wiki.gg/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe="/()_',.!:")


def links_between(page_html: str, start_id: str, end_id: str | None) -> list[str]:
    start = page_html.find(f'id="{start_id}"')
    if start < 0:
        return []
    end = page_html.find(f'id="{end_id}"', start + 1) if end_id else -1
    if end < 0:
        end = len(page_html)
    root = html.fromstring("<div>" + page_html[start:end] + "</div>")
    titles: list[str] = []
    seen: set[str] = set()
    for anchor in root.xpath('.//a[@href]'):
        href = anchor.get("href") or ""
        if "?" in href or "/wiki/Battlegrounds/" not in href:
            continue
        title = urllib.parse.unquote(href.split("#")[0].split("/wiki/", 1)[1]).replace("_", " ")
        if not title.startswith("Battlegrounds/"):
            continue
        if title in seen:
            continue
        seen.add(title)
        titles.append(title)
    return titles


def load_wiki_index(cache_dir: Path) -> list[dict[str, Any]]:
    path = cache_dir / "wiki-card-index-card.json"
    if not path.exists():
        raise RuntimeError(f"Missing wiki index: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError(f"Invalid wiki index: {path}")
    return [entry for entry in entries if isinstance(entry, dict)]


def load_hsj_cards(url: str) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    cards = http_json(url)
    if not isinstance(cards, list):
        return {}, {}
    by_dbf: dict[int, dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for card in cards:
        if not isinstance(card, dict):
            continue
        if card.get("dbfId") is not None:
            by_dbf[int(card["dbfId"])] = card
        if card.get("id"):
            by_id[str(card["id"])] = card
    return by_dbf, by_id


def localized(value: Any, locale: str = "ru_RU") -> Any:
    if isinstance(value, dict):
        return value.get(locale) or value.get("ru_RU") or value.get("en_US")
    return value


def load_official_cards(bg_type: str | None) -> dict[int, dict[str, Any]]:
    if not bg_type:
        return {}
    url = "https://hearthstone.blizzard.com/api/cards?" + urllib.parse.urlencode(
        {
            "locale": "ru_RU",
            "gameMode": "battlegrounds",
            "bgCardType": bg_type,
            "pageSize": "200",
        }
    )
    data = http_json(url)
    result: dict[int, dict[str, Any]] = {}
    for card in data.get("cards", []) if isinstance(data, dict) else []:
        if isinstance(card, dict) and card.get("id") is not None:
            result[int(card["id"])] = card
    return result


def render_image(card_id: str | None) -> str | None:
    if not card_id:
        return None
    return HSJ_RENDER_RU + urllib.parse.quote(card_id, safe="") + ".png"


def bgs_image(card_id: str | None) -> str | None:
    if not card_id:
        return None
    return HSJ_BGS_RU + urllib.parse.quote(card_id, safe="") + ".png"


def crop_image(card_id: str | None) -> str | None:
    if not card_id:
        return None
    return HSJ_ORIG_BASE + urllib.parse.quote(card_id, safe="") + ".png"


def official_image(card: dict[str, Any] | None, key: str) -> str | None:
    if not card:
        return None
    value = card.get(key)
    localized_value = localized(value)
    return str(localized_value) if localized_value else None


def first_working_url(candidates: list[str | None]) -> str | None:
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        candidate = str(candidate)
        if candidate in seen:
            continue
        seen.add(candidate)
        if url_ok(candidate):
            return candidate
    return None


def card_image_url(card_id: str, official: dict[str, Any] | None, wiki_image_url: str | None = None) -> str | None:
    return first_working_url([
        official_image(official, "image"),
        bgs_image(card_id),
        render_image(card_id),
        wiki_image_url,
    ])


def card_crop_url(card_id: str, official: dict[str, Any] | None) -> str | None:
    return first_working_url([
        official_image(official, "cropImage"),
        crop_image(card_id),
    ])


def trinket_group_from_card(
    ru_card: dict[str, Any] | None,
    en_card: dict[str, Any] | None,
    title: str | None = None,
) -> tuple[str | None, str | None]:
    spell_school = str((ru_card or en_card or {}).get("spellSchool") or "")
    if spell_school == "LESSER_TRINKET":
        return "lesser", "Малый аксессуар"
    if spell_school == "GREATER_TRINKET":
        return "greater", "Большой аксессуар"

    title = str(title or "")
    if "(Lesser)" in title:
        return "lesser", "Малый аксессуар"
    if "(Greater)" in title:
        return "greater", "Большой аксессуар"
    return None, None


def build_payload(
    library: str,
    entry: dict[str, Any],
    in_pool: bool,
    sort_order: int,
    hsj_ru_by_dbf: dict[int, dict[str, Any]],
    hsj_en_by_dbf: dict[int, dict[str, Any]],
    official_by_dbf: dict[int, dict[str, Any]],
    wiki_image_url: str | None = None,
    group_slug: str | None = None,
    group_name_ru: str | None = None,
    tier_value: int | None = None,
    tier_slug: str | None = None,
    tier_name_ru: str | None = None,
) -> dict[str, Any] | None:
    dbf = entry.get("dbf_id")
    dbf_int = int(dbf) if dbf is not None else None
    card_id = str(entry.get("card_id") or "")
    if not card_id:
        return None
    ru_card = hsj_ru_by_dbf.get(dbf_int) if dbf_int is not None else None
    en_card = hsj_en_by_dbf.get(dbf_int) if dbf_int is not None else None
    official = official_by_dbf.get(dbf_int) if dbf_int is not None else None
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    fallback = MANUAL_FALLBACKS.get(card_id, {})
    if library == "trinket" and not group_slug:
        group_slug, group_name_ru = trinket_group_from_card(ru_card, en_card, str(entry.get("page_title") or ""))
    name_ru = (ru_card or {}).get("name") or localized((official or {}).get("name")) or fallback.get("name_ru")
    if not name_ru:
        return None
    return {
        "library": library,
        "card_id": card_id,
        "dbf": dbf_int,
        "name_ru": name_ru,
        "name_en": (en_card or {}).get("name") or entry.get("name"),
        "text_ru": (ru_card or {}).get("text") or localized((official or {}).get("text")) or fallback.get("text_ru"),
        "text_en": (en_card or {}).get("text"),
        "image_url": card_image_url(card_id, official, wiki_image_url),
        "image_gold_url": first_working_url([official_image(official, "imageGold")]),
        "crop_image_url": card_crop_url(card_id, official),
        "artist": (ru_card or {}).get("artist") or localized((official or {}).get("artistName")) or details.get("artist"),
        "card_type": (ru_card or {}).get("type") or (en_card or {}).get("type"),
        "card_type_id": (official or {}).get("cardTypeId") or details.get("type_id"),
        "mana_cost": (official or {}).get("manaCost") if official else (ru_card or {}).get("cost"),
        "in_pool": bool(in_pool),
        "pool_status": "available" if in_pool else "removed",
        "group_slug": group_slug,
        "group_name_ru": group_name_ru,
        "tier_value": tier_value,
        "tier_slug": tier_slug,
        "tier_name_ru": tier_name_ru,
        "sort_order": sort_order,
        "wiki_page_title": entry.get("page_title"),
        "wiki_page_url": entry.get("page_url") or wiki_page_url(entry.get("page_title")),
        "source": SOURCE_WIKI,
        "source_payload": {
            "wiki_entry": entry,
            "hsj_ru": ru_card,
            "official": official,
        },
    }


def build_official_payload(
    library: str,
    dbf: int,
    official: dict[str, Any],
    hsj_ru_by_dbf: dict[int, dict[str, Any]],
    hsj_en_by_dbf: dict[int, dict[str, Any]],
    wiki_by_card_id: dict[str, dict[str, Any]],
    wiki_image_url: str | None = None,
    group_slug: str | None = None,
    group_name_ru: str | None = None,
    tier_value: int | None = None,
    tier_slug: str | None = None,
    tier_name_ru: str | None = None,
) -> dict[str, Any] | None:
    ru_card = hsj_ru_by_dbf.get(dbf)
    en_card = hsj_en_by_dbf.get(dbf)
    card_id = str((ru_card or en_card or {}).get("id") or "")
    if not card_id:
        return None
    wiki_entry = wiki_by_card_id.get(card_id)
    if library == "trinket" and not group_slug:
        group_slug, group_name_ru = trinket_group_from_card(ru_card, en_card, str((wiki_entry or {}).get("page_title") or ""))
    name_ru = (ru_card or {}).get("name") or localized(official.get("name"))
    if not name_ru:
        return None
    return {
        "library": library,
        "card_id": card_id,
        "dbf": dbf,
        "name_ru": name_ru,
        "name_en": (en_card or {}).get("name") or localized(official.get("name"), "en_US"),
        "text_ru": (ru_card or {}).get("text") or localized(official.get("text")),
        "text_en": (en_card or {}).get("text"),
        "image_url": card_image_url(card_id, official, wiki_image_url),
        "image_gold_url": first_working_url([official_image(official, "imageGold")]),
        "crop_image_url": card_crop_url(card_id, official),
        "artist": (ru_card or {}).get("artist") or localized(official.get("artistName")),
        "card_type": (ru_card or {}).get("type") or (en_card or {}).get("type"),
        "card_type_id": official.get("cardTypeId"),
        "mana_cost": official.get("manaCost"),
        "in_pool": True,
        "pool_status": "available",
        "group_slug": group_slug,
        "group_name_ru": group_name_ru,
        "tier_value": tier_value,
        "tier_slug": tier_slug,
        "tier_name_ru": tier_name_ru,
        "sort_order": 0,
        "wiki_page_title": wiki_entry.get("page_title") if wiki_entry else None,
        "wiki_page_url": wiki_entry.get("page_url") if wiki_entry else None,
        "source": SOURCE_BLIZZARD,
        "source_payload": {
            "official": official,
            "hsj_ru": ru_card,
            "wiki_entry": wiki_entry,
        },
    }


def current_hash(conn, library: str, card_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_hash FROM battlegrounds_library_cards WHERE library = %s AND card_id = %s",
            (library, card_id),
        )
        row = cur.fetchone()
    return row["source_hash"] if row else None


def save_payload(conn, payload: dict[str, Any], dry_run: bool) -> str:
    source_payload = dict(payload)
    new_hash = stable_hash(source_payload)
    old_hash = current_hash(conn, payload["library"], payload["card_id"])
    changed = old_hash != new_hash
    if dry_run:
        return "changed" if changed else "unchanged"
    now = utc_now()
    params = {
        **{key: value for key, value in payload.items() if key != "source_payload"},
        "in_pool": 1 if payload["in_pool"] else 0,
        "source_payload_json": json_dump(source_payload),
        "source_hash": new_hash,
        "fetched_at": now,
        "changed_at": now,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO battlegrounds_library_cards (
                library, card_id, dbf, name_ru, name_en, text_ru, text_en,
                image_url, image_gold_url, crop_image_url, artist, card_type,
                card_type_id, mana_cost, in_pool, pool_status, group_slug,
                group_name_ru, tier_value, tier_slug, tier_name_ru, sort_order,
                wiki_page_title, wiki_page_url, source, source_payload_json,
                source_hash, fetched_at, changed_at
            ) VALUES (
                %(library)s, %(card_id)s, %(dbf)s, %(name_ru)s, %(name_en)s, %(text_ru)s, %(text_en)s,
                %(image_url)s, %(image_gold_url)s, %(crop_image_url)s, %(artist)s, %(card_type)s,
                %(card_type_id)s, %(mana_cost)s, %(in_pool)s, %(pool_status)s, %(group_slug)s,
                %(group_name_ru)s, %(tier_value)s, %(tier_slug)s, %(tier_name_ru)s, %(sort_order)s,
                %(wiki_page_title)s, %(wiki_page_url)s, %(source)s, %(source_payload_json)s,
                %(source_hash)s, %(fetched_at)s, %(changed_at)s
            )
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf),
                name_ru = VALUES(name_ru),
                name_en = VALUES(name_en),
                text_ru = VALUES(text_ru),
                text_en = VALUES(text_en),
                image_url = VALUES(image_url),
                image_gold_url = VALUES(image_gold_url),
                crop_image_url = VALUES(crop_image_url),
                artist = VALUES(artist),
                card_type = VALUES(card_type),
                card_type_id = VALUES(card_type_id),
                mana_cost = VALUES(mana_cost),
                in_pool = VALUES(in_pool),
                pool_status = VALUES(pool_status),
                group_slug = VALUES(group_slug),
                group_name_ru = VALUES(group_name_ru),
                tier_value = VALUES(tier_value),
                tier_slug = VALUES(tier_slug),
                tier_name_ru = VALUES(tier_name_ru),
                sort_order = VALUES(sort_order),
                wiki_page_title = VALUES(wiki_page_title),
                wiki_page_url = VALUES(wiki_page_url),
                source = VALUES(source),
                source_payload_json = VALUES(source_payload_json),
                fetched_at = VALUES(fetched_at),
                changed_at = IF(
                    battlegrounds_library_cards.source_hash <> VALUES(source_hash)
                    OR battlegrounds_library_cards.source_hash IS NULL,
                    VALUES(changed_at),
                    changed_at
                ),
                source_hash = VALUES(source_hash)
            """,
            params,
        )
    return "changed" if changed else "unchanged"


def sync_library(
    conn,
    library: str,
    cache_dir: Path,
    hsj_ru_by_dbf: dict[int, dict[str, Any]],
    hsj_en_by_dbf: dict[int, dict[str, Any]],
    wiki_entries: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, int]:
    config = LIBRARIES[library]
    official_by_dbf = load_official_cards(config["official_bg_type"])
    wiki_by_title = {str(entry.get("page_title")): entry for entry in wiki_entries if entry.get("page_title")}
    wiki_by_card_id = {str(entry.get("card_id")): entry for entry in wiki_entries if entry.get("card_id")}
    payloads: list[dict[str, Any]] = []

    if library == "dark_gift":
        # Season 14 Dark Gifts are real display cards in HearthstoneJSON, but
        # Blizzard's auxiliary Battlegrounds API does not expose a dedicated
        # bgCardType for them. Their stable BG36_MidGameEffect token IDs are
        # therefore the authoritative discovery contract for this library.
        dark_gifts = [
            (dbf, card)
            for dbf, card in hsj_en_by_dbf.items()
            if str(card.get("id") or "").startswith("BG36_MidGameEffect_000t")
            and card.get("type") == "SPELL"
        ]
        for idx, (dbf, en_card) in enumerate(sorted(dark_gifts, key=lambda item: str(item[1].get("id") or "")), start=1):
            card_id = str(en_card.get("id") or "")
            payload = build_payload(
                library,
                {"card_id": card_id, "dbf_id": dbf, "name": en_card.get("name")},
                True,
                idx,
                hsj_ru_by_dbf,
                hsj_en_by_dbf,
                official_by_dbf,
            )
            if payload:
                payload["source"] = SOURCE_HSJ
                payloads.append(payload)
    elif library == "reward":
        reward_titles: list[str] = []
        for dbf in official_by_dbf:
            ru_card = hsj_ru_by_dbf.get(dbf)
            en_card = hsj_en_by_dbf.get(dbf)
            card_id = str((ru_card or en_card or {}).get("id") or "")
            wiki_entry = wiki_by_card_id.get(card_id) if card_id else None
            title = str((wiki_entry or {}).get("page_title") or "")
            if title:
                reward_titles.append(title)
        wiki_images = load_wiki_page_images(reward_titles)
        for idx, (dbf, official) in enumerate(sorted(official_by_dbf.items()), start=1):
            ru_card = hsj_ru_by_dbf.get(dbf)
            en_card = hsj_en_by_dbf.get(dbf)
            card_id = str((ru_card or en_card or {}).get("id") or "")
            wiki_entry = wiki_by_card_id.get(card_id) if card_id else None
            wiki_image_url = wiki_images.get(str((wiki_entry or {}).get("page_title") or ""))
            payload = build_official_payload(
                library,
                dbf,
                official,
                hsj_ru_by_dbf,
                hsj_en_by_dbf,
                wiki_by_card_id,
                wiki_image_url,
            )
            if payload:
                payload["sort_order"] = idx
                payloads.append(payload)
    else:
        page = str(config["wiki_page"])
        rendered = mediawiki_parse_html(page)
        removed_start, removed_end = config["removed_section"]
        active_titles: list[str] = []
        active_group_by_title: dict[str, tuple[str | None, str | None]] = {}
        active_tier_by_title: dict[str, tuple[int | None, str | None, str | None]] = {}
        if config.get("active_tier_sections"):
            for tier_value, tier_slug, tier_name_ru, active_start, active_end in config["active_tier_sections"]:
                for title in links_between(rendered, active_start, active_end):
                    active_titles.append(title)
                    active_tier_by_title[title] = (tier_value, tier_slug, tier_name_ru)
        elif config.get("active_group_sections"):
            for group_slug, group_name_ru, active_start, active_end in config["active_group_sections"]:
                for title in links_between(rendered, active_start, active_end):
                    active_titles.append(title)
                    active_group_by_title[title] = (group_slug, group_name_ru)
        else:
            active_start, active_end = config["active_section"]
            active_titles = links_between(rendered, active_start, active_end)
        removed_titles = links_between(rendered, removed_start, removed_end)
        wiki_images = load_wiki_page_images(active_titles + removed_titles)
        seen: set[str] = set()
        ordered = [(title, True) for title in active_titles] + [(title, False) for title in removed_titles if title not in active_titles]
        for idx, (title, in_pool) in enumerate(ordered, start=1):
            entry = wiki_by_title.get(title)
            if not entry:
                print(json_dump({"library": library, "missing_wiki_index_title": title}), file=sys.stderr)
                continue
            card_id = str(entry.get("card_id") or "")
            if not card_id or card_id in seen:
                continue
            seen.add(card_id)
            group_slug, group_name_ru = active_group_by_title.get(title, (None, None))
            tier_value, tier_slug, tier_name_ru = active_tier_by_title.get(title, (None, None, None))
            payload = build_payload(
                library,
                entry,
                in_pool,
                idx,
                hsj_ru_by_dbf,
                hsj_en_by_dbf,
                official_by_dbf,
                wiki_images.get(title),
                group_slug,
                group_name_ru,
                tier_value,
                tier_slug,
                tier_name_ru,
            )
            if payload:
                payloads.append(payload)

        if library == "trinket":
            # The wiki's active tables can lag behind a new Battlegrounds
            # season. Supplement only the newest BGxx trinket generation from
            # HearthstoneJSON; wiki-backed records remain authoritative once
            # their pages appear and are already protected by ``seen``.
            newest_generation = max(
                (
                    int(match.group(1))
                    for card in hsj_en_by_dbf.values()
                    if card.get("type") == "BATTLEGROUND_TRINKET"
                    for match in [re.match(r"BG(\d+)_MagicItem_", str(card.get("id") or ""))]
                    if match
                ),
                default=None,
            )
            if newest_generation is not None:
                prefix = f"BG{newest_generation}_MagicItem_"
                supplements = [
                    (dbf, card)
                    for dbf, card in hsj_en_by_dbf.items()
                    if card.get("type") == "BATTLEGROUND_TRINKET"
                    and str(card.get("id") or "").startswith(prefix)
                    and str(card.get("id") or "") not in seen
                ]
                for supplement_idx, (dbf, en_card) in enumerate(
                    sorted(supplements, key=lambda item: str(item[1].get("id") or "")),
                    start=len(payloads) + 1,
                ):
                    card_id = str(en_card.get("id") or "")
                    payload = build_payload(
                        library,
                        {"card_id": card_id, "dbf_id": dbf, "name": en_card.get("name")},
                        True,
                        supplement_idx,
                        hsj_ru_by_dbf,
                        hsj_en_by_dbf,
                        official_by_dbf,
                    )
                    if payload:
                        payload["source"] = SOURCE_HSJ
                        payloads.append(payload)

    stats = {"scanned": len(payloads), "changed": 0, "unchanged": 0}
    for payload in payloads:
        outcome = save_payload(conn, payload, dry_run)
        stats[outcome] += 1
    return stats


def main() -> int:
    argp = argparse.ArgumentParser(description="Sync Battlegrounds auxiliary libraries.")
    argp.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    argp.add_argument("--library", choices=["all", *LIBRARIES.keys()], default="all")
    argp.add_argument("--dry-run", action="store_true")
    args = argp.parse_args()

    cache_dir = Path(args.cache_dir)
    hsj_ru_by_dbf, _ = load_hsj_cards(HSJ_RU_URL)
    hsj_en_by_dbf, _ = load_hsj_cards(HSJ_EN_URL)
    wiki_entries = load_wiki_index(cache_dir)
    libraries = list(LIBRARIES.keys()) if args.library == "all" else [args.library]
    conn = connect_db(load_php_config())
    try:
        ensure_schema(conn)
        print(json_dump({"libraries": libraries, "hsj_ru": len(hsj_ru_by_dbf), "dry_run": args.dry_run}))
        all_stats = {}
        for library in libraries:
            stats = sync_library(conn, library, cache_dir, hsj_ru_by_dbf, hsj_en_by_dbf, wiki_entries, args.dry_run)
            all_stats[library] = stats
            print(json_dump({library: stats}))
        if not args.dry_run:
            conn.commit()
        print(json_dump({"done": all_stats}))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
