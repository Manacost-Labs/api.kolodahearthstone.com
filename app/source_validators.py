from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from .hsreplay_card_periods import (
    HSREPLAY_CARD_PERIOD_SOURCE_IDS,
    STANDARD_HSREPLAY_CARD_PERIOD_SOURCE_IDS,
)
from .parsing_normalize import parse_decimal, parse_percent
from .post_patch_policy import (
    HSREPLAY_CURRENT_PATCH_EARLY_SOURCE_IDS,
    effective_arena_card_minimum,
    effective_heartharena_thresholds,
    policy_for,
)
from .quality_thresholds import threshold_for
from .source_contracts import is_decodable_deck_code
from .trinket_slices import TRINKET_SLICE_SOURCE_IDS

ARENA_PERCENT_FIELDS = (
    "deck_winrate",
    "win_rate",
    "winrate_when_drawn",
    "winrate_when_played",
    "in_runs",
    "pick_rate",
    "offer_rate",
    "popularity",
    "drawn_winrate",
    "mulligan_winrate",
    "kept_rate",
)

FIRESTONE_STANDARD_MAX_UPSTREAM_AGE_HOURS = 36.0
FIRESTONE_STANDARD_MAX_FUTURE_SKEW_HOURS = 6.0


def _parse_arena_percent(value: Any) -> float | None:
    # parse_percent delegates to a legacy helper that treats numeric zero as empty.
    # Zero is a valid percentage for freshly collected post-patch rows.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_percent(value)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"
    field: str | None = None


@dataclass
class ValidationReport:
    ok: bool = True
    score: float = 1.0
    issues: list[ValidationIssue] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def add_issue(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        severity: str = "error",
    ) -> None:
        self.issues.append(
            ValidationIssue(code=code, message=message, field=field, severity=severity)
        )
        if severity == "error":
            self.ok = False

    @property
    def reason(self) -> str:
        return "; ".join(issue.message for issue in self.issues) or "ok"


def _valid_name(value: Any) -> bool:
    return str(value or "").strip() not in {"", "-", "—", "Unknown"}


