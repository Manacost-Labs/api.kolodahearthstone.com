from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from starlette.testclient import TestClient

from app.balance_radar import backtest, history, store
from app.balance_radar.analysis import developer_posts, validate_output
from app.balance_radar.pipeline import (
    _trend,
    attach_calibration,
    candidate_counts,
    changes_since_previous,
)
from app.balance_radar.schema import ModelOutput
from app.balance_radar.social import SocialPost, reddit_comment, reddit_post
from app.balance_radar.stats import StatsBundle, attach_mulligan
from app.main import app


def balance_patch(
    version: str,
    day: str,
    meta: list[list[Any]],
    nerfed: list[str],
    buffed: list[str] = (),
) -> dict[str, Any]:
    changes = [
        {
            "id": f"{version}:N{i}",
            "cardId": f"N{i}",
            "dbfId": 100 + i,
            "direction": "nerf",
            "fields": [],
            "archetypes": [{"archetype": name}],
        }
        for i, name in enumerate(nerfed)
    ] + [
        {
            "id": f"{version}:B{i}",
            "cardId": f"B{i}",
            "dbfId": 200 + i,
            "direction": "buff",
            "fields": [],
            "archetypes": [{"archetype": name}],
        }
        for i, name in enumerate(buffed)
    ]
    return {"version": version, "date": day, "changes": changes, "metaBeforeAll": meta}


META = [
    ["Strong", "WARLOCK", 55.0, 12.0],
    ["Popular", "ROGUE", 53.0, 10.0],
    ["Weak", "DRUID", 38.0, 0.5],
    ["Average", "MAGE", 49.0, 2.0],
    ["Average2", "PRIEST", 49.5, 2.5],
]
PATCHES = [
    balance_patch("1.0.2.10", "2026-05-01", META, ["Strong"], ["Weak"]),
    balance_patch("1.2.2.20", "2026-06-01", META, ["Strong", "Popular"]),
    balance_patch("1.4.2.30", "2026-07-01", META, ["Popular"], ["Weak"]),
    balance_patch("1.6.2.40", "2026-08-01", META, ["Strong"]),
]


def test_calibration_counts_how_often_a_band_was_hit() -> None:
    tables = history.calibration_tables(PATCHES)
    assert tables["samples"] == 20 and tables["patches"] == 4
    strong = history.calibrate(tables, "nerfs", 55.0, 12.0)
    assert strong["level"] == "winrate×popularity" and strong["historicalRate"] == 0.75
    assert strong["samples"] == 4 and strong["hits"] == 3
    weak = history.calibrate(tables, "buff", 38.0, 0.5)
    assert weak["historicalRate"] == 0.5
    # A band never seen falls back to the coarser tables (here: popularity).
    unseen = history.calibrate(tables, "nerf", 60.0, 30.0)
    assert unseen["level"] == "popularity" and unseen["samples"] == 8
    assert history.calibrate(tables, "nerf", None, 3.0) is None


def test_patch_probability_is_smoothed_and_timing_reads_the_history() -> None:
    gaps = [14, 16, 17, 19, 21, 23]
    assert history.patch_probability(gaps, 13, 7) == round(
        (4 + 1) / (6 + 2), 3
    )  # 14, 16, 17, 19
    assert history.patch_probability(gaps, 30, 7) is None  # too few longer gaps
    dataset = {
        "patterns": {
            "timing": {
                "balancePatchIntervals": gaps,
                "firstBalancePatchDaysAfterExpansion": [16, 19, 23, 17],
                "lastBalancePatch": "2026-10-01",
            }
        },
        "contentDrops": [
            {"version": "37.0.0.1", "date": "2026-10-12", "kind": "expansion"}
        ],
    }
    outlook = history.timing_outlook(dataset, date(2026, 10, 25))
    assert (
        outlook["daysSinceExpansion"] == 13
        and outlook["balancePatchSinceExpansion"] is False
    )
    assert outlook["daysSinceLastBalancePatch"] == 24
    assert outlook["medianDaysToFirstPatchAfterExpansion"] == 18.0
    assert outlook["nextPatchWithin14Days"] == round((4 + 1) / (4 + 2), 3)
    assert "expansion" in outlook["basis"]


