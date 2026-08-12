#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
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
HSJ_RU_URL = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.json"
HSJ_EN_URL = "https://api.hearthstonejson.com/v1/latest/enUS/cards.json"
HSJ_RENDER_RU = "https://art.hearthstonejson.com/v1/render/latest/ruRU/512x/"
HSJ_RENDER_EN = "https://art.hearthstonejson.com/v1/render/latest/enUS/512x/"
HSJ_ORIG_BASE = "https://art.hearthstonejson.com/v1/orig/"
WIKI_PAGE = "The Coin"
WIKI_PAGE_URL = "https://hearthstone.wiki.gg/wiki/The_Coin"
SOURCE_WIKI = "hearthstone.wiki.gg"
USER_AGENT = "db.kolodahs.ru-coin-sync/1.0"
URL_OK_CACHE: dict[str, bool] = {}


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
            CREATE TABLE IF NOT EXISTS hearthstone_coins (
                card_id VARCHAR(64) NOT NULL,
                dbf INT UNSIGNED DEFAULT NULL,
                release_date DATE DEFAULT NULL,
                coin_name_en VARCHAR(180) NOT NULL,
                card_name_ru VARCHAR(180) DEFAULT NULL,
                card_name_en VARCHAR(180) DEFAULT NULL,
                text_ru TEXT DEFAULT NULL,
                text_en TEXT DEFAULT NULL,
                flavor_text TEXT DEFAULT NULL,
                artist VARCHAR(255) DEFAULT NULL,
                image_url VARCHAR(512) DEFAULT NULL,
                image_gold_url VARCHAR(512) DEFAULT NULL,
                crop_image_url VARCHAR(512) DEFAULT NULL,
                wiki_image_url VARCHAR(512) DEFAULT NULL,
                wiki_image_file VARCHAR(255) DEFAULT NULL,
                wiki_page_title VARCHAR(255) DEFAULT NULL,
                wiki_page_url VARCHAR(512) DEFAULT NULL,
                cosmetic_sort_order INT UNSIGNED DEFAULT NULL,
                generated_by_card_ids_json JSON DEFAULT NULL,
                related_card_ids_json JSON DEFAULT NULL,
                generated_by_cards_json JSON DEFAULT NULL,
                related_cards_json JSON DEFAULT NULL,
                source VARCHAR(64) NOT NULL,
                source_payload_json JSON DEFAULT NULL,
                source_hash CHAR(64) DEFAULT NULL,
                fetched_at TIMESTAMP NULL DEFAULT NULL,
                changed_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (card_id),
                KEY idx_coin_dbf (dbf),
                KEY idx_coin_sort (cosmetic_sort_order),
                KEY idx_coin_changed_at (changed_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        try:
            cur.execute(
                "ALTER TABLE hearthstone_coins "
                "ADD COLUMN release_date DATE DEFAULT NULL AFTER dbf"
            )
        except pymysql.err.OperationalError as exc:
            if exc.args[0] != 1060:
                raise
    conn.commit()


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=80) as resp:
        return json.load(resp)


def mediawiki_parse_html(page_title: str) -> str:
    url = "https://hearthstone.wiki.gg/api.php?" + urllib.parse.urlencode(
        {"action": "parse", "page": page_title, "prop": "text", "format": "json"}
    )
    return http_json(url)["parse"]["text"]["*"]


def mediawiki_parse_source(page_title: str) -> dict[str, Any]:
    url = "https://hearthstone.wiki.gg/api.php?" + urllib.parse.urlencode(
        {"action": "parse", "page": page_title, "prop": "wikitext|images", "format": "json"}
    )
    parsed = http_json(url).get("parse")
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Invalid MediaWiki parse response for {page_title}")
    return parsed


def image_from_src(src: str | None) -> str | None:
    if not src:
        return None
    src = str(src)
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://hearthstone.wiki.gg" + src
    return src


def wiki_page_url(title: str | None) -> str | None:
    if not title:
        return None
    return "https://hearthstone.wiki.gg/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe="/()_',.!:")


def file_url(file_name: str | None) -> str | None:
    if not file_name:
        return None
    return "https://hearthstone.wiki.gg/wiki/Special:Redirect/file/" + urllib.parse.quote(str(file_name).replace(" ", "_"), safe="._-()'!,@")


def url_ok(url: str | None) -> bool:
    if not url:
        return False
    url = str(url)
    if url in URL_OK_CACHE:
        return URL_OK_CACHE[url]
    headers = {"User-Agent": USER_AGENT}
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


def render_url(card_id: str | None, locale: str = "ruRU") -> str | None:
    if not card_id:
        return None
    base = HSJ_RENDER_RU if locale == "ruRU" else HSJ_RENDER_EN
    return base + urllib.parse.quote(card_id, safe="") + ".png"


def crop_url(card_id: str | None) -> str | None:
    if not card_id:
        return None
    return HSJ_ORIG_BASE + urllib.parse.quote(card_id, safe="") + ".png"


def load_hsj_cards(url: str) -> dict[int, dict[str, Any]]:
    cards = http_json(url)
    by_dbf: dict[int, dict[str, Any]] = {}
    for card in cards if isinstance(cards, list) else []:
        if isinstance(card, dict) and card.get("dbfId") is not None:
            by_dbf[int(card["dbfId"])] = card
    return by_dbf


def load_wiki_index(cache_dir: Path) -> dict[str, dict[str, Any]]:
    path = cache_dir / "wiki-card-index-card.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError(f"Invalid wiki index: {path}")
    return {str(entry.get("page_title")): entry for entry in entries if isinstance(entry, dict) and entry.get("page_title")}


def section_node(page_html: str, section_id: str):
    doc = html.fromstring(page_html)
    nodes = doc.xpath(f'//*[@id="{section_id}"]')
    if not nodes:
        raise RuntimeError(f"Missing section #{section_id} on {WIKI_PAGE}")
    heading = nodes[0].getparent()
    section_nodes = []
    node = heading.getnext()
    while node is not None and node.tag != "h2":
        section_nodes.append(node)
        node = node.getnext()
    return html.fromstring("<div>" + "".join(html.tostring(item, encoding="unicode") for item in section_nodes) + "</div>")


def list_card_items(page_html: str, section_id: str) -> list[dict[str, str | None]]:
    section = section_node(page_html, section_id)
    items: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for card in section.xpath('.//div[contains(concat(" ", normalize-space(@class), " "), " list-cards ")]//div[contains(concat(" ", normalize-space(@class), " "), " card-div ")]'):
        anchor = card.xpath('.//a[@title and starts-with(@href, "/wiki/")]')
        if not anchor:
            continue
        title = str(anchor[0].get("title") or "")
        if title == "" or title in seen:
            continue
        seen.add(title)
        image = card.xpath(".//img")
        items.append(
            {
                "page_title": title,
                "page_url": wiki_page_url(title),
                "wiki_image_url": image_from_src(image[0].get("src")) if image else None,
                "wiki_image_file": str(image[0].get("alt") or "") if image else None,
            }
        )
    return items


def coin_card_id_from_file(file_name: str | None) -> str | None:
    stem = Path(str(file_name or "").replace(" ", "_")).stem
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_]{1,63}", stem):
        return None
    if stem.lower().endswith(("_premium1", "_premium2", "_art")):
        return None
    return stem


