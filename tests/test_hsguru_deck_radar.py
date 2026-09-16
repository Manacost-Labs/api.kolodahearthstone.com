from __future__ import annotations

import sqlite3
from unittest.mock import patch

from starlette.testclient import TestClient

from app.hsguru_deck_radar import (
    KnownDeckCatalog,
    list_deck_radar_events,
    reconcile_streamer_snapshot,
)
from app.main import app
from app.routers.constructed import deck_radar

DECK_A = "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63pBdCeBu6hBom1BoSZB+C+B43cBwAA"
DECK_B = "AAEBAf0GDsnCAtfOA/LtA/GRBOWwBI21BJfvBMCSBcKSBZWzBvWYB+ybB9edB8eyBw3nywL40ALamwPX7QO07QS4uAXu/QWEngbQngaUygapiAeEmQfgnQcAAA=="
KNOWN_DRAGON_WARRIOR = (
    "AAECAQcC69YHstgHDuPmBqr8Bqv8BveDB6WFB+iHB9KXB7etB+yyB4S9B7XAB5XCB5vCB5zCBwAA"
)
STREAMER_DRAGON_WARRIOR = (
    "AAECAQcC69YHstgHDuPmBqr8Bqv8BqWFB+iHB9KXB7etB+yyB7XAB5XCB5vCB5zCB6ngB/vgBwAA"
)
client = TestClient(app)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return connection


def _dataset(fetched_at: str, *codes: str) -> dict[str, object]:
    rows = [
        {
            "Deck": f"Deck {index}",
            "Streamer": f"Streamer {index}",
            "Format": "Standard",
            "Deck_url": f"https://www.hsguru.com/deck/radar-{index}",
            "deck_code": code,
        }
        for index, code in enumerate(codes, start=1)
    ]
    return {
        "source_id": "hsguru_streamer_decks_legend_1000",
        "fetched_at": fetched_at,
        "data": {"structured": {"type": "streamer_decks", "rows": rows}},
    }


def test_baseline_is_silent_and_later_deck_requires_two_snapshots() -> None:
    connection = _connection()

    baseline = reconcile_streamer_snapshot(
        _dataset("2026-09-16T10:00:00+00:00", DECK_A), connection=connection
    )
    repeated = reconcile_streamer_snapshot(
        _dataset("2026-09-16T10:00:00+00:00", DECK_A), connection=connection
    )
    candidate = reconcile_streamer_snapshot(
        _dataset("2026-09-16T10:15:00+00:00", DECK_A, DECK_B), connection=connection
    )

    assert baseline == {
        "baseline_created": True,
        "events_created": 0,
        "events_confirmed": 0,
        "events_suppressed_catalog_unavailable": 0,
        "events_suppressed_known_catalog": 0,
        "observations": 1,
        "snapshot_recorded": True,
    }
    assert repeated["snapshot_recorded"] is False
    assert candidate["events_created"] == 1
    assert candidate["events_confirmed"] == 0

    rows = list_deck_radar_events(connection=connection)
    assert rows["total"] == 1
    assert rows["events"][0]["deck_code"] == DECK_B
    assert rows["events"][0]["status"] == "candidate"
    assert rows["events"][0]["observation_count"] == 1

    confirmed = reconcile_streamer_snapshot(
        _dataset("2026-09-16T10:30:00+00:00", DECK_B), connection=connection
    )
    assert confirmed["events_confirmed"] == 1

    rows = list_deck_radar_events(status="confirmed", connection=connection)
    assert rows["total"] == 1
    assert rows["events"][0]["status"] == "confirmed"
    assert rows["events"][0]["observation_count"] == 2


def test_events_are_paginated_by_most_recent_observation() -> None:
    connection = _connection()
    reconcile_streamer_snapshot(
        _dataset("2026-09-16T10:00:00+00:00", DECK_A), connection=connection
    )
    reconcile_streamer_snapshot(
        _dataset("2026-09-16T10:15:00+00:00", DECK_B), connection=connection
    )

    page = list_deck_radar_events(limit=1, offset=0, connection=connection)

    assert page["total"] == 1
    assert len(page["events"]) == 1
    assert page["events"][0]["deck_code"] == DECK_B


