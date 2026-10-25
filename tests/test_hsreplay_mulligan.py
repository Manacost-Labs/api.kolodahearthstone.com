from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from app import hsreplay_mulligan as mulligan
from app import hsreplay_mulligan_store as store
from app.hsreplay_mulligan import FetchResult, Settings

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/hsreplay_mulligan"
NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
# Trimmed live answers (2026-10-25): COIN/Legend with premium (ALL, MAGE,
# PRIEST groups) and the free Bronze-Gold ALL answer; a Wild deck list.
COIN_LEGEND = json.loads((FIXTURES / "coin_legend.json").read_text())
FREE_ALL = json.loads((FIXTURES / "all_bronze_gold.json").read_text())
DECK_LIST = json.loads((FIXTURES / "deck_list.json").read_text())
DECK = "X3na9THamcly1Gu7wvEG1e"
FIRST_DBF = COIN_LEGEND["ALL"]["cards_by_dbf_id"][0]["dbf_id"]
ONE_DECK = Settings(
    standard_popular_decks=1,
    standard_winrate_decks=0,
    wild_popular_decks=0,
    wild_winrate_decks=0,
    scrape_do_run_credits=500,
    scrape_do_daily_credits=1000,
)


# --- parsing ---------------------------------------------------------------------


def test_parse_keeps_every_opponent_group_and_the_fallback_scope() -> None:
    groups = mulligan.parse_mulligan_payload(
        COIN_LEGEND, cards_by_dbf={FIRST_DBF: {"id": "TLC_100", "name": "Some Card"}}
    )
    assert [group["opponent_class"] for group in groups] == ["ALL", "MAGE", "PRIEST"]
    overall = groups[0]
    assert overall["games"] == 1904 and overall["base_winrate"] == 44.64
    assert overall["scope"] == "deck" and overall["deck_games"] == 1904
    # Too few deck games against Mage: HSReplay answered with the archetype.
    assert groups[1]["scope"] == "archetype" and groups[1]["archetype_games"] == 18646
    first = overall["cards"][0]
    assert first == {
        "dbf_id": FIRST_DBF,
        "card_id": "TLC_100",
        "card_name": "Some Card",
        "keep_percentage": 100.0,
        "opening_hand_winrate": 42.0,
        "winrate_when_drawn": 38.46,
        "winrate_when_played": 39.0,
        "avg_turns_in_hand": 1.66,
        "avg_turn_played_on": 4.29,
    }
    assert len(overall["cards"]) == 17


def test_parse_rejects_answers_without_the_all_group() -> None:
    with pytest.raises(mulligan.PayloadRejected):
        mulligan.parse_mulligan_payload({"detail": "Not found."})
    with pytest.raises(mulligan.PayloadRejected):
        mulligan.parse_mulligan_payload({"ALL": {"cards_by_dbf_id": []}})


def test_content_hash_ignores_recomputed_as_of_and_card_names() -> None:
    groups = mulligan.parse_mulligan_payload(COIN_LEGEND)
    again = mulligan.parse_mulligan_payload(
        COIN_LEGEND, cards_by_dbf={FIRST_DBF: {"id": "X", "name": "Renamed"}}
    )
    for group in again:
        group["as_of"] = "2026-10-26T00:00:00Z"
    assert mulligan.content_hash(groups) == mulligan.content_hash(again)
    again[0]["cards"][0]["keep_percentage"] = 99.0
    assert mulligan.content_hash(groups) != mulligan.content_hash(again)


def test_deck_pool_takes_most_played_then_best_winrate() -> None:
    settings = Settings(
        wild_popular_decks=5, wild_winrate_decks=1, winrate_min_games=1000
    )
    decks, as_of = mulligan.select_decks(
        DECK_LIST, format_name="wild", settings=settings, archetype_names={}
    )
    assert as_of == DECK_LIST["as_of"]
    assert [deck["pick_reason"] for deck in decks] == ["popular"] * 5 + ["winrate"]
    assert decks[0]["deck_id"] == DECK and decks[0]["total_games"] == 10268
    # The best winrate outside the top five by games (66.39 % over 4,484 games).
    assert decks[5]["deck_id"] == "zwTGrKfknP4P3y1JkrlIkd"
    assert decks[0]["archetype_name"] == "Other Paladin"
    assert [deck["position"] for deck in decks] == list(range(6))


