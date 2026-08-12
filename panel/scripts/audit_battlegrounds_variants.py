#!/usr/bin/env python3
"""Audit Battlegrounds base/golden relationships exposed by the public API."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections import Counter, defaultdict


API_URL = "https://api.kolodahearthstone.com/api/v1/cards"
USER_AGENT = "KolodahsVariantAudit/1.0"


def load_cards() -> list[dict]:
    cards: list[dict] = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {"page": page, "per_page": 200, "include_variants": "1", "variant_audit": "1"}
        )
        request = urllib.request.Request(f"{API_URL}?{query}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        cards.extend(payload.get("data") or [])
        pagination = payload.get("pagination") or {}
        if not pagination.get("has_next"):
            return cards
        page += 1


def duplicates(values: list[object]) -> list[object]:
    return [value for value, count in Counter(values).items() if value is not None and count > 1]


def main() -> int:
    cards = load_cards()
    by_dbf = {card.get("dbf"): card for card in cards if card.get("dbf") is not None}
    base_cards = [card for card in cards if (card.get("variant") or {}).get("kind") == "base"]
    golden_cards = [card for card in cards if (card.get("variant") or {}).get("kind") == "golden"]
    linked_pairs = 0

    failures: list[str] = []
    warnings: list[str] = []
    for card_id in duplicates([card.get("card_id") for card in cards]):
        failures.append(f"duplicate card_id: {card_id}")
    for dbf in duplicates([card.get("dbf") for card in cards]):
        failures.append(f"duplicate dbf: {dbf}")

    gold_by_base: dict[int, list[dict]] = defaultdict(list)
    for golden in golden_cards:
        variant = golden.get("variant") or {}
        base_dbf = variant.get("base_dbf")
        if base_dbf is None:
            warnings.append(f"legacy golden without base: {golden.get('card_id')}")
            continue
        gold_by_base[int(base_dbf)].append(golden)
        base = by_dbf.get(base_dbf)
        if not base:
            warnings.append(
                f"legacy golden without imported base: {golden.get('card_id')} -> dbf {base_dbf}"
            )
            continue
        linked_pairs += 1
        if (base.get("variant") or {}).get("kind") != "base":
            failures.append(f"golden {golden.get('card_id')} references non-base dbf {base_dbf}")
        if variant.get("base_card_id") != base.get("card_id"):
            failures.append(
                f"golden {golden.get('card_id')} base_card_id mismatch: "
                f"{variant.get('base_card_id')} != {base.get('card_id')}"
            )
        if (base.get("variant") or {}).get("premium_dbf") != golden.get("dbf"):
            failures.append(
                f"base {base.get('card_id')} premium_dbf mismatch for {golden.get('card_id')}"
            )

    for base_dbf, variants in gold_by_base.items():
        if len(variants) > 1:
            failures.append(
                f"base dbf {base_dbf} has multiple golden variants: "
                + ", ".join(str(card.get("card_id")) for card in variants)
            )

    for base in base_cards:
        premium_dbf = (base.get("variant") or {}).get("premium_dbf")
        if premium_dbf is not None and premium_dbf not in by_dbf:
            warnings.append(
                f"legacy base without imported golden: {base.get('card_id')} -> dbf {premium_dbf}"
            )

    summary = {
        "records": len(cards),
        "base_cards": len(base_cards),
        "golden_variants": len(golden_cards),
        "linked_pairs": linked_pairs,
        "warnings": len(warnings),
        "failures": len(failures),
    }
    print(json.dumps(summary, ensure_ascii=False))
    for warning in warnings[:50]:
        print(f"WARNING: {warning}")
    for failure in failures[:50]:
        print(f"FAILURE: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
