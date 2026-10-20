"""Adapt current publications without changing their source contracts or dates."""
from copy import deepcopy

from .models import digest, timestamp


def catalog_publication(dataset: dict) -> tuple[dict, dict] | None:
    """Deck catalogs are atomic publications with status in the same document."""
    source = str(dataset.get("source_id") or "")
    if not source.startswith("hsguru_deck_catalog_") or not isinstance(dataset.get("data"), list):
        return None
    scope = dataset.get("criteria") or {}
    groups = {}
    for deck in dataset["data"]:
        if deck.get("sample_period") != scope.get("period") or deck.get("sample_rank") != scope.get("rank"):
            continue
        name = deck.get("archetype")
        row = groups.setdefault(name, {"archetype": name, "class": deck.get("class"), "decks": []})
        row["decks"].append(deck)
    payload = {**dataset, "data": {"structured": {
        "type": "meta_lab_variants", "criteria": scope, "archetypes": list(groups.values()),
        "expected_hours": 24, "deck_coverage": "source_limited",
    }}}
    status = {"state": dataset.get("state"), "fetched_at": dataset.get("fetched_at"),
              "cached": dataset.get("sample_state") != "complete" or bool(dataset.get("missing_archetypes"))}
    return payload, status


def parts(dataset: dict) -> list[tuple[str, dict, dict]] | None:
    data = dataset.get("data")
    structured = data.get("structured") if isinstance(data, dict) else None
    if not isinstance(structured, dict):
        return None
    kind = structured.get("type")
    candidates = []
    if kind == "hsguru_meta_matrix" and "slices" in structured:
        catalog = structured.get("current_catalog") or {}
        catalog_rows = catalog.get("archetypes") or []
        covered = {(r.get("format"), r.get("rank"), r.get("period")) for r in catalog_rows}
        for item in structured.get("slices") or []:
            scope = {k: item.get(k, "unknown") for k in ("format", "rank", "period")}
            if tuple(scope.values()) in covered or item.get("coin", "any_player") != "any_player":
                continue
            candidates.append(("", "meta", scope, item.get("rows", []), item.get("fetched_at"),
                               bool((item.get("quality") or {}).get("serving_cached_slice"))))
        # The catalog keeps deck-level collection dates; never re-date its builds.
        if catalog_rows:
            candidates.append(("", "meta", catalog.get("criteria") or {}, catalog_rows,
                               dataset.get("fetched_at"), False))
    elif kind == "hsguru_archetype_analysis" and structured.get("schema_version", 0) >= 2:
        for row in structured.get("archetypes") or []:
            times = [row.get("card_stats_updated_at"), row.get("matchups_updated_at")]
            try:
                at = min(times, key=timestamp) if all(times) else dataset.get("fetched_at")
            except (ValueError, TypeError):
                at = dataset.get("fetched_at")
            invalid = (not all(times) or row.get("state") != "ok"
                       or row.get("matchups_state", "complete") != "complete"
                       or row.get("card_stats_state", "complete") != "complete")
            scope = {**(structured.get("criteria") or {}),
                     **{k: row[k] for k in ("format", "rank", "period") if row.get(k)}}
            candidates.append((":"+digest([scope, row.get("archetype")])[:20], "meta_lab_analysis", scope, [row], at, invalid))
    else:
        return None
    result = []
    for suffix, part_kind, scope, rows, at, cached in candidates:
        payload = deepcopy(dataset)
        payload["fetched_at"] = at
        payload["data"]["structured"] = {
            "type": part_kind, "schema_version": 1, "criteria": scope,
            "strategies" if part_kind == "meta" else "archetypes": rows, "expected_hours": 24 if part_kind == "meta_lab_analysis" else 12,
        }
        result.append((suffix, payload, {"fetched_at": at, "cached": cached}))
    return result
