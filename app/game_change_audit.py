from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .cards_index import cards_by_id
from .config import data_dir, source_operationally_enabled
from .parser_control import resolve_public_dataset
from .patches_db import list_patches
from .source_state import SourceState
from .storage import load_dataset, load_status, read_json, write_json

WIKI_API_URL = "https://hearthstone.wiki.gg/api.php"
USER_AGENT = "HSDataAPI/0.1 (+https://api.kolodahearthstone.com)"
AUDIT_SCHEMA_VERSION = 1

# One representative, user-visible feed per product area. Detailed freshness
# checks still cover every source; this list is the hard daily patch gate.
CRITICAL_SOURCES: dict[str, float] = {
    "hsguru_meta_standard_legend": 72,
    "hsguru_meta_wild_legend": 72,
    "hsreplay_cards_legend_1d": 30,
    "hsreplay_cards_wild_legend_1d": 30,
    "firestone_standard": 36,
    "hsreplay_arena_cards_advanced": 36,
    "heartharena_tierlist": 36,
    "hsreplay_battlegrounds_heroes": 36,
    "hsreplay_battlegrounds_minions": 36,
    "hsreplay_battlegrounds_comps": 36,
    "firestone_battlegrounds_comps": 36,
    "firestone_battlegrounds_cards": 36,
    "firestone_battlegrounds_spells": 36,
    "hsreplay_battlegrounds_trinkets_all_current_battlegrounds_patch": 36,
}

