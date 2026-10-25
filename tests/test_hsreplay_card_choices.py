from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from app import card_choice_pools as pools
from app import hsreplay_card_choices as choices
from app import hsreplay_card_choices_store as store
from app.hsreplay_mulligan import ACCOUNT_API, FetchResult

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/hsreplay_card_choices"
# A trimmed HearthstoneJSON base (every Outcast card, Choose One, Adapt and
# Excavate samples) and the live Legend answer for Illidari Studies (2026-10-25).
CARD_FIXTURE = json.loads((FIXTURES / "cards.json").read_text())
ILLIDARI_LEGEND = json.loads((FIXTURES / "illidari_studies_legend.json").read_text())
NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
ILLIDARI = "CORE_YOP_001"
ILLIDARI_DBF = 97377
COSMIC_MANIFESTATIONS = 127159
BYGONE_ECHOES = 121060
STANDARD_OUTCAST = {
    "BE_264",
    "CATA_533",
    "CORE_BT_480",
    "CORE_BT_491",
    "CORE_BT_801",
    "DINO_136",
    "END_005",
    "JAIL_892",
    "TIME_021",
}


@pytest.fixture(scope="module")
def base() -> pools.CardBase:
    return pools.CardBase(
        CARD_FIXTURE["cards"],
        standard_sets=CARD_FIXTURE["standard_sets"],
        names_ru=CARD_FIXTURE["names_ru"],
    )


# --- card base -------------------------------------------------------------------


def test_choice_kinds_follow_mechanics(base) -> None:
    kinds = {
        card: pools.choice_kind(base.by_id[card])
        for card in (
            ILLIDARI,
            "CORE_AT_037",
            "Core_LOE_115",
            "UNG_001",
            "DEEP_033",
            "CORE_BT_801",
        )
    }
    assert kinds == {
        ILLIDARI: "discover",
        "CORE_AT_037": "choose_one",
        # Choose One between two Discovers: HSReplay tracks the Discover.
        "Core_LOE_115": "discover",
        "UNG_001": "adapt",
        "DEEP_033": "excavate",
        "CORE_BT_801": None,
    }
    standard = {row["card_id"]: row for row in pools.choice_cards(base, "standard")}
    assert ILLIDARI in standard and "UNG_001" not in standard
    assert standard[ILLIDARI]["has_hsreplay_stats"] is True
    assert standard["CORE_AT_037"]["has_hsreplay_stats"] is False


def test_illidari_studies_pool_matches_hsreplay_offers(base) -> None:
    pool = pools.card_pool(base, base.by_id[ILLIDARI], "standard")
    assert pool["resolved"] is True and pool["class_dependent"] is False
    assert {card["card_id"] for card in pool["cards"]} == STANDARD_OUTCAST
    offered = {row["dbf_id"] for row in ILLIDARI_LEGEND["series"]["data"]["ALL"]}
    assert {card["dbf_id"] for card in pool["cards"]} == offered - {None}
    wild = pools.card_pool(base, base.by_id[ILLIDARI], "wild")
    assert len(wild["cards"]) > len(pool["cards"])
    # Core reprints collapse onto one entry per name.
    names = [card["name"] for card in wild["cards"]]
    assert len(names) == len(set(names))


@pytest.mark.parametrize(
    ("clause", "expected"),
    [
        ("a minion that costs (5) or more", {"type": "MINION", "cost_min": 5}),
        (
            "a 1-Cost spell from any class",
            {"type": "SPELL", "cost": 1, "class_rule": "any"},
        ),
        (
            "a Choose One card from another class",
            {"keywords_any": ["CHOOSE_ONE"], "class_rule": "another"},
        ),
        (
            "a Hunter, Paladin, or Warrior card",
            {"classes": ["HUNTER", "PALADIN", "WARRIOR"]},
        ),
        (
            "a Legendary Beast",
            {"rarity": "LEGENDARY", "races_any": ["BEAST"], "type": "MINION"},
        ),
        ("a Fel spell", {"school": "FEL", "type": "SPELL"}),
        ("a Demon Hunter card", {"classes": ["DEMONHUNTER"]}),
    ],
)
def test_discover_rules(clause: str, expected: dict[str, Any]) -> None:
    rule = pools.parse_discover_rule(clause, source_classes=["MAGE"])
    assert rule["resolved"] is True, rule
    assert {key: rule.get(key) for key in expected} == expected


