"""Run in the service environment: python -m app.build_material_index."""

from __future__ import annotations

import asyncio
import fcntl
import json

import numpy as np

from .graphql_api.repository import close_graphql_repository
from .material_embeddings import DIMENSIONS, MODEL, embedding_client
from .material_repository import material_repository
from .material_search_index import (
    content_hash,
    document_key,
    index_path,
    material_index,
)


async def rebuild() -> dict:
    path = index_path()
    from .storage import _assert_not_test_prod_write

    _assert_not_test_prod_write(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Search index rebuild already running") from None
        try:
            old_documents, old_vectors, _ = material_index.load()
        except (OSError, ValueError, KeyError):
            old_documents, old_vectors = [], np.empty((0, DIMENSIONS), dtype=np.float32)
        reusable = {
            (document_key(row), content_hash(row)): old_vectors[i]
            for i, row in enumerate(old_documents)
        }
        documents = []
        after = ""
        while True:
            page = await material_repository.documents(after=after, limit=500)
            if not page:
                break
            if document_key(page[-1]) <= after:
                raise RuntimeError("Invalid catalog pagination")
            documents.extend(page)
            after = document_key(page[-1])
            if len(documents) > 100_000:
                raise RuntimeError("Corpus exceeds the in-memory index limit")
        if not documents:
            raise RuntimeError("Empty catalog; preserving the current index")
        vectors = np.empty((len(documents), DIMENSIONS), dtype=np.float32)
        missing = []
        for i, row in enumerate(documents):
            previous = reusable.get((document_key(row), content_hash(row)))
            if previous is None:
                missing.append(i)
            else:
                vectors[i] = previous
        for start in range(0, len(missing), 16):
            batch = missing[start : start + 16]
            encoded = await embedding_client.encode(
                [documents[i]["content"] for i in batch]
            )
            for i, vector in zip(batch, encoded, strict=True):
                vectors[i] = vector
        await asyncio.to_thread(material_index.publish, documents, vectors)
        return {
            "model": MODEL,
            "documents": len(documents),
            "embedded": len(missing),
            "reused": len(documents) - len(missing),
        }


async def main() -> None:
    try:
        print(json.dumps(await rebuild()))
    finally:
        await embedding_client.close()
        await close_graphql_repository()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:  # noqa: BLE001 -- do not expose credentials retained by HTTP errors
        # Network exceptions can retain authorization headers; never print them.
        raise SystemExit(
            "Search index rebuild failed; previous index preserved."
        ) from None
