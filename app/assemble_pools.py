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
from collections.abc import Mapping
from typing import Any

from . import card_choice_pools as pools

ASSEMBLE_MARK = "<b>Assemble</b>"
LOCALES = ("ru", "en")

_UNESCAPED_AMP = re.compile(r"(?<!\\)&")
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
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
    """Plain text: no ``[x]``, tags, ``$``/``#`` markers, ``\\&`` unescaped, one space."""

    value = _TAG.sub("", text).replace("[x]", " ").replace("\\&", "&")
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