@pytest.mark.parametrize(
    ("clause", "reason"),
    [
        ("a card from your deck", "game_state"),
        ("a copy of a card in your opponent's hand", "game_state"),
        ("a Blood, Frost or Unholy card", "unparsed"),
    ],
)
def test_discover_rules_that_depend_on_the_game_are_not_guessed(clause, reason) -> None:
    rule = pools.parse_discover_rule(clause, source_classes=["MAGE"])
    assert rule["resolved"] is False and rule["reason"] == reason


def test_choose_one_adapt_and_excavate_pools(base) -> None:
    roots = pools.card_pool(base, base.by_id["CORE_AT_037"], "standard")
    # The Core reprint finds the original's options (AT_037a/b).
    assert [card["card_id"] for card in roots["cards"]] == ["AT_037a", "AT_037b"]
    adapt = pools.card_pool(base, base.by_id["UNG_001"], "wild")
    assert len(adapt["cards"]) == 10
    excavate = pools.card_pool(base, base.by_id["DEEP_033"], "wild")
    assert [tier["tier"] for tier in excavate["tiers"]] == [1, 2, 3, 4]
    legendary = excavate["tiers"][3]["cards"]
    # Tier 4 is the Paladin's own treasure only.
    assert [card["card_class"] for card in legendary] == ["PALADIN"]
    tracking = pools.card_pool(base, base.by_id["CORE_DS1_184"], "standard")
    assert tracking["resolved"] is False and tracking["reason"] == "game_state"


# --- HSReplay answer -------------------------------------------------------------


def test_parse_drops_the_unnamed_row_and_sorts_by_popularity(base) -> None:
    rows, unidentified, as_of = choices.parse_choices_payload(
        ILLIDARI_LEGEND, cards_by_dbf=base.by_dbf
    )
    assert unidentified == 1 and len(rows) == 9
    assert as_of == ILLIDARI_LEGEND["as_of"]
    assert rows[0] == {
        "dbf_id": 118503,
        "card_id": "DINO_136",
        "popularity": 44.98,
        "win_rate": 45.26,
    }
    with pytest.raises(choices.PayloadRejected):
        choices.parse_choices_payload({"series": {"data": {}}})


def test_options_flag_thin_samples_and_undervalued_picks() -> None:
    rows, _, _ = choices.parse_choices_payload(ILLIDARI_LEGEND)
    options = [dict(row) for row in rows] + [
        {"dbf_id": 1, "popularity": 100.0, "win_rate": 0.0}
    ]
    choices._mark_options(options)
    undervalued = {row["dbf_id"] for row in options if row["undervalued"]}
    # Higher winrate than average, taken less often than the median option.
    assert undervalued == {COSMIC_MANIFESTATIONS, BYGONE_ECHOES}
    assert [row["dbf_id"] for row in options if row["low_sample"]] == [1]


# --- plan ------------------------------------------------------------------------


def _played(base: pools.CardBase) -> dict[str, dict[int, int]]:
    return {
        "standard": {ILLIDARI_DBF: 900, 113109: 500, 69591: 10},
        "wild": {41076: 50},
    }


def test_plan_takes_the_most_played_tracked_cards(base) -> None:
    settings = choices.Settings(standard_cards=1, wild_cards=1)
    planned = choices.plan_targets(base, settings, times_played=_played(base))
    assert [(t["format"], t["rank"], t["card_id"]) for t in planned] == [
        ("standard", "legend", ILLIDARI),
        ("standard", "bronze_gold", ILLIDARI),
        ("wild", "legend", "UNG_001"),
        ("wild", "bronze_gold", "UNG_001"),
    ]
    estimate = choices.estimate_plan(planned)
    assert estimate["requests_per_week_by_ttl"] == 4.0


def test_targets_are_due_weekly_and_once_after_a_patch() -> None:
    target = {"last_success_at": (NOW - timedelta(days=3)).isoformat()}
    assert choices.due_score(target, NOW) is None
    assert choices.due_score(target, NOW + timedelta(days=4, minutes=1)) is not None
    patch = NOW - timedelta(days=2)
    # Read before the patch settled: due again once.
    assert choices.due_score(target, NOW, patch) == 1e6
    fresh = {"last_success_at": NOW.isoformat()}
    assert choices.due_score(fresh, NOW + timedelta(hours=1), patch) is None
    assert choices.due_score({}, NOW) == float("inf")


