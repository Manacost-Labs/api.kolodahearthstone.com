from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .dataset_regression import estimate_filled_metric_count, estimate_metric_count
from .post_patch_policy import policy_for
from .source_contracts import SourceContract, contract_quality_report, get_contract
from .source_validators import ValidationReport, validate_structured
from .sources import SOURCE_BY_ID, Source

_SAFE_STRUCTURED_TYPES = frozenset(
    {
        "arena_card_tiers",
        "arena_class_matrix",
        "arena_class_pages",
        "arena_legendary_groups",
        "arena_winning_decks",
        "bg_card_stats",
        "bg_compositions",
        "bg_comps",
        "bg_heroes",
        "bg_minions",
        "bg_trinkets",
        "card_stats",
        "firestone_standard",
        "fun_decks",
        "heartharena_tierlist",
        "hearthstone_decks",
        "hsreplay_meta_archetypes",
        "matchups",
        "meta",
        "metastats_decks",
        "metastats_matchups",
        "streamer_decks",
        "trending_decks",
        "vicious_live",
        "vicious_syndicate_radars",
    }
)
_SAFE_STAGES = frozenset(
    {
        "candidate_validation",
        "completed",
        "contract_validation",
        "deterministic_rejection",
        "failure_diagnosis",
        "fetch",
        "parse",
        "publication",
        "regression",
        "regression_check",
        "regression_rejection",
        "schema_validation",
        "semantic_validation",
    }
)
_SAFE_REGRESSION_REASON_CODES = frozenset(
    {
        "collection_drop",
        "filled_metric_drop",
        "none",
        "policy_changed",
        "post_patch_bypass",
        "row_count_drop",
        "source_identity_changed",
        "unknown",
    }
)
_SAFE_DETERMINISTIC_REASON_CODES = frozenset(
    {
        "ai_quarantine",
        "challenge_page",
        "contract_failure",
        "dependency",
        "empty_content",
        "http_4xx",
        "http_5xx",
        "identity_mismatch",
        "login_wall",
        "none",
        "parse_error",
        "policy_changed",
        "preflight",
        "provider_exhausted",
        "regression",
        "schema_mismatch",
        "semantic_failure",
        "suspicious_truncation",
        "timeout",
        "transport_error",
        "unknown",
    }
)
_SAFE_PIPELINE_METRIC_KEYS = frozenset(
    {
        "affected_sources",
        "affected_tiers",
        "cards",
        "cards_with_metrics",
        "deck_codes",
        "json_scripts",
        "quality_score",
        "radars",
        "radars_with_graph",
        "rows_total",
        "semantic_score",
        "table_rows",
        "text_lines",
    }
)
_SAFE_REGRESSION_NUMERIC_KEYS = frozenset(
    {
        "drop_ratio",
        "filled_after",
        "filled_before",
        "rows_after",
        "rows_before",
    }
)
_SAFE_POST_PATCH_NUMERIC_KEYS = frozenset(
    {
        "accepted_rows",
        "baseline_rows",
        "coverage_ratio",
        "minimum_sample",
    }
)
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}\Z")
_SENSITIVE_LABEL_PARTS = (
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)
_CHALLENGE_MARKERS = (
    "access denied",
    "captcha",
    "cf-chl",
    "cloudflare challenge",
    "just a moment",
)
_LOGIN_MARKERS = (
    "log in",
    "login required",
    "premium required",
    "sign in",
    "unauthorized",
)
_MAX_COUNT = 1_000_000_000
_MAX_ABSOLUTE_METRIC = 1_000_000_000_000.0
_MAX_SCAN_STRINGS = 80
_MAX_SCAN_CHARS = 20_000
_PREPARED_EVIDENCE_TOKEN = object()


