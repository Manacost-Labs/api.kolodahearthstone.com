#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from lxml import html
import pymysql
from pymysql.cursors import DictCursor


APP_ROOT = Path(__file__).resolve().parents[1]
PARSER_PATH = Path("/opt/wiki-hs-parser")
DEFAULT_CACHE_DIR = APP_ROOT / "var" / "constructed-wiki-cache"
DEFAULT_INDEX_PATH = DEFAULT_CACHE_DIR / "wiki-card-index-card.json"
SHARED_INDEX_PATH = APP_ROOT / "var" / "wiki-hs-cache" / "wiki-card-index-card.json"
SOURCE = "hearthstone.wiki.gg"
HSJ_EN_URL = "https://api.hearthstonejson.com/v1/latest/enUS/cards.json"


class RateLimitedError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


def import_parser():
    sys.path.insert(0, str(PARSER_PATH))
    import wiki_hs_lookup as lookup
    import wiki_hs_parser as parser
    return lookup, parser


def ensure_schema() -> None:
    subprocess.check_call(["php", str(APP_ROOT / "scripts" / "ensure_constructed_schema.php")])


def load_cards(
    conn,
    format_slug: str,
    set_slug: str | None,
    card_id: str | None,
    missing_only: bool,
    active_only: bool,
    oldest_first: bool,
    limit: int | None,
) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if format_slug != "all":
        where.append("f.format_slug = %s")
        params.append(format_slug)
    if set_slug:
        where.append("c.card_set = %s")
        params.append(set_slug)
    if card_id:
        where.append("c.card_id = %s")
        params.append(card_id)
    if active_only:
        where.append("f.in_format = 1")
    if missing_only:
        where.append("(wm.card_id IS NULL OR wm.status <> 'ok')")
    sql = """
        SELECT DISTINCT c.card_id, c.dbf, c.name_en, c.name_ru, c.wiki_page_title, c.wiki_page_url,
               MAX(c.source_payload_json) AS source_payload_json,
               GROUP_CONCAT(DISTINCT f.format_slug ORDER BY f.format_slug) AS formats
        FROM constructed_cards c
        INNER JOIN constructed_format_cards f ON f.card_id = c.card_id
        LEFT JOIN constructed_card_wiki_meta wm ON wm.card_id = c.card_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY c.card_id, c.dbf, c.name_en, c.name_ru, c.wiki_page_title, c.wiki_page_url"
    if oldest_first:
        sql += " ORDER BY COALESCE(MAX(wm.fetched_at), '1970-01-01 00:00:00') ASC, c.name_en ASC, c.card_id ASC"
    else:
        sql += " ORDER BY c.name_en ASC, c.card_id ASC"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def cache_path(cache_dir: Path, title: str, parser) -> Path:
    return cache_dir / "pages" / f"{parser.safe_filename(title)}.json"


def load_or_fetch_page(cache_dir: Path, title: str, refresh_pages: bool, parser, retries: int) -> tuple[dict[str, Any], str]:
    path = cache_path(cache_dir, title, parser)
    if not refresh_pages and path.exists():
        return json.loads(path.read_text(encoding="utf-8")), "cache"
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            result = parser.build_result(title, download=False, output_dir=cache_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return result, "network"
        except Exception as exc:
            if is_rate_limited(exc):
                raise RateLimitedError(f"Rate limited while fetching {title}: {exc}") from exc
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"Failed to fetch {title}: {last_error}") from last_error


def is_rate_limited(exc: Exception) -> bool:
    message = str(exc)
    return "HTTP Error 429" in message or "Too Many Requests" in message


def load_index(lookup, index_path: Path, refresh_index: bool) -> tuple[dict[str, Any], bool, str]:
    if not refresh_index and index_path.exists():
        return json.loads(index_path.read_text(encoding="utf-8")), False, str(index_path)
    if not refresh_index and index_path == DEFAULT_INDEX_PATH and SHARED_INDEX_PATH.exists():
        return json.loads(SHARED_INDEX_PATH.read_text(encoding="utf-8")), False, str(SHARED_INDEX_PATH)
    index, refreshed = lookup.load_or_refresh_index(index_path, "card", refresh_index)
    return index, bool(refreshed), str(index_path)


def find_match(lookup, index: dict[str, Any], card: dict[str, Any]) -> dict[str, Any] | None:
    for value, name in (
        (card.get("card_id"), None),
        (str(card["dbf"]) if card.get("dbf") is not None else None, None),
        (None, card.get("name_en")),
    ):
        matches = lookup.lookup_entries(index, value, name, "card")
        if matches:
            return matches[0]
    page_title = str(card.get("wiki_page_title") or "").strip()
    page_url = str(card.get("wiki_page_url") or "").strip()
    if not page_title and page_url:
        parsed = urlparse(page_url)
        if parsed.netloc.endswith("hearthstone.wiki.gg") and parsed.path.startswith("/wiki/"):
            page_title = unquote(parsed.path.removeprefix("/wiki/")).replace("_", " ")
    if page_title:
        return {"page_title": page_title, "page_url": page_url or f"https://hearthstone.wiki.gg/wiki/{page_title.replace(' ', '_')}"}
    return None


def simple_links_from_section(rendered_html: str, headline_id: str) -> list[dict[str, str | None]]:
    tree = html.fromstring(rendered_html)
    nodes = []
    try:
        import wiki_hs_parser as parser
        nodes = parser.iter_section_nodes(tree, headline_id)
    except Exception:
        nodes = []
    links: list[dict[str, str | None]] = []
    seen = set()
    for node in nodes:
        for anchor in node.xpath('.//a[@href]'):
            href = anchor.get("href") or ""
            text = " ".join(anchor.itertext()).strip()
            if not text and not href:
                continue
            if href.startswith("/wiki/"):
                href = "https://hearthstone.wiki.gg" + href
            key = (text, href)
            if key in seen:
                continue
            seen.add(key)
            links.append({"text": text or None, "url": href or None})
    return links


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


def load_hsj_by_dbf() -> dict[int, dict[str, Any]]:
    request = urllib.request.Request(
        HSJ_EN_URL,
        headers={
            "User-Agent": "db.kolodahs.ru-wiki-meta-sync/2.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        return {}
    return {
        int(card["dbfId"]): card
        for card in payload
        if isinstance(card, dict) and card.get("dbfId") is not None and card.get("id")
    }


def card_source_payload(card: dict[str, Any]) -> dict[str, Any]:
    value = card.get("source_payload_json")
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def wiki_card_url(name: str | None) -> str | None:
    name = str(name or "").strip()
    if not name:
        return None
    return "https://hearthstone.wiki.gg/wiki/" + quote(
        name.replace(" ", "_"),
        safe="/()_',.!:-",
    )


def intrinsic_related_groups(
    card: dict[str, Any],
    hsj_by_dbf: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    source = card_source_payload(card)
    blizzard = source.get("blizzard_en") or source.get("blizzard_ru") or {}
    parent_hsj = source.get("hsj_en") or source.get("hsj_ru") or {}
    if not isinstance(blizzard, dict) or not isinstance(parent_hsj, dict):
        return []

    child_dbfs = blizzard.get("childIds")
    if not isinstance(child_dbfs, list):
        return []
    mechanics = {
        str(value).upper()
        for value in (parent_hsj.get("mechanics") or [])
        if value not in (None, "")
    }
    referenced_tags = {
        str(value).upper()
        for value in (parent_hsj.get("referencedTags") or [])
        if value not in (None, "")
    }
    parent_type = str(parent_hsj.get("type") or "").upper()
    is_titan = "TITAN" in mechanics or "TITAN" in referenced_tags
    is_quest = bool({"QUEST", "QUESTLINE", "SIDE_QUEST"} & (mechanics | referenced_tags))
    is_choice = "CHOOSE_ONE" in mechanics or "CHOOSE_ONE" in referenced_tags

    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for child_dbf in child_dbfs:
        try:
            child = hsj_by_dbf.get(int(child_dbf))
        except (TypeError, ValueError):
            child = None
        if not child:
            continue
        child_id = str(child.get("id") or "").strip()
        if not child_id or child_id == card.get("card_id") or child_id in seen:
            continue
        seen.add(child_id)
        child_type = str(child.get("type") or "").upper()
        if parent_type == "HERO" and child_type == "HERO_POWER":
            heading = "Hero power"
        elif is_titan and child_type == "SPELL":
            heading = "Choice cards"
        elif is_quest:
            heading = "Quest rewards"
        elif is_choice:
            heading = "Choice cards"
        else:
            heading = "Related cards"
        grouped.setdefault(heading, []).append(
            {
                "card_id": child_id,
                "title": child.get("name"),
                "caption": "Official Blizzard child card",
                "url": wiki_card_url(child.get("name")),
                "image_alt": f"{child_id}.png",
                "image_url": None,
            }
        )
    return [{"heading": heading, "cards": cards} for heading, cards in grouped.items()]


def merge_related_groups(
    wiki_groups: list[dict[str, Any]],
    intrinsic_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = [
        {
            **group,
            "cards": list(group.get("cards", []) or []),
        }
        for group in wiki_groups
        if isinstance(group, dict)
    ]
    preferred_headings = {
        str(card.get("card_id") or "").strip(): str(
            group.get("heading") or "Related cards"
        )
        for group in intrinsic_groups
        if str(group.get("heading") or "Related cards") != "Related cards"
        for card in group.get("cards", []) or []
        if isinstance(card, dict) and str(card.get("card_id") or "").strip()
    }
    moved: dict[str, list[dict[str, Any]]] = {}
    for group in merged:
        if str(group.get("heading") or "") != "Related cards":
            continue
        retained = []
        for card in group.get("cards", []):
            card_id = str(card.get("card_id") or "").strip()
            preferred = preferred_headings.get(card_id)
            if preferred:
                moved.setdefault(preferred, []).append(card)
            else:
                retained.append(card)
        group["cards"] = retained
    merged = [group for group in merged if group.get("cards")]
    seen = {
        str(card.get("card_id") or "").strip()
        for group in merged
        for card in group.get("cards", [])
        if isinstance(card, dict) and str(card.get("card_id") or "").strip()
    }
    by_heading = {
        str(group.get("heading") or ""): group
        for group in merged
    }
    for heading, cards in moved.items():
        target = by_heading.get(heading)
        if target is None:
            target = {"heading": heading, "cards": []}
            merged.append(target)
            by_heading[heading] = target
        target["cards"].extend(cards)
        seen.update(
            str(card.get("card_id") or "").strip()
            for card in cards
            if str(card.get("card_id") or "").strip()
        )
    for intrinsic_group in intrinsic_groups:
        heading = str(intrinsic_group.get("heading") or "Related cards")
        target = by_heading.get(heading)
        if target is None:
            target = {"heading": heading, "cards": []}
            merged.append(target)
            by_heading[heading] = target
        for card in intrinsic_group.get("cards", []) or []:
            card_id = str(card.get("card_id") or "").strip()
            if not card_id or card_id in seen:
                continue
            seen.add(card_id)
            target["cards"].append(card)
    return [group for group in merged if group.get("cards")]


def related_ids(groups: list[dict[str, Any]]) -> list[str]:
    seen = set()
    ids = []
    for group in groups:
        for card in group.get("cards", []) or []:
            card_id = str(card.get("card_id") or "").strip()
            if card_id and card_id not in seen:
                seen.add(card_id)
                ids.append(card_id)
    return ids


def normalize_generated_card_pools(pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for pool in pools or []:
        cards = []
        card_ids = []
        seen = set()
        for card in pool.get("cards", []) or []:
            card_id = str(card.get("card_code") or "").strip() or None
            cards.append(
                {
                    "card_id": card_id,
                    "title": card.get("title"),
                    "caption": card.get("caption"),
                    "url": card.get("href"),
                    "image_alt": card.get("image_alt"),
                    "image_url": card.get("image_url"),
                }
            )
            if card_id and card_id not in seen:
                seen.add(card_id)
                card_ids.append(card_id)
        normalized.append(
            {
                "pool": pool.get("description"),
                "query_url": pool.get("query_url"),
                "card_ids": card_ids,
                "cards": cards,
            }
        )
    return normalized


def generated_card_ids(pools: list[dict[str, Any]]) -> list[str]:
    seen = set()
    ids = []
    for pool in pools:
        for card_id in pool.get("card_ids", []) or []:
            value = str(card_id or "").strip()
            if value and value not in seen:
                seen.add(value)
                ids.append(value)
    return ids


def normalize_ban_lists(card_data: dict[str, Any], section_links: list[dict[str, str | None]]) -> list[dict[str, Any]]:
    if section_links:
        return [{**link, "source": "wiki_section"} for link in section_links]
    availability = card_data.get("availability") if isinstance(card_data.get("availability"), dict) else {}
    exclusions = availability.get("exclusions") or card_data.get("exclusions") or []
    normalized = []
    seen = set()
    for exclusion in exclusions if isinstance(exclusions, list) else []:
        text = str(exclusion).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append({"text": text, "url": None, "source": "card_data.exclusions"})
    return normalized


def art_variants(result: dict[str, Any], contains: str) -> list[dict[str, Any]]:
    needle = contains.lower()
    variants = []
    for art in result.get("arts", []) or []:
        label = str(art.get("label") or art.get("source") or "").lower()
        caption = str(art.get("caption") or "").lower()
        if needle in label or needle in caption:
            variants.append(art)
    return variants


def normalize_payload(
    card: dict[str, Any],
    match: dict[str, Any],
    result: dict[str, Any],
    ban_lists: list[dict[str, str | None]],
    hsj_by_dbf: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    card_data = result.get("card_data") if isinstance(result.get("card_data"), dict) else {}
    related = merge_related_groups(
        normalize_related(result.get("related_cards", []) or []),
        intrinsic_related_groups(card, hsj_by_dbf),
    )
    generated_pools = normalize_generated_card_pools(result.get("generated_cards", []) or [])
    normalized_ban_lists = normalize_ban_lists(card_data, ban_lists)
    payload = {
        "source": SOURCE,
        "card_id": card["card_id"],
        "dbf": int(card["dbf"]) if card.get("dbf") is not None else None,
        "formats": str(card.get("formats") or "").split(",") if card.get("formats") else [],
        "wiki_page_title": result.get("page_title") or match.get("page_title"),
        "wiki_page_url": result.get("page_url") or match.get("page_url"),
        "wiki_mechanics": card_data.get("wiki_mechanics") or [],
        "wiki_tags": card_data.get("wiki_tags") or [],
        "ban_lists": normalized_ban_lists,
        "gallery": result.get("gallery_images", []) or [],
        "patch_changes": result.get("patch_changes", []) or [],
        "external_links": result.get("external_links", []) or [],
        "related_cards": related,
        "related_card_ids": related_ids(related),
        "generated_card_pools": generated_pools,
        "generated_card_ids": generated_card_ids(generated_pools),
        "sounds": result.get("sounds", []) or [],
        "golden_cards": art_variants(result, "golden"),
        "signature_cards": art_variants(result, "signature"),
        "diamond_cards": art_variants(result, "diamond"),
        "golden_animated": [],
        "signature_animated": [],
        "diamond_animated": [],
    }
    return payload


def current_hash(conn, card_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT source_hash FROM constructed_card_wiki_meta WHERE card_id = %s", (card_id,))
        row = cur.fetchone()
    return row["source_hash"] if row else None


def save_payload(conn, payload: dict[str, Any], dry_run: bool) -> str:
    new_hash = stable_hash(payload)
    changed = current_hash(conn, payload["card_id"]) != new_hash
    if dry_run:
        return "changed" if changed else "unchanged"
    now = utc_now()
    params = {
        "card_id": payload["card_id"],
        "dbf": payload["dbf"],
        "wiki_page_title": payload["wiki_page_title"],
        "wiki_page_url": payload["wiki_page_url"],
        "wiki_mechanics_json": json_dump(payload["wiki_mechanics"]),
        "wiki_tags_json": json_dump(payload["wiki_tags"]),
        "ban_lists_json": json_dump(payload["ban_lists"]),
        "gallery_json": json_dump(payload["gallery"]),
        "patch_changes_json": json_dump(payload["patch_changes"]),
        "external_links_json": json_dump(payload["external_links"]),
        "related_cards_json": json_dump(payload["related_cards"]),
        "related_card_ids_json": json_dump(payload["related_card_ids"]),
        "generated_card_pools_json": json_dump(payload["generated_card_pools"]),
        "generated_card_ids_json": json_dump(payload["generated_card_ids"]),
        "sounds_json": json_dump(payload["sounds"]),
        "golden_cards_json": json_dump(payload["golden_cards"]),
        "signature_cards_json": json_dump(payload["signature_cards"]),
        "diamond_cards_json": json_dump(payload["diamond_cards"]),
        "golden_animated_json": json_dump(payload["golden_animated"]),
        "signature_animated_json": json_dump(payload["signature_animated"]),
        "diamond_animated_json": json_dump(payload["diamond_animated"]),
        "source_payload_json": json_dump(payload),
        "source_hash": new_hash,
        "fetched_at": now,
        "changed_at": now,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO constructed_card_wiki_meta (
                card_id, dbf, wiki_page_title, wiki_page_url,
                wiki_mechanics_json, wiki_tags_json, ban_lists_json, gallery_json,
                patch_changes_json, external_links_json, related_cards_json, related_card_ids_json,
                generated_card_pools_json, generated_card_ids_json,
                sounds_json, golden_cards_json, signature_cards_json, diamond_cards_json,
                golden_animated_json, signature_animated_json, diamond_animated_json, source_payload_json,
                source_hash, status, error, fetched_at, changed_at
            ) VALUES (
                %(card_id)s, %(dbf)s, %(wiki_page_title)s, %(wiki_page_url)s,
                %(wiki_mechanics_json)s, %(wiki_tags_json)s, %(ban_lists_json)s, %(gallery_json)s,
                %(patch_changes_json)s, %(external_links_json)s, %(related_cards_json)s, %(related_card_ids_json)s,
                %(generated_card_pools_json)s, %(generated_card_ids_json)s,
                %(sounds_json)s, %(golden_cards_json)s, %(signature_cards_json)s, %(diamond_cards_json)s,
                %(golden_animated_json)s, %(signature_animated_json)s, %(diamond_animated_json)s, %(source_payload_json)s,
                %(source_hash)s, 'ok', NULL, %(fetched_at)s, %(changed_at)s
            )
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf), wiki_page_title = VALUES(wiki_page_title), wiki_page_url = VALUES(wiki_page_url),
                wiki_mechanics_json = VALUES(wiki_mechanics_json), wiki_tags_json = VALUES(wiki_tags_json),
                ban_lists_json = VALUES(ban_lists_json), gallery_json = VALUES(gallery_json),
                patch_changes_json = VALUES(patch_changes_json), external_links_json = VALUES(external_links_json),
                related_cards_json = VALUES(related_cards_json), related_card_ids_json = VALUES(related_card_ids_json),
                generated_card_pools_json = VALUES(generated_card_pools_json),
                generated_card_ids_json = VALUES(generated_card_ids_json),
                sounds_json = VALUES(sounds_json), golden_cards_json = VALUES(golden_cards_json),
                signature_cards_json = VALUES(signature_cards_json), diamond_cards_json = VALUES(diamond_cards_json),
                golden_animated_json = VALUES(golden_animated_json),
                signature_animated_json = VALUES(signature_animated_json),
                diamond_animated_json = VALUES(diamond_animated_json),
                source_payload_json = VALUES(source_payload_json),
                fetched_at = VALUES(fetched_at), status = 'ok', error = NULL,
                changed_at = IF(constructed_card_wiki_meta.source_hash <> VALUES(source_hash) OR constructed_card_wiki_meta.source_hash IS NULL, VALUES(changed_at), changed_at),
                source_hash = VALUES(source_hash)
            """,
            params,
        )
    golden_url = first_file_url(payload.get("golden_cards") or [])
    signature_url = first_file_url(payload.get("signature_cards") or [])
    diamond_url = first_file_url(payload.get("diamond_cards") or [])
    if golden_url or signature_url or diamond_url:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE constructed_cards
                SET image_gold_url = IF(%s IS NOT NULL AND %s <> '' AND (image_gold_url IS NULL OR image_gold_url = ''), %s, image_gold_url),
                    image_signature_url = IF(%s IS NOT NULL AND %s <> '' AND (image_signature_url IS NULL OR image_signature_url = ''), %s, image_signature_url),
                    image_diamond_url = IF(%s IS NOT NULL AND %s <> '' AND (image_diamond_url IS NULL OR image_diamond_url = ''), %s, image_diamond_url),
                    changed_at = IF(
                        (%s IS NOT NULL AND %s <> '' AND (image_gold_url IS NULL OR image_gold_url = ''))
                        OR (%s IS NOT NULL AND %s <> '' AND (image_signature_url IS NULL OR image_signature_url = ''))
                        OR (%s IS NOT NULL AND %s <> '' AND (image_diamond_url IS NULL OR image_diamond_url = '')),
                        %s,
                        changed_at
                    )
                WHERE card_id = %s
                """,
                (
                    golden_url,
                    golden_url,
                    golden_url,
                    signature_url,
                    signature_url,
                    signature_url,
                    diamond_url,
                    diamond_url,
                    diamond_url,
                    golden_url,
                    golden_url,
                    signature_url,
                    signature_url,
                    diamond_url,
                    diamond_url,
                    now,
                    payload["card_id"],
                ),
            )
    return "changed" if changed else "unchanged"


def first_file_url(items: list[dict[str, Any]]) -> str | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("file_url") or item.get("image_url") or "").strip()
        if url:
            return url
    return None


def save_error(conn, card: dict[str, Any], status: str, error: str, dry_run: bool) -> None:
    if dry_run:
        return
    now = utc_now()
    payload = {"card_id": card["card_id"], "dbf": card.get("dbf"), "status": status, "error": error}
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO constructed_card_wiki_meta (card_id, dbf, source_payload_json, source_hash, status, error, fetched_at, changed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf), source_payload_json = VALUES(source_payload_json),
                source_hash = VALUES(source_hash), status = VALUES(status), error = VALUES(error),
                fetched_at = VALUES(fetched_at), changed_at = VALUES(changed_at)
            """,
            (card["card_id"], card.get("dbf"), json_dump(payload), stable_hash(payload), status, error[:65535], now, now),
        )


