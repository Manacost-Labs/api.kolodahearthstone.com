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

Detection recipe:
1. Build META CORES = high-games catalog decks (format-aware).
2. Score candidates by (1 - Jaccard to best same-class meta core).
3. Boost: concept titles, rare archetype names, HL/XL shape, cheese WR bands.
4. Hard-reject: known high-volume ladder archetype variants and generic
   "<Aggro|Dragon|...> <class>" ladder titles that sit close to a meta core.
5. Quality gate: a deck needs a real sample (games, win rate), at least
   ``HS_FUN_DECK_MIN_GAMES`` games, and a 95% Wilson interval that is not
   entirely below ``HS_FUN_DECK_MIN_CI_HIGH`` percent (a losing list is not "fun").
6. Collapse near-identical lists (same format + class, Jaccard >= 0.6) into the
   better sampled one and publish with Standard/Wild balance, one deck per class
   first, then by fun score.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import sqrt
from typing import Any

from .config import (
    fun_deck_dedupe_jaccard,
    fun_deck_generic_name_max_similarity,
    fun_deck_max_meta_similarity,
    fun_deck_min_ci_high,
    fun_deck_min_games,
    fun_deck_min_score,
    fun_deck_retention_hours,
)
from .deck_decode import decode_deck_code
from .parser_control import load_resolved_public_dataset
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .sources import SOURCE_BY_ID, Source
from .storage import load_dataset, save_dataset, save_status

SOURCE_ID = "hsguru_fun_decks"
DETECTOR_VERSION = "concept-v7"
CANDIDATE_SOURCE_IDS = ("hsguru_streamer_decks_legend_1000",)
CATALOG_CANDIDATE_SOURCE_IDS = (
    "hsguru_deck_catalog_standard_all",
    "hsguru_deck_catalog_standard_legend",
    "hsguru_deck_catalog_wild_all",
    "hsguru_deck_catalog_wild_legend",
)
META_CATALOG_SOURCE_IDS = CATALOG_CANDIDATE_SOURCE_IDS
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
    r"elementals?|dude|big\s+spell|spell|mech|beast|pirate|undead"
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
class MetaReference:
    deck_code: str
    cards: Counter[int]
    archetype: str
    hero_class: str
    games: int
    format_name: str
    source_id: str
    win_rate: float | None = None


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


def build_meta_references(
    catalog_source_ids: tuple[str, ...] = META_CATALOG_SOURCE_IDS,
    *,
    per_source_limit: int = 350,
) -> list[MetaReference]:
    """High-volume decks only — the real ladder cores used for outlier detection."""
    refs: list[MetaReference] = []
    seen_codes: set[str] = set()
    for source_id in catalog_source_ids:
        rows = _catalog_rows(source_id)
        ranked = sorted(
            rows,
            key=lambda row: int(_parse_games(row.get("games")) or 0),
            reverse=True,
        )
        kept = 0
        for row in ranked:
            if kept >= per_source_limit:
                break
            code = str(row.get("deck_code") or "").strip()
            if not code or code in seen_codes:
                continue
            games = _parse_games(row.get("games")) or 0
            fmt = _format_bucket(row.get("format"))
            # Meta core thresholds (d0nkey public meta ≈ 200; Wild softer).
            min_games = 180 if fmt == "standard" else 120
            if "legend" in source_id:
                min_games = 80 if fmt == "standard" else 60
            if games < min_games:
                continue
            cards = deck_card_multiset(code)
            if not cards:
                continue
            seen_codes.add(code)
            kept += 1
            refs.append(
                MetaReference(
                    deck_code=code,
                    cards=cards,
                    archetype=str(
                        row.get("archetype") or row.get("title") or ""
                    ).strip(),
                    hero_class=str(row.get("class") or "").strip(),
                    games=games,
                    format_name=str(row.get("format") or "").strip(),
                    source_id=source_id,
                    win_rate=_parse_win_rate(row.get("win_rate")),
                )
            )
    return refs


