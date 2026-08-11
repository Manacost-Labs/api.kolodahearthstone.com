from __future__ import annotations

from typing import Any

from .config import dataset_regression_drop_ratio
from .post_patch_policy import policy_for
from .source_contracts import regression_drop_ratio_for_source
from .sources import Source


def _drop_ratio_for_source(source: Source) -> float:
    return regression_drop_ratio_for_source(source.id, dataset_regression_drop_ratio())


def estimate_metric_count(source: Source, data: dict[str, Any]) -> int:
    """Approximate row/card count for regression comparison."""
    structured = data.get("structured") or data.get("hsreplay_extracted") or {}
    stype = structured.get("type")

    if stype == "card_stats":
        return len(structured.get("cards") or [])
    if stype == "hsreplay_meta_archetypes":
        return sum(
            len(class_group.get("archetypes") or [])
            for class_group in (structured.get("classes") or [])
        )
    if stype == "arena_card_tiers":
        return len(structured.get("cards") or [])
    if stype == "arena_winning_decks":
        return len(structured.get("decks") or [])
    if stype == "bg_comps":
        return len(structured.get("comps") or [])
    if stype == "bg_card_stats":
        tiers = structured.get("tiers") or {}
        return sum(len(cards) for cards in tiers.values())
    if stype == "bg_trinkets":
        return sum(
            1
            for item in (structured.get("trinkets") or [])
            if item.get("pick_rate") or item.get("avg_placement")
        )
    if stype == "bg_heroes":
        return len(structured.get("heroes") or [])
    if stype == "bg_minions":
        return len(structured.get("minions") or [])
    if stype == "bg_compositions":
        return len(structured.get("compositions") or [])
    if stype == "arena_class_matrix":
        # dual-class арена удалена из игры: matchups всегда пуст,
        # для регрессии сравниваем только классы.
        return len(structured.get("classes") or [])
    if stype == "arena_legendary_groups":
        return len(structured.get("groups") or [])
    if stype == "heartharena_tierlist":
        return int(structured.get("total_cards") or 0) or sum(
            len(cl.get("cards") or []) for cl in (structured.get("classes") or [])
        )
    if stype == "hearthstone_decks":
        return int(structured.get("total_decks") or 0) or len(structured.get("decks") or [])
    if stype == "meta":
        return len(structured.get("strategies") or [])
    if stype == "matchups":
        return len(structured.get("matchups") or [])
    if stype == "streamer_decks":
        return len(structured.get("rows") or [])
    if stype == "vicious_syndicate_radars":
        return len(structured.get("radars") or [])
    if stype == "vicious_live":
        return len(structured.get("class_distribution") or []) + sum(
            len(row.get("decks") or []) for row in (structured.get("tier_list") or [])
        )
    if stype == "trending_decks":
        return len(structured.get("decks") or [])
    if stype == "metastats_decks":
        return len(structured.get("decks") or [])
    if stype == "firestone_standard":
        return len(structured.get("decks") or []) + len(
            structured.get("archetypes") or []
        )
    if stype == "metastats_matchups":
        return len(structured.get("matchups") or [])

    tables = data.get("tables") or []
    table_rows = sum(len(t.get("objects") or t.get("rows") or []) for t in tables)
    if table_rows:
        return table_rows

    decks = (structured.get("decks") or []) if isinstance(structured, dict) else []
    if decks:
        return len(decks)

    text_lines = data.get("text_preview") or []
    return len(text_lines)