# --- refresh ---------------------------------------------------------------------


@pytest.fixture()
def isolated(tmp_path, monkeypatch, base):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.card_choice_pools.load_card_base", lambda: base)
    monkeypatch.setattr(
        "app.hsreplay_client._hsreplay_cookie_header", lambda: "sessionid=s"
    )
    alerts: list[str] = []

    async def alert(source_id: str, state: str, detail: str, url: str) -> None:
        alerts.append(detail)

    monkeypatch.setattr("app.fetcher.send_telegram_alert", alert)
    return alerts


class FakeHsReplay:
    def __init__(
        self,
        *,
        answers: dict[tuple[int, str], FetchResult] | None = None,
        account: FetchResult | None = None,
    ) -> None:
        self.answers = answers or {}
        self.account = account or FetchResult(
            "ok", payload={"is_premium": True}, credits=1
        )
        self.calls: list[str] = []

    async def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        if url == ACCOUNT_API:
            return self.account
        query = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
        key = (int(query["card_id"]), query["LeagueRankRange"])
        return self.answers.get(
            key, FetchResult("ok", payload=ILLIDARI_LEGEND, credits=1)
        )

    def choice_calls(self) -> list[str]:
        return [url for url in self.calls if url != ACCOUNT_API]


def _refresh(base, fake, *, now=NOW, settings=None, patch_at=None):
    return asyncio.run(
        choices.refresh_hsreplay_card_choices(
            settings=settings or choices.Settings(standard_cards=2, wild_cards=0),
            call_json=fake,
            now=now,
            base=base,
            times_played=_played(base),
            patch_at=patch_at,
        )
    )


def _rows(table: str) -> list[dict[str, Any]]:
    from app.db import get_db_connection

    conn = get_db_connection()
    try:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
    finally:
        conn.close()


def test_refresh_stores_snapshots_only_on_change(isolated, base) -> None:
    fake = FakeHsReplay(
        answers={
            (113109, "BRONZE_THROUGH_GOLD"): FetchResult(
                "no_data", status=204, credits=1
            )
        }
    )
    result = _refresh(base, fake)
    assert result["state"] == "ok" and result["published"] is True
    assert result["attempted"] == 4 and result["no_data"] == 1
    assert result["scrape_do_credits"] == 5  # four targets plus the session probe
    assert all("GameType=RANKED_STANDARD" in url for url in fake.choice_calls())
    targets = {(row["card_id"], row["rank"]): row for row in _rows(store.TARGETS)}
    assert targets[("Core_LOE_115", "bronze_gold")]["state"] == "no_data"
    assert len(_rows(store.SNAPSHOTS)) == 4
    assert len(_rows(store.ROWS)) == 27

    # Nothing is due before the weekly TTL; an identical re-read adds no snapshot.
    assert _refresh(base, FakeHsReplay(), now=NOW + timedelta(days=1))["attempted"] == 0
    again = _refresh(base, fake, now=NOW + timedelta(days=8))
    assert again["unchanged"] == 4 and again["changed"] == 0
    assert len(_rows(store.SNAPSHOTS)) == 4


def test_refresh_respects_the_daily_credit_cap(isolated, base) -> None:
    settings = choices.Settings(
        standard_cards=2, wild_cards=0, scrape_do_daily_credits=3
    )
    result = _refresh(base, FakeHsReplay(), settings=settings)
    assert (
        result["scrape_do_credits"] == 3 and result["stop_reason"] == "scrape_do_budget"
    )
    later = _refresh(
        base, FakeHsReplay(), settings=settings, now=NOW + timedelta(hours=2)
    )
    assert later["scrape_do_credits"] == 0


def test_a_rejected_session_stops_the_run_and_alerts(isolated, base) -> None:
    fake = FakeHsReplay(account=FetchResult("denied", status=502))
    result = _refresh(base, fake)
    assert result["premium_session"]["state"] == "denied"
    assert result["stop_reason"] == "premium_session" and fake.choice_calls() == []
    assert result["state"] == "partial" and len(isolated) == 1


# --- read API --------------------------------------------------------------------


@pytest.fixture()
def client(isolated, base):
    _refresh(base, FakeHsReplay())
    from app.main import app

    return TestClient(app)


