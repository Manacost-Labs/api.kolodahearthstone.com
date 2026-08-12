#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
import subprocess
import time
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
DEFAULT_CACHE_DIR = APP_ROOT / "var" / "wiki-hs-cache"
WIKI_BASE = "https://hearthstone.wiki.gg"
USER_AGENT = "kolodahs-hero-skin-sync/1.0"
STATIC_SKIN_UPLOAD_DIR = APP_ROOT / "uploads" / "hero-skins" / "static"
STATIC_SKIN_UPLOAD_URL = "/uploads/hero-skins/static"

CLASS_BY_ID = {
    1: ("deathknight", "Death Knight", "Рыцарь смерти"),
    2: ("druid", "Druid", "Друид"),
    3: ("hunter", "Hunter", "Охотник"),
    4: ("mage", "Mage", "Маг"),
    5: ("paladin", "Paladin", "Паладин"),
    6: ("priest", "Priest", "Жрец"),
    7: ("rogue", "Rogue", "Разбойник"),
    8: ("shaman", "Shaman", "Шаман"),
    9: ("warlock", "Warlock", "Чернокнижник"),
    10: ("warrior", "Warrior", "Воин"),
    14: ("demonhunter", "Demon Hunter", "Охотник на демонов"),
}

CATEGORY_LABELS = {
    "Default portraits": ("default_portraits", "Default portraits", "Стартовые портреты"),
    "1000-win portraits": ("1000_win_portraits", "1000-win portraits", "Портреты за 1000 побед"),
    "1200 gold skins": ("1200_gold_skins", "1200 gold skins", "Скины за 1200 золота"),
    "1800 gold skins": ("1800_gold_skins", "1800 gold skins", "Скины за 1800 золота"),
    "2500 Runestone skins": ("2500_runestone_skins", "2500 Runestone skins", "Скины за 2500 рунных камней"),
    "Free Track skins": ("free_track_skins", "Free Track skins", "Скины бесплатной ленты"),
    "Paid Track skins": ("paid_track_skins", "Paid Track skins", "Скины платной ленты"),
    "Tavern Regular portraits": ("tavern_regular_portraits", "Tavern Regular portraits", "Портреты завсегдатая таверны"),
    "Event Track skins": ("event_track_skins", "Event Track skins", "Скины события"),
    "Bundle skins": ("bundle_skins", "Bundle skins", "Скины наборов"),
    "Bundle purchasable heroes": ("bundle_skins", "Bundle skins", "Скины наборов"),
    "Other skins": ("other_skins", "Other skins", "Другие скины"),
    "Unavailable": ("unavailable", "Unavailable", "Недоступные"),
    "Unknown": ("unknown", "Unknown", "Не классифицированы"),
    "Lite skins": ("lite_skins", "Lite skins", "Lite-скины"),
    "Full skins": ("full_skins", "Full skins", "Full-скины"),
    "Diamond skins": ("diamond_skins", "Diamond skins", "Diamond-скины"),
    "Legendary skins": ("legendary_skins", "Legendary skins", "Легендарные скины"),
    "Mythic skins": ("mythic_skins", "Mythic skins", "Мифические скины"),
    "Other portraits": ("other_portraits", "Other portraits", "Другие портреты"),
    "Rewards track portraits": ("rewards_track_portraits", "Rewards track portraits", "Портреты ленты наград"),
}

