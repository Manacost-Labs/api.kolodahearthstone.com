"""Small derived projections; the dataset file remains the source of truth."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

_lock = threading.RLock()
_cache: dict[str, tuple[tuple[int, ...], dict[str, Any]]] = {}
_SCHEMA_VERSION = 2


def _version(path: Path) -> tuple[int, ...]:
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def project_catalog_metadata(document: dict[str, Any]) -> dict[str, Any]:
    from .source_completeness import completeness_view

    data = document.get("data") or {}
    rows_total = None
    source_id = document.get("source_id")
    from .sources import SOURCE_BY_ID

    source = SOURCE_BY_ID.get(source_id)
    if source:
        from .dataset_regression import estimate_metric_count

        rows_total = estimate_metric_count(source, data)
    if not rows_total:
        for container in (data.get("structured") or {}, data):
            if isinstance(container, dict):
                for key in ("cards", "rows", "archetypes", "heroes", "radars", "decks"):
                    if isinstance(container.get(key), list):
                        rows_total = len(container[key])
                        break
            if rows_total is not None:
                break
    return {
        "fetched_at": document.get("fetched_at"),
        "data": {
            key: {
                "provisional": bool(value.get("provisional")),
                "upstream_freshness": {
                    name: value["upstream_freshness"][name]
                    for name in (
                        "status",
                        "reason",
                        "age_seconds",
                        "body_as_of",
                        "upstream_timestamp",
                        "max_age_seconds",
                    )
                    if name in (value.get("upstream_freshness") or {})
                }
                if isinstance(value.get("upstream_freshness"), dict)
                else None,
            }
            for key in ("structured", "hsreplay_extracted")
            if isinstance(value := data.get(key), dict)
        },
        "completeness": completeness_view(document),
        "rows_total": rows_total,
    }


def _index_path(path: Path) -> Path:
    return path.parent.parent / "catalog-metadata" / path.parent.name / path.name


def record_catalog_metadata(
    path: Path,
    document: dict[str, Any],
    *,
    expected_file: tuple[int, ...] | None = None,
) -> None:
    """Best-effort derived index; never changes publication success."""
    if path.parent.name not in {"datasets", "baselines"}:
        return
    try:
        with _lock:
            version = _version(path)
            if expected_file is not None and version[:3] != expected_file[:3]:
                return
            projection = project_catalog_metadata(document)
            index = _index_path(path)
            index.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=index.parent, prefix=".metadata-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(
                        {
                            "schema_version": _SCHEMA_VERSION,
                            "version": version,
                            "projection": projection,
                        },
                        stream,
                    )
                # Reject a raced writer rather than index another publication.
                if _version(path) == version:
                    os.replace(temporary, index)
                    _cache[str(path)] = (version, projection)
            finally:
                Path(temporary).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - derived index must not change publication success
        # A missing/stale sidecar is rebuilt from the real file on read.
        return


def load_catalog_metadata(path: Path) -> dict[str, Any] | None:
    with _lock:
        for _ in range(3):
            try:
                version = _version(path)
            except FileNotFoundError:
                _cache.pop(str(path), None)
                return None
            cached = _cache.get(str(path))
            if cached and cached[0] == version:
                return deepcopy(cached[1])
            try:
                index = json.loads(_index_path(path).read_text(encoding="utf-8"))
                if (
                    index.get("schema_version") == _SCHEMA_VERSION
                    and tuple(index["version"]) == version
                    and isinstance(index["projection"], dict)
                ):
                    if _version(path) != version:
                        continue
                    _cache[str(path)] = (version, index["projection"])
                    return deepcopy(index["projection"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeError):
                _cache.pop(str(path), None)
                return None
            if _version(path) != version:
                continue
            if not isinstance(document, dict):
                return None
            projection = project_catalog_metadata(document)
            _cache[str(path)] = (version, projection)
            # Backfill existing publications once; no premium fetch is needed.
            record_catalog_metadata(path, document, expected_file=version)
            return deepcopy(projection)
        raise OSError("Dataset changed while reading its metadata")