def build_archetype_volume(
    catalog_source_ids: tuple[str, ...] = META_CATALOG_SOURCE_IDS,
) -> dict[str, int]:
    """Total games per archetype name — high volume ⇒ established ladder package."""
    volume: dict[str, int] = defaultdict(int)
    for source_id in catalog_source_ids:
        for row in _catalog_rows(source_id):
            name = (
                str(row.get("archetype") or row.get("title") or "").strip().casefold()
            )
            games = _parse_games(row.get("games")) or 0
            if name and games:
                volume[name] += games
    return dict(volume)


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


def build_known_ladder_archetypes(
    volume: dict[str, int],
    popularity: dict[str, float],
    *,
    min_volume_games: int = 800,
    min_popularity_pct: float = 1.0,
) -> set[str]:
    known = {name for name, games in volume.items() if games >= min_volume_games}
    known.update(name for name, pct in popularity.items() if pct >= min_popularity_pct)
    return known


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


def _concept_hit(title: str) -> bool:
    if _ladder_archetype_name(title) or _EGG_LADDER_RE.search(title or ""):
        return False
    if _CONCEPT_TITLE_RE.search(title or ""):
        return True
    # Off-class egg titles only count when not already ladder-gated above.
    return bool(_EGG_TITLE_RE.search(title or "")) and not _ladder_archetype_name(title)


def strip_format_prefix(title: str) -> str:
    """Drop service format prefixes ("STD", "WLD", "Wild") from the start of a title."""
    text = (title or "").strip()
    while not _WILD_CARD_NAME_RE.match(text):
        stripped = _FORMAT_PREFIX_RE.sub("", text, count=1).strip()
        if not stripped or stripped == text:
            break
        text = stripped
    return text


def _generic_ladder_title(title: str) -> bool:
    """True for a bare "<Aggro|Dragon|Dude|...> <class>" name (format prefix ignored)."""
    return bool(_GENERIC_LADDER_TITLE_RE.match(strip_format_prefix(title)))


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


