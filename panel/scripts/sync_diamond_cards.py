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

import pymysql
from pymysql.cursors import DictCursor


APP_ROOT = Path(__file__).resolve().parents[1]
SOURCE = "hearthstone.wiki.gg"
USER_AGENT = "db.kolodahs.ru-diamond-sync/1.0"
HSJ_RU_URL = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.json"
HSJ_EN_URL = "https://api.hearthstonejson.com/v1/latest/enUS/cards.json"


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


def ensure_schema() -> None:
    subprocess.check_call(["php", str(APP_ROOT / "scripts" / "ensure_constructed_schema.php")])


def http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def wiki_api(params: dict[str, Any]) -> dict[str, Any]:
    return http_json("https://hearthstone.wiki.gg/api.php?" + urllib.parse.urlencode(params))


def cargo_query(where: str, section_slug: str, section_name_ru: str) -> list[dict[str, Any]]:
    fields = ",".join(
        [
            "Card.dbfId=base_dbf",
            "Card.id=base_card_id",
            "Card.name=name_en",
            "CardTag.isCollectible=is_collectible",
            "CardSetTiming.cardSetId=card_set_id",
            "CustomCard._pageName=page_title",
        ]
    )
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        data = wiki_api(
            {
                "action": "cargoquery",
                "format": "json",
                "tables": "CardSetTiming,Card,CardTag,CustomCard",
                "join_on": "CardSetTiming.dbfId=Card.dbfId,Card.dbfId=CardTag.dbfId,Card.dbfId=CustomCard.dbfId",
                "where": where,
                "fields": fields,
                "group_by": "Card.dbfId",
                "order_by": "CardSetTiming.cardSetId DESC, CardTag.cost, Card.name",
                "limit": "500",
                "offset": str(offset),
            }
        )
        batch = [item.get("title", {}) for item in data.get("cargoquery", [])]
        for row in batch:
            row["section_slug"] = section_slug
            row["section_name_ru"] = section_name_ru
            rows.append(row)
        if len(batch) < 500:
            break
        offset += len(batch)
    return rows


def fetch_diamond_rows() -> list[dict[str, Any]]:
    base = (
        'CardTag.stringTags LIKE "%HAS_DIAMOND_QUALITY=1%" '
        "AND NOT CardSetTiming.cardSetId=1586 "
        "AND CustomCard.originalCardDbfId IS NULL "
        "AND NOT CardSetTiming.cardSetId=1810"
    )
    collectible = base + ' AND CardTag.isCollectible="1"'
    uncollectible = base + ' AND (CardTag.isCollectible="0" OR CardTag.isCollectible IS NULL)'
    rows = cargo_query(collectible, "collectible", "Коллекционные")
    rows.extend(cargo_query(uncollectible, "uncollectible", "Неколлекционные"))
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        card_id = str(row.get("base_card_id") or "").strip()
        if card_id:
            deduped[card_id] = row
    return list(deduped.values())


def load_hsj_by_dbf(url: str) -> dict[int, dict[str, Any]]:
    cards = http_json(url)
    result: dict[int, dict[str, Any]] = {}
    if isinstance(cards, list):
        for card in cards:
            if isinstance(card, dict) and card.get("dbfId") is not None:
                result[int(card["dbfId"])] = card
    return result


