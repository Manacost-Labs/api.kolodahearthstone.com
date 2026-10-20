from __future__ import annotations

import hashlib
import threading
from typing import Any
from urllib.parse import parse_qs

from starlette.datastructures import MutableHeaders
from starlette.requests import Request

from .config import data_dir
from .sources import SOURCES
from .storage import load_dataset

PUBLIC_CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=600"
NO_STORE_CACHE_CONTROL = "no-store"


def _no_store_path(path: str) -> bool:
    return path.startswith(
        (
            "/v1/constructed/hsguru-deck",
            "/v1/hsguru/archetypes/analysis",
            "/v1/system/parsing-reliability",
            "/v1/search",
            "/v1/materials/",
            "/v1/mcp",
        )
    )


def _cacheable_path(path: str) -> bool:
    if path.startswith(("/v1/search", "/v1/materials/", "/v1/mcp")):
        return False
    if path.startswith("/v1/constructed/hsguru-deck"):
        # This endpoint can refresh an exact archetype in process without
        # changing the disk catalogs. A disk-derived strong ETag could return
        # a false 304 for a newer live result.
        return False
    if path.startswith("/v1/hsguru/archetypes/analysis"):
        # The response combines a dataset row with the latest refresh status.
        # Those files are published independently, so a precomputed strong
        # ETag can race their reads and return a false 304 for changed freshness
        # metadata. Keep this endpoint coherent by evaluating every request.
        return False
    if path.startswith("/v1/system/parsing-reliability"):
        # This report has moving time-window boundaries and generated_at even
        # without a database write. A strong shared ETag would be dishonest.
        return False
    if path.startswith(("/ops", "/admin", "/ui", "/health", "/v1/meta-intelligence")):
        return False
    if path.endswith("/health"):
        return False
    return path == "/datasets" or path.startswith(("/datasets/", "/api/", "/v1/"))


# path -> (file identity, fetched_at). Revisions run inside the async
# middleware, and the global fallback covers every source: parsing all
# published datasets (~260 MB) on each request held the event loop for about
# a second. Datasets are replaced atomically (tmp file + os.replace), so every
# write gets a new inode; mtime and size alone repeat for same-size rewrites
# within the filesystem's timestamp granularity.
_FETCHED_AT: dict[str, tuple[tuple[int, int, int, int], Any]] = {}
_FETCHED_AT_LOCK = threading.Lock()


def _dataset_fetched_at(source_id: str) -> Any:
    # The path of storage.dataset_path, without its directory creation: this
    # only reads metadata. Without a file, read through storage as before.
    path = data_dir() / "datasets" / f"{source_id}.json"
    try:
        stat = path.stat()
    except OSError:
        return (load_dataset(source_id) or {}).get("fetched_at")
    identity = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    key = str(path)
    with _FETCHED_AT_LOCK:
        cached = _FETCHED_AT.get(key)
    if cached and cached[0] == identity:
        return cached[1]
    value = (load_dataset(source_id) or {}).get("fetched_at")
    with _FETCHED_AT_LOCK:
        _FETCHED_AT[key] = (identity, value)
    return value


class _StateReadOnce:
    """One parser-control state read shared by every source of one revision.

    Each early source otherwise locks and parses the control state again.
    A storage error is raised again to each caller, which falls back as usual.
    """

    def __init__(self) -> None:
        self._result: Any = None
        self._error: Exception | None = None

    def read_state(self) -> Any:
        if self._result is None and self._error is None:
            from .parser_control import parser_control_store

            try:
                self._result = parser_control_store().read_state()
            except Exception as exc:  # noqa: BLE001 - re-raised to every caller
                self._error = exc
        if self._error is not None:
            raise self._error
        return self._result


def _dataset_timestamp(source_id: str, *, store: Any = None) -> str | None:
    try:
        value = _dataset_fetched_at(source_id)
    except (OSError, ValueError):
        return None
    if not value:
        return None
    try:
        from .parser_control import publication_cache_token

        control_token = publication_cache_token(source_id, store=store)
    except Exception:
        control_token = ""
    return f"{value}:{control_token}" if control_token else str(value)


def _latest_dataset_timestamp() -> str | None:
    store = _StateReadOnce()
    values = [
        value
        for source in SOURCES
        if (value := _dataset_timestamp(source.id, store=store))
    ]
    return max(values) if values else None


