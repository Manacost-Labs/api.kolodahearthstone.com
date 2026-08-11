from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_v1_sources_returns_registry_envelope() -> None:
    fetched_at = datetime.now(UTC).isoformat()

    def dataset(source_id: str) -> dict | None:
        if source_id == "hsreplay_arena":
            return {"fetched_at": fetched_at}
        return None

    with patch(
        "app.routers.system.load_resolved_public_dataset", side_effect=dataset
    ):
        response = client.get("/v1/system/sources?site=hsreplay&category=arena")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source_id"] == "source_registry"
    assert body["meta"]["count"] == len(body["data"])
    assert body["meta"]["stale"] is False
    assert all(row["site"] == "hsreplay" and row["category"] == "arena" for row in body["data"])
    assert any(row["id"] == "hsreplay_arena" and row["has_dataset"] for row in body["data"])


def test_v1_system_paths_do_not_replace_legacy_paths() -> None:
    paths = set(client.get("/openapi.json").json()["paths"])
    for legacy, versioned in (
        ("/sources", "/v1/system/sources"),
        ("/datasets", "/v1/system/datasets"),
        ("/health", "/v1/system/health"),
    ):
        assert legacy in paths
        assert versioned in paths
