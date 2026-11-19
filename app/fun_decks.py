"""Fun / off-meta deck detector for hs-data-api.

Research notes (HSGuru/d0nkey.top + community meta-detectors):
- There is NO native "fun" tag. Fun decks are the ones that fail to match
  high-volume ladder archetypes (same idea as MetaDetector/Advisor outliers).
- d0nkey assigns names via card-package heuristics (Highlander, Odd/Even, Quest,
  Mill, Cute, Mecha'thun, joke packages). We approximate that with title cues +
  structural shape (highlanderish / XL) + distance from meta cores.
- Public meta surfaces use high min_games (~200); personal/streamer show everything.
  Low sample + off-meta package ≈ experimental / fun brew.
- Weak named ladder decks (Egglock, Leyline, Zee) are NOT fun — they are just
  low-tier established packages. Gate them when title≈nearest and overlap is mid+.

Detection recipe (concept-v8):
1. Build a CATALOG INDEX: every HSGuru list with enough games, per (format, class),
   plus each archetype's share of its format's games. An archetype with a share
   of at least ``HS_FUN_DECK_LADDER_SHARE_PCT`` (or a named ladder package) is a
   LADDER archetype.
2. Throw out ladder lists first, for both kinds and whatever the title or card
   package says: Jaccard >= ``HS_FUN_DECK_LADDER_MEMBER_SIM`` to a ladder list, a
   ladder-archetype title close to that archetype, a bare "<Aggro|Dude|...> <class>".
3. ``brew``: a fun signal - a concept in the title, a spicy card package or a list
   no other list resembles (``HS_FUN_DECK_TWIN_SIM``). ``off_meta``: no signal, but
   the list is a variant of a named archetype that is not a ladder one.
4. Quality gate: a deck needs a real sample (games, win rate), at least
   ``HS_FUN_DECK_MIN_GAMES`` games, and a 95% Wilson interval that is not entirely
   below ``HS_FUN_DECK_MIN_CI_HIGH`` percent (a losing list is not "fun").
5. Collapse near-identical lists (same format + class, Jaccard >= 0.6) into the
   better sampled one and publish with Standard/Wild balance: brews first (one deck
   per class first, then by fun score), off-meta decks after them.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import sqrt
from typing import Any

from .config import (
    fun_deck_dedupe_jaccard,
    fun_deck_ladder_member_sim,
    fun_deck_ladder_share_pct,
    fun_deck_min_ci_high,
    fun_deck_min_games,
    fun_deck_min_score,
    fun_deck_retention_hours,
    fun_deck_spicy_package_max_share_pct,
    fun_deck_twin_sim,
)
from .deck_decode import decode_deck_code
from .parser_control import load_resolved_public_dataset
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .sources import SOURCE_BY_ID, Source
from .storage import load_dataset, save_dataset, save_status

SOURCE_ID = "hsguru_fun_decks"
DETECTOR_VERSION = "concept-v8"
CANDIDATE_SOURCE_IDS = ("hsguru_streamer_decks_legend_1000",)
CATALOG_CANDIDATE_SOURCE_IDS = (
    "hsguru_deck_catalog_standard_all",
    "hsguru_deck_catalog_standard_legend",
    "hsguru_deck_catalog_wild_all",
    "hsguru_deck_catalog_wild_legend",
)
META_ARCHETYPE_SOURCE_IDS = (
    "hsreplay_meta_legend_1d_firecrawl",
    "hsreplay_meta_top_1000_legend_1d_firecrawl",
    "hsreplay_meta_diamond_4to1_1d_firecrawl",
    "hsguru_meta_standard_legend",
    "hsguru_meta_wild_legend",
)

# Mechanical / meme / cheese concepts (not "weak tier-4 ladder archetype").
# True meme/cheese cues. Deliberately excludes ladder-common words like
# "quest" / "thief" — those are often established meta archetype names.
_CONCEPT_TITLE_RE = re.compile(
    r"\b("
    r"mill|fatigue|freeze|secret|odd|even|highlander|\bhl\b|reno|exodia|mecha.?thun|"
    r"togwaggle|\btog\b|otk|cheese|meme|fun|joke|random|yogg|king.?krush|big.?priest|"
    r"handbuff|discard|questline|infinite|infinity|\bxl\b|steal|"
    r"rainbow|jank|spicy|brew|homebrew|contraband|clown|casino|switcheroo|"
    r"plague|tick.?tock|shudder|ashtoungue|ashtongue|rafaam|whizbang|ogre|"
    r"miracle.?chad|cute|colifero|amalgam|menagerie|boarlock|"
    r"combo.?otk|one.?turn|surprise|troll|kirin|kiri"
    r")\b",
    re.IGNORECASE,
)

# Named ladder / midrange packages that must NOT enter the fun feed even when
# they carry a Quest card or look "off" vs the wrong nearest neighbor.
_LADDER_ARCHETYPE_RE = re.compile(
    r"\b("
    r"thief\s+priest|soothsayer(?:\s+priest)?|hostage\s+rogue|"
    r"quest\s+(hunter|paladin|rogue|warrior|mage|shaman|priest|warlock|druid|"
    r"demon\s*hunter|dh|death\s*knight|dk)|"
    r"egg\s+(priest|warrior|warlock|lock|dk|paladin)|egglock|"
    r"leyline(?:\s+mage)?|zee(?:\s+shaman)?|harold\s+rogue|pure\s+paladin|"
    r"imbue\s+(?:dk|death\s*knight)|face\s+hunter|aggro\s+dk|"
    r"godfrey\s+warlock|wallow\s+warlock|elemental\s+mage|miracle\s+rogue|"
    r"dragon\s+druid|shudder\s+shaman|pirate\s+dh"
    r")\b",
    re.IGNORECASE,
)

# Generic ladder titles: "<style word(s)> <class>" and nothing else, e.g. "Aggro
# Paladin", "Secret Mage", "Dragon Mage", "Elemental Shaman", "Dude Paladin",
# "Big Spell Mage". HSGuru gives such a name to any list of that style, so it says
# nothing about a concept. "Secret" is a concept cue in other titles, but inside a
# bare "<word> <class>" title it is only a style label.
_GENERIC_DECK_WORDS = (
    r"aggro|midrange|control|tempo|face|burn|ramp|tokens?|zoo|secrets?|dragons?|"
    r"elementals?|dude|big\s+spell|spell|mech|beast|pirate|undead|blood|frost|unholy"
)
_CLASS_NAME_WORDS = (
    r"death\s*knight|dk|demon\s*hunter|dh|druid|hunter|mage|paladin|priest|rogue|"
    r"shaman|warlock|lock|warrior"
)
_GENERIC_LADDER_TITLE_RE = re.compile(
    rf"^(?:(?:{_GENERIC_DECK_WORDS})\s+){{1,2}}(?:{_CLASS_NAME_WORDS})$",
    re.IGNORECASE,
)

# Service prefixes HSGuru puts before the deck name ("STD BFU Handbuff DK").
_FORMAT_PREFIX_RE = re.compile(r"^(?:std|wld|wild)\b[\s:|/_\-\u2013\u2014]*", re.IGNORECASE)
# "Wild" is also the first word of real cards; those titles keep it.
_WILD_CARD_NAME_RE = re.compile(
    r"^wild\s+(?:growth|pyromancer|magic|gods|spirits|bloodstinger)\b", re.IGNORECASE
)

# 95% two-sided Wilson score interval.
_WILSON_Z = 1.96

# Packages that prove a brew; quest alone is too common on ladder.
_SPICY_PACKAGES = frozenset(
    {
        "reno_highlander",
        "odd_even",
        "mechathun",
        "togwaggle_mill",
        "cthun",
        "whizbang",
        "sathrovarr",
        "yogg_package",
        "steal_package",
    }
)
_NEUTRAL_PACKAGES = frozenset(
    {
        "quest_package",
        "patches",
        "galakrondish_finley",
        "nzothish_darkness",
        "highlander_curve",
        "xl_package",
    }
)

# "Egg *" can be fun off-class brew, but Egg Warrior is often ladder-adjacent.
_EGG_TITLE_RE = re.compile(r"\begg\b", re.IGNORECASE)
_EGG_LADDER_RE = re.compile(r"\begg\s+(warrior|warlock|lock)\b", re.IGNORECASE)

_POP_RE = re.compile(r"([\d.]+)\s*%")
_GAMES_RE = re.compile(r"\(([\d\s,.]+)\)")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


# Stable collectible markers for d0nkey-style package detection (dbfId).
_PACKAGE_MARKERS: dict[str, frozenset[int]] = {
    "reno_highlander": frozenset(
        {2883, 103471, 53756}
    ),  # Reno Jackson, Reno Lone Ranger, Zephrys
    "odd_even": frozenset({48158, 116160}),  # Baku, Genn
    "mechathun": frozenset({120231}),
    "togwaggle_mill": frozenset(
        {46589, 69830, 69847, 69869}
    ),  # Tog, Coldlight, Cho, Naturalize
    "cthun": frozenset({102680}),
    "whizbang": frozenset({50477}),
    "sathrovarr": frozenset({56189}),
    "patches": frozenset({109340}),
    "nzothish_darkness": frozenset({46454}),
    "galakrondish_finley": frozenset({2948}),
    # Steal/generate-from-opponent package (need 2+ hits; see detect_card_packages).
    "steal_core": frozenset(
        {
            47211,
            86539,
            121643,  # Tess Greymane
            52715,
            54816,
            111374,  # Madame Lazul
            59643,  # Mindrender Illucia
            77368,
            118073,  # Identity Theft
            50278,  # Seance
            38876,  # Shadowcaster
            40701,  # Inkmaster Solia
            60066,
            69566,  # Psychic Conjurer
        }
    ),
}

_package_quest_ids: frozenset[int] | None = None
_package_yogg_ids: frozenset[int] | None = None
_package_name_cache_ready = False


def _ensure_dynamic_package_ids() -> None:
    """Load QUEST/Yogg markers from HearthstoneJSON once (best-effort)."""
    global _package_quest_ids, _package_yogg_ids, _package_name_cache_ready
    if _package_name_cache_ready:
        return
    quest_ids: set[int] = set()
    yogg_ids: set[int] = set()
    try:
        from .cards_index import cards_by_dbfid

        for dbf_id, card in cards_by_dbfid().items():
            if not card.get("collectible"):
                continue
            mechs = card.get("mechanics") or []
            if any(m in {"QUEST", "QUESTLINE", "SIDEQUEST"} for m in mechs):
                quest_ids.add(int(dbf_id))
            name = str(card.get("name") or "")
            if re.search(r"yogg-?saron", name, re.IGNORECASE):
                yogg_ids.add(int(dbf_id))
    except Exception:
        quest_ids = set()
        yogg_ids = {77853}  # Acolyte of Yogg fallback only
    _package_quest_ids = frozenset(quest_ids)
    _package_yogg_ids = frozenset(yogg_ids)
    _package_name_cache_ready = True


def detect_card_packages(cards: Counter[int]) -> tuple[str, ...]:
    """Return package labels present in the decoded card multiset."""
    if not cards:
        return ()
    _ensure_dynamic_package_ids()
    present = set(cards)
    hits: list[str] = []
    for label, markers in _PACKAGE_MARKERS.items():
        overlap = present & markers
        if not overlap:
            continue
        if label == "steal_core":
            # One generate-card is common; two+ is a real steal package.
            if len(overlap) < 2:
                continue
            hits.append("steal_package")
            continue
        hits.append(label)
    if _package_quest_ids and present & _package_quest_ids:
        hits.append("quest_package")
    if _package_yogg_ids and present & _package_yogg_ids:
        hits.append("yogg_package")
    # Structural highlander without Reno still counts as package-ish.
    unique_cards = len(cards)
    total_cards = sum(cards.values())
    if total_cards >= 28 and unique_cards >= 26 and "reno_highlander" not in hits:
        hits.append("highlander_curve")
    if total_cards >= 39:
        hits.append("xl_package")
    return tuple(dict.fromkeys(hits))


@dataclass(frozen=True)
class FunDeckScore:
    fun_score: float
    max_meta_similarity: float
    nearest_archetype: str | None
    nearest_deck_code: str | None
    catalog_games: int | None
    archetype_popularity_pct: float | None
    reasons: tuple[str, ...]
    is_fun: bool
    concept_hit: bool = False
    rare_archetype: bool = False
    # "brew" | "off_meta"; None when the deck was turned away.
    kind: str | None = None
    ladder_sim: float = 0.0
    content_sim: float = 0.0
    twin_sim: float = 0.0
    content_archetype: str | None = None


def _parse_popularity_pct(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _POP_RE.search(str(value))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _parse_games(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value)
    nested = _GAMES_RE.search(text)
    if nested:
        text = nested.group(1)
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def _parse_win_rate(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", "."))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def deck_card_multiset(deck_code: str) -> Counter[int] | None:
    decoded = decode_deck_code(deck_code)
    if not decoded.get("ok"):
        return None
    counts: Counter[int] = Counter()
    for card in decoded.get("cards") or []:
        dbfid = card.get("dbfId") or card.get("dbf_id") or card.get("dbfid")
        if dbfid is None:
            continue
        try:
            counts[int(dbfid)] += int(card.get("count") or 1)
        except (TypeError, ValueError):
            continue
    return counts or None


def deck_hero_class(deck_code: str) -> str:
    decoded = decode_deck_code(deck_code)
    if not decoded.get("ok"):
        return ""
    hero = decoded.get("hero") or {}
    if not isinstance(hero, dict):
        return ""
    for key in ("cardClass", "card_class", "class", "player_class", "name"):
        value = hero.get(key)
        if value:
            return str(value).strip()
    return ""


def jaccard_similarity(left: Counter[int], right: Counter[int]) -> float:
    if not left or not right:
        return 0.0
    keys = set(left) | set(right)
    inter = sum(min(left[k], right[k]) for k in keys)
    union = sum(max(left[k], right[k]) for k in keys)
    return (inter / union) if union else 0.0


def _catalog_rows(source_id: str) -> list[dict[str, Any]]:
    dataset = load_dataset(source_id) or {}
    if source_id.startswith("hsguru_deck_catalog_"):
        from .hsguru_decks import current_hsguru_deck_period

        if str(dataset.get("period") or "") != current_hsguru_deck_period():
            return []
    data = dataset.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("rows", "decks", "items"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        structured = data.get("structured")
        if isinstance(structured, dict):
            for key in ("rows", "decks", "items"):
                rows = structured.get(key)
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
    return []


def _streamer_rows(source_id: str) -> list[dict[str, Any]]:
    dataset = load_dataset(source_id) or {}
    data = dataset.get("data") or {}
    if not isinstance(data, dict):
        return []
    structured = data.get("structured") or {}
    if isinstance(structured, dict) and isinstance(structured.get("rows"), list):
        return [row for row in structured["rows"] if isinstance(row, dict)]
    tables = data.get("tables") or []
    if tables and isinstance(tables[0], dict):
        objects = tables[0].get("objects")
        if isinstance(objects, list):
            return [row for row in objects if isinstance(row, dict)]
    return []


def _format_bucket(value: Any) -> str:
    text = str(value or "").casefold()
    if "wild" in text or text == "1":
        return "wild"
    if "standard" in text or text == "2":
        return "standard"
    return "other"


# A list needs this many games to be compared against (below it the sample is noise).
CATALOG_MIN_GAMES = 45


@dataclass(frozen=True)
class CatalogDeck:
    """One HSGuru list of the catalog, as the detector compares candidates to it."""

    deck_code: str
    cards: Counter[int]
    # Casefolded archetype name without the service prefix; "" when the list has none.
    archetype: str
    label: str
    hero_class: str
    format: str
    games: int


def _class_key(value: Any) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").casefold())


class CatalogIndex:
    """Every compared catalog list by (format, class) and each archetype's share.

    ``archetype_games`` / ``format_games`` count every listed deck (also the ones
    below ``CATALOG_MIN_GAMES``); they default to the sums over ``decks``.
    """

    def __init__(
        self,
        decks: Iterable[CatalogDeck],
        *,
        archetype_games: Mapping[tuple[str, str], int] | None = None,
        format_games: Mapping[str, int] | None = None,
        meta_popularity: Mapping[tuple[str, str, str], float] | None = None,
        package_shares: Mapping[tuple[str, str], float] | None = None,
    ) -> None:
        self.decks = list(decks)
        self.meta_popularity = dict(meta_popularity or {})
        self._by_group: dict[tuple[str, str], list[CatalogDeck]] = defaultdict(list)
        derived_arch: dict[tuple[str, str], int] = defaultdict(int)
        derived_fmt: dict[str, int] = defaultdict(int)
        for deck in self.decks:
            self._by_group[(deck.format, deck.hero_class)].append(deck)
            derived_arch[(deck.format, deck.archetype)] += deck.games
            derived_fmt[deck.format] += deck.games
        self.archetype_games = dict(archetype_games or derived_arch)
        self.format_games = dict(format_games or derived_fmt)
        self._share_floor = fun_deck_ladder_share_pct()
        self._spicy_share_cap = fun_deck_spicy_package_max_share_pct()
        # Archetypes the meta sources (HSReplay, HSGuru) rate as popular, by (format, class).
        self._meta_by_group: dict[tuple[str, str], list[str]] = defaultdict(list)
        for (fmt, class_key, name), pct in (meta_popularity or {}).items():
            if pct >= _META_LADDER_POPULARITY_PCT:
                self._meta_by_group[(fmt, class_key)].append(name)
        self._ladder_cache: dict[tuple[str, str, str], bool] = {}
        self.ladder_names: frozenset[str] = frozenset(
            {
                name
                for fmt, name in self.archetype_games
                if name and self.is_ladder_archetype(fmt, name)
            }
            | {
                deck.archetype
                for deck in self.decks
                if deck.archetype
                and self.is_ladder_archetype(deck.format, deck.archetype, deck.hero_class)
            }
        )
        # Games of the lists carrying any marker of a spicy package, per format.
        package_games: dict[tuple[str, str], int] = defaultdict(int)
        compared_games: dict[str, int] = defaultdict(int)
        for deck in self.decks:
            compared_games[deck.format] += deck.games
            for label in detect_card_packages(deck.cards):
                if label in _SPICY_PACKAGES:
                    package_games[(deck.format, label)] += deck.games
        self._package_games = dict(package_games)
        self._compared_games = dict(compared_games)
        # Shares measured on a whole catalog, when `decks` is only a slice of it.
        self._package_shares = dict(package_shares) if package_shares is not None else None

    def neighbours(self, fmt: str, hero_class: str) -> list[CatalogDeck]:
        """Compared lists of one format and class (every format when ``fmt`` is "other")."""
        class_key = _class_key(hero_class)
        if fmt in {"standard", "wild"}:
            return self._by_group.get((fmt, class_key), [])
        return [d for (f, c), decks in self._by_group.items() if c == class_key for d in decks]

    def archetype_share_pct(self, fmt: str, name: str) -> float:
        total = self.format_games.get(fmt, 0)
        if not total or not name:
            return 0.0
        return round(100.0 * self.archetype_games.get((fmt, name), 0) / total, 4)

    def is_ladder_archetype(self, fmt: str, name: str, hero_class: str = "") -> bool:
        """A big archetype (share >= the ladder floor), a named ladder package, or one
        the meta sources rate as popular for the same format and class."""
        if not name:
            return False
        class_key = _class_key(hero_class)
        key = (fmt, name, class_key)
        cached = self._ladder_cache.get(key)
        if cached is None:
            cached = (
                _ladder_archetype_name(name)
                or self.archetype_share_pct(fmt, name) >= self._share_floor
                or (
                    bool(class_key)
                    and any(
                        _archetypes_align(name, meta_name)
                        for meta_name in self._meta_by_group.get((fmt, class_key), ())
                    )
                )
            )
            self._ladder_cache[key] = cached
        return cached

    def package_share_pct(self, fmt: str, label: str) -> float:
        """Share (percent) of the compared games of a format played with a package."""
        if self._package_shares is not None:
            return self._package_shares.get((fmt, label), 0.0)
        total = self._compared_games.get(fmt, 0)
        if not total:
            return 0.0
        return round(100.0 * self._package_games.get((fmt, label), 0) / total, 4)

    def is_neutral_package(self, fmt: str, label: str) -> bool:
        """The format plays the package a lot: it is meta there, not a fun signal."""
        return self.package_share_pct(fmt, label) >= self._spicy_share_cap

    def sharp_packages(self, fmt: str, packages: tuple[str, ...]) -> tuple[str, ...]:
        """The spicy packages that are still rare in the format (a fun signal)."""
        return tuple(
            label
            for label in packages
            if label in _SPICY_PACKAGES
            and self.package_share_pct(fmt, label) < self._spicy_share_cap
        )


# Meta sources: HSReplay / HSGuru meta tables, by the format they describe.
_META_SOURCE_FORMAT = {
    "hsreplay_meta_legend_1d_firecrawl": "standard",
    "hsreplay_meta_top_1000_legend_1d_firecrawl": "standard",
    "hsreplay_meta_diamond_4to1_1d_firecrawl": "standard",
    "hsguru_meta_standard_legend": "standard",
    "hsguru_meta_wild_legend": "wild",
}
# An archetype at least this popular (percent) in a meta source is a ladder archetype.
_META_LADDER_POPULARITY_PCT = 1.0
_TITLE_CLASS_SUFFIX = {
    "dk": "deathknight",
    "deathknight": "deathknight",
    "dh": "demonhunter",
    "demonhunter": "demonhunter",
    "lock": "warlock",
    "warlock": "warlock",
    "druid": "druid",
    "hunter": "hunter",
    "mage": "mage",
    "paladin": "paladin",
    "priest": "priest",
    "rogue": "rogue",
    "shaman": "shaman",
    "warrior": "warrior",
}


def build_meta_popularity(
    source_ids: tuple[str, ...] = META_ARCHETYPE_SOURCE_IDS,
) -> dict[tuple[str, str, str], float]:
    """Archetype popularity (percent) by (format, class, casefolded name) from the meta sources.

    HSReplay tables list archetypes per class; HSGuru tables have no class column, so
    the class is read from the last word of the name ("Harold DK") and rows without
    one are skipped. The highest value of a name wins.
    """
    popularity: dict[tuple[str, str, str], float] = {}

    def add(fmt: str, class_key: str, name: str, pct: float | None) -> None:
        if not class_key or not name or pct is None:
            return
        key = (fmt, class_key, name.strip().casefold())
        popularity[key] = max(popularity.get(key, 0.0), pct)

    for source_id in source_ids:
        fmt = _META_SOURCE_FORMAT.get(source_id)
        if fmt is None:
            continue
        data = (load_resolved_public_dataset(source_id) or {}).get("data") or {}
        structured = data.get("structured") if isinstance(data, dict) else None
        if not isinstance(structured, dict):
            continue
        for class_row in structured.get("classes") or []:
            if not isinstance(class_row, dict):
                continue
            class_key = _class_key(class_row.get("class"))
            for arch in class_row.get("archetypes") or []:
                if isinstance(arch, dict):
                    pct = _parse_popularity_pct(arch.get("popularity") or arch.get("raw_popularity"))
                    add(fmt, class_key, str(arch.get("archetype") or ""), pct)
        for row in structured.get("strategies") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Archetype") or row.get("archetype") or "")
            words = name.split()
            class_key = _TITLE_CLASS_SUFFIX.get(_class_key(words[-1])) if words else None
            pct = _parse_popularity_pct(row.get("Popularity") or row.get("popularity"))
            add(fmt, class_key or "", name, pct)
    return popularity


def build_catalog_index(
    catalog_source_ids: tuple[str, ...] = CATALOG_CANDIDATE_SOURCE_IDS,
    *,
    meta_popularity: Mapping[tuple[str, str, str], float] | None = None,
) -> CatalogIndex:
    """Index the whole HSGuru catalog (not only its top decks) for the detector.

    A list listed by several catalogs counts once, with its largest game count.
    """
    best: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    for source_id in catalog_source_ids:
        for row in _catalog_rows(source_id):
            code = str(row.get("deck_code") or "").strip()
            fmt = _format_bucket(row.get("format"))
            games = _parse_games(row.get("games")) or 0
            if not code or fmt == "other" or games <= 0:
                continue
            key = (fmt, code)
            if key not in best or games > best[key][0]:
                best[key] = (games, row)

    decks: list[CatalogDeck] = []
    archetype_games: dict[tuple[str, str], int] = defaultdict(int)
    format_games: dict[str, int] = defaultdict(int)
    for (fmt, code), (games, row) in best.items():
        label = strip_format_prefix(str(row.get("archetype") or row.get("title") or "")).strip()
        archetype_games[(fmt, label.casefold())] += games
        format_games[fmt] += games
        if games < CATALOG_MIN_GAMES:
            continue
        cards = deck_card_multiset(code)
        if not cards:
            continue
        decks.append(
            CatalogDeck(
                deck_code=code,
                cards=cards,
                archetype=label.casefold(),
                label=label,
                hero_class=_class_key(row.get("class") or deck_hero_class(code)),
                format=fmt,
                games=games,
            )
        )
    return CatalogIndex(
        decks,
        archetype_games=archetype_games,
        format_games=format_games,
        meta_popularity=build_meta_popularity() if meta_popularity is None else meta_popularity,
    )


@dataclass(frozen=True)
class Neighbourhood:
    """How a candidate relates to the catalog lists of its format and class."""

    ladder_sim: float = 0.0
    ladder_archetype: str | None = None
    ladder_deck_code: str | None = None
    # Closest list with a named archetype.
    content_sim: float = 0.0
    content_archetype: str | None = None
    content_label: str | None = None
    # Closest list of any kind.
    twin_sim: float = 0.0
    # The title names a ladder archetype; how close the lists of that name are.
    title_ladder: bool = False
    title_sim: float = 0.0


def analyze_neighbourhood(
    index: CatalogIndex,
    *,
    deck_code: str,
    title: str,
    fmt: str,
    hero_class: str,
    cards: Counter[int],
) -> Neighbourhood:
    title_key = strip_format_prefix(title).casefold().strip()
    ladder_sim = content_sim = twin_sim = title_sim = 0.0
    ladder_deck: CatalogDeck | None = None
    content_deck: CatalogDeck | None = None
    aligned_cache: dict[str, bool] = {}
    title_aligned_ladder = False
    for deck in index.neighbours(fmt, hero_class):
        if deck.deck_code == deck_code:
            continue
        sim = jaccard_similarity(cards, deck.cards)
        twin_sim = max(twin_sim, sim)
        is_ladder = index.is_ladder_archetype(deck.format, deck.archetype, deck.hero_class)
        if is_ladder and sim > ladder_sim:
            ladder_sim, ladder_deck = sim, deck
        if deck.archetype and sim > content_sim:
            content_sim, content_deck = sim, deck
        if deck.archetype and title_key:
            aligned = aligned_cache.get(deck.archetype)
            if aligned is None:
                aligned = aligned_cache[deck.archetype] = _archetypes_align(
                    title_key, deck.archetype
                )
            if aligned:
                title_sim = max(title_sim, sim)
                title_aligned_ladder = title_aligned_ladder or is_ladder
    return Neighbourhood(
        ladder_sim=ladder_sim,
        ladder_archetype=ladder_deck.label if ladder_deck else None,
        ladder_deck_code=ladder_deck.deck_code if ladder_deck else None,
        content_sim=content_sim,
        content_archetype=content_deck.archetype if content_deck else None,
        content_label=content_deck.label if content_deck else None,
        twin_sim=twin_sim,
        title_ladder=_ladder_archetype_name(title_key) or title_aligned_ladder,
        title_sim=title_sim,
    )


def build_archetype_popularity(
    source_ids: tuple[str, ...] = META_ARCHETYPE_SOURCE_IDS,
) -> dict[str, float]:
    popularity: dict[str, float] = {}
    for source_id in source_ids:
        dataset = load_resolved_public_dataset(source_id) or {}
        data = dataset.get("data") or {}
        if not isinstance(data, dict):
            continue
        structured = data.get("structured") or {}
        if not isinstance(structured, dict):
            continue
        for class_row in structured.get("classes") or []:
            if not isinstance(class_row, dict):
                continue
            for arch in class_row.get("archetypes") or []:
                if not isinstance(arch, dict):
                    continue
                name = str(arch.get("archetype") or "").strip().casefold()
                pct = _parse_popularity_pct(
                    arch.get("popularity") or arch.get("raw_popularity")
                )
                if name and pct is not None:
                    popularity[name] = max(popularity.get(name, 0.0), pct)
        for row in structured.get("strategies") or []:
            if not isinstance(row, dict):
                continue
            name = (
                str(row.get("Archetype") or row.get("archetype") or "")
                .strip()
                .casefold()
            )
            pct = _parse_popularity_pct(row.get("Popularity") or row.get("popularity"))
            if name and pct is not None:
                popularity[name] = max(popularity.get(name, 0.0), pct)
    return popularity


def _name_tokens(value: str) -> set[str]:
    stop = {"the", "a", "an", "of", "and", "deck", "hs", "xl", "hl", "std", "bu", "buu"}
    return {
        token
        for token in _TOKEN_RE.findall((value or "").casefold())
        if len(token) > 2 and token not in stop
    }


def _names_align(left: str, right: str) -> bool:
    if not left or not right:
        return False
    a = left.casefold().strip()
    b = right.casefold().strip()
    if a == b or a in b or b in a:
        return True
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    return (len(ta & tb) / max(1, min(len(ta), len(tb)))) >= 0.67


_CLASS_TOKENS = frozenset(
    {
        "death", "knight", "demon", "hunter", "druid", "mage", "paladin", "priest",
        "rogue", "shaman", "warlock", "lock", "warrior",
    }
)  # fmt: skip


def _distinctive_tokens(value: str) -> set[str]:
    return _name_tokens(value) - _CLASS_TOKENS


def _archetypes_align(left: str, right: str) -> bool:
    """Same archetype by its distinctive words, ignoring the class word and prefixes.

    A bare class name ("Priest", "XL Shaman") has no distinctive word and aligns with
    nothing, so it cannot make every title of the class a ladder title.
    """
    a, b = _distinctive_tokens(left), _distinctive_tokens(right)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.67


def _is_known_name(name: str, known: set[str]) -> bool:
    if not name:
        return False
    key = name.casefold().strip()
    if key in known:
        return True
    return any(_names_align(key, other) for other in known)


def _ladder_archetype_name(title: str) -> bool:
    """True for established named ladder packages (Thief Priest, Soothsayer, …)."""
    return bool(_LADDER_ARCHETYPE_RE.search(title or ""))


def _spicy_packages(packages: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(label for label in packages if label in _SPICY_PACKAGES)


# Concept words that only name a card package; backed by a package the format plays a lot,
# such a word is no concept (Yogg in Standard).
_WORD_PACKAGE = {
    "yogg": "yogg_package",
    "reno": "reno_highlander",
    "highlander": "reno_highlander",
    "hl": "reno_highlander",
    "odd": "odd_even",
    "even": "odd_even",
    "tog": "togwaggle_mill",
    "togwaggle": "togwaggle_mill",
    "mill": "togwaggle_mill",
    "mechathun": "mechathun",
}


def _concept_words(title: str) -> list[str]:
    """The concept words of a title, casefolded; none for a named ladder package."""
    if _ladder_archetype_name(title) or _EGG_LADDER_RE.search(title or ""):
        return []
    words = [re.sub(r"[^a-z0-9]", "", m.group(1).casefold()) for m in _CONCEPT_TITLE_RE.finditer(title or "")]
    if _EGG_TITLE_RE.search(title or ""):
        words.append("egg")
    return words


def _concept_hit(title: str) -> bool:
    return bool(_concept_words(title))


def strip_format_prefix(title: str) -> str:
    """Drop service format prefixes ("STD", "WLD", "Wild") from the start of a title."""
    text = (title or "").strip()
    while not _WILD_CARD_NAME_RE.match(text):
        stripped = _FORMAT_PREFIX_RE.sub("", text, count=1).strip()
        if not stripped or stripped == text:
            break
        text = stripped
    return text


# Service prefixes of a title that say nothing about the deck: XL / HL / Highlander and the
# rune letters of a Death Knight list (BBU, FFU, BFU...).
_SERVICE_TITLE_PREFIX_RE = re.compile(r"^(?:(?:xl|hl|highlander|[bfu]{3})\b\s*)+", re.IGNORECASE)


def _generic_ladder_title(title: str) -> bool:
    """True for a bare "<Aggro|Dragon|Dude|...> <class>" name (service prefixes ignored)."""
    text = strip_format_prefix(title)
    return bool(
        _GENERIC_LADDER_TITLE_RE.match(text)
        or _GENERIC_LADDER_TITLE_RE.match(_SERVICE_TITLE_PREFIX_RE.sub("", text, count=1).strip())
    )


def wilson_interval(win_rate_pct: float, games: int) -> tuple[float, float]:
    """95% Wilson score interval for a win rate, in percent (unrounded).

    ``win_rate_pct`` is a percent in 0..100 and ``games`` the sample size. The
    interval always contains the win rate and narrows as ``games`` grows.
    """
    if games <= 0:
        raise ValueError("games must be positive")
    p = min(1.0, max(0.0, win_rate_pct / 100.0))
    z2 = _WILSON_Z * _WILSON_Z
    denominator = 1.0 + z2 / games
    center = (p + z2 / (2.0 * games)) / denominator
    margin = (
        _WILSON_Z * sqrt(p * (1.0 - p) / games + z2 / (4.0 * games * games)) / denominator
    )
    return (
        max(0.0, min(center - margin, p)) * 100.0,
        min(1.0, max(center + margin, p)) * 100.0,
    )


@dataclass(frozen=True)
class SampleVerdict:
    """Outcome of the sample / win-rate quality gate for one deck."""

    ok: bool
    # None when ok; else "missing_stats", "too_few_games" or "losing_win_rate".
    reject: str | None = None
    ci_low: float | None = None
    ci_high: float | None = None


def evaluate_sample(
    games: Any,
    win_rate: Any,
    *,
    min_games: int | None = None,
    min_ci_high: float | None = None,
) -> SampleVerdict:
    """Quality gate: real sample, enough games, win rate not provably losing.

    ``ci_low``/``ci_high`` are percents rounded to one decimal and always satisfy
    ``ci_low <= win_rate <= ci_high``. The ``min_ci_high`` cut is applied to the
    unrounded upper bound.
    """
    games_n = _parse_games(games)
    rate = _parse_win_rate(win_rate)
    if games_n is None or games_n <= 0 or rate is None or not 0.0 <= rate <= 100.0:
        return SampleVerdict(ok=False, reject="missing_stats")
    low, high = wilson_interval(rate, games_n)
    ci_low = min(round(low, 1), rate)
    ci_high = max(round(high, 1), rate)
    floor_games = fun_deck_min_games() if min_games is None else min_games
    floor_high = fun_deck_min_ci_high() if min_ci_high is None else min_ci_high
    if games_n < floor_games:
        return SampleVerdict(False, "too_few_games", ci_low, ci_high)
    if high < floor_high:
        return SampleVerdict(False, "losing_win_rate", ci_low, ci_high)
    return SampleVerdict(True, None, ci_low, ci_high)


# Weights of the fun score; they add up to 1.0, so the score has no ceiling effect.
SCORE_WEIGHTS = {
    "ladder_distance": 0.40,
    "concept": 0.20,
    "package": 0.20,
    "uniqueness": 0.10,
    "meme_or_cheese": 0.10,
}
# The title names a ladder archetype and the list is at least this close to its lists.
_LADDER_TITLE_SIM = 0.35
# An off-meta deck resembles a list of a named (non-ladder) archetype at least this much.
_OFF_META_CONTENT_SIM = 0.55
# "far_from_ladder" is reported below this similarity to the ladder lists.
_FAR_FROM_LADDER_SIM = 0.30


def _package_weight(spicy: tuple[str, ...]) -> float:
    """0.12 for one spicy package, 0.04 more for each further one, at most 0.20."""
    if not spicy:
        return 0.0
    return min(SCORE_WEIGHTS["package"], 0.12 + 0.04 * (len(spicy) - 1))


def score_fun_deck(
    *,
    deck_code: str,
    title: str = "",
    format_name: str = "",
    cards: Counter[int] | None = None,
    hero_class: str = "",
    win_rate: float | None = None,
    catalog_games_hint: int | None = None,
    index: CatalogIndex,
    min_score: float | None = None,
    ladder_member_sim: float | None = None,
    twin_sim: float | None = None,
) -> FunDeckScore:
    """Classify one candidate: turn it away (a gate) or call it a brew / off-meta deck."""
    threshold = fun_deck_min_score() if min_score is None else min_score
    member_cap = fun_deck_ladder_member_sim() if ladder_member_sim is None else ladder_member_sim
    twin_cap = fun_deck_twin_sim() if twin_sim is None else twin_sim
    bag = cards or deck_card_multiset(deck_code)
    if not bag:
        return FunDeckScore(
            fun_score=0.0,
            max_meta_similarity=0.0,
            nearest_archetype=None,
            nearest_deck_code=None,
            catalog_games=None,
            archetype_popularity_pct=None,
            reasons=("undecodable_deck",),
            is_fun=False,
        )

    fmt = _format_bucket(format_name)
    title_name = (title or "").strip()
    hood = analyze_neighbourhood(
        index,
        deck_code=deck_code,
        title=title_name,
        fmt=fmt,
        hero_class=hero_class,
        cards=bag,
    )
    packages = detect_card_packages(bag)
    # A package that much of the format plays is meta, not a concept.
    spicy = index.sharp_packages(fmt, _spicy_packages(packages))
    # A word backed by a package that much of the format plays is no concept.
    title_concept = any(
        not (label := _WORD_PACKAGE.get(word)) or not index.is_neutral_package(fmt, label)
        for word in _concept_words(title_name)
    )
    concept = title_concept or bool(spicy)

    # 1. Ladder lists are turned away first, whatever concept words or packages say.
    gates: list[str] = []
    if hood.ladder_sim >= member_cap:
        gates.append("ladder_member_gate")
    if hood.title_ladder and hood.title_sim >= _LADDER_TITLE_SIM:
        gates.append("ladder_title_gate")
    # "Aggro Paladin", "Dude Paladin": a bare style + class name says nothing about a
    # concept, and a style is not a forgotten archetype either.
    if _generic_ladder_title(title_name) and not spicy:
        gates.append("generic_ladder_gate")

    # 2-3. The kind: a fun signal makes a brew, a variant of a rare named archetype an
    # off-meta deck; anything else is not "fun".
    # A list that is a member of its own named archetype is a variant, not unique.
    unique_list = hood.twin_sim < twin_cap and hood.content_sim < _OFF_META_CONTENT_SIM
    kind: str | None = None
    if not gates:
        if concept or unique_list:
            kind = "brew"
        elif (
            hood.content_archetype
            and hood.content_sim >= _OFF_META_CONTENT_SIM
            and not index.is_ladder_archetype(fmt, hood.content_archetype, hero_class)
        ):
            kind = "off_meta"
        else:
            gates.append("needs_fun_signal_gate")

    reasons: list[str] = []
    score = SCORE_WEIGHTS["ladder_distance"] * (1.0 - hood.ladder_sim)
    if hood.ladder_sim < _FAR_FROM_LADDER_SIM:
        reasons.append("far_from_ladder")
    if title_concept:
        score += SCORE_WEIGHTS["concept"]
        reasons.append("concept_title")
    if spicy:
        score += _package_weight(spicy)
        reasons.append("card_package")
        reasons.extend(f"pkg:{label}" for label in spicy[:4])
    elif packages:
        # Neutral packages (quest/xl) are logged but do not count.
        reasons.append("neutral_package")
        reasons.extend(f"meta_pkg:{label}" for label in packages[:3])
    score += SCORE_WEIGHTS["uniqueness"] * (1.0 - hood.twin_sim)
    if unique_list:
        reasons.append("unique_list")

    # Cheese / dying meme winrate bands on low samples.
    if win_rate is not None and catalog_games_hint is not None and catalog_games_hint <= 120:
        if win_rate <= 42 and concept:
            score += SCORE_WEIGHTS["meme_or_cheese"]
            reasons.append("meme_low_winrate")
        elif win_rate >= 68 and concept:
            score += SCORE_WEIGHTS["meme_or_cheese"]
            reasons.append("cheese_high_winrate")
    score = round(score, 4)

    reasons.extend(gates)
    if kind == "brew" and score < threshold:
        reasons.append("below_min_score")
    is_fun = kind == "off_meta" or (kind == "brew" and score >= threshold)
    content_archetype = hood.content_archetype
    return FunDeckScore(
        fun_score=score,
        max_meta_similarity=round(hood.ladder_sim, 4),
        nearest_archetype=hood.ladder_archetype,
        nearest_deck_code=hood.ladder_deck_code,
        catalog_games=catalog_games_hint,
        archetype_popularity_pct=(
            index.archetype_share_pct(fmt, content_archetype) if content_archetype else None
        ),
        reasons=tuple(dict.fromkeys(reasons)),
        is_fun=is_fun,
        concept_hit=concept,
        rare_archetype=kind == "off_meta",
        kind=kind,
        ladder_sim=round(hood.ladder_sim, 4),
        content_sim=round(hood.content_sim, 4),
        twin_sim=round(hood.twin_sim, 4),
        content_archetype=content_archetype,
    )


def _candidate_rows(
    *,
    known_ladder: Iterable[str],
    format_focus: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # (format, code): HSGuru lists one code in both formats' catalogs.
    seen_codes: set[tuple[str, str]] = set()
    known_names = set(known_ladder)
    focus = _format_bucket(format_focus) if format_focus else None
    if focus == "other":
        focus = None

    for source_id in CANDIDATE_SOURCE_IDS:
        for row in _streamer_rows(source_id):
            code = str(row.get("deck_code") or "").strip()
            row_fmt = _format_bucket(row.get("Format") or row.get("format"))
            if not code or (row_fmt, code) in seen_codes:
                continue
            if focus and row_fmt not in {focus, "other"}:
                continue
            seen_codes.add((row_fmt, code))
            enriched = dict(row)
            enriched["_candidate_source_id"] = source_id
            rows.append(enriched)

    # Catalog hunt budgets. Standard-focused refresh digs deeper into Standard.
    if focus == "standard":
        catalog_budget = {"standard": 420, "wild": 0}
    elif focus == "wild":
        catalog_budget = {"standard": 0, "wild": 360}
    else:
        catalog_budget = {"standard": 260, "wild": 220}
    catalog_kept = {"standard": 0, "wild": 0}
    for source_id in CATALOG_CANDIDATE_SOURCE_IDS:
        if focus == "standard" and "wild" in source_id:
            continue
        if focus == "wild" and "standard" in source_id:
            continue
        ranked = sorted(
            _catalog_rows(source_id),
            key=lambda row: int(_parse_games(row.get("games")) or 10**9),
        )
        for row in ranked:
            code = str(row.get("deck_code") or "").strip()
            bucket = _format_bucket(row.get("format"))
            if not code or (bucket, code) in seen_codes:
                continue
            games = _parse_games(row.get("games")) or 0
            if games < 45 or games > 220:
                continue
            title = str(row.get("title") or row.get("archetype") or "").strip()
            if bucket not in catalog_budget:
                continue
            if catalog_kept[bucket] >= catalog_budget[bucket]:
                continue
            concept = _concept_hit(title)
            known = _is_known_name(strip_format_prefix(title), known_names)
            # Keep rare / concept brews; skip ladder-archetype names.
            if known and not concept:
                continue
            if not concept and games > 140:
                continue
            seen_codes.add((bucket, code))
            catalog_kept[bucket] += 1
            rows.append(
                {
                    "Deck": title,
                    "title": title,
                    "archetype": row.get("archetype") or title,
                    "Format": row.get("format"),
                    "format": row.get("format"),
                    "class": row.get("class"),
                    "deck_code": code,
                    "games": games,
                    "win_rate": row.get("win_rate"),
                    "url": row.get("url"),
                    "_candidate_source_id": source_id,
                }
            )
    return rows


def _deck_title(row: dict[str, Any]) -> str:
    raw = str(row.get("Deck") or row.get("title") or row.get("archetype") or "").strip()
    raw = re.sub(r"^#+\s*", "", raw)
    raw = re.split(r"\s+AAE[A-Za-z0-9+/=]{10,}", raw, maxsplit=1)[0]
    raw = re.split(r"\s+#\s+", raw, maxsplit=1)[0]
    return raw.strip()


def _normalize_fun_row(
    row: dict[str, Any],
    scored: FunDeckScore,
    *,
    seen_at: str,
    sample: SampleVerdict | None = None,
) -> dict[str, Any]:
    code = str(row.get("deck_code") or "").strip()
    source_title = _deck_title(row)
    verdict = sample or evaluate_sample(row.get("games"), row.get("win_rate"))
    return {
        "deck_code": code,
        "title": strip_format_prefix(source_title) or source_title,
        "source_title": source_title,
        "streamer": row.get("Streamer") or row.get("streamer"),
        "format": row.get("Format") or row.get("format"),
        "class": row.get("class"),
        "peak": row.get("Peak") or row.get("peak"),
        "latest": row.get("Latest") or row.get("latest"),
        "worst": row.get("Worst") or row.get("worst"),
        "record": row.get("Win - Loss") or row.get("record"),
        "last_played": row.get("Last Played") or row.get("last_played"),
        "links": row.get("Links") or row.get("links"),
        "games": row.get("games"),
        "win_rate": row.get("win_rate"),
        "win_rate_ci_low": verdict.ci_low,
        "win_rate_ci_high": verdict.ci_high,
        "variants": 0,
        "url": row.get("url"),
        "candidate_source_id": row.get("_candidate_source_id"),
        "fun_score": scored.fun_score,
        "max_meta_similarity": scored.max_meta_similarity,
        "nearest_archetype": scored.nearest_archetype,
        "nearest_deck_code": scored.nearest_deck_code,
        "catalog_games": scored.catalog_games,
        "archetype_popularity_pct": scored.archetype_popularity_pct,
        "concept_hit": scored.concept_hit,
        "rare_archetype": scored.rare_archetype,
        "kind": scored.kind or "brew",
        "ladder_sim": scored.ladder_sim,
        "content_sim": scored.content_sim,
        "twin_sim": scored.twin_sim,
        "content_archetype": scored.content_archetype,
        "reasons": list(scored.reasons),
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
    }


def _merge_history(
    previous_rows: list[dict[str, Any]],
    fresh_rows: list[dict[str, Any]],
    *,
    retention_hours: int,
    now: datetime,
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(hours=max(1, retention_hours))
    by_code: dict[tuple[str, str], dict[str, Any]] = {}

    def _parse_ts(value: Any) -> datetime | None:
        if not value:
            return None
        text = str(value).replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    for row in previous_rows:
        code = str(row.get("deck_code") or "").strip()
        if not code:
            continue
        key = (_format_bucket(row.get("format")), code)
        last_seen = _parse_ts(row.get("last_seen_at") or row.get("first_seen_at"))
        if last_seen is not None and last_seen < cutoff:
            continue
        title = str(row.get("title") or "")
        if _ladder_archetype_name(title):
            pkgs = tuple(
                str(x)[4:]
                for x in (row.get("reasons") or [])
                if str(x).startswith("pkg:")
            )
            meme_twist = bool(
                re.search(r"\b(xl|hl|reno|mill|yogg)\b", title, re.IGNORECASE)
            )
            if not (meme_twist and _spicy_packages(pkgs)):
                continue
        by_code[key] = dict(row)

    for row in fresh_rows:
        code = str(row.get("deck_code") or "").strip()
        if not code:
            continue
        key = (_format_bucket(row.get("format")), code)
        prev = by_code.get(key)
        if prev:
            merged = dict(prev)
            merged.update(row)
            merged["first_seen_at"] = prev.get("first_seen_at") or row.get(
                "first_seen_at"
            )
            merged["last_seen_at"] = row.get("last_seen_at") or prev.get("last_seen_at")
            if float(prev.get("fun_score") or 0) > float(row.get("fun_score") or 0):
                for key in (
                    "fun_score",
                    "max_meta_similarity",
                    "nearest_archetype",
                    "nearest_deck_code",
                    "catalog_games",
                    "archetype_popularity_pct",
                    "reasons",
                    "concept_hit",
                    "rare_archetype",
                    "kind",
                    "ladder_sim",
                    "content_sim",
                    "twin_sim",
                    "content_archetype",
                ):
                    merged[key] = prev.get(key)
            by_code[key] = merged
        else:
            by_code[key] = dict(row)
    return list(by_code.values())


def _is_xl_hl_title(title: str) -> bool:
    text = (title or "").casefold()
    return "xl" in text or "highlander" in text or bool(re.search(r"\bhl\b", text))


def _group_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        _format_bucket(row.get("format")),
        str(row.get("class") or "unknown").casefold(),
    )


def dedupe_near_duplicates(
    rows: list[dict[str, Any]],
    cards_by_code: Mapping[str, Counter[int]],
    *,
    threshold: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Keep one deck out of every set of near-identical lists.

    Two decks of the same format and class whose card multisets have a Jaccard
    similarity >= ``threshold`` are one list. The deck with more ``games`` stays
    (ties: higher ``fun_score``, then ``deck_code``); an absorbed deck adds one
    plus its own ``variants`` to the survivor's ``variants``. A deck absorbed by
    several survivors goes to the most similar one. Rows without cards in
    ``cards_by_code`` are never merged. Returns the survivors in input order and
    the number of absorbed decks; applying it again changes nothing.
    """
    cutoff = fun_deck_dedupe_jaccard() if threshold is None else threshold
    ranked = sorted(
        range(len(rows)),
        key=lambda i: (
            -(_parse_games(rows[i].get("games")) or 0),
            -float(rows[i].get("fun_score") or 0),
            str(rows[i].get("deck_code") or ""),
        ),
    )
    survivors: dict[tuple[str, str], list[int]] = defaultdict(list)
    variants = {i: int(rows[i].get("variants") or 0) for i in range(len(rows))}
    absorbed: set[int] = set()
    for index in ranked:
        cards = cards_by_code.get(str(rows[index].get("deck_code") or ""))
        if not cards:
            continue
        best_survivor: int | None = None
        best_similarity = -1.0
        for other in survivors[_group_key(rows[index])]:
            other_cards = cards_by_code[str(rows[other].get("deck_code") or "")]
            similarity = jaccard_similarity(cards, other_cards)
            if similarity >= cutoff and similarity > best_similarity:
                best_survivor, best_similarity = other, similarity
        if best_survivor is None:
            survivors[_group_key(rows[index])].append(index)
            continue
        variants[best_survivor] += 1 + variants[index]
        absorbed.add(index)
    kept: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if index in absorbed:
            continue
        kept.append({**row, "variants": variants[index]})
    return kept, len(absorbed)


