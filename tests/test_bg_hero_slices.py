"""On-demand MMR slices of /api/bg/heroes: last-known-good copy, stale
answers, 503 without a copy, and the hero index contract for narrow slices."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from starlette.testclient import TestClient
from web_scraper import validate_response
from web_scraper.fetchers import RawResponse

from app import bg_hero_slices as slices
from app import hsreplay_bg_hero_details as hero_details
from app.parsesunix_contracts import hsreplay_json_response_contract

HEROES_URL = (
    "https://hsreplay.net/api/v1/battlegrounds/heroes/"
    "?BattlegroundsMMRPercentile=TOP_1_PERCENT"
)


def _wire_row(dbf_id: int, *, with_stats: bool = True) -> dict[str, Any]:
    """One hero row as HSReplay sends it; a hero with too few games has nulls."""

    return {
        "hero_dbf_id": dbf_id,
        "pick_rate": 22.44,
        "armor": 14,
        "avg_final_placement": 3.831 if with_stats else None,
        "adjusted_avg_final_placement": None,
        "final_placement_distribution": [12.5] * 8 if with_stats else None,
        "tier_v2": "d" if with_stats else None,
        "anomaly_adjusted": False,
        "best_composition": None,
        "key_minions_top3": [],
    }


def _wire_payload(
    count: int = 40, *, without_stats: set[int] | None = None
) -> dict[str, Any]:
    missing = without_stats or set()
    return {
        "data": [
            _wire_row(1000 + index, with_stats=index not in missing)
            for index in range(count)
        ]
    }


class Upstream:
    """Stands in for the HSReplay JSON client behind fetch_hero_index."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.payload: dict[str, Any] | None = _wire_payload()
        self.error: Exception | None = None

    async def __call__(self, url: str, *, cache_key: str) -> dict[str, Any]:
        self.calls.append(url)
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        assert self.payload is not None
        return self.payload


