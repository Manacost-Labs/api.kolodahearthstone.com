from __future__ import annotations

import json
from collections import defaultdict

from .freshness import with_freshness
from .models import RULE_VERSION, Settings, canonical, digest, now_iso, timestamp
from .store import Store

KINDS = {"popularity_rise", "popularity_drop", "winrate_shift", "matchup_shift", "deck_variant_detected", "streamer_burst"}


def _signal(kind: str, snapshots: list[dict], archetype: str, state: str, reason: str, values: dict) -> dict:
    last = snapshots[-1]
    scope = {k: last[k] for k in ("format", "rank", "period", "patch")}
    return {
        "id": digest([kind, last["source_id"], scope, archetype, RULE_VERSION]),
        "kind": kind, "state": state, **scope, "archetype": archetype,
        "detected_at": last["fetched_at"], "updated_at": now_iso(),
        "confidence": {"observing": 0.25, "early": 0.55, "confirmed": 0.8, "rejected": 0.0}[state],
        "rule_version": RULE_VERSION, "reason": reason,
        "evidence": {"snapshot_ids": [s["id"] for s in snapshots], "values": values, "confidence_type": "rule_score_not_probability"},
    }


def evaluate_sequence(snapshots: list[dict], settings: Settings) -> list[dict]:
    if len(snapshots) < 2:
        return []
    snapshots = sorted(snapshots, key=lambda s: s["fetched_at"])[-settings.confirmed_snapshots:]
    first, last = snapshots[0], snapshots[-1]
    # Caller groups by exact source/slice/patch. Unknown patch cannot confirm.
    same_scope = len({tuple(s[k] for k in ("source_id", "format", "rank", "period", "patch")) for s in snapshots}) == 1
    if not same_scope:
        return []
    fresh = last.get("freshness", {}).get("fresh", False)
    timestamps = [timestamp(s["fetched_at"]) for s in snapshots]
    spaced = len(set(timestamps)) == len(timestamps) and (timestamps[-1]-timestamps[0]).total_seconds() >= settings.min_signal_hours*3600
    contiguous = all((b-a).total_seconds() <= max(snapshots[i]["expected_hours"], snapshots[i+1]["expected_hours"])*3600 for i,(a,b) in enumerate(zip(timestamps,timestamps[1:])))
    ready = fresh and spaced and contiguous and len(snapshots) >= settings.confirmed_snapshots and last["patch"] != "unknown" and last["format"] in {"standard","wild"} and last["period"] != "unknown" and last["rank"] != "unknown"
    ready = ready and all(s["payload"].get("patch_scope") == "single_patch" for s in snapshots)
    maps = [{r["archetype"]: r for r in s["payload"]["archetypes"]} for s in snapshots]
    signals = []
    for name in maps[-1]:
        rows = [m.get(name) for m in maps]
        if any(r is None for r in rows):
            continue
        enough = all((r.get("games") or 0) >= settings.min_games for r in rows)
        popularity = [r.get("popularity_pct") for r in rows]
        if all(v is not None for v in popularity):
            delta = popularity[-1]-popularity[0]
            relative = abs(delta)/max(popularity[0], 0.1)
            if abs(delta) >= settings.popularity_pp and relative >= settings.popularity_relative:
                monotonic = all((b-a)*delta > 0 for a,b in zip(popularity,popularity[1:]))
                state = "confirmed" if ready and enough and monotonic else "early" if fresh and enough else "observing"
                signals.append(_signal("popularity_rise" if delta>0 else "popularity_drop", snapshots, name, state,
                    f"Популярность: {popularity[0]:.1f}% → {popularity[-1]:.1f}% ({delta:+.1f} п.п.). Подтверждение означает устойчивое направление в последовательных снимках.",
                    {"popularity": popularity, "games": [r["games"] for r in rows]}))
        winrates = [r.get("winrate") for r in rows]
        if all(v is not None for v in winrates) and abs(winrates[-1]-winrates[0]) >= settings.winrate_pp:
            # Aggregated rolling windows overlap; counts do not establish independent samples.
            signals.append(_signal("winrate_shift", snapshots, name, "early" if fresh and enough else "observing",
                "Изменение винрейта требует независимых выборок. Перекрывающиеся периоды не позволяют подтвердить статистическую значимость.", {"winrates": winrates, "games": [r["games"] for r in rows]}))
        deck_sets = [{d["id"] for d in r["decks"] if d["composition_complete"] and d["fetched_at"] and d["fetched_at"] >= s["fetched_at"]} for r,s in zip(rows,snapshots)]
        new = deck_sets[-1] - deck_sets[0]
        if new:
            # One discovery is early; persistence requires additional later observations.
            signals.append(_signal("deck_variant_detected", snapshots, name, "early" if fresh else "observing",
                f"Найдено новых составов: {len(new)}. Новизна состава не означает превосходство по силе.", {"variant_ids": sorted(new)}))
    match_maps = [{(r.get("archetype"), r.get("vs")): r for r in s["payload"]["matchups"]} for s in snapshots]
    for pair, row in match_maps[-1].items():
        previous = match_maps[0].get(pair)
        if not previous:
            continue
        from .models import number
        a,b = number(previous.get("winrate")), number(row.get("winrate"))
        if a is not None and b is not None and abs(b-a) >= settings.winrate_pp:
            signals.append(_signal("matchup_shift", snapshots, str(pair[0]), "observing",
                "Матчап изменился; для подтверждения нужны размеры и независимость выборок пары.", {"opponent": pair[1], "before": a, "after": b}))
    authors = defaultdict(set)
    for row in last["payload"]["streamers"]:
        if row.get("author_reference") and row.get("canonical_url") and row.get("archetype"):
            authors[row["archetype"]].add(row["author_reference"])
    for name, independent in authors.items():
        if len(independent) >= 3:
            signals.append(_signal("streamer_burst", snapshots, name, "early" if fresh else "observing",
                "Несколько независимых авторов играют архетип. Само по себе это не подтверждает его силу.", {"independent_authors": len(independent)}))
    return signals


