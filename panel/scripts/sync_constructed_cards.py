#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor


APP_ROOT = Path(__file__).resolve().parents[1]
HSJ_RU_URL = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.json"
HSJ_EN_URL = "https://api.hearthstonejson.com/v1/latest/enUS/cards.json"

# Blizzard can lag behind a freshly released balance patch. Keep both text and
# rendered-image corrections narrowly scoped and self-expiring: they apply only
# while the provider still exposes the exact known pre-patch value.
PATCH_TEXT_OVERRIDES: dict[str, dict[str, dict[str, str]]] = {
    "JAIL_733": {
        "en": {
            "old_marker": "Battlecry and Deathrattle:",
            "text": "<b>Taunt</b> <b>Deathrattle:</b> Get a Void Soul.",
        },
        "ru": {
            "old_marker": "Боевой клич и предсмертный хрип:",
            "text": "<b>Провокация</b>. <b>Предсмертный хрип:</b> вы получаете «Душу Бездны».",
        },
    },
}
PATCH_IMAGE_OVERRIDES: dict[str, dict[str, str]] = {
    "JAIL_733": {
        "stale_url": "https://d15f34w2p8l1cc.cloudfront.net/hearthstone/c1d2c0af640c1c3cb4a580021cbc662ecf8510e469a04c265eeca2831a5c70b0.png",
        "image_url": "https://art.hearthstonejson.com/v1/render/latest/ruRU/512x/JAIL_733.png",
    },
}
# These event rewards are playable in Standard and Wild from August 4 through
# August 25, but Hearthstone's Game Data API did not expose them at patch 36.2
# launch. Keep the fallback deliberately narrow and date-bound so unrelated or
# retired HearthstoneJSON records can never leak into a live format.
TEMPORARY_HSJ_FORMAT_FALLBACKS = {
    "standard": {"JAIL_EVENT_100", "JAIL_EVENT_101", "JAIL_EVENT_102"},
    "wild": {"JAIL_EVENT_100", "JAIL_EVENT_101", "JAIL_EVENT_102"},
}
TEMPORARY_HSJ_FORMAT_FALLBACK_EXPIRES_ON = date(2026, 8, 26)
SOURCE = "blizzard"


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


