from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from app.main import app
from app.source_state import SourceState


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_documented_v1_paths_exist_in_openapi() -> None:
    openapi_paths = set(TestClient(app).get("/openapi.json").json()["paths"])
    api_docs = _read("docs/API.md")
    expected = {
        "/v1/constructed/decks",
        "/v1/constructed/archetypes",
        "/v1/battlegrounds/heroes",
        "/v1/battlegrounds/minions",
        "/v1/arena/classes",
        "/v1/sources",
        "/v1/datasets",
        "/v1/health",
    }
    assert expected <= openapi_paths
    assert all(path in api_docs for path in expected)


def test_documented_source_states_match_enum() -> None:
    api_docs = _read("docs/API.md")
    security_docs = _read("docs/SECURITY_AND_PARSING.md")
    for state in SourceState:
        assert f"`{state.value}`" in api_docs
        assert f"`{state.value}`" in security_docs
    assert "`ok_cached`" in api_docs
    assert "effective_state" in api_docs


def test_reliability_root_doc_is_only_a_canonical_link() -> None:
    root_doc = _read("PROXY_AND_RELIABILITY.md")
    assert "docs/PROXY_AND_RELIABILITY.md" in root_doc
    assert len(root_doc.splitlines()) < 10


def test_operations_docs_use_current_catalog_and_publish_gate() -> None:
    combined = "\n".join(
        _read(path)
        for path in (
            "README.md",
            "DEPLOY.md",
            "docs/API.md",
            "docs/SECURITY_AND_PARSING.md",
            "docs/PROXY_AND_RELIABILITY.md",
        )
    )
    assert "46" in combined
    assert "validate_candidate_for_publish" in combined
    assert "33 шт." not in combined
    assert "/srv/hs-data-api" in combined
    assert "uvicorn app.server:app" not in combined