def coin_entry_from_parse(item: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("page_title") or "").strip()
    wikitext = str((parsed.get("wikitext") or {}).get("*") or "")
    dbf_match = re.search(r"\|\s*dbfId\s*=\s*([0-9]+)", wikitext, flags=re.I)
    card_id = coin_card_id_from_file(item.get("wiki_image_file"))
    if not card_id:
        for image in parsed.get("images") or []:
            card_id = coin_card_id_from_file(str(image))
            if card_id:
                break
    if not title or not card_id or not dbf_match:
        raise RuntimeError(f"Cannot derive cosmetic coin identity from Wiki page {title or '<empty>'}")
    return {
        "scope": "card",
        "source_url": wiki_page_url(title),
        "page_title": title,
        "page_url": wiki_page_url(title),
        "name": title,
        "card_id": card_id,
        "dbf_id": int(dbf_match.group(1)),
        "details": {},
    }


def resolve_coin_entry(item: dict[str, Any], wiki_by_title: dict[str, dict[str, Any]]) -> dict[str, Any]:
    title = str(item.get("page_title") or "").strip()
    cached = wiki_by_title.get(title)
    if cached:
        return cached
    return coin_entry_from_parse(item, mediawiki_parse_source(title))


def relation_cards(titles: list[str], wiki_by_title: dict[str, dict[str, Any]], hsj_ru: dict[int, dict[str, Any]], hsj_en: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for title in titles:
        entry = wiki_by_title.get(title)
        if not entry:
            raise RuntimeError(f"Missing wiki index entry for {title}")
        dbf = int(entry["dbf_id"]) if entry.get("dbf_id") is not None else None
        ru = hsj_ru.get(dbf) if dbf is not None else {}
        en = hsj_en.get(dbf) if dbf is not None else {}
        result.append(
            {
                "page_title": title,
                "card_id": entry.get("card_id"),
                "dbf": dbf,
                "name_ru": ru.get("name"),
                "name_en": en.get("name") or entry.get("name"),
                "page_url": entry.get("page_url") or wiki_page_url(title),
            }
        )
    return result


def build_payloads(cache_dir: Path) -> list[dict[str, Any]]:
    page_html = mediawiki_parse_html(WIKI_PAGE)
    wiki_by_title = load_wiki_index(cache_dir)
    hsj_ru = load_hsj_cards(HSJ_RU_URL)
    hsj_en = load_hsj_cards(HSJ_EN_URL)

    generated_titles = [str(item["page_title"]) for item in list_card_items(page_html, "Generated_by")]
    related_titles = [str(item["page_title"]) for item in list_card_items(page_html, "Related_with")]
    generated_cards = relation_cards(generated_titles, wiki_by_title, hsj_ru, hsj_en)
    related_cards = relation_cards(related_titles, wiki_by_title, hsj_ru, hsj_en)
    generated_ids = [str(card["card_id"]) for card in generated_cards if card.get("card_id")]
    related_ids = [str(card["card_id"]) for card in related_cards if card.get("card_id")]

    cosmetic_items = list_card_items(page_html, "Cosmetic_Coins")
    cosmetic_entries = {
        str(item.get("page_title") or ""): resolve_coin_entry(item, wiki_by_title)
        for item in cosmetic_items
    }
    release_dates = fetch_release_dates(
        (
            cosmetic_entries.get(str(item.get("page_title") or ""), {}).get("dbf_id")
            for item in cosmetic_items
        ),
        user_agent=USER_AGENT,
    )
    payloads: list[dict[str, Any]] = []
    for idx, item in enumerate(cosmetic_items, start=1):
        title = str(item["page_title"] or "")
        entry = cosmetic_entries[title]
        card_id = str(entry.get("card_id") or "")
        dbf = int(entry["dbf_id"]) if entry.get("dbf_id") is not None else None
        ru_card = hsj_ru.get(dbf) if dbf is not None else {}
        en_card = hsj_en.get(dbf) if dbf is not None else {}
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        wiki_image_url = str(item.get("wiki_image_url") or "")
        image_file = str(item.get("wiki_image_file") or "")
        payloads.append(
            {
                "card_id": card_id,
                "dbf": dbf,
                "release_date": release_dates.get(dbf) if dbf is not None else None,
                "coin_name_en": title,
                "card_name_ru": ru_card.get("name"),
                "card_name_en": en_card.get("name") or entry.get("name"),
                "text_ru": ru_card.get("text"),
                "text_en": en_card.get("text"),
                "flavor_text": details.get("flavor_text"),
                "artist": ru_card.get("artist") or details.get("artist"),
                "image_url": first_working_url([render_url(card_id, "ruRU"), render_url(card_id, "enUS"), wiki_image_url, file_url(image_file)]),
                "image_gold_url": first_working_url([render_url(card_id + "_golden", "ruRU"), render_url(card_id + "_golden", "enUS")]),
                "crop_image_url": first_working_url([crop_url(card_id)]),
                "wiki_image_url": wiki_image_url or file_url(image_file),
                "wiki_image_file": image_file or None,
                "wiki_page_title": title,
                "wiki_page_url": entry.get("page_url") or wiki_page_url(title),
                "cosmetic_sort_order": idx,
                "generated_by_card_ids": generated_ids,
                "related_card_ids": related_ids,
                "generated_by_cards": generated_cards,
                "related_cards": related_cards,
                "source": SOURCE_WIKI,
                "source_payload": {
                    "source_page": WIKI_PAGE_URL,
                    "wiki_entry": entry,
                    "wiki_item": item,
                    "hsj_ru": ru_card,
                },
            }
        )
    return payloads


def current_hash(conn, card_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT source_hash FROM hearthstone_coins WHERE card_id = %s", (card_id,))
        row = cur.fetchone()
    return row["source_hash"] if row else None


def save_payload(conn, payload: dict[str, Any], dry_run: bool = False) -> str:
    source_payload = dict(payload)
    new_hash = stable_hash(source_payload)
    old_hash = current_hash(conn, payload["card_id"])
    changed = old_hash != new_hash
    if dry_run:
        return "changed" if changed else "unchanged"

    now = utc_now()
    params = {
        **{key: value for key, value in payload.items() if key not in {"source_payload", "generated_by_card_ids", "related_card_ids", "generated_by_cards", "related_cards"}},
        "generated_by_card_ids_json": json_dump(payload["generated_by_card_ids"]),
        "related_card_ids_json": json_dump(payload["related_card_ids"]),
        "generated_by_cards_json": json_dump(payload["generated_by_cards"]),
        "related_cards_json": json_dump(payload["related_cards"]),
        "source_payload_json": json_dump(source_payload),
        "source_hash": new_hash,
        "fetched_at": now,
        "changed_at": now,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO hearthstone_coins (
                card_id, dbf, release_date, coin_name_en, card_name_ru, card_name_en, text_ru,
                text_en, flavor_text, artist, image_url, image_gold_url,
                crop_image_url, wiki_image_url, wiki_image_file, wiki_page_title,
                wiki_page_url, cosmetic_sort_order, generated_by_card_ids_json,
                related_card_ids_json, generated_by_cards_json, related_cards_json,
                source, source_payload_json, source_hash, fetched_at, changed_at
            ) VALUES (
                %(card_id)s, %(dbf)s, %(release_date)s, %(coin_name_en)s, %(card_name_ru)s, %(card_name_en)s, %(text_ru)s,
                %(text_en)s, %(flavor_text)s, %(artist)s, %(image_url)s, %(image_gold_url)s,
                %(crop_image_url)s, %(wiki_image_url)s, %(wiki_image_file)s, %(wiki_page_title)s,
                %(wiki_page_url)s, %(cosmetic_sort_order)s, %(generated_by_card_ids_json)s,
                %(related_card_ids_json)s, %(generated_by_cards_json)s, %(related_cards_json)s,
                %(source)s, %(source_payload_json)s, %(source_hash)s, %(fetched_at)s, %(changed_at)s
            )
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf),
                release_date = VALUES(release_date),
                coin_name_en = VALUES(coin_name_en),
                card_name_ru = VALUES(card_name_ru),
                card_name_en = VALUES(card_name_en),
                text_ru = VALUES(text_ru),
                text_en = VALUES(text_en),
                flavor_text = VALUES(flavor_text),
                artist = VALUES(artist),
                image_url = VALUES(image_url),
                image_gold_url = VALUES(image_gold_url),
                crop_image_url = VALUES(crop_image_url),
                wiki_image_url = VALUES(wiki_image_url),
                wiki_image_file = VALUES(wiki_image_file),
                wiki_page_title = VALUES(wiki_page_title),
                wiki_page_url = VALUES(wiki_page_url),
                cosmetic_sort_order = VALUES(cosmetic_sort_order),
                generated_by_card_ids_json = VALUES(generated_by_card_ids_json),
                related_card_ids_json = VALUES(related_card_ids_json),
                generated_by_cards_json = VALUES(generated_by_cards_json),
                related_cards_json = VALUES(related_cards_json),
                source = VALUES(source),
                source_payload_json = VALUES(source_payload_json),
                fetched_at = VALUES(fetched_at),
                changed_at = IF(
                    hearthstone_coins.source_hash <> VALUES(source_hash)
                    OR hearthstone_coins.source_hash IS NULL,
                    VALUES(changed_at),
                    changed_at
                ),
                source_hash = VALUES(source_hash)
            """,
            params,
        )
    return "changed" if changed else "unchanged"


def main() -> int:
    argp = argparse.ArgumentParser(description="Sync Hearthstone cosmetic Coins from The Coin wiki page.")
    argp.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    argp.add_argument("--dry-run", action="store_true")
    args = argp.parse_args()

    payloads = build_payloads(Path(args.cache_dir))
    conn = connect_db(load_php_config())
    stats = {"scanned": len(payloads), "changed": 0, "unchanged": 0}
    try:
        ensure_schema(conn)
        for payload in payloads:
            outcome = save_payload(conn, payload, args.dry_run)
            stats[outcome] += 1
        if not args.dry_run:
            conn.commit()
        print(json_dump({"ok": True, **stats}))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
