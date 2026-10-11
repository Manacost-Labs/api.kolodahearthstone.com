from __future__ import annotations

import asyncio
import copy
import os
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urljoin

from .material_embeddings import MODEL, EmbeddingsUnavailable, embedding_client
from .material_repository import KINDS, material_repository
from .material_search_index import document_key, index_path, material_index

_results: OrderedDict[tuple, tuple[float, dict[str, Any]]] = OrderedDict()
_pending_searches: dict[tuple, asyncio.Task[dict[str, Any]]] = {}


def validate_search(
    query: str, kinds: list[str] | None, limit: int
) -> tuple[str, list[str] | None]:
    query = " ".join(query.split())
    if not 2 <= len(query) <= 240:
        raise ValueError("query must contain 2 to 240 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    if kinds is not None and (not kinds or any(kind not in KINDS for kind in kinds)):
        raise ValueError("Unknown or empty entity kinds")
    return query, sorted(set(kinds)) if kinds else None


def material_payload(row: dict[str, Any]) -> dict[str, Any]:
    key = document_key(row)
    image_url = row.get("image_url")
    return {
        "id": key,
        "kind": row["kind"],
        "title": row["name"],
        "subtitle": row.get("subtitle"),
        "text": row.get("content") or row.get("subtitle") or row["name"],
        "image_url": (
            urljoin("https://api.kolodahearthstone.com/", image_url)
            if image_url
            else None
        ),
        "source_id": row.get("source_id"),
        "updated_at": row.get("updated_at"),
        "metadata": row.get("metadata") or {},
        "url": "https://api.kolodahearthstone.com/v1/materials/" + quote(key, safe=""),
    }


async def _search_materials(
    query: str, kinds: list[str] | None = None, limit: int = 10, mode: str = "hybrid"
) -> dict[str, Any]:
    query, kinds = validate_search(query, kinds, limit)
    if mode not in {"hybrid", "lexical"}:
        raise ValueError("mode must be hybrid or lexical")
    started = time.monotonic()
    # Names and IDs are resolved first, without needing an embedding request.
    lexical = await material_repository.lexical(query, kinds, limit)
    exact = [
        row
        for row in lexical
        if query.casefold()
        in {
            str(row.get("name", "")).casefold(),
            str(row.get("name_ru", "")).casefold(),
            str(row.get("entity_id", "")).casefold(),
            str((row.get("metadata") or {}).get("cardId", "")).casefold(),
        }
    ]
    effective_mode = "lexical"
    built_at = None
    reason = None
    keys: list[str] = []
    enabled = os.environ.get("HS_SEARCH_ENABLED", "false").lower() in {
        "true",
        "1",
        "yes",
    }
    if mode == "hybrid" and enabled and not exact:
        try:
            # Do not spend tokens if there is no usable corpus.
            _, _, index_time = await asyncio.to_thread(material_index.load)
            if time.time() - index_time > 24 * 3600:
                raise ValueError("Index expired")
            vector = await embedding_client.query(query)
            keys, built_at = await asyncio.to_thread(
                material_index.candidates, vector, kinds, limit * 2
            )
            effective_mode = "hybrid"
        except (EmbeddingsUnavailable, OSError, ValueError, KeyError):
            reason = "semantic_unavailable"
    elif mode == "hybrid" and not enabled:
        reason = "semantic_disabled"
    # Rank fusion retains lexical matches while adding semantic discoveries.
    lexical_keys = [document_key(row) for row in lexical]
    scores: dict[str, float] = {}
    for ranking in (lexical_keys, keys):
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0) + 1 / (60 + rank + 1)
    exact_keys = {document_key(row) for row in exact}
    ordered = sorted(scores, key=lambda key: (key not in exact_keys, -scores[key], key))
    rows = (
        await material_repository.documents(keys=ordered, limit=max(1, len(ordered)))
        if ordered
        else []
    )
    # Hydrate from the live public catalog: deleted entities never leak from an old index.
    current = {document_key(row): row for row in rows}
    items = [material_payload(current[key]) for key in ordered if key in current][
        :limit
    ]
    return {
        "items": items,
        "meta": {
            "mode": effective_mode,
            "fallback_reason": reason,
            "model": MODEL if effective_mode == "hybrid" else None,
            "indexed_at": datetime.fromtimestamp(built_at, UTC).isoformat()
            if built_at
            else None,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "count": len(items),
        },
    }


async def _run_search(
    key: tuple, query: str, kinds: list[str] | None, limit: int, mode: str
) -> dict[str, Any]:
    try:
        result = await _search_materials(query, kinds, limit, mode)
        _results[key] = (time.monotonic(), result)
        _results.move_to_end(key)
        while len(_results) > 256:
            _results.popitem(last=False)
        return result
    finally:
        _pending_searches.pop(key, None)


async def search_materials(
    query: str, kinds: list[str] | None = None, limit: int = 10, mode: str = "hybrid"
) -> dict[str, Any]:
    started = time.monotonic()
    query, kinds = validate_search(query, kinds, limit)
    if mode not in {"hybrid", "lexical"}:
        raise ValueError("mode must be hybrid or lexical")
    try:
        stat = index_path().stat()
        revision = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        revision = None
    key = (
        query,
        tuple(kinds or []),
        limit,
        mode,
        os.environ.get("HS_SEARCH_ENABLED", "false"),
        str(index_path()),
        revision,
    )
    cached = _results.get(key)
    hit = bool(cached and started - cached[0] < 5)
    if hit:
        _results.move_to_end(key)
        result = copy.deepcopy(cached[1])
        age = started - cached[0]
    else:
        task = _pending_searches.get(key)
        if task is None:
            task = asyncio.create_task(_run_search(key, query, kinds, limit, mode))
            task.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )
            _pending_searches[key] = task
        result = copy.deepcopy(await asyncio.shield(task))
        age = 0
    result["meta"].update(
        {
            "cache": "hit" if hit else "miss",
            "cache_age_ms": round(age * 1000, 1),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
    )
    return result


async def close_search_cache() -> None:
    tasks = tuple(_pending_searches.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _results.clear()


async def fetch_material(material_id: str) -> dict[str, Any] | None:
    if not 3 <= len(material_id) <= 240 or material_id.partition(":")[0] not in KINDS:
        raise ValueError("Invalid material ID")
    rows = await material_repository.documents(keys=[material_id], limit=1)
    return material_payload(rows[0]) if rows else None
