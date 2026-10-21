"""Parse HSReplay Battlegrounds minion analytics and reproduce the site's math.

The four HSReplay analytics queries each return every minion at once.  The
formulas below mirror the minion page bundle on hsreplay.net (checked on
2026-10-21 against dbf 104551): header metrics weight each combat round by
``distinct_games / pct_players_remaining[round]`` and the board-position chart
sums every position row of the selected composition.  Raw counters are kept
next to every derived value so a client can apply its own thresholds.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

VARIANTS = ("normal", "golden")
BOARDS = ("normal", "taunt")
BOARD_POSITIONS = 7
MAX_COMBAT_ROUND = 16
ALL_COMPOSITIONS = 0

# HSReplay hides a value below these sample sizes; we return null the same way.
MIN_SUMMARY_SAMPLE = 30
MIN_ROUND_GAMES = 30
MIN_ROUND_COMBATS = 30
MIN_PURCHASE_OFFERS = 30  # the site requires strictly more offers than this
MIN_POSITION_OCCURRENCES = 200

METHODOLOGY: dict[str, Any] = {
    "formula_source": "hsreplay.net minion page bundle, verified 2026-10-21 on dbf 104551",
    "round_weight": "distinct_games / pct_players_remaining[combat_round]",
    "impact": (
        "sum over rounds with the minion of (avg_place_without - avg_place_with) "
        "* games_with / pct_players_remaining, divided by the sum of "
        "games_with / pct_players_remaining; null below 30"
    ),
    "combat_winrate": "100 * wins / (wins + losses); null below 30 combats",
    "popularity": (
        "sum of at_least_one * round_weight divided by the sum of round_weight; "
        "null below 30"
    ),
    "round_impact": "avg_place_without - avg_place_with; null below 30 games with and without",
    "round_combat_winrate": "100 * wins / (wins + losses); null below 30 combats",
    "round_popularity": "at_least_one; null below 30 games with and without",
    "purchase_rate": "100 * times_picked_k_owned / times_offered_k_owned; null unless offers > 30",
    "position_popularity": (
        "100 * occurrences at the position / occurrences of every selected row; "
        "composition 0 sums every row of the minion, otherwise rows of that composition"
    ),
    "position_impact": (
        "average placement at the other positions minus average placement at this "
        "position; null below 200 occurrences"
    ),
    "medians": "median_attack and median_health exactly as HSReplay returns them",
}


class MinionStatsPayloadError(ValueError):
    """An upstream payload does not match the analytics contract."""


def _rows(payload: Mapping[str, Any], query: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise MinionStatsPayloadError(f"{query}: payload must be an object")
    series = payload.get("series")
    data = series.get("data") if isinstance(series, Mapping) else None
    if not isinstance(data, list):
        raise MinionStatsPayloadError(f"{query}: series.data must be a list")
    if any(not isinstance(row, dict) for row in data):
        raise MinionStatsPayloadError(f"{query}: series.data rows must be objects")
    return data


def payload_as_of(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("as_of") if isinstance(payload, Mapping) else None
    return str(value) if value else None


def _int(row: Mapping[str, Any], field: str, query: str, *, nullable: bool = False) -> int | None:
    value = row.get(field)
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MinionStatsPayloadError(f"{query}: {field} must be a non-negative integer")
    return value


def _number(row: Mapping[str, Any], field: str, query: str, *, nullable: bool = True) -> float | None:
    value = row.get(field)
    if value is None and nullable:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise MinionStatsPayloadError(f"{query}: {field} must be a finite non-negative number")
    return float(value)


def _dbf(row: Mapping[str, Any], field: str, query: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MinionStatsPayloadError(f"{query}: {field} must be a positive integer")
    return value


def _round(value: Any, field: str, query: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_COMBAT_ROUND:
        raise MinionStatsPayloadError(f"{query}: {field} must be 1..{MAX_COMBAT_ROUND}")
    return value


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


# --- battlegrounds_minion_list ------------------------------------------------


def parse_pct_players_remaining(payload: Mapping[str, Any]) -> dict[int, float]:
    metadata = (payload.get("series") or {}).get("metadata")
    raw = metadata.get("pct_players_remaining") if isinstance(metadata, Mapping) else None
    if not isinstance(raw, Mapping):
        raise MinionStatsPayloadError("minion_list: metadata.pct_players_remaining is missing")
    out: dict[int, float] = {}
    for key, value in raw.items():
        try:
            combat_round = int(key)
        except (TypeError, ValueError) as exc:
            raise MinionStatsPayloadError("minion_list: pct_players_remaining keys must be rounds") from exc
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 < float(value) <= 1
        ):
            raise MinionStatsPayloadError("minion_list: pct_players_remaining must be in (0, 1]")
        out[_round(combat_round, "pct_players_remaining", "minion_list")] = float(value)
    return out


def _parse_aggregate(item: Any, query: str) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise MinionStatsPayloadError(f"{query}: aggregates must be objects")
    games_with = _int(item, "count_of_games_with_minion", query)
    games_without = _int(item, "count_of_games_without_minion", query)
    sum_with = _int(item, "sum_of_placements_for_players_with_minion", query)
    sum_without = _int(item, "sum_of_placements_for_players_without_minion", query)
    for count, total, field in (
        (games_with, sum_with, "with"),
        (games_without, sum_without, "without"),
    ):
        if not count <= total <= count * 8:
            raise MinionStatsPayloadError(
                f"{query}: placements {field} the minion must be 1..8 for every counted game"
            )
    wins = _int(item, "total_wins", query, nullable=True)
    losses = _int(item, "total_losses", query, nullable=True)
    if (wins is None) != (losses is None):
        raise MinionStatsPayloadError(f"{query}: total_wins and total_losses must be paired")
    at_least_one = _number(item, "at_least_one", query)
    if at_least_one is not None and at_least_one > 100:
        raise MinionStatsPayloadError(f"{query}: at_least_one must be a percentage")
    return {
        "combat_round": _round(item.get("combat_round"), "combat_round", query),
        "distinct_games": _int(item, "distinct_games", query, nullable=True),
        "games_with_minion": games_with,
        "games_without_minion": games_without,
        "sum_placement_with": sum_with,
        "sum_placement_without": sum_without,
        "wins": wins,
        "losses": losses,
        "at_least_one": at_least_one,
    }


def parse_minion_list(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return one validated row per minion with ``normal`` and ``golden`` rounds."""

    query = "minion_list"
    minions: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in _rows(payload, query):
        dbf_id = _dbf(row, "minion_dbf_id", query)
        if dbf_id in seen:
            raise MinionStatsPayloadError(f"{query}: duplicate minion_dbf_id {dbf_id}")
        seen.add(dbf_id)
        tier = row.get("minion_tier")
        if tier is not None and (isinstance(tier, bool) or not isinstance(tier, int) or not 1 <= tier <= 7):
            raise MinionStatsPayloadError(f"{query}: minion_tier must be 1..7")
        compositions = row.get("composition_ids") or []
        if not isinstance(compositions, list) or any(
            isinstance(value, bool) or not isinstance(value, int) for value in compositions
        ):
            raise MinionStatsPayloadError(f"{query}: composition_ids must be integers")
        variants: dict[str, list[dict[str, Any]]] = {}
        for variant, field in (("normal", "normal_aggregates"), ("golden", "premium_aggregates")):
            aggregates = row.get(field) or []
            if not isinstance(aggregates, list):
                raise MinionStatsPayloadError(f"{query}: {field} must be a list")
            parsed = [_parse_aggregate(item, query) for item in aggregates]
            rounds = [item["combat_round"] for item in parsed]
            if len(set(rounds)) != len(rounds):
                raise MinionStatsPayloadError(f"{query}: duplicate combat_round in {field}")
            variants[variant] = sorted(parsed, key=lambda item: item["combat_round"])
        minions.append(
            {
                "dbf_id": dbf_id,
                "tavern_tier": tier,
                "composition_ids": sorted(set(compositions)),
                "rounds": variants,
            }
        )
    return minions