def _row_key(row: dict[str, Any]) -> tuple[str, str]:
    """A deck is its format and code: HSGuru lists one code in both formats."""
    return (_format_bucket(row.get("format")), str(row.get("deck_code") or ""))


def _is_off_meta(row: dict[str, Any]) -> bool:
    # A row without `kind` (an older writer) is a brew.
    return row.get("kind") == "off_meta"


def _pick_diverse(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    max_per_class: int = 3,
    max_xl_hl: int = 5,
) -> list[dict[str, Any]]:
    """Brews first; off-meta decks after them: one per class, at most a third of the picks."""
    picked = _pick_diverse_brews(
        [row for row in rows if not _is_off_meta(row)],
        limit=limit,
        max_per_class=max_per_class,
        max_xl_hl=max_xl_hl,
    )
    # off <= (brews + off) / 3  <=>  off <= brews / 2
    room = min(limit - len(picked), len(picked) // 2)
    seen_classes: set[str] = set()
    off_meta: list[dict[str, Any]] = []
    for row in sorted(
        (row for row in rows if _is_off_meta(row)),
        key=lambda row: -float(row.get("fun_score") or 0),
    ):
        cls = str(row.get("class") or "unknown").casefold()
        if len(off_meta) >= room:
            break
        if cls in seen_classes:
            continue
        seen_classes.add(cls)
        off_meta.append(row)
    return picked + off_meta


def _pick_diverse_brews(
    rows: list[dict[str, Any]],
    *,
    limit: int,
    max_per_class: int = 3,
    max_xl_hl: int = 5,
) -> list[dict[str, Any]]:
    """Pick by score: one deck per class first, then fill with class + XL/HL caps."""
    rows = sorted(rows, key=lambda row: -float(row.get("fun_score") or 0))
    picked: list[dict[str, Any]] = []
    class_counts: dict[str, int] = defaultdict(int)
    xl_hl = 0
    # Seed: the best deck of every class, so no class with a candidate is lost.
    # The XL/HL cap is skipped only when a class has nothing else to show.
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_class[str(row.get("class") or "unknown").casefold()].append(row)
    for cls, class_rows in by_class.items():
        if len(picked) >= limit:
            break
        choice = next(
            (
                row
                for row in class_rows
                if xl_hl < max_xl_hl or not _is_xl_hl_title(str(row.get("title") or ""))
            ),
            class_rows[0],
        )
        if _is_xl_hl_title(str(choice.get("title") or "")):
            xl_hl += 1
        class_counts[cls] += 1
        picked.append(choice)
    seeded = {id(row) for row in picked}
    for row in rows:
        if id(row) in seeded:
            continue
        if len(picked) >= limit:
            break
        cls = str(row.get("class") or "unknown").casefold()
        if class_counts[cls] >= max_per_class:
            continue
        if _is_xl_hl_title(str(row.get("title") or "")):
            if xl_hl >= max_xl_hl:
                continue
            xl_hl += 1
        class_counts[cls] += 1
        picked.append(row)
    # If caps left holes, fill by score with a softer class cap; still respect XL/HL.
    if len(picked) < limit:
        picked_codes = {_row_key(r) for r in picked}
        for row in rows:
            if len(picked) >= limit:
                break
            code = _row_key(row)
            if code in picked_codes:
                continue
            cls = str(row.get("class") or "unknown").casefold()
            if class_counts[cls] >= max_per_class + 1:
                continue
            if _is_xl_hl_title(str(row.get("title") or "")):
                if xl_hl >= max_xl_hl:
                    continue
                xl_hl += 1
            class_counts[cls] += 1
            picked_codes.add(code)
            picked.append(row)
    return picked


def _balance_and_dedupe(
    rows: list[dict[str, Any]], *, per_format_keep: int = 22
) -> list[dict[str, Any]]:
    """Dedupe by format+class+title, then keep a diverse Standard/Wild top list."""
    rows = sorted(
        rows,
        key=lambda row: (
            -float(row.get("fun_score") or 0),
            float(row.get("max_meta_similarity") or 1.0),
            str(row.get("last_seen_at") or ""),
        ),
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = _title_key(row)
        if key[2] and key in seen:
            continue
        if key[2]:
            seen.add(key)
        deduped.append(row)

    by_fmt: dict[str, list[dict[str, Any]]] = {"standard": [], "wild": [], "other": []}
    for row in deduped:
        by_fmt[_format_bucket(row.get("format"))].append(row)

    balanced: list[dict[str, Any]] = []
    for bucket in ("standard", "wild"):
        # Slightly prefer Standard fill when Wild XL/HL dominates candidates.
        xl_cap = 4 if bucket == "wild" else 5
        balanced.extend(
            _pick_diverse(
                by_fmt[bucket],
                limit=per_format_keep,
                max_per_class=3,
                max_xl_hl=xl_cap,
            )
        )
    picked_codes = {_row_key(row) for row in balanced}
    # Off-meta decks only enter through `_pick_diverse`, which caps them.
    leftovers = [
        row
        for row in deduped
        if _row_key(row) not in picked_codes and not _is_off_meta(row)
    ]
    leftovers.sort(key=lambda row: -float(row.get("fun_score") or 0))
    target_total = per_format_keep * 2
    class_counts: dict[str, int] = defaultdict(int)
    xl_hl = 0
    for row in balanced:
        class_counts[str(row.get("class") or "unknown").casefold()] += 1
        if _is_xl_hl_title(str(row.get("title") or "")):
            xl_hl += 1
    for row in leftovers:
        if len(balanced) >= target_total:
            break
        cls = str(row.get("class") or "unknown").casefold()
        if class_counts[cls] >= 4:
            continue
        if _is_xl_hl_title(str(row.get("title") or "")) and xl_hl >= 8:
            continue
        if _is_xl_hl_title(str(row.get("title") or "")):
            xl_hl += 1
        class_counts[cls] += 1
        balanced.append(row)

    balanced.sort(
        key=lambda row: (
            -float(row.get("fun_score") or 0),
            0 if _format_bucket(row.get("format")) == "standard" else 1,
        )
    )
    return balanced


def _title_key(row: dict[str, Any]) -> tuple[str, str, str]:
    """Format, class and title: a name shared by decks of different classes (or
    formats) is not a duplicate, so it must not cost a class its deck."""
    return (*_group_key(row), str(row.get("title") or "").casefold().strip())


def _dedupe_titles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best fun_score first; one deck per format, class and title."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in sorted(rows, key=lambda r: -float(r.get("fun_score") or 0)):
        key = _title_key(row)
        if key[2] and key in seen:
            continue
        if key[2]:
            seen.add(key)
        out.append(row)
    return out


def _select_published(
    merged: list[dict[str, Any]], *, focus: str | None
) -> list[dict[str, Any]]:
    """Pick the published rows: titles deduped, one deck per class first, then by score."""
    # Standard-focused runs keep a larger Standard slice to fix Wild dominance.
    if focus == "standard":
        std_rows = _dedupe_titles(
            [r for r in merged if _format_bucket(r.get("format")) == "standard"]
        )
        wild_rows = _dedupe_titles(
            [r for r in merged if _format_bucket(r.get("format")) == "wild"]
        )
        other_rows = _dedupe_titles(
            [r for r in merged if _format_bucket(r.get("format")) == "other"]
        )
        published = (
            _pick_diverse(std_rows, limit=28, max_per_class=4, max_xl_hl=6)
            + _pick_diverse(wild_rows, limit=18, max_per_class=3, max_xl_hl=4)
            + other_rows[:2]
        )
        published.sort(
            key=lambda row: (
                -float(row.get("fun_score") or 0),
                0 if _format_bucket(row.get("format")) == "standard" else 1,
            )
        )
        return published
    return _balance_and_dedupe(merged, per_format_keep=22)


@dataclass
class FunDecksRun:
    """Everything one detector run produced; nothing of it is stored yet."""

    focus: str | None
    seen_at: str
    index: CatalogIndex
    candidates: list[dict[str, Any]]
    # (candidate row, verdict) for every decodable candidate, fun or not.
    scored: list[tuple[dict[str, Any], FunDeckScore]]
    fresh: list[dict[str, Any]]
    published: list[dict[str, Any]]
    rejected: int = 0
    rejected_by_gate: dict[str, int] = field(default_factory=dict)
    # Fun-looking decks the sample / win-rate gate turned away, by reason.
    quality_rejected: dict[str, int] = field(default_factory=dict)
    history_dropped: int = 0
    near_duplicates: int = 0
    undecodable: int = 0
    format_counts: dict[str, int] = field(default_factory=dict)


def compute_fun_decks(
    format_focus: str | None,
    previous_rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    index: CatalogIndex | None = None,
) -> FunDecksRun:
    """Detect, merge with ``previous_rows`` and select the published decks.

    Reads the catalog datasets but writes nothing: storing the result, and the
    locking around it, is the caller's job (`_refresh_fun_decks_unlocked`).
    ``previous_rows`` are the rows of the last run of the same detector version.
    """
    now = now or datetime.now(UTC)
    seen_at = now.isoformat()
    focus = _format_bucket(format_focus) if format_focus else None
    if focus == "other":
        focus = None

    index = index or build_catalog_index()
    candidates = _candidate_rows(known_ladder=index.ladder_names, format_focus=focus)

    scored_all: list[tuple[dict[str, Any], FunDeckScore]] = []
    scored_fun: list[dict[str, Any]] = []
    rejected = 0
    rejected_by_gate: Counter[str] = Counter()
    quality_rejected: Counter[str] = Counter()
    # (format, code) pairs whose fresh sample failed the gate, or that a fresh candidate
    # turned away (a gate or the score threshold): evicted from the history, so an old
    # row cannot outlive a violation. A deck with no fresh candidate stays. The format matters: a --format standard run looks at Standard
    # listings only and must not touch a Wild row that happens to share the code.
    quality_blocked: set[tuple[str, str]] = set()
    undecodable = 0
    format_counts = {"wild": 0, "standard": 0, "other": 0}
    for row in candidates:
        code = str(row.get("deck_code") or "").strip()
        if not code:
            undecodable += 1
            continue
        cards = deck_card_multiset(code)
        if not cards:
            undecodable += 1
            continue
        format_name = str(row.get("Format") or row.get("format") or "")
        # The sample gate is judged on every decodable candidate, fun or not: fresh
        # numbers that disqualify a deck must also evict it from the history, even
        # when the same candidate stopped looking fun in this run.
        sample = evaluate_sample(row.get("games"), row.get("win_rate"))
        if not sample.ok:
            quality_blocked.add((_format_bucket(format_name), code))
        scored = score_fun_deck(
            deck_code=code,
            title=_deck_title(row),
            format_name=format_name,
            cards=cards,
            hero_class=str(row.get("class") or deck_hero_class(code) or ""),
            win_rate=_parse_win_rate(row.get("win_rate")),
            catalog_games_hint=_parse_games(row.get("games")),
            index=index,
        )
        scored_all.append((row, scored))
        if not scored.is_fun:
            # counted here only; a not-fun deck is never also a quality rejection
            quality_blocked.add((_format_bucket(format_name), code))
            rejected += 1
            rejected_by_gate.update(r for r in scored.reasons if r.endswith("_gate"))
            continue
        if not sample.ok:
            quality_rejected[str(sample.reject)] += 1
            continue
        scored_fun.append(_normalize_fun_row(row, scored, seen_at=seen_at, sample=sample))
        format_counts[_format_bucket(format_name)] += 1

    # Focused refresh only updates that format; keep the other side from history.
    if focus in {"standard", "wild"}:
        previous_rows = [
            row for row in previous_rows if _format_bucket(row.get("format")) != focus
        ] + [row for row in previous_rows if _format_bucket(row.get("format")) == focus]

    # History must not keep a deck the fresh numbers now disqualify, nor one that
    # no longer meets the thresholds (they are configurable).
    history_before = len(previous_rows)
    previous_rows = [
        row
        for row in previous_rows
        if (_format_bucket(row.get("format")), str(row.get("deck_code") or "").strip())
        not in quality_blocked
        and evaluate_sample(row.get("games"), row.get("win_rate")).ok
    ]
    history_dropped = history_before - len(previous_rows)

    merged = _merge_history(
        previous_rows,
        scored_fun,
        retention_hours=fun_deck_retention_hours(),
        now=now,
    )
    cards_by_code: dict[str, Counter[int]] = {}
    for row in merged:
        code = str(row.get("deck_code") or "").strip()
        cards = deck_card_multiset(code) if code else None
        if cards:
            cards_by_code[code] = cards
    # `variants` counts the near-identical lists absorbed in this run. A focused run
    # only sees candidates of its own format, so for the other format it keeps what
    # the last run that saw them counted instead of resetting it to 0.
    merged, near_duplicates = dedupe_near_duplicates(
        [
            {**row, "variants": 0}
            if focus is None or _format_bucket(row.get("format")) == focus
            else row
            for row in merged
        ],
        cards_by_code,
    )
    return FunDecksRun(
        focus=focus,
        seen_at=seen_at,
        index=index,
        candidates=candidates,
        scored=scored_all,
        fresh=scored_fun,
        published=_select_published(merged, focus=focus),
        rejected=rejected,
        rejected_by_gate=dict(rejected_by_gate),
        quality_rejected=dict(quality_rejected),
        history_dropped=history_dropped,
        near_duplicates=near_duplicates,
        undecodable=undecodable,
        format_counts=format_counts,
    )


def _previous_rows() -> list[dict[str, Any]]:
    """Rows of the stored dataset, when the same detector version wrote them."""
    previous = load_dataset(SOURCE_ID) or {}
    data = previous.get("data") if isinstance(previous.get("data"), dict) else {}
    structured = data.get("structured") if isinstance(data, dict) else None
    if (
        isinstance(structured, dict)
        and structured.get("detector_version") == DETECTOR_VERSION
        and isinstance(structured.get("rows"), list)
    ):
        return [row for row in structured["rows"] if isinstance(row, dict)]
    return []


def _refresh_fun_decks_unlocked(
    *,
    scheduled: bool = False,
    format_focus: str | None = None,
) -> dict[str, Any]:
    del scheduled
    source: Source = SOURCE_BY_ID[SOURCE_ID]
    run = compute_fun_decks(format_focus, _previous_rows())
    focus, seen_at = run.focus, run.seen_at
    published, scored_fun = run.published, run.fresh
    published_fmt = Counter(_format_bucket(row.get("format")) for row in published)
    published_kind = Counter(str(row.get("kind") or "brew") for row in published)

    dataset = {
        "source_id": SOURCE_ID,
        "state": SourceState.OK,
        "fetched_at": seen_at,
        "http_status": 200,
        "final_url": source.url,
        "backend": "derived:fun_deck_detector",
        "data": {
            "source_id": SOURCE_ID,
            "site": source.site,
            "category": source.category,
            "url": source.url,
            "structured": {
                "type": "hsguru_fun_decks",
                "detector_version": DETECTOR_VERSION,
                "theory": (
                    "A candidate is compared with every HSGuru catalog list of its format and class. "
                    "Ladder lists are turned away first, whatever their title or package: a list "
                    "that resembles a ladder-archetype list (ladder_member_gate), a ladder-archetype "
                    "title close to its lists (ladder_title_gate) or a bare style name such as "
                    "'Aggro Paladin' (generic_ladder_gate). A brew then needs a fun signal: a concept "
                    "title, a spicy card package or a list no other list resembles; an off_meta deck "
                    "is a variant of a named archetype that is not a ladder one. A published deck has "
                    "a real sample (games and win rate, at least min_games) and a 95% Wilson win-rate "
                    "interval whose upper bound is not below min_ci_high; near-identical lists of one "
                    "format and class (Jaccard >= dedupe_jaccard) collapse into the best sampled one "
                    "(variants = absorbed lists); brews are published first, one deck per class "
                    "before the rest is filled by fun_score, and off-meta decks after them (one per "
                    "class, at most a third of a format)."
                ),
                "rows": published,
                "fresh_detected": scored_fun,
                "filters": {
                    "min_fun_score": fun_deck_min_score(),
                    "ladder_share_pct": fun_deck_ladder_share_pct(),
                    "ladder_member_sim": fun_deck_ladder_member_sim(),
                    "twin_sim": fun_deck_twin_sim(),
                    "retention_hours": fun_deck_retention_hours(),
                    "candidate_sources": list(CANDIDATE_SOURCE_IDS),
                    "catalog_candidate_sources": list(CATALOG_CANDIDATE_SOURCE_IDS),
                    "per_format_keep": 26 if focus == "standard" else 22,
                    "format_focus": focus,
                    "min_games": fun_deck_min_games(),
                    "min_ci_high": fun_deck_min_ci_high(),
                    "dedupe_jaccard": fun_deck_dedupe_jaccard(),
                },
                "stats": {
                    "candidates": len(run.candidates),
                    "fun_fresh": len(scored_fun),
                    "fun_retained": len(published),
                    "rejected": run.rejected,
                    "rejected_by_gate": run.rejected_by_gate,
                    "quality_rejected": run.quality_rejected,
                    "history_dropped": run.history_dropped,
                    "near_duplicates_absorbed": run.near_duplicates,
                    "undecodable": run.undecodable,
                    "catalog_decks": len(run.index.decks),
                    "ladder_archetypes": len(run.index.ladder_names),
                    "fresh_by_format": run.format_counts,
                    "published_by_format": dict(published_fmt),
                    "published_by_kind": dict(published_kind),
                    "published_by_class": dict(
                        Counter(str(row.get("class") or "unknown") for row in published)
                    ),
                },
            },
        },
    }
    save_dataset(SOURCE_ID, dataset)
    save_status(
        SOURCE_ID,
        {
            "source_id": SOURCE_ID,
            "state": SourceState.OK,
            "fetched_at": seen_at,
            "backend": "derived:fun_deck_detector",
            "detector_version": DETECTOR_VERSION,
            "rows": len(published),
            "fun_fresh": len(scored_fun),
            "published_by_format": dict(published_fmt),
            "detail": (
                f"Detected {len(scored_fun)} fun/concept decks; published {len(published)} "
                f"(standard={published_fmt.get('standard', 0)}, wild={published_fmt.get('wild', 0)}, "
                f"off_meta={published_kind.get('off_meta', 0)})"
            ),
        },
    )
    return {
        "ok": True,
        "source_id": SOURCE_ID,
        "detector_version": DETECTOR_VERSION,
        "format_focus": focus,
        "fun_fresh": len(scored_fun),
        "fun_retained": len(published),
        "candidates": len(run.candidates),
        "catalog_decks": len(run.index.decks),
        "fresh_by_format": run.format_counts,
        "published_by_format": dict(published_fmt),
        "published_by_kind": dict(published_kind),
        "quality_rejected": run.quality_rejected,
        "near_duplicates_absorbed": run.near_duplicates,
        "fetched_at": seen_at,
    }


def refresh_fun_decks(
    *,
    scheduled: bool = False,
    format_focus: str | None = None,
) -> dict[str, Any]:
    """Rebuild the derived dataset unless another process owns the source."""
    locks = ResourceLockSet(
        [SOURCE_ID],
        metadata={"operation": "refresh_fun_decks"},
    )
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {
            "ok": True,
            "published": False,
            "source_id": SOURCE_ID,
            **exc.as_outcome(),
        }

    try:
        result = _refresh_fun_decks_unlocked(
            scheduled=scheduled,
            format_focus=format_focus,
        )
        result["published"] = True
        return result
    finally:
        locks.release()
