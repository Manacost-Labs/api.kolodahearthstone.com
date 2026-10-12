"""Add missing catalog identities; never infer live pool membership from HSJ."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import date, datetime

from catalog_card_text import clean_text


def payload_json(value):
    def encode(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError("Unsupported catalog value")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=encode)


def provenance(row, evidence):
    row["source_payload_json"] = payload_json(evidence)
    row["source_hash"] = hashlib.sha256(row["source_payload_json"].encode()).hexdigest()
    return row


def form_suffix(card_id):
    return re.sub(r"^HERO_\d+[a-z]+", "", card_id)


def skin_variants(existing, upstream):
    """Share media only for an unambiguous identical named/credited form."""
    from sync_hero_skins import CLASS_BY_ID
    class_names = {slug.upper(): (key, slug, en, ru) for key, (slug, en, ru) in CLASS_BY_ID.items()}
    by_id = {x["id"]: x for x in upstream}
    known = {x["card_id"] for x in existing}
    result, unresolved = [], []
    for card in upstream:
        if card.get("set") != "HERO_SKINS" or card.get("type") != "HERO" or not card.get("collectible") or card["id"] in known:
            continue
        candidates = [row for row in existing
                      if row.get("name_en") == card.get("name")
                      and card.get("artist")
                      and by_id.get(row["card_id"], {}).get("artist") == card["artist"]
                      and form_suffix(row["card_id"]) == form_suffix(card["id"])]
        if len(candidates) != 1 or card.get("cardClass") not in class_names:
            unresolved.append(card["id"])
            continue
        canonical = candidates[0]
        row = copy.deepcopy(canonical)
        for key in ("created_at", "updated_at", "changed_at"):
            row.pop(key, None)
        class_id, slug, name_en, name_ru = class_names[card["cardClass"]]
        row.update(card_id=card["id"], dbf=card["dbfId"], class_id=class_id, class_slug=slug,
                   class_name_en=name_en, class_name_ru=name_ru, health=card.get("health"))
        result.append(provenance(row, {"catalog_variant": {"canonical_card_id": canonical["card_id"],
                                                          "identity": card},
                                       "canonical_source_payload": json.loads(canonical.get("source_payload_json") or "{}"),
                                       "canonical_fetched_at": canonical.get("fetched_at")}))
    return result, unresolved


def bartender_supplements(existing, upstream_ru, upstream_en):
    known = {x["card_id"] for x in existing}
    by_dbf = {x["dbfId"]: x for x in upstream_en}
    ru_by_id = {x["id"]: x for x in upstream_ru}
    result = []
    for card in upstream_en:
        parent = by_dbf.get(card.get("battlegroundsSkinParentId"), {})
        if parent.get("id") != "TB_BaconShopBob" or card["id"] in known:
            continue
        ru = ru_by_id.get(card["id"], {})
        result.append(provenance({"card_id": card["id"], "dbf": card["dbfId"],
            "parent_card_id": parent["id"], "name_en": card["name"], "name_ru": ru.get("name"),
            "image_url": None, "status": "catalog_only", "source": "hearthstonejson.com"},
            {"hsj_en": card, "hsj_ru": ru, "media_verified": False}))
    return result


def archival_bg_bases(existing, upstream_ru, upstream_en):
    """Restore referenced historical bases without putting them in today's pool."""
    known = {row['dbf'] for row in existing}
    by_dbf = {row['dbfId']: row for row in upstream_en}
    ru_by_id = {row['id']: row for row in upstream_ru}
    required = {row.get('base_dbf') for row in existing if row.get('variant_kind') == 'golden'} - known - {None}
    result, unresolved = [], []
    for dbf in sorted(required):
        card = by_dbf.get(dbf, {})
        ru = ru_by_id.get(card.get('id'), {})
        if card.get('type') != 'MINION' or not ru.get('name'):
            unresolved.append(dbf)
            continue
        linked_premiums = {row['dbf'] for row in existing
                           if row.get('variant_kind') == 'golden' and row.get('base_dbf') == dbf}
        premium_dbf = card.get('battlegroundsPremiumDbfId')
        if premium_dbf is None and len(linked_premiums) == 1:
            premium_dbf = next(iter(linked_premiums))
        if premium_dbf not in linked_premiums:
            unresolved.append(dbf)
            continue
        notes = (clean_text(ru.get('text')) or '') + '\nEN: ' + (clean_text(card.get('text')) or '')
        result.append(provenance({'card_id':card['id'], 'dbf':dbf, 'name':ru['name'], 'name_en':card['name'],
            'card_type':'minion', 'variant_kind':'base', 'premium_dbf':premium_dbf, 'tavern_tier':card.get('techLevel'),
            'creature_type':str(card.get('race') or 'all').lower(), 'attack':card.get('attack'),
            'health':card.get('health'), 'in_pool':False, 'duos_only':False,
            'notes':notes, 'source':'hearthstonejson', 'card_image':None, 'art_image':None},
            {'catalog_supplement':True, 'hsj_en':card, 'hsj_ru':ru, 'pool_membership_verified':False}))
    return result, unresolved


