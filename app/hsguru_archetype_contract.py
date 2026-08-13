from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SOURCE_ID = "hsguru_archetype_analysis"
SCHEMA_VERSION = 2
EXPECTED_RANK = "legend"
EXPECTED_PERIOD = "past_week"
ALLOWED_FORMATS = frozenset({"standard", "wild"})
EXPECTED_TARGET_SOURCE = "hsguru_meta_matrix:legend:past_week"


@dataclass(frozen=True)
class HSGuruArchetypeContractIssue:
    code: str
    message: str
    field: str | None = None


@dataclass
class HSGuruArchetypeContractResult:
    ok: bool = True
    score: float = 1.0
    issues: list[HSGuruArchetypeContractIssue] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def add_issue(self, code: str, message: str, *, field: str | None = None) -> None:
        self.ok = False
        self.issues.append(
            HSGuruArchetypeContractIssue(
                code=code,
                message=message,
                field=field,
            )
        )


TargetKey = tuple[str, str]


def _target_key(value: Any) -> TargetKey | None:
    if not isinstance(value, dict):
        return None
    format_name = value.get("format")
    archetype = value.get("archetype")
    if not isinstance(format_name, str) or format_name not in ALLOWED_FORMATS:
        return None
    if not isinstance(archetype, str) or not archetype.strip():
        return None
    return format_name, archetype.strip().casefold()


def _aware_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _negative_cache_keys(structured: dict[str, Any]) -> set[TargetKey]:
    keys: set[TargetKey] = set()
    raw_entries = structured.get("negative_cache")
    if not isinstance(raw_entries, list):
        return keys
    for entry in raw_entries:
        key = _target_key(entry)
        if (
            key is not None
            and entry.get("kind") == "card_stats"
            and entry.get("state")
            in {"source_no_data", "upstream_card_tallies_missing"}
            and _aware_iso(entry.get("checked_at"))
        ):
            keys.add(key)
    return keys


def _expected_targets(
    structured: dict[str, Any],
    result: HSGuruArchetypeContractResult,
) -> set[TargetKey] | None:
    raw_targets = structured.get("expected_targets")
    raw_total = structured.get("expected_targets_total")
    if raw_targets is None and raw_total is None:
        result.metrics["expected_target_metadata_present"] = False
        return None
    result.metrics["expected_target_metadata_present"] = True
    if not isinstance(raw_targets, list) or not raw_targets:
        result.add_issue(
            "hsguru_analysis.expected_targets_missing",
            "expected target count was supplied without a non-empty target list",
            field="expected_targets",
        )
        return set()
    keys = [_target_key(target) for target in raw_targets]
    valid_keys = {key for key in keys if key is not None}
    if len(valid_keys) != len(raw_targets):
        result.add_issue(
            "hsguru_analysis.expected_targets_invalid",
            "expected targets must be valid and unique format/archetype pairs",
            field="expected_targets",
        )
    if (
        not _non_negative_int(raw_total)
        or raw_total <= 0
        or raw_total != len(raw_targets)
    ):
        result.add_issue(
            "hsguru_analysis.expected_target_count",
            "expected_targets_total must equal the non-empty target list",
            field="expected_targets_total",
        )
    result.metrics["expected_targets"] = len(valid_keys)
    return valid_keys


def _component_is_consistent(
    row: dict[str, Any],
    *,
    component_name: str,
) -> bool:
    components = row.get("components")
    component = components.get(component_name) if isinstance(components, dict) else None
    if not isinstance(component, dict):
        return False
    prefix = "matchups" if component_name == "matchups" else "card_stats"
    state = row.get(f"{prefix}_state")
    checked_at = row.get(f"{prefix}_checked_at")
    updated_at = row.get(f"{prefix}_updated_at")
    updated_at_is_valid = _aware_iso(updated_at) or (
        component_name == "card_stats"
        and state in {"source_no_data", "upstream_card_tallies_missing"}
        and updated_at is None
    )
    return bool(
        component.get("state") == state
        and component.get("checked_at") == checked_at
        and component.get("updated_at") == updated_at
        and _aware_iso(checked_at)
        and updated_at_is_valid
    )


