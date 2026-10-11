"""Atomic on-disk snapshots and vectorized in-memory retrieval."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from .config import data_dir
from .material_embeddings import MODEL, normalize_vectors


def index_path() -> Path:
    return Path(
        os.environ.get("HS_SEARCH_INDEX_PATH", str(data_dir() / "search/materials.npz"))
    )


def document_key(document: dict[str, Any]) -> str:
    return f"{document['kind']}:{document['entity_id']}"


def content_hash(document: dict[str, Any]) -> str:
    return hashlib.sha256(document["content"].encode("utf-8")).hexdigest()


class MaterialIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision: tuple[str, int, int] | None = None
        self._snapshot: tuple[list[dict[str, Any]], np.ndarray, float] | None = None

    def load(self) -> tuple[list[dict[str, Any]], np.ndarray, float]:
        path = index_path()
        with self._lock:
            stat = path.stat()
            revision = (str(path), stat.st_mtime_ns, stat.st_size)
            if revision != self._revision:
                with np.load(path, allow_pickle=False) as saved:
                    if str(saved["model"].item()) != MODEL:
                        raise ValueError("Embedding model changed; rebuild the index")
                    documents = json.loads(str(saved["documents"].item()))
                    vectors = normalize_vectors(saved["vectors"], len(documents))
                    built_at = float(saved["built_at"].item())
                self._snapshot = (documents, vectors, built_at)
                self._revision = revision
            assert self._snapshot is not None
            return self._snapshot

    def publish(self, documents: list[dict[str, Any]], vectors: np.ndarray) -> None:
        vectors = normalize_vectors(vectors, len(documents))
        if not documents or len({document_key(row) for row in documents}) != len(
            documents
        ):
            raise ValueError("Search corpus must be nonempty with unique IDs")
        from .storage import _assert_not_test_prod_write

        path = index_path()
        _assert_not_test_prod_write(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
                temporary = output.name
                np.savez(
                    output,
                    model=np.array(MODEL),
                    documents=np.array(
                        json.dumps(documents, ensure_ascii=False, default=str)
                    ),
                    vectors=vectors,
                    built_at=np.array(time.time()),
                )
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def candidates(
        self, vector: np.ndarray, kinds: list[str] | None, limit: int
    ) -> tuple[list[str], float]:
        documents, matrix, built_at = self.load()
        if not documents or time.time() - built_at > 24 * 3600:
            raise ValueError("Search index requires refresh")
        eligible = np.array(
            [i for i, row in enumerate(documents) if not kinds or row["kind"] in kinds],
            dtype=np.intp,
        )
        if not len(eligible):
            return [], built_at
        # einsum avoids spawning a large BLAS thread pool for a short query.
        scores = np.einsum("ij,j->i", matrix[eligible], vector, optimize=False)
        size = min(limit, len(scores))
        top = np.argpartition(-scores, size - 1)[:size]
        top = sorted(
            top, key=lambda i: (-float(scores[i]), document_key(documents[eligible[i]]))
        )
        return [document_key(documents[eligible[i]]) for i in top], built_at


material_index = MaterialIndex()