def sleep_between(delay: float, jitter: float) -> None:
    wait = max(0.0, delay) + (random.uniform(0, jitter) if jitter > 0 else 0)
    if wait:
        time.sleep(wait)


def main() -> int:
    argp = argparse.ArgumentParser(description="Sync constructed Standard/Wild wiki metadata.")
    argp.add_argument("--format", choices=["all", "standard", "wild"], default="all")
    argp.add_argument("--set", dest="set_slug", help="Only sync one card_set slug.")
    argp.add_argument("--card-id", help="Only sync one exact constructed card ID.")
    argp.add_argument("--active-only", action="store_true", default=True)
    argp.add_argument("--all-format-links", action="store_false", dest="active_only")
    argp.add_argument("--missing-only", action="store_true")
    argp.add_argument(
        "--oldest-first",
        action="store_true",
        help="Refresh the least recently fetched cards first so scheduled batches eventually cover the full catalog.",
    )
    argp.add_argument("--limit", type=int)
    argp.add_argument("--refresh-index", action="store_true")
    argp.add_argument("--refresh-pages", action="store_true")
    argp.add_argument(
        "--skip-ban-list-refresh",
        action="store_true",
        help="Skip the second Wiki request used only for Ban lists during bulk refreshes.",
    )
    argp.add_argument("--index-path", type=Path, default=DEFAULT_INDEX_PATH)
    argp.add_argument("--delay-seconds", type=float, default=1.0)
    argp.add_argument("--jitter-seconds", type=float, default=0.3)
    argp.add_argument("--commit-every", type=int, default=10)
    argp.add_argument("--dry-run", action="store_true")
    args = argp.parse_args()
    args.set_slug = str(args.set_slug or "").strip().upper() or None
    args.card_id = str(args.card_id or "").strip() or None

    ensure_schema()
    lookup, parser = import_parser()
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    args.index_path.parent.mkdir(parents=True, exist_ok=True)
    index, refreshed, index_source = load_index(lookup, args.index_path, args.refresh_index)
    conn = connect_db(load_php_config())
    stats = {"scanned": 0, "changed": 0, "unchanged": 0, "missing": 0, "errors": 0, "rate_limited": 0, "network": 0, "cache": 0}
    try:
        cards = load_cards(
            conn,
            args.format,
            args.set_slug,
            args.card_id,
            args.missing_only,
            args.active_only,
            args.oldest_first,
            args.limit,
        )
        try:
            hsj_by_dbf = load_hsj_by_dbf()
        except Exception as exc:
            hsj_by_dbf = {}
            print(
                f"Warning: official child-card catalog is unavailable: {exc}",
                file=sys.stderr,
            )
        print(json_dump({"cards": len(cards), "format": args.format, "set": args.set_slug, "card_id": args.card_id, "oldest_first": args.oldest_first, "index_refreshed": refreshed, "index_source": index_source, "dry_run": args.dry_run}))
        for idx, card in enumerate(cards, start=1):
            stats["scanned"] += 1
            try:
                match = find_match(lookup, index, card)
                if not match:
                    stats["missing"] += 1
                    save_error(conn, card, "missing", "No hearthstone.wiki.gg lookup match", args.dry_run)
                    print(f"[{idx}/{len(cards)}] {card['card_id']} missing")
                    continue
                result, source = load_or_fetch_page(DEFAULT_CACHE_DIR, str(match["page_title"]), args.refresh_pages, parser, retries=3)
                stats[source] += 1
                try:
                    rendered_html = (
                        parser.get_rendered_html(str(match["page_title"]))
                        if args.refresh_pages and not args.skip_ban_list_refresh
                        else ""
                    )
                except Exception as exc:
                    if is_rate_limited(exc):
                        raise RateLimitedError(f"Rate limited while fetching rendered HTML for {match['page_title']}: {exc}") from exc
                    raise
                ban_lists = simple_links_from_section(rendered_html, "Ban_lists") if rendered_html else []
                payload = normalize_payload(
                    card,
                    match,
                    result,
                    ban_lists,
                    hsj_by_dbf,
                )
                outcome = save_payload(conn, payload, args.dry_run)
                stats[outcome] += 1
                print(f"[{idx}/{len(cards)}] {card['card_id']} {outcome} ({source})")
                if source == "network":
                    sleep_between(args.delay_seconds, args.jitter_seconds)
            except RateLimitedError as exc:
                stats["rate_limited"] += 1
                save_error(conn, card, "rate_limited", str(exc), args.dry_run)
                print(f"[{idx}/{len(cards)}] {card['card_id']} rate_limited: {exc}", file=sys.stderr)
                break
            except Exception as exc:
                stats["errors"] += 1
                save_error(conn, card, "error", str(exc), args.dry_run)
                print(f"[{idx}/{len(cards)}] {card['card_id']} error: {exc}", file=sys.stderr)
            if not args.dry_run and idx % max(1, args.commit_every) == 0:
                conn.commit()
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        print(json_dump(stats))
    finally:
        conn.close()
    if stats["rate_limited"]:
        return 75
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
