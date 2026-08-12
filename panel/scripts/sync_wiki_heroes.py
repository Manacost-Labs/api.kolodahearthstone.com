#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import re
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
HSJ_RU_URL = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.json"
HSJ_BGS_RENDER_BASE = "https://art.hearthstonejson.com/v1/bgs/latest/ruRU/512x/"
HSJ_RENDER_BASE = "https://art.hearthstonejson.com/v1/render/latest/ruRU/512x/"
HSJ_ORIG_BASE = "https://art.hearthstonejson.com/v1/orig/"
RELATED_CARD_NETWORK_DELAY_SECONDS = float(os.getenv("KOLODAHS_RELATED_CARD_DELAY_SECONDS", "0.35"))


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
            CREATE TABLE IF NOT EXISTS battlegrounds_heroes (
                card_id VARCHAR(64) NOT NULL,
                dbf INT UNSIGNED NOT NULL,
                hero_id INT UNSIGNED DEFAULT NULL,
                name_en VARCHAR(160) NOT NULL,
                name_ru VARCHAR(160) DEFAULT NULL,
                health SMALLINT DEFAULT NULL,
                armor SMALLINT DEFAULT NULL,
                duos_armor SMALLINT DEFAULT NULL,
                armor_text VARCHAR(64) DEFAULT NULL,
                artist VARCHAR(255) DEFAULT NULL,
                race VARCHAR(128) DEFAULT NULL,
                character_name VARCHAR(160) DEFAULT NULL,
                as_hero TEXT DEFAULT NULL,
                hero_description TEXT DEFAULT NULL,
                hero_image_url VARCHAR(512) DEFAULT NULL,
                hero_full_art_url VARCHAR(512) DEFAULT NULL,
                wiki_page_title VARCHAR(255) DEFAULT NULL,
                wiki_page_url VARCHAR(512) DEFAULT NULL,
                hero_power_dbf INT UNSIGNED DEFAULT NULL,
                hero_power_json JSON DEFAULT NULL,
                buddy_dbf INT UNSIGNED DEFAULT NULL,
                buddy_json JSON DEFAULT NULL,
                availability_json JSON DEFAULT NULL,
                hero_skins_json JSON DEFAULT NULL,
                gallery_json JSON DEFAULT NULL,
                card_changes_json JSON DEFAULT NULL,
                external_links_json JSON DEFAULT NULL,
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
                KEY idx_hero_id (hero_id),
                KEY idx_hero_power_dbf (hero_power_dbf),
                KEY idx_buddy_dbf (buddy_dbf),
                KEY idx_status (status),
                KEY idx_changed_at (changed_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS battlegrounds_hero_related (
                id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                hero_card_id VARCHAR(64) NOT NULL,
                related_card_id VARCHAR(64) DEFAULT NULL,
                heading VARCHAR(255) DEFAULT NULL,
                title VARCHAR(255) DEFAULT NULL,
                caption VARCHAR(255) DEFAULT NULL,
                url VARCHAR(512) DEFAULT NULL,
                image_url VARCHAR(512) DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_hero_card_id (hero_card_id),
                KEY idx_related_card_id (related_card_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS battlegrounds_hero_skins (
                id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                hero_card_id VARCHAR(64) NOT NULL,
                skin_card_id VARCHAR(64) DEFAULT NULL,
                title VARCHAR(255) DEFAULT NULL,
                url VARCHAR(512) DEFAULT NULL,
                image_url VARCHAR(512) DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_hero_card_id (hero_card_id),
                KEY idx_skin_card_id (skin_card_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS battlegrounds_hero_gallery (
                id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                hero_card_id VARCHAR(64) NOT NULL,
                caption VARCHAR(255) DEFAULT NULL,
                file_title VARCHAR(255) DEFAULT NULL,
                file_url VARCHAR(512) DEFAULT NULL,
                thumb_url VARCHAR(512) DEFAULT NULL,
                file_page_url VARCHAR(512) DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_hero_card_id (hero_card_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    conn.commit()


def import_parser(parser_path: Path):
    sys.path.insert(0, str(parser_path))
    import wiki_hs_lookup as lookup
    import wiki_hs_parser as parser

    return lookup, parser


def cache_path(cache_dir: Path, safe_filename, page_title: str) -> Path:
    return cache_dir / "hero-pages" / f"{safe_filename(page_title)}.json"


def rendered_cache_path(cache_dir: Path, safe_filename, page_title: str) -> Path:
    return cache_dir / "hero-rendered" / f"{safe_filename(page_title)}.html"


def related_card_cache_path(cache_dir: Path, safe_filename, page_title: str) -> Path:
    return cache_dir / "related-card-pages" / f"{safe_filename(page_title)}.json"


def load_or_fetch_page(cache_dir: Path, parser, safe_filename, page_title: str, refresh: bool) -> tuple[dict[str, Any], str]:
    path = cache_path(cache_dir, safe_filename, page_title)
    if not refresh and path.exists():
        return json.loads(path.read_text(encoding="utf-8")), "cache"

    result = parser.build_result(page_title, download=False, output_dir=cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if RELATED_CARD_NETWORK_DELAY_SECONDS > 0:
        time.sleep(RELATED_CARD_NETWORK_DELAY_SECONDS)
    return result, "network"


def load_or_fetch_rendered(cache_dir: Path, parser, safe_filename, page_title: str, refresh: bool) -> str:
    path = rendered_cache_path(cache_dir, safe_filename, page_title)
    if not refresh and path.exists():
        return path.read_text(encoding="utf-8")
    html = parser.get_rendered_html(page_title)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return html


def load_or_fetch_related_card_page(cache_dir: Path, parser, safe_filename, page_title: str, refresh: bool) -> tuple[dict[str, Any], str]:
    path = related_card_cache_path(cache_dir, safe_filename, page_title)
    if not refresh and path.exists():
        return json.loads(path.read_text(encoding="utf-8")), "cache"

    result = parser.build_result(page_title, download=False, output_dir=cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, "network"


def extract_section_text(parser, rendered_html: str, section_ids: list[str]) -> str | None:
    for section_id in section_ids:
        parts = []
        for node in parser.iter_section_nodes(parser.html.fromstring(rendered_html), section_id):
            if not isinstance(node.tag, str) or node.tag in {"h2", "h3", "h4"}:
                continue
            text = parser.extract_text(node)
            if text:
                parts.append(text)
        if parts:
            text = "\n".join(parts)
            return re.sub(r"\n{3,}", "\n\n", text).strip()
    return None


class BlizzardClient:
    def __init__(self, client_id: str | None, client_secret: str | None, region: str = "us", locale: str = "ru_RU") -> None:
        self.client_id = client_id or ""
        self.client_secret = client_secret or ""
        self.region = region
        self.locale = locale
        self._token: str | None = None

    def available(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def token(self) -> str:
        if self._token:
            return self._token
        if not self.available():
            raise RuntimeError("Blizzard credentials are not configured")
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        req = urllib.request.Request(
            "https://oauth.battle.net/token",
            data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
            headers={
                "Authorization": "Basic " + credentials,
                "User-Agent": "db.kolodahs.ru-hero-sync/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            self._token = json.load(resp)["access_token"]
        return self._token

    def card_by_dbf(self, dbf: int | None) -> dict[str, Any] | None:
        if dbf is None or not self.available():
            return None
        url = f"https://{self.region}.api.blizzard.com/hearthstone/cards/{dbf}?" + urllib.parse.urlencode({"locale": self.locale})
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": "Bearer " + self.token(),
                "User-Agent": "db.kolodahs.ru-hero-sync/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception:
            return None


def compact_blizzard_card(card: dict[str, Any] | None) -> dict[str, Any] | None:
    if not card:
        return None
    return {
        "dbf": card.get("id"),
        "name": card.get("name"),
        "text": card.get("text"),
        "image": card.get("image"),
        "image_gold": card.get("imageGold"),
        "crop_image": card.get("cropImage"),
        "parent_id": card.get("parentId"),
        "card_type_id": card.get("cardTypeId"),
        "artist": card.get("artistName"),
    }


def compact_local_card(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    image = row.get("card_image") or row.get("framed_image") or row.get("art_image")
    return {
        "dbf": row.get("dbf"),
        "card_id": row.get("card_id"),
        "name": row.get("name") or row.get("name_en"),
        "name_en": row.get("name_en"),
        "text": row.get("notes"),
        "image": image,
        "image_gold": row.get("golden_image"),
        "crop_image": row.get("art_image") or image,
        "card_type": row.get("card_type"),
        "attack": row.get("attack"),
        "health": row.get("health"),
        "tavern_tier": row.get("tavern_tier"),
    }


def compact_hsj_card(
    card: dict[str, Any] | None,
    cards_by_dbf: dict[int, dict[str, Any]] | None = None,
    wiki_golden_images_by_dbf: dict[int, dict[str, str | None]] | None = None,
) -> dict[str, Any] | None:
    if not card:
        return None
    card_id = card.get("id")
    encoded_id = urllib.parse.quote(str(card_id), safe="") if card_id else None
    image = f"{HSJ_BGS_RENDER_BASE}{encoded_id}.png" if encoded_id else None
    crop_image = f"{HSJ_ORIG_BASE}{encoded_id}.png" if encoded_id else image
    premium_dbf = card.get("battlegroundsPremiumDbfId")
    premium_card = None
    if premium_dbf is not None and cards_by_dbf:
        premium_card = cards_by_dbf.get(int(premium_dbf))
    premium_card_id = premium_card.get("id") if premium_card else None
    wiki_golden = wiki_golden_images_by_dbf.get(int(premium_dbf)) if premium_dbf is not None and wiki_golden_images_by_dbf else None
    image_gold = wiki_golden.get("image") if wiki_golden else None
    return {
        "dbf": card.get("dbfId"),
        "card_id": card_id,
        "name": card.get("name"),
        "text": card.get("text"),
        "image": image,
        "image_gold": image_gold,
        "crop_image": crop_image,
        "card_type": card.get("type"),
        "golden": {
            "dbf": premium_card.get("dbfId"),
            "card_id": premium_card_id,
            "name": premium_card.get("name"),
            "text": premium_card.get("text"),
            "image": image_gold,
            "page_title": wiki_golden.get("page_title") if wiki_golden else None,
            "page_url": wiki_golden.get("page_url") if wiki_golden else None,
            "card_type": premium_card.get("type"),
        }
        if premium_card
        else None,
    }


def compact_wiki_index_card(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    card_id = entry.get("card_id")
    encoded_id = urllib.parse.quote(str(card_id), safe="") if card_id else None
    image = f"{HSJ_BGS_RENDER_BASE}{encoded_id}.png" if encoded_id else None
    return {
        "dbf": entry.get("dbf_id"),
        "card_id": card_id,
        "name": entry.get("name"),
        "image": image,
        "crop_image": image,
        "page_title": entry.get("page_title"),
        "page_url": entry.get("page_url"),
        "attack": details.get("attack"),
        "health": details.get("health"),
        "tavern_tier": details.get("tier"),
    }


def first_card(*cards: dict[str, Any] | None) -> dict[str, Any] | None:
    for card in cards:
        if card:
            return card
    return None


def merge_cards(*cards: dict[str, Any] | None) -> dict[str, Any] | None:
    merged: dict[str, Any] | None = None
    for card in cards:
        if not card:
            continue
        if merged is None:
            merged = dict(card)
            continue
        for key, value in card.items():
            if value is None or value == "":
                continue
            if merged.get(key) is None or merged.get(key) == "":
                merged[key] = value
    return merged


def merge_cards_with_ru_render(*cards: dict[str, Any] | None) -> dict[str, Any] | None:
    merged = merge_cards(*cards)
    if not merged:
        return None
    for card in cards:
        if not card:
            continue
        image = card.get("image")
        if isinstance(image, str) and "art.hearthstonejson.com/v1/bgs/latest/ruRU/" in image:
            merged["image"] = image
            break
    return merged


def load_local_cards_by_dbf(conn) -> dict[int, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT dbf, card_id, name, name_en, card_type, attack, health, tavern_tier,
                   card_image, golden_image, art_image, framed_image, notes
            FROM battlegrounds_cards
            WHERE dbf IS NOT NULL
            """
        )
        rows = cur.fetchall()
    return {int(row["dbf"]): row for row in rows if row.get("dbf") is not None}


def load_hsj_cards_by_dbf() -> dict[int, dict[str, Any]]:
    req = urllib.request.Request(
        HSJ_RU_URL,
        headers={"User-Agent": "db.kolodahs.ru-hero-sync/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            cards = json.load(resp)
    except Exception:
        return {}
    if not isinstance(cards, list):
        return {}
    return {int(card["dbfId"]): card for card in cards if card.get("dbfId") is not None}


def build_wiki_cards_by_dbf(entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_dbf: dict[int, dict[str, Any]] = {}
    for entry in entries:
        dbf = entry.get("dbf_id")
        if dbf is not None:
            by_dbf[int(dbf)] = entry
    return by_dbf


def chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[idx : idx + size] for idx in range(0, len(values), size)]


def wiki_page_url(title: str) -> str:
    return "https://hearthstone.wiki.gg/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe="/()_',")


def load_wiki_pageimages(titles: list[str]) -> dict[str, dict[str, str | None]]:
    found: dict[str, dict[str, str | None]] = {}
    unique_titles = sorted({title for title in titles if title})
    for batch in chunks(unique_titles, 50):
        params = {
            "action": "query",
            "titles": "|".join(batch),
            "prop": "pageimages",
            "pithumbsize": "512",
            "format": "json",
        }
        url = "https://hearthstone.wiki.gg/api.php?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "db.kolodahs.ru-hero-sync/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
        except Exception:
            continue
        pages = data.get("query", {}).get("pages", {})
        if not isinstance(pages, dict):
            continue
        for page in pages.values():
            if not isinstance(page, dict) or page.get("missing") is not None:
                continue
            title = page.get("title")
            image = page.get("thumbnail", {}).get("source") if isinstance(page.get("thumbnail"), dict) else None
            if title and image:
                found[str(title)] = {
                    "image": str(image),
                    "page_title": str(title),
                    "page_url": wiki_page_url(str(title)),
                }
    return found


def build_wiki_golden_images_by_dbf(local_cards_by_dbf: dict[int, dict[str, Any]], hsj_cards_by_dbf: dict[int, dict[str, Any]]) -> dict[int, dict[str, str | None]]:
    title_by_premium_dbf: dict[int, str] = {}
    for dbf, local_card in local_cards_by_dbf.items():
        hsj_card = hsj_cards_by_dbf.get(dbf)
        if not hsj_card:
            continue
        premium_dbf = hsj_card.get("battlegroundsPremiumDbfId")
        name_en = local_card.get("name_en")
        if premium_dbf is None or not name_en:
            continue
        title_by_premium_dbf[int(premium_dbf)] = f"Battlegrounds/{name_en} (golden)"

    pageimages = load_wiki_pageimages(list(title_by_premium_dbf.values()))
    by_dbf: dict[int, dict[str, str | None]] = {}
    for premium_dbf, title in title_by_premium_dbf.items():
        image = pageimages.get(title)
        if image:
            by_dbf[premium_dbf] = image
    return by_dbf


def normalize_card_links(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        clips = [
            {
                "group": clip.get("group"),
                "file_title": clip.get("file_title"),
                "file_url": clip.get("file_url"),
                "description": clip.get("description"),
            }
            for clip in (group.get("clips") or [])
        ]
        if clips:
            normalized.append({"heading": group.get("heading"), "clips": clips})
    return normalized


def related_page_title_for_card(card: dict[str, Any] | None, dbf: int | None, wiki_cards_by_dbf: dict[int, dict[str, Any]]) -> str | None:
    if not card and dbf is None:
        return None
    entry = wiki_cards_by_dbf.get(int(dbf)) if dbf is not None else None
    if entry and entry.get("page_title"):
        return str(entry["page_title"])
    if card and card.get("page_title"):
        return str(card["page_title"])
    if card:
        name_en = card.get("name_en")
        if name_en:
            return f"Battlegrounds/{name_en}"
    return None


def enrich_related_card_from_wiki(
    card: dict[str, Any] | None,
    dbf: int | None,
    cache_dir: Path,
    parser,
    refresh: bool,
    wiki_cards_by_dbf: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    if not card:
        return None

    page_title = related_page_title_for_card(card, dbf, wiki_cards_by_dbf)
    if not page_title:
        return card

    enriched = dict(card)
    try:
        result, source = load_or_fetch_related_card_page(cache_dir, parser, parser.safe_filename, page_title, refresh)
    except Exception:
        return card

    gallery = normalize_gallery(result.get("gallery_images", []) or [])
    sounds = normalize_sounds(result.get("sounds", []) or [])
    arts = result.get("arts", []) or []
    first_full_art = next((item.get("file_url") for item in gallery if item.get("file_url")), None)
    if not first_full_art and arts:
        first_full_art = arts[0].get("file_url")

    enriched["wiki"] = {
        "page_title": result.get("page_title") or page_title,
        "page_url": result.get("page_url") or wiki_page_url(page_title),
        "cache_source": source,
    }
    if gallery:
        enriched["gallery"] = gallery
    if first_full_art:
        enriched["full_art_url"] = first_full_art
    if sounds:
        enriched["sounds"] = sounds

    return enriched


def normalize_external_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"label": item.get("label"), "url": item.get("url")} for item in links or []]


def normalize_patch_changes(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def build_payload(
    entry: dict[str, Any],
    result: dict[str, Any],
    rendered_html: str,
    parser,
    cache_dir: Path,
    refresh_pages: bool,
    blizzard: BlizzardClient,
    local_cards_by_dbf: dict[int, dict[str, Any]],
    hsj_cards_by_dbf: dict[int, dict[str, Any]],
    wiki_cards_by_dbf: dict[int, dict[str, Any]],
    wiki_golden_images_by_dbf: dict[int, dict[str, str | None]],
) -> dict[str, Any]:
    card_data = result.get("card_data") if isinstance(result.get("card_data"), dict) else {}
    hero_power_dbf = card_data.get("hero_power_dbf_id") or entry.get("details", {}).get("hero_power_dbf_id")
    buddy_dbf = card_data.get("buddy_dbf_id") or entry.get("details", {}).get("buddy_dbf_id")
    hero_power = merge_cards_with_ru_render(
        compact_blizzard_card(blizzard.card_by_dbf(hero_power_dbf)),
        compact_local_card(local_cards_by_dbf.get(int(hero_power_dbf))) if hero_power_dbf is not None else None,
        compact_hsj_card(hsj_cards_by_dbf.get(int(hero_power_dbf)), hsj_cards_by_dbf, wiki_golden_images_by_dbf) if hero_power_dbf is not None else None,
        compact_wiki_index_card(wiki_cards_by_dbf.get(int(hero_power_dbf))) if hero_power_dbf is not None else None,
    )
    buddy = merge_cards_with_ru_render(
        compact_blizzard_card(blizzard.card_by_dbf(buddy_dbf)),
        compact_local_card(local_cards_by_dbf.get(int(buddy_dbf))) if buddy_dbf is not None else None,
        compact_hsj_card(hsj_cards_by_dbf.get(int(buddy_dbf)), hsj_cards_by_dbf, wiki_golden_images_by_dbf) if buddy_dbf is not None else None,
        compact_wiki_index_card(wiki_cards_by_dbf.get(int(buddy_dbf))) if buddy_dbf is not None else None,
    )
    hero_power = enrich_related_card_from_wiki(hero_power, hero_power_dbf, cache_dir, parser, refresh_pages, wiki_cards_by_dbf)
    buddy = enrich_related_card_from_wiki(buddy, buddy_dbf, cache_dir, parser, refresh_pages, wiki_cards_by_dbf)
    hero_ru = merge_cards(
        compact_blizzard_card(blizzard.card_by_dbf(entry.get("dbf_id"))),
        compact_hsj_card(hsj_cards_by_dbf.get(int(entry["dbf_id"])), hsj_cards_by_dbf, wiki_golden_images_by_dbf) if entry.get("dbf_id") is not None else None,
    )
    gallery = normalize_gallery(result.get("gallery_images", []) or [])
    arts = result.get("arts", []) or []

    return {
        "source": SOURCE,
        "card_id": entry["card_id"],
        "dbf": entry["dbf_id"],
        "hero_id": card_data.get("hero_id"),
        "name_en": card_data.get("name") or entry.get("name"),
        "name_ru": (hero_ru or {}).get("name"),
        "health": card_data.get("health") or entry.get("details", {}).get("health"),
        "armor": card_data.get("armor") or entry.get("details", {}).get("armor"),
        "duos_armor": card_data.get("duos_armor") or entry.get("details", {}).get("duos_armor"),
        "armor_text": card_data.get("armor_text"),
        "artist": card_data.get("artist") or entry.get("details", {}).get("artist"),
        "race": card_data.get("race"),
        "character_name": card_data.get("character"),
        "as_hero": extract_section_text(parser, rendered_html, ["As_a_hero", "As_a_Hero"]),
        "hero_description": card_data.get("hero_description"),
        "hero_image_url": (arts[0].get("file_url") if arts else None) or (hero_ru or {}).get("image"),
        "hero_full_art_url": next((item.get("file_url") for item in gallery if item.get("file_url")), None),
        "wiki_page_title": result.get("page_title") or entry.get("page_title"),
        "wiki_page_url": result.get("page_url") or entry.get("page_url"),
        "hero_power_dbf": hero_power_dbf,
        "hero_power": hero_power,
        "buddy_dbf": buddy_dbf,
        "buddy": buddy,
        "availability": normalize_availability(card_data, result),
        "hero_skins": normalize_card_links(result.get("hero_skins", []) or []),
        "gallery": gallery,
        "card_changes": normalize_patch_changes(result.get("patch_changes", []) or []),
        "external_links": normalize_external_links(result.get("external_links", []) or []),
        "related_cards": normalize_card_links(result.get("related_cards", []) or []),
        "source_result": result,
    }


def current_hash(conn, card_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT source_hash FROM battlegrounds_heroes WHERE card_id = %s", (card_id,))
        row = cur.fetchone()
    return row["source_hash"] if row else None


def save_child_tables(conn, payload: dict[str, Any]) -> None:
    card_id = payload["card_id"]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM battlegrounds_hero_related WHERE hero_card_id = %s", (card_id,))
        cur.execute("DELETE FROM battlegrounds_hero_skins WHERE hero_card_id = %s", (card_id,))
        cur.execute("DELETE FROM battlegrounds_hero_gallery WHERE hero_card_id = %s", (card_id,))

        related_rows = []
        for group in payload.get("related_cards") or []:
            for card in group.get("cards") or []:
                related_rows.append(
                    (card_id, card.get("card_id"), group.get("heading"), card.get("title"), card.get("caption"), card.get("url"), card.get("image_url"))
                )
        if related_rows:
            cur.executemany(
                """
                INSERT INTO battlegrounds_hero_related
                (hero_card_id, related_card_id, heading, title, caption, url, image_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                related_rows,
            )

        skin_rows = []
        for group in payload.get("hero_skins") or []:
            for card in group.get("cards") or []:
                skin_rows.append((card_id, card.get("card_id"), card.get("title"), card.get("url"), card.get("image_url")))
        if skin_rows:
            cur.executemany(
                """
                INSERT INTO battlegrounds_hero_skins
                (hero_card_id, skin_card_id, title, url, image_url)
                VALUES (%s, %s, %s, %s, %s)
                """,
                skin_rows,
            )

        gallery_rows = [
            (card_id, item.get("caption"), item.get("file_title"), item.get("file_url"), item.get("thumb_url"), item.get("file_page_url"))
            for item in payload.get("gallery") or []
        ]
        if gallery_rows:
            cur.executemany(
                """
                INSERT INTO battlegrounds_hero_gallery
                (hero_card_id, caption, file_title, file_url, thumb_url, file_page_url)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                gallery_rows,
            )


def save_payload(conn, payload: dict[str, Any], dry_run: bool) -> str:
    source_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"source_result"}
    }
    source_payload["source_result"] = payload.get("source_result")
    new_hash = stable_hash(source_payload)
    old_hash = current_hash(conn, payload["card_id"])
    changed = old_hash != new_hash
    if dry_run:
        return "changed" if changed else "unchanged"

    now = utc_now()
    params = {
        "card_id": payload["card_id"],
        "dbf": payload["dbf"],
        "hero_id": payload["hero_id"],
        "name_en": payload["name_en"],
        "name_ru": payload["name_ru"],
        "health": payload["health"],
        "armor": payload["armor"],
        "duos_armor": payload["duos_armor"],
        "armor_text": payload["armor_text"],
        "artist": payload["artist"],
        "race": payload["race"],
        "character_name": payload["character_name"],
        "as_hero": payload["as_hero"],
        "hero_description": payload["hero_description"],
        "hero_image_url": payload["hero_image_url"],
        "hero_full_art_url": payload["hero_full_art_url"],
        "wiki_page_title": payload["wiki_page_title"],
        "wiki_page_url": payload["wiki_page_url"],
        "hero_power_dbf": payload["hero_power_dbf"],
        "hero_power_json": json_dump(payload.get("hero_power")),
        "buddy_dbf": payload["buddy_dbf"],
        "buddy_json": json_dump(payload.get("buddy")),
        "availability_json": json_dump(payload.get("availability")),
        "hero_skins_json": json_dump(payload.get("hero_skins")),
        "gallery_json": json_dump(payload.get("gallery")),
        "card_changes_json": json_dump(payload.get("card_changes")),
        "external_links_json": json_dump(payload.get("external_links")),
        "source_payload_json": json_dump(source_payload),
        "source_hash": new_hash,
        "fetched_at": now,
        "changed_at": now,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO battlegrounds_heroes (
                card_id, dbf, hero_id, name_en, name_ru, health, armor, duos_armor, armor_text,
                artist, race, character_name, as_hero, hero_description, hero_image_url, hero_full_art_url,
                wiki_page_title, wiki_page_url, hero_power_dbf, hero_power_json, buddy_dbf, buddy_json,
                availability_json, hero_skins_json, gallery_json, card_changes_json, external_links_json,
                source_payload_json, source_hash, status, error, fetched_at, changed_at
            ) VALUES (
                %(card_id)s, %(dbf)s, %(hero_id)s, %(name_en)s, %(name_ru)s, %(health)s, %(armor)s, %(duos_armor)s, %(armor_text)s,
                %(artist)s, %(race)s, %(character_name)s, %(as_hero)s, %(hero_description)s, %(hero_image_url)s, %(hero_full_art_url)s,
                %(wiki_page_title)s, %(wiki_page_url)s, %(hero_power_dbf)s, %(hero_power_json)s, %(buddy_dbf)s, %(buddy_json)s,
                %(availability_json)s, %(hero_skins_json)s, %(gallery_json)s, %(card_changes_json)s, %(external_links_json)s,
                %(source_payload_json)s, %(source_hash)s, 'ok', NULL, %(fetched_at)s, %(changed_at)s
            )
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf),
                hero_id = VALUES(hero_id),
                name_en = VALUES(name_en),
                name_ru = VALUES(name_ru),
                health = VALUES(health),
                armor = VALUES(armor),
                duos_armor = VALUES(duos_armor),
                armor_text = VALUES(armor_text),
                artist = VALUES(artist),
                race = VALUES(race),
                character_name = VALUES(character_name),
                as_hero = VALUES(as_hero),
                hero_description = VALUES(hero_description),
                hero_image_url = VALUES(hero_image_url),
                hero_full_art_url = VALUES(hero_full_art_url),
                wiki_page_title = VALUES(wiki_page_title),
                wiki_page_url = VALUES(wiki_page_url),
                hero_power_dbf = VALUES(hero_power_dbf),
                hero_power_json = VALUES(hero_power_json),
                buddy_dbf = VALUES(buddy_dbf),
                buddy_json = VALUES(buddy_json),
                availability_json = VALUES(availability_json),
                hero_skins_json = VALUES(hero_skins_json),
                gallery_json = VALUES(gallery_json),
                card_changes_json = VALUES(card_changes_json),
                external_links_json = VALUES(external_links_json),
                source_payload_json = VALUES(source_payload_json),
                status = 'ok',
                error = NULL,
                fetched_at = VALUES(fetched_at),
                changed_at = IF(
                    battlegrounds_heroes.source_hash <> VALUES(source_hash)
                    OR battlegrounds_heroes.source_hash IS NULL,
                    VALUES(changed_at),
                    changed_at
                ),
                source_hash = VALUES(source_hash)
            """,
            params,
        )
    save_child_tables(conn, payload)
    return "changed" if changed else "unchanged"


def save_error(conn, entry: dict[str, Any], status: str, error: str, dry_run: bool) -> None:
    if dry_run:
        return
    now = utc_now()
    payload = {
        "source": SOURCE,
        "card_id": entry.get("card_id"),
        "dbf": entry.get("dbf_id"),
        "status": status,
        "error": error,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO battlegrounds_heroes
            (card_id, dbf, name_en, source_payload_json, source_hash, status, error, fetched_at, changed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                source_payload_json = VALUES(source_payload_json),
                status = VALUES(status),
                error = VALUES(error),
                fetched_at = VALUES(fetched_at),
                changed_at = IF(
                    battlegrounds_heroes.source_hash <> VALUES(source_hash)
                    OR battlegrounds_heroes.source_hash IS NULL,
                    VALUES(changed_at),
                    changed_at
                ),
                source_hash = VALUES(source_hash)
            """,
            (
                entry.get("card_id"),
                entry.get("dbf_id"),
                entry.get("name") or entry.get("card_id"),
                json_dump(payload),
                stable_hash(payload),
                status,
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
    argp = argparse.ArgumentParser(description="Sync Battlegrounds heroes from hearthstone.wiki.gg and Blizzard RU card data.")
    argp.add_argument("--parser-path", default=str(DEFAULT_PARSER_PATH))
    argp.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    argp.add_argument("--refresh-index", action="store_true")
    argp.add_argument("--refresh-pages", action="store_true")
    argp.add_argument("--card-id")
    argp.add_argument("--dbf", type=int)
    argp.add_argument("--limit", type=int)
    argp.add_argument("--delay-seconds", type=float, default=1.2)
    argp.add_argument("--jitter-seconds", type=float, default=0.4)
    argp.add_argument("--commit-every", type=int, default=5)
    argp.add_argument("--dry-run", action="store_true")
    args = argp.parse_args()

    parser_path = Path(args.parser_path)
    cache_dir = Path(args.cache_dir)
    lookup, parser = import_parser(parser_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    index_path = cache_dir / "wiki-card-index.json"
    index, refreshed = lookup.load_or_refresh_index(index_path, "all", args.refresh_index)
    all_index_entries = index.get("entries", []) if isinstance(index.get("entries"), list) else []
    entries = [item for item in lookup.filter_scope(all_index_entries, "bg-hero") if item.get("card_id") and item.get("dbf_id")]
    if args.card_id:
        entries = [item for item in entries if item.get("card_id") == args.card_id]
    if args.dbf is not None:
        entries = [item for item in entries if int(item.get("dbf_id") or 0) == args.dbf]
    if args.limit is not None:
        entries = entries[: max(0, args.limit)]

    blizzard = BlizzardClient(
        os.getenv("BLIZZARD_CLIENT_ID"),
        os.getenv("BLIZZARD_CLIENT_SECRET"),
        os.getenv("BLIZZARD_REGION", "us"),
        os.getenv("BLIZZARD_LOCALE", "ru_RU"),
    )

    conn = connect_db(load_php_config())
    stats = {"scanned": 0, "changed": 0, "unchanged": 0, "errors": 0, "network": 0, "cache": 0}
    try:
        ensure_schema(conn)
        local_cards_by_dbf = load_local_cards_by_dbf(conn)
        hsj_cards_by_dbf = load_hsj_cards_by_dbf()
        wiki_cards_by_dbf = build_wiki_cards_by_dbf(all_index_entries)
        wiki_golden_images_by_dbf = build_wiki_golden_images_by_dbf(local_cards_by_dbf, hsj_cards_by_dbf)
        print(
            json_dump(
                {
                    "heroes": len(entries),
                    "index_refreshed": refreshed,
                    "blizzard": blizzard.available(),
                    "local_card_fallbacks": len(local_cards_by_dbf),
                    "hearthstonejson_fallbacks": len(hsj_cards_by_dbf),
                    "wiki_card_fallbacks": len(wiki_cards_by_dbf),
                    "wiki_golden_images": len(wiki_golden_images_by_dbf),
                    "dry_run": args.dry_run,
                }
            )
        )
        for idx, entry in enumerate(entries, start=1):
            stats["scanned"] += 1
            try:
                result, source = load_or_fetch_page(cache_dir, parser, parser.safe_filename, entry["page_title"], args.refresh_pages)
                rendered_html = load_or_fetch_rendered(cache_dir, parser, parser.safe_filename, entry["page_title"], args.refresh_pages)
                stats[source] += 1
                payload = build_payload(
                    entry,
                    result,
                    rendered_html,
                    parser,
                    cache_dir,
                    args.refresh_pages,
                    blizzard,
                    local_cards_by_dbf,
                    hsj_cards_by_dbf,
                    wiki_cards_by_dbf,
                    wiki_golden_images_by_dbf,
                )
                outcome = save_payload(conn, payload, args.dry_run)
                stats[outcome] += 1
                print(f"[{idx}/{len(entries)}] {entry['card_id']} {entry.get('name')} {outcome} ({source})")
                if source == "network" and idx < len(entries):
                    sleep_between_requests(args.delay_seconds, args.jitter_seconds)
            except Exception as exc:
                stats["errors"] += 1
                save_error(conn, entry, "error", str(exc), args.dry_run)
                print(f"[{idx}/{len(entries)}] {entry.get('card_id')} error: {exc}", file=sys.stderr)

            if not args.dry_run and idx % max(1, args.commit_every) == 0:
                conn.commit()
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
