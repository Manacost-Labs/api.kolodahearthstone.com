"""Assemble cards and the text of an assembled minion, built from the card base.

Reign of the Black Empire's *Assemble* picks a random base minion and offers
two donor minions. The player takes one half (trigger or effect) of one donor,
which replaces the same half of the base. HearthstoneJSON marks the split of
every pool minion with a ``&`` between the trigger ("At the end of your turn,")
and the effect ("summon a 5/5 Baine Bloodhoof."). This module works without
network: it finds the Assemble cards, splits texts into halves and joins a base
and a donor into the text HSReplay shows.

Traps verified against the 2026-11-18 card base:

- ``\\&`` is an escaped ampersand ("Attack \\& Armor" of ``ETC_386``), not a
  separator: only the first unescaped ``&`` splits.
- Keywords stand before the trigger (``<b>Taunt</b>\\n``,
  ``<b>Divine Shield</b>, <b>Taunt</b> ``, ``<b>Гигант +3.</b> ``) and HSReplay
  does not show them. A bold group with a colon (``<b>Deathrattle:</b>``) is a
  trigger and stays.
- ``[x]`` (a no-wrap marker) also occurs in the middle of a text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from . import card_choice_pools as pools

ASSEMBLE_MARK = "<b>Assemble</b>"
LOCALES = ("ru", "en")

_UNESCAPED_AMP = re.compile(r"(?<!\\)&")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
# ruRU hyphenation marker inside a word, with the padding run after it.
_WORD_BREAK = re.compile(r"\[d\]\s*")
# One leading keyword: a bold group without a colon, with an optional dot,
# followed by whitespace or a comma ("<b>Taunt</b>\n", "<b>Gigantic +3.</b> ").
_LEADING_KEYWORD = re.compile(r"^\s*<b>[^<]*?(?<!:)\.?</b>[\s.,]*", re.IGNORECASE)
_LEADING_KEYWORD_WITH_COLON = re.compile(r"^\s*<b>[^<]*:\s*</b>")


def is_assemble_card(card: Mapping[str, Any]) -> bool:
    return bool(card.get("collectible")) and ASSEMBLE_MARK in str(
        card.get("text") or ""
    )


def assemble_cards(
    base: pools.CardBase, format_name: str | None = None
) -> list[Mapping[str, Any]]:
    """Collectible Assemble cards of the base (optionally of one format), by id."""

    found: dict[str, Mapping[str, Any]] = {}
    for card in base.collectible:
        if not is_assemble_card(card):
            continue
        if format_name is not None and not base.in_format(card, format_name):
            continue
        found.setdefault(str(card["id"]), card)
    return [found[key] for key in sorted(found)]


def _strip_leading_keywords(trigger: str) -> str:
    while not _LEADING_KEYWORD_WITH_COLON.match(trigger):
        stripped = _LEADING_KEYWORD.sub("", trigger, count=1)
        if stripped == trigger:
            break
        trigger = stripped
    return trigger


def clean(text: str) -> str:
    """Plain text: no ``[x]``, ``[d]`` (glued), tags, ``$``/``#`` markers, ``\\&`` unescaped, one space."""

    value = _WORD_BREAK.sub("", text)
    value = _TAG.sub("", value).replace("[x]", " ").replace("\\&", "&")
    value = value.replace("$", "").replace("#", "")
    return _SPACE.sub(" ", value).strip()


def split_halves(text: str | None) -> tuple[str, str] | None:
    """``(trigger, effect)`` of a pool minion text, or ``None`` without a separator."""

    raw = str(text or "")
    parts = _UNESCAPED_AMP.split(raw, maxsplit=1)
    if len(parts) != 2:
        return None
    trigger = clean(_strip_leading_keywords(parts[0].replace("[x]", " ")))
    effect = clean(parts[1])
    if not trigger or not effect:
        return None
    return trigger, effect