def round_metrics(aggregate: Mapping[str, Any]) -> dict[str, Any]:
    """Derived per-round values without the site's display thresholds."""

    games_with = aggregate["games_with_minion"]
    games_without = aggregate["games_without_minion"]
    avg_with = _ratio(aggregate["sum_placement_with"], games_with)
    avg_without = _ratio(aggregate["sum_placement_without"], games_without)
    wins, losses = aggregate.get("wins"), aggregate.get("losses")
    combats = (wins or 0) + (losses or 0)
    return {
        "avg_placement_with": avg_with,
        "avg_placement_without": avg_without,
        "impact": avg_without - avg_with if avg_with is not None and avg_without is not None else None,
        "combat_winrate": 100 * wins / combats if wins is not None and combats else None,
    }


def summarize_variant(
    aggregates: Iterable[Mapping[str, Any]],
    pct_players_remaining: Mapping[int, float],
    *,
    first_round: int = 1,
    last_round: int = MAX_COMBAT_ROUND,
) -> dict[str, Any]:
    """HSReplay header metrics for one minion variant (rounds 1-16 by default)."""

    rows = [row for row in aggregates if first_round <= row["combat_round"] <= last_round]
    for row in rows:
        if row["combat_round"] not in pct_players_remaining:
            raise MinionStatsPayloadError(
                f"minion_list: pct_players_remaining lacks round {row['combat_round']}"
            )
    remaining = {row["combat_round"]: pct_players_remaining[row["combat_round"]] for row in rows}
    weight = sum((row["distinct_games"] or 0) / remaining[row["combat_round"]] for row in rows)
    popularity = (
        sum(
            (row["at_least_one"] or 0) * (row["distinct_games"] or 0) / remaining[row["combat_round"]]
            for row in rows
        )
        / weight
        if weight >= MIN_SUMMARY_SAMPLE
        else None
    )
    wins = sum(row["wins"] or 0 for row in rows)
    losses = sum(row["losses"] or 0 for row in rows)
    combats = wins + losses
    combat_winrate = 100 * wins / combats if combats >= MIN_SUMMARY_SAMPLE else None
    played = [row for row in rows if row["games_with_minion"]]
    impact_weight = sum(row["games_with_minion"] / remaining[row["combat_round"]] for row in played)
    impact: float | None = None
    if impact_weight >= MIN_SUMMARY_SAMPLE:
        round_impacts = [round_metrics(row)["impact"] for row in played]
        if all(value is not None for value in round_impacts):
            impact = (
                sum(
                    value * row["games_with_minion"] / remaining[row["combat_round"]]
                    for value, row in zip(round_impacts, played, strict=True)
                )
                / impact_weight
            )
    games_with = sum(row["games_with_minion"] for row in rows)
    games_without = sum(row["games_without_minion"] for row in rows)
    return {
        "impact": impact,
        "combat_winrate": combat_winrate,
        "popularity": popularity,
        "games_with_minion": games_with,
        "games_without_minion": games_without,
        "avg_placement_with": _ratio(sum(row["sum_placement_with"] for row in rows), games_with),
        "avg_placement_without": _ratio(
            sum(row["sum_placement_without"] for row in rows), games_without
        ),
        "wins": wins,
        "losses": losses,
    }