def test_plan_has_every_initiative_and_period_and_marks_the_free_slice() -> None:
    planned = mulligan.plan_targets(
        [
            {"deck_id": DECK, "format": "standard", "rank": "bronze_gold"},
            {"deck_id": DECK, "format": "wild", "rank": "legend"},
        ]
    )
    assert len(planned) == 18
    free = [t for t in planned if not t["premium"]]
    assert {(t["rank"], t["initiative"]) for t in free} == {("bronze_gold", "all")}
    assert len(free) == 3
    assert {t["tier"] for t in planned if t["format"] == "wild"} == {3}
    url = mulligan.mulligan_api_url(free[0])
    assert parse_qs(urlsplit(url).query) == {
        "shortid": [DECK],
        "game_type_filter": ["RANKED_STANDARD"],
        "league_rank_range": ["BRONZE_THROUGH_GOLD"],
        "player_initiative": ["ALL"],
        "time_range": [mulligan.PERIODS[free[0]["period"]]],
    }


def test_default_plan_fits_the_daily_scrape_do_budget() -> None:
    settings = Settings()
    pool = [
        {
            "deck_id": f"deck{format_name}{rank}{index:04d}",
            "format": format_name,
            "rank": rank,
        }
        for format_name in mulligan.FORMATS
        for rank in mulligan.RANKS
        for index in range(
            settings.standard_popular_decks + settings.standard_winrate_decks
            if format_name == "standard"
            else settings.wild_popular_decks + settings.wild_winrate_decks
        )
    ]
    estimate = mulligan.estimate_plan(pool)
    runs_per_day = 8  # one session probe per run
    per_day = (
        estimate["mulligan_requests_per_day_by_ttl"]
        + estimate["deck_list_requests_per_day"]
        + runs_per_day
    )
    assert estimate["targets"] == 72 * 9
    assert 120 <= per_day <= settings.scrape_do_daily_credits


def test_settings_cap_concurrency_at_five(monkeypatch) -> None:
    monkeypatch.setenv("HS_HSREPLAY_MULLIGAN_CONCURRENCY", "40")
    assert Settings.from_env().concurrency == 5


# --- Scrape.do classification ------------------------------------------------------


class _Scrape:
    def __init__(self, html: str, status: int = 200, cost: int = 1) -> None:
        self.html = html
        self.status_code = status
        self.request_cost = cost


def _empty_body(backend: Any, status: int) -> Exception:
    return backend.ScrapeDoContentError(
        "Scrape.do returned an empty body",
        scrape=backend.ScrapeDoScrape(
            html="",
            status_code=status,
            final_url="https://hsreplay.net/",
            request_cost=1,
            credits_remaining=None,
            super_proxy=False,
        ),
    )


@pytest.mark.parametrize(
    ("raised", "outcome", "credits"),
    [
        ("rotation", "denied", 0),
        ("forbidden", "denied", 0),
        ("bad_request", "unsupported", 0),
        ("account", "account", 0),
        ("no_content", "no_data", 1),
        # 2026-10-25 live: a cold CURRENT_PATCH query answers 202 with no body.
        ("processing_empty", "processing", 1),
        ("empty_ok", "error", 1),
        ("transient", "error", 0),
    ],
)
def test_scrape_do_errors_are_classified(monkeypatch, raised, outcome, credits) -> None:
    from app import scrape_do_backend as backend

    errors = {
        "rotation": backend.ScrapeDoTransientError(
            'Scrape.do HTTP 502: {"StatusCode":502,"ErrorType":"ROTATION_FAILED"}',
            status_code=502,
        ),
        "forbidden": backend.ScrapeDoRequestError("HTTP 403", status_code=403),
        "bad_request": backend.ScrapeDoRequestError("HTTP 400", status_code=400),
        "account": backend.ScrapeDoAccountError("HTTP 401", status_code=401),
        "no_content": _empty_body(backend, 204),
        "transient": backend.ScrapeDoTransientError("HTTP 429", status_code=429),
        "processing_empty": _empty_body(backend, 202),
        "empty_ok": _empty_body(backend, 200),
    }

    async def scrape(url: str, **kwargs: Any):
        raise errors[raised]

    monkeypatch.setattr(backend, "scrape_url", scrape)
    client = mulligan.ScrapeDoClient(credit_cap=5)
    result = asyncio.run(client.fetch(mulligan.ACCOUNT_API))
    assert (result.outcome, result.credits) == (outcome, credits)
    assert client.credits == credits and client.requests == 1


