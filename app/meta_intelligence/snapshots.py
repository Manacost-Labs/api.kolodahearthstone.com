from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from hearthstone.deckstrings import parse_deckstring

from ..source_validators import validate_structured
from ..source_state import EFFECTIVE_OK_CACHED
from .models import Settings, canonical, digest, now_iso, number, timestamp
from .store import Store


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _field(row: dict, *names: str) -> Any:
    normalized = {_key(k): v for k, v in row.items()}
    return next((normalized[_key(n)] for n in names if _key(n) in normalized), None)


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(str(value).replace(",", "").replace(" ", "").replace("\u00a0", ""))
        return result if result >= 0 else None
    except (ValueError, TypeError):
        return None


def normalize_deck(row: dict, *, inherited_at: str | None = None) -> dict:
    code = row.get("deck_code") or row.get("deckstring")
    cards, heroes, sideboards = [], [], []
    complete = False
    if isinstance(code, str) and len(code) <= 4096:
        try:
            main, heroes, _, side = parse_deckstring(code)
            cards = [{"dbf_id": card, "count": count, "sideboard_owner": 0} for card, count in main]
            sideboards = [{"dbf_id": card, "count": count, "sideboard_owner": owner} for card, count, owner in side]
            complete = bool(cards)
        except (ValueError, TypeError, EOFError, IndexError):
            pass
    if not cards and isinstance(row.get("cards"), list):
        cards = [{"dbf_id": int(c["dbf_id"]), "count": int(c["count"]), "sideboard_owner": int(c.get("sideboard_owner") or 0)}
                 for c in row["cards"] if c.get("dbf_id") and c.get("count")]
        complete = row.get("composition_complete") is True and bool(cards)
    all_cards = sorted(cards + sideboards, key=lambda c: (c["sideboard_owner"], c["dbf_id"]))
    identity = digest({"cards": all_cards, "heroes": sorted(heroes)}) if complete else digest({"code": code, "url": row.get("url")})
    winrate = number(row.get("win_rate", row.get("winrate")))
    try:
        deck_at = timestamp(row.get("updated_at") or inherited_at).isoformat()
    except (ValueError, TypeError, AttributeError):
        deck_at = None
    return {
        "id": identity, "deck_code": code, "cards": all_cards,
        "composition_complete": complete, "winrate": winrate if winrate is not None and 0 <= winrate <= 100 else None,
        "games": _count(row.get("games", row.get("total_games"))),
        "url": row.get("url"), "fetched_at": deck_at,
    }


def normalize_archetype(row: dict, fetched_at: str) -> dict:
    pop_raw = _field(row, "popularity_pct", "popularity", "play_rate")
    pop = number(str(pop_raw).split("%", 1)[0].split("(", 1)[0]) if pop_raw is not None else None
    games = _count(_field(row, "games", "total_games"))
    if games is None and pop_raw:
        match = re.search(r"\(([\d,\s]+)\)", str(pop_raw))
        games = _count(match.group(1)) if match else None
    return {
        "archetype": str(_field(row, "archetype", "archetype_name", "deck", "name") or "").strip(),
        "class": _field(row, "class", "player_class"),
        "winrate": number(_field(row, "winrate", "win_rate", "Winrate↓")),
        "popularity_pct": pop, "games": games,
        "decks": [normalize_deck(d, inherited_at=None) for d in row.get("decks", [])],
        "cards": row.get("card_stats", []), "class_matchups": row.get("class_matchups", []),
        "cards_fetched_at": row.get("card_stats_updated_at"),
        "matchups_fetched_at": row.get("matchups_updated_at"),
    }


def _dimensions(source_id: str, dataset: dict, structured: dict) -> dict:
    query = parse_qs(urlparse(dataset.get("final_url") or "").query)
    criteria = structured.get("criteria") or {}
    fmt = criteria.get("format") or {"1": "wild", "2": "standard"}.get(query.get("format", [""])[0])
    if fmt not in {"standard", "wild"}:
        fmt = "wild" if "wild" in source_id else "standard" if "standard" in source_id else "unknown"
    rank = criteria.get("rank") or query.get("rank", [None])[0]
    if not rank:
        rank = next((r for r in ("diamond_4to1", "top_legend", "top_5k", "legend") if r in source_id), "unknown")
    period = criteria.get("period") or query.get("period", ["unknown"])[0]
    patch = criteria.get("patch") or dataset.get("patch") or (period if str(period).startswith("patch_") else "unknown")
    return {"format": fmt, "rank": rank, "period": period, "patch": patch}