def _validation_now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_aware_iso_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _validate_bg_heroes(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    heroes = [row for row in (structured.get("heroes") or []) if isinstance(row, dict)]
    row_count = len(heroes)
    names = [str(row.get("hero") or "").strip() for row in heroes]
    dbf_ids = [row.get("dbfId") for row in heroes if row.get("dbfId") is not None]
    avg_values = [parse_decimal(row.get("avg_placement")) for row in heroes]
    pick_rates = [parse_percent(row.get("pick_rate")) for row in heroes]
    distributions = [
        row.get("placement_distribution")
        for row in heroes
        if isinstance(row.get("placement_distribution"), list)
    ]
    valid_names = sum(1 for name in names if _valid_name(name))
    valid_avg = sum(1 for value in avg_values if value is not None and 1.0 <= value <= 8.0)
    valid_pick = sum(1 for value in pick_rates if value is not None and value >= 0.0)
    valid_distributions = 0
    for dist in distributions:
        if len(dist) != 8:
            continue
        parsed = [parse_percent(value) for value in dist]
        if any(value is None for value in parsed):
            continue
        total = sum(value for value in parsed if value is not None)
        if 98.0 <= total <= 102.0:
            valid_distributions += 1

    unique_names = len({name for name in names if _valid_name(name)})
    unique_avg = len({round(value, 2) for value in avg_values if value is not None})
    unique_tiers = len({str(row.get("tier") or "").upper() for row in heroes if row.get("tier")})
    unique_dbf = len({int(value) for value in dbf_ids if str(value).isdigit()})

    report.metrics.update(
        {
            "rows": row_count,
            "valid_names": valid_names,
            "unique_names": unique_names,
            "unique_dbf": unique_dbf,
            "valid_avg_placement": valid_avg,
            "unique_avg_placement": unique_avg,
            "valid_pick_rate": valid_pick,
            "valid_distributions": valid_distributions,
            "unique_tiers": unique_tiers,
        }
    )

    if row_count < 30:
        report.add_issue("bg_heroes.too_few_rows", f"bg heroes too few ({row_count} < 30)")
    if valid_names < max(20, int(row_count * 0.7)):
        report.add_issue(
            "bg_heroes.bad_names",
            f"bg heroes valid names too low ({valid_names}/{row_count})",
            field="hero",
        )
    if unique_names < 20:
        report.add_issue(
            "bg_heroes.low_name_diversity",
            f"bg heroes unique names too low ({unique_names})",
            field="hero",
        )
    if unique_dbf < max(20, int(row_count * 0.7)):
        report.add_issue(
            "bg_heroes.low_dbf_diversity",
            f"bg heroes unique dbfIds too low ({unique_dbf}/{row_count})",
            field="dbfId",
        )
    if valid_pick < max(20, int(row_count * 0.7)):
        report.add_issue(
            "bg_heroes.bad_pick_rate",
            f"bg heroes valid pick_rate too low ({valid_pick}/{row_count})",
            field="pick_rate",
        )
    if valid_avg < max(20, int(row_count * 0.7)):
        report.add_issue(
            "bg_heroes.bad_avg_placement",
            f"bg heroes valid avg_placement too low ({valid_avg}/{row_count})",
            field="avg_placement",
        )
    if unique_avg < 10:
        report.add_issue(
            "bg_heroes.low_avg_diversity",
            f"bg heroes avg_placement diversity too low ({unique_avg})",
            field="avg_placement",
        )
    if valid_distributions < max(20, int(row_count * 0.7)):
        report.add_issue(
            "bg_heroes.bad_distribution",
            f"bg heroes valid placement_distribution too low ({valid_distributions}/{row_count})",
            field="placement_distribution",
        )
    if unique_tiers < 2:
        report.add_issue(
            "bg_heroes.low_tier_diversity",
            f"bg heroes tier diversity too low ({unique_tiers})",
            field="tier",
        )

    denominator = max(row_count, 1)
    scores = [
        valid_names / denominator,
        valid_pick / denominator,
        valid_avg / denominator,
        valid_distributions / denominator,
        min(unique_names / 30.0, 1.0),
        min(unique_avg / 10.0, 1.0),
        min(unique_dbf / max(row_count, 1), 1.0),
    ]
    report.score = round(sum(scores) / len(scores), 4)
    return report


def _validate_vicious_live(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    class_distribution = [
        row for row in (structured.get("class_distribution") or []) if isinstance(row, dict)
    ]
    tier_list = [
        row for row in (structured.get("tier_list") or []) if isinstance(row, dict)
    ]
    tier_deck_count = sum(len(row.get("decks") or []) for row in tier_list)
    distribution_names = {
        str(row.get("deck") or "").strip()
        for row in (structured.get("deck_distribution") or [])
        if isinstance(row, dict) and _valid_name(row.get("deck"))
    }
    tier_names = {
        str(deck.get("deck") or "").strip()
        for bracket in (structured.get("tier_list") or [])
        if isinstance(bracket, dict)
        for deck in (bracket.get("decks") or [])
        if isinstance(deck, dict) and _valid_name(deck.get("deck"))
    }
    deck_names = distribution_names | tier_names
    placeholder_names = {
        name
        for name in deck_names
        if re.fullmatch(r"(?:Other|Unknown)\s+\S+", name, flags=re.IGNORECASE)
    }
    named_archetypes = deck_names - placeholder_names
    placeholder_ratio = len(placeholder_names) / max(len(deck_names), 1)
    report.metrics.update(
        {
            "upstream_state": structured.get("upstream_state"),
            "unique_decks": len(deck_names),
            "named_archetypes": len(named_archetypes),
            "placeholder_decks": len(placeholder_names),
            "placeholder_ratio": round(placeholder_ratio, 4),
            "classes": len(class_distribution),
            "tier_brackets": len(tier_list),
            "tier_decks": tier_deck_count,
        }
    )

    upstream_state = str(structured.get("upstream_state") or "ready")
    if upstream_state != "ready":
        report.add_issue(
            "vicious_live.upstream_not_ready",
            f"vicious live upstream is not ready ({upstream_state})",
            field="upstream_state",
        )

    if len(class_distribution) < 8:
        report.add_issue(
            "vicious_live.too_few_classes",
            f"vicious live too few classes ({len(class_distribution)} < 8)",
            field="class_distribution",
        )
    if len(tier_list) < 3 or tier_deck_count < 20:
        report.add_issue(
            "vicious_live.too_few_tier_decks",
            f"vicious live tier data too small ({len(tier_list)} brackets, {tier_deck_count} decks)",
            field="tier_list",
        )
    if len(named_archetypes) < 3:
        report.add_issue(
            "vicious_live.too_few_named_archetypes",
            f"vicious live named archetypes too few ({len(named_archetypes)} < 3)",
            field="deck",
        )
    if placeholder_ratio > 0.75:
        report.add_issue(
            "vicious_live.placeholder_dominated",
            f"vicious live placeholder decks dominate ({len(placeholder_names)}/{len(deck_names)})",
            field="deck",
        )
    report.score = round(
        sum(
            (
                min(len(class_distribution) / 8.0, 1.0),
                min(len(tier_list) / 3.0, 1.0),
                min(tier_deck_count / 20.0, 1.0),
                min(len(named_archetypes) / 8.0, 1.0) * (1.0 - placeholder_ratio),
            )
        )
        / 4,
        4,
    )
    return report


def _validate_vicious_radars(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    issue_raw = str(structured.get("issue") or "")
    latest_issue_raw = str(structured.get("latest_report_issue") or "")
    issue = int(issue_raw) if issue_raw.isdigit() else None
    latest_issue = int(latest_issue_raw) if latest_issue_raw.isdigit() else None
    published_raw = str(structured.get("latest_report_published_at") or "")
    content_age_days: int | None = None
    try:
        published_at = datetime.fromisoformat(published_raw)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        content_age_days = max(0, (datetime.now(UTC) - published_at).days)
    except ValueError:
        pass
    report.metrics.update(
        {
            "radar_issue": issue,
            "latest_report_issue": latest_issue,
            "latest_report_published_at": published_raw or None,
            "content_age_days": content_age_days,
        }
    )

    if issue is None or latest_issue is None:
        report.add_issue(
            "vicious_radars.missing_issue_freshness",
            "vicious radars missing active/latest report issue metadata",
            field="issue",
        )
    elif issue < latest_issue:
        report.add_issue(
            "vicious_radars.outdated_issue",
            f"vicious radar issue is outdated ({issue} < {latest_issue})",
            field="issue",
            severity="warning",
        )
    if content_age_days is None:
        report.add_issue(
            "vicious_radars.missing_published_at",
            "vicious latest report publication date is missing or invalid",
            field="latest_report_published_at",
        )
    elif content_age_days > 21:
        report.add_issue(
            "vicious_radars.stale_content",
            f"vicious latest report content is stale ({content_age_days} days > 21)",
            field="latest_report_published_at",
            severity="warning",
        )
    issue_score = 1.0 if issue is not None and issue == latest_issue else 0.0
    age_score = 1.0 if content_age_days is not None and content_age_days <= 21 else 0.0
    report.score = (issue_score + age_score) / 2
    return report


def _validate_arena_class_matrix(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    classes = [row for row in (structured.get("classes") or []) if isinstance(row, dict)]
    report.metrics["classes"] = len(classes)
    if len(classes) < 8:
        report.add_issue(
            "arena_class_matrix.too_few_classes",
            f"arena class stats too few ({len(classes)} < 8)",
            field="classes",
        )
    report.score = round(min(len(classes) / 8.0, 1.0), 4)
    return report


def _validate_arena_class_pages(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    classes = [row for row in (structured.get("classes") or []) if isinstance(row, dict)]
    with_stats = sum(
        1
        for row in classes
        if row.get("win_rate") is not None and row.get("pick_rate") is not None
    )
    report.metrics.update({"classes": len(classes), "classes_with_stats": with_stats})
    if len(classes) < 10:
        report.add_issue(
            "arena_class_pages.too_few_classes",
            f"arena class pages too few ({len(classes)} < 10)",
            field="classes",
        )
    if with_stats < 10:
        report.add_issue(
            "arena_class_pages.missing_stats",
            f"arena class pages missing stats ({with_stats}/{len(classes)}; minimum 10)",
            field="win_rate,pick_rate",
        )
    report.score = round(
        (min(len(classes) / 10.0, 1.0) + min(with_stats / 10.0, 1.0)) / 2,
        4,
    )
    return report


def _validate_arena_winning_decks(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    decks = [row for row in (structured.get("decks") or []) if isinstance(row, dict)]
    with_final_deck = sum(1 for row in decks if row.get("final_deck"))
    report.metrics.update({"decks": len(decks), "decks_with_final_deck": with_final_deck})
    if not decks:
        report.add_issue(
            "arena_winning_decks.empty",
            "arena winning decks empty",
            field="decks",
        )
    if with_final_deck < 1:
        report.add_issue(
            "arena_winning_decks.missing_final_deck",
            "arena winning decks missing final_deck",
            field="final_deck",
        )
    report.score = 1.0 if decks and with_final_deck else 0.0
    return report


def _validate_arena_legendary_groups(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    groups = [row for row in (structured.get("groups") or []) if isinstance(row, dict)]
    with_key_card = sum(1 for row in groups if row.get("key_card"))
    with_pick = sum(1 for row in groups if row.get("pick_rate") not in (None, ""))
    with_offer = sum(1 for row in groups if row.get("offer_rate") not in (None, ""))
    with_score = sum(1 for row in groups if row.get("score") is not None)
    with_winrate = sum(1 for row in groups if row.get("winrate") not in (None, ""))
    report.metrics.update(
        {
            "groups": len(groups),
            "groups_with_key_card": with_key_card,
            "groups_with_winrate": with_winrate,
            "groups_with_pick_rate": with_pick,
            "groups_with_offer_rate": with_offer,
            "groups_with_score": with_score,
        }
    )
    if len(groups) < 10:
        report.add_issue(
            "arena_legendary_groups.too_few_groups",
            f"legendary groups too few ({len(groups)} < 10)",
            field="groups",
        )
    if with_key_card < 1:
        report.add_issue(
            "arena_legendary_groups.missing_key_card",
            "legendary groups missing key_card",
            field="key_card",
        )
    # Arenasmith footer metrics (pick/offer/score) should cover most groups after enrich.
    metrics_floor = max(5, len(groups) // 2) if groups else 5
    if groups and with_score < metrics_floor:
        report.add_issue(
            "arena_legendary_groups.missing_score_metrics",
            f"legendary score fill too low ({with_score}/{len(groups)}; minimum {metrics_floor})",
            field="score",
        )
    if groups and with_pick < metrics_floor:
        report.add_issue(
            "arena_legendary_groups.missing_pick_rate",
            f"legendary pick_rate fill too low ({with_pick}/{len(groups)}; minimum {metrics_floor})",
            field="pick_rate",
        )
    if groups and with_offer < metrics_floor:
        report.add_issue(
            "arena_legendary_groups.missing_offer_rate",
            f"legendary offer_rate fill too low ({with_offer}/{len(groups)}; minimum {metrics_floor})",
            field="offer_rate",
        )
    fill_score = (
        min(with_pick / max(len(groups), 1), 1.0)
        + min(with_offer / max(len(groups), 1), 1.0)
        + min(with_score / max(len(groups), 1), 1.0)
    ) / 3.0
    report.score = round(
        (min(len(groups) / 10.0, 1.0) + min(with_key_card, 1) + fill_score) / 3,
        4,
    )
    return report


def _validate_bg_comps(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    comps = [row for row in (structured.get("comps") or []) if isinstance(row, dict)]
    with_cards = sum(
        1 for row in comps if row.get("main_cards") or row.get("additional_cards")
    )
    minimum_with_cards = max(3, len(comps) // 2)
    report.metrics.update(
        {
            "comps": len(comps),
            "comps_with_cards": with_cards,
            "minimum_with_cards": minimum_with_cards,
        }
    )
    if len(comps) < 3:
        report.add_issue(
            "bg_comps.too_few_comps",
            f"bg comps too few ({len(comps)} < 3)",
            field="comps",
        )
    if with_cards < minimum_with_cards:
        report.add_issue(
            "bg_comps.mostly_empty",
            f"bg comps mostly empty ({with_cards}/{len(comps)} with cards; minimum {minimum_with_cards})",
            field="main_cards,additional_cards",
        )
    report.score = round(
        (min(len(comps) / 3.0, 1.0) + min(with_cards / max(minimum_with_cards, 1), 1.0)) / 2,
        4,
    )
    return report


def _validate_bg_card_stats(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    tiers = structured.get("tiers") or {}
    cards = [
        card
        for tier_cards in tiers.values()
        if isinstance(tier_cards, list)
        for card in tier_cards
        if isinstance(card, dict)
    ] if isinstance(tiers, dict) else []
    with_stats = sum(
        1
        for card in cards
        if card.get("average_placement") is not None or card.get("total_played")
    )
    report.metrics.update({"cards": len(cards), "cards_with_stats": with_stats})
    if len(cards) < 50:
        report.add_issue(
            "bg_card_stats.too_few_cards",
            f"bg card stats too few ({len(cards)} < 50)",
            field="tiers",
        )
    if with_stats < 40:
        report.add_issue(
            "bg_card_stats.missing_stats",
            f"bg card stats missing placement stats ({with_stats}/{len(cards)}; minimum 40)",
            field="average_placement,total_played",
        )
    report.score = round(
        (min(len(cards) / 50.0, 1.0) + min(with_stats / 40.0, 1.0)) / 2,
        4,
    )
    return report


def _validate_bg_trinkets(source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    trinkets = [row for row in (structured.get("trinkets") or []) if isinstance(row, dict)]
    tier_counts = {"lesser": 0, "greater": 0}
    for row in trinkets:
        tier = str(
            row.get("trinket_tier") or row.get("type") or row.get("group") or ""
        ).strip().lower()
        for expected_tier in tier_counts:
            if expected_tier in tier:
                tier_counts[expected_tier] += 1
                break
    valid = [
        row
        for row in trinkets
        if row.get("pick_rate")
        and len(str(row.get("name") or "")) >= 4
        and str(row.get("name") or "")[:1].isalnum()
    ]
    minimum_valid = max(6, len(trinkets) // 2)
    complete_descriptions = [
        row
        for row in trinkets
        if len(str(row.get("description") or "").strip()) >= 20
        and "92" not in str(row.get("description") or "")
        and "|4(" not in str(row.get("description") or "")
        and re.search(r'[.!?)]$|["”»]$', str(row.get("description") or "").strip())
    ]
    minimum_complete_descriptions = math.ceil(len(trinkets) * 0.90)
    report.metrics.update(
        {
            "trinkets": len(trinkets),
            "valid_trinkets": len(valid),
            "minimum_valid": minimum_valid,
            "complete_descriptions": len(complete_descriptions),
            "minimum_complete_descriptions": minimum_complete_descriptions,
            "parser_level": structured.get("parser_level"),
            "dropped_rows": int(structured.get("dropped_rows") or 0),
            "lesser_trinkets": tier_counts["lesser"],
            "greater_trinkets": tier_counts["greater"],
        }
    )
    parser_level = str(structured.get("parser_level") or "primary")
    if parser_level != "primary":
        report.add_issue(
            "bg_trinkets.fallback_parser",
            f"bg trinkets parsed with fallback level {parser_level}",
            field="parser_level",
            severity="warning",
        )
    if len(trinkets) < 8:
        report.add_issue(
            "bg_trinkets.too_few_rows",
            f"bg trinkets too few ({len(trinkets)} < 8)",
            field="trinkets",
        )
    if source_id in TRINKET_SLICE_SOURCE_IDS and min(tier_counts.values()) < 4:
        report.add_issue(
            "bg_trinkets.incomplete_tier_mix",
            (
                "combined bg trinkets slice is incomplete "
                f"(lesser={tier_counts['lesser']}, greater={tier_counts['greater']}; "
                "minimum 4 each)"
            ),
            field="trinket_tier",
        )
    if len(valid) < minimum_valid:
        report.add_issue(
            "bg_trinkets.invalid_names_or_stats",
            f"bg trinkets invalid names/stats ({len(valid)}/{len(trinkets)}; minimum {minimum_valid})",
            field="name,pick_rate",
        )
    if len(complete_descriptions) < minimum_complete_descriptions:
        report.add_issue(
            "bg_trinkets.incomplete_descriptions",
            (
                "bg trinkets have incomplete descriptions "
                f"({len(complete_descriptions)}/{len(trinkets)}; "
                f"minimum {minimum_complete_descriptions})"
            ),
            field="description",
        )
    report.score = round(
        (
            min(len(trinkets) / 8.0, 1.0)
            + min(len(valid) / max(minimum_valid, 1), 1.0)
            + min(
                len(complete_descriptions) / max(minimum_complete_descriptions, 1),
                1.0,
            )
        )
        / 3,
        4,
    )
    return report


def _validate_bg_minions(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    minions = [row for row in (structured.get("minions") or []) if isinstance(row, dict)]
    with_stats = sum(
        1
        for row in minions
        if row.get("impact") is not None and row.get("win_share") and row.get("popularity")
    )
    report.metrics.update({"minions": len(minions), "minions_with_stats": with_stats})
    if len(minions) < 50:
        report.add_issue(
            "bg_minions.too_few_rows",
            f"bg minions too few ({len(minions)} < 50)",
            field="minions",
        )
    if with_stats < 40:
        report.add_issue(
            "bg_minions.missing_stats",
            f"bg minions missing stats ({with_stats}/{len(minions)}; minimum 40)",
            field="impact,win_share,popularity",
        )
    report.score = round(
        (min(len(minions) / 50.0, 1.0) + min(with_stats / 40.0, 1.0)) / 2,
        4,
    )
    return report


def _validate_bg_compositions(_source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    compositions = [
        row for row in (structured.get("compositions") or []) if isinstance(row, dict)
    ]
    with_stats = sum(
        1
        for row in compositions
        if row.get("first_place")
        and row.get("avg_placement") is not None
        and row.get("popularity")
    )
    report.metrics.update(
        {"compositions": len(compositions), "compositions_with_stats": with_stats}
    )
    if len(compositions) < 5:
        report.add_issue(
            "bg_compositions.too_few_rows",
            f"bg compositions too few ({len(compositions)} < 5)",
            field="compositions",
        )
    if with_stats < 5:
        report.add_issue(
            "bg_compositions.missing_stats",
            f"bg compositions missing stats ({with_stats}/{len(compositions)}; minimum 5)",
            field="first_place,avg_placement,popularity",
        )
    report.score = round(
        (min(len(compositions) / 5.0, 1.0) + min(with_stats / 5.0, 1.0)) / 2,
        4,
    )
    return report


def _validate_arena_card_tiers(source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    cards = [row for row in (structured.get("cards") or []) if isinstance(row, dict)]
    default_min = 20 if "legendary" in source_id else 100
    configured_minimum = int(threshold_for(source_id, "arena_card_tiers_min", default_min))
    minimum_cards = effective_arena_card_minimum(source_id, configured_minimum)
    has_tier_labels = "firestone" in source_id or any(
        row.get("tier")
        or row.get("win_rate") is not None
        or row.get("deck_winrate")
        for row in cards[:50]
    )
    policy = policy_for(source_id)
    card_ids = [
        str(row.get("card_id") or row.get("id") or "").strip()
        for row in cards
        if str(row.get("card_id") or row.get("id") or "").strip()
    ]
    unique_card_ids = set(card_ids)
    duplicate_card_ids = len(card_ids) - len(unique_card_ids)
    parsed_winrates = [
        (
            row.get("deck_winrate")
            if row.get("deck_winrate") is not None
            else row.get("win_rate")
        )
        for row in cards
    ]
    valid_winrates = sum(
        1
        for raw_value in parsed_winrates
        if (value := _parse_arena_percent(raw_value))
        is not None
        and 0.0 <= value <= 100.0
    )
    invalid_winrates = sum(
        1
        for raw_value in parsed_winrates
        if raw_value not in (None, "")
        and (
            (value := _parse_arena_percent(raw_value)) is None
            or not 0.0 <= value <= 100.0
        )
    )
    invalid_percent_values: list[tuple[str, Any]] = []
    for row in cards:
        for field_name in ARENA_PERCENT_FIELDS:
            raw_value = row.get(field_name)
            if raw_value in (None, ""):
                continue
            parsed_value = _parse_arena_percent(raw_value)
            if parsed_value is None or not 0.0 <= parsed_value <= 100.0:
                invalid_percent_values.append((field_name, raw_value))
    report.metrics.update(
        {
            "cards": len(cards),
            "minimum_cards": minimum_cards,
            "has_tier_labels": has_tier_labels,
            "unique_card_ids": len(unique_card_ids),
            "duplicate_card_ids": duplicate_card_ids,
            "valid_winrates": valid_winrates,
            "invalid_winrates": invalid_winrates,
            "invalid_percent_values": len(invalid_percent_values),
            "invalid_percent_fields": sorted(
                {field_name for field_name, _ in invalid_percent_values}
            ),
        }
    )
    if len(cards) < minimum_cards:
        report.add_issue(
            "arena_card_tiers.too_few_cards",
            f"arena card tiers too few ({len(cards)} < {minimum_cards})",
            field="cards",
        )
    if not has_tier_labels:
        report.add_issue(
            "arena_card_tiers.missing_tier_labels",
            "arena card tiers missing tier labels",
            field="tier,win_rate,deck_winrate",
        )
    if policy is not None:
        required_valid_rows = max(1, math.ceil(len(cards) * 0.80))
        if len(unique_card_ids) < required_valid_rows:
            report.add_issue(
                "arena_card_tiers.low_id_diversity",
                (
                    "arena card tiers unique card ids too low "
                    f"({len(unique_card_ids)} < {required_valid_rows})"
                ),
                field="card_id,id",
            )
        if duplicate_card_ids:
            report.add_issue(
                "arena_card_tiers.duplicate_card_ids",
                f"arena card tiers contain duplicate card ids ({duplicate_card_ids})",
                field="card_id,id",
            )
        if valid_winrates < required_valid_rows:
            report.add_issue(
                "arena_card_tiers.invalid_winrates",
                (
                    "arena card tiers valid winrates too low "
                    f"({valid_winrates} < {required_valid_rows})"
                ),
                field="deck_winrate,win_rate",
            )
        if invalid_winrates:
            report.add_issue(
                "arena_card_tiers.impossible_winrates",
                f"arena card tiers contain invalid winrates ({invalid_winrates})",
                field="deck_winrate,win_rate",
            )
        if invalid_percent_values:
            invalid_fields = sorted(
                {field_name for field_name, _ in invalid_percent_values}
            )
            report.add_issue(
                "arena_card_tiers.impossible_percentages",
                (
                    "arena card tiers contain out-of-range percentage values "
                    f"({len(invalid_percent_values)} across {', '.join(invalid_fields)})"
                ),
                field=",".join(invalid_fields),
            )
        if source_id == "firestone_arena_cards_normal":
            rows_with_sample = sum(
                1
                for row in cards
                if (
                    parse_decimal(row.get("total_games") or row.get("times_played"))
                    or 0
                )
                >= policy.minimum_sample
            )
            report.metrics["rows_with_minimum_sample"] = rows_with_sample
            report.metrics["minimum_sample"] = policy.minimum_sample
            if rows_with_sample < required_valid_rows:
                report.add_issue(
                    "arena_card_tiers.low_sample",
                    (
                        "firestone arena rows with sufficient sample too low "
                        f"({rows_with_sample} < {required_valid_rows})"
                    ),
                    field="total_games,times_played",
                )
    report.score = round(
        (min(len(cards) / max(minimum_cards, 1), 1.0) + float(has_tier_labels)) / 2,
        4,
    )
    return report


def _validate_heartharena_tierlist(
    source_id: str,
    structured: dict[str, Any],
) -> ValidationReport:
    report = ValidationReport()
    classes = [row for row in (structured.get("classes") or []) if isinstance(row, dict)]
    total_cards = int(structured.get("total_cards") or 0)
    cards = [
        card
        for class_row in classes
        for card in (class_row.get("cards") or [])
        if isinstance(card, dict)
    ]
    with_tier = sum(1 for card in cards if card.get("tier_id"))
    with_card_id = sum(1 for card in cards if card.get("card_id") or card.get("id"))
    actual_cards = len(cards)
    minimum_classes, minimum_cards, minimum_tier_ids = effective_heartharena_thresholds(
        source_id,
        total_cards=actual_cards,
    )
    report.metrics.update(
        {
            "classes": len(classes),
            "total_cards": total_cards,
            "actual_cards": actual_cards,
            "cards_with_tier_id": with_tier,
            "cards_with_card_id": with_card_id,
            "minimum_classes": minimum_classes,
            "minimum_cards": minimum_cards,
            "minimum_tier_ids": minimum_tier_ids,
        }
    )
    if len(classes) < minimum_classes:
        report.add_issue(
            "heartharena_tierlist.too_few_classes",
            f"heartharena classes too few ({len(classes)} < {minimum_classes})",
            field="classes",
        )
    if actual_cards < minimum_cards:
        report.add_issue(
            "heartharena_tierlist.too_few_cards",
            f"heartharena cards too few ({actual_cards} < {minimum_cards})",
            field="classes.cards",
        )
    if total_cards != actual_cards:
        report.add_issue(
            "heartharena_tierlist.card_count_mismatch",
            (
                "heartharena declared card count does not match flattened cards "
                f"({total_cards} != {actual_cards})"
            ),
            field="total_cards,classes.cards",
        )
    if with_tier < minimum_tier_ids:
        report.add_issue(
            "heartharena_tierlist.missing_tier_ids",
            f"heartharena cards missing tier_id ({with_tier} < {minimum_tier_ids})",
            field="tier_id",
        )
    policy = policy_for(source_id)
    if policy is not None and with_card_id < minimum_tier_ids:
        report.add_issue(
            "heartharena_tierlist.missing_card_ids",
            f"heartharena cards missing card ids ({with_card_id} < {minimum_tier_ids})",
            field="card_id,id",
        )
    score_parts = [
        min(len(classes) / max(minimum_classes, 1), 1.0),
        min(actual_cards / max(minimum_cards, 1), 1.0),
        min(with_tier / max(minimum_tier_ids, 1), 1.0),
    ]
    if policy is not None:
        score_parts.append(min(with_card_id / max(minimum_tier_ids, 1), 1.0))
    report.score = round(sum(score_parts) / len(score_parts), 4)
    return report


STANDARD_CARD_PERCENT_FIELDS = (
    "deck_winrate",
    "deck_popularity",
    "winrate_when_played",
    "winrate_when_drawn",
    "keep_percentage",
    "opening_hand_winrate",
)


def validate_standard_card_aliases(data: dict[str, Any]) -> ValidationReport:
    """Require the two public Standard-card aliases to be the same snapshot."""

    report = ValidationReport()
    structured = data.get("structured")
    extracted = data.get("hsreplay_extracted")
    report.metrics.update(
        {
            "structured_present": isinstance(structured, dict),
            "hsreplay_extracted_present": isinstance(extracted, dict),
            "aliases_equal": (
                isinstance(structured, dict)
                and isinstance(extracted, dict)
                and structured == extracted
            ),
        }
    )
    if not isinstance(structured, dict) or not isinstance(extracted, dict):
        report.add_issue(
            "card_stats.aliases_missing",
            "standard card aliases structured/hsreplay_extracted are both required",
            field="structured,hsreplay_extracted",
        )
    elif structured != extracted:
        report.add_issue(
            "card_stats.aliases_disagree",
            "standard card aliases structured/hsreplay_extracted disagree",
            field="structured,hsreplay_extracted",
        )
    return report


def _validate_card_stats(source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    cards = [row for row in (structured.get("cards") or []) if isinstance(row, dict)]
    with_metrics = sum(
        1
        for row in cards
        if any(
            row.get(field_name) not in (None, "")
            for field_name in STANDARD_CARD_PERCENT_FIELDS
        )
    )
    blocked = bool(structured.get("blocked"))
    early_current_patch = (
        source_id in HSREPLAY_CURRENT_PATCH_EARLY_SOURCE_IDS
        and policy_for(source_id) is not None
    )
    minimum_cards = 20 if early_current_patch else 30
    report.metrics.update(
        {
            "cards": len(cards),
            "cards_with_metrics": with_metrics,
            "blocked": blocked,
            "minimum_cards": minimum_cards,
        }
    )
    if blocked and len(cards) < 10:
        report.add_issue(
            "card_stats.blocked_or_empty",
            "card stats blocked or empty",
            field="blocked",
        )
    if len(cards) < minimum_cards:
        report.add_issue(
            "card_stats.too_few_cards",
            f"card stats too few ({len(cards)} < {minimum_cards})",
            field="cards",
        )
    if with_metrics < 20:
        report.add_issue(
            "card_stats.missing_metrics",
            f"card stats missing metrics ({with_metrics}/{len(cards)}; minimum 20)",
            field="deck_winrate,deck_popularity",
        )
    if early_current_patch:
        valid_card_keys: set[tuple[str, str]] = set()
        for row in cards:
            card_id = str(row.get("id") or "").strip()
            dbf_id = str(row.get("dbfId") or "").strip()
            identity = ("id", card_id) if card_id else (("dbfId", dbf_id) if dbf_id else None)
            valid_metric = any(
                (value := _parse_arena_percent(row.get(field_name))) is not None
                and 0.0 <= value <= 100.0
                for field_name in STANDARD_CARD_PERCENT_FIELDS
                if row.get(field_name) not in (None, "")
            )
            if identity is not None and valid_metric:
                valid_card_keys.add(identity)
        report.metrics["unique_valid_cards"] = len(valid_card_keys)
        if len(valid_card_keys) < 20:
            report.add_issue(
                "card_stats.too_few_unique_valid_patch_cards",
                (
                    "current-patch card stats have too few unique valid cards "
                    f"({len(valid_card_keys)} < 20)"
                ),
                field="id,dbfId,deck_winrate,deck_popularity",
            )
    if source_id in HSREPLAY_CARD_PERIOD_SOURCE_IDS:
        is_standard_period = source_id in STANDARD_HSREPLAY_CARD_PERIOD_SOURCE_IDS
        # HSReplay Standard statistics currently contain roughly one thousand
        # rows.  A much larger payload is not an early-meta expansion: it is a
        # format/filter failure (usually Wild/all-cards data under the Standard
        # source id).  Keep the ceiling configurable for future rotations, but
        # enforce it during every semantic validation so an orphan immutable
        # snapshot cannot become the bootstrap regression baseline merely
        # because its checksum and timestamp are valid.
        maximum_cards = (
            int(threshold_for(source_id, "cards_max", 1_800))
            if is_standard_period
            else None
        )
        if (
            structured.get("provisional") is True
            or structured.get("data_phase") == "post_patch_early"
        ) and not early_current_patch:
            report.add_issue(
                "card_stats.provisional_not_supported",
                (
                    "provisional/post-patch-early publication is not supported "
                    "for this HSReplay card-period source"
                ),
                field="provisional,data_phase",
            )
        ids = [str(row.get("id") or "").strip() for row in cards]
        dbf_ids = [str(row.get("dbfId") or "").strip() for row in cards]
        duplicate_ids = len([value for value in ids if value]) - len(
            {value for value in ids if value}
        )
        duplicate_dbf_ids = len([value for value in dbf_ids if value]) - len(
            {value for value in dbf_ids if value}
        )
        missing_identity = sum(
            1
            for card_id, dbf_id in zip(ids, dbf_ids, strict=True)
            if not card_id and not dbf_id
        )
        invalid_percentages: list[dict[str, Any]] = []
        popularity_cascade = 0
        for index, row in enumerate(cards):
            for field_name in STANDARD_CARD_PERCENT_FIELDS:
                raw_value = row.get(field_name)
                if raw_value is None or raw_value == "":
                    continue
                parsed_value = _parse_arena_percent(raw_value)
                if (
                    parsed_value is None or not 0.0 <= parsed_value <= 100.0
                ) and len(invalid_percentages) < 20:
                    invalid_percentages.append(
                        {
                            "index": index,
                            "field": field_name,
                            "value": str(raw_value)[:80],
                        }
                    )
            popularity = _parse_arena_percent(row.get("deck_popularity"))
            if popularity is not None and popularity >= 80.0:
                popularity_cascade += 1

        report.metrics.update(
            {
                "duplicate_card_ids": duplicate_ids,
                "duplicate_dbf_ids": duplicate_dbf_ids,
                "missing_card_identity": missing_identity,
                "invalid_percentage_values": len(invalid_percentages),
                "invalid_percentage_examples": invalid_percentages,
                "deck_popularity_at_least_80": popularity_cascade,
            }
        )
        if maximum_cards is not None:
            report.metrics["maximum_cards"] = maximum_cards
        if maximum_cards is not None and len(cards) > maximum_cards:
            report.add_issue(
                "card_stats.too_many_standard_cards",
                (
                    "standard card stats contain too many rows "
                    f"({len(cards)} > {maximum_cards}); probable format/filter leak"
                ),
                field="cards",
            )
        if duplicate_ids:
            report.add_issue(
                "card_stats.duplicate_card_id",
                f"HSReplay card stats contain duplicate card ids ({duplicate_ids})",
                field="id",
            )
        if duplicate_dbf_ids:
            report.add_issue(
                "card_stats.duplicate_dbf_id",
                f"HSReplay card stats contain duplicate dbfIds ({duplicate_dbf_ids})",
                field="dbfId",
            )
        if missing_identity:
            report.add_issue(
                "card_stats.missing_card_identity",
                f"HSReplay card stats contain rows without id/dbfId ({missing_identity})",
                field="id,dbfId",
            )
        if invalid_percentages:
            report.add_issue(
                "card_stats.percent_out_of_range",
                (
                    "HSReplay card stats contain invalid percentage values outside "
                    f"0..100 ({len(invalid_percentages)})"
                ),
                field=",".join(STANDARD_CARD_PERCENT_FIELDS),
            )
        if popularity_cascade >= 10:
            report.add_issue(
                "card_stats.systemic_popularity_cascade",
                (
                    "systemic HSReplay-card popularity cascade detected: "
                    f"{popularity_cascade} cards have deck_popularity >= 80%"
                ),
                field="deck_popularity",
            )
    report.score = round(
        (
            float(not blocked or len(cards) >= 10)
            + min(len(cards) / float(minimum_cards), 1.0)
            + min(with_metrics / 20.0, 1.0)
        )
        / 3,
        4,
    )
    return report


def _validate_hsreplay_meta_archetypes(
    _source_id: str,
    structured: dict[str, Any],
) -> ValidationReport:
    report = ValidationReport()
    classes = [row for row in (structured.get("classes") or []) if isinstance(row, dict)]
    archetypes = [
        archetype
        for class_row in classes
        for archetype in (class_row.get("archetypes") or [])
        if isinstance(archetype, dict)
    ]
    with_metrics = sum(
        1
        for archetype in archetypes
        if archetype.get("winrate")
        and archetype.get("popularity")
        and archetype.get("games")
    )
    report.metrics.update(
        {
            "classes": len(classes),
            "archetypes": len(archetypes),
            "archetypes_with_metrics": with_metrics,
        }
    )
    if len(classes) < 8:
        report.add_issue(
            "hsreplay_meta_archetypes.too_few_classes",
            f"meta archetypes too few classes ({len(classes)} < 8)",
            field="classes",
        )
    if len(archetypes) < 20:
        report.add_issue(
            "hsreplay_meta_archetypes.too_few_rows",
            f"meta archetypes too few rows ({len(archetypes)} < 20)",
            field="archetypes",
        )
    if with_metrics < 20:
        report.add_issue(
            "hsreplay_meta_archetypes.missing_metrics",
            f"meta archetypes missing metrics ({with_metrics}/{len(archetypes)}; minimum 20)",
            field="winrate,popularity,games",
        )
    report.score = round(
        (
            min(len(classes) / 8.0, 1.0)
            + min(len(archetypes) / 20.0, 1.0)
            + min(with_metrics / 20.0, 1.0)
        )
        / 3,
        4,
    )
    return report


def _validate_hsguru_meta(source_id: str, structured: dict[str, Any]) -> ValidationReport:
    report = ValidationReport()
    strategies = [
        row for row in (structured.get("strategies") or []) if isinstance(row, dict)
    ]
    policy = policy_for(source_id)
    minimum_rows = (
        policy.minimum_rows
        if policy is not None
        else int(threshold_for(source_id, "meta_table_rows_min", 5))
    )
    report.metrics.update({"strategies": len(strategies), "minimum_rows": minimum_rows})
    if len(strategies) < minimum_rows:
        report.add_issue(
            "hsguru_meta.too_few_rows",
            f"HSGuru meta too few rows ({len(strategies)} < {minimum_rows})",
            field="strategies",
        )
    if policy is not None:
        valid_archetypes: set[str] = set()
        for row in strategies:
            archetype = str(row.get("Archetype") or "").strip()
            winrate = _parse_arena_percent(row.get("Winrate↓"))
            popularity = _parse_arena_percent(row.get("Popularity"))
            if (
                _valid_name(archetype)
                and winrate is not None
                and 0.0 <= winrate <= 100.0
                and popularity is not None
                and 0.0 <= popularity <= 100.0
            ):
                valid_archetypes.add(archetype.casefold())
        complete_rows = len(valid_archetypes)
        report.metrics["complete_early_rows"] = complete_rows
        if complete_rows < 3:
            report.add_issue(
                "hsguru_meta.too_few_complete_early_rows",
                (
                    "HSGuru early meta has too few unique rows with valid "
                    f"metrics ({complete_rows} < 3)"
                ),
                field="Archetype,Winrate↓,Popularity",
            )
    report.score = round(min(len(strategies) / max(minimum_rows, 1), 1.0), 4)
    return report


def _validate_hsguru_streamer_decks(
    _source_id: str,
    structured: dict[str, Any],
) -> ValidationReport:
    report = ValidationReport()
    rows = [row for row in (structured.get("rows") or []) if isinstance(row, dict)]
    decodable_deck_codes = sum(
        1 for row in rows if is_decodable_deck_code(row.get("deck_code"))
    )
    complete_rows = sum(
        1
        for row in rows
        if _valid_name(row.get("Deck"))
        and _valid_name(row.get("Streamer"))
        and is_decodable_deck_code(row.get("deck_code"))
    )
    report.metrics.update(
        {
            "rows": len(rows),
            "complete_rows": complete_rows,
            "decodable_deck_codes": decodable_deck_codes,
            "low_activity": 0 < len(rows) < 3 and complete_rows == len(rows),
        }
    )
    if not rows:
        report.add_issue(
            "hsguru_streamer_decks.empty_window",
            "HSGuru streamer decks rolling-hour window is empty",
            field="rows",
        )
    elif complete_rows != len(rows):
        report.add_issue(
            "hsguru_streamer_decks.incomplete_rows",
            (
                "HSGuru streamer decks requires Deck, Streamer and a decodable "
                f"deck code on every row ({complete_rows}/{len(rows)} complete)"
            ),
            field="Deck,Streamer,deck_code",
        )
    report.score = round(complete_rows / len(rows), 4) if rows else 0.0
    return report


def _validate_hsguru_fun_decks(
    _source_id: str,
    structured: dict[str, Any],
) -> ValidationReport:
    report = ValidationReport()
    rows = [row for row in (structured.get("rows") or []) if isinstance(row, dict)]
    with_scores = sum(
        1
        for row in rows
        if row.get("deck_code") and row.get("fun_score") is not None
    )
    report.metrics.update({"rows": len(rows), "rows_with_scores": with_scores})
    # Empty is allowed early on; once populated, require scored codes.
    if rows and with_scores < max(1, len(rows) // 2):
        report.add_issue(
            "hsguru_fun_decks.missing_scores",
            f"Fun decks missing scores/codes ({with_scores}/{len(rows)})",
            field="fun_score,deck_code",
        )
    report.score = 1.0 if not rows else round(min(with_scores / max(len(rows), 1), 1.0), 4)
    return report


def _validate_hsguru_matchups(
    source_id: str,
    structured: dict[str, Any],
) -> ValidationReport:
    report = ValidationReport()
    matchups = [
        row for row in (structured.get("matchups") or []) if isinstance(row, dict)
    ]
    with_winrate = sum(1 for row in matchups if row.get("winrate"))
    report.metrics.update({"matchups": len(matchups), "matchups_with_winrate": with_winrate})
    if len(matchups) < 3:
        report.add_issue(
            "hsguru_matchups.too_few_rows",
            f"HSGuru matchups too few rows ({len(matchups)} < 3)",
            field="matchups",
        )
    if with_winrate < 1:
        report.add_issue(
            "hsguru_matchups.missing_winrates",
            "HSGuru matchups content not detected",
            field="winrate",
        )
    if policy_for(source_id) is not None:
        valid_matchups: set[tuple[str, str]] = set()
        for row in matchups:
            archetype = str(row.get("archetype") or "").strip()
            opponent = str(row.get("vs") or "").strip()
            winrate = _parse_arena_percent(row.get("winrate"))
            if (
                _valid_name(archetype)
                and _valid_name(opponent)
                and winrate is not None
                and 0.0 <= winrate <= 100.0
            ):
                valid_matchups.add((archetype.casefold(), opponent.casefold()))
        complete_rows = len(valid_matchups)
        report.metrics["complete_early_rows"] = complete_rows
        if complete_rows < 3:
            report.add_issue(
                "hsguru_matchups.too_few_complete_early_rows",
                (
                    "HSGuru early matchups have too few unique rows with "
                    "valid metrics "
                    f"({complete_rows} < 3)"
                ),
                field="archetype,vs,winrate",
            )
    report.score = round(
        (min(len(matchups) / 3.0, 1.0) + min(with_winrate, 1)) / 2,
        4,
    )
    return report


def _validate_hearthstone_decks(
    _source_id: str,
    structured: dict[str, Any],
) -> ValidationReport:
    from .deck_decode import decode_deck_code

    report = ValidationReport()
    decks = [row for row in (structured.get("decks") or []) if isinstance(row, dict)]
    format_counts = {
        format_name: sum(1 for row in decks if row.get("format") == format_name)
        for format_name in ("Standard", "Wild")
    }
    canonical_urls: list[str] = []
    valid_urls = 0
    decodable_codes = 0
    wordpress_ids: list[int] = []
    valid_wordpress_rows = 0
    for row in decks:
        raw_url = str(row.get("url") or "").strip()
        parsed_url = urlparse(raw_url)
        if (
            parsed_url.scheme == "https"
            and (parsed_url.hostname or "").rstrip(".").lower()
            in {"hearthstone-decks.net", "www.hearthstone-decks.net"}
            and parsed_url.path.rstrip("/")
            and not parsed_url.fragment
        ):
            valid_urls += 1
        canonical_urls.append(raw_url.rstrip("/").casefold())

        deck_code = str(row.get("deck_code") or "").strip()
        if deck_code and decode_deck_code(deck_code).get("ok"):
            decodable_codes += 1

        post_id = row.get("wordpress_post_id")
        categories = row.get("wordpress_categories")
        if isinstance(post_id, int) and not isinstance(post_id, bool) and post_id > 0:
            wordpress_ids.append(post_id)
            expected_category = 3 if row.get("format") == "Standard" else 13
            if isinstance(categories, list) and expected_category in categories:
                valid_wordpress_rows += 1

    unique_urls = len({url for url in canonical_urls if url})
    unique_wordpress_ids = len(set(wordpress_ids))
    minimum_decodable = max(1, math.ceil(len(decks) * 0.95))
    report.metrics.update(
        {
            "decks": len(decks),
            "standard_decks": format_counts["Standard"],
            "wild_decks": format_counts["Wild"],
            "valid_urls": valid_urls,
            "unique_urls": unique_urls,
            "decodable_deck_codes": decodable_codes,
            "minimum_decodable_deck_codes": minimum_decodable,
            "wordpress_rows": len(wordpress_ids),
            "valid_wordpress_rows": valid_wordpress_rows,
            "unique_wordpress_ids": unique_wordpress_ids,
        }
    )
    if len(decks) != 40:
        report.add_issue(
            "hearthstone_decks.wrong_total",
            f"Hearthstone-Decks row count must be 40 ({len(decks)}/40)",
            field="decks",
        )
    for format_name, count in format_counts.items():
        if count != 20:
            report.add_issue(
                "hearthstone_decks.wrong_format_count",
                f"Hearthstone-Decks {format_name} row count must be 20 ({count}/20)",
                field="format",
            )
    if valid_urls != len(decks) or unique_urls != len(decks):
        report.add_issue(
            "hearthstone_decks.invalid_or_duplicate_urls",
            (
                "Hearthstone-Decks URLs are invalid or duplicated "
                f"({valid_urls} valid, {unique_urls} unique, {len(decks)} total)"
            ),
            field="url",
        )
    if decodable_codes < minimum_decodable:
        report.add_issue(
            "hearthstone_decks.invalid_deck_codes",
            (
                "Hearthstone-Decks decodable deck codes too few "
                f"({decodable_codes} < {minimum_decodable})"
            ),
            field="deck_code",
        )

    strategy = structured.get("fetch_strategy")
    if strategy == "wordpress_rest":
        if (
            len(wordpress_ids) != len(decks)
            or valid_wordpress_rows != len(decks)
            or unique_wordpress_ids != len(decks)
        ):
            report.add_issue(
                "hearthstone_decks.invalid_wordpress_identity",
                "WordPress REST rows have missing, duplicate, or mismatched identities",
                field="wordpress_post_id,wordpress_categories",
            )
        if (
            structured.get("wordpress_rest_requests") != 2
            or structured.get("wordpress_rest_accepted_formats") != 2
            or structured.get("html_list_pages") != 0
        ):
            report.add_issue(
                "hearthstone_decks.invalid_rest_telemetry",
                "WordPress REST telemetry does not match the two-feed API-only path",
                field="wordpress_rest_requests,wordpress_rest_accepted_formats,html_list_pages",
            )
    elif strategy != "validated_html_fallback":
        report.add_issue(
            "hearthstone_decks.unknown_fetch_strategy",
            f"Hearthstone-Decks fetch strategy is not recognized: {strategy!r}",
            field="fetch_strategy",
        )

    report.score = round(
        (
            min(len(decks) / 40.0, 1.0)
            + min(format_counts["Standard"] / 20.0, 1.0)
            + min(format_counts["Wild"] / 20.0, 1.0)
            + min(valid_urls / max(len(decks), 1), 1.0)
            + min(unique_urls / max(len(decks), 1), 1.0)
            + min(decodable_codes / max(len(decks), 1), 1.0)
        )
        / 6.0,
        4,
    )
    return report


def _validate_firestone_standard(
    _source_id: str,
    structured: dict[str, Any],
) -> ValidationReport:
    from .deck_decode import decode_deck_code

    report = ValidationReport()
    decks = [row for row in (structured.get("decks") or []) if isinstance(row, dict)]
    archetypes = [
        row for row in (structured.get("archetypes") or []) if isinstance(row, dict)
    ]

    def complete_metric_row(row: dict[str, Any]) -> bool:
        games = parse_decimal(row.get("games"))
        wins = parse_decimal(row.get("wins"))
        winrate = parse_decimal(row.get("winrate"))
        return bool(
            row.get("archetype_id") is not None
            and _valid_name(row.get("archetype_name"))
            and _valid_name(row.get("player_class"))
            and games is not None
            and games > 0
            and wins is not None
            and 0 <= wins <= games
            and winrate is not None
            and 0.0 <= winrate <= 1.0
        )

    complete_decks = sum(
        1 for row in decks if row.get("decklist") and complete_metric_row(row)
    )
    complete_archetypes = sum(1 for row in archetypes if complete_metric_row(row))
    unique_decklists = len(
        {str(row.get("decklist")) for row in decks if row.get("decklist")}
    )
    unique_archetype_ids = len(
        {row.get("archetype_id") for row in archetypes if row.get("archetype_id") is not None}
    )
    decodable_decks = 0
    for row in decks:
        deck_code = str(row.get("deck_code") or "").strip()
        if deck_code and decode_deck_code(deck_code).get("ok"):
            decodable_decks += 1

    invalid_deck_scope = sum(
        1
        for row in decks
        if row.get("format") != "standard"
        or row.get("rank_bracket") != "legend"
        or row.get("time_period") != "last-patch"
    )
    invalid_archetype_scope = sum(
        1 for row in archetypes if row.get("format") != "standard"
    )

    metadata = structured.get("metadata")
    valid_metadata = 0
    metadata_age_hours: dict[str, float | None] = {}
    now = _validation_now_utc()
    if isinstance(metadata, dict):
        for collection in ("decks", "archetypes"):
            item = metadata.get(collection)
            if not isinstance(item, dict):
                metadata_age_hours[collection] = None
                continue
            data_points = parse_decimal(item.get("data_points"))
            last_updated = _parse_aware_iso_timestamp(item.get("last_updated"))
            age_hours = (
                (now - last_updated).total_seconds() / 3600.0
                if last_updated is not None
                else None
            )
            metadata_age_hours[collection] = (
                round(age_hours, 3) if age_hours is not None else None
            )
            timestamp_fresh = bool(
                age_hours is not None
                and -FIRESTONE_STANDARD_MAX_FUTURE_SKEW_HOURS
                <= age_hours
                <= FIRESTONE_STANDARD_MAX_UPSTREAM_AGE_HOURS
            )
            if (
                data_points is not None
                and data_points > 0
                and timestamp_fresh
                and item.get("format") == "standard"
                and item.get("rank_bracket") == "legend"
                and item.get("time_period") == "last-patch"
            ):
                valid_metadata += 1
            if last_updated is None:
                report.add_issue(
                    "firestone_standard.invalid_upstream_timestamp",
                    f"Firestone Standard {collection} last_updated is not an aware ISO timestamp",
                    field=f"metadata.{collection}.last_updated",
                )
            elif (
                age_hours is not None
                and age_hours > FIRESTONE_STANDARD_MAX_UPSTREAM_AGE_HOURS
            ):
                report.add_issue(
                    "firestone_standard.stale_upstream_snapshot",
                    (
                        f"Firestone Standard {collection} snapshot is stale "
                        f"({age_hours:.1f}h > "
                        f"{FIRESTONE_STANDARD_MAX_UPSTREAM_AGE_HOURS:g}h)"
                    ),
                    field=f"metadata.{collection}.last_updated",
                )
            elif (
                age_hours is not None
                and age_hours < -FIRESTONE_STANDARD_MAX_FUTURE_SKEW_HOURS
            ):
                report.add_issue(
                    "firestone_standard.future_upstream_timestamp",
                    (
                        f"Firestone Standard {collection} snapshot is too far in the future "
                        f"({-age_hours:.1f}h > "
                        f"{FIRESTONE_STANDARD_MAX_FUTURE_SKEW_HOURS:g}h)"
                    ),
                    field=f"metadata.{collection}.last_updated",
                )

    report.metrics.update(
        {
            "decks": len(decks),
            "archetypes": len(archetypes),
            "complete_decks": complete_decks,
            "complete_archetypes": complete_archetypes,
            "unique_decklists": unique_decklists,
            "unique_archetype_ids": unique_archetype_ids,
            "decodable_decks": decodable_decks,
            "valid_metadata_collections": valid_metadata,
            "metadata_age_hours": metadata_age_hours,
            "invalid_deck_scope_rows": invalid_deck_scope,
            "invalid_archetype_scope_rows": invalid_archetype_scope,
        }
    )
    if len(decks) < 10:
        report.add_issue(
            "firestone_standard.too_few_decks",
            f"Firestone Standard decks too few ({len(decks)} < 10)",
            field="decks",
        )
    if len(archetypes) < 10:
        report.add_issue(
            "firestone_standard.too_few_archetypes",
            f"Firestone Standard archetypes too few ({len(archetypes)} < 10)",
            field="archetypes",
        )
    required_complete_decks = max(1, math.ceil(len(decks) * 0.80))
    required_complete_archetypes = max(1, math.ceil(len(archetypes) * 0.80))
    if complete_decks < required_complete_decks:
        report.add_issue(
            "firestone_standard.incomplete_decks",
            (
                "Firestone Standard complete deck rows too few "
                f"({complete_decks} < {required_complete_decks})"
            ),
            field="decklist,archetype_id,archetype_name,player_class,games,wins,winrate",
        )
    if complete_archetypes < required_complete_archetypes:
        report.add_issue(
            "firestone_standard.incomplete_archetypes",
            (
                "Firestone Standard complete archetype rows too few "
                f"({complete_archetypes} < {required_complete_archetypes})"
            ),
            field="archetype_id,archetype_name,player_class,games,wins,winrate",
        )
    if unique_decklists < required_complete_decks:
        report.add_issue(
            "firestone_standard.duplicate_decks",
            f"Firestone Standard unique decklists too few ({unique_decklists}/{len(decks)})",
            field="decklist",
        )
    required_decodable_decks = max(1, math.ceil(len(decks) * 0.90))
    if decodable_decks < required_decodable_decks:
        report.add_issue(
            "firestone_standard.invalid_deck_codes",
            (
                "Firestone Standard decodable deck codes too few "
                f"({decodable_decks} < {required_decodable_decks})"
            ),
            field="deck_code",
        )
    if unique_archetype_ids < required_complete_archetypes:
        report.add_issue(
            "firestone_standard.duplicate_archetypes",
            (
                "Firestone Standard unique archetype ids too few "
                f"({unique_archetype_ids}/{len(archetypes)})"
            ),
            field="archetype_id",
        )
    if valid_metadata < 2:
        report.add_issue(
            "firestone_standard.invalid_metadata",
            f"Firestone Standard valid metadata collections too few ({valid_metadata} < 2)",
            field="metadata",
        )
    if invalid_deck_scope:
        report.add_issue(
            "firestone_standard.invalid_deck_scope",
            f"Firestone Standard decks outside requested scope ({invalid_deck_scope})",
            field="decks.format,decks.rank_bracket,decks.time_period",
        )
    if invalid_archetype_scope:
        report.add_issue(
            "firestone_standard.invalid_archetype_scope",
            (
                "Firestone Standard archetypes outside requested format "
                f"({invalid_archetype_scope})"
            ),
            field="archetypes.format",
        )

    report.score = round(
        (
            min(len(decks) / 10.0, 1.0)
            + min(len(archetypes) / 10.0, 1.0)
            + min(complete_decks / max(len(decks), 1), 1.0)
            + min(complete_archetypes / max(len(archetypes), 1), 1.0)
            + min(decodable_decks / max(len(decks), 1), 1.0)
            + valid_metadata / 2.0
        )
        / 6.0,
        4,
    )
    return report


_VALIDATORS: dict[str, Callable[[str, dict[str, Any]], ValidationReport]] = {
    "bg_heroes": _validate_bg_heroes,
    "vicious_live": _validate_vicious_live,
    "vicious_syndicate_radars": _validate_vicious_radars,
    "arena_class_matrix": _validate_arena_class_matrix,
    "arena_class_pages": _validate_arena_class_pages,
    "arena_winning_decks": _validate_arena_winning_decks,
    "arena_legendary_groups": _validate_arena_legendary_groups,
    "bg_comps": _validate_bg_comps,
    "bg_card_stats": _validate_bg_card_stats,
    "bg_trinkets": _validate_bg_trinkets,
    "bg_minions": _validate_bg_minions,
    "bg_compositions": _validate_bg_compositions,
    "arena_card_tiers": _validate_arena_card_tiers,
    "heartharena_tierlist": _validate_heartharena_tierlist,
    "card_stats": _validate_card_stats,
    "hsreplay_meta_archetypes": _validate_hsreplay_meta_archetypes,
    "meta": _validate_hsguru_meta,
    "streamer_decks": _validate_hsguru_streamer_decks,
    "fun_decks": _validate_hsguru_fun_decks,
    "matchups": _validate_hsguru_matchups,
    "hearthstone_decks": _validate_hearthstone_decks,
    "firestone_standard": _validate_firestone_standard,
}


def validate_structured(source_id: str, structured: dict[str, Any]) -> ValidationReport:
    validator = _VALIDATORS.get(str(structured.get("type") or ""))
    if validator is None:
        return ValidationReport(metrics={"source_id": source_id, "structured_type": structured.get("type")})
    report = validator(source_id, structured)
    report.metrics["source_id"] = source_id
    report.metrics["structured_type"] = structured.get("type")
    return report
