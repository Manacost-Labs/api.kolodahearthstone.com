from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from starlette.testclient import TestClient

from app.main import _fresh_only_publication_reason, app

SOURCE_ID = "hsreplay_meta_archetypes_legend_eu_1d"


def _dataset(status: str | None) -> dict[str, object]:
    structured: dict[str, object] = {}
    if status is not None:
        structured["upstream_freshness"] = {
            "status": status,
            "reason": None if status == "fresh" else "upstream_snapshot_too_old",
            "age_seconds": 3600 if status == "fresh" else 200000,
            "body_as_of": datetime.now(UTC).isoformat(),
        }
    return {"data": {"structured": structured}}


def test_meta_publication_requires_explicit_fresh_evidence() -> None:
    assert _fresh_only_publication_reason(SOURCE_ID, _dataset("fresh")) is None
    assert _fresh_only_publication_reason(SOURCE_ID, _dataset("stale")) is not None
    assert _fresh_only_publication_reason(SOURCE_ID, _dataset(None)) is not None


def test_non_meta_sources_keep_existing_serving_policy() -> None:
    assert _fresh_only_publication_reason("hsguru_meta_standard_legend", _dataset(None)) is None


def test_dataset_endpoint_does_not_serve_stale_meta_snapshot() -> None:
    with (
        patch("app.main.load_dataset", return_value=_dataset("stale")),
        patch("app.parser_control.resolve_public_dataset", side_effect=lambda _sid, dataset: dataset),
    ):
        response = TestClient(app).get(f"/datasets/{SOURCE_ID}")

    assert response.status_code == 503
    assert response.json()["detail"]["reason"] == (
        "upstream snapshot is not fresh (upstream_snapshot_too_old)"
    )
