"""History of Blizzard balance changes and the patterns behind them.

* Card changes are exact: consecutive HearthstoneJSON builds are compared
  field by field (cost, attack, health, durability, armor, text) for every
  collectible card, and each build is attributed to the patch with that build
  number (the build is the last part of the patch version, e.g. 36.6.3.253932).
* Patches, dates, titles and the Russian developer commentary come from the
  ``hearthstone_patches`` table behind ``/api/patches`` (wiki.gg, official
  notes and hs-manacost.ru) — the same source the HearthPulse patches page uses.
* Statistics before and after a change come from the HSReplay archetype
  tables (Standard Legend daily winrate/popularity series and archetype deck
  lists). Changes older than those tables keep ``preStats: null``.
* Claude labels the changes a field diff cannot (text reworks, mixed stat
  changes), summarizes each patch and writes the pattern narrative. Counts,
  thresholds and timing are computed here.

The dataset lives in ``<data>/balance-radar-history.json``; ``ensure_history``
rebuilds it when a new patch appears or it is a week old.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..storage import root_dir
from .analysis import AnalysisError, call_structured

logger = logging.getLogger(__name__)

HSJSON_INDEX_URL = "https://api.hearthstonejson.com/v1/"
HSJSON_CARDS_URL = (
    "https://api.hearthstonejson.com/v1/{build}/enUS/cards.collectible.json"
)
HISTORY_FILE = "balance-radar-history.json"
# 2: metaBeforeAll and calibration tables.
SCHEMA_VERSION = 2
MAX_AGE = timedelta(days=7)
NUMERIC_FIELDS = ("cost", "attack", "health", "durability", "armor")
TEXT_FIELD = "text"
CORE_SHARE = 0.5
STATS_WINDOW_DAYS = 7
CONTENT_DROP_MIN_CARDS = 3
_TAGS = re.compile(r"<[^>]+>|\[x\]|\$|#|@|\{\d\}")
_SPACE = re.compile(r"\s+")
_DEV_COMMENT = re.compile(r"Комментарий разработчиков:\s*(.+)", re.DOTALL)


class HistoryError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# -- HearthstoneJSON builds ------------------------------------------------


def _cache_dir() -> Path:
    path = root_dir() / "cache" / "balance-radar" / "hsjson"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get(client: httpx.Client, url: str, *, attempts: int = 4) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.get(url)
        except httpx.TransportError as exc:
            last = exc
        else:
            if response.status_code == 200:
                return response
            if response.status_code not in {429, 500, 502, 503, 504}:
                raise HistoryError(
                    f"HearthstoneJSON returned HTTP {response.status_code}",
                    status_code=response.status_code,
                )
            last = HistoryError(f"HTTP {response.status_code}")
        time.sleep(min(30.0, 2.0**attempt))
    raise HistoryError(f"HearthstoneJSON unavailable: {last}")


def hsjson_builds(client: httpx.Client) -> list[int]:
    html = _get(client, HSJSON_INDEX_URL).text
    return sorted({int(build) for build in re.findall(r'href="/v1/(\d+)/', html)})


def normalize_text(value: Any) -> str:
    """Card text without markup: tags, [x], line-break markers (&, _) and spacing."""
    # Some builds append internal markers after "~" (e.g. "~GAMEPLAY ASPROXY ...").
    text = _TAGS.sub("", str(value or "").split("~", 1)[0])
    text = (
        text.replace("\n", " ")
        .replace("&", " ")
        .replace("_", " ")
        .replace("\u00a0", " ")
    )
    return _SPACE.sub(" ", text).strip()


def _stat(card: Mapping[str, Any], field: str) -> Any:
    # Weapon durability moved from ``durability`` to ``health`` in some builds.
    if card.get("type") == "WEAPON":
        if field == "durability":
            return card.get("durability") or card.get("health")
        if field == "health":
            return None
    return card.get(field)


def _compact(card: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": card.get("id"),
        "dbfId": card.get("dbfId"),
        "name": card.get("name"),
        "type": card.get("type"),
        "set": card.get("set"),
        "cardClass": card.get("cardClass"),
        "rarity": card.get("rarity"),
        **{field: card.get(field) for field in NUMERIC_FIELDS},
        "text": normalize_text(card.get("text")),
    }


def load_build(build: int, client: httpx.Client) -> dict[int, dict[str, Any]] | None:
    """Collectible cards of one build by dbfId; builds are immutable, so cached forever.

    ``None`` when HearthstoneJSON lists the build but serves no card file.
    """
    path = _cache_dir() / f"{build}.json.gz"
    missing = path.with_suffix(".missing")
    if missing.exists():
        return None
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return {int(k): v for k, v in json.load(handle).items()}
    try:
        cards = _get(client, HSJSON_CARDS_URL.format(build=build)).json()
    except HistoryError as exc:
        if exc.status_code in {403, 404}:
            missing.touch()
            return None
        raise
    if not isinstance(cards, list) or len(cards) < 500:
        raise HistoryError(f"HearthstoneJSON build {build} is incomplete")
    compact = {
        int(card["dbfId"]): _compact(card)
        for card in cards
        if isinstance(card, dict)
        and isinstance(card.get("dbfId"), int)
        and card.get("set") != "HERO_SKINS"
        and not (card.get("type") == "HERO" and card.get("cost") is None)
    }
    pending = path.with_suffix(".tmp")
    with gzip.open(pending, "wt", encoding="utf-8") as handle:
        json.dump(compact, handle, ensure_ascii=False)
    os.replace(pending, path)
    return compact


def diff_builds(
    before: Mapping[int, Mapping[str, Any]], after: Mapping[int, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for dbf_id, new in after.items():
        old = before.get(dbf_id)
        if old is None:
            continue
        fields = [
            {"field": field, "before": _stat(old, field), "after": _stat(new, field)}
            for field in NUMERIC_FIELDS
            if _stat(old, field) != _stat(new, field)
            and _stat(old, field) is not None
            and _stat(new, field) is not None
        ]
        old_text, new_text = (
            normalize_text(old.get("text")),
            normalize_text(new.get("text")),
        )
        text_changed = old_text != new_text
        if not fields and not text_changed:
            continue
        changes.append(
            {
                "dbfId": dbf_id,
                "cardId": new.get("id"),
                "nameEn": new.get("name"),
                "type": new.get("type"),
                "set": new.get("set"),
                "class": new.get("cardClass"),
                "rarity": new.get("rarity"),
                "fields": fields,
                "textBefore": old_text if text_changed else None,
                "textAfter": new_text if text_changed else None,
                "direction": numeric_direction(fields, text_changed),
            }
        )
    return changes


def numeric_direction(fields: Sequence[Mapping[str, Any]], text_changed: bool) -> str:
    """nerf/buff from the numbers alone; ``rework``/``mixed`` needs a model label."""
    score = 0
    signs = set()
    for item in fields:
        delta = (item["after"] or 0) - (item["before"] or 0)
        sign = (1 if delta > 0 else -1) * (1 if item["field"] == "cost" else -1)
        signs.add(sign)
        score += sign
    if text_changed:
        return "rework"
    if not signs:
        return "neutral"
    if len(signs) > 1:
        return "mixed"
    return "nerf" if score > 0 else "buff"


def new_cards(
    before: Mapping[int, Any], after: Mapping[int, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    return [card for dbf, card in after.items() if dbf not in before]


# -- patches -----------------------------------------------------------------


def _patch_build(version: str) -> int | None:
    tail = version.rsplit(".", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _patch_date(patch: Mapping[str, Any]) -> str | None:
    value = patch.get("official_published_at") or patch.get("published_at")
    return str(value)[:10] if value else None


def dev_comment(content: str | None, limit: int = 900) -> str | None:
    """Hearthstone developer commentary (the Battlegrounds part is cut off)."""
    if not content:
        return None
    text = content.split("Изменения Полей сражений", 1)[0]
    match = _DEV_COMMENT.search(text)
    if not match:
        return None
    return _SPACE.sub(" ", match.group(1)).strip()[:limit] or None


def load_patches() -> list[dict[str, Any]]:
    from ..patches_db import list_patches

    rows = list_patches(limit=2000, include_content=True).get("patches") or []
    patches: dict[int, dict[str, Any]] = {}
    for row in rows:
        build = _patch_build(str(row.get("version") or ""))
        day = _patch_date(row)
        if build is None or day is None:
            continue
        patches[build] = {
            "version": row["version"],
            "displayVersion": row.get("display_version") or row["version"],
            "build": build,
            "date": day,
            "title": row.get("official_title") or row.get("wiki_title"),
            "titleRu": row.get("title"),
            "officialUrl": row.get("official_url"),
            "wikiUrl": row.get("wiki_url"),
            "manacostUrl": row.get("source_url"),
            "devComment": dev_comment(row.get("content_text")),
        }
    return [patches[build] for build in sorted(patches)]


def attribute(
    build: int, patches: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """The patch a client build belongs to: the newest patch at or before it."""
    candidate = None
    for patch in patches:
        if patch["build"] <= build:
            candidate = patch
        else:
            break
    return candidate


# -- HSReplay statistics around a patch --------------------------------------


class ArchetypeStats:
    """Standard Legend archetype series and deck lists from the HSReplay tables."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.names: dict[int, tuple[str, str | None]] = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT archetype_id, name, player_class FROM hsreplay_archetypes"
            )
        }
        self.series: dict[int, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        rows = conn.execute(
            """
            SELECT s.archetype_id, t.series_name, t.point_date, t.value
            FROM archetype_time_series t JOIN archetype_snapshots s ON s.id = t.snapshot_id
            WHERE s.game_type = 'RANKED_STANDARD' AND s.rank_range = 'LEGEND' AND t.value IS NOT NULL
            ORDER BY t.snapshot_id
            """
        )
        for archetype_id, series, day, value in rows:
            key = "popularity" if series.startswith("popularity") else "winrate"
            self.series[archetype_id][key][str(day)[:10]] = float(value)
        days = [d for a in self.series.values() for s in a.values() for d in s]
        self.first_day = min(days) if days else None
        self.runs = [
            (row[0], str(row[1]))
            for row in conn.execute(
                "SELECT run_id, MIN(fetched_at) FROM archetype_snapshots "
                "WHERE game_type='RANKED_STANDARD' AND rank_range='LEGEND' GROUP BY run_id ORDER BY 2"
            )
        ]
        self._shares: dict[int, dict[int, dict[int, float]]] = {}

    def card_name(self, dbf_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT COALESCE(card_name_en, card_name) FROM archetype_deck_cards WHERE dbf_id = ? LIMIT 1",
            (dbf_id,),
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def _run_for(self, day: str) -> tuple[int, str] | None:
        before = [run for run in self.runs if run[1][:10] < day]
        if before:
            return before[-1]
        after = [run for run in self.runs if run[1][:10] >= day]
        return after[0] if after else None

    def _card_shares(self, run_id: int) -> dict[int, dict[int, float]]:
        """dbfId -> {archetype_id: share of the archetype's deck games that run it}."""
        if run_id in self._shares:
            return self._shares[run_id]
        totals: dict[int, float] = defaultdict(float)
        with_card: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        rows = self.conn.execute(
            """
            SELECT s.archetype_id, d.id, COALESCE(d.total_games, 0), c.dbf_id
            FROM archetype_snapshots s
            JOIN archetype_decks d ON d.snapshot_id = s.id
            JOIN archetype_deck_cards c ON c.archetype_deck_id = d.id
            WHERE s.run_id = ? AND c.sideboard = 0
            """,
            (run_id,),
        )
        seen_decks: set[int] = set()
        for archetype_id, deck_id, games, dbf_id in rows:
            weight = float(games or 1)
            if deck_id not in seen_decks:
                seen_decks.add(deck_id)
                totals[archetype_id] += weight
            with_card[dbf_id][archetype_id] += weight
        shares = {
            dbf: {a: weight / totals[a] for a, weight in per.items() if totals.get(a)}
            for dbf, per in with_card.items()
        }
        self._shares[run_id] = shares
        return shares

    def _window(
        self, archetype_id: int, start: date, end: date
    ) -> dict[str, Any] | None:
        series = self.series.get(archetype_id) or {}
        values = {}
        for key in ("winrate", "popularity"):
            points = [
                value
                for day, value in (series.get(key) or {}).items()
                if start.isoformat() <= day <= end.isoformat()
            ]
            values[key] = round(statistics.fmean(points), 2) if points else None
        if values["winrate"] is None and values["popularity"] is None:
            return None
        return values

    def around(self, dbf_id: int, day: str) -> list[dict[str, Any]]:
        """Archetypes built around the card, with stats a week before and after."""
        if not self.first_day or day < self.first_day:
            return []
        run = self._run_for(day)
        if run is None:
            return []
        patch_day = date.fromisoformat(day)
        result = []
        for archetype_id, share in sorted(
            (self._card_shares(run[0]).get(dbf_id) or {}).items(),
            key=lambda item: -item[1],
        ):
            if share < CORE_SHARE:
                continue
            before = self._window(
                archetype_id,
                patch_day - timedelta(days=STATS_WINDOW_DAYS),
                patch_day - timedelta(days=1),
            )
            if before is None:
                continue
            after = self._window(
                archetype_id,
                patch_day + timedelta(days=1),
                patch_day + timedelta(days=STATS_WINDOW_DAYS),
            )
            name, class_id = self.names.get(archetype_id, (str(archetype_id), None))
            result.append(
                {
                    "archetype": name,
                    "archetypeId": archetype_id,
                    "class": class_id,
                    "cardShare": round(share, 2),
                    "source": "hsreplay",
                    "rank": "LEGEND",
                    "before": before,
                    "after": after,
                    "deckListsAt": run[1],
                }
            )
        return result[:3]

    def meta_before(self, day: str, limit: int | None = 10) -> list[dict[str, Any]]:
        if not self.first_day or day < self.first_day:
            return []
        patch_day = date.fromisoformat(day)
        rows = []
        for archetype_id in self.series:
            stats = self._window(
                archetype_id,
                patch_day - timedelta(days=STATS_WINDOW_DAYS),
                patch_day - timedelta(days=1),
            )
            if (
                stats
                and stats["popularity"] is not None
                and stats["winrate"] is not None
            ):
                name, class_id = self.names.get(archetype_id, (str(archetype_id), None))
                rows.append({"archetype": name, "class": class_id, **stats})
        rows.sort(key=lambda row: -(row["popularity"] or 0))
        return rows[:limit] if limit else rows


# -- model labels -------------------------------------------------------------


class ChangeLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str
    direction: Literal["nerf", "buff", "rework", "neutral"]
    summary: str = Field(description="Что изменилось, одной фразой по-русски")


class PatchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    summary: str = Field(
        description="Что и зачем сделали в патче, 1-3 предложения по-русски"
    )
    targeted_archetypes: list[str] = Field(
        description="Колоды, по которым били нерфы или которым помогали баффы"
    )


class Pattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    detail: str = Field(
        description="Закономерность с цифрами и примерами патчей, по-русски"
    )


class LabelsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: list[ChangeLabel] = Field(
        description="Ровно по одной метке на каждое переданное изменение"
    )


class NarrativeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patches: list[PatchSummary]
    summary: str = Field(
        description="Общий вывод о подходе Blizzard к балансу, 2-4 абзаца по-русски"
    )
    patterns: list[Pattern] = Field(description="6-10 закономерностей")


LABEL_BATCH = 120
LABELS_PROMPT = """Ты размечаешь изменения карт Hearthstone. Для каждого изменения
(сравнение двух сборок клиента: поля стоимости/атаки/здоровья и текст до и
после) определи итог:
- nerf — карта стала слабее; buff — сильнее;
- rework — изменили механику без явного усиления или ослабления;
- neutral — только формулировка, сила не изменилась.
Учитывай комментарий разработчиков патча, если он есть. summary — одной
короткой фразой по-русски, что именно изменилось (например, «Урон 5 → 4»).
Верни ровно одну метку на каждый change_id из входа.
"""
NARRATIVE_PROMPT = """Ты — аналитик баланса Hearthstone. Тебе дают историю балансных
патчей с точными изменениями карт (итоговое направление nerf/buff/rework уже
определено), комментарии разработчиков, сроки от выхода набора, винрейт и
популярность ключевых архетипов HSReplay (Legend) за неделю до и после патча,
где они есть, и посчитанную по всей истории статистику (computedPatterns).

1. patches: для каждого патча — 1–3 предложения по-русски, что сделали и
   зачем (опирайся на комментарий разработчиков), и какие колоды были целью.
2. summary и patterns: закономерности Blizzard — после каких винрейта и
   популярности обычно следует нерф, какие правки типичны (стоимость, статы,
   текст), как часто и через сколько дней после выхода набора выходят
   балансные патчи, что обычно баффают, как быстро трогают новые карты,
   что откатывают. Все числа бери только из computedPatterns и данных
   патчей, называй версии патчей и карты. Учитывай, что статистика до/после
   есть только для патчей начиная с coverage.statsFrom.
"""


def _label_input(changes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": change["id"],
            "card": change.get("nameEn"),
            "class": change.get("class"),
            "direction": change["direction"],
            "fields": [
                [f["field"], f["before"], f["after"]]
                for f in change.get("fields") or []
            ],
            "textBefore": (change.get("textBefore") or "")[:220] or None,
            "textAfter": (change.get("textAfter") or "")[:220] or None,
            "setAgeDays": change.get("setAgeDays"),
            "core": [
                [
                    a["archetype"],
                    (a.get("before") or {}).get("winrate"),
                    (a.get("before") or {}).get("popularity"),
                ]
                for a in change.get("archetypes") or []
            ],
        }
        for change in changes
    ]


# -- patterns ------------------------------------------------------------------


def _quantiles(values: Sequence[float]) -> dict[str, Any] | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    if len(values) == 1:
        return {
            "n": 1,
            "min": values[0],
            "p25": values[0],
            "median": values[0],
            "p75": values[0],
            "max": values[0],
        }
    q = statistics.quantiles(values, n=4)
    return {
        "n": len(values),
        "min": round(values[0], 2),
        "p25": round(q[0], 2),
        "median": round(statistics.median(values), 2),
        "p75": round(q[2], 2),
        "max": round(values[-1], 2),
    }


def compute_patterns(
    patches: Sequence[Mapping[str, Any]], drops: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    changes = [change for patch in patches for change in patch["changes"]]
    by_direction: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for change in changes:
        by_direction[change["direction"]].append(change)

    def kinds(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for change in items:
            fields = {f["field"] for f in change.get("fields") or []}
            if change.get("textAfter") is not None:
                counter["text" if not fields else "text+stats"] += 1
            elif fields == {"cost"}:
                counter["cost"] += 1
            elif "cost" in fields:
                counter["cost+stats"] += 1
            else:
                counter["stats"] += 1
        return dict(counter.most_common())

    def deltas(items: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for change in items:
            for item in change.get("fields") or []:
                if item["field"] == field:
                    delta = (item["after"] or 0) - (item["before"] or 0)
                    counter[f"{delta:+d}"] += 1
        return dict(counter.most_common(6))

    def pre(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any] | None:
        values = []
        for change in items:
            archetypes = change.get("archetypes") or []
            if archetypes and (archetypes[0].get("before") or {}).get(key) is not None:
                values.append(archetypes[0]["before"][key])
        return _quantiles(values)

    def effect(items: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        values = []
        for change in items:
            for archetype in (change.get("archetypes") or [])[:1]:
                before, after = (
                    archetype.get("before") or {},
                    archetype.get("after") or {},
                )
                if (
                    before.get("winrate") is not None
                    and after.get("winrate") is not None
                ):
                    values.append(after["winrate"] - before["winrate"])
        return _quantiles(values)

    # A balance patch changes the power of at least one card.
    balance_days = sorted(
        {
            patch["date"]
            for patch in patches
            if any(c["direction"] in {"nerf", "buff"} for c in patch["changes"])
        }
    )
    intervals = [
        (date.fromisoformat(b) - date.fromisoformat(a)).days
        for a, b in pairwise(balance_days)
    ]
    first_after_drop = []
    for drop in drops:
        later = [d for d in balance_days if d > drop["date"]]
        if later:
            first_after_drop.append(
                {
                    "drop": drop["version"],
                    "kind": drop["kind"],
                    "date": drop["date"],
                    "firstBalancePatch": later[0],
                    "days": (
                        date.fromisoformat(later[0]) - date.fromisoformat(drop["date"])
                    ).days,
                }
            )
    years = Counter(day[:4] for day in balance_days)
    nerfs, buffs = by_direction.get("nerf", []), by_direction.get("buff", [])

    # Strong popular archetypes before a balance patch that no nerf touched.
    spared: list[dict[str, Any]] = []
    nerf_wr = pre(nerfs, "winrate")
    nerf_pop = pre(nerfs, "popularity")
    if nerf_wr and nerf_pop:
        for patch in patches:
            hit = {
                a["archetype"]
                for c in patch["changes"]
                if c["direction"] == "nerf"
                for a in c.get("archetypes") or []
            }
            for row in patch.get("metaBefore") or []:
                if (
                    row["winrate"] >= nerf_wr["p25"]
                    and row["popularity"] >= nerf_pop["p25"]
                    and row["archetype"] not in hit
                ):
                    spared.append({"patch": patch["version"], **row})
    return {
        "balancePatches": len(balance_days),
        "balancePatchesPerYear": dict(sorted(years.items())),
        "daysBetweenBalancePatches": _quantiles([float(v) for v in intervals]),
        "daysFromContentDropToFirstBalancePatch": {
            "expansion": _quantiles(
                [float(r["days"]) for r in first_after_drop if r["kind"] == "expansion"]
            ),
            "miniSetOrCards": _quantiles(
                [float(r["days"]) for r in first_after_drop if r["kind"] != "expansion"]
            ),
            "events": first_after_drop[-12:],
        },
        "changes": {
            direction: len(items) for direction, items in sorted(by_direction.items())
        },
        "changeKinds": {"nerf": kinds(nerfs), "buff": kinds(buffs)},
        "costDeltas": {"nerf": deltas(nerfs, "cost"), "buff": deltas(buffs, "cost")},
        "attackDeltas": {
            "nerf": deltas(nerfs, "attack"),
            "buff": deltas(buffs, "attack"),
        },
        "healthDeltas": {
            "nerf": deltas(nerfs, "health"),
            "buff": deltas(buffs, "health"),
        },
        "byClass": {
            "nerf": dict(Counter(str(c.get("class")) for c in nerfs).most_common()),
            "buff": dict(Counter(str(c.get("class")) for c in buffs).most_common()),
        },
        "byRarity": {
            "nerf": dict(Counter(str(c.get("rarity")) for c in nerfs).most_common()),
            "buff": dict(Counter(str(c.get("rarity")) for c in buffs).most_common()),
        },
        "setAgeDays": {
            "nerf": _quantiles(
                [
                    float(c["setAgeDays"])
                    for c in nerfs
                    if c.get("setAgeDays") is not None
                ]
            ),
            "buff": _quantiles(
                [
                    float(c["setAgeDays"])
                    for c in buffs
                    if c.get("setAgeDays") is not None
                ]
            ),
        },
        "newSetShare": {
            "nerf": round(
                sum(1 for c in nerfs if (c.get("setAgeDays") or 999) <= 45)
                / len(nerfs),
                2,
            )
            if nerfs
            else None,
            "buff": round(
                sum(1 for c in buffs if (c.get("setAgeDays") or 999) <= 45)
                / len(buffs),
                2,
            )
            if buffs
            else None,
        },
        "preStats": {
            "basis": "core HSReplay Legend archetype (card in >=50% of its deck games), mean of 7 days before the patch",
            "nerf": {"winrate": nerf_wr, "popularity": nerf_pop},
            "buff": {
                "winrate": pre(buffs, "winrate"),
                "popularity": pre(buffs, "popularity"),
            },
        },
        "effect": {
            "basis": "core archetype winrate, 7 days after minus 7 days before",
            "nerf": effect(nerfs),
            "buff": effect(buffs),
        },
        "sparedArchetypes": spared[-15:],
        "calibration": calibration_tables(patches),
        "timing": {
            "balancePatchIntervals": intervals,
            "firstBalancePatchDaysAfterExpansion": [
                r["days"] for r in first_after_drop if r["kind"] == "expansion"
            ],
            "firstBalancePatchDaysAfterMiniSet": [
                r["days"] for r in first_after_drop if r["kind"] != "expansion"
            ],
            "lastBalancePatch": balance_days[-1] if balance_days else None,
        },
    }


# -- calibration ----------------------------------------------------------------

NERF_WINRATE_BANDS = (
    (None, 50.0),
    (50.0, 52.0),
    (52.0, 54.0),
    (54.0, 56.0),
    (56.0, None),
)
NERF_POPULARITY_BANDS = ((None, 3.0), (3.0, 8.0), (8.0, None))
BUFF_WINRATE_BANDS = (
    (None, 40.0),
    (40.0, 45.0),
    (45.0, 48.0),
    (48.0, 51.0),
    (51.0, None),
)
BUFF_POPULARITY_BANDS = ((None, 1.0), (1.0, 3.0), (3.0, None))
MIN_CELL_SAMPLES = 4


def _band(value: float, bands: Sequence[tuple[float | None, float | None]]) -> str:
    for low, high in bands:
        if (low is None or value >= low) and (high is None or value < high):
            left = "" if low is None else f"{low:g}"
            right = "" if high is None else f"{high:g}"
            return f"{left}-{right}"
    return "?"


def calibration_samples(patches: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Archetype-weeks before balance patches, marked with what the patch did to them."""
    samples = []
    for patch in patches:
        if not patch.get("metaBeforeAll"):
            continue
        if not any(c["direction"] in {"nerf", "buff"} for c in patch["changes"]):
            continue
        hits = {
            direction: {
                a["archetype"]
                for c in patch["changes"]
                if c["direction"] == direction
                for a in c.get("archetypes") or []
            }
            for direction in ("nerf", "buff")
        }
        for name, class_id, winrate, popularity in patch["metaBeforeAll"]:
            samples.append(
                {
                    "patch": patch["version"],
                    "archetype": name,
                    "class": class_id,
                    "winrate": winrate,
                    "popularity": popularity,
                    "nerfed": name in hits["nerf"],
                    "buffed": name in hits["buff"],
                }
            )
    return samples


def calibration_tables(patches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How often an archetype in a winrate/popularity band was hit by the next balance patch."""
    samples = calibration_samples(patches)

    def table(flag: str, wr_bands: Any, pop_bands: Any) -> dict[str, Any]:
        grid: dict[str, dict[str, Any]] = {}
        by_wr: dict[str, dict[str, Any]] = {}
        by_pop: dict[str, dict[str, Any]] = {}
        for sample in samples:
            for key, store, label in (
                (
                    f"{_band(sample['winrate'], wr_bands)}|{_band(sample['popularity'], pop_bands)}",
                    grid,
                    "grid",
                ),
                (_band(sample["winrate"], wr_bands), by_wr, "wr"),
                (_band(sample["popularity"], pop_bands), by_pop, "pop"),
            ):
                cell = store.setdefault(key, {"samples": 0, "hits": 0, "examples": []})
                cell["samples"] += 1
                if sample[flag]:
                    cell["hits"] += 1
                    if label == "grid" and len(cell["examples"]) < 3:
                        cell["examples"].append(
                            f"{sample['archetype']} ({sample['patch']})"
                        )
        for store in (grid, by_wr, by_pop):
            for cell in store.values():
                cell["rate"] = (
                    round(cell["hits"] / cell["samples"], 3)
                    if cell["samples"]
                    else None
                )
        total = sum(1 for s in samples if s[flag])
        return {
            "grid": grid,
            "byWinrate": by_wr,
            "byPopularity": by_pop,
            "overall": round(total / len(samples), 3) if samples else None,
        }

    return {
        "basis": (
            "HSReplay Legend archetypes seen the week before each balance patch with "
            "statistics; hit = a changed card is in at least half of the archetype's decks"
        ),
        "samples": len(samples),
        "patches": len({s["patch"] for s in samples}),
        "nerf": table("nerfed", NERF_WINRATE_BANDS, NERF_POPULARITY_BANDS),
        "buff": table("buffed", BUFF_WINRATE_BANDS, BUFF_POPULARITY_BANDS),
    }


def calibrate(
    calibration: Mapping[str, Any] | None,
    side: str,
    winrate: float | None,
    popularity: float | None,
) -> dict[str, Any] | None:
    """Historical hit rate for this winrate/popularity, falling back to coarser tables."""
    if not calibration or winrate is None or popularity is None:
        return None
    direction = "nerf" if side in {"nerf", "nerfs"} else "buff"
    tables = calibration.get(direction) or {}
    wr_bands = NERF_WINRATE_BANDS if direction == "nerf" else BUFF_WINRATE_BANDS
    pop_bands = NERF_POPULARITY_BANDS if direction == "nerf" else BUFF_POPULARITY_BANDS
    wr_band, pop_band = _band(winrate, wr_bands), _band(popularity, pop_bands)
    for level, key, store in (
        ("winrate×popularity", f"{wr_band}|{pop_band}", tables.get("grid") or {}),
        ("winrate", wr_band, tables.get("byWinrate") or {}),
        ("popularity", pop_band, tables.get("byPopularity") or {}),
    ):
        cell = store.get(key)
        if cell and cell["samples"] >= MIN_CELL_SAMPLES:
            return {
                "level": level,
                "band": key,
                "historicalRate": cell["rate"],
                "samples": cell["samples"],
                "hits": cell["hits"],
                "examples": cell.get("examples") or [],
            }
    overall = tables.get("overall")
    return {
        "level": "overall",
        "band": None,
        "historicalRate": overall,
        "samples": calibration.get("samples"),
        "hits": None,
        "examples": [],
    }


def patch_probability(
    intervals: Sequence[int], days_since: int | None, horizon: int
) -> float | None:
    """P(next balance patch within ``horizon`` days | ``days_since`` days without one)."""
    if days_since is None:
        return None
    longer = [gap for gap in intervals if gap > days_since]
    if len(longer) < 3:
        return None
    # Laplace smoothing: eight past expansions are not certainty.
    hits = sum(1 for gap in longer if gap <= days_since + horizon)
    return round((hits + 1) / (len(longer) + 2), 3)


def timing_outlook(
    history: Mapping[str, Any] | None, today: date
) -> dict[str, Any] | None:
    if not history:
        return None
    patterns = history.get("patterns") or {}
    timing = patterns.get("timing") or {}
    drops = history.get("contentDrops") or []
    expansion = next((d for d in drops if d.get("kind") == "expansion"), None)
    latest_drop = drops[0] if drops else None
    last_patch = timing.get("lastBalancePatch")
    since_patch = (today - date.fromisoformat(last_patch)).days if last_patch else None
    since_expansion = (
        (today - date.fromisoformat(expansion["date"])).days if expansion else None
    )
    balance_since_expansion = bool(
        expansion and last_patch and last_patch > expansion["date"]
    )
    after_release = timing.get("firstBalancePatchDaysAfterExpansion") or []
    intervals = timing.get("balancePatchIntervals") or []
    if expansion and not balance_since_expansion:
        p7, p14 = (
            patch_probability(after_release, since_expansion, h) for h in (7, 14)
        )
        basis = (
            "days since the expansion launch, first balance patch after past expansions"
        )
    else:
        p7, p14 = (patch_probability(intervals, since_patch, h) for h in (7, 14))
        basis = (
            "days since the last balance patch, intervals between past balance patches"
        )
    return {
        "today": today.isoformat(),
        "lastExpansion": expansion["version"] if expansion else None,
        "lastExpansionDate": expansion["date"] if expansion else None,
        "daysSinceExpansion": since_expansion,
        "lastContentDrop": latest_drop["version"] if latest_drop else None,
        "lastBalancePatchDate": last_patch,
        "daysSinceLastBalancePatch": since_patch,
        "balancePatchSinceExpansion": balance_since_expansion,
        "medianDaysToFirstPatchAfterExpansion": statistics.median(after_release)
        if after_release
        else None,
        "medianDaysBetweenBalancePatches": statistics.median(intervals)
        if intervals
        else None,
        "nextPatchWithin7Days": p7,
        "nextPatchWithin14Days": p14,
        "basis": basis,
    }


# -- build ---------------------------------------------------------------------


def _ru_names() -> dict[str, str]:
    try:
        from ..cards_index import cards_by_id

        return {
            card_id: str(card.get("name"))
            for card_id, card in cards_by_id("ruRU").items()
            if card.get("name")
        }
    except Exception as exc:  # noqa: BLE001 - names are cosmetic
        logger.warning("ruRU card names unavailable: %s", type(exc).__name__)
        return {}


def build_history(
    *,
    client: httpx.Client | None = None,
    patches: Sequence[Mapping[str, Any]] | None = None,
    stats: ArchetypeStats | None = None,
    builds: Sequence[int] | None = None,
    loader: Callable[[int], Mapping[int, Mapping[str, Any]] | None] | None = None,
    ru_names: Mapping[str, str] | None = None,
    label: bool = True,
    claude: Any = None,
    previous: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    own_client = client is None and (builds is None or loader is None)
    if own_client:
        client = httpx.Client(timeout=90.0, follow_redirects=True)
    try:
        patches = list(patches if patches is not None else load_patches())
        if not patches:
            raise HistoryError("patch catalog is empty")
        builds = list(builds if builds is not None else hsjson_builds(client))  # type: ignore[arg-type]
        load = loader or (lambda build: load_build(build, client))  # type: ignore[arg-type]
        if stats is None:
            from ..db import get_db_connection

            stats = ArchetypeStats(get_db_connection())
        ru = dict(ru_names if ru_names is not None else _ru_names())
        set_released: dict[str, str] = {}
        drops: dict[str, dict[str, Any]] = {}
        per_patch: dict[str, dict[str, Any]] = {}
        previous_cards: Mapping[int, Mapping[str, Any]] | None = None
        first_patch = None
        for build in builds:
            patch = attribute(build, patches)
            cards = load(build)
            if cards is None:
                continue
            if patch is None:
                previous_cards = cards
                continue
            first_patch = first_patch or patch
            if previous_cards is None:
                for card in cards.values():
                    set_released.setdefault(
                        str(card.get("set")), ""
                    )  # older than coverage
                previous_cards = cards
                continue
            added = new_cards(previous_cards, cards)
            for card in added:
                set_released.setdefault(str(card.get("set")), patch["date"])
            if len(added) >= CONTENT_DROP_MIN_CARDS:
                drop = drops.setdefault(
                    patch["version"],
                    {
                        "version": patch["version"],
                        "date": patch["date"],
                        "title": patch.get("titleRu") or patch.get("title"),
                        "kind": "expansion"
                        if re.match(r"^\d+\.0\.0", patch["version"])
                        else "mini_set_or_cards",
                        "newCards": 0,
                        "sets": [],
                    },
                )
                drop["newCards"] += len(added)
                drop["sets"] = sorted(
                    {*drop["sets"], *(str(c.get("set")) for c in added)}
                )
            for change in diff_builds(previous_cards, cards):
                entry = per_patch.setdefault(patch["version"], {**patch, "changes": {}})
                entry["changes"][change["dbfId"]] = (
                    change  # a later build of the patch wins
                )
            previous_cards = cards
        result_patches: list[dict[str, Any]] = []
        for version, entry in per_patch.items():
            changes = []
            for change in entry.pop("changes").values():
                released = set_released.get(str(change.get("set")))
                change_id = f"{version}:{change['cardId']}"
                changes.append(
                    {
                        "id": change_id,
                        **change,
                        "name": ru.get(str(change["cardId"])) or change.get("nameEn"),
                        "setReleasedAt": released or None,
                        "setAgeDays": (
                            date.fromisoformat(entry["date"])
                            - date.fromisoformat(released)
                        ).days
                        if released
                        else None,
                        "archetypes": stats.around(change["dbfId"], entry["date"]),
                        "summary": None,
                    }
                )
            changes.sort(
                key=lambda c: (
                    c["direction"],
                    str(c.get("class")),
                    str(c.get("nameEn")),
                )
            )
            drop = max(
                (d for d in drops.values() if d["date"] <= entry["date"]),
                key=lambda d: d["date"],
                default=None,
            )
            result_patches.append(
                {
                    **entry,
                    "changes": changes,
                    "counts": dict(Counter(c["direction"] for c in changes)),
                    "lastContentDrop": drop["version"] if drop else None,
                    "daysSinceContentDrop": (
                        date.fromisoformat(entry["date"])
                        - date.fromisoformat(drop["date"])
                    ).days
                    if drop
                    else None,
                    "metaBefore": stats.meta_before(entry["date"]),
                    # Every archetype seen the week before: the calibration sample.
                    "metaBeforeAll": [
                        [
                            row["archetype"],
                            row["class"],
                            row["winrate"],
                            row["popularity"],
                        ]
                        for row in stats.meta_before(entry["date"], limit=None)
                        if (row["popularity"] or 0) >= 0.1
                    ],
                    "summary": None,
                    "targetedArchetypes": [],
                }
            )
        result_patches.sort(key=lambda p: p["build"], reverse=True)
        drop_list = sorted(drops.values(), key=lambda d: d["date"], reverse=True)
        dataset = {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": now.astimezone(UTC).isoformat(timespec="seconds"),
            "coverage": {
                "fromBuild": builds[0] if builds else None,
                "toBuild": builds[-1] if builds else None,
                "fromPatch": first_patch["version"] if first_patch else None,
                "from": first_patch["date"] if first_patch else None,
                "to": result_patches[0]["date"] if result_patches else None,
                "statsFrom": stats.first_day,
                "statsSource": "HSReplay Standard Legend archetypes",
                "patchCatalog": "/api/patches (wiki.gg, playhearthstone.com, hs-manacost.ru)",
                "cardSource": "HearthstoneJSON collectible cards per client build",
            },
            "contentDrops": drop_list,
            "patches": result_patches,
            "patterns": None,
            "narrative": None,
            "model": None,
            "usage": None,
        }
        _apply_labels(dataset, previous)
        dataset["patterns"] = compute_patterns(result_patches, drop_list)
        dataset["labelsHash"] = _labels_hash(dataset)
        if label:
            label_history(dataset, claude=claude, previous=previous)
        elif previous and previous.get("labelsHash") == dataset["labelsHash"]:
            dataset["narrative"] = previous.get("narrative")
            dataset["model"] = previous.get("model")
        return dataset
    finally:
        if own_client and client is not None:
            client.close()


def _labels_hash(dataset: Mapping[str, Any]) -> str:
    """Identity of everything the narrative depends on."""
    stable = [
        [
            p["version"],
            p.get("devComment"),
            [
                [c["id"], c["direction"], c.get("fields"), c.get("textAfter")]
                for c in p["changes"]
            ],
        ]
        for p in dataset["patches"]
    ]
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _change_key(change: Mapping[str, Any]) -> str:
    return json.dumps(
        [change.get("fields"), change.get("textBefore"), change.get("textAfter")],
        ensure_ascii=False,
    )


def _apply_labels(dataset: dict[str, Any], previous: Mapping[str, Any] | None) -> None:
    """Reuse labels of unchanged changes from the previous build: only new ones cost a call."""
    if not previous:
        return
    labelled = {
        c["id"]: c
        for p in previous.get("patches") or []
        for c in p.get("changes") or []
        if c.get("labelKey")
    }
    for patch in dataset["patches"]:
        for change in patch["changes"]:
            prior = labelled.get(change["id"])
            if not prior or prior["labelKey"] != _change_key(change):
                continue
            change["summary"] = prior.get("summary")
            change["labelKey"] = prior["labelKey"]
            if prior.get("labelledDirection") and change["direction"] in {
                "rework",
                "mixed",
            }:
                change["direction"] = prior["labelledDirection"]
                change["labelledDirection"] = prior["labelledDirection"]
        patch["counts"] = dict(Counter(c["direction"] for c in patch["changes"]))


def _patch_context(patch: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version": patch["version"],
        "date": patch["date"],
        "title": patch.get("titleRu") or patch.get("title"),
        "daysSinceContentDrop": patch.get("daysSinceContentDrop"),
        "devComment": patch.get("devComment"),
    }


def _sum_usage(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    keys = (
        "inputTokens",
        "outputTokens",
        "cacheReadInputTokens",
        "cacheCreationInputTokens",
    )
    total: dict[str, Any] = {
        key: sum(int(item.get(key) or 0) for item in items) for key in keys
    }
    total["costUsd"] = round(sum(float(item.get("costUsd") or 0) for item in items), 4)
    total["calls"] = len(items)
    return total


def label_history(
    dataset: dict[str, Any],
    *,
    claude: Any = None,
    previous: Mapping[str, Any] | None = None,
) -> None:
    """Label ambiguous changes in batches, then write the narrative from final numbers."""
    calls: list[dict[str, Any]] = []
    model = None
    patches_by_version = {p["version"]: p for p in dataset["patches"]}
    pending = [
        (patch["version"], change)
        for patch in dataset["patches"]
        for change in patch["changes"]
        if change["direction"] in {"rework", "mixed"} and not change.get("labelKey")
    ]
    errors: list[str] = []
    for start in range(0, len(pending), LABEL_BATCH):
        batch = pending[start : start + LABEL_BATCH]
        versions = sorted({version for version, _ in batch})
        payload = {
            "patches": [_patch_context(patches_by_version[v]) for v in versions],
            "changes": [
                {"patch": version, **item}
                for version, item in zip(
                    (v for v, _ in batch),
                    _label_input(c for _, c in batch),
                    strict=True,
                )
            ],
        }
        try:
            output, model, usage, _request = call_structured(
                payload,
                system=LABELS_PROMPT,
                output_model=LabelsOutput,
                client=claude,
                effort="low",
            )
        except AnalysisError as exc:
            errors.append(str(exc))
            logger.warning("balance history label batch skipped: %s", exc)
            continue
        calls.append(usage)
        labels = {label.change_id: label for label in output.labels}
        for _version, change in batch:
            label = labels.get(change["id"])
            if label is None:
                continue
            change["direction"] = label.direction
            change["labelledDirection"] = label.direction
            change["summary"] = label.summary
            change["labelKey"] = _change_key(change)
    for patch in dataset["patches"]:
        patch["counts"] = dict(Counter(c["direction"] for c in patch["changes"]))
    dataset["patterns"] = compute_patterns(dataset["patches"], dataset["contentDrops"])
    dataset["labelsHash"] = _labels_hash(dataset)
    narrative_usage = None
    if (
        previous
        and previous.get("labelsHash") == dataset["labelsHash"]
        and previous.get("narrative")
    ):
        old = {p["version"]: p for p in previous.get("patches") or []}
        for patch in dataset["patches"]:
            patch["summary"] = (old.get(patch["version"]) or {}).get("summary")
            patch["targetedArchetypes"] = (old.get(patch["version"]) or {}).get(
                "targetedArchetypes"
            ) or []
        dataset["narrative"] = previous["narrative"]
        model = model or previous.get("model")
        narrative_usage = (previous.get("usage") or {}).get("narrative")
        dataset["narrativeReused"] = True
    else:
        payload = {
            "coverage": dataset["coverage"],
            "computedPatterns": dataset["patterns"],
            "patches": [
                {
                    **_patch_context(patch),
                    "counts": patch["counts"],
                    "metaBefore": [
                        [m["archetype"], m["winrate"], m["popularity"]]
                        for m in patch.get("metaBefore") or []
                    ][:6],
                    "changes": [
                        [
                            c.get("nameEn"),
                            c.get("class"),
                            c["direction"],
                            [
                                [f["field"], f["before"], f["after"]]
                                for f in c.get("fields") or []
                            ],
                            c.get("summary"),
                            c.get("setAgeDays"),
                            [
                                [
                                    a["archetype"],
                                    (a.get("before") or {}).get("winrate"),
                                    (a.get("before") or {}).get("popularity"),
                                    (a.get("after") or {}).get("winrate"),
                                ]
                                for a in (c.get("archetypes") or [])[:1]
                            ],
                        ]
                        for c in patch["changes"]
                        if c["direction"] in {"nerf", "buff"}
                    ][:30],
                }
                for patch in dataset["patches"]
                if any(c["direction"] in {"nerf", "buff"} for c in patch["changes"])
            ],
        }
        try:
            output, model, narrative_usage, _request = call_structured(
                payload,
                system=NARRATIVE_PROMPT,
                output_model=NarrativeOutput,
                client=claude,
                effort="medium",
            )
        except AnalysisError as exc:
            errors.append(str(exc))
            logger.warning("balance history narrative skipped: %s", exc)
            output = None
        if output is not None:
            summaries = {summary.version: summary for summary in output.patches}
            for patch in dataset["patches"]:
                summary = summaries.get(patch["version"])
                if summary:
                    patch["summary"] = summary.summary
                    patch["targetedArchetypes"] = summary.targeted_archetypes
            dataset["narrative"] = {
                "summary": output.summary,
                "patterns": [pattern.model_dump() for pattern in output.patterns],
            }
    unlabelled = sum(
        1 for p in dataset["patches"] for c in p["changes"] if c["direction"] == "mixed"
    )
    dataset["model"] = model
    dataset["usage"] = {
        "labels": _sum_usage(calls),
        "narrative": narrative_usage,
        "costUsd": round(
            _sum_usage(calls)["costUsd"]
            + float((narrative_usage or {}).get("costUsd") or 0),
            4,
        ),
    }
    dataset["labelErrors"] = errors
    dataset["unlabelledMixed"] = unlabelled


# -- storage and lookup ------------------------------------------------------------


def history_path() -> Path:
    return root_dir() / HISTORY_FILE


def load_history() -> dict[str, Any] | None:
    try:
        return json.loads(history_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_history(dataset: Mapping[str, Any]) -> None:
    path = history_path()
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    os.replace(pending, path)


def ensure_history(
    *, force: bool = False, claude: Any = None, now: datetime | None = None
) -> dict[str, Any] | None:
    """Rebuild when missing, stale or behind the patch catalog; never raise."""
    now = now or datetime.now(UTC)
    current = load_history()
    try:
        newest = max((p["build"] for p in load_patches()), default=None)
    except Exception as exc:  # noqa: BLE001 - keep serving the previous history
        logger.warning("patch catalog unavailable for history: %s", type(exc).__name__)
        return current
    if current and current.get("schemaVersion", 1) < SCHEMA_VERSION:
        force = True
    if current and not force:
        generated = datetime.fromisoformat(current["generatedAt"])
        known = max((p["build"] for p in current.get("patches") or []), default=0)
        if now - generated < MAX_AGE and (
            newest is None
            or newest <= max(known, current["coverage"].get("toBuild") or 0)
        ):
            return current
    try:
        dataset = build_history(claude=claude, previous=current, now=now)
    except (HistoryError, sqlite3.Error, httpx.HTTPError) as exc:
        logger.warning("balance history rebuild failed: %s", exc)
        return current
    save_history(dataset)
    try:
        # The statistical backtest is free; keep it in step with the history.
        from .backtest import load_backtest, run_backtest, save_backtest

        save_backtest(run_backtest(dataset, previous=load_backtest(), now=now))
    except Exception as exc:  # noqa: BLE001 - the backtest only reports
        logger.warning("balance radar backtest skipped: %s", exc)
    return dataset


def model_context(history: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Compact patterns block for the radar prompt."""
    if not history:
        return None
    patterns = history.get("patterns") or {}
    narrative = history.get("narrative") or {}
    return {
        "coverage": history.get("coverage"),
        "summary": narrative.get("summary"),
        "patterns": narrative.get("patterns"),
        "numbers": {
            key: patterns.get(key)
            for key in (
                "balancePatches",
                "balancePatchesPerYear",
                "daysBetweenBalancePatches",
                "daysFromContentDropToFirstBalancePatch",
                "changeKinds",
                "costDeltas",
                "newSetShare",
                "setAgeDays",
                "preStats",
                "effect",
            )
        },
        "recentPatches": [
            {
                "version": p["version"],
                "date": p["date"],
                "summary": p.get("summary"),
                "counts": p.get("counts"),
                "daysSinceContentDrop": p.get("daysSinceContentDrop"),
            }
            for p in (history.get("patches") or [])[:8]
        ],
    }


def all_changes(history: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for patch in (history or {}).get("patches") or []:
        for change in patch["changes"]:
            out.append(
                {
                    **change,
                    "patch": patch["version"],
                    "date": patch["date"],
                    "url": patch.get("officialUrl")
                    or patch.get("wikiUrl")
                    or patch.get("manacostUrl"),
                }
            )
    return out


def precedent_view(
    change: Mapping[str, Any], similarity: Sequence[str], *, cited: bool = False
) -> dict[str, Any]:
    core = (change.get("archetypes") or [None])[0]
    return {
        "id": f"precedent:{change['id']}",
        "patch": change["patch"],
        "date": change.get("date"),
        "cardId": change["cardId"],
        "dbfId": change.get("dbfId"),
        "name": change.get("name") or change.get("nameEn") or change["cardId"],
        "nameEn": change.get("nameEn"),
        "class": change.get("class"),
        "direction": change["direction"]
        if change["direction"] in {"nerf", "buff", "rework", "neutral"}
        else "rework",
        "changes": change.get("fields") or [],
        "textBefore": change.get("textBefore"),
        "textAfter": change.get("textAfter"),
        "summary": change.get("summary"),
        "setAgeDays": change.get("setAgeDays"),
        "preStats": {"archetype": core["archetype"], **(core.get("before") or {})}
        if core
        else None,
        "postStats": {"archetype": core["archetype"], **(core.get("after") or {})}
        if core and core.get("after")
        else None,
        "url": change.get("url"),
        "similarity": list(similarity),
        "citedByModel": cited,
    }


def find_precedents(
    entity: Mapping[str, Any],
    side: str,
    changes: Sequence[Mapping[str, Any]],
    *,
    limit: int = 4,
) -> list[tuple[Mapping[str, Any], list[str]]]:
    """Past changes most like this candidate: same card, same class and similar stats."""
    direction = "nerf" if side == "nerfs" else "buff"
    stats = entity.get("stats") or {}
    winrate = stats.get("legendWinrate") or stats.get("winrate")
    popularity = stats.get("legendPopularity") or stats.get("popularity")
    scored = []
    for change in changes:
        reasons: list[str] = []
        score = 0.0
        if entity.get("kind") == "card" and change.get("cardId") == entity.get("id"):
            reasons.append("same_card")
            score += 10
        if change["direction"] != direction and "same_card" not in reasons:
            continue
        if entity.get("class") and change.get("class") == entity.get("class"):
            reasons.append("same_class")
            score += 2
        if entity.get("kind") == "archetype":
            names = {a["archetype"].casefold() for a in change.get("archetypes") or []}
            if str(entity.get("name", "")).casefold() in names:
                reasons.append("same_archetype")
                score += 6
        core = (change.get("archetypes") or [None])[0]
        before = (core or {}).get("before") or {}
        if (
            winrate is not None
            and before.get("winrate") is not None
            and abs(before["winrate"] - winrate) <= 3
        ):
            reasons.append("similar_winrate")
            score += 3 - abs(before["winrate"] - winrate)
        if (
            popularity
            and before.get("popularity")
            and 0.5 <= before["popularity"] / popularity <= 2
        ):
            reasons.append("similar_popularity")
            score += 1.5
        if entity.get("newSet") and (change.get("setAgeDays") or 999) <= 45:
            reasons.append("new_set_card")
            score += 1
        if not reasons or reasons == ["same_class"]:
            continue
        scored.append((score, change.get("date") or "", change, reasons))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [(change, reasons) for _score, _day, change, reasons in scored[:limit]]
