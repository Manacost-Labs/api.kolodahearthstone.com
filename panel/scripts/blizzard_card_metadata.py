"""Resolve Blizzard IDs from its public metadata, without guessing HSJSON values."""
from __future__ import annotations

GROUPS = {
    "class": "classes", "cardType": "types", "rarity": "rarities",
    "minionType": "minionTypes", "spellSchool": "spellSchools",
}


def reference_cards(metadata: dict) -> list[dict]:
    references = []
    for field, group in GROUPS.items():
        rows = metadata.get(group)
        if not isinstance(rows, list):
            raise ValueError(f"Missing Blizzard metadata group: {group}")
        valid = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity, slug = row.get("id"), row.get("slug")
            if type(identity) is int and identity > 0 and isinstance(slug, str) and slug.strip():
                canonical = slug.strip().replace(" ", "_").replace("-", "_") if field == "cardType" else slug.strip()
                valid.append({field: {"id": identity, "slug": canonical}})
        if field in {"class", "cardType", "rarity"} and not valid:
            raise ValueError(f"Empty Blizzard metadata group: {group}")
        references.extend(valid)
    return references