def _fit_effect(trigger: str, effect: str, locale: str) -> str:
    """English grammar after the join: capital after ``:``, lower case after ``,``."""

    if locale != "en" or not effect:
        return effect
    first, rest = effect[0], effect[1:]
    if trigger.endswith(":"):
        return first.upper() + rest
    if trigger.endswith(",") and first.isupper() and rest[:1].islower():
        return first.lower() + rest
    return effect


def join_halves(trigger: str, effect: str, locale: str) -> dict[str, str]:
    return {"trigger": trigger, "effect": _fit_effect(trigger, effect, locale)}


class AssembleTexts:
    """Both halves of every card of the base, per locale (``None`` when not split)."""

    def __init__(self, base: pools.CardBase) -> None:
        self._base = base
        self._cache: dict[tuple[int, str], tuple[str, str] | None] = {}

    def card(self, dbf_id: int) -> Mapping[str, Any] | None:
        return self._base.by_dbf.get(int(dbf_id))

    def halves(self, dbf_id: int, locale: str) -> tuple[str, str] | None:
        key = (int(dbf_id), locale)
        if key not in self._cache:
            card = self.card(dbf_id)
            if card is None:
                text = None
            elif locale == "en":
                text = card.get("text")
            else:
                text = self._base.texts_ru.get(str(card.get("id")))
            self._cache[key] = split_halves(text)
        return self._cache[key]

    def resolvable(self, dbf_id: int) -> bool:
        """The row can be shown: a known card whose English text splits."""

        return self.halves(dbf_id, "en") is not None

    def option(
        self, base_dbf: int, donor_dbf: int, half: str
    ) -> dict[str, dict[str, str] | None]:
        """Assembled text per locale when the donor's ``half`` replaces the base's."""

        result: dict[str, dict[str, str] | None] = {}
        for locale in LOCALES:
            mine = self.halves(base_dbf, locale)
            theirs = self.halves(donor_dbf, locale)
            if mine is None or theirs is None:
                result[locale] = None
            elif half == "effect":
                result[locale] = join_halves(mine[0], theirs[1], locale)
            else:
                result[locale] = join_halves(theirs[0], mine[1], locale)
        return result

    def card_text(self, card: Mapping[str, Any], locale: str) -> str | None:
        text = (
            card.get("text")
            if locale == "en"
            else self._base.texts_ru.get(str(card.get("id")))
        )
        value = clean(str(text or ""))
        return value or None


# --- donor pools -------------------------------------------------------------------
#
# Which minions can be offered next to a base. The rules (checked on the HSReplay
# snapshot of 2026-11-19, see docs/HSREPLAY_ASSEMBLE.md "Полный пул доноров"):
#
# R1  donor cost: base cost .. base cost + 2; a base of 9-10 -> donors of 8-10;
# R2  BE_045 (Mismatcher): donors of 5 and more;
# R3  BE_004 (Rajh): a curated list of donors of any class and cost;
# R4  donor class: the player's class + neutral (the Assemble card's class by default);
# R5  a neutral Assemble card (BE_044) without a player class: every class;
# R6  a keyword of the base's "Assemble a <keyword> minion" never filters donors;
# R7  a donor with the same trigger as the base is not offered (never the base itself);
# R8  Standard: Standard cards only; Wild: all;
# R9  collectible cards (minions, weapons, spells and heroes: HSReplay offers all of
#     them) whose English text splits at ``&``; a CORE_ copy and its original are
#     one donor.

CURATED_DONORS = {
    # Rajh: the 14 minions HSReplay showed in both formats on 2026-11-19
    # (13 bases + TLC_833); the list does not depend on the player's class.
    "BE_004": frozenset(
        {
            "BE_008",
            "BE_701",
            "BE_997",
            "CATA_133",
            "CORE_BAR_878",
            "CORE_EX1_559",
            "CORE_RLK_083",
            "CORE_TTN_843",
            "EDR_462",
            "FIR_955",
            "TIME_100",
            "TLC_254",
            "TLC_623",
            "TLC_833",
        }
    )
}
MIN_DONOR_COST = {"BE_045": 5}
TOP_COST_BASE = 9
TOP_COST_FLOOR = 8
MAX_COST = 10
COST_SPAN = 2
_NOT_WORD = re.compile(r"[^\w]+")

