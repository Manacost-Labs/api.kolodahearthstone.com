"""Cards that offer a choice and the full pool each one chooses from.

Everything here is derived from the HearthstoneJSON card base; no network.

*Kinds* (by mechanics and text of a collectible card):

- ``discover``   — **Discover**: three options from a pool the card text defines;
- ``adapt``      — **Adapt**: three of the ten Un'Goro adaptations;
- ``dredge``     — **Dredge**: the bottom three cards of your own deck;
- ``choose_one`` — **Choose One**: the card's own two options;
- ``excavate``   — **Excavate**: a random treasure of the next tier (no choice).

HSReplay ``single_card_choices_by_winrate_v2`` answers ``discover``, ``adapt``
and ``dredge`` cards (``STATS_KINDS``); it answers HTTP 204 for Choose One
and Excavate (verified live 2026-10-25), so those get the pool only.

The *pool* of a Discover card is parsed from its text: card type, minion type,
spell school, keyword, rarity, cost and class rule. Text that depends on game
state (your deck, the opponent's hand, what died, runes, ...) or that the
parser does not understand yields ``resolved: false`` with a reason instead of
a guessed pool.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from typing import Any

from . import cards_index
from .storage import load_dataset

CLASSES = (
    "DEATHKNIGHT",
    "DEMONHUNTER",
    "DRUID",
    "HUNTER",
    "MAGE",
    "PALADIN",
    "PRIEST",
    "ROGUE",
    "SHAMAN",
    "WARLOCK",
    "WARRIOR",
)
NEUTRAL = "NEUTRAL"
FORMATS = ("standard", "wild")
STATS_KINDS = frozenset({"discover", "adapt", "dredge"})
KIND_ORDER = ("discover", "adapt", "dredge", "choose_one", "excavate")

# Standard is read from HSReplay's own Standard card table (a set with at
# least this many played cards is in rotation); this list is the fallback
# when no such dataset exists yet (Standard of October 2026).
STANDARD_SETS_FALLBACK = frozenset(
    {
        "CORE",
        "BE",
        "CATACLYSM",
        "EMERALD_DREAM",
        "ESCAPEFROM_VIOLET_HOLD",
        "THE_LOST_CITY",
        "TIME_TRAVEL",
    }
)
STANDARD_CARD_DATASETS = (
    "hsreplay_cards_legend_14d",
    "hsreplay_cards_legend_patch",
    "hsreplay_cards_diamond_14d",
)
STANDARD_SET_MIN_CARDS = 10
# Event cards rotate one by one, so EVENT never counts as a whole set.
PER_CARD_SETS = frozenset({"EVENT"})

ADAPT_OPTION_PREFIX = "UNG_999t"
EXCAVATE_TREASURE_PREFIXES = ("WW_001t", "DEEP_999t")
EXCAVATE_TIERS = {"COMMON": 1, "RARE": 2, "EPIC": 3, "LEGENDARY": 4}

RACES = {
    "beast": "BEAST",
    "demon": "DEMON",
    "dragon": "DRAGON",
    "elemental": "ELEMENTAL",
    "mech": "MECHANICAL",
    "murloc": "MURLOC",
    "naga": "NAGA",
    "pirate": "PIRATE",
    "totem": "TOTEM",
    "undead": "UNDEAD",
    "quilboar": "QUILBOAR",
    "draenei": "DRAENEI",
}
SCHOOLS = {
    "arcane": "ARCANE",
    "fel": "FEL",
    "fire": "FIRE",
    "frost": "FROST",
    "holy": "HOLY",
    "nature": "NATURE",
    "shadow": "SHADOW",
}
TYPES = {
    "card": None,
    "spell": "SPELL",
    "minion": "MINION",
    "weapon": "WEAPON",
    "location": "LOCATION",
}
KEYWORDS = {
    "battlecry": "BATTLECRY",
    "choose one": "CHOOSE_ONE",
    "combo": "COMBO",
    "deathrattle": "DEATHRATTLE",
    "divine shield": "DIVINE_SHIELD",
    "lifesteal": "LIFESTEAL",
    "outcast": "OUTCAST",
    "overload": "OVERLOAD",
    "rush": "RUSH",
    "secret": "SECRET",
    "spell damage": "SPELLPOWER",
    "stealth": "STEALTH",
    "taunt": "TAUNT",
}
CLASS_WORDS = {
    "death knight": "DEATHKNIGHT",
    "demon hunter": "DEMONHUNTER",
    "druid": "DRUID",
    "hunter": "HUNTER",
    "mage": "MAGE",
    "paladin": "PALADIN",
    "priest": "PRIEST",
    "rogue": "ROGUE",
    "shaman": "SHAMAN",
    "warlock": "WARLOCK",
    "warrior": "WARRIOR",
}
# The pool comes from the game, not from the card base.
GAME_STATE_MARKERS = (
    "your deck",
    "in your deck",
    "from your deck",
    "opponent",
    "enemy deck",
    "enemy hand",
    "died this game",
    "copy of",
    "you haven't",
    "same rarity",
    "equal to",
    "rune",
    "contraband",
    "one.",
    "refresh your options",
    "wild god",
    "type you",
    "odd-attack",
    "even-attack",
    "suspicious",
    "friendly",
)
# Words that carry no filter once every known filter is consumed.
FILLER = frozenset(
    {
        "a",
        "an",
        "any",
        "two",
        "2",
        "3",
        "three",
        "card",
        "cards",
        "from",
        "with",
        "dark",
        "gift",
        "to",
        "summon",
        "temporary",
        "it",
        "its",
        "or",
        "and",
        "another",
        "class",
        "the",
        "past",
        "your",
        "",
    }
)

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def clean_text(text: str | None) -> str:
    value = _TAG.sub("", str(text or "")).replace("[x]", " ")
    value = value.replace("&", " ").replace("\n", " ").replace("$", "").replace("#", "")
    return _SPACE.sub(" ", value).strip()


def _mechanics(card: Mapping[str, Any]) -> set[str]:
    return {str(m) for m in (card.get("mechanics") or [])}


def card_classes(card: Mapping[str, Any]) -> list[str]:
    classes = [str(c) for c in (card.get("classes") or []) if c]
    if not classes and card.get("cardClass"):
        classes = [str(card["cardClass"])]
    return classes or [NEUTRAL]


def choice_kind(card: Mapping[str, Any]) -> str | None:
    """The choice mechanic of one collectible card, or ``None``."""

    if not card.get("collectible"):
        return None
    mechanics = _mechanics(card)
    referenced = {str(m) for m in (card.get("referencedTags") or [])}
    text = clean_text(card.get("text")).lower()
    if "EXCAVATE" in mechanics:
        return "excavate"
    if "DISCOVER" in mechanics:
        return "discover"
    if "ADAPT" in mechanics or "ADAPT" in referenced or re.search(r"\badapt\b", text):
        return "adapt"
    if "DREDGE" in mechanics:
        return "dredge"
    if "CHOOSE_ONE" in mechanics:
        return "choose_one"
    return None


# --- formats --------------------------------------------------------------------


def _dataset_cards(source_id: str) -> list[Mapping[str, Any]]:
    try:
        payload = load_dataset(source_id) or {}
    except Exception:  # noqa: BLE001 - an unreadable dataset means "use the fallback"
        return []
    structured = (payload.get("data") or {}).get("structured") or {}
    cards = structured.get("cards") if isinstance(structured, dict) else None
    return [row for row in cards or [] if isinstance(row, Mapping)]


def standard_evidence(
    cards_by_dbf: Mapping[int, Mapping[str, Any]],
    datasets: Iterable[str] = STANDARD_CARD_DATASETS,
) -> tuple[frozenset[str], frozenset[int]]:
    """Standard sets and the extra Standard dbf ids seen in HSReplay's Standard tables."""

    seen: set[int] = set()
    for source_id in datasets:
        for row in _dataset_cards(source_id):
            dbf = row.get("dbfId", row.get("dbf_id"))
            try:
                seen.add(int(dbf))
            except (TypeError, ValueError):
                continue
    counts: dict[str, int] = {}
    for dbf in seen:
        card = cards_by_dbf.get(dbf)
        if card and card.get("set"):
            counts[str(card["set"])] = counts.get(str(card["set"]), 0) + 1
    sets = frozenset(
        name
        for name, count in counts.items()
        if count >= STANDARD_SET_MIN_CARDS and name not in PER_CARD_SETS
    )
    if not sets:
        return STANDARD_SETS_FALLBACK, frozenset(seen)
    return sets, frozenset(seen)


