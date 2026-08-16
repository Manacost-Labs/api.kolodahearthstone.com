from __future__ import annotations

import re
from typing import Any

import httpx

from .sources import Source
from .vicious_syndicate_auth import vicious_syndicate_cookies_for_fetch

FIREBASE_BASE = "https://data-reaper.firebaseio.com"
FIREBASE_AUTH_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
VS_APP_JS = "https://www.vicioussyndicate.com/datareaperlive/scripts/app.js"
VS_PREMIUM_BUILD_JS = "https://www.vicioussyndicate.com/datareaperlive/scripts/build_premium.js"

HS_CLASSES = [
    "DeathKnight",
    "DemonHunter",
    "Druid",
    "Hunter",
    "Mage",
    "Paladin",
    "Priest",
    "Rogue",
    "Shaman",
    "Warlock",
    "Warrior",
]

RANKING_SYSTEM = {
    "legend": (0, 0),
    "diamond": (1, 10),
    "platinum": (11, 20),
    "gold": (21, 30),
    "silver": (31, 40),
    "bronze": (41, 50),
}

POWER_BRACKETS = [
    ("All ranks", 0, 19),
    ("Legend", 0, 0),
    ("Diamond 1-4", 1, 4),
    ("Diamond 5-10", 5, 10),
    ("Platinum", 11, 15),
    ("Gold Silver Bronze", 16, 19),
]

LIVE_TIME_RANGES = ("lastDay", "last3Days", "lastWeek", "last2Weeks")


class ViciousLiveWindowUnavailable(RuntimeError):
    """Firebase has no aligned ladder and matchup window with live samples."""

    failure_reason_code = "unavailable"
    upstream_state = "upstream_temporarily_empty"
    skip_browser_fallback = True

    def __init__(self, diagnostics: dict[str, Any]) -> None:
        self.upstream_readiness = diagnostics
        super().__init__("Vicious Firebase has no non-empty aligned data window")


