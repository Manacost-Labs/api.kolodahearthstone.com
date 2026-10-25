"""Backtest: would the radar have flagged what Blizzard actually changed?

For every past balance patch with local statistics, the inputs are rebuilt
from data dated before the patch only:

* HSReplay Legend archetypes, mean winrate/popularity over the week before;
* the core cards of those archetypes from the deck lists of that time;
* the balance history sliced to earlier patches (patterns and calibration).

Two predictors are scored against the truth (archetypes whose core card was
nerfed or buffed, and the changed cards themselves):

* ``statistical`` ranks archetypes by the calibrated historical hit rate,
  leave-one-patch-out (the patch being tested never calibrates itself);
* ``model`` (optional, ``--llm``) asks Claude for ranked candidates.

Precision@K and recall@K are micro-averaged over patches. Claude may have read
some of these patch notes in training, so the model scores are an upper bound;
the statistical predictor has no such leak.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..storage import root_dir
from .analysis import AnalysisError, call_structured
from .history import (
    STATS_WINDOW_DAYS,
    ArchetypeStats,
    calibrate,
    calibration_tables,
    compute_patterns,
)

logger = logging.getLogger(__name__)

BACKTEST_FILE = "balance-radar-backtest.json"
KS = (3, 5, 10)
CORE_CARDS_PER_ARCHETYPE = 6


def backtest_path() -> Path:
    return root_dir() / BACKTEST_FILE


def load_backtest() -> dict[str, Any] | None:
    try:
        return json.loads(backtest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_backtest(result: Mapping[str, Any]) -> None:
    path = backtest_path()
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(pending, path)


def _truth(patch: Mapping[str, Any]) -> dict[str, dict[str, set]]:
    truth: dict[str, dict[str, set]] = {}
    for direction in ("nerf", "buff"):
        changes = [c for c in patch["changes"] if c["direction"] == direction]
        truth[direction] = {
            "archetypes": {
                a["archetype"] for c in changes for a in c.get("archetypes") or []
            },
            "cards": {c["dbfId"] for c in changes},
        }
    return truth


def _score(ranked: Sequence[str], truth: set, k: int) -> tuple[int, int, int]:
    """(hits in top k, predicted count, true count)."""
    top = list(ranked)[:k]
    return sum(1 for item in top if item in truth), len(top), len(truth)


def _metrics(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in KS:
        hits = sum(r[key][str(k)][0] for r in rows)
        predicted = sum(r[key][str(k)][1] for r in rows)
        actual = sum(r[key][str(k)][2] for r in rows)
        out[f"@{k}"] = {
            "precision": round(hits / predicted, 3) if predicted else None,
            "recall": round(hits / actual, 3) if actual else None,
            "hits": hits,
            "predicted": predicted,
            "actual": actual,
        }
    return out


def _evaluable(history: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        patch
        for patch in reversed(history.get("patches") or [])
        if patch.get("metaBeforeAll")
        and any(
            c["direction"] in {"nerf", "buff"} and c.get("archetypes")
            for c in patch["changes"]
        )
    ]


def statistical_backtest(history: Mapping[str, Any]) -> dict[str, Any]:
    patches = list(history.get("patches") or [])
    rows = []
    for patch in _evaluable(history):
        others = [p for p in patches if p["version"] != patch["version"]]
        calibration = calibration_tables(others)
        truth = _truth(patch)
        row: dict[str, Any] = {"patch": patch["version"], "date": patch["date"]}
        for direction in ("nerf", "buff"):
            scored = []
            for name, _class, winrate, popularity in patch["metaBeforeAll"]:
                cal = calibrate(calibration, direction, winrate, popularity) or {}
                scored.append(
                    (
                        (cal.get("historicalRate") or 0.0),
                        popularity if direction == "nerf" else -popularity,
                        name,
                    )
                )
            scored.sort(reverse=True)
            ranked = [name for _rate, _pop, name in scored]
            row[direction] = {
                str(k): _score(ranked, truth[direction]["archetypes"], k) for k in KS
            }
            row[f"{direction}Top"] = ranked[:5]
            row[f"{direction}Truth"] = sorted(truth[direction]["archetypes"])
        rows.append(row)
    return {
        "predictor": "statistical",
        "basis": "leave-one-patch-out calibrated hit rate of HSReplay Legend archetypes (week before the patch)",
        "patches": len(rows),
        "nerf": _metrics(rows, "nerf") if rows else None,
        "buff": _metrics(rows, "buff") if rows else None,
        "perPatch": rows,
    }


# -- model backtest ---------------------------------------------------------------


class BacktestCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(
        description="ref из входных данных: archetype:<имя> или card:<dbfId>"
    )
    confidence: int = Field(ge=0, le=100)


class BacktestOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nerfs: list[BacktestCandidate]
    buffs: list[BacktestCandidate]


BACKTEST_PROMPT = """Ты — аналитик баланса Hearthstone. Дата во входных данных — день
перед балансным патчем. Тебе дают архетипы HSReplay (Legend) с винрейтом и
популярностью за предыдущую неделю, ключевые карты этих архетипов, историю
прошлых правок Blizzard и посчитанные закономерности (пороги, калибровку, сроки).
Не используй знания о том, что произошло после этой даты.
Назови до 15 кандидатов на нерф и до 15 на бафф (архетипы и карты), от самого
вероятного, с confidence 0–100. Используй только ref из входа.
"""


def _model_input(
    patch: Mapping[str, Any], history: Mapping[str, Any], stats: ArchetypeStats
) -> tuple[dict[str, Any], set[str]]:
    day = patch["date"]
    earlier = [p for p in history.get("patches") or [] if p["date"] < day]
    patterns = compute_patterns(
        earlier, [d for d in history.get("contentDrops") or [] if d["date"] < day]
    )
    archetypes = sorted(patch["metaBeforeAll"], key=lambda row: -row[3])[:40]
    names = {row[0] for row in archetypes}
    run = stats._run_for(day)
    cards: dict[int, dict[str, Any]] = {}
    if run is not None:
        shares = stats._card_shares(run[0])
        by_archetype: dict[str, list[tuple[float, int]]] = {}
        for dbf_id, per in shares.items():
            for archetype_id, share in per.items():
                name = stats.names.get(archetype_id, ("", None))[0]
                if name in names and share >= 0.5:
                    by_archetype.setdefault(name, []).append((share, dbf_id))
        for name, items in by_archetype.items():
            for share, dbf_id in sorted(items, reverse=True)[:CORE_CARDS_PER_ARCHETYPE]:
                entry = cards.setdefault(
                    dbf_id, {"ref": f"card:{dbf_id}", "dbfId": dbf_id, "archetypes": []}
                )
                entry["archetypes"].append([name, round(share, 2)])
    names_by_dbf = {
        c["dbfId"]: c.get("nameEn")
        for p in history.get("patches") or []
        for c in p["changes"]
    }
    for dbf_id, entry in cards.items():
        entry["name"] = names_by_dbf.get(dbf_id) or stats.card_name(dbf_id)
    drops = [d for d in history.get("contentDrops") or [] if d["date"] < day]
    payload = {
        "date": day,
        "daysSinceContentDrop": (
            date.fromisoformat(day) - date.fromisoformat(drops[0]["date"])
        ).days
        if drops
        else None,
        "archetypes": [
            {
                "ref": f"archetype:{name}",
                "name": name,
                "class": class_id,
                "winrate": wr,
                "popularity": pop,
            }
            for name, class_id, wr, pop in archetypes
        ],
        "cards": list(cards.values()),
        "patterns": {
            key: patterns.get(key)
            for key in (
                "balancePatches",
                "daysBetweenBalancePatches",
                "changeKinds",
                "preStats",
                "calibration",
            )
        },
        "recentChanges": [
            [c.get("nameEn"), c.get("class"), c["direction"], p["version"]]
            for p in earlier[-6:]
            for c in p["changes"]
            if c["direction"] in {"nerf", "buff"}
        ][:60],
    }
    refs = {a["ref"] for a in payload["archetypes"]} | {
        c["ref"] for c in payload["cards"]
    }
    return payload, refs


def model_backtest(
    history: Mapping[str, Any],
    stats: ArchetypeStats,
    *,
    claude: Any = None,
    limit: int | None = None,
) -> dict[str, Any]:
    rows = []
    usage_total = 0.0
    errors = []
    for patch in _evaluable(history)[-(limit or 100) :]:
        payload, refs = _model_input(patch, history, stats)
        try:
            output, model, usage, _req = call_structured(
                payload,
                system=BACKTEST_PROMPT,
                output_model=BacktestOutput,
                client=claude,
                effort="medium",
            )
        except AnalysisError as exc:
            errors.append(f"{patch['version']}: {exc}")
            continue
        usage_total += float(usage.get("costUsd") or 0)
        truth = _truth(patch)
        card_archetypes = {
            c["ref"]: [a[0] for a in c["archetypes"]] for c in payload["cards"]
        }
        row: dict[str, Any] = {
            "patch": patch["version"],
            "date": patch["date"],
            "model": model,
        }
        for direction, items in (("nerf", output.nerfs), ("buff", output.buffs)):
            ranked = sorted(
                (i for i in items if i.ref in refs), key=lambda i: -i.confidence
            )
            archetypes: list[str] = []
            cards: list[int] = []
            for item in ranked:
                if item.ref.startswith("archetype:"):
                    names = [item.ref.split(":", 1)[1]]
                else:
                    cards.append(int(item.ref.split(":", 1)[1]))
                    names = card_archetypes.get(item.ref, [])
                for name in names:
                    if name not in archetypes:
                        archetypes.append(name)
            pool_cards = {
                int(r.split(":", 1)[1]) for r in refs if r.startswith("card:")
            }
            row[direction] = {
                str(k): _score(archetypes, truth[direction]["archetypes"], k)
                for k in KS
            }
            row[f"{direction}Cards"] = {
                str(k): _score(cards, truth[direction]["cards"] & pool_cards, k)
                for k in KS
            }
            row[f"{direction}Top"] = archetypes[:5]
            row[f"{direction}Truth"] = sorted(truth[direction]["archetypes"])
        rows.append(row)
    return {
        "predictor": "model",
        "basis": "Claude on data dated before each patch; may have seen these patch notes in training (upper bound)",
        "patches": len(rows),
        "nerf": _metrics(rows, "nerf") if rows else None,
        "buff": _metrics(rows, "buff") if rows else None,
        "nerfCards": _metrics(rows, "nerfCards") if rows else None,
        "buffCards": _metrics(rows, "buffCards") if rows else None,
        "costUsd": round(usage_total, 4),
        "errors": errors,
        "perPatch": rows,
    }


def run_backtest(
    history: Mapping[str, Any],
    *,
    stats: ArchetypeStats | None = None,
    llm: bool = False,
    claude: Any = None,
    previous: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    result: dict[str, Any] = {
        "generatedAt": now.astimezone(UTC).isoformat(timespec="seconds"),
        "historyGeneratedAt": history.get("generatedAt"),
        "window": f"{STATS_WINDOW_DAYS} days before each patch",
        "statistical": statistical_backtest(history),
        "model": (previous or {}).get("model"),
    }
    if llm:
        if stats is None:
            from ..db import get_db_connection

            stats = ArchetypeStats(get_db_connection())
        result["model"] = model_backtest(history, stats, claude=claude)
    return result


def backtest_summary(result: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return None

    def pick(block: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not block or not block.get("patches"):
            return None
        return {
            "patches": block["patches"],
            "nerf@5": (block.get("nerf") or {}).get("@5"),
            "buff@5": (block.get("buff") or {}).get("@5"),
            "nerfCards@10": (block.get("nerfCards") or {}).get("@10"),
        }

    return {
        "generatedAt": result.get("generatedAt"),
        "statistical": pick(result.get("statistical")),
        "model": pick(result.get("model")),
        "url": "/v1/constructed/balance-radar/backtest",
    }
