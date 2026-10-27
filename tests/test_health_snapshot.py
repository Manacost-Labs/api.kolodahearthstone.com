"""`/v1/health` and `/demo/overview` as an API consumer sees them in production.

Regression: every 15-second cache miss rebuilt the diagnostics in the request,
parsing and validating every dataset (2.7-7.3 s for `/v1/health`, 3.4 s for
`/demo/overview` on production). A stale snapshot is now answered at once
while one background build refreshes it.

Real application, real data directory and real status files; only the
monotonic clock (the time boundary) is controlled by the test.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from app import main
from app.routers import system
from app.storage import save_status

SOURCE = "hsreplay_arena"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[Clock]:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PYTHON_ENV", "production")
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    clock = Clock()
    monkeypatch.setattr("app.stale_while_revalidate.time", clock)
    _reset_snapshots()
    yield clock
    _reset_snapshots()


def _reset_snapshots() -> None:
    main._health_snapshot.reset()
    main._overview_snapshot.reset()
    system._public_freshest = None


def _wait_for(client: TestClient, path: str, done) -> object:  # type: ignore[no-untyped-def]
    # The background refresh runs on a real thread; give it real seconds.
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(path)
        if done(response):
            return response
        time.sleep(0.05)
    raise AssertionError(f"{path} never served the refreshed snapshot")


def test_health_answers_from_the_snapshot_and_refreshes_it_in_the_background(
    production: Clock,
) -> None:
    client = TestClient(main.app)

    first = client.get("/v1/health")
    assert first.status_code == 200
    assert first.headers["X-Health-Snapshot-Age"] == "0"
    assert SOURCE in first.json()["data"]["hard_failed_sources"]

    save_status(SOURCE, {"state": "ok"})
    production.now = 1010.0
    within_ttl = client.get("/v1/health")
    assert within_ttl.headers["X-Health-Snapshot-Age"] == "10"
    assert SOURCE in within_ttl.json()["data"]["hard_failed_sources"]

    # Old behaviour: this request rebuilt everything before answering.
    production.now = 1020.0
    stale = client.get("/v1/health")
    assert stale.status_code == 200
    assert stale.headers["X-Health-Snapshot-Age"] == "20"
    assert SOURCE in stale.json()["data"]["hard_failed_sources"]

    refreshed = _wait_for(
        client,
        "/v1/health",
        lambda r: SOURCE not in r.json()["data"]["hard_failed_sources"],
    )
    assert refreshed.headers["X-Health-Snapshot-Age"] == "0"
    assert refreshed.json()["data"]["ok"] is False


def test_health_older_than_ten_minutes_is_rebuilt_before_answering(
    production: Clock,
) -> None:
    client = TestClient(main.app)
    assert SOURCE in client.get("/v1/health").json()["data"]["hard_failed_sources"]

    save_status(SOURCE, {"state": "ok"})
    production.now = 1600.0
    response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.headers["X-Health-Snapshot-Age"] == "0"
    assert SOURCE not in response.json()["data"]["hard_failed_sources"]


def test_canonical_and_deprecated_health_routes_share_the_snapshot(
    production: Clock,
) -> None:
    client = TestClient(main.app)
    client.get("/v1/health")
    production.now = 1005.0

    deprecated = client.get("/v1/system/health")

    assert deprecated.status_code == 200
    assert deprecated.headers["X-Health-Snapshot-Age"] == "5"


def test_overview_answers_from_the_snapshot_and_refreshes_it(production: Clock) -> None:
    client = TestClient(main.app)

    def state(response) -> str:  # type: ignore[no-untyped-def]
        return next(i for i in response.json()["sources"] if i["source_id"] == SOURCE)["state"]

    assert state(client.get("/demo/overview")) == "never_fetched"

    save_status(SOURCE, {"state": "ok"})
    production.now = 1010.0
    assert state(client.get("/demo/overview")) == "never_fetched"

    production.now = 1020.0
    stale = client.get("/demo/overview")
    assert stale.status_code == 200
    assert state(stale) == "never_fetched"

    refreshed = _wait_for(client, "/demo/overview", lambda r: state(r) == "ok")
    assert refreshed.status_code == 200
