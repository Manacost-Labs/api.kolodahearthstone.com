"""Russian names in the model's input and clean Russian text in the output.

``localize`` runs before the analysis: candidates, mulligan cards, core
archetypes and precedents get Russian names (English kept in ``nameEn``) and
the run's glossary is filled. ``finalize`` runs on the assembled payload:
every reader text is cleaned by the glossary, mentions are marked up by
``ref`` and posts get their full Russian translations.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .names import CLASS_NAMES, Glossary, archetype_name, translate_archetype_names
from .stats import StatsBundle

logger = logging.getLogger(__name__)

CANDIDATE_TEXT_FIELDS = (
    "headline",
    "verdict",
    "suggestedChange",
    "metaImpact",
    "developerSignal",
)
CANDIDATE_LIST_FIELDS = ("reasons", "keyNumbers")


def card_maps(
    cards_en: Mapping[str, Mapping[str, Any]], cards_ru: Mapping[str, Mapping[str, Any]]
) -> dict[str, str]:
    """casefolded English card name -> ruRU name, collectible cards only."""
    out: dict[str, str] = {}
    for card_id, card in cards_en.items():
        if not card.get("collectible"):
            continue
        russian = (cards_ru.get(card_id) or {}).get("name")
        if card.get("name") and russian:
            out.setdefault(" ".join(str(card["name"]).split()).casefold(), str(russian))
    return out


def _load_ru_cards() -> dict[str, dict[str, Any]]:
    try:
        from ..cards_index import cards_by_id

        return cards_by_id("ruRU")
    except Exception as exc:  # noqa: BLE001 - English names stay when ruRU is missing
        logger.warning("ruRU card index unavailable: %s", type(exc).__name__)
        return {}


def _archetype_names(
    bundles: Iterable[StatsBundle], changes: Sequence[Mapping[str, Any]]
) -> set[str]:
    names: set[str] = set()
    for bundle in bundles:
        for record in bundle.archetypes:
            names.add(record["name"])
        for record in (*bundle.card_nerf_pool, *bundle.card_buff_pool):
            stats = record.get("stats") or {}
            names.update(c["archetype"] for c in stats.get("core") or [])
            if isinstance(stats.get("mulligan"), list):
                names.update(m["archetype"] for m in stats["mulligan"])
        for record in bundle.classes:
            names.update((record.get("stats") or {}).get("topArchetypes") or [])
    for change in changes:
        names.update(a["archetype"] for a in change.get("archetypes") or [])
    return {n for n in names if n}


def localize(
    bundles: Sequence[StatsBundle],
    changes: Sequence[dict[str, Any]],
    *,
    cards_en: Mapping[str, Mapping[str, Any]],
    cards_ru: Mapping[str, Mapping[str, Any]] | None = None,
    claude: Any = None,
    translate: bool = True,
) -> Glossary:
    """Give every name the model and reader see its Russian form; return the glossary."""
    cards_ru = cards_ru if cards_ru is not None else _load_ru_cards()
    ru_by_en = card_maps(cards_en, cards_ru)
    glossary = Glossary()
    for english, russian in CLASS_NAMES.items():
        glossary.add(english, russian)
    archetypes = _archetype_names(bundles, changes)
    final: dict[str, str] = {}
    pending = []
    for name in archetypes:
        russian, done = archetype_name(name, cards_ru_by_en=ru_by_en, translated={})
        if done:
            final[name] = russian
        else:
            pending.append(name)
    if pending and translate:
        card_glossary = {
            en: ru
            for en, ru in ru_by_en.items()
            if any(en in p.casefold() for p in pending)
        }
        final.update(
            translate_archetype_names(pending, glossary=card_glossary, claude=claude)
        )
    for name in pending:
        if name not in final:
            final[name] = archetype_name(name, cards_ru_by_en=ru_by_en, translated={})[
                0
            ]

    def ru_card(english: str | None) -> str | None:
        return ru_by_en.get(" ".join(str(english or "").split()).casefold())

    for bundle in bundles:
        for record in bundle.archetypes:
            record.setdefault("nameEn", record["name"])
            record["name"] = final.get(record["nameEn"], record["name"])
            glossary.add(record["nameEn"], record["name"], _link(record))
            for alias in record.get("aliases") or []:
                glossary.add(alias, record["name"])
            mulligan = (record.get("stats") or {}).get("mulligan")
            if isinstance(mulligan, dict):
                for card in (
                    *(mulligan.get("keyCards") or []),
                    *(mulligan.get("weakCards") or []),
                ):
                    if "nameEn" in card:
                        continue
                    card["nameEn"] = card["name"]
                    card["name"] = ru_card(card["name"]) or card["name"]
                    glossary.add(card["nameEn"], card["name"])
        for record in (*bundle.card_nerf_pool, *bundle.card_buff_pool):
            glossary.add(record.get("nameEn"), record["name"], _link(record))
            stats = record.get("stats") or {}
            for core in stats.get("core") or []:
                if "archetypeEn" in core:
                    continue
                core["archetypeEn"] = core["archetype"]
                core["archetype"] = final.get(core["archetype"], core["archetype"])
                glossary.add(core["archetypeEn"], core["archetype"])
            if isinstance(stats.get("mulligan"), list):
                for item in stats["mulligan"]:
                    if "archetypeEn" in item:
                        continue
                    item["archetypeEn"] = item["archetype"]
                    item["archetype"] = final.get(item["archetype"], item["archetype"])
        for record in bundle.classes:
            glossary.add(record.get("nameEn"), record["name"], _link(record))
            stats = record.get("stats") or {}
            stats["topArchetypes"] = [
                final.get(n, n) for n in stats.get("topArchetypes") or []
            ]
    for change in changes:
        if change.get("nameEn") and change.get("name"):
            glossary.add(
                change["nameEn"],
                change["name"],
                {
                    "ref": f"card:{change['cardId']}",
                    "kind": "card",
                    "id": change["cardId"],
                    "dbfId": change.get("dbfId"),
                },
            )
        for archetype in change.get("archetypes") or []:
            if "archetypeEn" not in archetype:
                archetype["archetypeEn"] = archetype["archetype"]
                archetype["archetype"] = final.get(
                    archetype["archetype"], archetype["archetype"]
                )
            glossary.add(archetype["archetypeEn"], archetype["archetype"])
    for english, russian in final.items():
        glossary.add(english, russian)
    return glossary


def _link(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ref": record["ref"],
        "kind": record["kind"],
        "id": record["id"],
        "dbfId": record.get("dbfId"),
    }


def finalize(
    payload: dict[str, Any],
    glossary: Glossary,
    *,
    translations: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Clean reader texts, mark up mentions and attach post translations, in place."""
    issues: dict[str, list[str]] = {}

    def clean(path: str, text: Any) -> Any:
        if not isinstance(text, str) or not text:
            return text
        found: list[str] = []
        cleaned = glossary.clean(text, found)
        if any(item != "changed" for item in found):
            issues[path] = found
        return cleaned

    payload["summary"] = clean("summary", payload.get("summary"))
    payload["summaryEntities"] = glossary.mentions(payload["summary"])
    timing = payload.get("timing") or {}
    if timing.get("outlook"):
        timing["outlook"] = clean("timing.outlook", timing["outlook"])
    for side in ("nerfs", "buffs"):
        for candidate in payload.get(side) or []:
            prefix = f"{side}:{candidate['ref']}"
            entities = []
            for name in CANDIDATE_TEXT_FIELDS:
                if candidate.get(name):
                    candidate[name] = clean(f"{prefix}:{name}", candidate[name])
                    entities.extend(
                        {"field": name, **m} for m in glossary.mentions(candidate[name])
                    )
            for name in CANDIDATE_LIST_FIELDS:
                values = [
                    clean(f"{prefix}:{name}", v) for v in candidate.get(name) or []
                ]
                candidate[name] = values
                for index, value in enumerate(values):
                    entities.extend(
                        {"field": f"{name}.{index}", **m}
                        for m in glossary.mentions(value)
                    )
            for change in candidate.get("predictedChanges") or []:
                change["change"] = clean(
                    f"{prefix}:predictedChange", change.get("change")
                )
            social = candidate.get("social") or {}
            if social.get("summary"):
                social["summary"] = clean(f"{prefix}:social", social["summary"])
                entities.extend(
                    {"field": "social.summary", **m}
                    for m in glossary.mentions(social["summary"])
                )
            candidate["textEntities"] = entities
            for post in social.get("topPosts") or []:
                post.update(
                    (translations or {}).get(post["id"])
                    or {"titleRu": None, "textRu": None}
                )
            for quote in candidate.get("quotes") or []:
                translated = (translations or {}).get(
                    f"quote:{quote['postId']}:{quote['text'][:40]}"
                ) or {}
                quote["textRu"] = translated.get("textRu")
    payload.setdefault("usage", {})["textIssues"] = issues
    return payload
