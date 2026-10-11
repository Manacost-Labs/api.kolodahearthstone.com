"""Repair missing game-data substitutions without replacing valid hotfix text."""
from __future__ import annotations

import re


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"\s+", " ", value.replace("[x]", "").replace("\u00a0", " ")).strip()


def missing_cost(value: str | None) -> bool:
    return bool(value and (re.search(r"(?<![\w}])-Cost", value) or re.search(r"за\s*\(\s*\)", value)))


def repair_texts(card_id: str, ru: str | None, en: str | None,
                 hsj_ru: str | None, hsj_en: str | None) -> tuple[str | None, str | None, dict[str, str]]:
    texts = {"ru": ru, "en": en}
    evidence = {"ru": clean_text(hsj_ru), "en": clean_text(hsj_en)}
    repairs = {}
    for locale in texts:
        if missing_cost(texts[locale]) and evidence[locale] and not missing_cost(evidence[locale]):
            texts[locale] = evidence[locale]
            repairs[locale] = "hsj_missing_cost_substitution"
    # A localized Blizzard response can lag behind the English hotfix. Scope
    # this correction to the exact observed old/new pair, not all numbers.
    if (card_id == "TLC_600" and texts["ru"] and texts["en"]
            and re.search(r"стоит на \(3\) меньше", texts["ru"])
            and "Costs (2) less" in texts["en"]
            and evidence["ru"] and "стоит на (2) меньше" in evidence["ru"]):
        texts["ru"] = evidence["ru"]
        repairs["ru"] = "hsj_confirmed_localized_hotfix"
    return texts["ru"], texts["en"], repairs