class CardBase:
    """Collectible cards of one HearthstoneJSON snapshot with Standard membership."""

    def __init__(
        self,
        cards: Iterable[Mapping[str, Any]],
        *,
        standard_sets: Iterable[str],
        standard_dbf: Iterable[int] = (),
        names_ru: Mapping[str, str] | None = None,
    ) -> None:
        self.all = [card for card in cards if card.get("id")]
        self.by_id = {str(card["id"]): card for card in self.all}
        self.by_dbf = {
            int(card["dbfId"]): card
            for card in self.all
            if card.get("dbfId") is not None
        }
        self.standard_sets = frozenset(standard_sets)
        self.standard_dbf = frozenset(int(dbf) for dbf in standard_dbf)
        self.names_ru = dict(names_ru or {})
        self.collectible = [
            card
            for card in self.all
            if card.get("collectible") and card.get("type") not in {"ENCHANTMENT"}
        ]

    def is_standard(self, card: Mapping[str, Any]) -> bool:
        if card.get("set") in PER_CARD_SETS:
            return int(card.get("dbfId") or 0) in self.standard_dbf
        return card.get("set") in self.standard_sets

    def formats_of(self, card: Mapping[str, Any]) -> list[str]:
        return ["standard", "wild"] if self.is_standard(card) else ["wild"]

    def in_format(self, card: Mapping[str, Any], format_name: str) -> bool:
        return format_name == "wild" or self.is_standard(card)

    def name_ru(self, card: Mapping[str, Any]) -> str | None:
        return self.names_ru.get(str(card.get("id"))) or card.get("name")

    def resolve(self, ref: str) -> Mapping[str, Any] | None:
        """A card by id (any case) or dbf id."""

        value = str(ref or "").strip()
        if not value:
            return None
        if value.isdigit():
            return self.by_dbf.get(int(value))
        found = self.by_id.get(value)
        if found is not None:
            return found
        lowered = value.lower()
        return next(
            (card for key, card in self.by_id.items() if key.lower() == lowered), None
        )


