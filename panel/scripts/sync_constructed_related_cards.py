#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor


APP_ROOT = Path(__file__).resolve().parents[1]
HSJ_RU_URL = "https://api.hearthstonejson.com/v1/latest/ruRU/cards.json"
HSJ_EN_URL = "https://api.hearthstonejson.com/v1/latest/enUS/cards.json"
ART_BASE = "https://art.hearthstonejson.com/v1/512x"
UPLOAD_DIR = APP_ROOT / "uploads" / "constructed-related"
UPLOAD_URL = "/uploads/constructed-related"
ART_UPLOAD_DIR = APP_ROOT / "uploads" / "constructed-related-art"
ART_UPLOAD_URL = "/uploads/constructed-related-art"
SOURCE = "hearthstonejson_related"
BLIZZARD_CLIENT_ID = os.environ.get("BLIZZARD_CLIENT_ID", "").strip()
BLIZZARD_CLIENT_SECRET = os.environ.get("BLIZZARD_CLIENT_SECRET", "").strip()
BLIZZARD_REGION = os.environ.get("BLIZZARD_REGION", "eu").strip().lower()
BLIZZARD_LOCALE = os.environ.get("BLIZZARD_LOCALE", "ru_RU").strip()
BLIZZARD_API_ORIGIN = (
    f"https://{BLIZZARD_REGION}.api.blizzard.com"
    if re.fullmatch(r"[a-z]{2}", BLIZZARD_REGION)
    else "https://eu.api.blizzard.com"
)
_BLIZZARD_TOKEN: str | None = None
_BLIZZARD_IMAGE_CACHE: dict[int, str | None] = {}


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


def http_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "db.kolodahs.ru-related-cards-sync/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def fetch_hsj(url: str) -> dict[str, dict[str, Any]]:
    payload = http_json(url)
    if not isinstance(payload, list):
        return {}
    return {
        str(card["id"]): card
        for card in payload
        if isinstance(card, dict) and card.get("id")
    }


def synthetic_hsj_card(
    card_id: str,
    relation: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    jade_match = re.fullmatch(r"CFM_712_t(\d{2})", card_id)
    if not jade_match:
        return None
    stats = int(jade_match.group(1))
    template = catalog.get("CFM_712_t01")
    if not template:
        return None
    card = dict(template)
    card.update(
        {
            "id": card_id,
            "dbfId": None,
            "collectible": False,
            "cost": min(stats, 10),
            "attack": stats,
            "health": stats,
        }
    )
    title = str(relation.get("title") or "").strip()
    if title and not str(card.get("name") or "").strip():
        card["name"] = title
    return card


def json_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return decoded if isinstance(decoded, list) else []


def load_related_ids(
    conn,
    format_slug: str,
    parent_card_id: str | None = None,
) -> tuple[set[str], int]:
    parent_filter = ""
    params: list[str] = [format_slug]
    if parent_card_id:
        parent_filter = "AND wm.card_id = %s"
        params.append(parent_card_id)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT DISTINCT wm.card_id, wm.related_card_ids_json
            FROM constructed_card_wiki_meta wm
            INNER JOIN constructed_format_cards f ON f.card_id = wm.card_id
            WHERE f.format_slug = %s
              AND f.in_format = 1
              AND wm.status = 'ok'
              {parent_filter}
            """,
            params,
        )
        rows = cursor.fetchall()

    related_ids: set[str] = set()
    for row in rows:
        related_ids.update(
            str(card_id).strip()
            for card_id in json_list(row.get("related_card_ids_json"))
            if str(card_id).strip()
        )
    return related_ids, len(rows)


def load_related_sources(
    conn,
    format_slug: str,
    parent_card_id: str | None = None,
) -> tuple[dict[str, dict[str, Any]], int]:
    parent_filter = ""
    params: list[str] = [format_slug]
    if parent_card_id:
        parent_filter = "AND wm.card_id = %s"
        params.append(parent_card_id)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT DISTINCT
                wm.card_id,
                wm.related_card_ids_json,
                wm.related_cards_json
            FROM constructed_card_wiki_meta wm
            INNER JOIN constructed_format_cards f ON f.card_id = wm.card_id
            WHERE f.format_slug = %s
              AND f.in_format = 1
              AND wm.status = 'ok'
              {parent_filter}
            """,
            params,
        )
        rows = cursor.fetchall()

    related: dict[str, dict[str, Any]] = {}
    for row in rows:
        for related_card_id in json_list(row.get("related_card_ids_json")):
            related_card_id = str(related_card_id or "").strip()
            if related_card_id:
                related.setdefault(related_card_id, {})
        for group in json_list(row.get("related_cards_json")):
            for item in group.get("cards", []) if isinstance(group, dict) else []:
                if not isinstance(item, dict):
                    continue
                card_id = str(item.get("card_id") or "").strip()
                if not card_id:
                    continue
                current = related.setdefault(card_id, {})
                for key in ("title", "url", "image_url", "image_alt", "caption"):
                    value = item.get(key)
                    if value not in (None, "") and current.get(key) in (None, ""):
                        current[key] = value
    return related, len(rows)