@pytest.fixture()
def upstream(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(hero_details, "cards_by_dbfid", dict)
    fake = Upstream()
    monkeypatch.setattr(hero_details, "_fetch_json", fake)
    slices._reset_for_tests()
    yield fake
    slices._reset_for_tests()


@pytest.fixture()
def client(upstream):
    from app.main import app

    return TestClient(app)


def _age_copy(mode: str, mmr: str, *, hours: float) -> str:
    path = slices.slice_path(mode, mmr)
    copy = json.loads(path.read_text(encoding="utf-8"))
    copy["fetched_at"] = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    path.write_text(json.dumps(copy), encoding="utf-8")
    return copy["fetched_at"]


ALL_CHANNELS_FAILED = RuntimeError(
    "Could not fetch HSReplay JSON: flaresolverr: response contract PARSE_FAIL; "
    "scrape_do: response contract PARSE_FAIL; curl_cffi: response contract PARSE_FAIL"
)


# --- route ------------------------------------------------------------------------


def test_successful_read_answers_and_stores_the_copy(client, upstream) -> None:
    response = client.get("/api/bg/heroes?mode=solo&mmr=TOP_1_PERCENT")

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "bg_heroes" and body["mode"] == "solo"
    assert body["count"] == 40 and len(body["heroes"]) == 40
    assert body["stale"] is False
    assert body["filters"]["mmr_percentile"] == "TOP_1_PERCENT"
    assert "BattlegroundsMMRPercentile=TOP_1_PERCENT" in upstream.calls[0]
    copy = json.loads(slices.slice_path("solo", "TOP_1_PERCENT").read_text("utf-8"))
    assert copy["fetched_at"] == body["fetched_at"]
    assert len(copy["payload"]["heroes"]) == 40
    # The body is refreshed inside the request: it must not be pinned by an ETag.
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers


def test_fresh_copy_answers_without_asking_upstream(client, upstream) -> None:
    first = client.get("/api/bg/heroes?mmr=TOP_5_PERCENT").json()
    upstream.error = ALL_CHANNELS_FAILED

    second = client.get("/api/bg/heroes?mmr=TOP_5_PERCENT&q=hero")

    assert second.status_code == 200
    assert len(upstream.calls) == 1
    assert second.json()["stale"] is False
    assert second.json()["fetched_at"] == first["fetched_at"]


def test_all_channels_failing_serves_the_stored_copy_as_stale(client, upstream) -> None:
    first = client.get("/api/bg/heroes?mode=solo&mmr=TOP_1_PERCENT").json()
    copied_at = _age_copy("solo", "TOP_1_PERCENT", hours=30)
    upstream.error = ALL_CHANNELS_FAILED

    response = client.get("/api/bg/heroes?mode=solo&mmr=TOP_1_PERCENT")

    assert response.status_code == 200
    body = response.json()
    assert body["stale"] is True
    assert body["fetched_at"] == copied_at
    assert body["heroes"] == first["heroes"] and body["count"] == 40
    assert response.headers["cache-control"] == "no-store"
    # The failure did not replace the stored data.
    copy = json.loads(slices.slice_path("solo", "TOP_1_PERCENT").read_text("utf-8"))
    assert copy["fetched_at"] == copied_at and len(copy["payload"]["heroes"]) == 40


def test_failed_read_is_not_repeated_on_every_request(client, upstream) -> None:
    client.get("/api/bg/heroes?mmr=TOP_1_PERCENT")
    _age_copy("solo", "TOP_1_PERCENT", hours=30)
    upstream.error = ALL_CHANNELS_FAILED

    for _ in range(3):
        assert client.get("/api/bg/heroes?mmr=TOP_1_PERCENT").json()["stale"] is True

    assert len(upstream.calls) == 2  # the first success and one failed refresh


def test_recovered_upstream_replaces_the_stale_copy(client, upstream) -> None:
    client.get("/api/bg/heroes?mmr=TOP_1_PERCENT")
    copied_at = _age_copy("solo", "TOP_1_PERCENT", hours=30)
    upstream.error = ALL_CHANNELS_FAILED
    assert client.get("/api/bg/heroes?mmr=TOP_1_PERCENT").json()["stale"] is True

    upstream.error = None
    upstream.payload = _wire_payload(35)
    slices._retry_not_before.clear()  # the cooldown has passed
    body = client.get("/api/bg/heroes?mmr=TOP_1_PERCENT").json()

    assert body["stale"] is False and body["count"] == 35
    assert body["fetched_at"] > copied_at
    copy = json.loads(slices.slice_path("solo", "TOP_1_PERCENT").read_text("utf-8"))
    assert len(copy["payload"]["heroes"]) == 35


@pytest.mark.parametrize(
    ("mode", "mmr"),
    [
        ("solo", "TOP_1_PERCENT"),
        ("solo", "TOP_5_PERCENT"),
        ("solo", "TOP_20_PERCENT"),
        ("duos", "TOP_1_PERCENT"),
        ("duos", "TOP_20_PERCENT"),
    ],
)
def test_failure_without_a_copy_is_503_with_detail(client, upstream, mode, mmr) -> None:
    upstream.error = ALL_CHANNELS_FAILED

    response = client.get(f"/api/bg/heroes?mode={mode}&mmr={mmr}")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert mmr in detail and mode in detail
    assert "PARSE_FAIL" not in response.text and "flaresolverr" not in response.text
    assert int(response.headers["retry-after"]) == slices.FAILURE_COOLDOWN_SECONDS
    assert not slices.slice_path(mode, mmr).exists()
    assert "etag" not in response.headers

    # Inside the cooldown the answer stays 503 and upstream is left alone.
    again = client.get(f"/api/bg/heroes?mode={mode}&mmr={mmr}")
    assert again.status_code == 503
    assert 1 <= int(again.headers["retry-after"]) <= slices.FAILURE_COOLDOWN_SECONDS
    assert len(upstream.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        ALL_CHANNELS_FAILED,
        hero_details.ProxyPaymentRequiredError("proxy", status_code=402),
        TimeoutError(),
        KeyError("data"),
    ],
    ids=["channels", "proxy", "timeout", "shape"],
)
def test_no_upstream_error_becomes_a_500(client, upstream, error) -> None:
    upstream.error = error

    assert client.get("/api/bg/heroes?mmr=TOP_1_PERCENT").status_code == 503


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        _wire_payload(12),
        _wire_payload(40, without_stats=set(range(40))),
        {"detail": "Authentication credentials were not provided."},
    ],
    ids=["empty", "too-few", "no-statistics", "auth-body"],
)
def test_unusable_answer_is_never_stored_or_served(client, upstream, payload) -> None:
    upstream.payload = payload

    response = client.get("/api/bg/heroes?mmr=TOP_1_PERCENT")

    assert response.status_code == 503
    assert not slices.slice_path("solo", "TOP_1_PERCENT").exists()