def test_scrape_do_call_sends_the_session_cookie_and_spots_processing(
    monkeypatch,
) -> None:
    from app import hsreplay_client
    from app import scrape_do_backend as backend

    seen: dict[str, Any] = {}
    bodies = iter(
        [
            _Scrape('{"msg": "Query is processing. Try again later."}', status=202),
            _Scrape(json.dumps(FREE_ALL)),
        ]
    )

    async def scrape(url: str, **kwargs: Any):
        seen.update(kwargs)
        return next(bodies)

    monkeypatch.setattr(backend, "scrape_url", scrape)
    monkeypatch.setattr(
        hsreplay_client, "_hsreplay_cookie_header", lambda: "sessionid=s"
    )
    client = mulligan.ScrapeDoClient(credit_cap=5)
    first = asyncio.run(client.fetch("https://hsreplay.net/api/v1/mulligan/?shortid=x"))
    second = asyncio.run(
        client.fetch("https://hsreplay.net/api/v1/mulligan/?shortid=x")
    )
    assert first.outcome == "processing" and second.outcome == "ok"
    assert second.payload["ALL"]["num_games"] == FREE_ALL["ALL"]["num_games"]
    assert seen["render"] is False and seen["headers"]["Cookie"] == "sessionid=s"
    assert client.credits == 2


def test_client_never_exceeds_concurrency_or_credit_cap() -> None:
    state = {"now": 0, "max": 0}

    async def call(url: str) -> FetchResult:
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return FetchResult("ok", payload={}, credits=1)

    async def run() -> list[FetchResult]:
        client = mulligan.ScrapeDoClient(credit_cap=7, concurrency=3, call=call)
        return await asyncio.gather(*(client.fetch(f"u{i}") for i in range(12)))

    results = asyncio.run(run())
    assert state["max"] == 3
    assert sum(result.outcome == "ok" for result in results) == 7
    assert sum(result.outcome == "budget" for result in results) == 5


# --- refresh -----------------------------------------------------------------------


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.cards_index.cards_by_dbfid",
        lambda: {FIRST_DBF: {"id": "TLC_100", "name": "Some Card"}},
    )
    monkeypatch.setattr(
        "app.hsreplay_client._hsreplay_cookie_header", lambda: "sessionid=s"
    )
    alerts: list[tuple[str, str]] = []

    async def alert(source_id: str, state: str, detail: str, url: str) -> None:
        alerts.append((state, detail))

    monkeypatch.setattr("app.fetcher.send_telegram_alert", alert)
    return alerts


class FakeHsReplay:
    """Routes HSReplay URLs to canned answers and records every call."""

    def __init__(
        self,
        *,
        account: FetchResult | None = None,
        premium: FetchResult | None = None,
        overrides: dict[tuple[str, str, str], list[FetchResult]] | None = None,
    ) -> None:
        self.account = account or FetchResult(
            "ok", payload={"is_premium": True}, credits=1
        )
        self.premium = premium or FetchResult("ok", payload=COIN_LEGEND, credits=1)
        self.overrides = overrides or {}
        self.calls: list[str] = []

    async def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        if url == mulligan.ACCOUNT_API:
            return self.account
        if url.startswith(mulligan.DECK_LIST_API):
            return FetchResult("ok", payload=DECK_LIST, credits=1)
        query = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
        key = (
            query["league_rank_range"],
            query["player_initiative"],
            query["time_range"],
        )
        if self.overrides.get(key):
            return self.overrides[key].pop(0)
        if key[:2] == ("BRONZE_THROUGH_GOLD", "ALL"):
            return FetchResult("ok", payload=FREE_ALL, credits=1)
        return self.premium

    def mulligan_calls(self) -> list[str]:
        return [url for url in self.calls if url.startswith(mulligan.MULLIGAN_API)]


