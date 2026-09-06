import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.acquisition_detail_queue import DetailQueue

URL = "https://example.com/detail?id=1"


def test_enqueue_deduplicates_and_restart_preserves_success(tmp_path):
    path = tmp_path / "queue.sqlite"
    queue = DetailQueue(path)
    assert queue.enqueue("scope", [("one", URL)]) == 1
    claim = queue.claim("scope")
    assert claim is not None
    queue.finish(claim, status="succeeded", payload={"name": "one"}, request_cost=2)
    assert DetailQueue(path).enqueue("scope", [("one", URL)]) == 0
    assert DetailQueue(path).items("scope") == [
        {
            "entity_id": "one",
            "url": URL,
            "status": "succeeded",
            "attempts": 1,
            "payload": {"name": "one"},
            "error_code": None,
            "credits_spent": 2,
            "unknown_cost_attempts": 0,
        }
    ]


def test_claim_is_atomic_between_instances(tmp_path):
    path = tmp_path / "queue.sqlite"
    first, second = DetailQueue(path), DetailQueue(path)
    first.enqueue("scope", [("one", URL), ("two", "https://example.com/two")])
    claims = {first.claim("scope").entity_id, second.claim("scope").entity_id}
    assert claims == {"one", "two"}


def test_expired_lease_is_fenced_and_reclaimed_until_attempt_cap(tmp_path):
    now = [100.0]
    queue = DetailQueue(tmp_path / "queue.sqlite", clock=lambda: now[0])
    queue.enqueue("scope", [("one", URL)], max_attempts=2)
    old = queue.claim("scope", lease_seconds=1)
    now[0] = 102
    with pytest.raises(ValueError):
        queue.finish(old, status="succeeded", payload={})
    new = queue.claim("scope")
    assert new is not None and new.attempts == 2 and new.lease_token != old.lease_token
    now[0] = 300
    assert queue.claim("scope") is None
    assert queue.items("scope")[0]["status"] == "failed"
    assert queue.items("scope")[0]["unknown_cost_attempts"] == 2


def test_retry_backoff_succeeded_skip_and_scope_isolation(tmp_path):
    now = [10.0]
    queue = DetailQueue(tmp_path / "queue.sqlite", clock=lambda: now[0])
    queue.enqueue("a", [("one", URL)])
    queue.enqueue("b", [("one", "https://example.com/b")])
    claim = queue.claim("a")
    queue.finish(claim, status="retry", error_code="temporary", retry_after=5)
    assert queue.claim("a") is None
    assert queue.claim("b").url == "https://example.com/b"
    now[0] = 15
    again = queue.claim("a")
    queue.finish(again, status="succeeded", payload={"ok": True})
    assert queue.claim("a") is None


def test_invalid_batch_is_atomic_and_existing_url_policy_must_match(tmp_path):
    queue = DetailQueue(tmp_path / "queue.sqlite")
    with pytest.raises(ValueError):
        queue.enqueue("scope", [("one", URL), ("one", URL)])
    assert queue.items("scope") == []
    queue.enqueue("scope", [("one", URL)], max_attempts=2)
    assert queue.enqueue("scope", [("one", URL)], max_attempts=2) == 0
    with pytest.raises(ValueError):
        queue.enqueue("scope", [("one", "https://example.com/changed")], max_attempts=2)
    with pytest.raises(ValueError):
        queue.enqueue("scope", [("two", "http://example.com/no")])
    with pytest.raises(ValueError):
        queue.enqueue("scope", [("two", "https://127.0.0.1/no")])
    assert [row["entity_id"] for row in queue.items("scope")] == ["one"]


def test_finish_validation_cost_accounting_and_absent_reason(tmp_path):
    queue = DetailQueue(tmp_path / "queue.sqlite")
    queue.enqueue("scope", [("one", URL), ("two", "https://example.com/two")])
    one = queue.claim("scope")
    with pytest.raises(ValueError):
        queue.finish(one, status="succeeded")
    with pytest.raises(ValueError):
        queue.finish(one, status="unknown", payload={})
    with pytest.raises(ValueError):
        queue.finish(one, status="succeeded", payload={}, request_cost=True)
    queue.finish(one, status="succeeded", payload={"ok": 1}, request_cost=4)
    two = queue.claim("scope")
    with pytest.raises(ValueError):
        queue.finish(two, status="absent")
    queue.finish(two, status="absent", error_code="not_found")
    rows = {item["entity_id"]: item for item in queue.items("scope")}
    assert (
        rows["one"]["credits_spent"] == 4 and rows["one"]["unknown_cost_attempts"] == 0
    )
    assert rows["two"]["unknown_cost_attempts"] == 1


@pytest.mark.parametrize("value", [True, False, math.inf, -1, 3601])
def test_invalid_lease_values_are_rejected(tmp_path, value):
    queue = DetailQueue(tmp_path / "queue.sqlite")
    queue.enqueue("scope", [("one", URL)])
    with pytest.raises(ValueError):
        queue.claim("scope", lease_seconds=value)


def test_rejects_foreign_existing_database(tmp_path):
    path = tmp_path / "foreign.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("create table unrelated (id integer)")
    connection.close()
    with pytest.raises(ValueError):
        DetailQueue(path)


def test_large_input_iterator_stops_at_batch_limit(tmp_path):
    queue = DetailQueue(tmp_path / "queue.sqlite")

    def rows():
        for i in range(10_001):
            yield (str(i), URL)
        raise AssertionError("iterator consumed beyond batch limit")

    with pytest.raises(ValueError, match="batch"):
        queue.enqueue("scope", rows())
    assert queue.items("scope") == []


def test_nonfinite_payload_is_not_valid_json(tmp_path):
    queue = DetailQueue(tmp_path / "queue.sqlite")
    queue.enqueue("scope", [("one", URL)])
    claim = queue.claim("scope")
    with pytest.raises(ValueError, match="JSON"):
        queue.finish(claim, status="succeeded", payload={"winrate": math.nan})
    assert queue.items("scope")[0]["status"] == "running"


def test_concurrent_initialization_and_claim(tmp_path):
    path = tmp_path / "queue.sqlite"
    start = Barrier(4)

    def initialize(_):
        start.wait()
        return DetailQueue(path)

    with ThreadPoolExecutor(max_workers=4) as pool:
        queues = list(pool.map(initialize, range(4)))
        queues[0].enqueue(
            "scope", [(str(i), f"https://example.com/{i}") for i in range(4)]
        )
        claims = list(pool.map(lambda queue: queue.claim("scope"), queues))
    assert len({claim.entity_id for claim in claims}) == 4