def estimate_filled_metric_count(source: Source, data: dict[str, Any]) -> int:
    """Approximate records that still have the fields users rely on."""
    structured = data.get("structured") or data.get("hsreplay_extracted") or {}
    stype = structured.get("type")

    if stype == "card_stats":
        return sum(
            1
            for card in (structured.get("cards") or [])
            if card.get("deck_winrate") or card.get("deck_popularity")
        )
    if stype == "hsreplay_meta_archetypes":
        return sum(
            1
            for class_group in (structured.get("classes") or [])
            for archetype in (class_group.get("archetypes") or [])
            if archetype.get("winrate") and archetype.get("popularity") and archetype.get("games")
        )
    if stype == "vicious_syndicate_radars":
        return sum(
            1
            for radar in (structured.get("radars") or [])
            if radar.get("nodes") or radar.get("edges")
        )
    if stype == "vicious_live":
        return len(structured.get("class_distribution") or []) + sum(
            1
            for row in (structured.get("tier_list") or [])
            for deck in (row.get("decks") or [])
            if deck.get("deck") and deck.get("winrate")
        )
    if stype == "arena_card_tiers":
        return sum(
            1
            for card in (structured.get("cards") or [])
            if card.get("tier") or card.get("win_rate") or card.get("deck_winrate")
        )
    if stype == "bg_comps":
        return sum(
            1
            for comp in (structured.get("comps") or [])
            if comp.get("main_cards") or comp.get("additional_cards")
        )
    if stype == "bg_trinkets":
        return sum(
            1
            for item in (structured.get("trinkets") or [])
            if item.get("trinket_id") and (item.get("pick_rate") or item.get("avg_placement"))
        )
    if stype == "bg_minions":
        return sum(
            1
            for item in (structured.get("minions") or [])
            if item.get("impact") is not None and item.get("win_share") and item.get("popularity")
        )
    if stype == "bg_compositions":
        return sum(
            1
            for item in (structured.get("compositions") or [])
            if item.get("first_place") and item.get("avg_placement") is not None and item.get("popularity")
        )
    if stype == "hearthstone_decks":
        return sum(1 for deck in (structured.get("decks") or []) if deck.get("deck_code"))
    if stype == "firestone_standard":
        return sum(
            1
            for collection in ("decks", "archetypes")
            for row in (structured.get(collection) or [])
            if (
                row.get("archetype_id") is not None
                and row.get("archetype_name")
                and row.get("player_class")
                and row.get("games") is not None
                and row.get("wins") is not None
                and row.get("winrate") is not None
            )
        )
    return estimate_metric_count(source, data)


def check_dataset_regression(
    source: Source,
    *,
    previous_data: dict[str, Any] | None,
    new_data: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    """
    Returns (regression_detected, message, extra).
    Regression = new count < previous * (1 - drop_ratio).
    """
    if not previous_data:
        return False, None, {}

    prev_url = previous_data.get("url") or previous_data.get("fetch_url")
    new_url = new_data.get("url") or new_data.get("fetch_url") or source.url
    if prev_url and new_url and prev_url.rstrip("/") != new_url.rstrip("/"):
        return False, None, {"url_changed": True, "prev_url": prev_url, "new_url": new_url}

    prev_count = estimate_metric_count(source, previous_data)
    new_count = estimate_metric_count(source, new_data)
    prev_filled = estimate_filled_metric_count(source, previous_data)
    new_filled = estimate_filled_metric_count(source, new_data)
    extra = {
        "rows_before": prev_count,
        "rows_after": new_count,
        "filled_before": prev_filled,
        "filled_after": new_filled,
        "drop_ratio": _drop_ratio_for_source(source),
    }
    if policy_for(source.id) is not None:
        extra["post_patch_regression_bypass"] = True
        return False, None, extra
    if prev_count < 10:
        return False, None, extra

    ratio = float(extra["drop_ratio"])
    previous_structured = (
        previous_data.get("structured")
        or previous_data.get("hsreplay_extracted")
        or {}
    )
    new_structured = (
        new_data.get("structured") or new_data.get("hsreplay_extracted") or {}
    )
    if (
        previous_structured.get("type") == "firestone_standard"
        and new_structured.get("type") == "firestone_standard"
    ):
        collection_counts: dict[str, dict[str, int]] = {}
        for collection in ("decks", "archetypes"):
            before = len(previous_structured.get(collection) or [])
            after = len(new_structured.get(collection) or [])
            collection_threshold = int(before * (1.0 - ratio))
            collection_counts[collection] = {
                "before": before,
                "after": after,
                "threshold": collection_threshold,
            }
            if before >= 10 and after < collection_threshold:
                extra["collections"] = collection_counts
                pct = round(100.0 * (before - after) / before, 1)
                msg = (
                    "Dataset regression: Firestone Standard "
                    f"{collection} count dropped {before} -> {after} "
                    f"({pct}% decrease, threshold {int(ratio * 100)}%)"
                )
                return True, msg, extra
        extra["collections"] = collection_counts

    threshold = int(prev_count * (1.0 - ratio))
    if new_count < threshold:
        pct = round(100.0 * (prev_count - new_count) / prev_count, 1)
        msg = (
            f"Dataset regression: metric count dropped {prev_count} -> {new_count} "
            f"({pct}% decrease, threshold {int(ratio * 100)}%)"
        )
        return True, msg, extra
    if prev_filled >= 10:
        filled_threshold = int(prev_filled * (1.0 - ratio))
        if new_filled < filled_threshold:
            pct = round(100.0 * (prev_filled - new_filled) / prev_filled, 1)
            msg = (
                f"Dataset regression: filled metric count dropped {prev_filled} -> {new_filled} "
                f"({pct}% decrease, threshold {int(ratio * 100)}%)"
            )
            return True, msg, extra
    return False, None, extra