def _refresh(fake: FakeHsReplay, *, settings: Settings = ONE_DECK, now=NOW, **kwargs):
    return asyncio.run(
        mulligan.refresh_hsreplay_mulligan(
            settings=settings, call_json=fake, now=now, **kwargs
        )
    )


def _rows(table: str) -> list[dict[str, Any]]:
    from app.db import get_db_connection

    conn = get_db_connection()
    try:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
    finally:
        conn.close()


def test_refresh_collects_every_slice_with_premium_breakdown(isolated) -> None:
    fake = FakeHsReplay(
        overrides={
            ("DIAMOND", "COIN", "CURRENT_EXPANSION"): [
                FetchResult("no_data", credits=1)
            ],
            ("LEGEND", "FIRST", "CURRENT_PATCH"): [
                FetchResult("processing", status=202, credits=1)
            ],
        }
    )
    result = _refresh(fake)

    assert result["state"] == "ok" and result["published"], result
    assert result["premium_session"]["state"] == "ok"
    # Wild pools are empty in this configuration; four Standard ranks x one deck.
    assert result["decks"] == 4 and result["targets"] == 36
    assert result["attempted"] == 36 and result["failed"] == 0
    assert result["no_data"] == 1 and result["changed"] == 34
    # A query HSReplay is still computing (202) is deferred, not a failure.
    assert result["processing"] == 1
    # 1 probe + 8 deck lists + 36 slices; no same-run re-poll.
    assert result["requests"] == len(fake.calls) == 45
    assert result["scrape_do_credits"] == 45
    snapshots = _rows(store.SNAPSHOTS)
    assert len(snapshots) == 34
    groups = _rows(store.GROUPS)
    assert {row["opponent_class"] for row in groups} == {"ALL", "MAGE", "PRIEST"}
    run = _rows(store.RUNS)[-1]
    assert run["premium_state"] == "ok" and run["scrape_do_credits"] == 45
    deferred = [row for row in _rows(store.TARGETS) if row["state"] == "processing"]
    # Retried after an hour, then 2, 4 ... 24 hours while it keeps answering 202.
    assert len(deferred) == 1 and deferred[0]["consecutive_failures"] == 1
    assert deferred[0]["next_attempt_after"] == (NOW + timedelta(hours=1)).isoformat()

    # After the longest TTL (expansion window, tier 2: 14 days) every slice is
    # due again; identical answers add no snapshot.
    again = _refresh(FakeHsReplay(), now=NOW + timedelta(days=15))
    # The 204 slice and the computed 202 slice have data now.
    assert again["unchanged"] == 34 and again["changed"] == 2
    assert len(_rows(store.SNAPSHOTS)) == 36


def test_daily_cap_counts_earlier_runs_of_the_same_utc_day(isolated) -> None:
    capped = Settings(**{**ONE_DECK.__dict__, "scrape_do_daily_credits": 20})
    first = _refresh(FakeHsReplay(), settings=capped)
    assert (
        first["scrape_do_credits"] == 20 and first["stop_reason"] == "scrape_do_budget"
    )
    assert first["targets_due_after_run"] == 36 - 11
    second = _refresh(FakeHsReplay(), settings=capped, now=NOW + timedelta(hours=3))
    assert second["requests"] == 0 and second["stop_reason"] == "scrape_do_budget"
    tomorrow = _refresh(FakeHsReplay(), settings=capped, now=NOW + timedelta(days=1))
    assert tomorrow["scrape_do_credits"] == 20