class PreparedAIReviewEvidence(dict[str, Any]):
    """Immutable outbound evidence produced only by the trusted sanitizer.

    The canonical JSON snapshot is the actual outbound value. Even if a caller
    mutates a nested object returned through the dict compatibility interface,
    that mutation cannot cross the OpenRouter boundary.
    """

    __slots__ = ("_canonical_json",)

    def __init__(self, payload: Mapping[str, Any], *, _token: object) -> None:
        if _token is not _PREPARED_EVIDENCE_TOKEN:
            raise TypeError("PreparedAIReviewEvidence must be built by the sanitizer")
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        dict.__init__(self, json.loads(canonical))
        object.__setattr__(self, "_canonical_json", canonical)

    def to_payload(self) -> dict[str, Any]:
        """Return a fresh copy of the sanitizer-owned canonical payload."""

        value = json.loads(self._canonical_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor invariant
            raise TypeError("prepared evidence payload is not an object")
        return value

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise TypeError("prepared AI evidence is immutable")

    @staticmethod
    def _immutable(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("prepared AI evidence is immutable")

    __delitem__ = _immutable
    __ior__ = _immutable
    __setitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def _safe_identifier(value: Any, *, default: str = "unknown") -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip()
    lowered = normalized.casefold()
    if (
        not normalized
        or _SAFE_IDENTIFIER.fullmatch(normalized) is None
        or any(part in lowered for part in _SENSITIVE_LABEL_PARTS)
    ):
        return default
    return normalized


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        return value if abs(value) <= _MAX_ABSOLUTE_METRIC else None
    try:
        number = float(value)
    except OverflowError:
        return None
    if not math.isfinite(number) or abs(number) > _MAX_ABSOLUTE_METRIC:
        return None
    return round(number, 6)


def _safe_count(value: Any) -> int | None:
    number = _safe_number(value)
    if number is None or number < 0 or number > _MAX_COUNT:
        return None
    integer = int(number)
    return integer if float(number) == float(integer) else None


def _safe_ratio(value: Any) -> float | None:
    number = _safe_number(value)
    if number is None:
        return None
    return float(number)


def _safe_structured_type(value: Any, *, expected: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized == expected or normalized in _SAFE_STRUCTURED_TYPES:
        return normalized
    return "unknown"


def _structured_payload(parsed: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("structured", "hsreplay_extracted"):
        value = parsed.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _dataset_data(value: Mapping[str, Any]) -> dict[str, Any]:
    data = value.get("data")
    if isinstance(data, Mapping):
        return dict(data)
    return dict(value)


def _canonical_without_hash(evidence: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in evidence.items() if key != "evidence_hash"}
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def evidence_sha256(evidence: Mapping[str, Any]) -> str:
    """Return a deterministic SHA-256 of the sanitized evidence payload."""

    canonical = _canonical_without_hash(evidence).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _registered_source_identity(source: Source) -> tuple[str, bool, bool]:
    registered = SOURCE_BY_ID.get(source.id)
    if registered is None:
        return "unregistered", False, False
    return registered.id, True, registered == source


def _trusted_contract(contract: SourceContract | None) -> dict[str, Any]:
    if contract is None:
        return {"present": False}
    collections = [
        {
            "collection": _safe_identifier(collection),
            "minimum_rows": minimum_rows,
        }
        for collection, minimum_rows in sorted(contract.min_collection_rows)
    ]
    return {
        "present": True,
        "structured_type": _safe_structured_type(
            contract.structured_type,
            expected=contract.structured_type,
        ),
        "preferred_channels": sorted(
            _safe_identifier(channel) for channel in contract.preferred_channels
        ),
        "allow_browser_fallback": contract.allow_browser_fallback,
        "minimum_rows": contract.min_rows,
        "minimum_collections": collections,
        "critical_fields": sorted(
            _safe_identifier(field) for field in contract.critical_fields
        ),
        "minimum_field_fill_rate": contract.min_field_fill_rate,
        "regression_drop_ratio": contract.regression_drop_ratio,
        "volatility": _safe_identifier(contract.volatility),
        "fallback_policy": _safe_identifier(contract.fallback_policy),
        "minimum_html_bytes": contract.min_html_bytes,
        "early_minimum_html_bytes": contract.early_min_html_bytes,
    }


def _contract_signals(
    source: Source,
    structured: dict[str, Any],
    contract: SourceContract | None,
) -> tuple[dict[str, Any], list[str]]:
    if not structured:
        return {
            "passed": False if contract is not None else None,
            "rows_total": 0,
            "minimum_rows": contract.min_rows if contract else None,
            "row_minimum_met": False if contract and contract.min_rows else None,
            "low_activity": False,
            "collections": [],
            "field_fill_rates": {},
            "quality_score": None,
        }, ["schema.structured_missing"] if contract is not None else []

    try:
        report = contract_quality_report(source.id, structured)
    except Exception:  # noqa: BLE001 - diagnosis must remain fail-open
        return {
            "passed": False,
            "rows_total": None,
            "minimum_rows": contract.min_rows if contract else None,
            "row_minimum_met": None,
            "low_activity": False,
            "collections": [],
            "field_fill_rates": {},
            "quality_score": None,
        }, ["contract.validation_exception"]

    rows_total = _safe_count(report.get("rows_total"))
    minimum_rows = _safe_count(report.get("minimum_rows"))
    low_activity = report.get("low_activity") is True
    row_minimum_met = (
        rows_total >= minimum_rows or low_activity
        if rows_total is not None and minimum_rows is not None
        else None
    )
    issue_codes: list[str] = []
    if row_minimum_met is False:
        issue_codes.append("contract.minimum_rows_not_met")

    allowed_collections = {
        collection
        for collection, _minimum in (contract.min_collection_rows if contract else ())
    }
    raw_collections = report.get("minimum_collections")
    collections: list[dict[str, Any]] = []
    if isinstance(raw_collections, Mapping):
        for collection in sorted(allowed_collections):
            value = raw_collections.get(collection)
            if not isinstance(value, Mapping):
                continue
            rows = _safe_count(value.get("rows"))
            minimum = _safe_count(value.get("minimum_rows"))
            met = rows >= minimum if rows is not None and minimum is not None else None
            collections.append(
                {
                    "collection": _safe_identifier(collection),
                    "rows": rows,
                    "minimum_rows": minimum,
                    "minimum_met": met,
                }
            )
            if met is False:
                issue_codes.append("contract.minimum_collection_not_met")

    allowed_fields = set(contract.critical_fields if contract else ())
    raw_fields = report.get("critical_fields")
    fill_rates: dict[str, dict[str, Any]] = {}
    if isinstance(raw_fields, Mapping):
        for field in sorted(allowed_fields):
            value = raw_fields.get(field)
            if not isinstance(value, Mapping):
                continue
            filled = _safe_count(value.get("filled"))
            total = _safe_count(value.get("total"))
            rate = _safe_ratio(value.get("rate"))
            minimum_rate = contract.min_field_fill_rate if contract else 0.0
            minimum_met = rate >= minimum_rate if rate is not None else None
            fill_rates[_safe_identifier(field)] = {
                "filled": filled,
                "total": total,
                "rate": rate,
                "minimum_rate": minimum_rate,
                "minimum_met": minimum_met,
            }
            if minimum_met is False:
                issue_codes.append("contract.field_fill_below_minimum")

    return {
        "passed": bool(report.get("ok")),
        "rows_total": rows_total,
        "minimum_rows": minimum_rows,
        "row_minimum_met": row_minimum_met,
        "low_activity": low_activity,
        "collections": collections,
        "field_fill_rates": fill_rates,
        "quality_score": _safe_ratio(report.get("quality_score")),
    }, sorted(set(issue_codes))


def _semantic_signals(source: Source, structured: dict[str, Any]) -> dict[str, Any]:
    if not structured:
        return {
            "passed": False,
            "score": None,
            "numeric_metrics": {},
            "issue_codes": ["schema.structured_missing"],
            "error_count": 1,
            "warning_count": 0,
        }
    try:
        report: ValidationReport = validate_structured(source.id, structured)
    except Exception:  # noqa: BLE001 - diagnosis must remain fail-open
        return {
            "passed": False,
            "score": None,
            "numeric_metrics": {},
            "issue_codes": ["semantic.validation_exception"],
            "error_count": 1,
            "warning_count": 0,
        }

    numeric_metrics: dict[str, int | float] = {}
    for raw_key, raw_value in sorted(report.metrics.items()):
        key = _safe_identifier(raw_key)
        value = _safe_number(raw_value)
        if key != "unknown" and value is not None:
            numeric_metrics[key] = value
    issue_codes = sorted(
        {
            issue.code
            for issue in report.issues
            if _SAFE_IDENTIFIER.fullmatch(issue.code) is not None
        }
    )
    return {
        "passed": report.ok,
        "score": _safe_ratio(report.score),
        "numeric_metrics": numeric_metrics,
        "issue_codes": issue_codes,
        "error_count": sum(issue.severity == "error" for issue in report.issues),
        "warning_count": sum(issue.severity == "warning" for issue in report.issues),
    }


def _pipeline_quality_signals(quality: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(quality, Mapping):
        return {"numeric_metrics": {}, "blocked_marker": False}
    metrics: dict[str, int | float] = {}
    for key in sorted(_SAFE_PIPELINE_METRIC_KEYS):
        value = _safe_number(quality.get(key))
        if value is not None:
            metrics[key] = value
    return {
        "numeric_metrics": metrics,
        "blocked_marker": quality.get("blocked_marker") is True,
    }


def _bounded_text_signals(parsed: Mapping[str, Any]) -> tuple[bool, bool, bool]:
    title = parsed.get("title")
    title_present = isinstance(title, str) and bool(title.strip())
    candidates: list[str] = []
    if isinstance(title, str):
        candidates.append(title)
    preview = parsed.get("text_preview")
    if isinstance(preview, Sequence) and not isinstance(
        preview, (str, bytes, bytearray)
    ):
        candidates.extend(
            value for value in preview[:_MAX_SCAN_STRINGS] if isinstance(value, str)
        )
    bounded_parts: list[str] = []
    remaining = _MAX_SCAN_CHARS
    for candidate in candidates:
        if remaining <= 0:
            break
        part = candidate[:remaining]
        bounded_parts.append(part)
        remaining -= len(part)
    joined = " ".join(bounded_parts).casefold()
    challenge = any(marker in joined for marker in _CHALLENGE_MARKERS)
    login_wall = any(marker in joined for marker in _LOGIN_MARKERS)
    return title_present, challenge, login_wall


def _candidate_counts(
    source: Source, parsed: Mapping[str, Any]
) -> dict[str, int | None]:
    try:
        metric_rows = _safe_count(estimate_metric_count(source, dict(parsed)))
        filled_rows = _safe_count(estimate_filled_metric_count(source, dict(parsed)))
    except Exception:  # noqa: BLE001 - diagnosis must remain fail-open
        metric_rows = None
        filled_rows = None
    return {"metric_rows": metric_rows, "filled_metric_rows": filled_rows}


def _delta(after: int | None, before: int | None) -> int | None:
    if after is None or before is None:
        return None
    return after - before


def _retention_ratio(after: int | None, before: int | None) -> float | None:
    if after is None or before is None or before <= 0:
        return None
    return round(after / before, 6)


def _lkg_signals(
    source: Source,
    parsed: Mapping[str, Any],
    lkg: Mapping[str, Any] | None,
) -> dict[str, Any]:
    current = _candidate_counts(source, parsed)
    if not isinstance(lkg, Mapping):
        return {
            "available": False,
            "current_rows": current["metric_rows"],
            "current_filled_rows": current["filled_metric_rows"],
            "lkg_rows": None,
            "lkg_filled_rows": None,
            "row_delta": None,
            "filled_row_delta": None,
            "row_retention_ratio": None,
            "filled_retention_ratio": None,
        }
    previous = _candidate_counts(source, _dataset_data(lkg))
    return {
        "available": True,
        "current_rows": current["metric_rows"],
        "current_filled_rows": current["filled_metric_rows"],
        "lkg_rows": previous["metric_rows"],
        "lkg_filled_rows": previous["filled_metric_rows"],
        "row_delta": _delta(current["metric_rows"], previous["metric_rows"]),
        "filled_row_delta": _delta(
            current["filled_metric_rows"],
            previous["filled_metric_rows"],
        ),
        "row_retention_ratio": _retention_ratio(
            current["metric_rows"],
            previous["metric_rows"],
        ),
        "filled_retention_ratio": _retention_ratio(
            current["filled_metric_rows"],
            previous["filled_metric_rows"],
        ),
    }


def _safe_regression_reason(regression: Mapping[str, Any] | None) -> str:
    if not isinstance(regression, Mapping):
        return "none"
    detected = regression.get("detected") is True
    raw = regression.get("reason_code")
    if isinstance(raw, str) and raw in _SAFE_REGRESSION_REASON_CODES:
        return raw if detected or raw in {"none", "post_patch_bypass"} else "none"
    return "unknown" if detected else "none"


def _regression_signals(
    regression: Mapping[str, Any] | None,
    contract: SourceContract | None,
) -> dict[str, Any]:
    raw: Mapping[str, Any] = regression if isinstance(regression, Mapping) else {}
    extra_value = raw.get("extra")
    extra: Mapping[str, Any] = extra_value if isinstance(extra_value, Mapping) else raw
    numbers = {
        key: _safe_number(extra.get(key))
        for key in sorted(_SAFE_REGRESSION_NUMERIC_KEYS)
    }
    rows_before = _safe_count(numbers["rows_before"])
    rows_after = _safe_count(numbers["rows_after"])
    filled_before = _safe_count(numbers["filled_before"])
    filled_after = _safe_count(numbers["filled_after"])
    allowed_collections = {
        name for name, _minimum in (contract.min_collection_rows if contract else ())
    }
    collections: list[dict[str, Any]] = []
    raw_collections = extra.get("collections")
    if isinstance(raw_collections, Mapping):
        for name in sorted(allowed_collections):
            values = raw_collections.get(name)
            if not isinstance(values, Mapping):
                continue
            before = _safe_count(values.get("before"))
            after = _safe_count(values.get("after"))
            threshold = _safe_count(values.get("threshold"))
            collections.append(
                {
                    "collection": _safe_identifier(name),
                    "before": before,
                    "after": after,
                    "threshold": threshold,
                    "delta": _delta(after, before),
                    "retention_ratio": _retention_ratio(after, before),
                    "threshold_met": (
                        after >= threshold
                        if after is not None and threshold is not None
                        else None
                    ),
                }
            )
    return {
        "detected": raw.get("detected") is True,
        "reason_code": _safe_regression_reason(regression),
        "rows_before": rows_before,
        "rows_after": rows_after,
        "row_delta": _delta(rows_after, rows_before),
        "row_retention_ratio": _retention_ratio(rows_after, rows_before),
        "filled_before": filled_before,
        "filled_after": filled_after,
        "filled_delta": _delta(filled_after, filled_before),
        "filled_retention_ratio": _retention_ratio(filled_after, filled_before),
        "drop_ratio_threshold": _safe_ratio(numbers["drop_ratio"]),
        "post_patch_bypass": extra.get("post_patch_regression_bypass") is True,
        "collections": collections,
    }


def _post_patch_signals(
    source: Source,
    structured: Mapping[str, Any],
    post_patch: Mapping[str, Any] | None,
    *,
    regression_bypass: bool,
) -> dict[str, Any]:
    try:
        policy = policy_for(source.id)
    except Exception:  # noqa: BLE001 - diagnosis must remain fail-open
        policy = None
    metadata: Mapping[str, Any] = (
        post_patch if isinstance(post_patch, Mapping) else structured
    )
    raw_phase = metadata.get("data_phase")
    if raw_phase == "post_patch_early":
        phase = "post_patch_early"
    elif raw_phase == "stable":
        phase = "stable"
    else:
        phase = "unknown"
    numbers = {
        key: _safe_number(metadata.get(key))
        for key in sorted(_SAFE_POST_PATCH_NUMERIC_KEYS)
    }
    return {
        "policy_active": policy is not None,
        "data_phase": phase,
        "provisional": metadata.get("provisional") is True
        and phase == "post_patch_early",
        "low_sample_expected": policy is not None and phase == "post_patch_early",
        "accepted_rows": _safe_count(numbers["accepted_rows"]),
        "baseline_rows": _safe_count(numbers["baseline_rows"]),
        "coverage_ratio": _safe_ratio(numbers["coverage_ratio"]),
        "minimum_sample": _safe_count(numbers["minimum_sample"]),
        "regression_bypass": regression_bypass,
        "policy_thresholds": (
            {
                "minimum_rows": policy.minimum_rows,
                "minimum_classes": policy.minimum_classes,
                "minimum_tier_fill_rate": policy.minimum_tier_fill_rate,
                "minimum_sample": policy.minimum_sample,
            }
            if policy is not None
            else None
        ),
    }


def _deterministic_issue_codes(
    *,
    deterministic_ok: bool,
    deterministic_extra: Mapping[str, Any] | None,
    contract_issue_codes: Sequence[str],
    identity_issue_codes: Sequence[str],
) -> list[str]:
    codes = set(contract_issue_codes) | set(identity_issue_codes)
    if isinstance(deterministic_extra, Mapping):
        raw_reason = deterministic_extra.get("reason_code")
        if (
            isinstance(raw_reason, str)
            and raw_reason in _SAFE_DETERMINISTIC_REASON_CODES
            and raw_reason != "none"
        ):
            codes.add(f"deterministic.{raw_reason}")
    if not deterministic_ok and not codes:
        codes.add("deterministic.unknown_failure")
    return sorted(codes)


def build_ai_review_evidence_v2(
    source: Source,
    parsed: Mapping[str, Any],
    *,
    backend: str | None,
    stage: str,
    deterministic_ok: bool,
    deterministic_extra: Mapping[str, Any] | None = None,
    quality: Mapping[str, Any] | None = None,
    regression: Mapping[str, Any] | None = None,
    lkg: Mapping[str, Any] | None = None,
    post_patch: Mapping[str, Any] | None = None,
) -> PreparedAIReviewEvidence:
    """Build bounded, deterministic evidence without copying upstream content.

    Only registered source metadata, locally computed validation results, numeric
    aggregates, booleans, and closed-set reason codes are emitted. Raw strings,
    URLs, row values, credentials, cookies, and unknown upstream keys are never
    copied into the result.
    """

    source_id, registry_known, registry_match = _registered_source_identity(source)
    contract = get_contract(source.id) if registry_known else None
    normalized_stage = stage if stage in _SAFE_STAGES else "unknown"
    preparse_stage = normalized_stage == "fetch"
    structured = _structured_payload(parsed)
    expected_type = contract.structured_type if contract else None
    actual_type = _safe_structured_type(structured.get("type"), expected=expected_type)
    parsed_source_present = "source_id" in parsed
    parsed_source_matches = (
        parsed.get("source_id") == source.id if parsed_source_present else None
    )
    type_matches = (
        actual_type == expected_type
        if expected_type is not None and actual_type is not None
        else None
    )
    identity_issue_codes: list[str] = []
    if parsed_source_matches is False:
        identity_issue_codes.append("identity.parsed_source_mismatch")
    if type_matches is False:
        identity_issue_codes.append("identity.structured_type_mismatch")

    if preparse_stage:
        contract_validation = {
            "evaluated": False,
            "passed": None,
            "rows_total": None,
            "minimum_rows": contract.min_rows if contract else None,
            "row_minimum_met": None,
            "low_activity": None,
            "collections": [],
            "field_fill_rates": {},
            "quality_score": None,
        }
        contract_issue_codes: list[str] = []
        semantic = {
            "evaluated": False,
            "passed": None,
            "score": None,
            "numeric_metrics": {},
            "issue_codes": [],
            "error_count": 0,
            "warning_count": 0,
        }
        lkg_comparison = {
            "evaluated": False,
            "available": False,
            "current_rows": None,
            "current_filled_rows": None,
            "lkg_rows": None,
            "lkg_filled_rows": None,
            "row_delta": None,
            "filled_row_delta": None,
            "row_retention_ratio": None,
            "filled_retention_ratio": None,
        }
    else:
        contract_validation, contract_issue_codes = _contract_signals(
            source,
            structured,
            contract,
        )
        contract_validation["evaluated"] = True
        semantic = _semantic_signals(source, structured)
        semantic["evaluated"] = True
        lkg_comparison = _lkg_signals(source, parsed, lkg)
        lkg_comparison["evaluated"] = True
    regression_signals = _regression_signals(regression, contract)
    title_present, challenge_detected, login_wall_detected = _bounded_text_signals(
        parsed
    )
    pipeline_quality = _pipeline_quality_signals(quality)
    challenge_detected = challenge_detected or pipeline_quality["blocked_marker"]

    evidence: dict[str, Any] = {
        "schema_version": 2,
        "stage": normalized_stage,
        "source": {
            "id": source_id,
            "registry_known": registry_known,
            "registry_match": registry_match,
        },
        "fetch": {"backend": _safe_identifier(backend)},
        "trusted_contract": _trusted_contract(contract),
        "identity": {
            "parsed_source_id_present": parsed_source_present,
            "parsed_source_id_matches": parsed_source_matches,
            "structured_payload_present": bool(structured),
            "expected_structured_type": _safe_structured_type(
                expected_type,
                expected=expected_type,
            ),
            "actual_structured_type": actual_type,
            "structured_type_matches": type_matches,
            "title_present": title_present,
            "challenge_detected": challenge_detected,
            "login_wall_detected": login_wall_detected,
        },
        "deterministic_validation": {
            "passed": bool(deterministic_ok),
            "issue_codes": _deterministic_issue_codes(
                deterministic_ok=deterministic_ok,
                deterministic_extra=deterministic_extra,
                contract_issue_codes=contract_issue_codes,
                identity_issue_codes=identity_issue_codes,
            ),
            "pipeline_numeric_metrics": pipeline_quality["numeric_metrics"],
        },
        "contract_validation": contract_validation,
        "semantic_validation": semantic,
        "regression": regression_signals,
        "lkg_comparison": lkg_comparison,
        "post_patch": _post_patch_signals(
            source,
            structured,
            post_patch,
            regression_bypass=regression_signals["post_patch_bypass"],
        ),
    }
    evidence["evidence_hash"] = evidence_sha256(evidence)
    return PreparedAIReviewEvidence(evidence, _token=_PREPARED_EVIDENCE_TOKEN)


__all__ = [
    "PreparedAIReviewEvidence",
    "build_ai_review_evidence_v2",
    "evidence_sha256",
]