def score_fun_deck(
    *,
    deck_code: str,
    title: str = "",
    format_name: str = "",
    cards: Counter[int] | None = None,
    hero_class: str = "",
    win_rate: float | None = None,
    catalog_games_hint: int | None = None,
    references: list[MetaReference],
    archetype_popularity: dict[str, float],
    archetype_volume: dict[str, int],
    known_ladder: set[str],
    min_score: float | None = None,
    max_meta_similarity: float | None = None,
    generic_name_max_similarity: float | None = None,
) -> FunDeckScore:
    threshold = fun_deck_min_score() if min_score is None else min_score
    similarity_cap = (
        fun_deck_max_meta_similarity()
        if max_meta_similarity is None
        else max_meta_similarity
    )
    generic_name_cap = (
        fun_deck_generic_name_max_similarity()
        if generic_name_max_similarity is None
        else generic_name_max_similarity
    )
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
    class_key = hero_class.casefold()
    best_sim = -1.0
    best_ref: MetaReference | None = None
    exact_games = catalog_games_hint
    for ref in references:
        if ref.deck_code == deck_code:
            exact_games = max(exact_games or 0, ref.games)
        # Prefer same format + class for fairer meta distance.
        if fmt != "other" and _format_bucket(ref.format_name) not in {fmt, "other"}:
            continue
        if class_key and ref.hero_class and ref.hero_class.casefold() != class_key:
            continue
        sim = jaccard_similarity(bag, ref.cards)
        if sim > best_sim:
            best_sim = sim
            best_ref = ref

    if best_sim < 0:
        for ref in references:
            sim = jaccard_similarity(bag, ref.cards)
            if sim > best_sim:
                best_sim = sim
                best_ref = ref

    similarity = max(0.0, best_sim)
    title_name = (title or "").strip()
    nearest = (best_ref.archetype if best_ref else "") or title_name
    pop = archetype_popularity.get(nearest.casefold()) if nearest else None
    if pop is None and title_name:
        pop = archetype_popularity.get(title_name.casefold())
    volume = archetype_volume.get(title_name.casefold(), 0)
    if nearest:
        volume = max(volume, archetype_volume.get(nearest.casefold(), 0))

    title_known = _is_known_name(title_name, known_ladder)
    nearest_known = _is_known_name(nearest, known_ladder)
    title_matches_nearest = _names_align(title_name, nearest)
    packages = detect_card_packages(bag)
    spicy = _spicy_packages(packages)
    title_concept = _concept_hit(title_name)
    # Only the deck's own title counts as a ladder name. Nearest-neighbor names
    # like "Egg Priest" must not contaminate unrelated outliers.
    ladder_named = _ladder_archetype_name(title_name) or (
        title_matches_nearest and _ladder_archetype_name(nearest)
    )
    # Quest/XL alone do not prove "fun" — need spicy packages or meme title cues.
    concept = title_concept or bool(spicy)
    rare_name = (
        bool(title_name) and volume < 250 and not title_known and not ladder_named
    )

    unique_cards = len(bag)
    total_cards = sum(bag.values())
    highlanderish = total_cards >= 28 and unique_cards >= 26
    xl_shape = total_cards >= 39

    reasons: list[str] = []
    # Primary signal: meta-core outlier (Advisor/MetaDetector style).
    score = (1.0 - similarity) * 0.55

    if similarity <= 0.28:
        score += 0.20
        reasons.append("meta_core_outlier")
    elif similarity <= 0.38:
        score += 0.12
        reasons.append("low_meta_overlap")

    if title_concept:
        score += 0.14
        reasons.append("concept_title")
    if spicy:
        score += min(0.22, 0.10 + 0.04 * len(spicy))
        reasons.append("card_package")
        reasons.extend(f"pkg:{label}" for label in spicy[:4])
    elif packages:
        # Neutral packages (quest/xl) are logged but barely boosted.
        score += 0.03
        reasons.append("neutral_package")
        reasons.extend(f"pkg:{label}" for label in packages[:3])
    if rare_name:
        score += 0.10
        reasons.append("rare_archetype_name")
    if highlanderish and spicy:
        score += 0.06
        reasons.append("highlander_shape")
    if xl_shape and (spicy or title_concept):
        score += 0.04
        reasons.append("xl_shape")
    # Soft tax: generic XL/HL titles are common Wild ladder niches, not unique fun.
    title_cf = title_name.casefold()
    if (
        "xl" in title_cf or "highlander" in title_cf or re.search(r"\bhl\b", title_cf)
    ) and not (
        title_concept
        and any(
            k in title_cf for k in ("mill", "yogg", "exodia", "casino", "joke", "meme")
        )
    ):
        score -= 0.06
        reasons.append("xl_hl_diversity_tax")

    if (
        title_name
        and nearest
        and not title_matches_nearest
        and similarity <= 0.45
        and not ladder_named
    ):
        score += 0.08
        reasons.append("title_mismatch_vs_nearest")

    # Cheese / dying meme winrate bands on low samples.
    sample = exact_games if exact_games is not None else catalog_games_hint
    if win_rate is not None and sample is not None and sample <= 120:
        if win_rate <= 42 and (spicy or title_concept):
            score += 0.06
            reasons.append("meme_low_winrate")
        elif win_rate >= 68 and concept:
            score += 0.07
            reasons.append("cheese_high_winrate")

    # Penalize established ladder packages hard.
    if ladder_named:
        score -= 0.45
        reasons.append("named_ladder_archetype")
    elif title_known and title_matches_nearest and similarity > 0.30 and not spicy:
        score -= 0.35
        reasons.append("known_ladder_archetype")
    elif nearest_known and similarity > 0.42 and not concept:
        score -= 0.20
        reasons.append("near_known_ladder_package")
    elif title_matches_nearest and (pop or 0) >= 0.2 and not spicy:
        score -= 0.25
        reasons.append("self_named_archetype")

    # Standard gets a small keep-alive so Wild concept spam doesn't dominate.
    if fmt == "standard" and not ladder_named:
        score += 0.05
        reasons.append("standard_balance_boost")
    elif fmt == "wild" and concept:
        score += 0.03
        reasons.append("wild_concept_boost")

    score = round(min(max(score, 0.0), 1.0), 4)

    hard_fail = False
    if similarity > similarity_cap:
        hard_fail = True
        reasons.append("similarity_gate")
    if ladder_named and not spicy:
        hard_fail = True
        reasons.append("ladder_name_gate")
    # "Aggro Paladin", "Dragon Mage": a bare style + class name that still looks like
    # a meta core and brings no card package is just an ordinary ladder list.
    if _generic_ladder_title(title_name) and similarity > generic_name_cap and not spicy:
        hard_fail = True
        reasons.append("generic_ladder_gate")
    if title_known and title_matches_nearest and similarity > 0.30 and not spicy:
        hard_fail = True
        reasons.append("named_ladder_gate")
    if title_matches_nearest and (pop or 0) >= 0.2 and not spicy and not title_concept:
        hard_fail = True
        reasons.append("self_named_gate")
    if (
        not concept
        and not rare_name
        and not (highlanderish and spicy)
        and similarity > 0.36
    ):
        hard_fail = True
        reasons.append("needs_concept_or_outlier_signal")

    is_fun = (not hard_fail) and score >= threshold
    return FunDeckScore(
        fun_score=score,
        max_meta_similarity=round(similarity, 4),
        nearest_archetype=nearest or None,
        nearest_deck_code=best_ref.deck_code if best_ref else None,
        catalog_games=exact_games,
        archetype_popularity_pct=pop,
        reasons=tuple(dict.fromkeys(reasons)),
        is_fun=is_fun,
        concept_hit=concept,
        rare_archetype=rare_name,
    )