def recompute(store: Store, settings: Settings | None = None) -> int:
    settings = settings or Settings.from_env()
    grouped = defaultdict(list)
    for snap in store.snapshots(limit=10000):
        grouped[tuple(snap[k] for k in ("source_id", "format", "rank", "period", "patch"))].append(snap)
    produced = []
    for snapshots in grouped.values():
        snapshots.sort(key=lambda s: s["fetched_at"])
        # An invalid/partial observation breaks the evidence sequence.
        last = snapshots[-1]
        with store.connect() as conn:
            bad = conn.execute("SELECT MAX(checked_at) FROM observations WHERE source_id=? AND format=? AND rank=? AND period=? AND valid=0", tuple(last[k] for k in ("source_id", "format", "rank", "period"))).fetchone()[0]
        eligible = [s for s in snapshots if not bad or s["fetched_at"] > bad]
        if not eligible:
            continue
        eligible[-1] = with_freshness(store, eligible[-1])
        produced.extend(evaluate_sequence(eligible, settings))
    active = {s["id"] for s in produced}
    with store.connect(write=True) as conn:
        for signal in produced:
            evidence = canonical(signal["evidence"])
            existing = conn.execute("SELECT * FROM signals WHERE id=?", (signal["id"],)).fetchone()
            if existing and existing["state"] == signal["state"] and existing["evidence"] == evidence:
                continue
            values = [signal[k] for k in ("id","kind","state","format","rank","period","patch","archetype","detected_at","updated_at","confidence","rule_version","reason")]+[evidence]
            conn.execute("""INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                state=excluded.state,updated_at=excluded.updated_at,confidence=excluded.confidence,reason=excluded.reason,evidence=excluded.evidence""", values)
            conn.execute("INSERT OR IGNORE INTO signal_events(signal_id,at,state,evidence) VALUES(?,?,?,?)", (signal["id"],signal["updated_at"],signal["state"],evidence))
        for previous in conn.execute("SELECT * FROM signals WHERE state!='rejected'").fetchall():
            if previous["id"] not in active:
                at = now_iso()
                conn.execute("UPDATE signals SET state='rejected',confidence=0,reason=?,updated_at=? WHERE id=?", ("Условие правила больше не выполняется или отсутствуют свежие валидные подтверждения.",at,previous["id"]))
                conn.execute("INSERT OR IGNORE INTO signal_events(signal_id,at,state,evidence) VALUES(?,?,?,?)", (previous["id"],at,"rejected",previous["evidence"]))
    return len(produced)