def normalize_name(value: str) -> str:
    value = re.sub(r"\.(gif|webm|mp4|png|jpg|jpeg)$", "", value, flags=re.I)
    value = value.replace("_", " ")
    value = re.sub(r"\bdiamond\b|\banimated\b", " ", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def load_diamond_animated_index() -> dict[str, dict[str, Any]]:
    titles: list[str] = []
    cont: dict[str, str] = {}
    while True:
        data = wiki_api(
            {
                "action": "query",
                "format": "json",
                "list": "categorymembers",
                "cmtitle": "Category:Animated card images",
                "cmnamespace": "6",
                "cmlimit": "500",
                **cont,
            }
        )
        titles.extend(str(item["title"])[5:] for item in data.get("query", {}).get("categorymembers", []) if str(item.get("title", "")).startswith("File:"))
        if "continue" not in data:
            break
        cont = {k: v for k, v in data["continue"].items() if k != "continue"}

    diamond_titles = [title for title in titles if "diamond" in title.lower()]
    if not diamond_titles:
        return {}

    pages = "|".join("File:" + title for title in diamond_titles)
    data = wiki_api(
        {
            "action": "query",
            "format": "json",
            "titles": pages,
            "prop": "imageinfo",
            "iiprop": "url|mime|size",
        }
    )
    index: dict[str, dict[str, Any]] = {}
    for page in data.get("query", {}).get("pages", {}).values():
        title = str(page.get("title", ""))
        file_name = title[5:] if title.startswith("File:") else title
        info = (page.get("imageinfo") or [{}])[0]
        url = str(info.get("url") or "").strip()
        if not file_name or not url:
            continue
        index[normalize_name(file_name)] = {
            "file_title": file_name,
            "file_url": url,
            "mime": info.get("mime"),
            "width": info.get("width"),
            "height": info.get("height"),
            "source": SOURCE,
        }
    return index


def existing_constructed_by_card_id(conn) -> dict[str, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.*, GROUP_CONCAT(DISTINCT f.format_slug ORDER BY f.format_slug) AS formats
            FROM constructed_cards c
            LEFT JOIN constructed_format_cards f ON f.card_id = c.card_id AND f.in_format = 1
            GROUP BY c.card_id
            """
        )
        return {str(row["card_id"]): row for row in cur.fetchall()}


def page_url(title: str | None) -> str | None:
    if not title:
        return None
    return "https://hearthstone.wiki.gg/wiki/" + urllib.parse.quote(str(title).replace(" ", "_"), safe="/()_',")


def diamond_image_url(card_id: str) -> str:
    return "https://hearthstone.wiki.gg/wiki/Special:Redirect/file/" + urllib.parse.quote(card_id + "_Premium2.png")


def normalize_payload(
    row: dict[str, Any],
    constructed: dict[str, Any] | None,
    hsj_ru: dict[int, dict[str, Any]],
    hsj_en: dict[int, dict[str, Any]],
    animated_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    base_card_id = str(row.get("base_card_id") or "").strip()
    base_dbf = int(row["base_dbf"]) if row.get("base_dbf") not in (None, "") else None
    ru = hsj_ru.get(base_dbf or -1, {})
    en = hsj_en.get(base_dbf or -1, {})
    name_en = (constructed or {}).get("name_en") or en.get("name") or row.get("name_en")
    name_ru = (constructed or {}).get("name_ru") or ru.get("name")
    formats = set(str((constructed or {}).get("formats") or "").split(",")) if constructed else set()
    animated = animated_index.get(normalize_name(str(name_en or "")))
    image_url = diamond_image_url(base_card_id)
    return {
        "base_card_id": base_card_id,
        "base_dbf": base_dbf,
        "diamond_card_id": base_card_id + "_Premium2",
        "diamond_dbf": None,
        "name_en": name_en,
        "name_ru": name_ru,
        "card_set_id": int(row["card_set_id"]) if row.get("card_set_id") not in (None, "") else (constructed or {}).get("card_set_id"),
        "card_set": (constructed or {}).get("card_set") or en.get("set"),
        "card_type": (constructed or {}).get("card_type") or en.get("type"),
        "rarity": (constructed or {}).get("rarity") or en.get("rarity"),
        "class_slug": (constructed or {}).get("class_slug") or en.get("cardClass"),
        "mana_cost": (constructed or {}).get("mana_cost") if constructed else en.get("cost"),
        "collectible": 1 if str(row.get("is_collectible") or "") == "1" else 0,
        "section_slug": row.get("section_slug"),
        "section_name_ru": row.get("section_name_ru"),
        "in_standard": 1 if "standard" in formats else 0,
        "in_wild": 1 if "wild" in formats else 0,
        "image_url": image_url,
        "animated_url": animated.get("file_url") if animated else None,
        "animated_source": animated.get("source") if animated else None,
        "hearthpwn_url": "https://www.hearthpwn.com/tag/diamond-card",
        "wiki_page_title": row.get("page_title") or name_en,
        "wiki_page_url": page_url(row.get("page_title") or name_en),
        "source": SOURCE,
        "animated_asset": animated,
    }


def current_hash(conn, card_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT source_hash FROM constructed_diamond_cards WHERE base_card_id = %s", (card_id,))
        row = cur.fetchone()
    return row["source_hash"] if row else None


def save_payload(conn, payload: dict[str, Any], dry_run: bool) -> str:
    hash_payload = dict(payload)
    new_hash = stable_hash(hash_payload)
    changed = current_hash(conn, payload["base_card_id"]) != new_hash
    if dry_run:
        return "changed" if changed else "unchanged"

    now = utc_now()
    params = {
        **{k: payload.get(k) for k in [
            "base_card_id",
            "base_dbf",
            "diamond_card_id",
            "diamond_dbf",
            "name_en",
            "name_ru",
            "card_set_id",
            "card_set",
            "card_type",
            "rarity",
            "class_slug",
            "mana_cost",
            "collectible",
            "section_slug",
            "section_name_ru",
            "in_standard",
            "in_wild",
            "image_url",
            "animated_url",
            "animated_source",
            "hearthpwn_url",
            "wiki_page_title",
            "wiki_page_url",
            "source",
        ]},
        "source_payload_json": json_dump(payload),
        "source_hash": new_hash,
        "fetched_at": now,
        "changed_at": now,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO constructed_diamond_cards (
                base_card_id, base_dbf, diamond_card_id, diamond_dbf, name_en, name_ru,
                card_set_id, card_set, card_type, rarity, class_slug, mana_cost,
                collectible, section_slug, section_name_ru, in_standard, in_wild,
                image_url, animated_url, animated_source, hearthpwn_url,
                wiki_page_title, wiki_page_url, source, source_payload_json,
                source_hash, fetched_at, changed_at
            ) VALUES (
                %(base_card_id)s, %(base_dbf)s, %(diamond_card_id)s, %(diamond_dbf)s, %(name_en)s, %(name_ru)s,
                %(card_set_id)s, %(card_set)s, %(card_type)s, %(rarity)s, %(class_slug)s, %(mana_cost)s,
                %(collectible)s, %(section_slug)s, %(section_name_ru)s, %(in_standard)s, %(in_wild)s,
                %(image_url)s, %(animated_url)s, %(animated_source)s, %(hearthpwn_url)s,
                %(wiki_page_title)s, %(wiki_page_url)s, %(source)s, %(source_payload_json)s,
                %(source_hash)s, %(fetched_at)s, %(changed_at)s
            )
            ON DUPLICATE KEY UPDATE
                base_dbf = VALUES(base_dbf),
                diamond_card_id = VALUES(diamond_card_id),
                diamond_dbf = VALUES(diamond_dbf),
                name_en = VALUES(name_en),
                name_ru = VALUES(name_ru),
                card_set_id = VALUES(card_set_id),
                card_set = VALUES(card_set),
                card_type = VALUES(card_type),
                rarity = VALUES(rarity),
                class_slug = VALUES(class_slug),
                mana_cost = VALUES(mana_cost),
                collectible = VALUES(collectible),
                section_slug = VALUES(section_slug),
                section_name_ru = VALUES(section_name_ru),
                in_standard = VALUES(in_standard),
                in_wild = VALUES(in_wild),
                image_url = VALUES(image_url),
                animated_url = VALUES(animated_url),
                animated_source = VALUES(animated_source),
                hearthpwn_url = VALUES(hearthpwn_url),
                wiki_page_title = VALUES(wiki_page_title),
                wiki_page_url = VALUES(wiki_page_url),
                source = VALUES(source),
                source_payload_json = VALUES(source_payload_json),
                fetched_at = VALUES(fetched_at),
                changed_at = IF(constructed_diamond_cards.source_hash <> VALUES(source_hash) OR constructed_diamond_cards.source_hash IS NULL, VALUES(changed_at), changed_at),
                source_hash = VALUES(source_hash)
            """,
            params,
        )
        cur.execute(
            """
            UPDATE constructed_cards
            SET image_diamond_url = %s,
                animated_diamond_url = %s,
                changed_at = IF(
                    COALESCE(image_diamond_url, '') <> COALESCE(%s, '')
                    OR COALESCE(animated_diamond_url, '') <> COALESCE(%s, ''),
                    %s,
                    changed_at
                )
            WHERE card_id = %s
            """,
            (
                payload["image_url"],
                payload["animated_url"],
                payload["image_url"],
                payload["animated_url"],
                now,
                payload["base_card_id"],
            ),
        )
        diamond_cards_json = json_dump(
            [
                {
                    "kind": "diamond",
                    "card_id": payload["diamond_card_id"],
                    "base_card_id": payload["base_card_id"],
                    "base_dbf": payload["base_dbf"],
                    "file_url": payload["image_url"],
                    "wiki_page_url": payload["wiki_page_url"],
                    "section": payload["section_slug"],
                }
            ]
        )
        diamond_animated_json = json_dump([payload["animated_asset"]] if payload.get("animated_asset") else [])
        cur.execute(
            """
            UPDATE constructed_card_wiki_meta
            SET diamond_cards_json = %s,
                diamond_animated_json = %s,
                changed_at = IF(
                    COALESCE(diamond_cards_json, '') <> COALESCE(%s, '')
                    OR COALESCE(diamond_animated_json, '') <> COALESCE(%s, ''),
                    %s,
                    changed_at
                )
            WHERE card_id = %s
            """,
            (diamond_cards_json, diamond_animated_json, diamond_cards_json, diamond_animated_json, now, payload["base_card_id"]),
        )
    return "changed" if changed else "unchanged"


def run(dry_run: bool) -> dict[str, Any]:
    ensure_schema()
    config = load_php_config()
    conn = connect_db(config)
    summary = {"scanned": 0, "changed": 0, "unchanged": 0, "errors": 0}
    try:
        constructed = existing_constructed_by_card_id(conn)
        hsj_ru = load_hsj_by_dbf(HSJ_RU_URL)
        hsj_en = load_hsj_by_dbf(HSJ_EN_URL)
        animated_index = load_diamond_animated_index()
        rows = fetch_diamond_rows()
        for row in rows:
            summary["scanned"] += 1
            try:
                card_id = str(row.get("base_card_id") or "")
                payload = normalize_payload(row, constructed.get(card_id), hsj_ru, hsj_en, animated_index)
                status = save_payload(conn, payload, dry_run)
                summary[status] += 1
            except Exception as exc:
                summary["errors"] += 1
                print(f"[diamond-sync] {row.get('base_card_id')}: {exc}", file=sys.stderr)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Hearthstone diamond cards into constructed library.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json_dump({"dry_run": args.dry_run, "diamond_cards": run(args.dry_run)}))


if __name__ == "__main__":
    main()