RARITY_LABELS = {
    "Default portraits": ("basic", "Basic", "Базовый"),
    "Lite skins": ("lite", "Lite", "Lite"),
    "Full skins": ("full", "Full", "Full"),
    "Diamond skins": ("diamond", "Diamond", "Diamond"),
    "Legendary skins": ("legendary", "Legendary", "Легендарный"),
    "Mythic skins": ("mythic", "Mythic", "Мифический"),
    "Unknown": ("unknown", "Unknown", "Не указана"),
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


def fetch_url(url: str, cache_path: Path | None = None, refresh: bool = False) -> str:
    if cache_path and cache_path.exists() and not refresh:
        return cache_path.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as response:
        body = response.read().decode("utf-8", "replace")
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(body, encoding="utf-8")
    return body


def cargo_query(params: dict[str, str], cache_dir: Path, cache_name: str, refresh: bool = False) -> list[dict[str, Any]]:
    query = {"action": "cargoquery", "format": "json"}
    query.update(params)
    url = WIKI_BASE + "/api.php?" + urllib.parse.urlencode(query)
    body = fetch_url(url, cache_dir / cache_name, refresh)
    payload = json.loads(body)
    if "error" in payload:
        raise RuntimeError(payload["error"].get("info", "Cargo query failed"))
    return [row.get("title", {}) for row in payload.get("cargoquery", [])]


def file_redirect_url(file_name: str | None) -> str | None:
    file_name = (file_name or "").strip()
    if not file_name:
        return None
    if file_name.startswith("http://") or file_name.startswith("https://"):
        return file_name
    return WIKI_BASE + "/wiki/Special:Redirect/file/" + urllib.parse.quote(file_name.replace(" ", "_"), safe="._-(),%")


def direct_file_url(file_name: str | None) -> str | None:
    file_name = (file_name or "").strip()
    if not file_name:
        return None
    if file_name.startswith("http://") or file_name.startswith("https://"):
        return file_name
    normalized = file_name.replace(" ", "_")
    return WIKI_BASE + "/images/" + urllib.parse.quote(normalized, safe="._-(),%'@")


def cache_static_skin_image(source_url: str | None, card_id: str, refresh: bool = False) -> str | None:
    if not source_url or not card_id:
        return source_url
    extension = Path(urllib.parse.urlparse(source_url).path).suffix.lower() or ".png"
    if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
        extension = ".png"
    file_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", card_id).strip("_") + extension
    target = STATIC_SKIN_UPLOAD_DIR / file_name
    if target.exists() and target.stat().st_size > 0 and not refresh:
        return f"{STATIC_SKIN_UPLOAD_URL}/{file_name}"
    try:
        STATIC_SKIN_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(source_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as response:
            content_type = response.headers.get("content-type", "")
            body = response.read()
        if not body or not content_type.startswith("image/"):
            return source_url
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(target)
        target.chmod(0o644)
        STATIC_SKIN_UPLOAD_DIR.chmod(0o755)
        STATIC_SKIN_UPLOAD_DIR.parent.chmod(0o755)
        return f"{STATIC_SKIN_UPLOAD_URL}/{file_name}"
    except Exception:
        return source_url


def page_url(page_title: str) -> str:
    return WIKI_BASE + "/wiki/" + urllib.parse.quote(page_title.replace(" ", "_"), safe="._-()%22")


def normalize_page_from_href(href: str) -> str:
    path = urllib.parse.urlparse(href).path if href.startswith("http") else href
    title = urllib.parse.unquote(path.removeprefix("/wiki/")).replace("_", " ")
    return title


def category_payload(name_en: str) -> dict[str, str]:
    slug, label_en, label_ru = CATEGORY_LABELS.get(
        name_en,
        (
            re.sub(r"[^a-z0-9]+", "_", name_en.lower()).strip("_"),
            name_en,
            name_en,
        ),
    )
    return {"slug": slug, "name_en": label_en, "name_ru": label_ru}


def rarity_payload(name_en: str | None) -> dict[str, str]:
    slug, label_en, label_ru = RARITY_LABELS.get(name_en or "Unknown", RARITY_LABELS["Unknown"])
    return {"slug": slug, "name_en": label_en, "name_ru": label_ru}


def split_tags(value: str | None) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for raw in (value or "").split("&&"):
        tag = raw.strip()
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def parse_visible_categories(cache_dir: Path, refresh: bool = False) -> dict[str, list[str]]:
    body = fetch_url(
        WIKI_BASE + "/wiki/Hero_skin?action=render",
        cache_dir / "hero-skin-index-render.html",
        refresh,
    )
    doc = html.fromstring(body)
    categories: dict[str, list[str]] = {}
    for heading in doc.xpath("//h3|//h4"):
        title = " ".join(heading.text_content().split()).replace("[ edit ]", "").strip()
        if title not in CATEGORY_LABELS:
            continue
        table = heading.getnext()
        while table is not None and table.tag not in ("h2", "h3", "h4"):
            if table.tag == "table":
                for href in table.xpath('.//a[img]/@href'):
                    if "/wiki/File:" in href or href.endswith("/wiki/Hero_skin"):
                        continue
                    page = normalize_page_from_href(href)
                    categories.setdefault(page, [])
                    if title not in categories[page]:
                        categories[page].append(title)
                break
            table = table.getnext()
    return categories


def load_rarity_map(cache_dir: Path, refresh: bool = False) -> dict[str, str]:
    rarity_by_card_id: dict[str, str] = {}
    fields = "Card.id=CardId,Card.name=Name,DerivedCard._pageName=Page"
    for hidden_tag in [tag for tag in RARITY_LABELS if tag != "Unknown"]:
        offset = 0
        while True:
            batch = cargo_query(
                {
                    "tables": "DerivedCard,Card,CustomCard,CardTag",
                    "join_on": "DerivedCard.dbfId=Card.dbfId,DerivedCard.dbfId=CustomCard.dbfId,DerivedCard.dbfId=CardTag.dbfId",
                    "fields": fields,
                    "where": f'DerivedCard.setId=17 AND CardTag.typeId=3 AND CustomCard.hiddenTags HOLDS "{hidden_tag}"',
                    "order_by": "Card.name ASC",
                    "limit": "500",
                    "offset": str(offset),
                },
                cache_dir,
                f"hero-skins-rarity-{re.sub(r'[^a-z0-9]+', '_', hidden_tag.lower()).strip('_')}-{offset}.json",
                refresh,
            )
            for row in batch:
                card_id = str(row.get("CardId") or "")
                if card_id:
                    rarity_by_card_id[card_id] = hidden_tag
            if len(batch) < 500:
                break
            offset += 500
    return rarity_by_card_id


def parse_gallery_from_raw(raw: str) -> list[dict[str, str | None]]:
    match = re.search(r"\|article_gallery=(.*?)(?:\n\|[a-zA-Z0-9_]+=|\n}}\s*)", raw, flags=re.S)
    if not match:
        return []
    gallery: list[dict[str, str | None]] = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line.startswith("File:"):
            continue
        file_part, _, caption = line.partition("{{!}}")
        file_name = file_part.removeprefix("File:").strip()
        if file_name:
            gallery.append(
                {
                    "file_title": "File:" + file_name,
                    "file_url": file_redirect_url(file_name),
                    "caption": re.sub(r"\[\[|\]\]", "", caption).strip() or None,
                }
            )
    return gallery


def section_after(doc: html.HtmlElement, section_id: str) -> list[html.HtmlElement]:
    matches = doc.xpath(f'//span[@id="{section_id}"]/ancestor::*[self::h2 or self::h3][1]')
    if not matches:
        return []
    nodes: list[html.HtmlElement] = []
    node = matches[0].getnext()
    while node is not None and node.tag not in ("h2", "h3"):
        nodes.append(node)
        node = node.getnext()
    return nodes


def parse_sounds(doc: html.HtmlElement) -> list[dict[str, str | None]]:
    sounds: list[dict[str, str | None]] = []
    for node in section_after(doc, "Sounds"):
        for row in node.xpath(".//tr"):
            cells = row.xpath("./th|./td")
            if len(cells) < 2:
                continue
            kind = " ".join(cells[0].text_content().split())
            if kind.lower() in ("type", ""):
                continue
            transcript = " ".join(cells[-1].text_content().split())
            file_url = None
            file_title = None
            for href in row.xpath('.//a/@href'):
                if "/wiki/File:" in href:
                    file_title = normalize_page_from_href(href).removeprefix("File:")
                    file_url = file_redirect_url(file_title)
                    break
                if re.search(r"\.(wav|ogg|mp3)(?:\?|$)", href, re.I):
                    file_url = href if href.startswith("http") else WIKI_BASE + href
                    file_title = urllib.parse.unquote(urllib.parse.urlparse(href).path.rsplit("/", 1)[-1])
                    break
            sounds.append({"type": kind, "transcript": transcript or None, "file_title": file_title, "file_url": file_url})
    return sounds


def parse_render_details(render_html: str) -> dict[str, Any]:
    doc = html.fromstring(render_html)
    animated_file = None
    animated_url = None
    for src in doc.xpath('//img[contains(translate(@src, "GIF", "gif"), ".gif")]/@src'):
        src = src if src.startswith("http") else WIKI_BASE + src
        animated_url = src
        animated_file = urllib.parse.unquote(urllib.parse.urlparse(src).path.rsplit("/", 1)[-1])
        break
    if not animated_url:
        for src in doc.xpath('//video//source/@src|//source/@src'):
            if not re.search(r"\.(webm|mp4)(?:\?|$)", src, re.I):
                continue
            src = src if src.startswith("http") else WIKI_BASE + src
            animated_url = src
            animated_file = urllib.parse.unquote(urllib.parse.urlparse(src).path.rsplit("/", 1)[-1])
            break

    gallery_from_section: list[dict[str, str | None]] = []
    for node in section_after(doc, "Gallery"):
        for img in node.xpath(".//img"):
            src = img.get("src") or ""
            alt = img.get("alt") or ""
            if not src or alt in ("", "Gold", "Runestone"):
                continue
            file_url = src if src.startswith("http") else WIKI_BASE + src
            gallery_from_section.append(
                {
                    "file_title": alt if alt.startswith("File:") else None,
                    "file_url": file_url,
                    "caption": alt or None,
                }
            )

    return {
        "animated_file": animated_file,
        "animated_url": animated_url,
        "sounds": parse_sounds(doc),
        "gallery_from_section": gallery_from_section,
    }


def load_skin_rows(cache_dir: Path, refresh: bool = False) -> list[dict[str, Any]]:
    fields = ",".join(
        [
            "DerivedCard._pageName=Page",
            "DerivedCard.dbfId=Dbf",
            "Card.id=CardId",
            "Card.name=Name",
            "DerivedCard.img=StaticImage",
            "DerivedCard.imgMain=MainImage",
            "DerivedCard.imgMainPremium=MainPremiumImage",
            "DerivedCard.imgBg=BackgroundImage",
            "DerivedCard.imgBgPremium=BackgroundPremiumImage",
            "DerivedCard.imgFullArt=FullArt",
            "DerivedCard.artist=Artist",
            "DerivedCard.classIds=ClassIds",
            "CustomCard.hiddenTags=Tags",
            "CustomCard.voiceActor=Actor",
            "CustomCard.characs=Character",
            "CardTag.health=Health",
            "CardTag.classId=ClassId",
        ]
    )
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = cargo_query(
            {
                "tables": "DerivedCard,Card,CustomCard,CardTag",
                "join_on": "DerivedCard.dbfId=Card.dbfId,DerivedCard.dbfId=CustomCard.dbfId,DerivedCard.dbfId=CardTag.dbfId",
                "fields": fields,
                "where": "DerivedCard.setId=17 AND CardTag.typeId=3",
                "order_by": "DerivedCard._pageName ASC",
                "limit": "500",
                "offset": str(offset),
            },
            cache_dir,
            f"hero-skins-cargo-{offset}.json",
            refresh,
        )
        rows.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        card_id = str(row.get("CardId") or "")
        if card_id:
            unique[card_id] = row
    return list(unique.values())


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS hero_skins (
                card_id VARCHAR(96) NOT NULL,
                dbf INT UNSIGNED DEFAULT NULL,
                release_date DATE DEFAULT NULL,
                page_title VARCHAR(255) NOT NULL,
                page_url VARCHAR(512) NOT NULL,
                name_en VARCHAR(255) NOT NULL,
                class_id SMALLINT UNSIGNED DEFAULT NULL,
                class_slug VARCHAR(64) DEFAULT NULL,
                class_name_en VARCHAR(80) DEFAULT NULL,
                class_name_ru VARCHAR(80) DEFAULT NULL,
                health SMALLINT DEFAULT NULL,
                character_name VARCHAR(255) DEFAULT NULL,
                actor VARCHAR(255) DEFAULT NULL,
                artist VARCHAR(255) DEFAULT NULL,
                rarity_slug VARCHAR(48) DEFAULT NULL,
                rarity_name_en VARCHAR(80) DEFAULT NULL,
                rarity_name_ru VARCHAR(80) DEFAULT NULL,
                primary_category_slug VARCHAR(96) DEFAULT NULL,
                primary_category_en VARCHAR(160) DEFAULT NULL,
                primary_category_ru VARCHAR(160) DEFAULT NULL,
                categories_json JSON DEFAULT NULL,
                tags_json JSON DEFAULT NULL,
                static_image_file VARCHAR(255) DEFAULT NULL,
                static_image_url VARCHAR(512) DEFAULT NULL,
                animated_image_file VARCHAR(255) DEFAULT NULL,
                animated_image_url VARCHAR(512) DEFAULT NULL,
                animated_asset_json JSON DEFAULT NULL,
                full_art_file VARCHAR(255) DEFAULT NULL,
                full_art_url VARCHAR(512) DEFAULT NULL,
                gallery_json JSON DEFAULT NULL,
                sounds_json JSON DEFAULT NULL,
                source_payload_json JSON DEFAULT NULL,
                source_hash CHAR(64) DEFAULT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'ok',
                error TEXT DEFAULT NULL,
                fetched_at TIMESTAMP NULL DEFAULT NULL,
                changed_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (card_id),
                UNIQUE KEY uniq_hero_skins_dbf (dbf),
                KEY idx_hero_skins_class (class_slug),
                KEY idx_hero_skins_rarity (rarity_slug),
                KEY idx_hero_skins_category (primary_category_slug),
                KEY idx_hero_skins_status (status),
                KEY idx_hero_skins_changed_at (changed_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        for ddl in [
            "ALTER TABLE hero_skins ADD COLUMN release_date DATE DEFAULT NULL AFTER dbf",
            "ALTER TABLE hero_skins ADD COLUMN animated_asset_json JSON DEFAULT NULL AFTER animated_image_url",
            "ALTER TABLE hero_skins ADD COLUMN rarity_slug VARCHAR(48) DEFAULT NULL AFTER artist",
            "ALTER TABLE hero_skins ADD COLUMN rarity_name_en VARCHAR(80) DEFAULT NULL AFTER rarity_slug",
            "ALTER TABLE hero_skins ADD COLUMN rarity_name_ru VARCHAR(80) DEFAULT NULL AFTER rarity_name_en",
            "ALTER TABLE hero_skins ADD INDEX idx_hero_skins_rarity (rarity_slug)",
        ]:
            try:
                cur.execute(ddl)
            except pymysql.err.OperationalError as exc:
                if exc.args[0] not in (1060, 1061):
                    raise
    conn.commit()


def build_record(
    row: dict[str, Any],
    visible_categories: dict[str, list[str]],
    rarity_map: dict[str, str],
    release_dates: dict[int, str],
    cache_dir: Path,
    args,
) -> dict[str, Any]:
    page_title_value = str(row.get("Page") or row.get("Name") or "").strip()
    card_id = str(row.get("CardId") or "").strip()
    class_id = int(row["ClassId"]) if str(row.get("ClassId") or "").isdigit() else None
    class_slug, class_en, class_ru = CLASS_BY_ID.get(class_id or -1, (None, None, None))
    tags = split_tags(row.get("Tags"))
    categories_en: list[str] = []
    for tag in tags + visible_categories.get(page_title_value, []):
        if tag in CATEGORY_LABELS and tag not in categories_en:
            categories_en.append(tag)
    if not categories_en:
        categories_en.append("Unknown")
    categories = [category_payload(name) for name in categories_en]
    primary = categories[0]
    rarity = rarity_payload(rarity_map.get(card_id) or next((tag for tag in tags if tag in RARITY_LABELS), None))
    static_file = (row.get("StaticImage") or "").strip() or None
    static_url = direct_file_url(static_file) or file_redirect_url(static_file)
    static_url = cache_static_skin_image(static_url, card_id, args.refresh_pages)
    full_art_file = (row.get("FullArt") or "").strip() or None
    animated_assets = [
        {"kind": "main", "asset": (row.get("MainImage") or "").strip() or None},
        {"kind": "main_premium", "asset": (row.get("MainPremiumImage") or "").strip() or None},
        {"kind": "background", "asset": (row.get("BackgroundImage") or "").strip() or None},
        {"kind": "background_premium", "asset": (row.get("BackgroundPremiumImage") or "").strip() or None},
    ]
    animated_assets = [item for item in animated_assets if item["asset"]]
    details: dict[str, Any] = {"animated_file": None, "animated_url": None, "sounds": [], "gallery_from_section": []}
    raw_gallery: list[dict[str, str | None]] = []
    error = None
    status = "ok"
    if not args.skip_details:
        try:
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", page_title_value)[:180]
            render = fetch_url(page_url(page_title_value) + "?action=render", cache_dir / "hero-skins-pages" / f"{safe}.render.html", args.refresh_pages)
            raw = fetch_url(page_url(page_title_value) + "?action=raw", cache_dir / "hero-skins-pages" / f"{safe}.raw.txt", args.refresh_pages)
            details = parse_render_details(render)
            raw_gallery = parse_gallery_from_raw(raw)
            time.sleep(args.delay)
        except Exception as exc:  # keep the skin row even when one page parse fails
            status = "partial"
            error = str(exc)

    gallery = raw_gallery or details.get("gallery_from_section") or []
    dbf = int(row["Dbf"]) if str(row.get("Dbf") or "").isdigit() else None
    release_date = release_dates.get(dbf) if dbf is not None else None
    payload = {
        "cargo": row,
        "release_date": release_date,
        "categories": categories,
        "rarity": rarity,
        "gallery": gallery,
        "sounds": details.get("sounds") or [],
        "animated_assets": animated_assets,
    }
    return {
        "card_id": card_id,
        "dbf": dbf,
        "release_date": release_date,
        "page_title": page_title_value,
        "page_url": page_url(page_title_value),
        "name_en": str(row.get("Name") or page_title_value),
        "class_id": class_id,
        "class_slug": class_slug,
        "class_name_en": class_en,
        "class_name_ru": class_ru,
        "health": int(row["Health"]) if str(row.get("Health") or "").isdigit() else None,
        "character_name": (row.get("Character") or "").strip() or None,
        "actor": (row.get("Actor") or "").strip() or None,
        "artist": (row.get("Artist") or "").strip() or None,
        "rarity_slug": rarity["slug"],
        "rarity_name_en": rarity["name_en"],
        "rarity_name_ru": rarity["name_ru"],
        "primary_category_slug": primary["slug"],
        "primary_category_en": primary["name_en"],
        "primary_category_ru": primary["name_ru"],
        "categories_json": json_dump(categories),
        "tags_json": json_dump(tags),
        "static_image_file": static_file,
        "static_image_url": static_url,
        "animated_image_file": details.get("animated_file"),
        "animated_image_url": details.get("animated_url"),
        "animated_asset_json": json_dump(animated_assets),
        "full_art_file": full_art_file,
        "full_art_url": direct_file_url(full_art_file) or file_redirect_url(full_art_file),
        "gallery_json": json_dump(gallery),
        "sounds_json": json_dump(details.get("sounds") or []),
        "source_payload_json": json_dump(payload),
        "source_hash": stable_hash(payload),
        "status": status,
        "error": error,
    }


def upsert_records(conn, records: list[dict[str, Any]]) -> dict[str, int]:
    now = utc_now()
    stats = {"scanned": len(records), "inserted": 0, "updated": 0, "changed": 0}
    columns = [
        "card_id",
        "dbf",
        "release_date",
        "page_title",
        "page_url",
        "name_en",
        "class_id",
        "class_slug",
        "class_name_en",
        "class_name_ru",
        "health",
        "character_name",
        "actor",
        "artist",
        "rarity_slug",
        "rarity_name_en",
        "rarity_name_ru",
        "primary_category_slug",
        "primary_category_en",
        "primary_category_ru",
        "categories_json",
        "tags_json",
        "static_image_file",
        "static_image_url",
        "animated_image_file",
        "animated_image_url",
        "animated_asset_json",
        "full_art_file",
        "full_art_url",
        "gallery_json",
        "sounds_json",
        "source_payload_json",
        "source_hash",
        "status",
        "error",
        "fetched_at",
        "changed_at",
    ]
    update_cols = [col for col in columns if col not in ("card_id", "changed_at")]
    sql = (
        "INSERT INTO hero_skins (" + ",".join(columns) + ") VALUES ("
        + ",".join(["%s"] * len(columns))
        + ") ON DUPLICATE KEY UPDATE "
        + ",".join([f"{col}=VALUES({col})" for col in update_cols])
        + ", changed_at=IF(source_hash <> VALUES(source_hash), VALUES(changed_at), changed_at)"
    )
    with conn.cursor() as cur:
        for record in records:
            cur.execute("SELECT source_hash FROM hero_skins WHERE card_id=%s", (record["card_id"],))
            existing = cur.fetchone()
            changed_at = now if not existing or existing.get("source_hash") != record["source_hash"] else None
            values = [record.get(col) for col in columns]
            values[columns.index("fetched_at")] = now
            values[columns.index("changed_at")] = changed_at
            cur.execute(sql, values)
            if not existing:
                stats["inserted"] += 1
                stats["changed"] += 1
            else:
                stats["updated"] += 1
                if changed_at:
                    stats["changed"] += 1
    conn.commit()
    return stats


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync Hearthstone hero skins from hearthstone.wiki.gg")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--refresh", action="store_true", help="Refresh both index data and per-skin pages (legacy alias).")
    parser.add_argument("--refresh-index", action="store_true", help="Refresh discovery/category/rarity Cargo data.")
    parser.add_argument("--refresh-pages", action="store_true", help="Refresh per-skin pages and cached static images.")
    parser.add_argument("--skip-details", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.08)
    parser.add_argument("--workers", type=int, default=8)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    args.refresh_index = args.refresh or args.refresh_index
    args.refresh_pages = args.refresh or args.refresh_pages

    config = load_php_config()
    conn = connect_db(config)
    ensure_schema(conn)
    visible_categories = parse_visible_categories(args.cache_dir, args.refresh_index)
    rarity_map = load_rarity_map(args.cache_dir, args.refresh_index)
    rows = load_skin_rows(args.cache_dir, args.refresh_index)
    release_dates = fetch_release_dates(
        (row.get("Dbf") for row in rows),
        user_agent=USER_AGENT,
    )
    if args.limit > 0:
        rows = rows[: args.limit]
    records: list[dict[str, Any]] = []
    workers = max(1, min(args.workers, 16))
    if workers == 1 or args.skip_details:
        records = [
            build_record(row, visible_categories, rarity_map, release_dates, args.cache_dir, args)
            for row in rows
        ]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    build_record,
                    row,
                    visible_categories,
                    rarity_map,
                    release_dates,
                    args.cache_dir,
                    args,
                )
                for row in rows
            ]
            for index, future in enumerate(as_completed(futures), 1):
                records.append(future.result())
                if index % 50 == 0:
                    print(json_dump({"progress": index, "total": len(futures)}), flush=True)
    stats = upsert_records(conn, records)
    conn.close()
    print(json_dump({"ok": True, **stats}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
