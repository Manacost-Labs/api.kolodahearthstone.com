"""Publish changed images without overwriting files used by older card versions."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit


def image_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def current_image_path(url: str | None, upload_dir: Path, fallback: Path) -> Path:
    parsed = urlsplit(url or "")
    path = unquote(parsed.path)
    if parsed.hostname not in (None, "api.kolodahearthstone.com") or not path.startswith("/uploads/"):
        return fallback
    relative = Path(path.removeprefix("/uploads/"))
    if relative.is_absolute() or any(part in (".", "..") for part in relative.parts):
        raise ValueError("Invalid local card image path")
    return upload_dir / relative


def publish_image(candidate: Path, current: Path, upload_dir: Path) -> tuple[Path, str]:
    upload_dir = upload_dir.resolve()
    digest = image_digest(candidate)
    if current.is_file() and image_digest(current) == digest:
        return current, digest
    target = upload_dir / "card-history-media" / (digest + ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(candidate, 0o644)
    try:
        os.link(candidate, target)
    except FileExistsError:
        if image_digest(target) != digest:
            raise ValueError("Archived card image hash mismatch")
    return target, digest
