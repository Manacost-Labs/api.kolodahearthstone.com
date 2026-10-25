from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from app.balance_radar import history
from app.balance_radar.history import (
    ArchetypeStats,
    build_history,
    diff_builds,
    find_precedents,
    normalize_text,
    numeric_direction,
)
from app.main import app

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def card(dbf: int, card_id: str, **fields: Any) -> dict[str, Any]:
    base = {
        "id": card_id,
        "dbfId": dbf,
        "name": card_id.title(),
        "type": "MINION",
        "set": "OLD",
        "cardClass": "WARLOCK",
        "rarity": "COMMON",
        "cost": 2,
        "attack": 3,
        "health": 3,
        "durability": None,
        "armor": None,
        "text": "",
    }
    return {**base, **fields}


# Build 100: baseline. 200: expansion "NEW" (3 cards). 300: balance patch.
BUILDS: dict[int, dict[int, dict[str, Any]]] = {
    100: {
        1: card(1, "RITUAL"),
        2: card(2, "AXE", type="WEAPON", attack=3, health=2, durability=None),
        3: card(
            3, "SPELLY", type="SPELL", attack=None, health=None, text="Deal 5 damage."
        ),
    },
}
BUILDS[200] = {
    **BUILDS[100],
    2: card(
        2, "AXE", type="WEAPON", attack=3, health=None, durability=2
    ),  # field moved: not a change
    3: card(
        3, "SPELLY", type="SPELL", attack=None, health=None, text="Deal 5&damage."
    ),  # markup only
    10: card(10, "NEWA", set="NEW"),
    11: card(11, "NEWB", set="NEW", cardClass="DRUID"),
    12: card(12, "NEWC", set="NEW"),
}
BUILDS[300] = {
    **BUILDS[200],
    1: card(1, "RITUAL", cost=3),  # nerf
    10: card(10, "NEWA", set="NEW", attack=2, health=2),  # nerf
    11: card(11, "NEWB", set="NEW", cardClass="DRUID", cost=1),  # buff
    3: card(
        3, "SPELLY", type="SPELL", attack=None, health=None, text="Deal 4 damage."
    ),  # rework
}
PATCHES = [
    {
        "version": "1.0.0.100",
        "build": 100,
        "date": "2026-07-01",
        "title": "base",
        "titleRu": None,
        "officialUrl": None,
        "wikiUrl": "https://wiki/1",
        "manacostUrl": None,
        "devComment": None,
    },
    {
        "version": "2.0.0.200",
        "build": 200,
        "date": "2026-07-10",
        "title": "Expansion",
        "titleRu": "Дополнение",
        "officialUrl": None,
        "wikiUrl": "https://wiki/2",
        "manacostUrl": None,
        "devComment": None,
    },
    {
        "version": "2.0.3.300",
        "build": 300,
        "date": "2026-07-31",
        "title": "Balance",
        "titleRu": "Баланс",
        "officialUrl": "https://news/300",
        "wikiUrl": None,
        "manacostUrl": None,
        "devComment": "Ритуал слишком силён.",
    },
]