# --- battlegrounds_minion_position --------------------------------------------


def _position_cells(board: Any, query: str) -> tuple[int, int, list[int], list[int]] | None:
    if board is None:
        return None
    if not isinstance(board, Mapping):
        raise MinionStatsPayloadError(f"{query}: board must be an object")
    occurrences = _int(board, "occurrences", query, nullable=True) or 0
    if not occurrences:
        # The site drops rows without occurrences before summing positions.
        return None
    sum_placement = _int(board, "sum_placement", query, nullable=True) or 0
    positions = board.get("positions") or []
    if not isinstance(positions, list) or len(positions) > BOARD_POSITIONS:
        raise MinionStatsPayloadError(f"{query}: positions must hold at most 7 cells")
    position_occurrences = [0] * BOARD_POSITIONS
    position_sums = [0] * BOARD_POSITIONS
    for index, cell in enumerate(positions):
        if cell is None:
            continue
        if not isinstance(cell, Mapping):
            raise MinionStatsPayloadError(f"{query}: position cells must be objects")
        position_occurrences[index] = _int(cell, "occurrences", query, nullable=True) or 0
        position_sums[index] = _int(cell, "sum_placement", query, nullable=True) or 0
    return occurrences, sum_placement, position_occurrences, position_sums


def aggregate_positions(
    payload: Mapping[str, Any], minion_dbf_ids: set[int]
) -> list[dict[str, Any]]:
    """Sum position rows per (minion, composition, board) the way the site does.

    Composition ``0`` is the site's "all compositions" view and sums every row
    of the minion, including HSReplay's ``-1`` (all) rows.  A positive id sums
    the rows of that composition across every opponent composition.
    """

    query = "minion_position"
    totals: dict[tuple[int, int, str], list[Any]] = {}
    seen: set[tuple[int, int, int]] = set()
    for row in _rows(payload, query):
        dbf_id = _dbf(row, "minion_dbf_id", query)
        composition = row.get("composition_id")
        opponent = row.get("opponent_composition_id")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (composition, opponent)):
            raise MinionStatsPayloadError(f"{query}: composition ids must be integers")
        key = (dbf_id, composition, opponent)
        if key in seen:
            raise MinionStatsPayloadError(f"{query}: duplicate position row")
        seen.add(key)
        if dbf_id not in minion_dbf_ids:
            continue
        targets = [ALL_COMPOSITIONS] + ([composition] if composition > 0 else [])
        for board in BOARDS:
            cells = _position_cells(row.get(board), query)
            if cells is None:
                continue
            occurrences, sum_placement, position_occurrences, position_sums = cells
            for target in targets:
                bucket = totals.setdefault(
                    (dbf_id, target, board),
                    [0, 0, [0] * BOARD_POSITIONS, [0] * BOARD_POSITIONS],
                )
                bucket[0] += occurrences
                bucket[1] += sum_placement
                for index in range(BOARD_POSITIONS):
                    bucket[2][index] += position_occurrences[index]
                    bucket[3][index] += position_sums[index]
    return [
        {
            "dbf_id": dbf_id,
            "composition_id": composition,
            "board": board,
            "occurrences": values[0],
            "sum_placement": values[1],
            "position_occurrences": values[2],
            "position_sum_placement": values[3],
        }
        for (dbf_id, composition, board), values in sorted(totals.items())
    ]