def _candidate_rows(
    *,
    known_ladder: set[str],
    format_focus: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    focus = _format_bucket(format_focus) if format_focus else None
    if focus == "other":
        focus = None

    for source_id in CANDIDATE_SOURCE_IDS:
        for row in _streamer_rows(source_id):
            code = str(row.get("deck_code") or "").strip()
            if not code or code in seen_codes:
                continue
            row_fmt = _format_bucket(row.get("Format") or row.get("format"))
            if focus and row_fmt not in {focus, "other"}:
                continue
            seen_codes.add(code)
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
            if not code or code in seen_codes:
                continue
            games = _parse_games(row.get("games")) or 0
            if games < 45 or games > 220:
                continue
            title = str(row.get("title") or row.get("archetype") or "").strip()
            bucket = _format_bucket(row.get("format"))
            if bucket not in catalog_budget:
                continue
            if catalog_kept[bucket] >= catalog_budget[bucket]:
                continue
            concept = _concept_hit(title)
            known = _is_known_name(title, known_ladder)
            # Keep rare / concept brews; skip high-volume ladder names.
            if known and not concept:
                continue
            if not concept and games > 140:
                continue
            seen_codes.add(code)
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
    by_code: dict[str, dict[str, Any]] = {}

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
        by_code[code] = dict(row)

    for row in fresh_rows:
        code = str(row.get("deck_code") or "").strip()
        if not code:
            continue
        prev = by_code.get(code)
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
                ):
                    merged[key] = prev.get(key)
            by_code[code] = merged
        else:
            by_code[code] = dict(row)
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


