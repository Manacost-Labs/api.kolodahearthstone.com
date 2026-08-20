#!/usr/bin/env python3
"""Read-only analyzer for the public parser reliability contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _integer(value: Any) -> int:
    return int(_number(value))


def _recovery_counts(
    value: Any,
    *,
    outcome: str,
    expected_events: int,
) -> dict[str, int] | None:
    if not isinstance(value, dict) or not isinstance(value.get(outcome), dict):
        return None
    raw = value[outcome]
    keys = (
        "events",
        "recovered_to_fresh",
        "reclassified_upstream_pending",
        "unresolved",
    )
    if any(
        isinstance(raw.get(key), bool) or not isinstance(raw.get(key), int)
        for key in keys
    ):
        return None
    result = {key: int(raw[key]) for key in keys}
    if (
        any(count < 0 for count in result.values())
        or result["events"] != expected_events
        or result["events"]
        != result["recovered_to_fresh"]
        + result["reclassified_upstream_pending"]
        + result["unresolved"]
    ):
        return None
    return result


def _unwrap(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("reliability response must be a JSON object")
    data = payload.get("data", payload)
    if not isinstance(data, dict) or not isinstance(data.get("windows"), list):
        raise TypeError("reliability response does not contain data.windows")
    return data


def _finding(
    severity: str,
    code: str,
    message: str,
    *,
    window: str | None = None,
) -> dict[str, str]:
    result = {"severity": severity, "code": code, "message": message}
    if window is not None:
        result["window"] = window
    return result


def build_audit(payload: Any, *, target_pct: float = 99.0) -> dict[str, Any]:
    report = _unwrap(payload)
    findings: list[dict[str, str]] = []
    windows: list[dict[str, Any]] = []

    for raw_window in report["windows"]:
        if not isinstance(raw_window, dict):
            continue
        label = str(raw_window.get("window", "unknown"))
        eligible = _integer(raw_window.get("eligible_attempts"))
        counts = raw_window.get("counts")
        if not isinstance(counts, dict):
            counts = {}
        fresh = _integer(counts.get("fresh_published"))
        provisional = _integer(counts.get("provisional"))
        lkg = _integer(counts.get("lkg_served"))
        failed = _integer(counts.get("failed"))
        timed_out = _integer(counts.get("timed_out"))
        skipped = _integer(counts.get("skipped"))
        upstream_pending = _integer(raw_window.get("upstream_pending_attempts"))
        end_to_end_attempts = _integer(raw_window.get("end_to_end_attempts"))
        if end_to_end_attempts <= 0:
            end_to_end_attempts = eligible + upstream_pending
        bad = max(eligible - fresh, 0)
        end_to_end_bad = max(end_to_end_attempts - fresh, 0)
        allowed_bad = eligible * max(100.0 - target_pct, 0.0) / 100.0
        measurement = str(raw_window.get("measurement_status", "unknown"))
        raw_recovery = raw_window.get("outcome_recovery")
        provisional_recovery = _recovery_counts(
            raw_recovery,
            outcome="provisional",
            expected_events=provisional,
        )
        lkg_recovery = _recovery_counts(
            raw_recovery,
            outcome="lkg_served",
            expected_events=lkg,
        )
        recovery_reported = (
            provisional_recovery is not None and lkg_recovery is not None
        )

        summary = {
            "window": label,
            "measurement_status": measurement,
            "coverage_ratio": raw_window.get("coverage_ratio"),
            "eligible_attempts": eligible,
            "fresh_published": fresh,
            "provisional": provisional,
            "lkg_served": lkg,
            "failed": failed,
            "timed_out": timed_out,
            "skipped": skipped,
            "upstream_pending_attempts": upstream_pending,
            "end_to_end_attempts": end_to_end_attempts,
            "full_fresh_rate_pct": raw_window.get("full_fresh_rate_pct"),
            "end_to_end_fresh_rate_pct": raw_window.get(
                "end_to_end_fresh_rate_pct",
                raw_window.get("full_fresh_rate_pct"),
            ),
            "accepted_fresh_rate_pct": raw_window.get("accepted_fresh_rate_pct"),
            "data_available_rate_pct": raw_window.get("data_available_rate_pct"),
            "bad_attempts": bad,
            "end_to_end_bad_attempts": end_to_end_bad,
            "allowed_bad_attempts": round(allowed_bad, 4),
            "outcome_recovery": {
                "reported": recovery_reported,
                "provisional": provisional_recovery,
                "lkg_served": lkg_recovery,
            },
        }
        windows.append(summary)

        full_fresh_rate = raw_window.get("full_fresh_rate_pct")
        if eligible and _number(full_fresh_rate, -1.0) < target_pct:
            findings.append(
                _finding(
                    "critical" if label == "24h" else "high",
                    "freshness_below_target",
                    f"Full-fresh is {full_fresh_rate}% ({fresh}/{eligible}); "
                    f"bad={bad}, allowed={allowed_bad:.2f}.",
                    window=label,
                )
            )
        if measurement != "observed":
            findings.append(
                _finding(
                    "high",
                    "measurement_incomplete",
                    "The window is still collecting; it cannot prove the SLO.",
                    window=label,
                )
            )
        if upstream_pending:
            findings.append(
                _finding(
                    "medium",
                    "upstream_publication_pending",
                    f"{upstream_pending} attempts independently confirmed that "
                    "the upstream artifact was not yet published; they remain "
                    "bad for end-to-end freshness but are excluded from parser SLO.",
                    window=label,
                )
            )
        if provisional and not recovery_reported:
            findings.append(
                _finding(
                    "high",
                    "provisional_candidates",
                    f"{provisional} candidates are accepted but not full-fresh.",
                    window=label,
                )
            )
        elif provisional_recovery and provisional_recovery["unresolved"]:
            findings.append(
                _finding(
                    "high",
                    "provisional_unresolved",
                    f"{provisional_recovery['unresolved']}/{provisional} provisional "
                    "events still have no later fresh publication.",
                    window=label,
                )
            )
        if lkg and not recovery_reported:
            findings.append(
                _finding(
                    "high",
                    "lkg_dependency",
                    f"{lkg} logical attempts served last-known-good data.",
                    window=label,
                )
            )
        elif lkg_recovery:
            if lkg_recovery["unresolved"]:
                findings.append(
                    _finding(
                        "high",
                        "lkg_unresolved",
                        f"{lkg_recovery['unresolved']}/{lkg} LKG events still "
                        "have no later fresh or verified upstream resolution.",
                        window=label,
                    )
                )
            if lkg_recovery["reclassified_upstream_pending"]:
                findings.append(
                    _finding(
                        "medium",
                        "lkg_upstream_reclassified",
                        f"{lkg_recovery['reclassified_upstream_pending']}/{lkg} "
                        "historical LKG events are now explained by a verified "
                        "upstream publication delay; data remained stale.",
                        window=label,
                    )
                )

        reasons = raw_window.get("failure_reasons")
        if isinstance(reasons, dict):
            unknown = _integer(reasons.get("unknown"))
            if unknown:
                findings.append(
                    _finding(
                        "medium",
                        "unknown_failures",
                        f"{unknown} failures lack an actionable bounded reason.",
                        window=label,
                    )
                )

        completeness = raw_window.get("verified_completeness")
        if isinstance(completeness, dict):
            catalog_coverage = _number(completeness.get("source_catalog_coverage_pct"))
            observation_coverage = _number(
                completeness.get("instrumented_source_observation_coverage_pct")
            )
            attempt_coverage = _number(
                completeness.get("coverage_of_all_parser_attempts_pct")
            )
            instrumented_sources = _integer(completeness.get("instrumented_sources"))
            observed_sources = _integer(
                completeness.get("observed_instrumented_sources")
            )
            sources_without_observations = _integer(
                completeness.get("sources_without_observations")
            )
            tracked_attempts = _integer(completeness.get("tracked_attempts"))
            summary["verified_completeness"] = {
                "source_catalog_coverage_pct": catalog_coverage,
                "instrumented_source_observation_coverage_pct": observation_coverage,
                "coverage_of_all_parser_attempts_pct": attempt_coverage,
                "instrumented_sources": instrumented_sources,
                "observed_instrumented_sources": observed_sources,
                "sources_without_observations": sources_without_observations,
                "tracked_attempts": tracked_attempts,
            }
            if catalog_coverage < target_pct:
                findings.append(
                    _finding(
                        "high",
                        "completeness_catalog_gap",
                        f"Verified-completeness instrumentation covers only "
                        f"{catalog_coverage}% of catalog sources.",
                        window=label,
                    )
                )
            if observation_coverage < target_pct:
                findings.append(
                    _finding(
                        "high",
                        "completeness_observation_gap",
                        f"Only {observed_sources}/{instrumented_sources} instrumented "
                        f"sources were observed ({observation_coverage}%); "
                        f"without observations={sources_without_observations}.",
                        window=label,
                    )
                )
            if attempt_coverage < target_pct:
                findings.append(
                    _finding(
                        "high",
                        "completeness_attempt_coverage_gap",
                        f"Only {tracked_attempts}/{eligible} eligible parser attempts "
                        f"have completeness evidence ({attempt_coverage}%).",
                        window=label,
                    )
                )

        schedules = raw_window.get("scheduled_reliability")
        if isinstance(schedules, dict):
            schedule_coverage = _number(schedules.get("schedule_coverage_ratio"))
            summary["scheduled_reliability"] = {
                "schedule_coverage_ratio": schedule_coverage,
                "on_time_upstream_pending": _integer(
                    schedules.get("on_time_upstream_pending")
                ),
                "parser_eligible_due_slots": _integer(
                    schedules.get("parser_eligible_due_slots")
                ),
                "parser_on_time_fresh_rate_pct": schedules.get(
                    "parser_on_time_fresh_rate_pct"
                ),
            }
            if schedule_coverage < 1.0:
                findings.append(
                    _finding(
                        "high",
                        "schedule_ledger_gap",
                        f"Schedule ledger coverage is {schedule_coverage:.2%}.",
                        window=label,
                    )
                )

        rollout = raw_window.get("parsesunix_rollout")
        if (
            isinstance(rollout, dict)
            and _integer(rollout.get("observed_attempts")) == 0
        ):
            findings.append(
                _finding(
                    "low",
                    "parsesunix_unobserved",
                    "ParsesUnix has no bounded rollout evidence in this window.",
                    window=label,
                )
            )

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(
        key=lambda item: (
            severity_order[item["severity"]],
            item.get("window", ""),
            item["code"],
        )
    )
    return {
        "generated_at": report.get("generated_at"),
        "target_rate_pct": target_pct,
        "windows": windows,
        "findings": findings,
        "limitations": [
            "Public aggregates identify reliability gaps but not source-level root causes.",
            "A collecting window is observed evidence, not proof of a completed SLO period.",
        ],
    }


def _load_remote(base_url: str, timeout: float) -> Any:
    url = f"{base_url.rstrip('/')}/system/parsing-reliability"
    request = Request(
        url, headers={"Accept": "application/json", "User-Agent": "hs-parser-audit/1"}
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", type=Path, help="Saved reliability JSON response")
    source.add_argument(
        "--base-url",
        default="https://api.kolodahearthstone.com/v1",
        help="Public API v1 base URL",
    )
    parser.add_argument(
        "--target", type=float, default=99.0, help="Full-fresh target percentage"
    )
    parser.add_argument(
        "--timeout", type=float, default=15.0, help="HTTP timeout in seconds"
    )
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.input is not None:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
        else:
            payload = _load_remote(args.base_url, args.timeout)
        result = build_audit(payload, target_pct=args.target)
    except (
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        HTTPError,
        URLError,
    ) as exc:
        print(f"audit failed: {exc}", file=sys.stderr)
        return 2

    indent = None if args.compact else 2
    print(json.dumps(result, ensure_ascii=False, indent=indent, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