def safe_filename(card_id: str, extension: str = "png") -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", card_id).strip("._")
    if not safe:
        raise ValueError(f"Unsafe empty filename for card_id {card_id!r}")
    return safe + "." + extension


def safe_chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except PermissionError:
        # Existing deploy/test artifacts may be owned by another service user.
        # Readable files are still valid and must not abort metadata imports.
        pass


def blizzard_access_token() -> str | None:
    global _BLIZZARD_TOKEN
    if _BLIZZARD_TOKEN:
        return _BLIZZARD_TOKEN
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        return None
    credentials = base64.b64encode(
        f"{BLIZZARD_CLIENT_ID}:{BLIZZARD_CLIENT_SECRET}".encode("utf-8")
    ).decode("ascii")
    request = urllib.request.Request(
        "https://oauth.battle.net/token",
        data=b"grant_type=client_credentials",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "db.kolodahs.ru-related-cards-sync/2.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    token = str(payload.get("access_token") or "").strip()
    _BLIZZARD_TOKEN = token or None
    return _BLIZZARD_TOKEN


def blizzard_image_url(dbf: int | None) -> str | None:
    if dbf is None:
        return None
    dbf = int(dbf)
    if dbf in _BLIZZARD_IMAGE_CACHE:
        return _BLIZZARD_IMAGE_CACHE[dbf]
    token = blizzard_access_token()
    if not token:
        _BLIZZARD_IMAGE_CACHE[dbf] = None
        return None
    query = urllib.parse.urlencode({"locale": BLIZZARD_LOCALE})
    request = urllib.request.Request(
        f"{BLIZZARD_API_ORIGIN}/hearthstone/cards/{dbf}?{query}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "db.kolodahs.ru-related-cards-sync/2.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as exc:
        if exc.code == 401:
            global _BLIZZARD_TOKEN
            _BLIZZARD_TOKEN = None
        if exc.code in {401, 404}:
            _BLIZZARD_IMAGE_CACHE[dbf] = None
            return None
        raise
    image_url = str(payload.get("image") or "").strip() or None
    _BLIZZARD_IMAGE_CACHE[dbf] = image_url
    return image_url


def download_render(
    card_id: str,
    dbf: int | None,
    wiki_image_url: str | None,
    dry_run: bool,
    skip_images: bool,
    refresh_images: bool,
) -> tuple[str | None, str | None, bool, str, str | None]:
    filename = safe_filename(card_id)
    local_url = f"{UPLOAD_URL}/{filename}"
    destination = UPLOAD_DIR / filename
    if not dry_run:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_chmod(UPLOAD_DIR, 0o755)
        if destination.exists():
            safe_chmod(destination, 0o644)
    if skip_images:
        return (
            wiki_image_url,
            local_url if destination.exists() else None,
            False,
            "existing",
            None,
        )
    if destination.exists() and not refresh_images:
        return None, local_url, False, "existing", None
    if dry_run:
        return wiki_image_url, local_url, True, "dry_run", None

    errors: list[str] = []
    sources: list[tuple[str, str]] = []
    try:
        official_url = blizzard_image_url(dbf)
        if official_url:
            sources.append(("blizzard", official_url))
    except Exception as exc:
        errors.append(f"blizzard lookup: {type(exc).__name__}: {exc}")
    if wiki_image_url and all(url != wiki_image_url for _, url in sources):
        sources.append(("wiki", wiki_image_url))

    for source, remote_url in sources:
        request = urllib.request.Request(
            remote_url,
            headers={
                "User-Agent": "db.kolodahs.ru-related-cards-sync/2.0",
                "Accept": "image/png,image/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                content = response.read()
            if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("unexpected non-PNG payload")
            temporary = destination.with_suffix(".png.tmp")
            temporary.write_bytes(content)
            safe_chmod(temporary, 0o644)
            temporary.replace(destination)
            safe_chmod(destination, 0o644)
            return remote_url, local_url, True, source, None
        except Exception as exc:
            errors.append(f"{source} download: {type(exc).__name__}: {exc}")

    return (
        wiki_image_url,
        local_url if destination.exists() else None,
        False,
        "missing",
        "; ".join(errors) or "no image source",
    )


def download_art(
    card_id: str, dry_run: bool, skip_images: bool
) -> tuple[str | None, str | None, bool, bool]:
    filename = safe_filename(card_id, "jpg")
    local_url = f"{ART_UPLOAD_URL}/{filename}"
    remote_url = f"{ART_BASE}/{urllib.parse.quote(card_id, safe='')}.jpg"
    destination = ART_UPLOAD_DIR / filename
    if not dry_run:
        ART_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        safe_chmod(ART_UPLOAD_DIR, 0o755)
        if destination.exists():
            safe_chmod(destination, 0o644)
    if skip_images or destination.exists():
        return remote_url, local_url, False, False
    if dry_run:
        return remote_url, local_url, True, False

    request = urllib.request.Request(
        remote_url,
        headers={
            "User-Agent": "db.kolodahs.ru-related-cards-sync/1.0",
            "Accept": "image/jpeg,image/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            content = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None, None, False, True
        raise
    if not content.startswith(b"\xff\xd8"):
        raise RuntimeError(f"Unexpected full-art payload for {card_id}")
    temporary = destination.with_suffix(".jpg.tmp")
    temporary.write_bytes(content)
    safe_chmod(temporary, 0o644)
    temporary.replace(destination)
    safe_chmod(destination, 0o644)
    return remote_url, local_url, True, False


def normalize(
    card_id: str,
    ru: dict[str, Any],
    en: dict[str, Any] | None,
    image_url: str | None,
    local_image_url: str | None,
    art_url: str | None,
    local_art_url: str | None,
) -> dict[str, Any]:
    en = en or {}
    source_payload = {"ruRU": ru, "enUS": en or None}
    races = ru.get("races") if isinstance(ru.get("races"), list) else []
    minion_type = ru.get("race") or (races[0] if races else None)
    return {
        "card_id": card_id,
        "dbf": ru.get("dbfId") or en.get("dbfId"),
        "collectible": int(bool(ru.get("collectible") or en.get("collectible"))),
        "name_ru": ru.get("name"),
        "name_en": en.get("name"),
        "text_ru": ru.get("text"),
        "text_en": en.get("text"),
        "flavor_ru": ru.get("flavor"),
        "flavor_en": en.get("flavor"),
        "card_set": ru.get("set") or en.get("set"),
        "card_type": ru.get("type") or en.get("type"),
        "rarity": ru.get("rarity") or en.get("rarity"),
        "class_slug": ru.get("cardClass") or en.get("cardClass"),
        "multi_class_json": ru.get("classes") or en.get("classes") or [],
        "minion_type": minion_type,
        "spell_school": ru.get("spellSchool") or en.get("spellSchool"),
        "mana_cost": ru.get("cost") if ru.get("cost") is not None else en.get("cost"),
        "attack": ru.get("attack") if ru.get("attack") is not None else en.get("attack"),
        "health": ru.get("health") if ru.get("health") is not None else en.get("health"),
        "durability": ru.get("durability") if ru.get("durability") is not None else en.get("durability"),
        "armor": ru.get("armor") if ru.get("armor") is not None else en.get("armor"),
        "artist": ru.get("artist") or en.get("artist"),
        "image_url": image_url,
        "local_image_url": local_image_url,
        "crop_image_url": art_url,
        "local_crop_image_url": local_art_url,
        "mechanics_json": ru.get("mechanics") or en.get("mechanics") or [],
        "referenced_tags_json": ru.get("referencedTags") or en.get("referencedTags") or [],
        "wiki_page_title": en.get("name") or ru.get("name"),
        "source_payload_json": json_dump(source_payload),
        "source_hash": stable_hash(source_payload),
    }


def card_exists(conn, card_id: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("SELECT 1 FROM constructed_cards WHERE card_id = %s LIMIT 1", (card_id,))
        return cursor.fetchone() is not None


def save_card(conn, card: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        return
    now = utc_now()
    params = {
        **card,
        "multi_class_json": json_dump(card["multi_class_json"]),
        "mechanics_json": json_dump(card["mechanics_json"]),
        "referenced_tags_json": json_dump(card["referenced_tags_json"]),
        "source": SOURCE,
        "now": now,
    }
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO constructed_cards (
                card_id, dbf, collectible, name_ru, name_en, text_ru, text_en, flavor_ru, flavor_en,
                card_set, card_type, rarity, class_slug, multi_class_json, minion_type, spell_school,
                mana_cost, attack, health, durability, armor, artist, image_url, local_image_url,
                crop_image_url, local_crop_image_url, mechanics_json, referenced_tags_json,
                wiki_page_title, source, source_payload_json,
                source_hash, first_seen_at, last_seen_at, changed_at
            ) VALUES (
                %(card_id)s, %(dbf)s, %(collectible)s, %(name_ru)s, %(name_en)s, %(text_ru)s, %(text_en)s,
                %(flavor_ru)s, %(flavor_en)s, %(card_set)s, %(card_type)s, %(rarity)s, %(class_slug)s,
                %(multi_class_json)s, %(minion_type)s, %(spell_school)s, %(mana_cost)s, %(attack)s,
                %(health)s, %(durability)s, %(armor)s, %(artist)s, %(image_url)s, %(local_image_url)s,
                %(crop_image_url)s, %(local_crop_image_url)s, %(mechanics_json)s,
                %(referenced_tags_json)s, %(wiki_page_title)s, %(source)s, %(source_payload_json)s,
                %(source_hash)s, %(now)s, %(now)s, %(now)s
            )
            ON DUPLICATE KEY UPDATE
                name_ru = COALESCE(NULLIF(name_ru, ''), VALUES(name_ru)),
                name_en = COALESCE(NULLIF(name_en, ''), VALUES(name_en)),
                text_ru = COALESCE(NULLIF(text_ru, ''), VALUES(text_ru)),
                text_en = COALESCE(NULLIF(text_en, ''), VALUES(text_en)),
                flavor_ru = COALESCE(NULLIF(flavor_ru, ''), VALUES(flavor_ru)),
                flavor_en = COALESCE(NULLIF(flavor_en, ''), VALUES(flavor_en)),
                card_set = COALESCE(NULLIF(card_set, ''), VALUES(card_set)),
                card_type = COALESCE(NULLIF(card_type, ''), VALUES(card_type)),
                rarity = COALESCE(NULLIF(rarity, ''), VALUES(rarity)),
                class_slug = COALESCE(NULLIF(class_slug, ''), VALUES(class_slug)),
                minion_type = COALESCE(NULLIF(minion_type, ''), VALUES(minion_type)),
                spell_school = COALESCE(NULLIF(spell_school, ''), VALUES(spell_school)),
                mana_cost = COALESCE(mana_cost, VALUES(mana_cost)),
                attack = COALESCE(attack, VALUES(attack)),
                health = COALESCE(health, VALUES(health)),
                durability = COALESCE(durability, VALUES(durability)),
                armor = COALESCE(armor, VALUES(armor)),
                artist = COALESCE(NULLIF(artist, ''), VALUES(artist)),
                image_url = COALESCE(NULLIF(VALUES(image_url), ''), image_url),
                local_image_url = COALESCE(NULLIF(VALUES(local_image_url), ''), local_image_url),
                crop_image_url = COALESCE(NULLIF(VALUES(crop_image_url), ''), crop_image_url),
                local_crop_image_url = COALESCE(NULLIF(VALUES(local_crop_image_url), ''), local_crop_image_url),
                mechanics_json = COALESCE(mechanics_json, VALUES(mechanics_json)),
                referenced_tags_json = COALESCE(referenced_tags_json, VALUES(referenced_tags_json)),
                wiki_page_title = COALESCE(NULLIF(wiki_page_title, ''), VALUES(wiki_page_title)),
                last_seen_at = VALUES(last_seen_at)
            """,
            params,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Import localized companion cards for constructed cards.")
    parser.add_argument("--format", choices=["standard", "wild", "all"], default="all")
    parser.add_argument("--parent-card-id", help="Only import companions of one exact parent card ID.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument(
        "--refresh-images",
        action="store_true",
        help="Replace existing rendered card images with Blizzard/Wiki versions.",
    )
    parser.add_argument("--commit-every", type=int, default=50)
    parser.add_argument(
        "--image-workers",
        type=int,
        default=8,
        help="Parallel downloads; database writes remain sequential.",
    )
    args = parser.parse_args()
    args.parent_card_id = str(args.parent_card_id or "").strip() or None

    subprocess.check_call(["php", str(APP_ROOT / "scripts" / "ensure_constructed_schema.php")])
    conn = connect_db(load_php_config())
    try:
        formats = ["standard", "wild"] if args.format == "all" else [args.format]
        related_sources: dict[str, dict[str, Any]] = {}
        parent_count = 0
        for format_slug in formats:
            format_related_sources, format_parent_count = load_related_sources(
                conn,
                format_slug,
                args.parent_card_id,
            )
            for card_id, source in format_related_sources.items():
                current = related_sources.setdefault(card_id, {})
                for key, value in source.items():
                    if value not in (None, "") and current.get(key) in (None, ""):
                        current[key] = value
            parent_count += format_parent_count

        hsj_ru = fetch_hsj(HSJ_RU_URL)
        hsj_en = fetch_hsj(HSJ_EN_URL)
        stats = {
            "formats": formats,
            "parent_card_id": args.parent_card_id,
            "parents": parent_count,
            "requested": len(related_sources),
            "localized": 0,
            "inserted": 0,
            "existing": 0,
            "images_downloaded": 0,
            "images_blizzard": 0,
            "images_wiki": 0,
            "image_errors": [],
            "arts_downloaded": 0,
            "missing_art": [],
            "missing_hsj": [],
            "failed_cards": [],
        }
        render_results: dict[
            str,
            tuple[str | None, str | None, bool, str, str | None],
        ] = {}
        download_candidates: list[
            tuple[str, int | None, str | None]
        ] = []
        if not args.dry_run and not args.skip_images and args.image_workers > 1:
            for card_id in sorted(related_sources):
                destination = UPLOAD_DIR / safe_filename(card_id)
                if destination.exists() and not args.refresh_images:
                    continue
                relation = related_sources.get(card_id) or {}
                ru = hsj_ru.get(card_id) or synthetic_hsj_card(
                    card_id, relation, hsj_ru
                )
                en = hsj_en.get(card_id) or synthetic_hsj_card(
                    card_id, relation, hsj_en
                )
                if not ru:
                    continue
                dbf_value = ru.get("dbfId") or (en or {}).get("dbfId")
                dbf = int(dbf_value) if dbf_value is not None else None
                download_candidates.append(
                    (
                        card_id,
                        dbf,
                        str(relation.get("image_url") or "").strip() or None,
                    )
                )
            if download_candidates:
                try:
                    blizzard_access_token()
                except Exception:
                    pass
                workers = max(1, min(16, args.image_workers))
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=workers
                ) as executor:
                    future_cards = {
                        executor.submit(
                            download_render,
                            card_id,
                            dbf,
                            wiki_image_url,
                            False,
                            False,
                            args.refresh_images,
                        ): (card_id, wiki_image_url)
                        for card_id, dbf, wiki_image_url in download_candidates
                    }
                    for future in concurrent.futures.as_completed(future_cards):
                        card_id, wiki_image_url = future_cards[future]
                        try:
                            render_results[card_id] = future.result()
                        except Exception as exc:
                            render_results[card_id] = (
                                wiki_image_url,
                                None,
                                False,
                                "missing",
                                f"parallel download: {type(exc).__name__}: {exc}",
                            )
        successful_since_commit = 0
        for card_id in sorted(related_sources):
            relation = related_sources.get(card_id) or {}
            ru = hsj_ru.get(card_id) or synthetic_hsj_card(
                card_id, relation, hsj_ru
            )
            en = hsj_en.get(card_id) or synthetic_hsj_card(
                card_id, relation, hsj_en
            )
            if not ru:
                stats["missing_hsj"].append(card_id)
                continue
            savepoint_active = False
            if not args.dry_run:
                with conn.cursor() as cursor:
                    cursor.execute("SAVEPOINT related_card")
                savepoint_active = True
            try:
                existed = card_exists(conn, card_id)
                dbf_value = ru.get("dbfId") or (en or {}).get("dbfId")
                dbf = int(dbf_value) if dbf_value is not None else None
                render_result = render_results.get(card_id)
                if render_result is None:
                    render_result = download_render(
                        card_id,
                        dbf,
                        str(relation.get("image_url") or "").strip() or None,
                        args.dry_run,
                        args.skip_images,
                        args.refresh_images,
                    )
                (
                    image_url,
                    local_image_url,
                    image_downloaded,
                    image_source,
                    image_error,
                ) = render_result
                # Full artwork is imported separately from the original
                # hearthstone.wiki.gg file. Do not download game-file crops here.
                art_url = local_art_url = None
                art_downloaded = False
                art_missing = False
                card = normalize(
                    card_id, ru, en, image_url, local_image_url, art_url, local_art_url
                )
                save_card(conn, card, args.dry_run)
                if not args.dry_run:
                    with conn.cursor() as cursor:
                        cursor.execute("RELEASE SAVEPOINT related_card")
                    savepoint_active = False
                stats["localized"] += 1
                stats["existing" if existed else "inserted"] += 1
                stats["images_downloaded"] += int(image_downloaded)
                stats["images_blizzard"] += int(
                    image_downloaded and image_source == "blizzard"
                )
                stats["images_wiki"] += int(image_downloaded and image_source == "wiki")
                stats["arts_downloaded"] += int(art_downloaded)
                if image_error:
                    stats["image_errors"].append(
                        {
                            "card_id": card_id,
                            "kind": "card",
                            "error": image_error,
                        }
                    )
                if art_missing:
                    stats["missing_art"].append(card_id)
                successful_since_commit += 1
                if (
                    not args.dry_run
                    and args.commit_every > 0
                    and successful_since_commit >= args.commit_every
                ):
                    conn.commit()
                    successful_since_commit = 0
            except Exception as exc:
                if savepoint_active:
                    try:
                        with conn.cursor() as cursor:
                            cursor.execute("ROLLBACK TO SAVEPOINT related_card")
                            cursor.execute("RELEASE SAVEPOINT related_card")
                    except pymysql.MySQLError:
                        # MariaDB automatically rolls back a deadlocked transaction,
                        # which also removes its savepoints.
                        conn.rollback()
                        successful_since_commit = 0
                stats["failed_cards"].append(
                    {
                        "card_id": card_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        print(
            json_dump(
                {
                    "ok": not stats["failed_cards"],
                    "dry_run": args.dry_run,
                    **stats,
                }
            )
        )
        return 1 if stats["failed_cards"] else 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
