from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, datetime

from ..config import data_dir
from .freshness import with_freshness
from .models import Settings, timestamp
from .store import Store


def scopes(store: Store) -> list[dict]:
    with store.connect() as conn:
        rows = conn.execute("SELECT format,rank,period,patch,COUNT(*) AS snapshots,MAX(fetched_at) AS latest FROM snapshots GROUP BY format,rank,period,patch ORDER BY format,rank,period,patch DESC").fetchall()
    return [dict(r) for r in rows]


def current(store: Store, *, format: str, rank: str, period: str, patch: str) -> list[dict]:
    # Keep sources separate within the explicitly selected historical patch.
    with store.connect() as conn:
        rows = conn.execute("""SELECT * FROM (
            SELECT *,ROW_NUMBER() OVER(PARTITION BY source_id,format,rank,period ORDER BY fetched_at DESC,id DESC) AS ordinal
            FROM snapshots WHERE format=? AND rank=? AND period=? AND patch=?
        ) WHERE ordinal=1 ORDER BY fetched_at DESC""", (format,rank,period,patch)).fetchall()
    return [with_freshness(store, {**dict(r), "payload": json.loads(r["payload"])}) for r in rows]


def provenance(snapshot: dict) -> dict:
    return {**{k: snapshot[k] for k in ("id", "source_id", "format", "rank", "period", "patch", "fetched_at", "freshness", "coverage")},
            "patch_scope": snapshot["payload"].get("patch_scope", "unverified")}


def archetypes(store: Store, **scope) -> list[dict]:
    by_name: dict[str, dict] = {}
    for snapshot in current(store, **scope):
        for row in snapshot["payload"]["archetypes"]:
            key = row["archetype"].casefold()
            item = by_name.setdefault(key, {"archetype": row["archetype"], "class": row["class"], "winrate": None, "games": None, "popularity_pct": None, "decks": 0, "cards": 0, "evidence": []})
            if row["winrate"] is not None and item["winrate"] is None:
                item.update({k: row[k] for k in ("winrate", "games", "popularity_pct")})
                item["freshness"] = snapshot["freshness"]
            item["decks"] = max(item["decks"], len(row["decks"]))
            item["cards"] = max(item["cards"], len(row["cards"]))
            item["evidence"].append(provenance(snapshot))
            item.setdefault("freshness", snapshot["freshness"])
    return sorted(by_name.values(), key=lambda r: (-(r["games"] or 0), r["archetype"]))


def winrate_interval(winrate: float | None, games: int | None) -> list[float] | None:
    """Descriptive Wilson interval; rounded source winrates are approximate."""
    if winrate is None or not games or not 0 <= winrate <= 100:
        return None
    p, n, z = winrate / 100, games, 1.96
    center = (p + z*z/(2*n)) / (1+z*z/n)
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
    return [round(max(0, center-half)*100, 2), round(min(1, center+half)*100, 2)]


def _card_names() -> dict[int, dict]:
    # Read the already-installed card index only. GET must never fetch an index.
    path = data_dir() / "hearthstonejson.cards.ruRU.json"
    if not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text())
        return {r["dbfId"]: r for r in rows if isinstance(r, dict) and r.get("dbfId")} if isinstance(rows, list) else {}
    except (OSError, ValueError):
        return {}


