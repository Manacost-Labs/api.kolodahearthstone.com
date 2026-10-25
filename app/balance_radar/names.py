"""Russian names for the radar's texts: archetypes, cards, classes and ranks.

* Archetypes follow HearthPulse's own labels (``data/archetype_labels_ru.json``,
  the site's admin table over its built-in fallback) with the site's matcher:
  an exact name, else the longest label contained in the name plus the
  uncovered words. Uncovered words that are card names become the card's
  ruRU name; anything still English is translated once by Claude and cached
  in ``<data>/balance-radar-names.json``.
* Cards use the HearthstoneJSON ruRU localization by card id.

``Glossary`` cleans every model text deterministically (English names to
Russian, English duplicates in brackets, rank words, pipeline field names)
and marks up mentions of candidates so the site can link them by ``ref``.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..storage import root_dir

logger = logging.getLogger(__name__)

LABELS_PATH = Path(__file__).with_name("data") / "archetype_labels_ru.json"
NAMES_CACHE = "balance-radar-names.json"

CLASS_NAMES = {
    "Death Knight": "Рыцарь смерти",
    "Demon Hunter": "Охотник на демонов",
    "Druid": "Друид",
    "Hunter": "Охотник",
    "Mage": "Маг",
    "Paladin": "Паладин",
    "Priest": "Жрец",
    "Rogue": "Разбойник",
    "Shaman": "Шаман",
    "Warlock": "Чернокнижник",
    "Warrior": "Воин",
}
# Localized rank words; Blizzard is the company and stays as it is.
RANK_WORDS = (
    (re.compile(r"\bTop[\s-]?Legend\b", re.IGNORECASE), "топ Легенды"),
    (re.compile(r"\bLegend\b", re.IGNORECASE), "Легенда"),
    (re.compile(r"\bDiamond\b", re.IGNORECASE), "Алмаз"),
    (re.compile(r"\bPlatinum\b", re.IGNORECASE), "Платина"),
    (re.compile(r"Даймонд\w*", re.IGNORECASE), "Алмаз"),
    (re.compile(r"Леджен\w*", re.IGNORECASE), "Легенда"),
    (re.compile(r"Близз?ард\w*"), "Blizzard"),
    # The model sometimes names the company after the Mage spell.
    (
        re.compile(
            r"Снежн(?:ая|ой|ую) бур(?:я|и|ю)(?=\s+(?:сообщ|отмет|заяв|реш|призна|"
            r"написа|ввел|следит|может|уже|не\s|пока|обычно|часто|чаще|исторически))"
        ),
        "Blizzard",
    ),
    # The Wild format is "Вольный" in the Russian client.
    (re.compile(r"\bДиком\b"), "Вольном"),
    (re.compile(r"\bДикий\b"), "Вольный"),
    (re.compile(r"\bДикого\b"), "Вольного"),
    (re.compile(r"\bДикому\b"), "Вольному"),
    (re.compile(r"(\d)\s*п\.\s?п\."), r"\1 пункта"),
)
# Game terms the model borrows from English posts.
TERMS = {
    "Rush": "Натиск",
    "Taunt": "Провокация",
    "Battlecry": "Боевой клич",
    "Deathrattle": "Предсмертный хрип",
    "Lifesteal": "Похищение жизни",
    "Discover": "Раскопка",
    "Mulligan": "муллиган",
    "The Coin": "Монетка",
    "Coin": "Монетка",
    "Mill": "милл",
    "Handbuff": "усиление в руке",
}
ALLOWED_LATIN = frozenset(
    {
        "blizzard",
        "hsreplay",
        "hsguru",
        "reddit",
        "x",
        "twitch",
        "youtube",
        "hearthstone",
        "hearthpulse",
        "vs",
        "playhearthstone",
    }
)
_LATIN_BRACKETS = re.compile(r"\s*\((?=[^()]*[A-Za-z])[^()А-Яа-яЁё]*\)")
# Pipeline identifiers the reader must never see: dotted paths
# (history.patterns), camelCase and snake_case keys. Patch versions are digits.
_FIELD_NAMES = re.compile(
    r"\s*,?\s*(?:\b(?:из|в|по|согласно)\s+)?\(?\b(?:[a-z][A-Za-z0-9]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+|"
    r"[a-z]+[A-Z][A-Za-z0-9]*|[a-z]+_[a-z0-9_]+)\b\)?"
)
_DUPLICATE_BRACKETS = re.compile(r"(?P<name>[\wЁё'][\wЁё' -]{2,60}?)\s*\((?P=name)\)")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'’]+")
_SPACES = re.compile(r"[ \t]{2,}")
_SPACE_PUNCT = re.compile(r"\s+([,.;:!?)])")


# Rough English -> Cyrillic phonetics, longest rules first: enough to spot a
# model's transliteration ("Блазинг Инвокейшн") of a name it should translate.
_TRANSLIT_RULES = (
    ("tion", "шн"),
    ("sion", "жн"),
    ("igh", "ай"),
    ("sh", "ш"),
    ("ch", "ч"),
    ("th", "т"),
    ("ph", "ф"),
    ("ee", "и"),
    ("ea", "и"),
    ("oo", "у"),
    ("ou", "ау"),
    ("ck", "к"),
    ("qu", "кв"),
    ("kh", "х"),
    ("ai", "ей"),
    ("ay", "ей"),
    ("ow", "оу"),
    ("a", "а"),
    ("b", "б"),
    ("c", "к"),
    ("d", "д"),
    ("e", "е"),
    ("f", "ф"),
    ("g", "г"),
    ("h", "х"),
    ("i", "и"),
    ("j", "дж"),
    ("k", "к"),
    ("l", "л"),
    ("m", "м"),
    ("n", "н"),
    ("o", "о"),
    ("p", "п"),
    ("q", "к"),
    ("r", "р"),
    ("s", "с"),
    ("t", "т"),
    ("u", "у"),
    ("v", "в"),
    ("w", "в"),
    ("x", "кс"),
    ("y", "и"),
    ("z", "з"),
)
TRANSLIT_SIMILARITY = 0.8
# One-word names collide with ordinary inflected words ("Мага", "демонов").
TRANSLIT_SIMILARITY_ONE_WORD = 0.9
_CYRILLIC_WORD = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё'-]*")


def transliterate(english: str) -> str:
    text, out, index = english.casefold(), [], 0
    while index < len(text):
        for source, target in _TRANSLIT_RULES:
            if text.startswith(source, index):
                out.append(target)
                index += len(source)
                break
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


@lru_cache(maxsize=1)
def site_labels() -> dict[str, str]:
    try:
        return dict(json.loads(LABELS_PATH.read_text(encoding="utf-8"))["labels"])
    except (OSError, ValueError, KeyError) as exc:  # pragma: no cover - packaged file
        logger.warning("archetype labels unavailable: %s", type(exc).__name__)
        return {}


def _key(name: str) -> str:
    return " ".join(name.strip().split()).casefold()


def match_label(name: str, labels: Mapping[str, str]) -> tuple[str, str] | None:
    """(label, uncovered words) by HearthPulse's matcher, or None."""
    key = _key(name)
    if not key:
        return None
    if key in labels:
        return labels[key], ""
    best = max((k for k in labels if k and k in key), key=len, default="")
    if not best:
        return None
    start = key.index(best)
    words = " ".join(name.split())
    qualifier = f"{words[:start]} {words[start + len(best) :]}".strip(" -–—:·,")
    return labels[best], " ".join(qualifier.split())