def _numeric_total(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return max(float(value), 0.0)
    if isinstance(value, (list, tuple)):
        return sum(_numeric_total(item) for item in value)
    return 0.0


def _archetype_identities(rows: Any) -> set[tuple[str, str]]:
    if not isinstance(rows, list):
        return set()
    return {
        (str(item[0]), str(item[1]))
        for item in rows
        if isinstance(item, list) and len(item) >= 2
    }


def _select_live_window(
    ladder_payload: dict[str, Any],
    table_payload: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for time_range in LIVE_TIME_RANGES:
        ladder_interval = ladder_payload.get(time_range)
        table_interval = table_payload.get(time_range)
        ladder = ladder_interval if isinstance(ladder_interval, dict) else {}
        table_group = table_interval if isinstance(table_interval, dict) else {}
        ranks_all = table_group.get("ranks_all")
        table = ranks_all if isinstance(ranks_all, dict) else {}

        ladder_archetypes = _archetype_identities(ladder.get("archetypes"))
        table_archetypes = _archetype_identities(table.get("archetypes"))
        ladder_games = _numeric_total(ladder.get("gamesPerRank"))
        matchup_games = _numeric_total(table.get("table"))
        shared_archetypes = len(ladder_archetypes & table_archetypes)
        usable = (
            ladder_games > 0
            and matchup_games > 0
            and shared_archetypes > 0
        )
        diagnostics.append(
            {
                "time_range": time_range,
                "ladder_games": int(ladder_games),
                "ladder_archetypes": len(ladder_archetypes),
                "table_archetypes": len(table_archetypes),
                "shared_archetypes": shared_archetypes,
                "matchup_games": int(matchup_games),
                "usable": usable,
            }
        )
        if usable:
            selection = {
                "preferred_time_range": LIVE_TIME_RANGES[0],
                "selected_time_range": time_range,
                "fallback_used": time_range != LIVE_TIME_RANGES[0],
                "window_state": (
                    "preferred_ready"
                    if time_range == LIVE_TIME_RANGES[0]
                    else "fallback_ready"
                ),
                "window_diagnostics": diagnostics,
            }
            return time_range, ladder, table, selection

    selection = {
        "preferred_time_range": LIVE_TIME_RANGES[0],
        "selected_time_range": None,
        "fallback_used": False,
        "window_state": "upstream_temporarily_empty",
        "window_diagnostics": diagnostics,
    }
    raise ViciousLiveWindowUnavailable(selection)


def _pct(value: float | int | None) -> str | None:
    if value is None:
        return None
    return f"{float(value) * 100:.2f}%"


def _sum_arrays(rows: list[list[float]]) -> list[float]:
    if not rows:
        return []
    return [sum(values) for values in zip(*rows)]


def _breakdown(rows: list[Any]) -> dict[str, list[Any]]:
    return {key: rows[start : end + 1] for key, (start, end) in RANKING_SYSTEM.items()}


def restructure_games_per_rank(rows: list[int | float]) -> list[int | float]:
    if len(rows) == 20:
        return rows
    parts = _breakdown(rows)
    platinum = [sum(parts["platinum"][idx : idx + 2]) for idx in range(0, 10, 2)]
    gold = [sum(parts["gold"][:5]), sum(parts["gold"][5:10])]
    return (
        parts["legend"]
        + parts["diamond"]
        + platinum
        + gold
        + [sum(parts["silver"])]
        + [sum(parts["bronze"])]
    )


def restructure_rank_matrix(rows: list[list[float]]) -> list[list[float]]:
    if len(rows) == 20:
        return rows
    parts = _breakdown(rows)
    platinum = [_sum_arrays(parts["platinum"][idx : idx + 2]) for idx in range(0, 10, 2)]
    gold = [_sum_arrays(parts["gold"][:5]), _sum_arrays(parts["gold"][5:10])]
    return (
        parts["legend"]
        + parts["diamond"]
        + platinum
        + gold
        + [_sum_arrays(parts["silver"])]
        + [_sum_arrays(parts["bronze"])]
    )


def smooth_ladder(data: list[list[float]], sums: list[int | float]) -> list[list[float]]:
    hs_ranks = 20
    sums = [float(value or 0) for value in sums[:hs_ranks]]
    data_new = [data[0][:]]
    if sums[0] == 0:
        sums[0] = 1
    if sums[1] == 0:
        sums[1] = 1

    w_rank = 3.5
    for rank in range(1, hs_ranks - 1):
        if sums[rank + 1] == 0:
            sums[rank + 1] = 1
        w_upper = min(sums[rank - 1] / sums[rank], 2 * w_rank)
        w_lower = min(sums[rank + 1] / sums[rank], 2 * w_rank)
        if rank % 5 == 0:
            w_lower = 0
        if rank % 5 == 1:
            w_upper = 0
        w_total = w_rank + w_lower + w_upper
        row = []
        for index in range(len(data[rank])):
            value = data[rank][index] / sums[rank]
            lower = data[rank + 1][index] / sums[rank + 1]
            upper = data[rank - 1][index] / sums[rank - 1]
            row.append((value * w_rank + lower * w_lower + upper * w_upper) / w_total)
        data_new.append(row)

    data_new.append(data[hs_ranks - 1][:])
    data_new[0] = [value / sums[0] for value in data_new[0]]
    data_new[hs_ranks - 1] = [value / sums[hs_ranks - 1] for value in data_new[hs_ranks - 1]]
    return data_new


def _vs_name(archetype: list[str]) -> str:
    return f"{archetype[1]} {archetype[0].replace('§', '')}"


def live_upstream_availability(*archetype_lists: list[list[str]]) -> dict[str, Any]:
    unique = {
        (str(item[0]), str(item[1]))
        for rows in archetype_lists
        for item in rows
        if isinstance(item, list) and len(item) >= 2
    }
    placeholders = {
        item for item in unique if item[1].strip().lower() in {"other", "unknown"}
    }
    named = unique - placeholders
    ready = len(named) >= 3
    return {
        "state": "ready" if ready else "upstream_unclassified",
        "ready": ready,
        "total_archetypes": len(unique),
        "named_archetypes": len(named),
        "placeholder_archetypes": len(placeholders),
        "reason": (
            None
            if ready
            else "Vicious Firebase has not classified post-expansion archetypes yet"
        ),
    }


def _without_placeholder_decks(
    deck_distribution: list[dict[str, Any]],
    tier_list: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    placeholder = re.compile(r"^(?:Other|Unknown)\s+\S+$", re.IGNORECASE)
    decks = [row for row in deck_distribution if not placeholder.fullmatch(str(row.get("deck") or ""))]
    tiers = [
        {
            **bracket,
            "decks": [
                row
                for row in (bracket.get("decks") or [])
                if not placeholder.fullmatch(str(row.get("deck") or ""))
            ],
        }
        for bracket in tier_list
    ]
    return decks, tiers


def _extract(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text)
    if not match:
        raise RuntimeError(f"Could not extract {label} from VS Live script")
    return match.group(1)


async def _fetch_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    cookies: dict[str, str] | None = None,
) -> str:
    response = await client.get(
        url,
        headers={"user-agent": "Mozilla/5.0"},
        cookies=cookies,
    )
    response.raise_for_status()
    return response.text


async def _firebase_token(client: httpx.AsyncClient) -> str:
    cookies = vicious_syndicate_cookies_for_fetch()
    app_js, build_js = await _fetch_text(
        client, VS_APP_JS, cookies=cookies
    ), await _fetch_text(client, VS_PREMIUM_BUILD_JS, cookies=cookies)
    api_key = _extract(r'apiKey:\s*"([^"]+)"', app_js, "Firebase apiKey")
    email = _extract(r"email:\s*'([^']+)'", build_js, "Firebase email")
    password = _extract(r"pw:\s*'([^']+)'", build_js, "Firebase password")
    response = await client.post(
        FIREBASE_AUTH_URL,
        params={"key": api_key},
        json={"email": email, "password": password, "returnSecureToken": True},
    )
    if response.status_code >= 400:
        raise RuntimeError(f"VS Live Firebase auth failed: HTTP {response.status_code}")
    return str(response.json()["idToken"])


async def _firebase_json(client: httpx.AsyncClient, path: str, token: str) -> dict[str, Any]:
    response = await client.get(f"{FIREBASE_BASE}/{path}.json", params={"auth": token})
    if response.status_code >= 400:
        raise RuntimeError(f"VS Live Firebase request failed for {path}: HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected Firebase payload for {path}")
    return payload


def build_ladder_view(ladder_interval: dict[str, Any]) -> dict[str, Any]:
    archetypes = ladder_interval["archetypes"]
    rank_sums = restructure_games_per_rank(ladder_interval["gamesPerRank"])
    rank_data = smooth_ladder(restructure_rank_matrix(ladder_interval["rankData"]), rank_sums)
    class_rank_data = smooth_ladder(restructure_rank_matrix(ladder_interval["classRankData"]), rank_sums)

    class_values = [sum(class_rank_data[rank][idx] for rank in range(20)) for idx in range(len(HS_CLASSES))]
    class_total = sum(class_values) or 1.0
    class_distribution = [
        {"class": class_name, "frequency": _pct(value / class_total)}
        for class_name, value in sorted(
            zip(HS_CLASSES, class_values, strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
    ]

    deck_values = [sum(rank_data[rank][idx] for rank in range(20)) for idx in range(len(archetypes))]
    for idx, (hs_class, _archetype) in enumerate(archetypes):
        if deck_values[idx] < 0.03 and idx > 8:
            class_idx = HS_CLASSES.index(hs_class)
            deck_values[class_idx] += deck_values[idx]
            deck_values[idx] = 0
    deck_total = sum(deck_values) or 1.0
    deck_distribution = [
        {
            "deck": _vs_name(archetypes[idx]),
            "class": archetypes[idx][0],
            "frequency": _pct(value / deck_total),
        }
        for idx, value in sorted(
            enumerate(deck_values),
            key=lambda item: item[1],
            reverse=True,
        )
        if value > 0
    ]

    ladder_archetypes = [
        {
            "name": _vs_name(archetypes[idx]),
            "class": archetypes[idx][0],
            "fr_ranks": [rank_data[rank][idx] for rank in range(20)],
        }
        for idx in range(len(archetypes))
    ]
    return {
        "games": int(sum(rank_sums)),
        "class_distribution": class_distribution,
        "deck_distribution": deck_distribution,
        "ladder_archetypes": ladder_archetypes,
    }


def build_table_view(table_payload: dict[str, Any], *, num_arch: int = 16) -> dict[str, Any]:
    frequencies = list(table_payload["frequency"])
    raw_table = table_payload["table"]
    raw_archetypes = table_payload["archetypes"]
    indexes = sorted(range(len(frequencies)), key=lambda idx: frequencies[idx], reverse=True)[:num_arch]

    names: list[str] = []
    selected_frequencies: list[float] = []
    table: list[list[float]] = []
    for row_pos, raw_row_idx in enumerate(indexes):
        names.append(_vs_name(raw_archetypes[raw_row_idx]))
        selected_frequencies.append(float(frequencies[raw_row_idx]))
        row = []
        for col_pos, raw_col_idx in enumerate(indexes):
            w1, l1 = raw_table[raw_row_idx][raw_col_idx][0], raw_table[raw_row_idx][raw_col_idx][1]
            w2, l2 = raw_table[raw_col_idx][raw_row_idx][1], raw_table[raw_col_idx][raw_row_idx][0]
            wr1 = w1 / (w1 + l1) if w1 + l1 > 0 else 0
            wr2 = w2 / (w2 + l2) if w2 + l2 > 0 else 0
            total_games = w1 + w2 + l1 + l2
            if row_pos == col_pos:
                winrate = 0.5
            elif total_games < 50:
                winrate = 0.5
            elif w1 + l1 > 0 and w2 + l2 > 0:
                winrate = (wr1 + wr2) / 2
            elif w1 + l1 == 0:
                winrate = wr2
            else:
                winrate = wr1
            row.append(winrate)
        table.append(row)

    return {"archetypes": names, "frequency": selected_frequencies, "table": table}


def build_power_tier_list(
    ladder_view: dict[str, Any],
    table_view: dict[str, Any],
    *,
    limit: int = 16,
) -> list[dict[str, Any]]:
    ladder_archetypes = ladder_view["ladder_archetypes"]
    table_archetypes = table_view["archetypes"]
    matchup_table = table_view["table"]
    rows: list[dict[str, Any]] = []

    for bracket_name, start, end in POWER_BRACKETS:
        entries = []
        for archetype in ladder_archetypes:
            if archetype["name"] not in table_archetypes:
                continue
            archetype_idx = table_archetypes.index(archetype["name"])
            total_wr = 0.0
            count = 0
            for rank in range(start, end + 1):
                total_frequency = 0.0
                weighted_wr = 0.0
                for opponent in ladder_archetypes:
                    if opponent["name"] not in table_archetypes:
                        continue
                    opponent_idx = table_archetypes.index(opponent["name"])
                    frequency = opponent["fr_ranks"][rank]
                    total_frequency += frequency
                    weighted_wr += frequency * matchup_table[archetype_idx][opponent_idx]
                winrate = weighted_wr / total_frequency if total_frequency > 0 else 0
                total_wr += winrate
                if winrate > 0:
                    count += 1
            if count:
                entries.append({"deck": archetype["name"], "winrate": total_wr / count})
        entries.sort(key=lambda item: item["winrate"], reverse=True)
        rows.append(
            {
                "rank_bracket": bracket_name,
                "decks": [
                    {"rank": idx + 1, "deck": item["deck"], "winrate": _pct(item["winrate"])}
                    for idx, item in enumerate(entries[:limit])
                ],
            }
        )
    return rows


async def fetch_vicious_live(source: Source) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        token = await _firebase_token(client)
        ladder_payload = await _firebase_json(client, "premiumData/ladderData/Standard", token)
        table_payload = await _firebase_json(client, "premiumData/tableData/Standard", token)

    time_range, ladder_interval, table_interval, window_selection = (
        _select_live_window(ladder_payload, table_payload)
    )
    ladder_view = build_ladder_view(ladder_interval)
    table_view = build_table_view(table_interval)
    tier_list = build_power_tier_list(ladder_view, table_view)
    availability = live_upstream_availability(
        ladder_interval.get("archetypes") or [],
        table_interval.get("archetypes") or [],
    )
    availability.update(window_selection)
    deck_distribution = ladder_view["deck_distribution"]
    if not availability["ready"]:
        deck_distribution, tier_list = _without_placeholder_decks(
            deck_distribution,
            tier_list,
        )
    return {
        "type": "vicious_live",
        "format": "Standard",
        "pie_time_range": time_range,
        "tier_ladder_time_range": time_range,
        "tier_matchup_time_range": time_range,
        "games": ladder_view["games"],
        "class_distribution": ladder_view["class_distribution"],
        "deck_distribution": deck_distribution,
        "tier_list": tier_list,
        "upstream_state": availability["state"],
        "upstream_availability": availability,
        "source": {
            "url": source.url,
            "backend": "vicious_live_firebase",
            "upstream_ready": availability["ready"],
            "firebase_paths": [
                "premiumData/ladderData/Standard",
                "premiumData/tableData/Standard",
            ],
        },
    }