REJECT_REASONS = (
    "unknown_card",
    "no_separator",
    "format",
    "curated",
    "cost",
    "same_trigger",
    "self",
)


# Keyword triggers (``Battlecry:``, ``Deathrattle:``): a base and a donor never share one,
# so "Battlecry:" and "Battlecry: If you control a Mech," are one family.
KEYWORD_TRIGGERS = (
    "battlecry",
    "deathrattle",
    "combo",
    "outcast",
    "kindred",
    "spellburst",
    "frenzy",
    "honorable kill",
    "overheal",
    "infuse",
    "start of game",
    "inspire",
    "overkill",
)
_LEADING_VERB = re.compile(r"^(after|whenever) ")
_KEYWORD_ALTERNATIVES = "|".join(KEYWORD_TRIGGERS)
_LEADING_KEYWORDS = re.compile(
    rf"^(?:{_KEYWORD_ALTERNATIVES})(?: (?:and|or) (?:{_KEYWORD_ALTERNATIVES}))*\b"
)


def condition_key(trigger: str) -> str:
    """Normalized trigger: same words, any case and punctuation."""

    return _NOT_WORD.sub(" ", trigger.casefold()).strip()


def condition_family(trigger: str) -> tuple[frozenset[str], str]:
    """``(keywords, event)`` of a trigger.

    ``keywords``: the keyword triggers the text starts with ("Battlecry and
    Deathrattle:" -> both; a later "Kindred:" is not counted). ``event``: the words without the leading
    "After"/"Whenever" (they exclude each other), empty for keyword triggers.
    """

    key = condition_key(trigger)
    leading = _LEADING_KEYWORDS.match(key)
    if leading:
        return frozenset(re.findall(_KEYWORD_ALTERNATIVES, leading.group(0))), ""
    return frozenset(), _LEADING_VERB.sub("", key)


def same_condition(first: str, second: str) -> bool:
    """The two triggers are the same condition (R7): equal, one keyword shared, or
    one event text a word-prefix of the other ("you cast a spell" / "you cast a spell on a minion")."""

    keywords_a, event_a = condition_family(first)
    keywords_b, event_b = condition_family(second)
    if keywords_a or keywords_b:
        return bool(keywords_a & keywords_b)
    return event_a == event_b or (
        event_a.startswith(event_b + " ") or event_b.startswith(event_a + " ")
    )


def cost_range(
    assemble_id: str, base_cost: int | None
) -> tuple[int, int | None] | None:
    """Allowed donor cost ``(low, high)``; ``None`` when the cost does not filter (Rajh)."""

    if assemble_id in CURATED_DONORS:
        return None
    if assemble_id in MIN_DONOR_COST:
        return MIN_DONOR_COST[assemble_id], None
    cost = int(base_cost or 0)
    if cost >= TOP_COST_BASE:
        return TOP_COST_FLOOR, MAX_COST
    return cost, cost + COST_SPAN


def player_class_of(
    assemble: Mapping[str, Any], requested: str | None = None
) -> str | None:
    """The class whose donors are offered: the request, else the card's class (``None`` = all)."""

    if str(assemble.get("id")) in CURATED_DONORS:
        return None
    if requested:
        return requested.upper()
    own = pools.card_classes(assemble)[0]
    return None if own == pools.NEUTRAL else own


def is_class_dependent(assemble: Mapping[str, Any]) -> bool:
    return (
        str(assemble.get("id")) not in CURATED_DONORS
        and pools.card_classes(assemble)[0] == pools.NEUTRAL
    )


_POOL_CARDS: dict[tuple[int, str], tuple[pools.CardBase, list[Mapping[str, Any]]]] = {}


