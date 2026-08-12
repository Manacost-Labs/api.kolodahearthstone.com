from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.api_tokens import ApiTokenStore
from app.main import app
from app.redis_cache import TieredCache
from tests.test_redis_cache import FakeBackend


def _token(
    monkeypatch: Any, tmp_path: Path, *, rate: int, quota: int
) -> tuple[str, ApiTokenStore]:
    store = ApiTokenStore(database_path=tmp_path / "tokens.sqlite3")
    issued = store.issue(
        name="Limited client",
        scopes=["database:read"],
        expires_in_days=30,
        created_by="test",
        rate_limit_per_minute=rate,
        monthly_quota=quota,
    )
    monkeypatch.setattr("app.api_tokens._default_store", store)
    monkeypatch.setattr(
        "app.api_token_limits.get_tiered_cache",
        lambda: TieredCache(FakeBackend(), max_local_entries=0),
    )
    return issued.token, store


def test_token_request_reports_rate_quota_and_usage(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    token, store = _token(monkeypatch, tmp_path, rate=10, quota=5)

    response = TestClient(app).get(
        "/v1/auth/token",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "10"
    assert response.headers["x-quota-remaining"] == "4"
    usage = store.usage(response.json()["data"]["id"])
    assert usage.request_count == 1
    assert usage.error_count == 0
    assert usage.response_bytes > 0


def test_token_rate_limit_returns_structured_429(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    token, _store = _token(monkeypatch, tmp_path, rate=1, quota=10)
    cache = TieredCache(FakeBackend(), max_local_entries=0)
    monkeypatch.setattr("app.api_token_limits.get_tiered_cache", lambda: cache)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/v1/auth/token", headers=headers).status_code == 200
    limited = client.get("/v1/auth/token", headers=headers)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert limited.headers["retry-after"] == "60"


def test_token_monthly_quota_is_durable_when_rate_cache_changes(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    token, _store = _token(monkeypatch, tmp_path, rate=100, quota=1)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/v1/auth/token", headers=headers).status_code == 200
    monkeypatch.setattr(
        "app.api_token_limits.get_tiered_cache",
        lambda: TieredCache(FakeBackend(), max_local_entries=0),
    )
    limited = client.get("/v1/auth/token", headers=headers)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "MONTHLY_QUOTA_EXCEEDED"
