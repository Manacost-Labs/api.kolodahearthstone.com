"""Report of the Assemble donor pool rules against a HSReplay snapshot (no network).

``python -m app.cli assemble-donor-pool-report [--format standard|wild]
[--snapshot <dataset.json>] [--json]``: per card and format, the observed
(base, donor) pairs the rules do not cover, the share of donor shows they make,
the worst examples with the reason, the share of shows of "foreign" class donors
(copies of the card by another class) and the pool candidates that were never
offered at bases with many shows (a sign of surplus donors).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import card_choice_pools as pools
from .assemble_pools import (
    AssembleTexts,
    DonorPools,
    assemble_cards,
    is_class_dependent,
    player_class_of,
)
from .hsreplay_assemble import (
    FORMATS,
    POOL_MISMATCH_SHARE,
    _donor_pool_check,
    _pct,
    _stored_formats,
)

MIN_BASE_OFFERED = 300
SURPLUS_ALERT_SHARE = 20.0


def _reference_class(
    card: Mapping[str, Any], base_card: Mapping[str, Any]
) -> str | None:
    """Class the donors of this base should be of; ``None`` when no class rule applies."""

    own = player_class_of(card)
    if own is not None or not is_class_dependent(card):
        return own
    base_class = pools.card_classes(base_card)[0]
    return None if base_class == pools.NEUTRAL else base_class


def card_report(
    card_base: pools.CardBase,
    texts: AssembleTexts,
    card: Mapping[str, Any],
    format_name: str,
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    rows = ((snapshot or {}).get("cards") or {}).get(str(card["dbfId"])) or []
    entry: dict[str, Any] = {
        "card_id": str(card["id"]),
        "format": format_name,
        "state": "no_data",
    }
    if not rows:
        return entry
    rules = DonorPools(card_base, texts, card, format_name)
    check = _donor_pool_check(rules, rows)
    shown = foreign = 0
    surplus = pool_total = checked_bases = 0
    for middle_dbf, middle_offered, donor_rows in rows:
        base_card = card_base.by_dbf.get(int(middle_dbf))
        reference = _reference_class(card, base_card) if base_card else None
        seen = {int(row[0]) for row in donor_rows if int(row[1]) > 0}
        for row in donor_rows:
            shown += int(row[1])
            donor = card_base.by_dbf.get(int(row[0]))
            if reference and donor is not None:
                classes = pools.card_classes(donor)
                if pools.NEUTRAL not in classes and reference not in classes:
                    foreign += int(row[1])
        if base_card is None or int(middle_offered) < MIN_BASE_OFFERED:
            continue
        candidates = rules.candidates(
            int(middle_dbf), reference, prefer=seen, keep=seen
        )
        checked_bases += 1
        pool_total += len(candidates)
        surplus += sum(1 for item in candidates if int(item["dbfId"]) not in seen)
    uncovered = _pct(surplus, pool_total)
    entry.update(
        {
            "state": "mismatch" if check["mismatch"] else "ok",
            "observed_pairs": check["observed_pairs"],
            "unresolved_pairs": check["unresolved_pairs"],
            "uncovered_pairs": check["uncovered_pairs"],
            "uncovered_offer_share": check["uncovered_offer_share"],
            "reasons": check["reasons"],
            "worst": [
                {
                    **item,
                    "base": (card_base.by_dbf.get(item["base_dbf"]) or {}).get("id"),
                    "donor": (card_base.by_dbf.get(item["donor_dbf"]) or {}).get("id"),
                }
                for item in check["worst"]
            ],
            "foreign_class_offer_share": (
                round(_pct(foreign, shown) or 0.0, 2) if not is_curated(card) else None
            ),
            "surplus": {
                "min_base_offered": MIN_BASE_OFFERED,
                "bases": checked_bases,
                "pool_candidates": pool_total,
                "never_offered": surplus,
                "never_offered_share": round(uncovered, 1)
                if uncovered is not None
                else None,
                "alert": bool(
                    uncovered is not None and uncovered > SURPLUS_ALERT_SHARE
                ),
            },
        }
    )
    return entry


def is_curated(card: Mapping[str, Any]) -> bool:
    return player_class_of(card) is None and not is_class_dependent(card)


def build_report(
    card_base: pools.CardBase,
    formats: Mapping[str, Any],
    format_names: tuple[str, ...] = tuple(FORMATS),
) -> dict[str, Any]:
    texts = AssembleTexts(card_base)
    rows: list[dict[str, Any]] = []
    for format_name in format_names:
        snapshot = formats.get(format_name)
        for card in assemble_cards(card_base, format_name):
            rows.append(card_report(card_base, texts, card, format_name, snapshot))
    return {
        "mismatch_share_threshold": POOL_MISMATCH_SHARE,
        "rows": rows,
        "ok": all(row["state"] == "ok" for row in rows),
    }


def load_snapshot_file(path: str) -> dict[str, Any]:
    return _stored_formats(json.loads(Path(path).read_text(encoding="utf-8")))


def _surplus_cell(surplus: Mapping[str, Any]) -> str:
    if not surplus["bases"]:
        return f"n/a (no base with {surplus['min_base_offered']}+ shows)"
    return (
        f"{surplus['never_offered']}/{surplus['pool_candidates']}"
        f" = {surplus['never_offered_share']}% ({surplus['bases']} bases)"
        + (" ALERT" if surplus["alert"] else "")
    )


def render_table(report: Mapping[str, Any]) -> str:
    lines = [
        "card      format    state     pairs  unres  uncov  uncov_share%  foreign%  never_offered(bases/pool)",
    ]
    details: list[str] = []
    for row in report["rows"]:
        if row["state"] == "no_data":
            lines.append(f"{row['card_id']:<9} {row['format']:<8}  no_data")
            continue
        surplus = row["surplus"]
        foreign = row["foreign_class_offer_share"]
        lines.append(
            f"{row['card_id']:<9} {row['format']:<8}  {row['state']:<8}"
            f" {row['observed_pairs']:>6} {row['unresolved_pairs']:>6}"
            f" {row['uncovered_pairs']:>6}"
            f" {row['uncovered_offer_share']:>12.4f}"
            f" {'-' if foreign is None else format(foreign, '.2f'):>9}"
            f"  {_surplus_cell(surplus)}"
        )
        for item in row["worst"]:
            details.append(
                f"  {row['card_id']} {row['format']}: {item['base']} + {item['donor']}"
                f" offered={item['offered']} reason={item['reason']}"
            )
    if details:
        lines.append("")
        lines.append("Worst uncovered pairs (up to 5 per card and format):")
        lines.extend(details)
    return "\n".join(lines)