_BASE_CACHE: dict[str, Any] = {}
BASE_CACHE_SECONDS = 3600


def load_card_base() -> CardBase:
    """The current HearthstoneJSON base, rebuilt when the card index or the hour changes."""

    by_dbf = cards_index.cards_by_dbfid()
    now = time.monotonic()
    cached = _BASE_CACHE.get("base")
    if (
        cached is not None
        and _BASE_CACHE.get("index") is by_dbf
        and now - float(_BASE_CACHE.get("built_at") or 0) < BASE_CACHE_SECONDS
    ):
        return cached
    sets, extra = standard_evidence(by_dbf)
    try:
        names_ru = {
            card_id: str(card.get("name"))
            for card_id, card in cards_index.cards_by_id("ruRU").items()
            if card.get("name")
        }
    except Exception:  # noqa: BLE001 - Russian names are decoration only
        names_ru = {}
    base = CardBase(
        by_dbf.values(), standard_sets=sets, standard_dbf=extra, names_ru=names_ru
    )
    _BASE_CACHE.update(base=base, index=by_dbf, built_at=now)
    return base


# --- choice cards ---------------------------------------------------------------


def choice_cards(
    base: CardBase, format_name: str | None = None
) -> list[dict[str, Any]]:
    """Every collectible card with a choice, optionally of one format."""

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in base.collectible:
        kind = choice_kind(card)
        if kind is None:
            continue
        formats = base.formats_of(card)
        if format_name is not None and format_name not in formats:
            continue
        if card["id"] in seen:
            continue
        seen.add(str(card["id"]))
        rows.append(
            {
                "card_id": str(card["id"]),
                "dbf_id": int(card["dbfId"]),
                "name": card.get("name"),
                "name_ru": base.name_ru(card),
                "kind": kind,
                "card_class": card_classes(card)[0],
                "set": card.get("set"),
                "formats": formats,
                "has_hsreplay_stats": kind in STATS_KINDS,
            }
        )
    rows.sort(key=lambda row: (KIND_ORDER.index(row["kind"]), row["card_id"]))
    return rows


# --- pools ----------------------------------------------------------------------