def position_chart(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    occurrences = list(row["position_occurrences"])
    sums = list(row["position_sum_placement"])
    total = row["occurrences"]
    all_occurrences = sum(occurrences)
    all_sums = sum(sums)
    out = []
    for index in range(BOARD_POSITIONS):
        count = occurrences[index]
        impact = None
        other_occurrences = all_occurrences - count
        if count >= MIN_POSITION_OCCURRENCES and other_occurrences:
            impact = (all_sums - sums[index]) / other_occurrences - sums[index] / count
        out.append(
            {
                "position": index + 1,
                "popularity": 100 * count / total if total else None,
                "impact": impact,
                "occurrences": count,
                "sum_placement": sums[index],
            }
        )
    return out


# --- battlegrounds_purchase_rates_by_turn -------------------------------------

PURCHASE_COUNTERS = tuple(
    f"times_{kind}_{owned}_owned"
    for kind in ("picked", "offered", "discover_picked", "discover_offered")
    for owned in range(3)
) + ("total_times_offered", "total_times_discover_offered")


def parse_purchases(payload: Mapping[str, Any], minion_dbf_ids: set[int]) -> list[dict[str, Any]]:
    query = "purchase_rates_by_turn"
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for row in _rows(payload, query):
        dbf_id = _dbf(row, "minion_dbf_id", query)
        recruitment_round = row.get("recruitment_round")
        if isinstance(recruitment_round, bool) or not isinstance(recruitment_round, int) or recruitment_round < 1:
            raise MinionStatsPayloadError(f"{query}: recruitment_round must be a positive integer")
        key = (dbf_id, recruitment_round)
        if key in seen:
            raise MinionStatsPayloadError(f"{query}: duplicate purchase row")
        seen.add(key)
        counters = {field: _int(row, field, query, nullable=True) or 0 for field in PURCHASE_COUNTERS}
        for prefix in ("", "discover_"):
            for owned in range(3):
                if counters[f"times_{prefix}picked_{owned}_owned"] > counters[f"times_{prefix}offered_{owned}_owned"]:
                    raise MinionStatsPayloadError(f"{query}: picks exceed offers")
        if dbf_id in minion_dbf_ids and recruitment_round <= MAX_COMBAT_ROUND:
            out.append({"dbf_id": dbf_id, "recruitment_round": recruitment_round, **counters})
    return sorted(out, key=lambda item: (item["dbf_id"], item["recruitment_round"]))


def purchase_rate(picked: int, offered: int) -> float | None:
    return 100 * picked / offered if offered > MIN_PURCHASE_OFFERS else None


# --- battlegrounds_minion_stats -----------------------------------------------


def parse_medians(payload: Mapping[str, Any], minion_dbf_ids: set[int]) -> list[dict[str, Any]]:
    query = "minion_stats"
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    for row in _rows(payload, query):
        dbf_id = _dbf(row, "normal_dbf_id", query)
        premium = row.get("is_premium")
        if premium not in (0, 1) or isinstance(premium, bool):
            raise MinionStatsPayloadError(f"{query}: is_premium must be 0 or 1")
        combat_round = _round(row.get("combat_round"), "combat_round", query)
        key = (dbf_id, premium, combat_round)
        if key in seen:
            raise MinionStatsPayloadError(f"{query}: duplicate median row")
        seen.add(key)
        if dbf_id not in minion_dbf_ids:
            continue
        out.append(
            {
                "dbf_id": dbf_id,
                "variant": "golden" if premium else "normal",
                "combat_round": combat_round,
                "median_attack": _number(row, "median_attack", query),
                "median_health": _number(row, "median_health", query),
            }
        )
    return sorted(out, key=lambda item: (item["dbf_id"], item["variant"], item["combat_round"]))


# --- response assembly --------------------------------------------------------


def _clean(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def public_summary(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "impact": _clean(row.get("impact"), 4),
        "combat_winrate": _clean(row.get("combat_winrate"), 3),
        "popularity": _clean(row.get("popularity"), 3),
        "games_with_minion": row.get("games_with_minion"),
        "games_without_minion": row.get("games_without_minion"),
        "avg_placement_with": _clean(row.get("avg_placement_with"), 4),
        "avg_placement_without": _clean(row.get("avg_placement_without"), 4),
        "wins": row.get("wins"),
        "losses": row.get("losses"),
    }


def public_round(row: Mapping[str, Any]) -> dict[str, Any]:
    games_with = row["games_with_minion"]
    games_without = row["games_without_minion"]
    combats = (row.get("wins") or 0) + (row.get("losses") or 0)
    enough_games = games_with >= MIN_ROUND_GAMES and games_without >= MIN_ROUND_GAMES
    return {
        "impact": _clean(row.get("impact"), 4) if enough_games else None,
        "combat_winrate": _clean(row.get("combat_winrate"), 3) if combats >= MIN_ROUND_COMBATS else None,
        "popularity": (
            _clean(row.get("at_least_one"), 3) if enough_games and row.get("at_least_one") else None
        ),
        "games_with_minion": games_with,
        "games_without_minion": games_without,
        "distinct_games": row.get("distinct_games"),
        "wins": row.get("wins"),
        "losses": row.get("losses"),
        "at_least_one": row.get("at_least_one"),
    }


def public_purchase(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "recruitment_round": row["recruitment_round"],
        "first_copy": _clean(purchase_rate(row["times_picked_0_owned"], row["times_offered_0_owned"]), 3),
        "second_copy": _clean(purchase_rate(row["times_picked_1_owned"], row["times_offered_1_owned"]), 3),
        "third_copy": _clean(purchase_rate(row["times_picked_2_owned"], row["times_offered_2_owned"]), 3),
        "discover": {
            "first_copy": _clean(
                purchase_rate(row["times_discover_picked_0_owned"], row["times_discover_offered_0_owned"]), 3
            ),
            "second_copy": _clean(
                purchase_rate(row["times_discover_picked_1_owned"], row["times_discover_offered_1_owned"]), 3
            ),
            "third_copy": _clean(
                purchase_rate(row["times_discover_picked_2_owned"], row["times_discover_offered_2_owned"]), 3
            ),
        },
        "counts": {field: row[field] for field in PURCHASE_COUNTERS},
    }


def public_positions(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in rows:
        out[row["board"]] = {
            "occurrences": row["occurrences"],
            "positions": [
                {**cell, "popularity": _clean(cell["popularity"], 3), "impact": _clean(cell["impact"], 4)}
                for cell in position_chart(row)
            ],
        }
    return out


def build_minion_stats(
    *,
    card: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    summaries: Iterable[Mapping[str, Any]],
    rounds: Iterable[Mapping[str, Any]],
    positions: Iterable[Mapping[str, Any]],
    position_compositions: Iterable[int],
    purchases: Iterable[Mapping[str, Any]],
    medians: Iterable[Mapping[str, Any]],
    composition_id: int = ALL_COMPOSITIONS,
) -> dict[str, Any]:
    """Assemble one minion's page payload from stored rows (SQLite or PostgreSQL)."""

    by_variant = {row["variant"]: row for row in summaries}
    tavern_tier = next(
        (row.get("tavern_tier") for row in by_variant.values() if row.get("tavern_tier") is not None),
        card.get("tavern_tier"),
    )
    round_cells: dict[int, dict[str, Any]] = {
        number: {"combat_round": number, "normal": None, "golden": None}
        for number in range(1, MAX_COMBAT_ROUND + 1)
    }
    for row in rounds:
        round_cells[row["combat_round"]][row["variant"]] = public_round(row)
    median_cells: dict[int, dict[str, Any]] = {}
    for row in medians:
        cell = median_cells.setdefault(
            row["combat_round"], {"combat_round": row["combat_round"], "normal": None, "golden": None}
        )
        cell[row["variant"]] = {"attack": row["median_attack"], "health": row["median_health"]}
    names = snapshot.get("composition_names") or {}
    compositions = sorted({int(value) for value in position_compositions if int(value) > 0})
    mechanics = {str(value).upper() for value in card.get("mechanics") or []}
    has_positions = bool(snapshot.get("has_positions"))
    return {
        "minion": {**card, "tavern_tier": tavern_tier},
        "slice": {
            "snapshot_id": snapshot["id"],
            "mmr_percentile": snapshot["mmr_percentile"],
            "time_range": snapshot["time_range"],
            "fetched_at": snapshot["fetched_at"],
            "as_of": {
                "minion_list": snapshot.get("list_as_of"),
                "positions": snapshot.get("positions_as_of"),
                "purchases": snapshot.get("purchases_as_of"),
                "medians": snapshot.get("medians_as_of"),
            },
        },
        "normal": public_summary(by_variant.get("normal")),
        "golden": public_summary(by_variant.get("golden")),
        "rounds": [round_cells[number] for number in sorted(round_cells)],
        "positions": (
            {
                "composition_id": composition_id,
                "default_board": "taunt" if "TAUNT" in mechanics else "normal",
                **public_positions(positions),
                "compositions": [
                    {"composition_id": value, "name": names.get(str(value))} for value in compositions
                ],
            }
            if has_positions
            else None
        ),
        "purchases": [public_purchase(row) for row in sorted(purchases, key=lambda r: r["recruitment_round"])],
        "medians": [median_cells[number] for number in sorted(median_cells)],
        "methodology": METHODOLOGY,
    }