def stats_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE hsreplay_archetypes (archetype_id INTEGER PRIMARY KEY, name TEXT, player_class TEXT);
        CREATE TABLE archetype_snapshots (id INTEGER PRIMARY KEY, run_id INTEGER, archetype_id INTEGER,
            game_type TEXT, rank_range TEXT, fetched_at TEXT);
        CREATE TABLE archetype_time_series (snapshot_id INTEGER, series_name TEXT, point_date TEXT, value REAL);
        CREATE TABLE archetype_decks (id INTEGER PRIMARY KEY, snapshot_id INTEGER, total_games INTEGER);
        CREATE TABLE archetype_deck_cards (archetype_deck_id INTEGER, dbf_id INTEGER, sideboard INTEGER);
        INSERT INTO hsreplay_archetypes VALUES (7, 'Ritual Warlock', 'WARLOCK'), (8, 'Big Druid', 'DRUID');
        INSERT INTO archetype_snapshots VALUES (1, 1, 7, 'RANKED_STANDARD', 'LEGEND', '2026-07-25T00:00:00'),
                                               (2, 1, 8, 'RANKED_STANDARD', 'LEGEND', '2026-07-25T00:00:00');
        INSERT INTO archetype_decks VALUES (1, 1, 900), (2, 1, 100), (3, 2, 500);
        INSERT INTO archetype_deck_cards VALUES (1, 1, 0), (1, 10, 0), (2, 10, 0), (3, 11, 0);
        """
    )
    start = date(2026, 7, 20)
    for offset in range(20):
        day = (start + timedelta(days=offset)).isoformat()
        before = day < "2026-07-31"
        conn.execute(
            "INSERT INTO archetype_time_series VALUES (1, 'winrates_over_time', ?, ?)",
            (day, 57.0 if before else 52.0),
        )
        conn.execute(
            "INSERT INTO archetype_time_series VALUES (1, 'popularity_over_time', ?, ?)",
            (day, 12.0 if before else 6.0),
        )
        conn.execute(
            "INSERT INTO archetype_time_series VALUES (2, 'winrates_over_time', ?, ?)",
            (day, 45.0 if before else 48.0),
        )
        conn.execute(
            "INSERT INTO archetype_time_series VALUES (2, 'popularity_over_time', ?, ?)",
            (day, 1.0),
        )
    return conn


def build(**kwargs: Any) -> dict[str, Any]:
    return build_history(
        patches=PATCHES,
        stats=ArchetypeStats(stats_db()),
        builds=sorted(BUILDS),
        loader=BUILDS.get,
        ru_names={"RITUAL": "Ритуал"},
        now=NOW,
        **kwargs,
    )


def test_text_normalization_drops_markup_only_differences() -> None:
    assert (
        normalize_text("<b>Battlecry:</b> Deal 5&damage.~GAMEPLAY ASPROXY")
        == "Battlecry: Deal 5 damage."
    )
    assert normalize_text("[x]Draw\na card_") == "Draw a card"


@pytest.mark.parametrize(
    ("fields", "text", "expected"),
    [
        ([{"field": "cost", "before": 2, "after": 3}], False, "nerf"),
        ([{"field": "attack", "before": 3, "after": 2}], False, "nerf"),
        ([{"field": "cost", "before": 3, "after": 2}], False, "buff"),
        ([{"field": "health", "before": 3, "after": 4}], False, "buff"),
        (
            [
                {"field": "cost", "before": 2, "after": 3},
                {"field": "attack", "before": 3, "after": 5},
            ],
            False,
            "mixed",
        ),
        ([], True, "rework"),
    ],
)
def test_numeric_direction(fields, text, expected) -> None:
    assert numeric_direction(fields, text) == expected


def test_build_diff_ignores_field_moves_and_markup() -> None:
    assert diff_builds(BUILDS[100], BUILDS[200]) == []
    changes = {c["cardId"]: c for c in diff_builds(BUILDS[200], BUILDS[300])}
    assert changes["RITUAL"]["fields"] == [{"field": "cost", "before": 2, "after": 3}]
    assert changes["SPELLY"]["direction"] == "rework"
    assert changes["SPELLY"]["textAfter"] == "Deal 4 damage."


def test_history_attributes_changes_and_measures_timing_and_stats() -> None:
    dataset = build(label=False)

    assert [p["version"] for p in dataset["patches"]] == ["2.0.3.300"]
    patch = dataset["patches"][0]
    assert (
        patch["lastContentDrop"] == "2.0.0.200" and patch["daysSinceContentDrop"] == 21
    )
    assert patch["counts"] == {"buff": 1, "nerf": 2, "rework": 1}
    ritual = next(c for c in patch["changes"] if c["cardId"] == "RITUAL")
    assert (
        ritual["name"] == "Ритуал" and ritual["setAgeDays"] is None
    )  # set older than coverage
    newa = next(c for c in patch["changes"] if c["cardId"] == "NEWA")
    assert newa["setAgeDays"] == 21 and newa["setReleasedAt"] == "2026-07-10"
    core = ritual["archetypes"][0]
    assert core["archetype"] == "Ritual Warlock" and core["cardShare"] == 0.9
    assert core["before"] == {"winrate": 57.0, "popularity": 12.0}
    assert core["after"] == {"winrate": 52.0, "popularity": 6.0}
    # NEWA sits in 100% of Ritual Warlock decks too; Big Druid runs NEWB.
    assert [a["archetype"] for a in newa["archetypes"]] == ["Ritual Warlock"]
    assert patch["metaBefore"][0]["archetype"] == "Ritual Warlock"
    assert dataset["contentDrops"][0]["version"] == "2.0.0.200"
    assert dataset["contentDrops"][0]["kind"] == "expansion"

    patterns = dataset["patterns"]
    assert patterns["changes"] == {"buff": 1, "nerf": 2, "rework": 1}
    assert patterns["preStats"]["nerf"]["winrate"]["median"] == 57.0
    assert patterns["preStats"]["buff"]["winrate"]["median"] == 45.0
    assert patterns["effect"]["nerf"]["median"] == -5.0
    assert (
        patterns["daysFromContentDropToFirstBalancePatch"]["expansion"]["median"]
        == 21.0
    )
    assert patterns["changeKinds"]["nerf"] == {"cost": 1, "stats": 1}
    assert patterns["newSetShare"]["nerf"] == 0.5


class FakeClaude:
    def __init__(self) -> None:
        self.calls = 0
        self.label_calls = 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **request: Any) -> Any:
        self.calls += 1
        payload = json.loads(request["messages"][0]["content"])
        if request["output_format"] is history.LabelsOutput:
            self.label_calls += 1
            output: dict[str, Any] = {
                "labels": [
                    {
                        "change_id": c["id"],
                        "direction": "nerf",
                        "summary": "Урон 5 → 4.",
                    }
                    for c in payload["changes"]
                ]
            }
        else:
            assert request["output_format"] is history.NarrativeOutput
            assert payload["computedPatterns"]["changes"] == {
                "buff": 1,
                "nerf": 3,
            }  # labels applied first
            output = {
                "patches": [
                    {
                        "version": "2.0.3.300",
                        "summary": "Ослабили Ритуал.",
                        "targeted_archetypes": ["Ritual Warlock"],
                    }
                ],
                "summary": "Blizzard нерфит колоды с винрейтом около 57%.",
                "patterns": [{"title": "Порог", "detail": "57% при 12% популярности."}],
            }
        message = SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text", text=json.dumps(output, ensure_ascii=False)
                )
            ],
            stop_reason="end_turn",
            model="claude-sonnet-5-5",
            usage=SimpleNamespace(
                input_tokens=1000,
                output_tokens=500,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                iterations=[],
            ),
        )

        class Stream:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def get_final_message(self) -> Any:
                return message

        return Stream()


def test_model_labels_reworks_and_is_reused_when_inputs_are_unchanged(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    claude = FakeClaude()
    first = build(claude=claude)
    spelly = next(c for c in first["patches"][0]["changes"] if c["cardId"] == "SPELLY")
    assert spelly["direction"] == "nerf" and spelly["labelledDirection"] == "nerf"
    assert spelly["summary"] == "Урон 5 → 4."
    assert first["patches"][0]["summary"] == "Ослабили Ритуал."
    assert first["patterns"]["changes"] == {"buff": 1, "nerf": 3}
    assert first["narrative"]["patterns"][0]["title"] == "Порог"
    assert claude.calls == 2 and claude.label_calls == 1
    assert first["usage"]["costUsd"] == 0.014 and first["usage"]["labels"]["calls"] == 1

    second = build(claude=claude, previous=first)
    assert claude.calls == 2 and second["narrativeReused"] is True
    assert second["patches"][0]["summary"] == "Ослабили Ритуал."
    assert (
        next(c for c in second["patches"][0]["changes"] if c["cardId"] == "SPELLY")[
            "direction"
        ]
        == "nerf"
    )


def test_precedents_prefer_the_same_card_then_similar_stats() -> None:
    changes = history.all_changes(build(label=False))
    same_card = find_precedents(
        {"kind": "card", "id": "RITUAL", "class": "WARLOCK", "stats": {}},
        "nerfs",
        changes,
    )
    assert same_card[0][0]["cardId"] == "RITUAL" and "same_card" in same_card[0][1]

    similar = find_precedents(
        {
            "kind": "archetype",
            "name": "Zoolock",
            "class": "WARLOCK",
            "stats": {"legendWinrate": 58.0, "legendPopularity": 10.0},
        },
        "nerfs",
        changes,
    )
    assert similar and {"similar_winrate", "similar_popularity"} <= set(similar[0][1])
    # A buff never answers a nerf question unless it is the same card.
    assert all(change["direction"] == "nerf" for change, _ in similar)
    view = history.precedent_view(similar[0][0], similar[0][1])
    assert (
        view["id"].startswith("precedent:2.0.3.300:")
        and view["preStats"]["winrate"] == 57.0
    )
    assert view["url"] == "https://news/300"


def test_history_endpoints_filter_and_report_patterns(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    assert client.get("/v1/constructed/balance-radar/history").status_code == 503
    assert client.get("/v1/constructed/balance-radar/patterns").status_code == 503

    history.save_history(build(label=False))
    body = client.get("/v1/constructed/balance-radar/history").json()
    assert (
        body["meta"]["count"] == 1
        and body["data"]["patches"][0]["version"] == "2.0.3.300"
    )
    only = client.get(
        "/v1/constructed/balance-radar/history", params={"card": "ritual"}
    ).json()
    assert [c["cardId"] for c in only["data"]["patches"][0]["changes"]] == ["RITUAL"]
    buffs = client.get(
        "/v1/constructed/balance-radar/history",
        params={"direction": "buff", "class": "druid"},
    ).json()
    assert [c["cardId"] for c in buffs["data"]["patches"][0]["changes"]] == ["NEWB"]
    none = client.get(
        "/v1/constructed/balance-radar/history", params={"since": "2026-08-01"}
    ).json()
    assert none["meta"]["count"] == 0
    patterns = client.get("/v1/constructed/balance-radar/patterns").json()
    assert patterns["data"]["patterns"]["balancePatches"] == 1
    assert patterns["meta"]["source_id"] == "balance_radar"


def test_ensure_history_keeps_the_previous_dataset_when_the_rebuild_fails(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    old = {**build(label=False), "generatedAt": (NOW - timedelta(days=30)).isoformat()}
    history.save_history(old)
    monkeypatch.setattr(history, "load_patches", lambda: PATCHES)

    def broken(**_kwargs: Any) -> dict[str, Any]:
        raise history.HistoryError("HearthstoneJSON unavailable")

    monkeypatch.setattr(history, "build_history", broken)
    assert history.ensure_history(now=NOW)["generatedAt"] == old["generatedAt"]

    fresh = {**old, "generatedAt": NOW.isoformat()}
    history.save_history(fresh)
    monkeypatch.setattr(
        history,
        "build_history",
        lambda **_k: pytest.fail("fresh history must not rebuild"),
    )
    assert (
        history.ensure_history(now=NOW + timedelta(days=1))["generatedAt"]
        == fresh["generatedAt"]
    )


def test_documented_history_examples_match_the_endpoints() -> None:
    from pathlib import Path

    examples = Path(__file__).resolve().parent.parent / "docs" / "examples"
    patterns = json.loads(
        (examples / "balance-radar-patterns.json").read_text(encoding="utf-8")
    )
    assert patterns["meta"]["source_id"] == "balance_radar"
    assert {
        "balancePatches",
        "preStats",
        "changeKinds",
        "daysBetweenBalancePatches",
    } <= set(patterns["data"]["patterns"])
    assert patterns["data"]["narrative"]["patterns"][0]["title"]
    sample = json.loads(
        (examples / "balance-radar-history.json").read_text(encoding="utf-8")
    )
    change = sample["data"]["patches"][0]["changes"][0]
    assert {"id", "cardId", "direction", "fields", "setAgeDays", "archetypes"} <= set(
        change
    )