def test_card_choices_endpoint_contract(client) -> None:
    response = client.get("/v1/hsreplay/card-choices", params={"card": ILLIDARI})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["source_id"] == "hsreplay_card_choices"
    assert body["meta"]["count"] == 9
    data = body["data"]
    assert data["format"] == "standard" and data["rank"] == "legend"
    assert data["card"]["kind"] == "discover" and data["card"]["name_ru"]
    assert data["stats"]["state"] == "ok" and data["stats"]["unidentified_rows"] == 1
    assert data["stats"]["source_url"] == "https://hsreplay.net/cards/97377/"
    first = data["options"][0]
    assert first["card_id"] == "DINO_136" and first["in_pool"] is True
    assert {"popularity", "win_rate", "low_sample", "undervalued", "name_ru"} <= set(
        first
    )
    assert data["pool"]["resolved"] is True and data["pool"]["total"] == 9
    assert all(card["has_stats"] for card in data["pool"]["cards"])
    assert {"format": "standard", "rank": "bronze_gold"} in data["available"]

    by_dbf = client.get("/v1/hsreplay/card-choices", params={"card": str(ILLIDARI_DBF)})
    assert by_dbf.json()["data"]["card"]["card_id"] == ILLIDARI


def test_untracked_and_unknown_cards(client) -> None:
    roots = client.get(
        "/v1/hsreplay/card-choices", params={"card": "CORE_AT_037"}
    ).json()
    assert roots["data"]["stats"]["state"] == "not_tracked"
    assert [card["has_stats"] for card in roots["data"]["pool"]["cards"]] == [
        False,
        False,
    ]
    wild = client.get(
        "/v1/hsreplay/card-choices", params={"card": ILLIDARI, "format": "wild"}
    ).json()["data"]
    assert wild["stats"]["state"] == "not_collected" and wild["options"] == []
    assert all(card["has_stats"] is False for card in wild["pool"]["cards"])
    excavate = client.get(
        "/v1/hsreplay/card-choices", params={"card": "DEEP_033"}
    ).json()
    assert (
        excavate["data"]["format"] == "wild"
        and len(excavate["data"]["pool"]["tiers"]) == 4
    )
    assert (
        client.get(
            "/v1/hsreplay/card-choices", params={"card": "CORE_BT_801"}
        ).status_code
        == 404
    )
    assert (
        client.get("/v1/hsreplay/card-choices", params={"card": "NOPE_1"}).status_code
        == 404
    )


def test_card_list_and_coverage(client) -> None:
    cards = client.get(
        "/v1/hsreplay/card-choices/cards", params={"format": "standard"}
    ).json()
    row = next(item for item in cards["data"] if item["card_id"] == ILLIDARI)
    assert {item["rank"] for item in row["collected"]} == {"legend", "bronze_gold"}
    coverage = client.get("/v1/hsreplay/card-choices/coverage").json()["data"]
    assert coverage["last_run"]["attempted"] == 4


def test_graphql_uses_the_same_assembler(monkeypatch, client, base) -> None:
    from app.db import get_db_connection

    conn = get_db_connection()
    try:
        stored = choices._load_stored(conn, ILLIDARI, "standard", "legend")
    finally:
        conn.close()

    class Fake:
        async def hsreplay_card_choices(self, **selector: Any) -> dict[str, Any]:
            assert selector == {
                "card_id": ILLIDARI,
                "format_name": "standard",
                "rank": "legend",
            }
            return stored

    monkeypatch.setattr("app.graphql_api.router.get_graphql_repository", lambda: Fake())
    response = client.post(
        "/v1/",
        json={"query": f'{{ hsreplayCardChoices(card: "{ILLIDARI}") }}'},
        follow_redirects=True,
    )
    assert response.status_code == 200, response.text
    graph = response.json()["data"]["hsreplayCardChoices"]
    rest = client.get("/v1/hsreplay/card-choices", params={"card": ILLIDARI}).json()[
        "data"
    ]
    assert graph["options"] == rest["options"] and graph["pool"] == rest["pool"]
    missing = client.post(
        "/v1/",
        json={"query": '{ hsreplayCardChoices(card: "CORE_BT_801") }'},
        follow_redirects=True,
    )
    assert missing.json()["data"]["hsreplayCardChoices"] is None
    bad = client.post(
        "/v1/",
        json={"query": f'{{ hsreplayCardChoices(card: "{ILLIDARI}", rank: "top") }}'},
        follow_redirects=True,
    )
    assert bad.json()["errors"][0]["extensions"]["code"] == "VALIDATION_ERROR"