def _validate_row(
    row: dict[str, Any],
    *,
    negative_cache_keys: set[TargetKey],
    result: HSGuruArchetypeContractResult,
) -> None:
    key = _target_key(row)
    label = (
        f"{row.get('format')}/{row.get('archetype')}"
        if key is not None
        else "invalid target"
    )
    if key is None:
        result.add_issue(
            "hsguru_analysis.invalid_target",
            f"{label} has an invalid format or archetype",
            field="archetypes.format,archetypes.archetype",
        )
        return
    if row.get("rank") != EXPECTED_RANK or row.get("period") != EXPECTED_PERIOD:
        result.add_issue(
            "hsguru_analysis.target_scope",
            f"{label} is outside the required rank/period",
            field="archetypes.rank,archetypes.period",
        )

    matchups = row.get("class_matchups")
    card_stats = row.get("card_stats")
    matchups_state = row.get("matchups_state")
    card_stats_state = row.get("card_stats_state")
    row_state = row.get("state")

    if not _component_is_consistent(row, component_name="matchups") or not (
        _component_is_consistent(row, component_name="card_stats")
    ):
        result.add_issue(
            "hsguru_analysis.component_mismatch",
            f"{label} component summary disagrees with its scalar state/timestamps",
            field="archetypes.components",
        )
    if not _aware_iso(row.get("checked_at")) or not _aware_iso(row.get("updated_at")):
        result.add_issue(
            "hsguru_analysis.component_mismatch",
            f"{label} lacks aggregate fresh checked/updated timestamps",
            field="archetypes.checked_at,archetypes.updated_at",
        )

    if matchups_state != "complete" or not isinstance(matchups, list) or not matchups:
        result.add_issue(
            "hsguru_analysis.not_fresh",
            f"{label} has no freshly completed matchup component",
            field="archetypes.matchups_state,archetypes.class_matchups",
        )

    expected_row_state = "ok"
    if card_stats_state == "complete":
        if not isinstance(card_stats, list) or not card_stats:
            result.add_issue(
                "hsguru_analysis.empty_complete_component",
                f"{label} marks empty card stats as complete",
                field="archetypes.card_stats",
            )
    elif card_stats_state == "sparse_valid":
        # The collector emits this bounded state only after it parsed non-empty
        # upstream rows and every row fell below the explicit 25/25 sample
        # threshold. It is therefore a fresh verified low-sample result, not LKG.
        if not isinstance(card_stats, list) or card_stats:
            result.add_issue(
                "hsguru_analysis.component_mismatch",
                f"{label} sparse_valid component must contain no publishable card rows",
                field="archetypes.card_stats,archetypes.card_stats_state",
            )
    elif card_stats_state in {"source_no_data", "upstream_card_tallies_missing"}:
        expected_row_state = "partial"
        if isinstance(card_stats, list) and card_stats:
            result.add_issue(
                "hsguru_analysis.component_mismatch",
                f"{label} declares a verified empty card-stat slice but contains rows",
                field="archetypes.card_stats",
            )
        if key not in negative_cache_keys:
            result.add_issue(
                "hsguru_analysis.zero_stats_without_evidence",
                f"{label} has zero card stats without a matching checked negative-cache record",
                field="negative_cache",
            )
    else:
        result.add_issue(
            "hsguru_analysis.not_fresh",
            f"{label} has a non-publishable card-stat state ({card_stats_state})",
            field="archetypes.card_stats_state",
        )

    if row_state != expected_row_state:
        result.add_issue(
            "hsguru_analysis.component_mismatch",
            f"{label} aggregate state does not match component states",
            field="archetypes.state",
        )