def test_corrupt_copy_is_ignored(client, upstream) -> None:
    path = slices.slice_path("solo", "TOP_1_PERCENT")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    upstream.error = ALL_CHANNELS_FAILED

    assert client.get("/api/bg/heroes?mmr=TOP_1_PERCENT").status_code == 503


def test_duos_slice_uses_its_own_copy_and_floor(client, upstream) -> None:
    upstream.payload = _wire_payload(22)

    duos = client.get("/api/bg/heroes?mode=duos&mmr=TOP_1_PERCENT")
    solo = client.get("/api/bg/heroes?mode=solo&mmr=TOP_1_PERCENT")

    assert duos.status_code == 200 and duos.json()["mode"] == "duos"
    assert "/duos/heroes/" in upstream.calls[0]
    assert solo.status_code == 503  # 22 heroes are below the solo floor of 30
    assert slices.slice_path("duos", "TOP_1_PERCENT").exists()


def test_stored_top_50_list_does_not_touch_the_on_demand_path(client, upstream) -> None:
    upstream.error = ALL_CHANNELS_FAILED

    response = client.get("/api/bg/heroes?mode=solo&mmr=TOP_50_PERCENT")

    assert response.status_code == 200 and upstream.calls == []


def test_concurrent_requests_share_one_upstream_read(upstream) -> None:
    async def run() -> list[dict[str, Any]]:
        return await asyncio.gather(
            *(
                slices.load_hero_slice(mode="solo", mmr="TOP_1_PERCENT")
                for _ in range(5)
            )
        )

    results = asyncio.run(run())

    assert len(upstream.calls) == 1
    assert {result["fetched_at"] for result in results} == {results[0]["fetched_at"]}


# --- narrow slice: heroes without placement statistics ----------------------------


def test_narrow_slice_with_null_first_row_is_a_healthy_answer(client, upstream) -> None:
    # Top 1% on 2026-11-16: 90 heroes, 16 without statistics, one of them first.
    upstream.payload = _wire_payload(90, without_stats={0, 5, 6, 26, 29, 37})

    body = client.get("/api/bg/heroes?mmr=TOP_1_PERCENT").json()

    assert body["count"] == 90 and body["stale"] is False
    empty = [row for row in body["heroes"] if row["avg_placement"] is None]
    assert len(empty) == 6
    assert all(
        row["tier"] is None and row["placement_distribution"] == [] for row in empty
    )
    assert all(row["pick_rate"] == "22.44%" for row in empty)
    # Heroes without statistics sort after the ranked ones.
    assert [row["avg_placement"] is None for row in body["heroes"]][-6:] == [True] * 6


def _validate(payload: Any) -> bool:
    contract = hsreplay_json_response_contract(HEROES_URL)
    body = json.dumps(payload).ljust(120)
    return validate_response(
        RawResponse(
            requested_url=HEROES_URL,
            final_url=HEROES_URL,
            status=200,
            headers={"Content-Type": "application/json"},
            body=body.encode(),
        ),
        contract,
    ).transport_validated


def test_hero_contract_accepts_a_first_row_without_statistics() -> None:
    assert _validate(_wire_payload(3, without_stats={0})) is True
    assert _validate(_wire_payload(3)) is True


def test_hero_contract_still_rejects_what_is_not_a_hero_list() -> None:
    assert _validate({"data": []}) is False
    assert _validate({"data": [{"name": "x"}]}) is False
    assert _validate({"data": [{"hero_dbf_id": 1}]}) is False
    assert (
        _validate({"detail": "Authentication credentials were not provided."}) is False
    )


def test_hero_index_without_any_statistics_is_rejected(upstream) -> None:
    upstream.payload = _wire_payload(40, without_stats=set(range(40)))

    with pytest.raises(RuntimeError, match="no placement statistics"):
        asyncio.run(hero_details.fetch_hero_index(mmr="TOP_1_PERCENT"))
