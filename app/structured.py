from __future__ import annotations

import html
import re
from typing import Any

from .cards_index import cards_by_id, resolve_card_name
from .completeness import COMPLETENESS_SCHEMA_VERSION, row_retrieval_evidence
from .parsing_normalize import is_percent, looks_like_name, normalize_percent_text
from .sources import Source

INT_RE = re.compile(r"^\d+$")
# HSReplay serves localized deck rows ("Winrate ... Games" or "Побед ... Игр"),
# with either dot or comma decimals and space/nbsp thousand separators.
DECK_LINK_RE = re.compile(
    r"^(?P<name>.+?)\s+(?:Live now|\d[\d,.\s\u00a0]*)\s*(?:Winrate|Побед)\s+(?P<winrate>\d+(?:[.,]\d+)?%)"
    r"\s+(?:Games|Игр)\s+(?P<games>\d(?:[\d,.\s\u00a0]*\d)?)"
    r"(?:\s+Avg Duration\s+(?P<duration>[\d.,]+\s*(?:min|мин)))?",
    re.I,
)


def _is_int(value: str) -> bool:
    return bool(INT_RE.match(value.strip()))


def parse_legendary_groups(lines: list[str]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        if lines[i] != "Included Cards:":
            i += 1
            continue
        cards: list[dict[str, Any]] = []
        i += 1
        while i < len(lines) and lines[i] not in ("Winrate", "Included Cards:"):
            if _is_int(lines[i]) and i + 1 < len(lines) and not is_percent(lines[i + 1]):
                count = int(lines[i])
                name = lines[i + 1]
                resolved = resolve_card_name(name)
                cards.append({"count": count, "name": name, **resolved})
                i += 2
            else:
                i += 1
        stats: dict[str, Any] = {}
        while i < len(lines) and lines[i] != "Included Cards:":
            key = lines[i]
            if key in ("Winrate", "Pick Rate", "Offer Rate", "Score") and i + 1 < len(lines):
                stats[key.lower().replace(" ", "_")] = lines[i + 1]
                i += 2
            else:
                i += 1
        groups.append({"cards": cards, **stats})
    return groups


def parse_winning_decks(lines: list[str]) -> list[dict[str, Any]]:
    decks: list[dict[str, Any]] = []
    classes = {
        "Death Knight", "Demon Hunter", "Druid", "Hunter", "Mage",
        "Paladin", "Priest", "Rogue", "Shaman", "Warlock", "Warrior",
        "Рыцарь смерти", "Охотник на демонов", "Друид", "Охотник", "Маг",
        "Паладин", "Жрец", "Разбойник", "Шаман", "Чернокнижник", "Воин",
    }
    i = 0
    while i < len(lines):
        if lines[i] not in classes:
            i += 1
            continue
        hero_class = lines[i]
        record = None
        player = None
        played_at = None
        j = i + 1
        while j < len(lines) and j < i + 8:
            if lines[j] == "Record" and j + 1 < len(lines):
                record = lines[j + 1]
            if lines[j] == "Player" and j + 1 < len(lines):
                player = lines[j + 1]
                if j + 2 < len(lines):
                    played_at = lines[j + 2]
            if lines[j] in classes and j > i:
                break
            if lines[j] == "Final Deck":
                break
            j += 1
        decks.append(
            {
                "class": hero_class,
                "record": record,
                "player": player,
                "played_at": played_at,
            }
        )
        i = j if j > i + 1 else i + 1
    return decks


def parse_trending_decks(links: list[dict[str, str]]) -> list[dict[str, Any]]:
    decks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in links:
        href = link.get("href") or ""
        if "/decks/" not in href or href in seen or "#tab=" in href:
            continue
        text = link.get("text") or ""
        match = DECK_LINK_RE.search(text)
        if not match:
            continue
        seen.add(href)
        deck_id = href.split("/decks/")[1].split("/")[0].split("#")[0]
        decks.append(
            {
                "name": match.group("name").strip(),
                "winrate": match.group("winrate"),
                "games": match.group("games"),
                "duration": match.group("duration"),
                "deck_url": href,
                "hsreplay_deck_id": deck_id,
            }
        )
    return decks


_HERO_SKIP = {
    "Tier", "Hero", "Pick Rate", "Best Composition", "Avg. Placement",
    "Placement Distribution", "Unlock with", "Tier7", "All Minion Types",
    "APPLY", "RESET", "SUBSCRIBE NOW", "—", ".",
}
_DESC_PREFIXES = (
    "At ", "On ", "Get ", "Discover", "Whenever", "Your ", "After ", "Choose ",
    "The ", "If ", "Each ", "All ", "When ", "Play ", "Repeat ", "Triple ",
    "Minions", "Cards", "spells", "to ", "two ", "a ", "an ",
)


def _looks_like_hero_name(line: str) -> bool:
    return looks_like_name(
        line,
        skipped=_HERO_SKIP,
        description_prefixes=_DESC_PREFIXES,
        forbidden={"Record", "Player", "Final Deck", "Quests", "Trinket"},
        max_length=55,
    )


def parse_bg_heroes(lines: list[str]) -> list[dict[str, Any]]:
    heroes: list[dict[str, Any]] = []
    start = 0
    for i, line in enumerate(lines):
        if line == "Placement Distribution":
            start = i + 1
            break
    i = start
    current: dict[str, Any] | None = None
    while i < len(lines):
        line = lines[i]
        if is_percent(line):
            if current is not None:
                current["pick_rate"] = line
                heroes.append(current)
                current = None
            i += 1
            continue
        if line in _HERO_SKIP or line.startswith("("):
            i += 1
            continue
        if _looks_like_hero_name(line):
            if current is not None:
                heroes.append(current)
            current = {"hero": line, "description": ""}
            i += 1
            continue
        if current is not None:
            part = line if line != "." else ""
            if part:
                current["description"] = (current["description"] + " " + part).strip()[:200]
        i += 1
    if current is not None:
        heroes.append(current)
    return [h for h in heroes if h.get("pick_rate")][:50]


def _parse_hsguru_matchups_with_evidence(
    tables: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not tables:
        return [], row_retrieval_evidence(
            raw_rows=0,
            eligible_rows=0,
            normalized_rows=0,
            scope="hsguru_matchup_cells",
        )
    table = tables[0]
    headers = table.get("headers") or []
    rows = table.get("rows") or []
    if len(headers) < 3 or not rows:
        return [], row_retrieval_evidence(
            raw_rows=0,
            eligible_rows=0,
            normalized_rows=0,
            scope="hsguru_matchup_cells",
        )
    columns = headers[2:]
    pairs: list[dict[str, Any]] = []
    raw_cells = 0
    explained_self_matchups = 0
    explained_upstream_empty_cells = 0
    unexplained_missing_cells = 0
    for row in rows:
        if len(row) < 2:
            continue
        if "%" in str(row[0]):
            continue
        row_arch = row[1] if len(row) > 1 else ""
        if not row_arch or row_arch in ("Winrate", "Seed Weights Reset Weights Popularity: Archetype"):
            continue
        for ci, col in enumerate(columns):
            idx = ci + 2
            raw_cells += 1
            if idx >= len(row):
                unexplained_missing_cells += 1
                continue
            val = row[idx]
            if val is None or val == "":
                if str(row_arch).strip().casefold() == str(col).strip().casefold():
                    explained_self_matchups += 1
                else:
                    explained_upstream_empty_cells += 1
                continue
            pairs.append(
                {
                    "archetype": row_arch,
                    "vs": col,
                    # Keep malformed evidence intact so the semantic gate can
                    # reject it; adding '%' must not manufacture a metric.
                    "winrate": normalize_percent_text(str(val)) or val,
                }
            )
    return pairs, row_retrieval_evidence(
        raw_rows=raw_cells,
        eligible_rows=raw_cells,
        normalized_rows=len(pairs),
        explained_reasons={
            reason: count
            for reason, count in (
                ("self_matchup_not_applicable", explained_self_matchups),
                (
                    "upstream_insufficient_matchup_sample",
                    explained_upstream_empty_cells,
                ),
            )
            if count
        },
        unexplained_reasons=(
            {"missing_matchup_cell": unexplained_missing_cells}
            if unexplained_missing_cells
            else None
        ),
        scope="hsguru_matchup_cells",
    )


def parse_hsguru_matchups(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs, _ = _parse_hsguru_matchups_with_evidence(tables)
    return pairs


def parse_cards_from_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for table in tables:
        headers = [str(h).strip() for h in (table.get("headers") or [])]
        rows = table.get("rows") or []
        objects = table.get("objects") or []
        if objects and not headers:
            for obj in objects:
                name = None
                for key, val in obj.items():
                    if not val or not isinstance(val, str):
                        continue
                    kl = key.lower()
                    if "card" in kl or kl == "карта" or key == "column_1":
                        if len(val) > 2 and not is_percent(val):
                            name = val
                            break
                if not name:
                    continue
                resolved = resolve_card_name(name)
                if not resolved.get("id"):
                    continue
                entry: dict[str, Any] = {"name": name, **resolved}
                for key, val in obj.items():
                    kl = key.lower()
                    if not val:
                        continue
                    if "winrate" in kl or "винрейт" in kl or "побед" in kl:
                        entry["deck_winrate"] = val
                    elif "popularity" in kl or ("%" in str(val) and "deck" in kl):
                        entry["deck_popularity"] = val
                    elif "copies" in kl or "копи" in kl:
                        entry["avg_copies"] = val
                    elif "played" in kl or "разыгран" in kl:
                        entry["times_played"] = val
                    elif "pick" in kl or "частота" in kl:
                        entry["pick_rate"] = val
                    elif val.replace(",", ".").replace("%", "").replace(".", "", 1).isdigit() and "%" in str(val):
                        entry.setdefault("deck_popularity", val)
                cards.append(entry)
            continue
        if not headers:
            continue
        header_lc = [h.lower() for h in headers]
        if not any("card" in h or "карт" in h for h in header_lc):
            if len(headers) >= 2 and rows:
                headers = ["Card"] + [f"stat_{i}" for i in range(1, len(headers))]
                header_lc = [h.lower() for h in headers]
            else:
                continue
        name_idx = next((i for i, h in enumerate(header_lc) if "card" in h or h == "карта"), 0)
        stat_map: dict[str, int] = {}
        for i, h in enumerate(header_lc):
            if "winrate" in h or "винрейт" in h or "побед" in h:
                stat_map.setdefault("deck_winrate", i)
            elif "popularity" in h or "%" in h and "deck" in h:
                stat_map.setdefault("deck_popularity", i)
            elif "copies" in h or "копи" in h:
                stat_map.setdefault("avg_copies", i)
            elif "played" in h or "разыгран" in h:
                stat_map.setdefault("times_played", i)
            elif "pick" in h or "выбор" in h or "частота" in h:
                stat_map.setdefault("pick_rate", i)
        for row in table.get("rows") or []:
            if name_idx >= len(row):
                continue
            name = str(row[name_idx]).strip()
            if len(name) < 2:
                continue
            resolved = resolve_card_name(name)
            entry: dict[str, Any] = {"name": name, **resolved}
            for key, idx in stat_map.items():
                if idx < len(row):
                    entry[key] = row[idx]
            if resolved.get("id"):
                cards.append(entry)
    return cards


def parse_card_stats_lines(lines: list[str]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        if lines[i] == "★":
            i += 1
            continue
        if lines[i].isdigit() and int(lines[i]) <= 10:
            mana = int(lines[i])
            i += 1
            if i < len(lines) and lines[i] == "★":
                i += 1
            if i >= len(lines):
                break
            name = lines[i]
            resolved = resolve_card_name(name)
            if resolved.get("id"):
                entry: dict[str, Any] = {"name": name, "mana": mana, **resolved}
                i += 1
                stats: list[str] = []
                while i < len(lines):
                    if lines[i] == "★" or (lines[i].isdigit() and int(lines[i]) <= 10):
                        break
                    if is_percent(lines[i]) or lines[i].replace(",", ".").replace(".", "", 1).isdigit():
                        stats.append(lines[i])
                        i += 1
                        continue
                    if resolve_card_name(lines[i]).get("id"):
                        break
                    i += 1
                if stats:
                    entry["deck_popularity"] = next((s for s in stats if "%" in s), stats[0])
                    if len(stats) > 1:
                        entry["avg_copies"] = stats[1] if "%" not in stats[1] else None
                cards.append(entry)
            continue
        i += 1
    return cards


def parse_arena_card_lines(lines: list[str]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        if lines[i] == "★":
            i += 1
            continue
        if lines[i].isdigit() and int(lines[i]) <= 10:
            mana = int(lines[i])
            i += 1
            if i < len(lines) and lines[i] == "★":
                i += 1
            if i >= len(lines):
                break
            name = lines[i]
            resolved = resolve_card_name(name)
            if resolved.get("id"):
                cards.append({"name": name, "mana": mana, **resolved})
            i += 1
            continue
        i += 1
    return cards


_TRINKET_SKIP = {
    "Tier", "Trinket", "Pick Rate", "Avg. Placement", "Placement Distribution",
    "APPLY", "RESET", "SUBSCRIBE NOW", "Tier7", "Trinket Guide", "Top BGs Player",
    "s", "—", ".",
}

TRINKET_TRIBE_RU_TO_EN = {
    "Зверь": "Beast",
    "Демон": "Demon",
    "Дракон": "Dragon",
    "Элементаль": "Elemental",
    "Механизм": "Mech",
    "Робот": "Mech",
    "Мурлок": "Murloc",
    "Нага": "Naga",
    "Пират": "Pirate",
    "Свинобраз": "Quilboar",
    "Кабан": "Quilboar",
    "Нежить": "Undead",
}
TRINKET_TRIBE_EN_TO_RU = {
    "Beast": "Зверь",
    "Demon": "Демон",
    "Dragon": "Дракон",
    "Elemental": "Элементаль",
    "Mech": "Механизм",
    "Murloc": "Мурлок",
    "Naga": "Нага",
    "Pirate": "Пират",
    "Quilboar": "Свинобраз",
    "Undead": "Нежить",
}
TRINKET_TRIBE_EN_PLURAL = {
    "Beast": "Beasts",
    "Demon": "Demons",
    "Dragon": "Dragons",
    "Elemental": "Elementals",
    "Mech": "Mechs",
    "Murloc": "Murlocs",
    "Naga": "Naga",
    "Pirate": "Pirates",
    "Quilboar": "Quilboar",
    "Undead": "Undead",
}
TRINKET_TRIBES = set(TRINKET_TRIBE_EN_TO_RU) | set(TRINKET_TRIBE_RU_TO_EN)
TRINKET_TIER_MARKERS = {"s", "a", "b", "c", "d", "e", "f"}


def _looks_like_trinket_name(line: str) -> bool:
    return looks_like_name(
        line,
        skipped=_TRINKET_SKIP,
        description_prefixes=_DESC_PREFIXES,
        min_length=4,
        forbidden={line} if line.endswith(" Place") or "Place:" in line else (),
        reject_terminal_punctuation=True,
    )


def normalize_trinket_tribe(value: Any) -> tuple[str | None, str | None]:
    raw = str(value or "").strip()
    if not raw:
        return None, None
    if raw in TRINKET_TRIBE_RU_TO_EN:
        tribe = TRINKET_TRIBE_RU_TO_EN[raw]
        return tribe, raw
    if raw in TRINKET_TRIBE_EN_TO_RU:
        return raw, TRINKET_TRIBE_EN_TO_RU[raw]
    return None, None


def _is_trinket_tribe(value: str) -> bool:
    return normalize_trinket_tribe(value)[0] is not None


def _active_trinket_tier(lines: list[str], index: int) -> str | None:
    for prev in reversed(lines[max(0, index - 80):index]):
        marker = prev.strip().lower()
        if marker in TRINKET_TIER_MARKERS:
            return marker.upper()
        if prev in ("Tier", "Trinket", "Placement Distribution"):
            return None
    return None


def trinket_variant_key(row: dict[str, Any], trinket_type: str | None = None) -> str:
    name = str(row.get("name") or "").strip().lower()
    tribe = str(row.get("tribe") or row.get("race") or "").strip().lower()
    description = str(row.get("description") or "").strip().lower()
    card_id = str(row.get("trinket_id") or row.get("id") or "").strip().lower()
    type_key = str(row.get("trinket_tier") or row.get("type") or trinket_type or "").strip().lower()
    return "|".join([type_key, name, tribe, card_id, description[:80]])


def trinket_identity_key(row: dict[str, Any], trinket_type: str | None = None) -> str:
    name = str(row.get("name") or "").strip().lower()
    tribe = str(row.get("tribe") or row.get("race") or "").strip().lower()
    card_id = str(row.get("trinket_id") or row.get("id") or "").strip().lower()
    type_key = str(row.get("trinket_tier") or row.get("type") or trinket_type or "").strip().lower()
    return "|".join([type_key, name, tribe, card_id])


def _normalize_trinket_card_text(
    value: Any,
    *,
    tribe: str | None = None,
    tribe_ru: str | None = None,
    locale: str = "enUS",
) -> str:
    text = str(value or "")
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\[x\]", "", text, flags=re.I)
    text = re.sub(r"\$(\d+)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()

    def replace_inflection(match: re.Match[str]) -> str:
        number = int(match.group("number"))
        forms = [form.strip() for form in match.group("forms").split(",")]
        if not forms:
            return str(number)
        if len(forms) >= 3 and locale == "ruRU":
            if number % 100 in range(11, 15):
                selected = forms[2]
            elif number % 10 == 1:
                selected = forms[0]
            elif number % 10 in range(2, 5):
                selected = forms[1]
            else:
                selected = forms[2]
        else:
            selected = forms[0] if number == 1 else forms[min(1, len(forms) - 1)]
        return f"{number} {selected}".strip()

    text = re.sub(
        r"\(?(?P<number>\d+)\)?\s*\|4\((?P<forms>[^)]+)\)",
        replace_inflection,
        text,
    )

    if locale == "ruRU":
        if tribe_ru:
            text = re.sub(r"\b92\b", tribe_ru.lower(), text)
        else:
            text = re.sub(r"типа\s+[«\"]?92[»\"]?", "предложенного типа", text, flags=re.I)
            text = re.sub(r"\b92\b", "существ предложенного типа", text)
    elif tribe:
        plural = TRINKET_TRIBE_EN_PLURAL.get(tribe, tribe)
        text = re.sub(
            r"\b(\d+)\s+random\s+92\b",
            lambda match: f"{match.group(1)} random {plural}",
            text,
            flags=re.I,
        )
        text = re.sub(r"\b92\b", tribe, text)
    else:
        text = re.sub(
            r"\ba random\s+92\b",
            "a random minion of the offered type",
            text,
            flags=re.I,
        )
        text = re.sub(r"\b92\b", "minions of the offered type", text)
    return text


def _canonical_trinket_fields(
    card_id: str,
    *,
    tribe: str | None,
    tribe_ru: str | None,
) -> dict[str, str]:
    if not card_id:
        return {}
    try:
        card_en = cards_by_id("enUS").get(card_id) or {}
    except Exception:
        card_en = {}
    try:
        card_ru = cards_by_id("ruRU").get(card_id) or {}
    except Exception:
        card_ru = {}

    fields: dict[str, str] = {}
    name_en = str(card_en.get("name") or "").strip()
    description_en = _normalize_trinket_card_text(
        card_en.get("text"),
        tribe=tribe,
        tribe_ru=tribe_ru,
        locale="enUS",
    )
    name_ru = str(card_ru.get("name") or "").strip()
    description_ru = _normalize_trinket_card_text(
        card_ru.get("text"),
        tribe=tribe,
        tribe_ru=tribe_ru,
        locale="ruRU",
    )
    if name_en:
        fields["name"] = name_en
    if description_en:
        fields["description"] = description_en
    if name_ru:
        fields["localized_name"] = name_ru
    if description_ru:
        fields["localized_description"] = description_ru
    return fields


def enrich_trinket_variant_fields(row: dict[str, Any], *, trinket_type: str | None = None) -> dict[str, Any]:
    out = dict(row)
    if trinket_type:
        out.setdefault("type", trinket_type)
        out.setdefault("trinket_tier", trinket_type)
    if out.get("type") and not out.get("trinket_tier"):
        out["trinket_tier"] = out["type"]
    tribe, tribe_ru = normalize_trinket_tribe(out.get("tribe") or out.get("race"))
    if not tribe:
        tribe, tribe_ru = normalize_trinket_tribe(out.get("description"))
    if tribe:
        out["tribe"] = tribe
        out["race"] = tribe
        out["tribe_ru"] = tribe_ru
        if str(out.get("description") or "").strip() in TRINKET_TRIBE_RU_TO_EN:
            out.pop("description", None)
    card_id = str(out.get("trinket_id") or out.get("id") or "").strip()
    out.update(
        _canonical_trinket_fields(
            card_id,
            tribe=tribe,
            tribe_ru=tribe_ru,
        )
    )
    out["variant_key"] = trinket_variant_key(out, trinket_type)
    return out


def parse_bg_trinkets(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    start = 0
    for i, line in enumerate(lines):
        if line == "Placement Distribution":
            start = i + 1
            break
    i = start
    current: dict[str, Any] | None = None
    current_tier: str | None = None
    while i < len(lines):
        line = lines[i]
        if line.strip().lower() in TRINKET_TIER_MARKERS:
            current_tier = line.strip().upper()
            i += 1
            continue
        if is_percent(line) and "Place" not in line:
            if current is not None:
                current["pick_rate"] = line
                if i + 1 < len(lines) and not is_percent(lines[i + 1]) and lines[i + 1].replace(".", "", 1).isdigit():
                    current["avg_placement"] = lines[i + 1]
                items.append(current)
                current = None
            i += 1
            continue
        if line in _TRINKET_SKIP or line.startswith("(") or "Place:" in line:
            i += 1
            continue
        if _looks_like_trinket_name(line):
            if current is not None:
                items.append(current)
            current = {"name": line, "description": "", "tier": current_tier or _active_trinket_tier(lines, i)}
            if i > 0 and lines[i - 1].isdigit():
                current["cost"] = int(lines[i - 1])
            i += 1
            if i < len(lines) and _is_trinket_tribe(lines[i]):
                tribe, tribe_ru = normalize_trinket_tribe(lines[i])
                current["tribe"] = tribe
                current["race"] = tribe
                current["tribe_ru"] = tribe_ru
                i += 1
            continue
        if current is not None and not _looks_like_trinket_name(line):
            current["description"] = (current.get("description", "") + " " + line).strip()[:180]
        i += 1
    if current is not None:
        items.append(current)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in items:
        if not t.get("pick_rate"):
            continue
        name = str(t.get("name") or "").strip()
        if len(name) < 4 or not name[0].isalnum():
            continue
        t = enrich_trinket_variant_fields(t)
        key = trinket_variant_key(t)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out[:140]


def parse_arena_matrix(tables: list[dict[str, Any]], lines: list[str]) -> list[dict[str, Any]]:
    if tables and tables[0].get("objects"):
        return tables[0]["objects"][:50]
    pairs: list[dict[str, Any]] = []
    classes = [
        "Death Knight", "Demon Hunter", "Druid", "Hunter", "Mage",
        "Paladin", "Priest", "Rogue", "Shaman", "Warlock", "Warrior",
    ]
    i = 0
    while i + 2 < len(lines):
        if lines[i] in classes and is_percent(lines[i + 2]):
            pairs.append({"class_a": lines[i], "class_b": lines[i + 1], "winrate": lines[i + 2]})
            i += 3
        else:
            i += 1
    return pairs[:60]


def streamer_decks_table(tables: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the populated HSGuru streamer table over an empty placeholder."""
    empty_match: dict[str, Any] | None = None
    for table in tables:
        if not isinstance(table, dict):
            continue
        headers = {
            str(header).strip().casefold()
            for header in (table.get("headers") or [])
        }
        objects = table.get("objects") or []
        has_streamer_fields = any(
            isinstance(row, dict)
            and {"deck", "streamer"}.issubset(
                {str(key).strip().casefold() for key in row}
            )
            for row in objects
        )
        if not ({"deck", "streamer"}.issubset(headers) or has_streamer_fields):
            continue
        if objects:
            return table
        empty_match = empty_match or table
    if empty_match is not None:
        return empty_match
    return tables[0] if tables and isinstance(tables[0], dict) else None


def build_structured(source: Source, data: dict[str, Any]) -> dict[str, Any]:
    extracted = data.get("hsreplay_extracted") or {}
    if extracted.get("type"):
        return extracted

    lines = data.get("text_preview") or []
    tables = data.get("tables") or []
    links = data.get("links") or []
    sid = source.id

    if source.site == "hsguru":
        if source.category == "meta":
            rows = []
            for table in tables:
                rows.extend(table.get("objects") or [])
            return {
                "type": "meta",
                "strategies": rows,
                "completeness_schema_version": COMPLETENESS_SCHEMA_VERSION,
                "row_retrieval": row_retrieval_evidence(
                    raw_rows=len(rows),
                    eligible_rows=len(rows),
                    normalized_rows=len(rows),
                    scope="hsguru_meta_tables",
                ),
            }
        if source.category == "streamer_decks":
            table = streamer_decks_table(tables)
            raw_rows = table.get("objects") if table else []
            if not isinstance(raw_rows, list):
                raw_rows = []
            rows = [row for row in raw_rows if isinstance(row, dict)]
            dropped_rows = len(raw_rows) - len(rows)
            return {
                "type": "streamer_decks",
                "rows": rows,
                "completeness_schema_version": COMPLETENESS_SCHEMA_VERSION,
                "row_retrieval": row_retrieval_evidence(
                    raw_rows=len(raw_rows),
                    eligible_rows=len(raw_rows),
                    normalized_rows=len(rows),
                    unexplained_reasons=(
                        {"non_object_table_row": dropped_rows}
                        if dropped_rows
                        else None
                    ),
                    scope="hsguru_streamer_first_table",
                ),
            }
        if source.category == "matchups":
            matchups, row_retrieval = _parse_hsguru_matchups_with_evidence(tables)
            return {
                "type": "matchups",
                "matchups": matchups,
                "completeness_schema_version": COMPLETENESS_SCHEMA_VERSION,
                "row_retrieval": row_retrieval,
            }

    if sid == "hsreplay_arena_legendaries":
        return {"type": "arena_legendary_groups", "groups": parse_legendary_groups(lines)}
    if sid == "hsreplay_arena_winning_decks":
        return {"type": "arena_winning_decks", "decks": parse_winning_decks(lines)}
    if sid == "hsreplay_decks_trending":
        return {"type": "trending_decks", "decks": parse_trending_decks(links)}
    if sid == "hsreplay_arena":
        return {"type": "arena_class_matrix", "matchups": parse_arena_matrix(tables, lines)}
    if sid == "hsreplay_battlegrounds_comps":
        blocked = any("sign in" in l.lower() for l in lines[:40]) and not any(
            "comp" in l.lower() and "%" in l for l in lines
        )
        comps = [
            l for l in lines
            if len(l) > 5 and len(l) < 80 and any(k in l.lower() for k in ("comp", "build", "tier"))
        ][:40]
        return {"type": "bg_comps", "blocked": blocked, "comps": comps}
    if sid.startswith("hsreplay_battlegrounds_trinkets_"):
        return {"type": "bg_trinkets", "trinkets": parse_bg_trinkets(lines)}
    if sid.startswith("hsreplay_cards_"):
        blocked = any("could not load" in l.lower() for l in lines)
        cards = parse_cards_from_tables(tables) if not blocked else []
        if len(cards) < 10:
            start = 0
            for i, line in enumerate(lines):
                if line in ("Карта", "Card"):
                    start = i + 1
                    break
            cards = parse_card_stats_lines(lines[start:]) if not blocked else []
        return {"type": "card_stats", "blocked": blocked, "cards": cards}
    if sid == "hsreplay_arena_cards_advanced":
        cards = parse_cards_from_tables(tables)
        if len(cards) < 10:
            cards = parse_arena_card_lines(lines)
        return {
            "type": "arena_card_tiers",
            "cards": cards,
            "total_cards": next((l for l in lines if l.isdigit() and int(l) > 100), None),
        }

    if sid == "metastats_decks":
        return data.get("structured") or data.get("hsreplay_extracted") or {"type": "metastats_decks", "decks": []}
    if sid == "metastats_matchups":
        return data.get("structured") or data.get("hsreplay_extracted") or {"type": "metastats_matchups", "matchups": [], "archetypes": []}
    if sid == "hearthstone_decks":
        return data.get("structured") or data.get("hsreplay_extracted") or {"type": "hearthstone_decks", "decks": []}
    if sid == "vicious_syndicate_radars":
        return data.get("structured") or data.get("hsreplay_extracted") or {"type": "vicious_syndicate_radars", "radars": []}

    return {"type": "raw", "lines": lines[:80]}