def _names_cache_path() -> Path:
    return root_dir() / NAMES_CACHE


def load_name_cache() -> dict[str, str]:
    try:
        data = json.loads(_names_cache_path().read_text(encoding="utf-8"))
        return (
            {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        )
    except (OSError, ValueError):
        return {}


def save_name_cache(cache: Mapping[str, str]) -> None:
    path = _names_cache_path()
    pending = path.with_suffix(".tmp")
    pending.write_text(
        json.dumps(dict(sorted(cache.items())), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    os.replace(pending, path)


class NameTranslation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    russian: str = Field(
        description="Русское название архетипа Hearthstone, без английских слов"
    )


class NameTranslations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    names: list[NameTranslation]


NAMES_PROMPT = """Переведи названия архетипов Hearthstone на русский для русскоязычного
сайта. Используй русские названия карт и классов строго из glossary (никакой
транслитерации), стиль — как в examples (например «Shredslock» → «Чернокнижник
на обрывках», «Attack Druid» → «Друид на атаке»). Без английских слов, без скобок.
Верни перевод для каждого name из входа.
"""


def translate_archetype_names(
    names: Iterable[str],
    *,
    glossary: Mapping[str, str],
    claude: Any = None,
) -> dict[str, str]:
    """Claude translations of names the site's labels do not cover; cached forever."""
    from .analysis import AnalysisError, call_structured

    cache = load_name_cache()
    missing = sorted({n for n in names if n and n not in cache})
    if missing:
        payload = {
            "names": missing,
            "examples": dict(list(site_labels().items())[:60]),
            "glossary": dict(glossary),
        }
        try:
            output, _model, _usage, _req = call_structured(
                payload,
                system=NAMES_PROMPT,
                output_model=NameTranslations,
                client=claude,
                effort="low",
            )
        except AnalysisError as exc:
            logger.warning("archetype name translation skipped: %s", exc)
            output = None
        if output is not None:
            for item in output.names:
                russian = _LATIN_BRACKETS.sub("", item.russian).strip()
                if (
                    item.name in missing
                    and russian
                    and not _LATIN_WORD.search(russian.replace("К'", ""))
                ):
                    cache[item.name] = russian
            save_name_cache(cache)
    return {n: cache[n] for n in names if n in cache}


def archetype_name(
    name: str, *, cards_ru_by_en: Mapping[str, str], translated: Mapping[str, str]
) -> tuple[str, bool]:
    """(Russian name, is_final). Not final means it still needs a translation."""
    if name in translated:
        return translated[name], True
    match = match_label(name, site_labels())
    if match:
        label, qualifier = match
        if not qualifier:
            return label, True
        russian_qualifier = cards_ru_by_en.get(_key(qualifier))
        if russian_qualifier:
            return f"{label} ({russian_qualifier})", True
        return f"{label} ({qualifier})", False
    return name, False


@lru_cache(maxsize=4096)
def _inflected(russian: str) -> re.Pattern[str]:
    """A Russian name with free case endings ("Ритуал призыва" ~ "Ритуала призыва")."""
    parts = []
    for word in russian.split():
        if len(word) > 4 and word[-1].isalpha():
            parts.append(re.escape(word[:-2]) + r"[\wёЁ]{0,4}")
        else:
            parts.append(re.escape(word))
    return re.compile(
        r"(?<![\wЁё])" + r"\s+".join(parts) + r"(?![\wЁё])", re.IGNORECASE
    )


class Glossary:
    """English → Russian names for one run, with text cleanup and mention markup."""

    def __init__(self) -> None:
        self.ru_by_en: dict[str, str] = {}
        # Every collectible card and game term: replaced when written in
        # English, but too many to search for transliterations or to send.
        self.extra_ru_by_en: dict[str, str] = dict(TERMS)
        self.entities: dict[str, dict[str, Any]] = {}  # Russian name -> entity link

    def add(
        self,
        english: str | None,
        russian: str | None,
        entity: Mapping[str, Any] | None = None,
    ) -> None:
        if not russian:
            return
        if english and english.strip() and _key(english) != _key(russian):
            self.ru_by_en.setdefault(english.strip(), russian.strip())
        if entity and len(russian) >= 3:
            self.entities.setdefault(russian.strip(), dict(entity))

    def as_lines(self, names: Iterable[str] | None = None) -> str:
        items = self.ru_by_en.items()
        if names is not None:
            wanted = {n.casefold() for n in names}
            items = [
                (en, ru)
                for en, ru in items
                if en.casefold() in wanted or ru.casefold() in wanted
            ]
        return "\n".join(f"{en} = {ru}" for en, ru in sorted(items))

    def relevant(self, text: str) -> list[str]:
        lowered = text.casefold()
        return [
            en
            for en, ru in self.ru_by_en.items()
            if en.casefold() in lowered or ru.casefold() in lowered
        ]

    def add_cards(
        self, ru_by_en_casefold: Mapping[str, str], english_names: Mapping[str, str]
    ) -> None:
        """All collectible cards (English name -> ruRU) for English-name replacement."""
        for key, russian in ru_by_en_casefold.items():
            english = english_names.get(key, key)
            if len(english) >= 4:
                self.extra_ru_by_en.setdefault(english, russian)
        self.__dict__.pop("_compiled", None)

    @property
    def _all_names(self) -> dict[str, str]:
        # Brand words stay Latin even when a card shares the name
        # (the Mage spell Blizzard must not rename the company).
        return {
            english: russian
            for english, russian in {**self.extra_ru_by_en, **self.ru_by_en}.items()
            if english.casefold() not in ALLOWED_LATIN
        }

    @property
    def _english_pattern(self) -> re.Pattern[str] | None:
        cached = self.__dict__.get("_compiled")
        names_map = self._all_names
        if cached and cached[0] == len(names_map):
            return cached[1]
        names = sorted(names_map, key=len, reverse=True)
        if not names:
            return None
        compiled = re.compile(
            r"(?<![\w'])(?:" + "|".join(re.escape(n) for n in names) + r")(?![\w'])",
            re.IGNORECASE,
        )
        self.__dict__["_compiled"] = (len(names_map), compiled)
        return compiled

    def clean(self, text: str | None, issues: list[str] | None = None) -> str:
        """Russian-only reader text: no English names, brackets, field names or rank slang."""
        if not text:
            return text or ""
        original = text
        text = text.replace("’", "'")
        text = _LATIN_BRACKETS.sub(
            "", text
        )  # English duplicates before names are replaced
        pattern = self._english_pattern
        if pattern is not None:
            lookup = {k.casefold(): v for k, v in self._all_names.items()}
            text = pattern.sub(
                lambda m: lookup.get(m.group(0).casefold(), m.group(0)), text
            )
        text = _LATIN_BRACKETS.sub("", text)
        text = self._replace_transliterations(text, issues)
        text = _DUPLICATE_BRACKETS.sub(lambda m: m.group("name"), text)
        for regex, replacement in RANK_WORDS:
            text = regex.sub(replacement, text)
        cleaned = _FIELD_NAMES.sub("", text)
        if cleaned != text and issues is not None:
            issues.append("field_name_removed")
        text = _SPACE_PUNCT.sub(r"\1", _SPACES.sub(" ", cleaned)).strip()
        if issues is not None:
            leftovers = sorted(
                {
                    w
                    for w in _LATIN_WORD.findall(text)
                    if w.casefold().strip("'’") not in ALLOWED_LATIN
                }
            )
            if leftovers:
                issues.append("latin:" + ",".join(leftovers[:8]))
            if text != original and "changed" not in issues:
                issues.append("changed")
        return text

    def _replace_transliterations(self, text: str, issues: list[str] | None) -> str:
        """Swap Cyrillic transliterations of glossary names for the Russian names."""
        words = list(_CYRILLIC_WORD.finditer(text))
        if not words:
            return text
        protected = self._russian_spans(text)
        replacements: list[tuple[int, int, str]] = []
        for english, russian in self.ru_by_en.items():
            parts = english.split()
            spelled = transliterate(english)
            if len(parts) == 1 and len(spelled) < 6 or spelled == russian.casefold():
                continue
            size = len(parts)
            for start in range(len(words) - size + 1):
                first, last = words[start], words[start + size - 1]
                if text[last.end() : last.end() + 1].isalpha():
                    continue
                phrase = " ".join(w.group(0) for w in words[start : start + size])
                if (
                    phrase.casefold() == russian.casefold()
                    or phrase[0].casefold() != spelled[0]
                ):
                    continue
                if " ".join(text[first.start() : last.end()].split()) != phrase:
                    continue  # the words are not adjacent
                span = (first.start(), last.end())
                if any(span[0] < end and start < span[1] for start, end in protected):
                    continue  # part of a real Russian name ("Охотник на демонов")
                if size == 1 and not -1 <= len(phrase) - len(spelled) <= 2:
                    continue
                threshold = (
                    TRANSLIT_SIMILARITY if size > 1 else TRANSLIT_SIMILARITY_ONE_WORD
                )
                ratio = difflib.SequenceMatcher(
                    None, spelled, phrase.casefold()
                ).ratio()
                if ratio >= threshold and not any(
                    span[0] < end and start < span[1] for start, end, _ in replacements
                ):
                    replacements.append((span[0], span[1], russian))
        if not replacements:
            return text
        if issues is not None:
            issues.append("transliteration_replaced")
        for start, end, russian in sorted(replacements, reverse=True):
            text = text[:start] + russian + text[end:]
        return text

    def rename(self, text: str) -> str:
        """Names only (English and transliterated to Russian); codes and keys stay."""
        text = text.replace("’", "'")
        pattern = self._english_pattern
        if pattern is not None:
            lookup = {k.casefold(): v for k, v in self._all_names.items()}
            text = pattern.sub(
                lambda m: lookup.get(m.group(0).casefold(), m.group(0)), text
            )
        return self._replace_transliterations(text, None)

    def _russian_spans(self, text: str) -> list[tuple[int, int]]:
        names = {*self.ru_by_en.values(), *self.entities, *CLASS_NAMES.values()}
        return [
            (m.start(), m.end())
            for name in names
            for m in _inflected(name).finditer(text)
        ]

    def clean_tree(self, value: Any) -> Any:
        """``rename`` applied to every prose string of a nested structure (model context)."""
        if isinstance(value, str):
            return (
                self.rename(value)
                if " " in value.strip() or not value.isascii()
                else value
            )
        if isinstance(value, list):
            return [self.clean_tree(item) for item in value]
        if isinstance(value, dict):
            return {key: self.clean_tree(item) for key, item in value.items()}
        return value

    def mentions(self, text: str | None) -> list[dict[str, Any]]:
        """Spans of candidate names in ``text`` (inflected endings allowed)."""
        if not text:
            return []
        found: list[dict[str, Any]] = []
        taken: list[tuple[int, int]] = []
        for russian in sorted(self.entities, key=len, reverse=True):
            regex = _inflected(russian)
            for match in regex.finditer(text):
                span = (match.start(), match.end())
                if any(span[0] < end and start < span[1] for start, end in taken):
                    continue
                taken.append(span)
                found.append(
                    {
                        "start": span[0],
                        "end": span[1],
                        "text": match.group(0),
                        **self.entities[russian],
                    }
                )
        found.sort(key=lambda item: item["start"])
        return found