def _discover_clauses(text: str) -> list[str]:
    """Each 'Discover ...' clause up to its sentence or option end."""

    clauses = []
    for match in re.finditer(
        r"discover\s+(.*?)(?:[.;!]|\bif\b|\((?!\d)|$)", text, re.IGNORECASE
    ):
        # "... to gain its stats", "... and reduce its Cost": effects, not filters.
        clause = re.split(
            r"\s+to\s+(?!summon\b)|\s+and\s+(?:reduce|give)\b", match.group(1)
        )[0]
        clauses.append(clause.strip())
    return clauses


def parse_discover_rule(clause: str, *, source_classes: list[str]) -> dict[str, Any]:
    """Filters of one Discover clause, or ``{"resolved": False, "reason": ...}``."""

    text = " " + re.sub(r"^and (?:cast|play) ", "", clause.lower().strip()) + " "
    for marker in GAME_STATE_MARKERS:
        if marker in text:
            return {"resolved": False, "reason": "game_state", "clause": clause}
    rule: dict[str, Any] = {"resolved": True, "clause": clause}
    if " from the past" in text:
        rule["past"] = True
        text = text.replace(" from the past", " ")
    if " from any class" in text or " any class" in text:
        rule["class_rule"] = "any"
        text = text.replace(" from any class", " ").replace(" any class", " ")
    elif " from another class" in text:
        rule["class_rule"] = "another"
        text = text.replace(" from another class", " ")
    elif " from your class" in text:
        rule["class_rule"] = "own_only"
        text = text.replace(" from your class", " ")
    match = re.search(r"\b(?:that )?costs? \((\d+)\) or (more|less)\b", text)
    if match:
        bound = int(match.group(1))
        rule["cost_min" if match.group(2) == "more" else "cost_max"] = bound
        text = text.replace(match.group(0), " ")
    match = re.search(r"\b(\d+)-cost\b", text)
    if match:
        rule["cost"] = int(match.group(1))
        text = text.replace(match.group(0), " ")
    if " legendary " in text:
        rule["rarity"] = "LEGENDARY"
        text = text.replace(" legendary ", " ")
    named: list[str] = []
    for word, klass in sorted(CLASS_WORDS.items(), key=lambda item: -len(item[0])):
        # Longest first: "demon hunter" is gone before "hunter" is tried.
        pattern = rf"\b{word}\b"
        if re.search(pattern, text):
            named.append(klass)
            text = re.sub(pattern, " ", text)
    if named:
        rule["class_rule"] = "explicit"
        rule["classes"] = sorted(named)
    keywords = []
    for word, mechanic in sorted(KEYWORDS.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{word}\b", text):
            keywords.append(mechanic)
            text = re.sub(rf"\b{word}\b", " ", text)
    if keywords:
        rule["keywords_any"] = sorted(keywords)
    races = []
    for word, race in RACES.items():
        if re.search(rf"\b{word}s?\b", text):
            races.append(race)
            text = re.sub(rf"\b{word}s?\b", " ", text)
    if races:
        rule["races_any"] = sorted(races)
        rule["type"] = "MINION"
    for word, school in SCHOOLS.items():
        if re.search(rf"\b{word}\b", text):
            rule["school"] = school
            rule["type"] = "SPELL"
            text = re.sub(rf"\b{word}\b", " ", text)
            break
    for word, card_type in TYPES.items():
        if re.search(rf"\b{word}s?\b", text):
            if card_type is not None:
                rule["type"] = card_type
            text = re.sub(rf"\b{word}s?\b", " ", text)
    leftover = [
        word
        for word in re.split(r"[\s,:\-]+", text)
        if word not in FILLER and not word.isdigit()
    ]
    if leftover:
        return {
            "resolved": False,
            "reason": "unparsed",
            "clause": clause,
            "unparsed": leftover[:6],
        }
    rule.setdefault("class_rule", "own")
    rule["source_classes"] = source_classes
    return rule


def named_cards_rule(
    base: CardBase, clause: str, format_name: str
) -> dict[str, Any] | None:
    """'Discover a Temporary Frostbolt, Arcane Intellect, or Fireball': the named cards."""

    text = re.sub(r"^(?:an?|two|2)\s+", "", clause.strip(), flags=re.IGNORECASE)
    text = re.sub(r"^temporary\s+", "", text, flags=re.IGNORECASE)
    parts = [
        part.strip()
        for part in re.split(r",\s*(?:or\s+)?|\s+or\s+", text)
        if part.strip()
    ]
    if len(parts) < 2:
        return None
    by_name: dict[str, Mapping[str, Any]] = {}
    for card in base.collectible:
        if base.in_format(card, format_name):
            by_name.setdefault(str(card.get("name") or "").lower(), card)
    found = [by_name.get(part.lower()) for part in parts]
    if not all(found):
        return None
    return {
        "resolved": True,
        "clause": clause,
        "named": [int(card["dbfId"]) for card in found if card],
    }


def _class_allowed(card: Mapping[str, Any], rule: Mapping[str, Any]) -> bool:
    classes = set(card_classes(card))
    source = set(rule.get("source_classes") or [NEUTRAL])
    class_rule = rule.get("class_rule", "own")
    if class_rule == "any":
        return True
    if class_rule == "explicit":
        return bool(classes & set(rule.get("classes") or []))
    if NEUTRAL in source:
        # A neutral card discovers for whichever class plays it: every class.
        return class_rule != "own_only" or NEUTRAL not in classes
    if class_rule == "another":
        return NEUTRAL not in classes and not (classes & source)
    if class_rule == "own_only":
        return bool(classes & source)
    return bool(classes & source) or NEUTRAL in classes


def matches_rule(card: Mapping[str, Any], rule: Mapping[str, Any]) -> bool:
    if rule.get("type") and card.get("type") != rule["type"]:
        return False
    if card.get("type") == "HERO" and not rule.get("type"):
        # A plain "Discover a card" offers no hero cards in practice.
        return False
    cost = card.get("cost")
    if "cost" in rule and cost != rule["cost"]:
        return False
    if "cost_min" in rule and (cost is None or cost < rule["cost_min"]):
        return False
    if "cost_max" in rule and (cost is None or cost > rule["cost_max"]):
        return False
    if rule.get("rarity") and card.get("rarity") != rule["rarity"]:
        return False
    if rule.get("school") and card.get("spellSchool") != rule["school"]:
        return False
    if rule.get("races_any"):
        races = {str(r) for r in (card.get("races") or [])}
        if card.get("race"):
            races.add(str(card["race"]))
        if "ALL" not in races and not races & set(rule["races_any"]):
            return False
    if rule.get("keywords_any"):
        mechanics = _mechanics(card)
        if not mechanics & set(rule["keywords_any"]):
            return False
    return _class_allowed(card, rule)


def option_row(base: CardBase, card: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "dbf_id": int(card["dbfId"]),
        "card_id": str(card["id"]),
        "name": card.get("name"),
        "name_ru": base.name_ru(card),
        "card_class": card_classes(card)[0],
        "cost": card.get("cost"),
        "type": card.get("type"),
        "rarity": card.get("rarity"),
        **extra,
    }


def _dedupe_by_name(cards: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """One entry per card name (Core reprints share the original's name)."""

    picked: dict[tuple[str, str], Mapping[str, Any]] = {}
    for card in cards:
        key = (str(card.get("name")), str(card.get("type")))
        current = picked.get(key)
        if current is None or (
            str(current.get("set")) == "CORE" and str(card.get("set")) != "CORE"
        ):
            picked[key] = card
    return list(picked.values())


def _sorted_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        options,
        key=lambda row: (
            row.get("cost") if row.get("cost") is not None else 99,
            row["name"] or "",
        ),
    )


def discover_pool(
    base: CardBase, card: Mapping[str, Any], format_name: str
) -> dict[str, Any]:
    text = clean_text(card.get("text"))
    clauses = _discover_clauses(text)
    if not clauses:
        return {"resolved": False, "reason": "unparsed", "rules": [], "cards": []}
    rules = []
    for clause in clauses:
        rule = parse_discover_rule(clause, source_classes=card_classes(card))
        if rule.get("reason") == "unparsed":
            rule = named_cards_rule(base, clause, format_name) or rule
        rules.append(rule)
    unresolved = next((rule for rule in rules if not rule.get("resolved")), None)
    if unresolved is not None:
        return {
            "resolved": False,
            "reason": unresolved["reason"],
            "rules": rules,
            "cards": [],
        }
    pool: dict[int, Mapping[str, Any]] = {}
    for rule in rules:
        if rule.get("named"):
            for dbf in rule["named"]:
                pool[int(dbf)] = base.by_dbf[int(dbf)]
            continue
        for candidate in base.collectible:
            if candidate.get("id") == card.get("id"):
                continue
            if rule.get("past"):
                if base.is_standard(candidate):
                    continue
            elif not base.in_format(candidate, format_name):
                continue
            if matches_rule(candidate, rule):
                pool[int(candidate["dbfId"])] = candidate
    cards = _dedupe_by_name(pool.values())
    return {
        "resolved": True,
        "reason": None,
        "rules": rules,
        "class_dependent": NEUTRAL in card_classes(card)
        and any(rule.get("class_rule") in {"own", "own_only"} for rule in rules),
        "cards": _sorted_options([option_row(base, item) for item in cards]),
    }


def _base_card_id(card_id: str) -> str:
    return re.sub(r"^core_", "", card_id, flags=re.IGNORECASE)


def choose_one_options(base: CardBase, card: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates = [str(card["id"]), _base_card_id(str(card["id"]))]
    copy_of = card.get("countAsCopyOfDbfId")
    if copy_of is not None and int(copy_of) in base.by_dbf:
        candidates.append(str(base.by_dbf[int(copy_of)]["id"]))
    for parent in dict.fromkeys(candidates):
        options = [
            base.by_id[child]
            for suffix in ("a", "b", "c", "A", "B", "C")
            if (child := f"{parent}{suffix}") in base.by_id
            and base.by_id[child].get("type") != "ENCHANTMENT"
        ]
        if not options:
            # Some cards name their options <id>t and <id>t2 (Boomkin).
            options = [
                base.by_id[child]
                for suffix in ("t", "t1", "t2")
                if (child := f"{parent}{suffix}") in base.by_id
                and base.by_id[child].get("type") == "SPELL"
            ]
            if len(options) != 2:
                options = []
        if options:
            return [option_row(base, option) for option in options]
    return []


def adapt_options(base: CardBase) -> list[dict[str, Any]]:
    options = [
        card
        for card in base.all
        if str(card["id"]).startswith(ADAPT_OPTION_PREFIX)
        and re.fullmatch(rf"{ADAPT_OPTION_PREFIX}\d+", str(card["id"]))
        and card.get("type") == "SPELL"
    ]
    return _sorted_options([option_row(base, card) for card in options])


def excavate_tiers(base: CardBase, card: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Treasures by tier; tier 4 is the class's own legendary treasure."""

    source = set(card_classes(card))
    tiers: dict[int, list[dict[str, Any]]] = {}
    for treasure in base.all:
        card_id = str(treasure["id"])
        if not card_id.startswith(EXCAVATE_TREASURE_PREFIXES):
            continue
        if treasure.get("collectible") or treasure.get("type") == "ENCHANTMENT":
            continue
        if not re.fullmatch(r"(WW_001t|DEEP_999t)\d*", card_id):
            continue
        tier = EXCAVATE_TIERS.get(str(treasure.get("rarity")))
        if tier is None:
            continue
        if (
            tier == 4
            and NEUTRAL not in source
            and not (set(card_classes(treasure)) & source)
        ):
            continue
        tiers.setdefault(tier, []).append(option_row(base, treasure))
    return [
        {"tier": tier, "cards": _sorted_options(cards)}
        for tier, cards in sorted(tiers.items())
    ]


def card_pool(
    base: CardBase, card: Mapping[str, Any], format_name: str
) -> dict[str, Any]:
    """The full option pool of one choice card in one format."""

    kind = choice_kind(card)
    if kind == "discover":
        return {"kind": kind, **discover_pool(base, card, format_name)}
    if kind == "adapt":
        return {
            "kind": kind,
            "resolved": True,
            "reason": None,
            "cards": adapt_options(base),
        }
    if kind == "choose_one":
        options = choose_one_options(base, card)
        return {
            "kind": kind,
            "resolved": bool(options),
            "reason": None if options else "unparsed",
            "cards": options,
        }
    if kind == "excavate":
        tiers = excavate_tiers(base, card)
        return {
            "kind": kind,
            "resolved": bool(tiers),
            "reason": None if tiers else "unparsed",
            "cards": [],
            "tiers": tiers,
        }
    if kind == "dredge":
        return {"kind": kind, "resolved": False, "reason": "game_state", "cards": []}
    return {"kind": None, "resolved": False, "reason": "no_choice", "cards": []}