def _validate_coverage(
    structured: dict[str, Any],
    rows: list[dict[str, Any]],
    result: HSGuruArchetypeContractResult,
) -> None:
    coverage = structured.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != ALLOWED_FORMATS:
        result.add_issue(
            "hsguru_analysis.coverage_mismatch",
            "coverage must contain exactly the supported formats",
            field="coverage",
        )
        return
    for format_name in sorted(ALLOWED_FORMATS):
        reported = coverage.get(format_name)
        selected = [row for row in rows if row.get("format") == format_name]
        actual = {
            "archetypes": len(selected),
            "with_matchups": sum(bool(row.get("class_matchups")) for row in selected),
            "with_card_stats": sum(bool(row.get("card_stats")) for row in selected),
            "complete": sum(row.get("state") == "ok" for row in selected),
        }
        if not isinstance(reported, dict) or any(
            not _non_negative_int(reported.get(field_name))
            or reported.get(field_name) != expected
            for field_name, expected in actual.items()
        ):
            result.add_issue(
                "hsguru_analysis.coverage_mismatch",
                f"{format_name} coverage does not match published component rows",
                field=f"coverage.{format_name}",
            )


def validate_hsguru_archetype_analysis(
    structured: dict[str, Any],
) -> HSGuruArchetypeContractResult:
    result = HSGuruArchetypeContractResult()
    if structured.get("type") != SOURCE_ID:
        result.add_issue(
            "hsguru_analysis.type",
            f"expected structured type {SOURCE_ID}",
            field="type",
        )
    if (
        not isinstance(structured.get("schema_version"), int)
        or isinstance(structured.get("schema_version"), bool)
        or structured.get("schema_version") != SCHEMA_VERSION
    ):
        result.add_issue(
            "hsguru_analysis.schema_version",
            f"expected schema_version {SCHEMA_VERSION}",
            field="schema_version",
        )

    criteria = structured.get("criteria")
    if not isinstance(criteria, dict) or (
        criteria.get("rank") != EXPECTED_RANK
        or criteria.get("period") != EXPECTED_PERIOD
        or criteria.get("requires_decks") is not False
        or criteria.get("target_source") != EXPECTED_TARGET_SOURCE
    ):
        result.add_issue(
            "hsguru_analysis.criteria",
            "criteria must identify the Legend/past-week HSGuru archetype catalog",
            field="criteria",
        )
    raw_formats = criteria.get("formats") if isinstance(criteria, dict) else None
    if (
        not isinstance(raw_formats, list)
        or len(raw_formats) != len(ALLOWED_FORMATS)
        or set(raw_formats) != ALLOWED_FORMATS
    ):
        result.add_issue(
            "hsguru_analysis.criteria_formats",
            "criteria formats must contain standard and wild exactly once",
            field="criteria.formats",
        )

    raw_rows = structured.get("archetypes")
    if not isinstance(raw_rows, list) or not raw_rows:
        result.add_issue(
            "hsguru_analysis.empty_targets",
            "archetype analysis must contain at least one target",
            field="archetypes",
        )
        rows: list[dict[str, Any]] = []
    else:
        rows = [row for row in raw_rows if isinstance(row, dict)]
        if len(rows) != len(raw_rows):
            result.add_issue(
                "hsguru_analysis.invalid_target",
                "every archetype target must be an object",
                field="archetypes",
            )

    row_keys = [_target_key(row) for row in rows]
    valid_row_keys = {key for key in row_keys if key is not None}
    if len(valid_row_keys) != len([key for key in row_keys if key is not None]):
        result.add_issue(
            "hsguru_analysis.duplicate_targets",
            "archetype targets must be unique within each format",
            field="archetypes",
        )

    expected = _expected_targets(structured, result)
    if expected is not None and expected != valid_row_keys:
        result.add_issue(
            "hsguru_analysis.missing_expected_targets",
            "published archetypes do not exactly match the declared target set",
            field="expected_targets,archetypes",
        )

    negative_cache_keys = _negative_cache_keys(structured)
    for row in rows:
        _validate_row(
            row,
            negative_cache_keys=negative_cache_keys,
            result=result,
        )
    _validate_coverage(structured, rows, result)

    result.metrics.update(
        {
            "rows": len(rows),
            "unique_targets": len(valid_row_keys),
            "verified_sparse_targets": sum(
                row.get("card_stats_state") == "sparse_valid" for row in rows
            ),
            "verified_zero_data_targets": sum(
                row.get("card_stats_state") == "source_no_data"
                and _target_key(row) in negative_cache_keys
                for row in rows
            ),
        }
    )
    result.score = 1.0 if result.ok else 0.0
    return result