def _pick_diverse(
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
        picked_codes = {str(r.get("deck_code") or "") for r in picked}
        for row in rows:
            if len(picked) >= limit:
                break
            code = str(row.get("deck_code") or "")
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
    picked_codes = {str(row.get("deck_code") or "") for row in balanced}
    leftovers = [
        row for row in deduped if str(row.get("deck_code") or "") not in picked_codes
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


def _refresh_fun_decks_unlocked(
    *,
    scheduled: bool = False,
    format_focus: str | None = None,
) -> dict[str, Any]:
    del scheduled
    source: Source = SOURCE_BY_ID[SOURCE_ID]
    now = datetime.now(UTC)
    seen_at = now.isoformat()
    focus = _format_bucket(format_focus) if format_focus else None
    if focus == "other":
        focus = None

    references = build_meta_references()
    popularity = build_archetype_popularity()
    volume = build_archetype_volume()
    known = build_known_ladder_archetypes(volume, popularity)
    candidates = _candidate_rows(known_ladder=known, format_focus=focus)

    scored_fun: list[dict[str, Any]] = []
    rejected = 0
    rejected_by_gate: Counter[str] = Counter()
    # Fun-looking decks the sample / win-rate gate turned away, by reason.
    quality_rejected: Counter[str] = Counter()
    # Codes whose fresh sample failed the gate, fun or not: evicted from the history.
    quality_blocked_codes: set[str] = set()
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
            quality_blocked_codes.add(code)
        scored = score_fun_deck(
            deck_code=code,
            title=_deck_title(row),
            format_name=format_name,
            cards=cards,
            hero_class=str(row.get("class") or deck_hero_class(code) or ""),
            win_rate=_parse_win_rate(row.get("win_rate")),
            catalog_games_hint=_parse_games(row.get("games")),
            references=references,
            archetype_popularity=popularity,
            archetype_volume=volume,
            known_ladder=known,
        )
        if not scored.is_fun:
            # counted here only; a not-fun deck is never also a quality rejection
            rejected += 1
            rejected_by_gate.update(r for r in scored.reasons if r.endswith("_gate"))
            continue
        if not sample.ok:
            quality_rejected[str(sample.reject)] += 1
            continue
        scored_fun.append(_normalize_fun_row(row, scored, seen_at=seen_at, sample=sample))
        format_counts[_format_bucket(format_name)] += 1

    previous = load_dataset(SOURCE_ID) or {}
    previous_data = (
        previous.get("data") if isinstance(previous.get("data"), dict) else {}
    )
    previous_rows: list[dict[str, Any]] = []
    if isinstance(previous_data, dict):
        structured = previous_data.get("structured") or {}
        if (
            isinstance(structured, dict)
            and structured.get("detector_version") == DETECTOR_VERSION
            and isinstance(structured.get("rows"), list)
        ):
            previous_rows = [row for row in structured["rows"] if isinstance(row, dict)]

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
        if str(row.get("deck_code") or "").strip() not in quality_blocked_codes
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
    published = _select_published(merged, focus=focus)
    published_fmt = Counter(_format_bucket(row.get("format")) for row in published)

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
                    "Fun decks are meta-core outliers with card-package + concept/rare signals; quest/steal cards are allowed, but named ladder archetypes (Thief Priest, Quest X, Soothsayer) are gated; "
                    "weak named ladder archetypes are excluded. A published deck has a real sample "
                    "(games and win rate, at least min_games) and a 95% Wilson win-rate interval whose "
                    "upper bound is not below min_ci_high; near-identical lists of one format and class "
                    "(Jaccard >= dedupe_jaccard) collapse into the best sampled one (variants = absorbed "
                    "lists); every class with a candidate gets one deck before the rest is filled by fun_score."
                ),
                "rows": published,
                "fresh_detected": scored_fun,
                "filters": {
                    "min_fun_score": fun_deck_min_score(),
                    "max_meta_similarity": fun_deck_max_meta_similarity(),
                    "retention_hours": fun_deck_retention_hours(),
                    "candidate_sources": list(CANDIDATE_SOURCE_IDS),
                    "catalog_candidate_sources": list(CATALOG_CANDIDATE_SOURCE_IDS),
                    "meta_core_sources": list(META_CATALOG_SOURCE_IDS),
                    "per_format_keep": 26 if focus == "standard" else 22,
                    "format_focus": focus,
                    "min_games": fun_deck_min_games(),
                    "min_ci_high": fun_deck_min_ci_high(),
                    "dedupe_jaccard": fun_deck_dedupe_jaccard(),
                    "generic_name_max_similarity": fun_deck_generic_name_max_similarity(),
                },
                "stats": {
                    "candidates": len(candidates),
                    "fun_fresh": len(scored_fun),
                    "fun_retained": len(published),
                    "rejected": rejected,
                    "rejected_by_gate": dict(rejected_by_gate),
                    "quality_rejected": dict(quality_rejected),
                    "history_dropped": history_dropped,
                    "near_duplicates_absorbed": near_duplicates,
                    "undecodable": undecodable,
                    "meta_references": len(references),
                    "known_ladder_archetypes": len(known),
                    "fresh_by_format": format_counts,
                    "published_by_format": dict(published_fmt),
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
                f"(standard={published_fmt.get('standard', 0)}, wild={published_fmt.get('wild', 0)})"
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
        "candidates": len(candidates),
        "meta_references": len(references),
        "fresh_by_format": format_counts,
        "published_by_format": dict(published_fmt),
        "quality_rejected": dict(quality_rejected),
        "near_duplicates_absorbed": near_duplicates,
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