def parent_hero(parent_id, hero_ids):
    if parent_id in hero_ids:
        return parent_id
    # Transformed heroes have their own dbf, but their skin still belongs to
    # the playable hero. A digit boundary prevents HERO_59 matching HERO_590.
    candidates = [x for x in hero_ids if parent_id.startswith(x)
                  and re.fullmatch(r"t\d*", parent_id[len(x):])]
    return max(candidates, key=len) if candidates else None


def bg_skin_variants(existing, heroes, upstream, image_lookup):
    by_dbf = {x["dbfId"]: x for x in upstream}
    known = {x["skin_card_id"] for x in existing}
    hero_ids = {x["card_id"] for x in heroes}
    result, unresolved = [], []
    for skin in upstream:
        if not skin.get("battlegroundsSkinParentId") or skin["id"] in known:
            continue
        parent = by_dbf.get(skin["battlegroundsSkinParentId"], {})
        canonical = parent_hero(parent.get("id", ""), hero_ids)
        if not canonical:
            unresolved.append(skin["id"])
            continue
        result.append({"hero_card_id": canonical, "skin_card_id": skin["id"], "title": skin["name"],
                       "url": None, "image_url": image_lookup(skin["id"])})
    return result, unresolved


def library_supplements(existing, upstream_ru, upstream_en):
    types = {"BATTLEGROUND_ANOMALY": "anomaly", "BATTLEGROUND_TRINKET": "trinket",
             "BATTLEGROUND_QUEST_REWARD": "reward"}
    known = {(x["library"], x["card_id"]) for x in existing}
    ru_by_id = {x["id"]: x for x in upstream_ru}
    result = []
    for card in upstream_en:
        library = types.get(card.get("type"))
        if not library or (library, card["id"]) in known:
            continue
        ru = ru_by_id.get(card["id"], {})
        if not ru.get("name"):
            continue
        row = {"library": library, "card_id": card["id"], "dbf": card["dbfId"],
               "name_en": card["name"], "name_ru": ru["name"], "text_en": clean_text(card.get("text")),
               "text_ru": clean_text(ru.get("text")), "card_type": card["type"],
               "mana_cost": card.get("cost"), "artist": card.get("artist"),
               "in_pool": False, "pool_status": "unknown", "source": "hearthstonejson.com"}
        result.append(provenance(row, {"catalog_supplement": True, "hsj_en": card, "hsj_ru": ru,
                                       "pool_membership_verified": False}))
    return result


def timewarped_supplements(existing, upstream_ru, upstream_en):
    known = {x["card_id"] for x in existing}
    ru_by_id = {x["id"]: x for x in upstream_ru}
    result = []
    for card in upstream_en:
        if (card["id"] in known or not card["id"].startswith("BG34_")
                or not card.get("name", "").startswith("Timewarped ")
                or card.get("type") not in ("MINION", "BATTLEGROUND_SPELL")
                or card.get("battlegroundsNormalDbfId")):
            continue
        ru = ru_by_id.get(card["id"], {})
        if not ru.get("name"):
            continue
        row = {"card_id": card["id"], "dbf": card["dbfId"], "name_en": card["name"],
               "name_ru": ru["name"], "text_en": clean_text(card.get("text")),
               "text_ru": clean_text(ru.get("text")), "card_type": card["type"],
               "mana_cost": card.get("cost"), "attack": card.get("attack"),
               "health": card.get("health"), "tavern_tier": card.get("techLevel"),
               "source": "hearthstonejson.com", "status": "partial",
               "availability_json": payload_json({"in_pool": None, "basis": "catalog_only"})}
        result.append(provenance(row, {"catalog_supplement": True, "hsj_en": card, "hsj_ru": ru}))
    return result


def merge_embedded_skins(groups, additions):
    groups = copy.deepcopy(groups or [])
    known = {x.get("card_id") for group in groups for x in group.get("cards", [])}
    cards = [{"card_id": x["skin_card_id"], "title": x["title"], "caption": x["title"],
              "url": x.get("url"), "image_url": x.get("image_url"), "image_alt": x["title"]}
             for x in additions if x["skin_card_id"] not in known]
    if cards:
        heading = "Additional game variants"
        group = next((x for x in groups if x.get("heading") == heading), None)
        if group is None:
            group = {"heading": heading, "cards": []}
            groups.append(group)
        group["cards"].extend(cards)
    return groups