def test_rejected_session_skips_premium_alerts_and_answers_explicitly(isolated) -> None:
    fake = FakeHsReplay(account=FetchResult("denied", status=502))
    result = _refresh(fake)

    assert result["premium_session"]["state"] == "denied"
    assert result["state"] == "partial" and result["published"]
    # Only Standard Bronze-Gold is listed and only its ALL slices are read.
    assert result["decks"] == 1 and result["skipped_premium"] == 6
    assert len(fake.mulligan_calls()) == 3
    assert all("player_initiative=ALL" in url for url in fake.mulligan_calls())
    assert [state for state, _ in isolated] == [mulligan.PREMIUM_ALERT_STATE]

    from app.storage import load_status

    status = load_status(mulligan.SOURCE_ID)
    assert status["state"] == "partial"
    assert "PREMIUM SESSION DENIED" in status["detail"]
    assert status["premium_session"]["state"] == "denied"

    from app.main import app

    client = TestClient(app)
    premium = client.get(
        "/v1/hsreplay/mulligan",
        params={"deck": DECK, "rank": "bronze_gold", "initiative": "coin"},
    )
    assert premium.status_code == 503, premium.text
    assert premium.json()["detail"]["premium_session"]["state"] == "denied"
    free = client.get(
        "/v1/hsreplay/mulligan", params={"deck": DECK, "rank": "bronze_gold"}
    )
    assert free.status_code == 200, free.text
    assert free.json()["data"]["premium_session"]["state"] == "denied"
    # The free answer has no class split: say why instead of an empty list.
    split = client.get(
        "/v1/hsreplay/mulligan",
        params={"deck": DECK, "rank": "bronze_gold", "opponent_class": "MAGE"},
    )
    assert split.status_code == 503 and "premium" in split.text
    coverage = client.get("/v1/hsreplay/mulligan/coverage").json()["data"]
    assert coverage["premium_session"]["state"] == "denied"


def test_an_inconclusive_probe_keeps_a_rejected_session(isolated) -> None:
    _refresh(FakeHsReplay(account=FetchResult("denied", status=502)))
    # Next run: the probe cannot decide (transport error); premium stays skipped.
    fake = FakeHsReplay(account=FetchResult("error", detail="http_429"))
    result = _refresh(fake, now=NOW + timedelta(days=3))
    assert result["premium_session"]["state"] == "denied"
    assert "kept: session probe inconclusive" in result["premium_session"]["detail"]
    assert all("player_initiative=ALL" in url for url in fake.mulligan_calls())
    # A conclusive probe restores premium collection.
    restored = _refresh(FakeHsReplay(), now=NOW + timedelta(days=6))
    assert restored["premium_session"]["state"] == "ok"
    assert restored["skipped_premium"] == 0 and restored["decks"] == 4


def test_premium_refusals_mid_run_trip_the_session_status(isolated) -> None:
    fake = FakeHsReplay(premium=FetchResult("denied", status=502))
    result = _refresh(
        fake, settings=Settings(**{**ONE_DECK.__dict__, "concurrency": 1})
    )

    assert result["premium_session"]["state"] == "denied"
    assert result["state"] == "partial"
    assert result["outcomes"]["denied"] == mulligan.PREMIUM_DENIAL_THRESHOLD
    assert result["skipped_premium"] == 33 - mulligan.PREMIUM_DENIAL_THRESHOLD
    assert len(isolated) == 1 and "refused 3 premium" in isolated[0][1]
    denied = [row for row in _rows(store.TARGETS) if row["state"] == "premium_denied"]
    # A refused session is not the slice's fault: no failure backoff growth.
    assert denied and all(row["consecutive_failures"] == 0 for row in denied)


# --- read API ------------------------------------------------------------------------


@pytest.fixture()
def client(isolated):
    _refresh(
        FakeHsReplay(
            overrides={
                ("LEGEND", "FIRST", "CURRENT_PATCH"): [
                    FetchResult("processing", status=202, credits=1)
                ]
            }
        )
    )
    from app.main import app

    return TestClient(app)