def test_statistical_backtest_is_leave_one_patch_out() -> None:
    result = backtest.statistical_backtest({"patches": list(reversed(PATCHES))})
    assert result["patches"] == 4
    top3 = result["nerf"]["@3"]
    # Strong and Popular are hit in every patch, so they lead the ranking.
    assert top3["actual"] == 5 and top3["hits"] == 5 and top3["recall"] == 1.0
    assert top3["precision"] == round(5 / 12, 3)
    assert all(
        row["nerfTop"][:2] == ["Strong", "Popular"]
        or row["nerfTop"][:2] == ["Popular", "Strong"]
        for row in result["perPatch"]
    )
    summary = backtest.backtest_summary(
        {"generatedAt": "x", "statistical": result, "model": None}
    )
    assert (
        summary["statistical"]["nerf@5"]["recall"] == 1.0 and summary["model"] is None
    )


def candidate(ref: str, **extra: Any) -> dict[str, Any]:
    return {
        "ref": ref,
        "confidence": 60,
        "headline": "h",
        "verdict": "v",
        "reasons": ["r"],
        "suggested_change": "c",
        "sentiment": "negative",
        "community_stance": "wants_nerf",
        "social_summary": "",
        "evidence_post_ids": [],
        "precedent_ids": [],
        "key_numbers": ["58% при 11%"],
        "meta_impact": "m",
        "developer_signal": "",
        "predicted_changes": [],
        "quotes": [],
        **extra,
    }


def test_quotes_must_be_verbatim_and_change_probabilities_capped() -> None:
    output = ModelOutput.model_validate(
        {
            "summary": "s",
            "timing_outlook": "t",
            "nerfs": [
                candidate(
                    "card:A",
                    quotes=[
                        {
                            "post_id": "reddit:1",
                            "text": "«Ritual of Summoning is   broken»",
                        },
                        {"post_id": "reddit:1", "text": "Blizzard confirmed a nerf"},
                        {"post_id": "x:unknown", "text": "anything"},
                    ],
                    predicted_changes=[
                        {
                            "change": "2 → 3",
                            "field": "cost",
                            "before": 2,
                            "after": 3,
                            "probability": 90,
                        },
                        {
                            "change": "3/5 → 3/4",
                            "field": "health",
                            "before": 5,
                            "after": 4,
                            "probability": 60,
                        },
                    ],
                )
            ],
            "buffs": [],
        }
    )
    cleaned, dropped = validate_output(
        output,
        {"card:A"},
        {"reddit:1"},
        set(),
        {"reddit:1": "Honestly ritual of summoning is broken and needs a nerf"},
    )
    item = cleaned.nerfs[0]
    assert [q.text for q in item.quotes] == ["Ritual of Summoning is   broken"]
    assert (
        "nerfs:card:A:quote:reddit:1" in dropped
        and "nerfs:card:A:quote:x:unknown" in dropped
    )
    assert [c.probability for c in item.predicted_changes] == [60, 40]


