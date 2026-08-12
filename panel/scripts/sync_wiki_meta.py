#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARSER_PATH = Path("/opt/wiki-hs-parser")
DEFAULT_CACHE_DIR = APP_ROOT / "var" / "wiki-hs-cache"
SOURCE = "hearthstone.wiki.gg"
MANUAL_PAGE_TITLES = {
    "BG23_HERO_303pt": "Battlegrounds/No Minions",
    "BG25_HERO_100pt": "Battlegrounds/Putricide's Creation",
}
TECHNICAL_NO_WIKI_PAGE = {
    "BG26_173": {
        "wiki_mechanics": ["Trigger visual"],
        "wiki_tags": ["Technical", "Token", "VFX"],
        "availability_note": "Technical VFX dummy token; no standalone hearthstone.wiki.gg page was found.",
    }
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_php_config() -> dict[str, Any]:
    code = f"echo json_encode(require {json.dumps(str(APP_ROOT / 'config.php'))}, JSON_UNESCAPED_SLASHES);"
    output = subprocess.check_output(["php", "-r", code], text=True)
    config = json.loads(output)
    if not isinstance(config, dict) or "db" not in config:
        raise RuntimeError("config.php does not contain db settings")
    return config


def parse_mysql_dsn(dsn: str) -> dict[str, Any]:
    if not dsn.startswith("mysql:"):
        raise RuntimeError(f"Unsupported DSN: {dsn}")
    parsed: dict[str, Any] = {}
    for part in dsn[len("mysql:") :].split(";"):
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        parsed[key] = value
    return parsed


def connect_db(config: dict[str, Any]):
    db = config["db"]
    dsn = parse_mysql_dsn(str(db["dsn"]))
    kwargs: dict[str, Any] = {
        "user": db["user"],
        "password": db["password"],
        "database": dsn.get("dbname"),
        "charset": dsn.get("charset", "utf8mb4"),
        "cursorclass": DictCursor,
        "autocommit": False,
    }
    if dsn.get("unix_socket"):
        kwargs["unix_socket"] = dsn["unix_socket"]
    else:
        kwargs["host"] = dsn.get("host", "localhost")
        if dsn.get("port"):
            kwargs["port"] = int(dsn["port"])
    return pymysql.connect(**kwargs)


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS battlegrounds_card_wiki_meta (
                card_id VARCHAR(64) NOT NULL,
                dbf INT UNSIGNED DEFAULT NULL,
                wiki_page_title VARCHAR(255) DEFAULT NULL,
                wiki_page_url VARCHAR(512) DEFAULT NULL,
                artist VARCHAR(255) DEFAULT NULL,
                race VARCHAR(128) DEFAULT NULL,
                minion_type VARCHAR(128) DEFAULT NULL,
                wiki_mechanics_json JSON DEFAULT NULL,
                wiki_tags_json JSON DEFAULT NULL,
                availability_json JSON DEFAULT NULL,
                sounds_json JSON DEFAULT NULL,
                external_links_json JSON DEFAULT NULL,
                related_cards_json JSON DEFAULT NULL,
                related_card_ids_json JSON DEFAULT NULL,
                card_changes_json JSON DEFAULT NULL,
                source_payload_json JSON DEFAULT NULL,
                source_hash CHAR(64) DEFAULT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'ok',
                error TEXT DEFAULT NULL,
                fetched_at TIMESTAMP NULL DEFAULT NULL,
                changed_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (card_id),
                KEY idx_dbf (dbf),
                KEY idx_status (status),
                KEY idx_changed_at (changed_at),
                KEY idx_fetched_at (fetched_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cur.execute(
            """
            ALTER TABLE battlegrounds_card_wiki_meta
            ADD COLUMN IF NOT EXISTS related_card_ids_json JSON DEFAULT NULL AFTER related_cards_json,
            ADD COLUMN IF NOT EXISTS card_changes_json JSON DEFAULT NULL AFTER related_card_ids_json
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS battlegrounds_card_wiki_related (
                id INT UNSIGNED NOT NULL AUTO_INCREMENT,
                card_id VARCHAR(64) NOT NULL,
                related_card_id VARCHAR(64) DEFAULT NULL,
                heading VARCHAR(255) DEFAULT NULL,
                title VARCHAR(255) DEFAULT NULL,
                caption VARCHAR(255) DEFAULT NULL,
                url VARCHAR(512) DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                KEY idx_card_id (card_id),
                KEY idx_related_card_id (related_card_id),
                UNIQUE KEY uniq_card_related (card_id, related_card_id, heading, title)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    conn.commit()


def load_cards(conn, args: argparse.Namespace) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if args.card_type != "all":
        where.append("card_type = %s")
        params.append(args.card_type)
    else:
        where.append("card_type IN ('minion', 'spell')")
    if args.card_id:
        where.append("card_id = %s")
        params.append(args.card_id)
    if args.in_pool_only:
        where.append("in_pool = 1")
    missing_join = ""
    if args.missing_only:
        missing_join = "LEFT JOIN battlegrounds_card_wiki_meta wm ON wm.card_id = c.card_id"
        where.append("(wm.card_id IS NULL OR wm.status <> 'ok')")

    limit = ""
    if args.limit is not None:
        limit = " LIMIT %s"
        params.append(args.limit)

    sql = f"""
        SELECT c.card_id, c.dbf, c.name, c.name_en, c.card_type, c.in_pool
        FROM battlegrounds_cards c
        {missing_join}
        WHERE {' AND '.join(where)}
        ORDER BY c.in_pool DESC, c.tavern_tier IS NULL, c.tavern_tier, c.name
        {limit}
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def cache_file(cache_dir: Path, safe_filename, page_title: str) -> Path:
    return cache_dir / "pages" / f"{safe_filename(page_title)}.json"


def load_or_fetch_page(cache_dir: Path, parser, safe_filename, page_title: str, refresh: bool) -> tuple[dict[str, Any], str]:
    path = cache_file(cache_dir, safe_filename, page_title)
    if not refresh and path.exists():
        return json.loads(path.read_text(encoding="utf-8")), "cache"

    result = parser.build_result(page_title, download=False, output_dir=cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, "network"


def normalize_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for link in links or []:
        normalized.append(
            {
                "label": link.get("label"),
                "url": link.get("url"),
            }
        )
    return normalized


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


def related_card_ids(related_groups: list[dict[str, Any]]) -> list[str]:
    seen = set()
    ids = []
    for group in related_groups or []:
        for card in group.get("cards", []) or []:
            card_id = (card.get("card_id") or "").strip()
            if card_id and card_id not in seen:
                seen.add(card_id)
                ids.append(card_id)
    return ids


def normalize_availability(card_data: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    availability = card_data.get("availability") if isinstance(card_data.get("availability"), dict) else {}
    page_entries = []
    for entry in result.get("page_availability", []) or []:
        page_entries.append(
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
        )
    return {
        "formats": availability.get("formats", []),
        "exclusions": availability.get("exclusions", []),
        "notes": availability.get("notes") or card_data.get("availability_notes") or [],
        "page_entries": page_entries,
    }


def normalize_payload(card: dict[str, Any], match: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    card_data = result.get("card_data") if isinstance(result.get("card_data"), dict) else {}
    related = normalize_related(result.get("related_cards", []) or [])
    changes = normalize_card_changes(result.get("patch_changes", []) or [])
    payload = {
        "source": SOURCE,
        "card_id": card["card_id"],
        "dbf": int(card["dbf"]) if card.get("dbf") is not None else None,
        "card_type": card.get("card_type"),
        "wiki_page_title": result.get("page_title") or match.get("page_title"),
        "wiki_page_url": result.get("page_url") or match.get("page_url"),
        "artist": card_data.get("artist") or match.get("details", {}).get("artist"),
        "race": card_data.get("race") or None,
        "minion_type": card_data.get("minion_type") or None,
        "wiki_mechanics": card_data.get("wiki_mechanics") or [],
        "wiki_tags": card_data.get("wiki_tags") or [],
        "availability": normalize_availability(card_data, result),
        "sounds": normalize_sounds(result.get("sounds", []) or []),
        "external_links": normalize_links(result.get("external_links", []) or []),
        "related_cards": related,
        "related_card_ids": related_card_ids(related),
        "card_changes": changes,
    }
    return payload


def technical_payload(card: dict[str, Any]) -> dict[str, Any]:
    technical = TECHNICAL_NO_WIKI_PAGE[str(card["card_id"])]
    payload = {
        "source": SOURCE,
        "card_id": card["card_id"],
        "dbf": int(card["dbf"]) if card.get("dbf") is not None else None,
        "card_type": card.get("card_type"),
        "wiki_page_title": None,
        "wiki_page_url": None,
        "artist": None,
        "race": None,
        "minion_type": None,
        "wiki_mechanics": technical["wiki_mechanics"],
        "wiki_tags": technical["wiki_tags"],
        "availability": {
            "formats": [],
            "exclusions": [],
            "notes": [technical["availability_note"]],
            "page_entries": [],
        },
        "sounds": [],
        "external_links": [],
        "related_cards": [],
        "related_card_ids": [],
        "card_changes": [],
        "technical_no_wiki_page": True,
    }
    return payload


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json_dump(payload).encode("utf-8")).hexdigest()


def lookup_scope(card: dict[str, Any]) -> str:
    return "card" if card.get("card_type") == "spell" else "bg-minion"


def find_match(lookup, indexes: dict[str, Any], card: dict[str, Any]) -> dict[str, Any] | None:
    manual_title = MANUAL_PAGE_TITLES.get(str(card.get("card_id")))
    if manual_title:
        return {"scope": "manual", "page_title": manual_title, "page_url": None, "details": {}}

    scope = lookup_scope(card)
    index = indexes[scope]
    for value, name in (
        (card.get("card_id"), None),
        (str(card["dbf"]) if card.get("dbf") is not None else None, None),
        (None, card.get("name_en")),
    ):
        matches = lookup.lookup_entries(index, value, name, scope)
        if scope == "card":
            matches = [
                match for match in matches
                if str(match.get("page_title") or "").startswith("Battlegrounds/")
            ]
        if matches:
            return matches[0]
    return None


def current_hash(conn, card_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT source_hash FROM battlegrounds_card_wiki_meta WHERE card_id = %s", (card_id,))
        row = cur.fetchone()
    return row["source_hash"] if row else None


def save_related(conn, card_id: str, related_groups: list[dict[str, Any]]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM battlegrounds_card_wiki_related WHERE card_id = %s", (card_id,))
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
                    )
                )
        if rows:
            cur.executemany(
                """
                INSERT IGNORE INTO battlegrounds_card_wiki_related
                (card_id, related_card_id, heading, title, caption, url)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                rows,
            )


def save_payload(conn, payload: dict[str, Any], dry_run: bool) -> str:
    new_hash = payload_hash(payload)
    old_hash = current_hash(conn, payload["card_id"])
    changed = old_hash != new_hash
    if dry_run:
        return "changed" if changed else "unchanged"

    now = utc_now()
    changed_at_expr = now if changed else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO battlegrounds_card_wiki_meta (
                card_id, dbf, wiki_page_title, wiki_page_url, artist, race, minion_type,
                wiki_mechanics_json, wiki_tags_json, availability_json, sounds_json,
                external_links_json, related_cards_json, related_card_ids_json,
                card_changes_json, source_payload_json, source_hash,
                status, error, fetched_at, changed_at
            ) VALUES (
                %(card_id)s, %(dbf)s, %(wiki_page_title)s, %(wiki_page_url)s, %(artist)s, %(race)s, %(minion_type)s,
                %(wiki_mechanics_json)s, %(wiki_tags_json)s, %(availability_json)s, %(sounds_json)s,
                %(external_links_json)s, %(related_cards_json)s, %(related_card_ids_json)s,
                %(card_changes_json)s, %(source_payload_json)s, %(source_hash)s,
                'ok', NULL, %(fetched_at)s, %(changed_at)s
            )
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf),
                wiki_page_title = VALUES(wiki_page_title),
                wiki_page_url = VALUES(wiki_page_url),
                artist = VALUES(artist),
                race = VALUES(race),
                minion_type = VALUES(minion_type),
                wiki_mechanics_json = VALUES(wiki_mechanics_json),
                wiki_tags_json = VALUES(wiki_tags_json),
                availability_json = VALUES(availability_json),
                sounds_json = VALUES(sounds_json),
                external_links_json = VALUES(external_links_json),
                related_cards_json = VALUES(related_cards_json),
                related_card_ids_json = VALUES(related_card_ids_json),
                card_changes_json = VALUES(card_changes_json),
                source_payload_json = VALUES(source_payload_json),
                status = 'ok',
                error = NULL,
                fetched_at = VALUES(fetched_at),
                changed_at = IF(
                    battlegrounds_card_wiki_meta.source_hash <> VALUES(source_hash)
                    OR battlegrounds_card_wiki_meta.source_hash IS NULL,
                    VALUES(changed_at),
                    changed_at
                ),
                source_hash = VALUES(source_hash)
            """,
            {
                "card_id": payload["card_id"],
                "dbf": payload["dbf"],
                "wiki_page_title": payload["wiki_page_title"],
                "wiki_page_url": payload["wiki_page_url"],
                "artist": payload["artist"],
                "race": payload["race"],
                "minion_type": payload["minion_type"],
                "wiki_mechanics_json": json_dump(payload["wiki_mechanics"]),
                "wiki_tags_json": json_dump(payload["wiki_tags"]),
                "availability_json": json_dump(payload["availability"]),
                "sounds_json": json_dump(payload["sounds"]),
                "external_links_json": json_dump(payload["external_links"]),
                "related_cards_json": json_dump(payload["related_cards"]),
                "related_card_ids_json": json_dump(payload["related_card_ids"]),
                "card_changes_json": json_dump(payload["card_changes"]),
                "source_payload_json": json_dump(payload),
                "source_hash": new_hash,
                "fetched_at": now,
                "changed_at": changed_at_expr or now,
            },
        )
    save_related(conn, payload["card_id"], payload["related_cards"])
    return "changed" if changed else "unchanged"


def save_error(conn, card: dict[str, Any], status: str, error: str, dry_run: bool) -> None:
    if dry_run:
        return
    now = utc_now()
    error_payload = {
        "source": SOURCE,
        "card_id": card["card_id"],
        "dbf": int(card["dbf"]) if card.get("dbf") is not None else None,
        "status": status,
        "error": error,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO battlegrounds_card_wiki_meta
            (card_id, dbf, source_payload_json, source_hash, status, error, fetched_at, changed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf),
                source_payload_json = VALUES(source_payload_json),
                source_hash = VALUES(source_hash),
                status = VALUES(status),
                error = VALUES(error),
                fetched_at = VALUES(fetched_at),
                changed_at = IF(
                    battlegrounds_card_wiki_meta.source_hash <> VALUES(source_hash)
                    OR battlegrounds_card_wiki_meta.source_hash IS NULL,
                    VALUES(changed_at),
                    changed_at
                ),
                source_hash = VALUES(source_hash)
            """,
            (
                card["card_id"],
                int(card["dbf"]) if card.get("dbf") is not None else None,
                json_dump(error_payload),
                payload_hash(error_payload),
                status,
                error[:65535],
                now,
                now,
            ),
        )
        cur.execute("DELETE FROM battlegrounds_card_wiki_related WHERE card_id = %s", (card["card_id"],))


def sleep_between_requests(delay_seconds: float, jitter_seconds: float) -> None:
    wait = max(0.0, delay_seconds)
    if jitter_seconds > 0:
        wait += random.uniform(0, jitter_seconds)
    if wait:
        time.sleep(wait)


def import_parser(parser_path: Path):
    sys.path.insert(0, str(parser_path))
    import wiki_hs_lookup as lookup
    import wiki_hs_parser as parser

    return lookup, parser


def main() -> int:
    argp = argparse.ArgumentParser(description="Sync Battlegrounds minion metadata from hearthstone.wiki.gg.")
    argp.add_argument("--parser-path", default=str(DEFAULT_PARSER_PATH))
    argp.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    argp.add_argument("--refresh-index", action="store_true")
    argp.add_argument("--refresh-pages", action="store_true")
    argp.add_argument("--card-id")
    argp.add_argument("--card-type", choices=["minion", "spell", "all"], default="minion")
    argp.add_argument("--in-pool-only", action="store_true")
    argp.add_argument("--missing-only", action="store_true")
    argp.add_argument("--limit", type=int)
    argp.add_argument("--delay-seconds", type=float, default=1.2)
    argp.add_argument("--jitter-seconds", type=float, default=0.4)
    argp.add_argument("--commit-every", type=int, default=10)
    argp.add_argument("--dry-run", action="store_true")
    args = argp.parse_args()

    parser_path = Path(args.parser_path)
    cache_dir = Path(args.cache_dir)
    if not parser_path.exists():
        raise RuntimeError(f"Parser path does not exist: {parser_path}")

    lookup, parser = import_parser(parser_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    indexes = {}
    refreshed_scopes = {}
    scopes = ["bg-minion"] if args.card_type == "minion" else (["card"] if args.card_type == "spell" else ["bg-minion", "card"])
    for scope in scopes:
        index_path = cache_dir / f"wiki-card-index-{scope}.json"
        indexes[scope], refreshed_scopes[scope] = lookup.load_or_refresh_index(index_path, scope, args.refresh_index)

    conn = connect_db(load_php_config())
    stats = {"scanned": 0, "changed": 0, "unchanged": 0, "missing": 0, "errors": 0, "network": 0, "cache": 0}
    try:
        ensure_schema(conn)
        cards = load_cards(conn, args)
        print(json_dump({
            "cards": len(cards),
            "card_type": args.card_type,
            "index_refreshed": refreshed_scopes,
            "dry_run": args.dry_run,
        }))
        for idx, card in enumerate(cards, start=1):
            stats["scanned"] += 1
            try:
                match = find_match(lookup, indexes, card)
                if not match:
                    if str(card.get("card_id")) in TECHNICAL_NO_WIKI_PAGE:
                        payload = technical_payload(card)
                        outcome = save_payload(conn, payload, args.dry_run)
                        stats[outcome] += 1
                        print(f"[{idx}/{len(cards)}] {card['card_id']} {outcome} (technical)")
                        continue
                    stats["missing"] += 1
                    save_error(conn, card, "missing", f"No {lookup_scope(card)} wiki lookup match", args.dry_run)
                    print(f"[{idx}/{len(cards)}] {card['card_id']} missing")
                    continue

                result, source = load_or_fetch_page(cache_dir, parser, parser.safe_filename, match["page_title"], args.refresh_pages)
                stats[source] += 1
                payload = normalize_payload(card, match, result)
                outcome = save_payload(conn, payload, args.dry_run)
                stats[outcome] += 1
                print(f"[{idx}/{len(cards)}] {card['card_id']} {outcome} ({source})")
                if source == "network" and idx < len(cards):
                    sleep_between_requests(args.delay_seconds, args.jitter_seconds)
            except Exception as exc:
                stats["errors"] += 1
                save_error(conn, card, "error", str(exc), args.dry_run)
                print(f"[{idx}/{len(cards)}] {card['card_id']} error: {exc}", file=sys.stderr)

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