def ingest(store: Store, source_id: str, dataset: dict, status: dict,
           *, settings: Settings | None = None, now: datetime | None = None, dry_run: bool = False) -> dict:
    """Ingest published datasets only. Never fetch, re-date LKG, or infer missing metrics."""
    settings = settings or Settings.from_env()
    now = now or datetime.now(UTC)
    from .publications import parts

    projected = parts(dataset)
    if projected:
        result = {"snapshots": [], "rejected": []}
        matched = status.get("fetched_at") == dataset.get("fetched_at")
        source_valid = validate_structured(source_id, dataset["data"]["structured"]).ok
        for suffix, payload, part_status in projected:
            part_status = {**status, **part_status,
                           "cached": bool(status.get("cached") or part_status["cached"] or not matched or not source_valid)}
            admitted = ingest(store, source_id + suffix, payload, part_status,
                              settings=settings, now=now, dry_run=dry_run)
            for key in result:
                result[key].extend(admitted[key])
        return result
    data = dataset.get("data")
    if not isinstance(data, dict):
        return {"snapshots": [], "rejected": [{"source_id": source_id, "reason": "unsupported_dataset"}]}
    structured = data.get("structured") or {}
    dims = _dimensions(source_id, dataset, structured)
    fetched_at = dataset.get("fetched_at") or now.isoformat()
    reasons = []
    try:
        fetched_at = timestamp(fetched_at).isoformat()
        if timestamp(fetched_at) > now:
            reasons.append("future_timestamp")
    except (ValueError, TypeError):
        fetched_at = now.isoformat()
        reasons.append("invalid_timestamp")
    if dataset.get("state") != "ok" or status.get("state") != "ok":
        reasons.append("not_valid_publication")
    if status.get("fetched_at") != dataset.get("fetched_at"):
        reasons.append("cached_after_failure_or_unmatched_status")
    if (dataset.get("backend") in {"cache", "cached", "lkg"} or status.get("cached")
            or status.get("preserved_previous_good") or status.get("serving_cached_dataset")
            or status.get("effective_state") == EFFECTIVE_OK_CACHED
            or str(status.get("firecrawl_cache_state", "")).lower() in {"cached", "cache", "hit", "stale"}
            or status.get("last_refresh_state") not in {None, "ok"}):
        reasons.append("cached_evidence")
    report = validate_structured(source_id, structured)
    if not report.ok:
        reasons.append("source_validation_failed")
    kind = structured.get("type")
    groups: dict[tuple, dict] = defaultdict(lambda: {"archetypes": [], "matchups": [], "streamers": []})
    if kind in {"hsguru_meta_matrix", "meta", "hsguru_archetype_analysis", "meta_lab_analysis", "meta_lab_details", "meta_lab_variants"}:
        rows = structured.get("archetypes", structured.get("strategies", []))
        for row in rows:
            rd = {**dims, **{k: row[k] for k in ("format", "rank", "period", "patch") if row.get(k)}}
            if str(rd["period"]).startswith("patch_"):
                rd["patch"] = rd["period"]
            key = tuple(rd[k] for k in ("format", "rank", "period", "patch"))
            groups[key]["archetypes"].append(normalize_archetype(row, fetched_at))
            if kind in {"hsguru_archetype_analysis", "meta_lab_analysis", "meta_lab_details"}:
                if row.get("state", "ok") != "ok":
                    reasons.append("partial_archetype")
                if kind in {"hsguru_archetype_analysis", "meta_lab_analysis"}:
                    times = [row.get("card_stats_updated_at"), row.get("matchups_updated_at")]
                    if any(not t or timestamp(t) < timestamp(fetched_at) for t in times):
                        reasons.append("retained_analysis_evidence")
    elif kind == "matchups":
        groups[tuple(dims[k] for k in ("format", "rank", "period", "patch"))]["matchups"] = structured.get("matchups", [])
    elif kind == "streamer_decks":
        for row in structured.get("rows", []):
            fmt = str(row.get("format") or "unknown").lower()
            key = (fmt, dims["rank"], dims["period"], dims["patch"])
            groups[key]["streamers"].append(row)
    else:
        reasons.append("unsupported_dataset")
    if not groups:
        groups[tuple(dims[k] for k in ("format", "rank", "period", "patch"))]
        reasons.append("empty_dataset")
    inserted, rejected = [], []
    for key, payload in groups.items():
        payload["patch_scope"] = "single_patch" if str(key[2]).startswith("patch_") and key[2] == key[3] else (structured.get("criteria") or {}).get("patch_scope", "unverified")
        group_reasons = list(reasons)
        archetypes = payload["archetypes"]
        if kind in {"meta", "hsguru_meta_matrix"}:
            valid_rows = sum(bool(r["archetype"]) and r["games"] is not None and r["games"] > 0
                             and r["winrate"] is not None and 0 <= r["winrate"] <= 100
                             and r["popularity_pct"] is not None and 0 <= r["popularity_pct"] <= 100 for r in archetypes)
            coverage = valid_rows / max(1, len(archetypes))
            if valid_rows != len(archetypes) or not archetypes:
                group_reasons.append("incomplete_metrics")
        else:
            coverage = 1.0 if any(payload[k] for k in ("archetypes", "matchups", "streamers")) else 0.0
        if kind == "meta_lab_variants":
            for row in archetypes:
                if not row["archetype"] or not row["decks"] or any(
                    not d["composition_complete"] or d["games"] is None or d["games"] <= 0
                    or d["winrate"] is None or not 0 <= d["winrate"] <= 100
                    or not d["fetched_at"] for d in row["decks"]
                ):
                    group_reasons.append("incomplete_deck_metrics")
        if kind in {"hsguru_archetype_analysis", "meta_lab_analysis", "meta_lab_details"}:
            for row in archetypes:
                if not row["archetype"] or not row["cards"] or not row["class_matchups"]:
                    group_reasons.append("incomplete_analysis")
                for card in row["cards"]:
                    if not (card.get("dbf_id") or card.get("card_id")):
                        group_reasons.append("missing_card_identity")
                    for metric in ("mulligan", "drawn", "kept"):
                        count, impact = _count(card.get(metric+"_count")), number(card.get(metric+"_impact"))
                        if count == 0 and impact is None:
                            continue
                        if count is None or impact is None or not -100 <= impact <= 100:
                            group_reasons.append("incomplete_card_metrics")
                for matchup in row["class_matchups"]:
                    wr, games = number(matchup.get("winrate")), _count(matchup.get("games"))
                    if wr is None or not 0 <= wr <= 100 or games is None or games <= 0:
                        group_reasons.append("incomplete_class_matchups")
                if kind == "meta_lab_details" and (not row["decks"] or any(not d["composition_complete"] or d["games"] is None or d["winrate"] is None for d in row["decks"])):
                    group_reasons.append("incomplete_deck_metrics")
        if coverage < settings.coverage_min:
            group_reasons.append("low_coverage")
        if len({r["archetype"].casefold() for r in archetypes}) != len(archetypes):
            group_reasons.append("duplicate_archetypes")
        reason = ",".join(sorted(set(group_reasons))) or "ok"
        expected_hours = float(structured.get("expected_hours") or (24 if "analysis" in str(kind) or kind == "meta_lab_details" else 1 if kind == "streamer_decks" else 12))
        acquisition = (dataset.get("data") or {}).get("acquisition", {})
        attempts = list(acquisition.get("attempt_ids", [])) + [p["attempt_id"] for p in acquisition.get("pages", []) if p.get("attempt_id")]
        if dry_run:
            if group_reasons:
                rejected.append({"source_id": source_id, "slice": key, "reason": reason})
            else:
                inserted.append("validated")
            continue
        with store.connect(write=True) as conn:
            conn.execute("INSERT OR IGNORE INTO observations(source_id,format,rank,period,fetched_at,checked_at,valid,reason) VALUES(?,?,?,?,?,?,?,?)", (source_id, *key[:3], fetched_at, now.isoformat(), int(not group_reasons), reason))
            if group_reasons:
                rejected.append({"source_id": source_id, "slice": key, "reason": reason})
                continue
            payload_hash = digest(payload)
            identity = digest([source_id, key, fetched_at, payload_hash])
            linked = []
            for attempt in set(attempts):
                entry = conn.execute("SELECT * FROM attempts WHERE id=?", (attempt,)).fetchone()
                if entry and entry["validation"] == "valid" and entry["format"] == key[0]:
                    linked.append(entry)
            cost = sum(r["credits"] for r in linked) if linked and all(r["credits"] is not None for r in linked) else None
            duration = sum(r["duration_ms"] or 0 for r in linked) if linked else None
            payload["kind"] = kind
            payload["deck_coverage"] = structured.get("deck_coverage", "source_limited")
            # Payload hash includes every persisted field.
            payload_hash = digest(payload)
            identity = digest([source_id, key, fetched_at, payload_hash])
            conn.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                identity, source_id, *key, fetched_at, str(structured.get("parser_version", "legacy-unversioned")),
                payload_hash, coverage, expected_hours, cost, duration, canonical(payload), now.isoformat(),
            ))
            for attempt in linked:
                conn.execute("INSERT OR IGNORE INTO snapshot_attempts VALUES(?,?)", (identity, attempt["id"]))
            inserted.append(identity)
    return {"snapshots": inserted, "rejected": rejected}


def ingest_published(source_id: str, status: dict) -> None:
    """Publication hook. Lab failures must not interrupt the existing site pipeline."""
    if not source_id.startswith("hsguru") or not Settings.from_env().enabled:
        return
    from ..storage import load_dataset
    dataset = load_dataset(source_id)
    if dataset:
        ingest(Store(), source_id, dataset, status)