def test_run_comparison_trends_and_counts() -> None:
    previous = {
        "payload": {
            "runId": "old",
            "generatedAt": "2026-10-24T12:00:00+00:00",
            "nerfs": [
                {"ref": "card:A", "kind": "card", "name": "A", "confidence": 50},
                {"ref": "card:Gone", "kind": "card", "name": "Gone", "confidence": 30},
            ],
            "buffs": [
                {
                    "ref": "class:standard:DRUID",
                    "kind": "class",
                    "name": "Друид",
                    "confidence": 40,
                }
            ],
        }
    }
    payload = {
        "nerfs": [
            {"ref": "card:A", "kind": "card", "name": "A", "confidence": 70},
            {
                "ref": "archetype:standard:Zoolock",
                "kind": "archetype",
                "name": "Zoolock",
                "confidence": 45,
            },
        ],
        "buffs": [
            {
                "ref": "class:standard:DRUID",
                "kind": "class",
                "name": "Друид",
                "confidence": 30,
            }
        ],
    }
    diff = changes_since_previous(payload, previous)
    assert diff["previousRunId"] == "old"
    assert [a["ref"] for a in diff["added"]] == ["archetype:standard:Zoolock"]
    assert [r["ref"] for r in diff["removed"]] == ["card:Gone"]
    assert diff["risen"][0]["delta"] == 20 and diff["fallen"][0]["delta"] == -10
    assert changes_since_previous(payload, None) is None
    assert candidate_counts(payload) == {
        "nerfs": {"total": 2, "card": 1, "archetype": 1, "class": 0},
        "buffs": {"total": 1, "card": 0, "archetype": 0, "class": 1},
    }
    assert _trend(70, 50) == {"status": "up", "previousConfidence": 50, "delta": 20}
    assert _trend(48, 50)["status"] == "stable" and _trend(40, None)["status"] == "new"


def test_calibration_attaches_comparable_features() -> None:
    bundle = StatsBundle(format="standard")
    bundle.entities = {
        "card:A": {
            "kind": "card",
            "stats": {
                "winrate": 56.0,
                "popularity": 20.0,
                "core": [{"archetype": "Strong", "winrate": 55.0, "popularity": 12.0}],
            },
        },
        "archetype:standard:Z": {
            "kind": "archetype",
            "stats": {
                "winrate": 63.0,
                "popularity": 9.0,
                "legendWinrate": 55.0,
                "legendPopularity": 12.0,
            },
        },
    }
    attach_calibration(bundle, history.calibration_tables(PATCHES), 0.8)
    card = bundle.entities["card:A"]["calibration"]
    assert card["features"]["source"].startswith(
        "HSReplay Legend core archetype Strong"
    )
    assert (
        card["nerf"]["historicalRate"] == 0.75
        and card["nerf"]["withPatchIn14Days"] == 0.6
    )
    assert (
        bundle.entities["archetype:standard:Z"]["calibration"]["features"]["source"]
        == "HSGuru Legend"
    )