def _db_revision(path: str) -> str | None:
    try:
        if path.startswith(("/v1/constructed/decks", "/api/db/decks")):
            from .db import get_db_connection

            conn = get_db_connection()
            try:
                row = conn.execute("SELECT MAX(updated_at) FROM decks").fetchone()
                return str(row[0]) if row and row[0] else None
            finally:
                conn.close()
        if path.startswith(("/v1/constructed/archetypes", "/api/db/archetypes")):
            from .hsreplay_archetypes_db import latest_run

            run = latest_run() or {}
            return str(run.get("completed_at") or run.get("started_at") or "") or None
        if path.startswith(
            (
                "/v1/bg/minions",
                "/v1/battlegrounds/minions",
                "/api/db/bg/minions",
            )
        ):
            from .hsreplay_bg_minions_db import latest_run

            run = latest_run() or {}
            return str(run.get("completed_at") or run.get("started_at") or "") or None
    except Exception:
        return None
    return None


def cache_revision(path: str, query_string: bytes) -> str:
    if path.startswith("/datasets/"):
        source_id = path.removeprefix("/datasets/").split("/", 1)[0]
        return _dataset_timestamp(source_id) or "not-cached"
    if path.startswith("/v1/arena/classes"):
        query = parse_qs(query_string.decode("utf-8", errors="ignore"))
        source_id = (
            query.get("source_id") or ["hsreplay_arena_class_pages_firecrawl"]
        )[0]
        return _dataset_timestamp(source_id) or "not-cached"
    if path.startswith(
        (
            "/v1/bg/heroes",
            "/v1/battlegrounds/heroes",
            "/api/bg/heroes",
        )
    ):
        return _dataset_timestamp("hsreplay_battlegrounds_hero_details") or "not-cached"
    if path.startswith("/v1/constructed/hsguru-deck"):
        from .hsguru_decks import hsguru_matrix_cache_revision

        query = parse_qs(query_string.decode("utf-8", errors="ignore"))
        format_name = (query.get("format_name") or ["standard"])[0]
        rank = (query.get("rank") or ["legend"])[0]
        if format_name in {"standard", "wild"}:
            catalog_rank = rank if rank in {"legend", "all"} else "all"
            source_id = f"hsguru_deck_catalog_{format_name}_{catalog_rank}"
            catalog_revision = _dataset_timestamp(source_id) or "not-cached"
            return f"{catalog_revision}:{hsguru_matrix_cache_revision()}"
    if path.startswith("/v1/hsguru/archetypes"):
        from .hsguru_decks import hsguru_matrix_cache_revision

        return hsguru_matrix_cache_revision()
    db_revision = _db_revision(path)
    if db_revision:
        return db_revision
    return _latest_dataset_timestamp() or "not-cached"


def build_etag(path: str, query_string: bytes, revision: str) -> str:
    seed = b"\0".join([path.encode(), query_string, revision.encode()])
    return f'"{hashlib.sha256(seed).hexdigest()}"'


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    normalized = etag.removeprefix("W/")
    return any(
        candidate.strip().removeprefix("W/") in {"*", normalized}
        for candidate in header.split(",")
    )


class PublicCacheMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "GET":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        if not _cacheable_path(path):
            if not _no_store_path(path):
                await self.app(scope, receive, send)
                return

            async def no_store_headers(message: dict[str, Any]) -> None:
                if message.get("type") == "http.response.start":
                    headers = MutableHeaders(scope=message)
                    headers["Cache-Control"] = NO_STORE_CACHE_CONTROL
                    if "etag" in headers:
                        del headers["etag"]
                await send(message)

            await self.app(scope, receive, no_store_headers)
            return

        request = Request(scope, receive=receive)
        revision = cache_revision(path, scope.get("query_string") or b"")
        etag = build_etag(path, scope.get("query_string") or b"", revision)
        requested_not_modified = _etag_matches(
            request.headers.get("if-none-match"), etag
        )
        not_modified = False

        async def cache_headers(message: dict[str, Any]) -> None:
            nonlocal not_modified
            if (
                message.get("type") == "http.response.start"
                and 200 <= int(message.get("status") or 0) < 300
            ):
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = PUBLIC_CACHE_CONTROL
                headers["ETag"] = etag
                if requested_not_modified:
                    not_modified = True
                    message["status"] = 304
                    if "content-length" in headers:
                        del headers["content-length"]
                    if "content-type" in headers:
                        del headers["content-type"]
            elif message.get("type") == "http.response.body" and not_modified:
                message = dict(message)
                message["body"] = b""
            await send(message)

        await self.app(scope, receive, cache_headers)
