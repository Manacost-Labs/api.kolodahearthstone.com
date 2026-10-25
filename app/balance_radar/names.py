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
    # The Wild format is "Вольный" in the Russian client.
    (re.compile(r"\bДиком\b"), "Вольном"),
    (re.compile(r"\bДикий\b"), "Вольный"),
    (re.compile(r"\bДикого\b"), "Вольного"),
    (re.compile(r"\bДикому\b"), "Вольному"),
    (re.compile(r"(\d)\s*п\.\s?п\."), r"\1 пункта"),
)
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


class Glossary:
    """English → Russian names for one run, with text cleanup and mention markup."""

    def __init__(self) -> None:
        self.ru_by_en: dict[str, str] = {}
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

    @property
    def _english_pattern(self) -> re.Pattern[str] | None:
        names = sorted(self.ru_by_en, key=len, reverse=True)
        if not names:
            return None
        return re.compile(
            r"(?<![\w'])(?:" + "|".join(re.escape(n) for n in names) + r")(?![\w'])",
            re.IGNORECASE,
        )

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
            lookup = {k.casefold(): v for k, v in self.ru_by_en.items()}
            text = pattern.sub(
                lambda m: lookup.get(m.group(0).casefold(), m.group(0)), text
            )
        text = _LATIN_BRACKETS.sub("", text)
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

    def mentions(self, text: str | None) -> list[dict[str, Any]]:
        """Spans of candidate names in ``text`` (inflected endings allowed)."""
        if not text:
            return []
        found: list[dict[str, Any]] = []
        taken: list[tuple[int, int]] = []
        for russian in sorted(self.entities, key=len, reverse=True):
            words = russian.split()
            parts = []
            for word in words:
                escaped = re.escape(word)
                if len(word) > 4 and word[-1].isalpha():
                    parts.append(re.escape(word[:-2]) + r"[\wёЁ]{0,4}")
                else:
                    parts.append(escaped)
            regex = re.compile(
                r"(?<![\wЁё])" + r"\s+".join(parts) + r"(?![\wЁё])", re.IGNORECASE
            )
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