def test_mulligan_impacts_reach_archetypes_and_cards() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE hsguru_stat_targets (id INTEGER PRIMARY KEY, latest_snapshot_id INTEGER);
        CREATE TABLE hsguru_stat_snapshots (id INTEGER PRIMARY KEY, kind TEXT, subject_type TEXT, format TEXT,
            archetype TEXT, period TEXT, rank TEXT, coin TEXT, opponent_class TEXT, games INTEGER, fetched_at TEXT);
        CREATE TABLE hsguru_mulligan_cards (snapshot_id INTEGER, card_id TEXT, dbf_id INTEGER, card_name TEXT,
            mulligan_impact REAL, mulligan_count INTEGER, drawn_impact REAL, drawn_count INTEGER, kept_impact REAL, kept_count INTEGER);
        INSERT INTO hsguru_stat_targets VALUES (1, 10), (2, 11);
        INSERT INTO hsguru_stat_snapshots VALUES
          (10, 'cards', 'archetype', 'standard', 'Zoolock', 'black_empire_ban', 'all', 'any_player', 'any', 170000, '2026-10-25T11:00:00'),
          (11, 'cards', 'archetype', 'standard', 'Zoolock', 'past_week', 'all', 'any_player', 'any', 90000, '2026-10-25T10:00:00');
        INSERT INTO hsguru_mulligan_cards VALUES
          (10, 'BE_085', 129793, 'Ritual of Summoning', 5.7, 63267, -2.5, 169040, 5.6, 41217),
          (10, 'X', 1, 'Junk', 9.0, 3, 9.0, 3, 9.0, 3),
          (11, 'BE_085', 129793, 'Ritual of Summoning', 1.0, 9000, 1.0, 9000, 1.0, 9000);
        """
    )
    bundle = StatsBundle(
        format="standard",
        periods={"ban": "black_empire_ban", "current": "black_empire"},
    )
    bundle.archetypes = [{"name": "Zoolock", "stats": {}}]
    bundle.card_nerf_pool = [{"dbfId": 129793, "stats": {}}]
    attach_mulligan(bundle, conn)
    mulligan = bundle.archetypes[0]["stats"]["mulligan"]
    assert (
        mulligan["period"] == "black_empire_ban"
    )  # the ban window wins over past_week
    assert [c["name"] for c in mulligan["keyCards"]] == [
        "Ritual of Summoning"
    ]  # tiny samples dropped
    assert bundle.card_nerf_pool[0]["stats"]["mulligan"][0]["mulliganImpact"] == 5.7
    assert bundle.sources[0]["id"] == "hsguru_mulligan"
    attach_mulligan(
        StatsBundle(format="wild"), sqlite3.connect(":memory:")
    )  # missing tables: no error


def test_developer_posts_are_flagged_and_support_replies_ignored() -> None:
    blizzard = reddit_post(
        {
            "id": "b1",
            "subreddit": "hearthstone",
            "title": "Balance changes coming",
            "upvotes": 900,
            "link_flair_text": "Blizzard",
        }
    )
    designer = reddit_post(
        {
            "id": "d1",
            "subreddit": "hearthstone",
            "title": "My thoughts",
            "upvotes": 50,
            "author_info": {"flair": {"text": "Game Designer"}},
        }
    )
    assert blizzard.official and blizzard.author_role == "official"
    assert designer.author_role == "developer"
    parent = reddit_post(
        {"id": "p1", "subreddit": "hearthstone", "title": "Nerf Ritual", "upvotes": 10}
    )
    comment = reddit_comment(
        {
            "id": "c1",
            "body": "We are watching Ritual.",
            "author_flair_text": "Blizzard Community Manager",
        },
        parent,
    )
    assert comment.author_role == "developer"
    support = SocialPost(
        id="x:1",
        platform="x",
        kind="tweet",
        url="u",
        author="PlayHearthstone",
        score=1,
        created_at="2026-10-25T10:00:00+00:00",
        community="x",
        text="@user This is a known issue",
        official=True,
        author_role="official",
    )
    statement = SocialPost(
        id="x:2",
        platform="x",
        kind="tweet",
        url="u",
        author="PlayHearthstone",
        score=1,
        created_at="2026-10-24T10:00:00+00:00",
        community="x",
        text="Our team has been keeping an eye on recent gameplay and balance",
        official=True,
        author_role="official",
    )
    found = developer_posts([support, statement, blizzard, designer, parent])
    assert [p.id for p in found] == ["x:2", "reddit:b1", "reddit:d1"] or {
        p.id for p in found
    } == {"x:2", "reddit:b1", "reddit:d1"}


def test_confidence_series_and_new_endpoints(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    assert client.get("/v1/constructed/balance-radar/backtest").status_code == 503
    for i, confidence in enumerate((40, 55)):
        store.save_run(
            run_id=f"20261025T1{i}0000Z-standard-aaaaaa",
            format_name="standard",
            status="ok",
            started_at=f"2026-10-25T1{i}:00:00+00:00",
            payload={
                "generatedAt": f"2026-10-25T1{i}:05:00+00:00",
                "nerfs": [{"ref": "card:A", "confidence": confidence}],
                "buffs": [],
            },
        )
    series = store.confidence_series("standard")
    assert [p["confidence"] for p in series[("nerfs", "card:A")]] == [40, 55]
    body = client.get(
        "/v1/constructed/balance-radar/confidence-history",
        params={"format": "standard", "ref": "card:A"},
    ).json()
    assert (
        body["data"]["series"][0]["points"][-1]["confidence"] == 55
        and body["meta"]["count"] == 1
    )

    backtest.save_backtest(
        backtest.run_backtest({"patches": list(reversed(PATCHES)), "generatedAt": "h"})
    )
    result = client.get("/v1/constructed/balance-radar/backtest").json()
    assert result["data"]["statistical"]["patches"] == 4
    assert (
        json.loads(backtest.backtest_path().read_text())["statistical"]["nerf"]["@3"][
            "recall"
        ]
        == 1.0
    )
