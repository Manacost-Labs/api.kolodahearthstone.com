#!/opt/wiki-hs-parser/.venv/bin/python
"""Fill newly released Battlegrounds heroes before their wiki pages are indexed."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from typing import Any

import sync_wiki_heroes as heroes


HSJ_URLS = {
    "ru": "https://api.hearthstonejson.com/v1/latest/ruRU/cards.json",
    "en": "https://api.hearthstonejson.com/v1/latest/enUS/cards.json",
}
SOURCE = "hearthstonejson.com"


def load_cards(url: str) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    request = urllib.request.Request(url, headers={"User-Agent": "db.kolodahs.ru-hero-sync/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        cards = json.load(response)
    by_dbf = {int(card["dbfId"]): card for card in cards if card.get("dbfId") is not None}
    by_id = {str(card["id"]): card for card in cards if card.get("id")}
    return by_dbf, by_id


def existing_images(conn) -> dict[str, str | None]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT card_id, hero_image_url FROM battlegrounds_heroes")
        return {str(row["card_id"]): row.get("hero_image_url") for row in cursor.fetchall()}


def should_sync_existing_hero(image_url: str | None) -> bool:
    """Refresh only rows still using the release-window HSJSON render."""
    return bool(image_url and "art.hearthstonejson.com" in image_url)


def hero_image_candidates(card_id: str) -> list[str]:
    encoded = urllib.parse.quote(card_id, safe="")
    return [
        f"{heroes.HSJ_BGS_RENDER_BASE}{encoded}.png",
        f"https://hearthstone.wiki.gg/wiki/Special:Redirect/file/{encoded}.png",
        f"{heroes.HSJ_ORIG_BASE}{encoded}.png",
    ]


def resolve_hero_image_url(card_id: str) -> str:
    """Resolve the first real image, skipping release-window 404 renders."""
    last_error: Exception | None = None
    for candidate in hero_image_candidates(card_id):
        request = urllib.request.Request(
            candidate,
            headers={"User-Agent": "db.kolodahs.ru-hero-sync/1.0", "Accept": "image/*"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                if not content_type.startswith("image/"):
                    raise RuntimeError(f"Unexpected hero image content type: {content_type or 'unknown'}")
                return response.geturl()
        except Exception as error:
            last_error = error
    raise RuntimeError(f"No hero image is available for {card_id}: {last_error}")


def related_card(
    dbf: int | None,
    ru_by_dbf: dict[int, dict[str, Any]],
    en_by_dbf: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    if dbf is None:
        return None
    ru_card = ru_by_dbf.get(int(dbf))
    en_card = en_by_dbf.get(int(dbf))
    compact = heroes.compact_hsj_card(ru_card or en_card, ru_by_dbf)
    if not compact:
        return None
    compact["name_en"] = (en_card or {}).get("name")
    compact["text_en"] = (en_card or {}).get("text")
    return compact


def hero_payload(
    ru_card: dict[str, Any],
    en_card: dict[str, Any],
    ru_by_dbf: dict[int, dict[str, Any]],
    en_by_dbf: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    card_id = str(en_card["id"])
    power_dbf = ru_card.get("heroPowerDbfId") or en_card.get("heroPowerDbfId")
    return {
        "source": SOURCE,
        "card_id": card_id,
        "dbf": int(en_card["dbfId"]),
        "hero_id": None,
        "name_en": en_card.get("name") or card_id,
        "name_ru": ru_card.get("name") or en_card.get("name") or card_id,
        "health": ru_card.get("health") or en_card.get("health"),
        "armor": ru_card.get("armor") if ru_card.get("armor") is not None else en_card.get("armor"),
        "duos_armor": ru_card.get("armorDuos") if ru_card.get("armorDuos") is not None else en_card.get("armorDuos"),
        "armor_text": None,
        "artist": ru_card.get("artist") or en_card.get("artist"),
        "race": ru_card.get("race") or en_card.get("race"),
        "character_name": None,
        "as_hero": None,
        "hero_description": ru_card.get("flavor") or en_card.get("flavor"),
        "hero_image_url": resolve_hero_image_url(card_id),
        "hero_full_art_url": f"{heroes.HSJ_ORIG_BASE}{urllib.parse.quote(card_id, safe='')}.png",
        "wiki_page_title": None,
        "wiki_page_url": None,
        "hero_power_dbf": int(power_dbf) if power_dbf is not None else None,
        "hero_power": related_card(power_dbf, ru_by_dbf, en_by_dbf),
        "buddy_dbf": None,
        "buddy": None,
        "availability": {"notes": ["Доступен в актуальных игровых данных."], "formats": [], "exclusions": []},
        "hero_skins": [],
        "gallery": [],
        "card_changes": [],
        "external_links": [],
        "related_cards": [],
        "source_result": {"hsj_ru": ru_card, "hsj_en": en_card},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Battlegrounds heroes missing from the wiki index.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ru_by_dbf, ru_by_id = load_cards(HSJ_URLS["ru"])
    en_by_dbf, en_by_id = load_cards(HSJ_URLS["en"])
    candidates = [
        card for card in en_by_id.values()
        if card.get("battlegroundsHero") is True and card.get("type") == "HERO"
    ]
    conn = heroes.connect_db(heroes.load_php_config())
    stats = {"candidates": len(candidates), "missing": 0, "refreshed": 0, "changed": 0, "unchanged": 0}
    try:
        heroes.ensure_schema(conn)
        known = existing_images(conn)
        for en_card in sorted(candidates, key=lambda card: str(card.get("id") or "")):
            card_id = str(en_card["id"])
            existing_image = known.get(card_id)
            if card_id in known and not should_sync_existing_hero(existing_image):
                continue
            if card_id in known:
                stats["refreshed"] += 1
            else:
                stats["missing"] += 1
            ru_card = ru_by_id.get(card_id, en_card)
            outcome = heroes.save_payload(
                conn,
                hero_payload(ru_card, en_card, ru_by_dbf, en_by_dbf),
                args.dry_run,
            )
            stats[outcome] += 1
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