def detail(store: Store, archetype: str, **scope) -> dict | None:
    matching = [(s, r) for s in current(store, **scope) for r in s["payload"]["archetypes"] if r["archetype"].casefold() == archetype.casefold()]
    if not matching:
        return None
    settings = Settings.from_env()
    decks, cards, matchups, evidence = {}, {}, [], []
    names = _card_names()
    for snap, row in matching:
        proof = provenance(snap)
        evidence.append(proof)
        for deck in row["decks"]:
            previous = decks.get(deck["id"])
            if previous and (previous.get("fetched_at") or "") >= (deck.get("fetched_at") or ""):
                continue
            age = (datetime.now(UTC) - timestamp(deck["fetched_at"])).total_seconds() if deck["fetched_at"] else None
            fresh = snap["freshness"]["fresh"] and age is not None and 0 <= age <= snap["expected_hours"] * 3600
            decks[deck["id"]] = {
                **deck, "evidence": proof, "fresh": fresh,
                "sample_sufficient": bool(deck["games"] and deck["games"] >= settings.min_games),
                "winrate_interval": winrate_interval(deck["winrate"], deck["games"]),
                "cards": [{**c, "name": names.get(c["dbf_id"], {}).get("name", f"Карта #{c['dbf_id']}"), "cost": names.get(c["dbf_id"], {}).get("cost")} for c in deck["cards"]],
            }
        for card in row["cards"]:
            identity = str(card.get("dbf_id") or card.get("card_id") or card.get("card_name"))
            if identity in cards:
                continue
            counts = [card.get(f"{metric}_count") or 0 for metric in ("mulligan", "drawn", "kept")]
            cards[identity] = {**card, "id": identity, "scope": "archetype", "evidence": proof,
                               "sample_sufficient": min(counts) >= settings.min_card_observations,
                               "interpretation": "source_reported_impact_not_causal_effect"}
        if row["class_matchups"] and not matchups:
            matchups = [{**m, "level": "class", "evidence": proof} for m in row["class_matchups"]]
    return {
        "archetype": matching[0][1]["archetype"], "scope": scope,
        "decks": sorted(decks.values(), key=lambda d: (-(d["games"] or 0), d["id"])),
        "cards": sorted(cards.values(), key=lambda c: -(c.get("mulligan_impact") or 0)),
        "matchups": matchups, "evidence": evidence,
        "limitations": ["Сборки ограничены охватом источника.", "Карточные показатели относятся к архетипу, а не к каждой сборке.", "Разница винрейта не доказывает эффект замены карты."],
    }


def compare(store: Store, archetype: str, left: str, right: str, **scope) -> dict | None:
    data = detail(store, archetype, **scope)
    decks = {d["id"]: d for d in data["decks"]} if data else {}
    if left not in decks or right not in decks:
        return None
    a, b = decks[left], decks[right]
    def composition(deck):
        return {(c["dbf_id"], c["sideboard_owner"]): c for c in deck["cards"]}
    ac, bc = composition(a), composition(b)
    changes = []
    for key in sorted(ac.keys() | bc.keys()):
        ca, cb = ac.get(key, {}).get("count", 0), bc.get(key, {}).get("count", 0)
        if ca != cb:
            changes.append({**(ac.get(key) or bc[key]), "left_count": ca, "right_count": cb})
    comparable = all(d["fresh"] and d["composition_complete"] and d["sample_sufficient"] and d["winrate"] is not None for d in (a,b))
    return {"left": a, "right": b, "changes": changes, "comparable": comparable,
            "winrate_delta_pp": round(b["winrate"]-a["winrate"], 2) if a["winrate"] is not None and b["winrate"] is not None else None,
            "winner": None, "reason": "Описательное сравнение. Состав соперников и игроков может отличаться; причинный эффект замены не установлен."}


def card_catalog(store: Store, **scope) -> list[dict]:
    grouped = defaultdict(list)
    seen = set()
    for snap in current(store, **scope):
        for arch in snap["payload"]["archetypes"]:
            for card in arch["cards"]:
                identity = str(card.get("dbf_id") or card.get("card_id") or card.get("card_name"))
                key = (identity, arch["archetype"].casefold())
                if key in seen:
                    continue
                seen.add(key)
                grouped[identity].append({**card, "archetype": arch["archetype"], "evidence": provenance(snap)})
    return [{"id": key, "name": rows[0].get("card_name") or f"Карта #{key}", "archetypes": rows,
             "global_power_score": None} for key, rows in grouped.items()]


def timeline(store: Store, archetype: str, *, limit: int = 180, **scope) -> list[dict]:
    snapshots = store.snapshots(format=scope["format"], rank=scope["rank"], period=scope["period"], limit=2000)
    result = []
    for snap in snapshots:
        if snap["patch"] != scope["patch"]:
            continue
        for row in snap["payload"]["archetypes"]:
            if row["archetype"].casefold() == archetype.casefold():
                result.append({"snapshot_id": snap["id"], "source_id": snap["source_id"], "fetched_at": snap["fetched_at"],
                               "winrate": row["winrate"], "games": row["games"], "popularity_pct": row["popularity_pct"],
                               "decks": len(row["decks"]), "cards": len(row["cards"])})
    return result[:limit]