def test_mulligan_endpoint_contract(client) -> None:
    response = client.get(
        "/v1/hsreplay/mulligan",
        params={
            "deck": DECK,
            "format": "standard",
            "rank": "legend",
            "initiative": "coin",
            "period": "last_30_days",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (
        body["meta"]["source_id"] == "hsreplay_mulligan" and body["meta"]["count"] == 17
    )
    data = body["data"]
    assert data["source"] == "hsreplay" and data["premium"] is True
    assert data["games"] == 1904 and data["scope"] == "deck"
    assert data["opponent_classes"] == ["any", "MAGE", "PRIEST"]
    assert data["deck"]["player_class"] == "PALADIN"
    assert data["deck"]["archetype_name"] == "Other Paladin"
    first = data["rows"][0]
    assert first["card_name"] == "Some Card" and first["dbf_id"] == FIRST_DBF
    assert first["opening_hand_impact"] == round(42.0 - 44.64, 2)
    assert "position" not in first and "opponent_class" not in first
    assert data["source_url"].startswith(f"https://hsreplay.net/decks/{DECK}/#")
    assert data["premium_session"]["state"] == "ok"

    mage = client.get(
        "/v1/hsreplay/mulligan",
        params={
            "deck": DECK,
            "rank": "legend",
            "initiative": "coin",
            "opponent_class": "MAGE",
        },
    ).json()["data"]
    assert mage["scope"] == "archetype" and mage["games"] == 18646
    missing = client.get(
        "/v1/hsreplay/mulligan",
        params={"deck": DECK, "rank": "legend", "opponent_class": "ROGUE"},
    )
    assert missing.status_code == 404
    unknown = client.get("/v1/hsreplay/mulligan", params={"deck": "Nope12345678"})
    assert unknown.status_code == 404 and "/v1/hsreplay/mulligan/decks" in unknown.text
    computing = client.get(
        "/v1/hsreplay/mulligan",
        params={"deck": DECK, "initiative": "first", "period": "current_patch"},
    )
    assert computing.status_code == 404 and "still computing" in computing.text
    invalid = client.get("/v1/hsreplay/mulligan", params={"deck": DECK, "rank": "top"})
    assert invalid.status_code == 422


def test_decks_slices_history_and_coverage(client) -> None:
    decks = client.get(
        "/v1/hsreplay/mulligan/decks", params={"format": "standard"}
    ).json()
    assert decks["meta"]["count"] == 4
    assert {row["rank"] for row in decks["data"]} == set(mulligan.RANKS)
    # Legend has one slice HSReplay is still computing.
    assert {row["rank"]: row["slices_with_data"] for row in decks["data"]} == {
        "legend": 8,
        "diamond_4to1": 9,
        "diamond": 9,
        "bronze_gold": 9,
    }

    slices = client.get("/v1/hsreplay/mulligan/slices", params={"deck": DECK}).json()
    assert slices["meta"]["count"] == 36
    assert sum(not row["premium"] for row in slices["data"]) == 3

    history = client.get(
        "/v1/hsreplay/mulligan/history",
        params={"deck": DECK, "rank": "legend", "card": str(FIRST_DBF)},
    ).json()["data"]
    assert len(history["points"]) == 1
    assert history["points"][0]["card"]["keep_percentage"] == 100.0

    coverage = client.get("/v1/hsreplay/mulligan/coverage")
    assert coverage.status_code == 200
    assert "no-store" in coverage.headers.get("cache-control", "")
    report = coverage.json()["data"]
    assert report["runs"][0]["requests"] == 45
    assert report["settings"]["concurrency"] == 5


def test_graphql_uses_the_same_resolver(monkeypatch, client) -> None:
    from app.db import get_db_connection

    selector = mulligan.Selector(
        deck_id=DECK,
        format_name=None,
        rank="legend",
        initiative="coin",
        period="last_30_days",
    )
    conn = get_db_connection()
    try:
        found = mulligan._load_slice(conn, selector)
    finally:
        conn.close()

    class Fake:
        async def hsreplay_mulligan(self, **selector: Any) -> dict[str, Any]:
            assert selector["initiative"] == "coin"
            return found

    monkeypatch.setattr("app.graphql_api.router.get_graphql_repository", lambda: Fake())
    response = client.post(
        "/v1/",
        json={
            "query": f'{{ hsreplayMulligan(deck: "{DECK}", rank: "legend", '
            'initiative: "coin") }'
        },
        follow_redirects=True,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["hsreplayMulligan"] == mulligan.get_mulligan(
        selector
    )

    bad = client.post(
        "/v1/",
        json={"query": f'{{ hsreplayMulligan(deck: "{DECK}", rank: "top") }}'},
        follow_redirects=True,
    )
    assert bad.json()["errors"][0]["extensions"]["code"] == "VALIDATION_ERROR"


def test_graphql_reports_a_rejected_premium_session(monkeypatch, client) -> None:
    found = {
        "premium_session": {
            "state": "denied",
            "checked_at": NOW.isoformat(),
            "detail": "x",
        },
        "target": {
            "deck_id": DECK,
            "format": "standard",
            "rank": "legend",
            "initiative": "coin",
            "period": "last_30_days",
            "premium": 1,
            "state": "premium_denied",
        },
        "snapshot": None,
    }

    class Fake:
        async def hsreplay_mulligan(self, **selector: Any) -> dict[str, Any]:
            return found

    monkeypatch.setattr("app.graphql_api.router.get_graphql_repository", lambda: Fake())
    response = client.post(
        "/v1/",
        json={"query": f'{{ hsreplayMulligan(deck: "{DECK}", initiative: "coin") }}'},
        follow_redirects=True,
    )
    error = response.json()["errors"][0]
    assert error["extensions"]["code"] == "PREMIUM_SESSION_UNAVAILABLE"
    assert error["extensions"]["premiumSession"]["state"] == "denied"


# --- PostgreSQL mirror and timer contracts ---------------------------------------------


def test_postgres_migration_mirrors_sqlite_columns_and_types() -> None:
    sql = (ROOT / "platform/sql/019_hsreplay_mulligan.sql").read_text()
    conn = sqlite3.connect(":memory:")
    store.ensure_hsreplay_mulligan_schema(conn)
    expected_type = {"INTEGER": "bigint", "REAL": "real", "TEXT": "text"}
    for table in store.TABLES:
        block = re.search(
            rf"CREATE TABLE IF NOT EXISTS analytics\.{table} \((.*?)\n\);",
            sql,
            re.DOTALL,
        )
        assert block, table
        pg_columns = [
            line.strip().rstrip(",").split()[:2]
            for line in block.group(1).strip().splitlines()
        ]
        sqlite_columns = [
            (row[1], row[2]) for row in conn.execute(f"PRAGMA table_info({table})")
        ]
        assert [name for name, _ in pg_columns] == [
            name for name, _ in sqlite_columns
        ], table
        for (name, pg_type), (_name, sqlite_type) in zip(
            pg_columns, sqlite_columns, strict=True
        ):
            assert pg_type == (
                "jsonb" if name.endswith("_json") else expected_type[sqlite_type]
            ), (table, name)
        assert "id bigint PRIMARY KEY" in block.group(1)


def test_analytics_delta_contract_lists_every_table() -> None:
    stage = (ROOT / "platform/scripts/stage-analytics.py").read_text()
    merge = (ROOT / "platform/scripts/merge-analytics.sql").read_text()
    shadow = (ROOT / "platform/tests/incremental-shadow.test.py").read_text()
    for table in store.TABLES:
        assert f"'{table}'" in stage and f"'{table}'" in merge
        assert f'"{table}"' in shadow
        # Pool and targets update in place and snapshots age out: never append-only.
        assert f"'{table}'" not in stage.split("APPEND_ONLY")[1].split(")")[0]


def test_systemd_timer_matches_the_schedule_inventory() -> None:
    from app.parser_control_schedule import _SCHEDULES

    spec = next(spec for spec in _SCHEDULES if spec.id == "refresh-hsreplay-mulligan")
    timer = (ROOT / "systemd" / spec.systemd_unit).read_text()
    service = (
        ROOT / "systemd" / spec.systemd_unit.replace(".timer", ".service")
    ).read_text()
    assert all(f"OnCalendar={value}" in timer for value in spec.on_calendar)
    assert len(spec.local_times) == 8
    assert (
        "refresh-hsreplay-mulligan --scheduled --schedule-id refresh-hsreplay-mulligan"
        in service
    )