class DonorPools:
    """Donor rules of one Assemble card in one format, on a card base (no network)."""

    def __init__(
        self,
        base: pools.CardBase,
        texts: AssembleTexts,
        assemble: Mapping[str, Any],
        format_name: str,
    ) -> None:
        self.base = base
        self.texts = texts
        self.assemble_id = str(assemble["id"])
        self.format_name = format_name

    def minions(self) -> list[Mapping[str, Any]]:
        """Pool cards of the format (R8, R9), by dbf id (shared by every card of the base)."""

        key = (id(self.base), self.format_name)
        cached = _POOL_CARDS.get(key)
        if cached is None or cached[0] is not self.base:
            found = sorted(
                (
                    card
                    for card in self.base.collectible
                    if self.base.in_format(card, self.format_name)
                    and split_halves(card.get("text")) is not None
                ),
                key=lambda card: int(card["dbfId"]),
            )
            if len(_POOL_CARDS) > 8:
                _POOL_CARDS.clear()
            cached = _POOL_CARDS[key] = (self.base, found)
        return cached[1]

    def reject_reason(self, base_dbf: int, donor_dbf: int) -> str | None:
        """Why the donor cannot stand next to the base; ``None`` when it can (class aside)."""

        donor = self.base.by_dbf.get(int(donor_dbf))
        base_card = self.base.by_dbf.get(int(base_dbf))
        if donor is None or base_card is None:
            return "unknown_card"
        if not donor.get("collectible"):
            return "unknown_card"
        donor_halves = self.texts.halves(int(donor_dbf), "en")
        base_halves = self.texts.halves(int(base_dbf), "en")
        if donor_halves is None or base_halves is None:
            return "no_separator"
        if not self.base.in_format(donor, self.format_name):
            return "format"
        if int(donor_dbf) == int(base_dbf):
            return "self"
        curated = CURATED_DONORS.get(self.assemble_id)
        if curated is not None and str(donor["id"]) not in curated:
            return "curated"
        span = cost_range(self.assemble_id, base_card.get("cost"))
        if span is not None:
            low, high = span
            cost = int(donor.get("cost") or 0)
            if cost < low or (high is not None and cost > high):
                return "cost"
        if same_condition(donor_halves[0], base_halves[0]):
            return "same_trigger"
        return None

    def class_ok(self, donor: Mapping[str, Any], player_class: str | None) -> bool:
        if player_class is None:
            return True
        classes = pools.card_classes(donor)
        return pools.NEUTRAL in classes or player_class in classes

    def candidates(
        self,
        base_dbf: int,
        player_class: str | None,
        prefer: Iterable[int] = (),
        keep: Iterable[int] = (),
    ) -> list[Mapping[str, Any]]:
        """The pool of a base, one card per donor (a CORE_ copy and its original are one).

        ``keep``: observed donors that always stay (within the check tolerance, class
        permitting). ``prefer``: dbf ids to show when a donor has several copies.
        """

        preferred = {int(dbf) for dbf in prefer}
        kept = {int(dbf) for dbf in keep}
        chosen: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
        for card in self.minions():
            dbf = int(card["dbfId"])
            if not self.class_ok(card, player_class):
                continue
            if dbf not in kept and self.reject_reason(base_dbf, dbf) is not None:
                continue
            key = (
                card.get("name"),
                card.get("text"),
                card.get("cost"),
                tuple(pools.card_classes(card)),
            )
            chosen.setdefault(key, []).append(card)
        result: list[Mapping[str, Any]] = []
        for group in chosen.values():
            wanted = [c for c in group if int(c["dbfId"]) in kept]
            if not wanted:
                wanted = [c for c in group if int(c["dbfId"]) in preferred]
            if not wanted:
                wanted = [
                    min(
                        group,
                        key=lambda c: (
                            not self.base.is_standard(c),
                            int(c["dbfId"]),
                        ),
                    )
                ]
            result.extend(wanted)
        return sorted(result, key=lambda card: int(card["dbfId"]))