CARD_FINGERPRINT_FIELDS = (
    "dbfId",
    "name",
    "text",
    "type",
    "set",
    "rarity",
    "cardClass",
    "cost",
    "attack",
    "health",
    "techLevel",
    "collectible",
    "isBattlegroundsPoolMinion",
    "isBattlegroundsPoolSpell",
    "battlegroundsPremiumDbfId",
)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _fingerprint(card: dict[str, Any]) -> str:
    payload = {field: card.get(field) for field in CARD_FINGERPRINT_FIELDS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def build_card_snapshot(
    en_cards: dict[str, dict[str, Any]],
    ru_cards: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    hashes = {card_id: _fingerprint(card) for card_id, card in sorted(en_cards.items())}
    battlegrounds = [
        card
        for card in en_cards.values()
        if card.get("isBattlegroundsPoolMinion") or card.get("isBattlegroundsPoolSpell")
    ]
    missing_ru = sorted(
        card_id
        for card_id, card in en_cards.items()
        if card.get("name") and not (ru_cards.get(card_id) or {}).get("name")
    )
    bg_missing_premium = sorted(
        str(card.get("id"))
        for card in battlegrounds
        if card.get("isBattlegroundsPoolMinion")
        and not card.get("battlegroundsPremiumDbfId")
    )
    return {
        "count": len(en_cards),
        "ru_count": len(ru_cards),
        "battlegrounds_pool_count": len(battlegrounds),
        "missing_ru_count": len(missing_ru),
        "missing_ru_ids": missing_ru[:100],
        "bg_missing_premium_count": len(bg_missing_premium),
        "bg_missing_premium_ids": bg_missing_premium[:100],
        "hashes": hashes,
    }


def compare_card_snapshots(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    before = (previous or {}).get("hashes") or {}
    after = current.get("hashes") or {}
    added = sorted(set(after) - set(before)) if before else []
    removed = sorted(set(before) - set(after)) if before else []
    changed = sorted(card_id for card_id in set(before) & set(after) if before[card_id] != after[card_id])
    return {
        "baseline_initialized": not bool(before),
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "added_ids": added[:200],
        "removed_ids": removed[:200],
        "changed_ids": changed[:200],
    }


def fetch_wiki_recent_changes(*, since: datetime, limit: int = 200) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "list": "recentchanges",
            "rcnamespace": "0",
            "rcstart": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "rcend": since.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "rclimit": min(max(limit, 1), 500),
            "rcprop": "title|ids|timestamp|comment|tags",
            "format": "json",
            "formatversion": "2",
        }
    )
    request = urllib.request.Request(
        f"{WIKI_API_URL}?{params}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    rows = ((payload.get("query") or {}).get("recentchanges") or [])
    return [row for row in rows if isinstance(row, dict)]


def relevant_wiki_changes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pattern = re.compile(
        r"patch|battleground|card|standard|wild|arena|quest|titan|hero|trinket|spell|minion",
        re.IGNORECASE,
    )
    return [row for row in rows if pattern.search(str(row.get("title") or ""))]


def _dataset_age_hours(payload: dict[str, Any], now: datetime) -> float | None:
    fetched = _parse_timestamp(payload.get("fetched_at"))
    if fetched is None:
        return None
    return max(0.0, (now - fetched).total_seconds() / 3600)


def audit_critical_sources(
    *,
    now: datetime,
    status_loader: Callable[[str], dict[str, Any] | None] = load_status,
    dataset_loader: Callable[[str], dict[str, Any] | None] = load_dataset,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for source_id, max_age_hours in CRITICAL_SOURCES.items():
        if not source_operationally_enabled(source_id):
            rows.append(
                {
                    "source_id": source_id,
                    "state": "excluded",
                    "dataset_age_hours": None,
                    "max_age_hours": max_age_hours,
                    "serving_cached_after_failure": False,
                    "last_refresh_state": None,
                    "reasons": [],
                    "exclusion_reason": "operationally-disabled",
                }
            )
            continue
        status = status_loader(source_id) or {}
        candidate = dataset_loader(source_id) or {}
        dataset = resolve_public_dataset(source_id, candidate) or {}
        age = _dataset_age_hours(dataset, now)
        state = str(status.get("state") or SourceState.NEVER_FETCHED)
        cached_failure = bool(status.get("serving_cached_dataset")) and status.get(
            "last_refresh_state"
        ) not in (None, SourceState.OK)
        reasons: list[str] = []
        if state != SourceState.OK:
            reasons.append(f"state={state}")
        if not dataset:
            reasons.append("missing_dataset")
        elif age is None:
            reasons.append("missing_fetched_at")
        elif age > max_age_hours:
            reasons.append(f"stale>{max_age_hours:g}h")
        if cached_failure:
            reasons.append("live_refresh_failed_cached")
        row = {
            "source_id": source_id,
            "state": state,
            "dataset_age_hours": round(age, 2) if age is not None else None,
            "max_age_hours": max_age_hours,
            "serving_cached_after_failure": cached_failure,
            "last_refresh_state": status.get("last_refresh_state"),
            "reasons": reasons,
        }
        rows.append(row)
        if reasons:
            issues.append(row)
    return rows, issues


def audit_paths() -> tuple[Path, Path, Path]:
    directory = data_dir() / "audits"
    directory.mkdir(parents=True, exist_ok=True)
    return (
        directory / "game-change-baseline.json",
        directory / "game-change-latest.json",
        directory / f"game-change-{datetime.now(UTC).date().isoformat()}.json",
    )


def _public_patch_version(version: str) -> str | None:
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
        return None
    parts = version.split(".")
    return ".".join(parts[:3]) if len(parts) == 4 else version


def current_patch_from_catalog(
    *, recent_changes: list[dict[str, Any]] | None = None
) -> str:
    rows = list_patches(limit=1).get("patches") or []
    candidates = [str((rows[0] if rows else {}).get("version") or "")]
    for row in recent_changes or []:
        match = re.fullmatch(
            r"Patch\s+(\d+(?:\.\d+){1,3})",
            str(row.get("title") or "").strip(),
            flags=re.IGNORECASE,
        )
        if match:
            candidates.append(match.group(1))
    versions = [
        normalized
        for candidate in candidates
        if (normalized := _public_patch_version(candidate)) is not None
    ]
    if not versions:
        raise RuntimeError("Patch catalog returned no valid current version")
    return max(versions, key=lambda value: tuple(int(part) for part in value.split(".")))


def run_game_change_audit() -> dict[str, Any]:
    now = datetime.now(UTC)
    baseline_path, latest_path, history_path = audit_paths()
    baseline = read_json(baseline_path) or {}
    wiki_rows = fetch_wiki_recent_changes(since=now - timedelta(hours=36))
    patch = current_patch_from_catalog(recent_changes=wiki_rows)
    card_snapshot = build_card_snapshot(cards_by_id("enUS"), cards_by_id("ruRU"))
    card_changes = compare_card_snapshots(baseline.get("cards"), card_snapshot)
    wiki_relevant = relevant_wiki_changes(wiki_rows)
    source_rows, source_issues = audit_critical_sources(now=now)
    previous_patch = baseline.get("patch")
    patch_changed = bool(previous_patch and previous_patch != patch)
    content_changed = any(card_changes[key] for key in ("added_count", "removed_count", "changed_count"))
    requires_attention = patch_changed or content_changed or bool(source_issues)
    report = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "checked_at": now.isoformat(),
        "ok": not requires_attention,
        "requires_attention": requires_attention,
        "patch": {
            "current": patch,
            "previous": previous_patch,
            "changed": patch_changed,
            "baseline_initialized": previous_patch is None,
        },
        "cards": {key: value for key, value in card_snapshot.items() if key != "hashes"},
        "card_changes": card_changes,
        "wiki": {
            "window_hours": 36,
            "changes_total": len(wiki_rows),
            "relevant_count": len(wiki_relevant),
            "relevant_changes": wiki_relevant[:100],
        },
        "sources": source_rows,
        "source_issue_count": len(source_issues),
        "source_issues": source_issues,
    }
    baseline_payload = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "updated_at": now.isoformat(),
        "patch": patch,
        "cards": card_snapshot,
    }
    write_json(baseline_path, baseline_payload)
    write_json(latest_path, report)
    write_json(history_path, report)
    return report