def test_beta_suppresses_a_known_archetype_title_and_missing_catalog() -> None:
    known_title_catalog = KnownDeckCatalog(
        deck_codes=(DECK_A,),
        deck_titles=frozenset({"dragon warrior"}),
        fetched_at="2026-09-16T10:00:00+00:00",
    )
    candidate_dataset = _dataset("2026-09-16T10:15:00+00:00", DECK_B)
    candidate_dataset["data"]["structured"]["rows"][0]["Deck"] = (
        f"### Dragon Warrior {DECK_B}"
    )

    titled_connection = _connection()
    reconcile_streamer_snapshot(
        _dataset("2026-09-16T10:00:00+00:00", DECK_A),
        connection=titled_connection,
        known_catalog=known_title_catalog,
    )
    titled_result = reconcile_streamer_snapshot(
        candidate_dataset,
        connection=titled_connection,
        known_catalog=known_title_catalog,
    )

    assert titled_result["events_suppressed_known_catalog"] == 1
    assert list_deck_radar_events(connection=titled_connection)["total"] == 0

    unavailable_connection = _connection()
    reconcile_streamer_snapshot(
        _dataset("2026-09-16T10:00:00+00:00", DECK_A),
        connection=unavailable_connection,
        known_catalog=known_title_catalog,
    )
    unavailable_result = reconcile_streamer_snapshot(
        _dataset("2026-09-16T10:15:00+00:00", DECK_B),
        connection=unavailable_connection,
        known_catalog=None,
        require_known_catalog=True,
    )

    assert unavailable_result["events_suppressed_catalog_unavailable"] == 1
    assert list_deck_radar_events(connection=unavailable_connection)["total"] == 0


def test_beta_suppresses_a_known_deck_variant_even_when_title_changes() -> None:
    known_catalog = KnownDeckCatalog(
        deck_codes=(KNOWN_DRAGON_WARRIOR,),
        deck_titles=frozenset(),
        fetched_at="2026-09-16T10:00:00+00:00",
    )
    candidate_dataset = _dataset("2026-09-16T10:15:00+00:00", STREAMER_DRAGON_WARRIOR)
    candidate_dataset["data"]["structured"]["rows"][0]["Deck"] = (
        f"### Custom name {STREAMER_DRAGON_WARRIOR}"
    )
    connection = _connection()
    reconcile_streamer_snapshot(
        _dataset("2026-09-16T10:00:00+00:00", DECK_A),
        connection=connection,
        known_catalog=known_catalog,
    )

    result = reconcile_streamer_snapshot(
        candidate_dataset,
        connection=connection,
        known_catalog=known_catalog,
    )

    assert result["events_suppressed_known_catalog"] == 1
    assert list_deck_radar_events(connection=connection)["total"] == 0


def test_v1_deck_radar_exposes_confirmed_events_with_v1_metadata() -> None:
    event = {
        "fingerprint": "c" * 64,
        "event_type": "new_exact_deck",
        "status": "confirmed",
        "deck_code": DECK_B,
        "deck_name": "Fresh deck",
        "streamer": "Streamer",
        "format": "Standard",
        "source_url": "https://www.hsguru.com/deck/radar-1",
        "first_seen_at": "2026-09-16T10:15:00+00:00",
        "last_seen_at": "2026-09-16T10:30:00+00:00",
        "observation_count": 2,
    }
    with patch(
        "app.routers.constructed.list_deck_radar_events",
        return_value={
            "events": [event],
            "total": 1,
            "fetched_at": event["last_seen_at"],
        },
    ) as listed:
        response = client.get("/v1/constructed/deck-radar?status=confirmed&limit=5")

    assert response.status_code == 200
    assert response.json()["data"][0]["status"] == "confirmed"
    assert response.json()["meta"] == {
        "source_id": "hsguru_streamer_decks_legend_1000",
        "fetched_at": event["last_seen_at"],
        "stale": True,
        "beta": True,
        "count": 1,
        "limit": 5,
        "offset": 0,
    }
    listed.assert_called_once_with(status="confirmed", limit=5, offset=0)


def test_deck_radar_route_reads_the_persisted_event_table(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    reconcile_streamer_snapshot(_dataset("2026-09-16T10:00:00+00:00", DECK_A))
    reconcile_streamer_snapshot(_dataset("2026-09-16T10:15:00+00:00", DECK_B))

    payload = deck_radar(status="candidate", limit=10, offset=0)

    assert payload.meta.count == 1
    assert payload.meta.source_id == "hsguru_streamer_decks_legend_1000"
    assert payload.data[0].deck_code == DECK_B