def http_json(url: str, headers: dict[str, str] | None = None, data: bytes | None = None) -> Any:
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "db.kolodahs.ru-constructed-sync/1.0",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def blizzard_token() -> tuple[str, str]:
    client_id = os.getenv("BLIZZARD_CLIENT_ID", "")
    client_secret = os.getenv("BLIZZARD_CLIENT_SECRET", "")
    region = os.getenv("BLIZZARD_REGION", "us")
    if not client_id or not client_secret:
        raise RuntimeError("BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET are required")
    token = http_json(
        "https://oauth.battle.net/token",
        headers={"Authorization": "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()},
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
    )
    access_token = token.get("access_token") if isinstance(token, dict) else None
    if not access_token:
        raise RuntimeError("Blizzard token response does not contain access_token")
    return region, str(access_token)


def fetch_blizzard_cards(format_slug: str, locale: str, region: str, token: str) -> dict[int, dict[str, Any]]:
    page = 1
    result: dict[int, dict[str, Any]] = {}
    while True:
        url = "https://" + region + ".api.blizzard.com/hearthstone/cards?" + urllib.parse.urlencode(
            {
                "locale": locale,
                "gameMode": "constructed",
                "set": format_slug,
                "pageSize": 500,
                "page": page,
            }
        )
        data = http_json(url, headers={"Authorization": "Bearer " + token})
        cards = data.get("cards", []) if isinstance(data, dict) else []
        for card in cards:
            if isinstance(card, dict) and card.get("id") is not None:
                result[int(card["id"])] = card
        page_count = int(data.get("pageCount") or page) if isinstance(data, dict) else page
        if page >= page_count:
            break
        page += 1
    return result


def fetch_hsj(url: str) -> dict[int, dict[str, Any]]:
    cards = http_json(url)
    result: dict[int, dict[str, Any]] = {}
    for card in cards if isinstance(cards, list) else []:
        if isinstance(card, dict) and card.get("dbfId") is not None:
            result[int(card["dbfId"])] = card
    return result


def hsj_format_fallback_cards(
    format_slug: str,
    hsj_ru: dict[int, dict[str, Any]],
    hsj_en: dict[int, dict[str, Any]],
    blizzard_dbfs: set[int],
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Return only currently approved HSJSON-only event cards."""
    allowed_ids = TEMPORARY_HSJ_FORMAT_FALLBACKS.get(format_slug, set())
    if not allowed_ids or (today or datetime.now(timezone.utc).date()) >= TEMPORARY_HSJ_FORMAT_FALLBACK_EXPIRES_ON:
        return []
    rows: list[dict[str, Any]] = []
    for dbf, ru in hsj_ru.items():
        card_id = str(ru.get("id") or "")
        if dbf in blizzard_dbfs or card_id not in allowed_ids or ru.get("collectible") is not True:
            continue
        en = hsj_en.get(dbf)
        if not en or str(en.get("id") or "") != card_id:
            continue
        rows.append({"id": card_id, "dbf": dbf, "ru": ru, "en": en})
    return sorted(rows, key=lambda row: str(row["id"]))


def nested_slug(value: Any) -> str | None:
    if isinstance(value, dict):
        return str(value.get("slug") or value.get("name") or "") or None
    if value is None:
        return None
    return str(value)


def nested_id(value: Any) -> int | None:
    if isinstance(value, dict) and value.get("id") is not None:
        return int(value["id"])
    return None


def localized_text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def patch_text(card_id: str, locale: str, value: Any) -> tuple[str | None, dict[str, str] | None]:
    """Correct known just-released text only while the provider is still stale."""
    normalized = localized_text(value)
    override = PATCH_TEXT_OVERRIDES.get(card_id, {}).get(locale)
    if not override or not normalized:
        return normalized, None
    if override["old_marker"].casefold() not in normalized.casefold():
        return normalized, None
    return override["text"], override


def patch_image(card_id: str, value: Any) -> tuple[str | None, dict[str, str] | None]:
    """Replace an exact stale localized render until Blizzard changes its URL."""
    normalized = localized_text(value)
    override = PATCH_IMAGE_OVERRIDES.get(card_id)
    if not override or normalized != override["stale_url"]:
        return normalized, None
    return override["image_url"], override


def normalize_card(dbf: int, ru: dict[str, Any], en: dict[str, Any] | None, hsj_ru: dict[str, Any] | None, hsj_en: dict[str, Any] | None) -> dict[str, Any]:
    card_id = str((hsj_ru or hsj_en or {}).get("id") or f"blizzard:{dbf}")
    text_ru, patch_ru = patch_text(card_id, "ru", ru.get("text") or (hsj_ru or {}).get("text"))
    text_en, patch_en = patch_text(card_id, "en", (en or {}).get("text") or (hsj_en or {}).get("text"))
    fallback_render = (
        f"https://art.hearthstonejson.com/v1/render/latest/ruRU/512x/{urllib.parse.quote(card_id, safe='')}.png"
        if hsj_ru or hsj_en
        else None
    )
    image_url, patch_image_override = patch_image(card_id, ru.get("image") or fallback_render)
    card_set = ru.get("cardSet") if isinstance(ru.get("cardSet"), dict) else {}
    card_type = ru.get("cardType") if isinstance(ru.get("cardType"), dict) else {}
    rarity = ru.get("rarity") if isinstance(ru.get("rarity"), dict) else {}
    card_class = ru.get("class") if isinstance(ru.get("class"), dict) else {}
    minion_type = ru.get("minionType") if isinstance(ru.get("minionType"), dict) else {}
    spell_school = ru.get("spellSchool") if isinstance(ru.get("spellSchool"), dict) else {}
    payload = {
        "blizzard_ru": ru,
        "blizzard_en": en,
        "hsj_ru": hsj_ru,
        "hsj_en": hsj_en,
        "patch_text_override": {key: value for key, value in (("ru", patch_ru), ("en", patch_en)) if value},
        "patch_image_override": patch_image_override,
    }
    return {
        "card_id": card_id,
        "dbf": dbf,
        "slug": ru.get("slug") or (en or {}).get("slug"),
        "collectible": 1 if (ru.get("collectible") is not False and (hsj_ru or {}).get("collectible") is not False) else 0,
        "name_ru": localized_text(ru.get("name") or (hsj_ru or {}).get("name")),
        "name_en": localized_text((en or {}).get("name") or (hsj_en or {}).get("name")),
        "text_ru": text_ru,
        "text_en": text_en,
        "flavor_ru": localized_text(ru.get("flavorText") or (hsj_ru or {}).get("flavor")),
        "flavor_en": localized_text((en or {}).get("flavorText") or (hsj_en or {}).get("flavor")),
        "card_set": nested_slug(card_set) or (hsj_ru or hsj_en or {}).get("set"),
        "card_set_id": nested_id(card_set) or ru.get("cardSetId"),
        "card_type": nested_slug(card_type) or (hsj_ru or hsj_en or {}).get("type"),
        "card_type_id": nested_id(card_type) or ru.get("cardTypeId"),
        "rarity": nested_slug(rarity) or (hsj_ru or hsj_en or {}).get("rarity"),
        "rarity_id": nested_id(rarity) or ru.get("rarityId"),
        "class_slug": nested_slug(card_class) or (hsj_ru or hsj_en or {}).get("cardClass"),
        "class_id": nested_id(card_class) or ru.get("classId"),
        "multi_class_json": ru.get("multiClassIds") or (hsj_ru or hsj_en or {}).get("classes") or [],
        "minion_type": nested_slug(minion_type) or (hsj_ru or hsj_en or {}).get("race"),
        "minion_type_id": nested_id(minion_type) or ru.get("minionTypeId"),
        "spell_school": nested_slug(spell_school) or (hsj_ru or hsj_en or {}).get("spellSchool"),
        "spell_school_id": nested_id(spell_school) or ru.get("spellSchoolId"),
        "mana_cost": ru.get("manaCost") if ru.get("manaCost") is not None else (hsj_ru or hsj_en or {}).get("cost"),
        "attack": ru.get("attack") if ru.get("attack") is not None else (hsj_ru or hsj_en or {}).get("attack"),
        "health": ru.get("health") if ru.get("health") is not None else (hsj_ru or hsj_en or {}).get("health"),
        "durability": ru.get("durability") or (hsj_ru or hsj_en or {}).get("durability"),
        "armor": ru.get("armor") or (hsj_ru or hsj_en or {}).get("armor"),
        "artist": ru.get("artistName") or (hsj_ru or hsj_en or {}).get("artist"),
        "image_url": image_url,
        "image_gold_url": ru.get("imageGold"),
        "crop_image_url": ru.get("cropImage"),
        "mechanics_json": (hsj_ru or hsj_en or {}).get("mechanics") or [],
        "referenced_tags_json": (hsj_ru or hsj_en or {}).get("referencedTags") or [],
        "keyword_ids_json": ru.get("keywordIds") or [],
        "wiki_page_title": (hsj_en or hsj_ru or {}).get("name"),
        "wiki_page_url": "https://hearthstone.wiki.gg/wiki/" + urllib.parse.quote(str((hsj_en or hsj_ru or {}).get("name") or "").replace(" ", "_"), safe="/()_',.!:") if (hsj_en or hsj_ru or {}).get("name") else None,
        "source_payload": payload,
        "source_hash": stable_hash(payload),
    }


def ensure_schema() -> None:
    subprocess.check_call(["php", str(APP_ROOT / "scripts" / "ensure_constructed_schema.php")])


def current_hash(conn, card_id: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT source_hash FROM constructed_cards WHERE card_id = %s", (card_id,))
        row = cur.fetchone()
    return row["source_hash"] if row else None


def existing_card_id_by_dbf(conn, dbf: int) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT card_id FROM constructed_cards WHERE dbf = %s", (dbf,))
        row = cur.fetchone()
    return str(row["card_id"]) if row else None


def migrate_fallback_card_id(conn, dbf: int, old_card_id: str, new_card_id: str, dry_run: bool) -> bool:
    if old_card_id == new_card_id:
        return False
    if not old_card_id.startswith("blizzard:") or new_card_id.startswith("blizzard:"):
        raise RuntimeError(
            f"Refusing conflicting card_id change for dbf {dbf}: {old_card_id!r} -> {new_card_id!r}"
        )
    if dry_run:
        return True

    with conn.cursor() as cur:
        cur.execute("SELECT dbf FROM constructed_cards WHERE card_id = %s", (new_card_id,))
        target = cur.fetchone()
        if target:
            raise RuntimeError(
                f"Cannot migrate dbf {dbf} to {new_card_id!r}: target card_id already belongs to dbf {target['dbf']}"
            )

        cur.execute(
            "SELECT base_card_id FROM constructed_diamond_cards WHERE base_card_id IN (%s, %s)",
            (old_card_id, new_card_id),
        )
        diamond_card_ids = {str(row["base_card_id"]) for row in cur.fetchall()}

        cur.execute(
            "UPDATE constructed_cards SET card_id = %s WHERE card_id = %s AND dbf = %s",
            (new_card_id, old_card_id, dbf),
        )
        if cur.rowcount != 1:
            raise RuntimeError(
                f"Failed to migrate fallback card_id for dbf {dbf}: {old_card_id!r} -> {new_card_id!r}"
            )

        # Format and wiki rows follow through ON UPDATE CASCADE. Diamond rows
        # have no foreign key. Their sync may already have created the canonical
        # target, so retain it and remove only a stale fallback duplicate.
        if old_card_id in diamond_card_ids:
            if new_card_id in diamond_card_ids:
                cur.execute("DELETE FROM constructed_diamond_cards WHERE base_card_id = %s", (old_card_id,))
            else:
                cur.execute(
                    "UPDATE constructed_diamond_cards SET base_card_id = %s WHERE base_card_id = %s",
                    (new_card_id, old_card_id),
                )
    return True


def save_card(conn, card: dict[str, Any], dry_run: bool) -> str:
    changed = current_hash(conn, card["card_id"]) != card["source_hash"]
    if dry_run:
        return "changed" if changed else "unchanged"
    now = utc_now()
    params = {
        **{k: v for k, v in card.items() if k != "source_payload"},
        "source": SOURCE,
        "source_payload_json": json_dump(card["source_payload"]),
        "first_seen_at": now,
        "last_seen_at": now,
        "changed_at": now,
        "multi_class_json": json_dump(card["multi_class_json"]),
        "mechanics_json": json_dump(card["mechanics_json"]),
        "referenced_tags_json": json_dump(card["referenced_tags_json"]),
        "keyword_ids_json": json_dump(card["keyword_ids_json"]),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO constructed_cards (
                card_id, dbf, slug, collectible, name_ru, name_en, text_ru, text_en, flavor_ru, flavor_en,
                card_set, card_set_id, card_type, card_type_id, rarity, rarity_id, class_slug, class_id,
                multi_class_json, minion_type, minion_type_id, spell_school, spell_school_id,
                mana_cost, attack, health, durability, armor, artist, image_url, image_gold_url,
                crop_image_url, mechanics_json, referenced_tags_json, keyword_ids_json, wiki_page_title,
                wiki_page_url, source, source_payload_json, source_hash, first_seen_at, last_seen_at, changed_at
            ) VALUES (
                %(card_id)s, %(dbf)s, %(slug)s, %(collectible)s, %(name_ru)s, %(name_en)s, %(text_ru)s, %(text_en)s, %(flavor_ru)s, %(flavor_en)s,
                %(card_set)s, %(card_set_id)s, %(card_type)s, %(card_type_id)s, %(rarity)s, %(rarity_id)s, %(class_slug)s, %(class_id)s,
                %(multi_class_json)s, %(minion_type)s, %(minion_type_id)s, %(spell_school)s, %(spell_school_id)s,
                %(mana_cost)s, %(attack)s, %(health)s, %(durability)s, %(armor)s, %(artist)s, %(image_url)s, %(image_gold_url)s,
                %(crop_image_url)s, %(mechanics_json)s, %(referenced_tags_json)s, %(keyword_ids_json)s, %(wiki_page_title)s,
                %(wiki_page_url)s, %(source)s, %(source_payload_json)s, %(source_hash)s, %(first_seen_at)s, %(last_seen_at)s, %(changed_at)s
            )
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf), slug = VALUES(slug), collectible = VALUES(collectible),
                name_ru = VALUES(name_ru), name_en = VALUES(name_en), text_ru = VALUES(text_ru), text_en = VALUES(text_en),
                flavor_ru = VALUES(flavor_ru), flavor_en = VALUES(flavor_en), card_set = VALUES(card_set),
                card_set_id = VALUES(card_set_id), card_type = VALUES(card_type), card_type_id = VALUES(card_type_id),
                rarity = VALUES(rarity), rarity_id = VALUES(rarity_id), class_slug = VALUES(class_slug),
                class_id = VALUES(class_id), multi_class_json = VALUES(multi_class_json), minion_type = VALUES(minion_type),
                minion_type_id = VALUES(minion_type_id), spell_school = VALUES(spell_school), spell_school_id = VALUES(spell_school_id),
                mana_cost = VALUES(mana_cost), attack = VALUES(attack), health = VALUES(health), durability = VALUES(durability),
                armor = VALUES(armor), artist = VALUES(artist), image_url = VALUES(image_url),
                image_gold_url = COALESCE(NULLIF(VALUES(image_gold_url), ''), image_gold_url),
                crop_image_url = COALESCE(NULLIF(VALUES(crop_image_url), ''), crop_image_url),
                mechanics_json = VALUES(mechanics_json), referenced_tags_json = VALUES(referenced_tags_json),
                keyword_ids_json = VALUES(keyword_ids_json), wiki_page_title = VALUES(wiki_page_title),
                wiki_page_url = VALUES(wiki_page_url), source = VALUES(source),
                source_payload_json = VALUES(source_payload_json), last_seen_at = VALUES(last_seen_at),
                changed_at = IF(constructed_cards.source_hash <> VALUES(source_hash) OR constructed_cards.source_hash IS NULL, VALUES(changed_at), changed_at),
                source_hash = VALUES(source_hash)
            """,
            params,
        )
    return "changed" if changed else "unchanged"


def save_format(conn, format_slug: str, card: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        return
    payload = {"format": format_slug, "card_id": card["card_id"], "dbf": card["dbf"], "source": SOURCE}
    now = utc_now()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO constructed_format_cards (
                format_slug, card_id, dbf, in_format, availability_status, source,
                source_payload_json, source_hash, first_seen_at, last_seen_at, changed_at
            ) VALUES (%s, %s, %s, 1, 'available', %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                dbf = VALUES(dbf), in_format = 1, availability_status = 'available',
                source = VALUES(source), source_payload_json = VALUES(source_payload_json),
                last_seen_at = VALUES(last_seen_at),
                changed_at = IF(constructed_format_cards.in_format <> 1 OR constructed_format_cards.availability_status <> 'available', VALUES(changed_at), changed_at),
                source_hash = VALUES(source_hash)
            """,
            (format_slug, card["card_id"], card["dbf"], SOURCE, json_dump(payload), stable_hash(payload), now, now, now),
        )


def mark_removed(conn, format_slug: str, active_card_ids: set[str], dry_run: bool) -> int:
    if dry_run:
        return 0
    if not active_card_ids:
        return 0
    placeholders = ",".join(["%s"] * len(active_card_ids))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE constructed_format_cards
            SET in_format = 0, availability_status = 'removed', changed_at = CURRENT_TIMESTAMP
            WHERE format_slug = %s AND in_format = 1 AND card_id NOT IN ({placeholders})
            """,
            (format_slug, *sorted(active_card_ids)),
        )
        return int(cur.rowcount)


def start_run(conn, format_slug: str) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO constructed_import_runs (job, format_slug, source) VALUES ('cards', %s, %s)", (format_slug, SOURCE))
        return int(cur.lastrowid)


def finish_run(conn, run_id: int, stats: dict[str, int], status: str = "ok", error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE constructed_import_runs
            SET finished_at = CURRENT_TIMESTAMP, status = %s, scanned = %s, inserted = %s, updated = %s, changed = %s, error = %s
            WHERE id = %s
            """,
            (status, stats.get("scanned", 0), stats.get("inserted", 0), stats.get("updated", 0), stats.get("changed", 0), error, run_id),
        )


def sync_format(conn, format_slug: str, region: str, token: str, hsj_ru: dict[int, dict[str, Any]], hsj_en: dict[int, dict[str, Any]], dry_run: bool) -> dict[str, int]:
    ru_cards = fetch_blizzard_cards(format_slug, "ru_RU", region, token)
    en_cards = fetch_blizzard_cards(format_slug, "en_US", region, token)
    stats = {"scanned": 0, "inserted": 0, "updated": 0, "changed": 0, "removed": 0, "renamed": 0}
    active_card_ids: set[str] = set()
    for dbf, ru in sorted(ru_cards.items()):
        card = normalize_card(dbf, ru, en_cards.get(dbf), hsj_ru.get(dbf), hsj_en.get(dbf))
        existing_card_id = existing_card_id_by_dbf(conn, dbf)
        if existing_card_id and migrate_fallback_card_id(
            conn, dbf, existing_card_id, card["card_id"], dry_run
        ):
            stats["renamed"] += 1
        exists = existing_card_id is not None or current_hash(conn, card["card_id"]) is not None
        outcome = save_card(conn, card, dry_run)
        save_format(conn, format_slug, card, dry_run)
        active_card_ids.add(card["card_id"])
        stats["scanned"] += 1
        stats["inserted" if not exists else "updated"] += 1
        if outcome == "changed":
            stats["changed"] += 1
    for fallback in hsj_format_fallback_cards(format_slug, hsj_ru, hsj_en, set(ru_cards)):
        dbf = int(fallback["dbf"])
        card = normalize_card(dbf, fallback["ru"], fallback["en"], fallback["ru"], fallback["en"])
        existing_card_id = existing_card_id_by_dbf(conn, dbf)
        exists = existing_card_id is not None or current_hash(conn, card["card_id"]) is not None
        outcome = save_card(conn, card, dry_run)
        save_format(conn, format_slug, card, dry_run)
        active_card_ids.add(card["card_id"])
        stats["scanned"] += 1
        stats["inserted" if not exists else "updated"] += 1
        if outcome == "changed":
            stats["changed"] += 1
    stats["removed"] = mark_removed(conn, format_slug, active_card_ids, dry_run)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync constructed Standard/Wild cards.")
    parser.add_argument("--format", choices=["all", "standard", "wild"], default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ensure_schema()
    region, token = blizzard_token()
    hsj_ru = fetch_hsj(HSJ_RU_URL)
    hsj_en = fetch_hsj(HSJ_EN_URL)
    formats = ["standard", "wild"] if args.format == "all" else [args.format]
    conn = connect_db(load_php_config())
    try:
        all_stats = {}
        for format_slug in formats:
            run_id = start_run(conn, format_slug)
            try:
                stats = sync_format(conn, format_slug, region, token, hsj_ru, hsj_en, args.dry_run)
                finish_run(conn, run_id, stats, "ok")
                all_stats[format_slug] = stats
                print(json_dump({format_slug: stats}))
            except Exception as exc:
                finish_run(conn, run_id, {"scanned": 0, "inserted": 0, "updated": 0, "changed": 0}, "error", str(exc))
                raise
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        print(json_dump({"done": all_stats, "dry_run": args.dry_run}))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
