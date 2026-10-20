from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.build_material_index import rebuild
from app.material_embeddings import DIMENSIONS, EmbeddingsUnavailable
from app.material_search_index import MaterialIndex


def test_rebuild_reuses_unchanged_vectors_and_removes_deleted_rows(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HS_SEARCH_INDEX_PATH", str(tmp_path / "materials.npz"))
    index = MaterialIndex()
    old = [
        {"kind": "card", "entity_id": id, "content": text}
        for id, text in [("A", "unchanged"), ("B", "deleted")]
    ]
    vectors = np.ones((2, DIMENSIONS), dtype=np.float32)
    index.publish(old, vectors)
    new = [
        old[0] | {"updated_at": "new"},
        {"kind": "card", "entity_id": "C", "content": "new description"},
    ]
    monkeypatch.setattr("app.build_material_index.material_index", index)
    documents = AsyncMock(side_effect=[new, []])
    monkeypatch.setattr(
        "app.build_material_index.material_repository.documents", documents
    )
    encode = AsyncMock(return_value=np.ones((1, DIMENSIONS), dtype=np.float32))
    monkeypatch.setattr("app.build_material_index.embedding_client.encode", encode)
    summary = asyncio.run(rebuild())
    assert summary["embedded"] == 1
    assert summary["reused"] == 1
    encode.assert_awaited_once_with(["new description"])
    saved, _, _ = index.load()
    assert [row["entity_id"] for row in saved] == ["A", "C"]
    assert saved[0]["updated_at"] == "new"
    assert documents.await_args_list[1].kwargs["after"] == "card:C"


def test_failed_rebuild_preserves_previous_index(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_SEARCH_INDEX_PATH", str(tmp_path / "materials.npz"))
    index = MaterialIndex()
    original = [{"kind": "card", "entity_id": "A", "content": "original"}]
    index.publish(original, np.ones((1, DIMENSIONS), dtype=np.float32))
    monkeypatch.setattr("app.build_material_index.material_index", index)
    changed = [original[0] | {"content": "changed"}]
    monkeypatch.setattr(
        "app.build_material_index.material_repository.documents",
        AsyncMock(side_effect=[changed, []]),
    )
    monkeypatch.setattr(
        "app.build_material_index.embedding_client.encode",
        AsyncMock(side_effect=EmbeddingsUnavailable()),
    )
    with pytest.raises(EmbeddingsUnavailable):
        asyncio.run(rebuild())
    assert index.load()[0] == original
