from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_DATA_DIR,
    data_dir,
    json_backup_keep_per_file,
    pytest_current_test,
    python_environment,
)

_dataset_write_lock = threading.Lock()
_production_data_dir = Path(DEFAULT_DATA_DIR).resolve()


def root_dir() -> Path:
    root = data_dir()
    _assert_not_test_prod_write(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "datasets").mkdir(exist_ok=True)
    (root / "statuses").mkdir(exist_ok=True)
    return root


def dataset_path(source_id: str) -> Path:
    return root_dir() / "datasets" / f"{source_id}.json"


def status_path(source_id: str) -> Path:
    return root_dir() / "statuses" / f"{source_id}.json"


def baseline_path(source_id: str, label: str) -> Path:
    for value in (source_id, label):
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        if not value or any(character not in allowed for character in value):
            raise ValueError("Invalid baseline storage key")
    directory = root_dir() / "baselines"
    directory.mkdir(exist_ok=True)
    return directory / f"{source_id}.{label}.json"


def _is_test_process() -> bool:
    if pytest_current_test():
        return True
    if python_environment() == "test":
        return True
    argv = " ".join(sys.argv).lower()
    return "pytest" in argv or "unittest" in argv


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _assert_not_test_prod_write(path: Path) -> None:
    if _is_test_process() and _is_inside(path, _production_data_dir):
        raise RuntimeError(
            f"Refusing to write production parser data during tests: {path}"
        )


def _prune_backups(backup_path: Path) -> None:
    keep_per_file = json_backup_keep_per_file()
    if keep_per_file <= 0:
        return
    prefix = backup_path.name.split(".", 1)[0]
    siblings = sorted(
        backup_path.parent.glob(f"{prefix}.*{backup_path.suffix}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in siblings[keep_per_file:]:
        old.unlink(missing_ok=True)


def _backup_existing_json(path: Path) -> None:
    if not path.exists():
        return
    root = root_dir()
    if not _is_inside(path, root):
        return
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return
    if not relative.parts or relative.parts[0] not in {"datasets", "statuses"}:
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = root / "backups" / relative.parent / f"{path.stem}.{stamp}{path.suffix}"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_bytes(path.read_bytes())
    _prune_backups(backup_path)


def _match_parent_permissions(path: Path) -> None:
    try:
        parent_stat = path.parent.stat()
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(path, parent_stat.st_uid, parent_stat.st_gid)
        path.chmod(0o644)
    except OSError:
        # Permission normalization is best-effort; write success should not be hidden.
        pass


def write_json(path: Path, payload: dict[str, Any]) -> None:
    _assert_not_test_prod_write(path)
    _backup_existing_json(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _match_parent_permissions(tmp)
    written = tmp.stat()
    tmp.replace(path)
    _match_parent_permissions(path)
    from .catalog_metadata import record_catalog_metadata

    record_catalog_metadata(path, payload, expected_file=(written.st_ino, written.st_size, written.st_mtime_ns))


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_dataset(source_id: str, payload: dict[str, Any]) -> None:
    from .sources import STATUS_ONLY_SOURCE_IDS

    if source_id in STATUS_ONLY_SOURCE_IDS:
        raise ValueError(f"{source_id} publishes no dataset; refusing to write one")
    write_json(dataset_path(source_id), payload)
    try:
        from .db import store_dataset_to_db

        with _dataset_write_lock:
            store_dataset_to_db(source_id, payload)
    except Exception as e:
        import logging

        logging.getLogger(__name__).error("Error storing dataset %s in SQLite: %s", source_id, e)
        try:
            from .refresh_log import log_action

            log_action(
                "dataset.db_store.fail",
                source_id=source_id,
                level="error",
                detail=str(e)[:1000],
                error_type=type(e).__name__,
            )
        except Exception:
            pass


def save_status(source_id: str, payload: dict[str, Any]) -> None:
    write_json(status_path(source_id), payload)
    if source_id.startswith("hsguru"):
        try:
            from .meta_intelligence.snapshots import ingest_published

            ingest_published(source_id, payload)
        except Exception:
            # A secondary research store must never break existing publication.
            import logging

            logging.getLogger(__name__).exception("Lab ingestion failed for source %s", source_id)


def load_dataset(source_id: str) -> dict[str, Any] | None:
    return read_json(dataset_path(source_id))


def load_status(source_id: str) -> dict[str, Any] | None:
    return read_json(status_path(source_id))


def save_baseline_once(source_id: str, label: str, payload: dict[str, Any]) -> bool:
    path = baseline_path(source_id, label)
    with _dataset_write_lock:
        if path.exists():
            return False
        write_json(path, payload)
    return True


def save_baseline(source_id: str, label: str, payload: dict[str, Any]) -> None:
    """Atomically replace a named baseline with a newly validated dataset."""
    path = baseline_path(source_id, label)
    with _dataset_write_lock:
        write_json(path, payload)


def load_baseline(source_id: str, label: str) -> dict[str, Any] | None:
    return read_json(baseline_path(source_id, label))
