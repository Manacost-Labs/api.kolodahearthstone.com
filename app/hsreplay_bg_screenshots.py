from __future__ import annotations

import io
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .config import data_dir
from .firecrawl_backend import (
    _decode_inline_screenshot,
    _download_https_screenshot,
    _image_mime,
    scrape_source_with_options,
)
from .hsreplay_auth import hsreplay_cookies_for_fetch
from .resource_locks import ResourceLocked, ResourceLockSet
from .sources import Source

COMPOSITIONS_URL = "https://hsreplay.net/battlegrounds/compositions/"
SCREENSHOT_SOURCE_ID = "hsreplay_battlegrounds_compositions_screenshot"
_IMAGE_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_PIL_FORMAT_MIMES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_MIN_IMAGE_DIMENSION = 64
_MAX_IMAGE_DIMENSION = 20_000
_MAX_IMAGE_PIXELS = 100_000_000
_PUBLIC_CAPTURE_BACKENDS = frozenset(
    {"firecrawl", "scrape_do", "scrape_do_super", "scrapfly"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _screenshot_dir() -> Path:
    path = Path(data_dir()) / "firecrawl" / "screenshots" / "hsreplay_battlegrounds_compositions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _validated_image_mime(raw: bytes, *, declared_mime: str | None = None) -> str:
    detected_mime = _image_mime(raw)
    if detected_mime is None or (
        declared_mime is not None and detected_mime != declared_mime
    ):
        raise ValueError("Firecrawl response did not contain a valid screenshot image")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if (
                _PIL_FORMAT_MIMES.get(str(image.format).upper()) != detected_mime
                or not _MIN_IMAGE_DIMENSION <= width <= _MAX_IMAGE_DIMENSION
                or not _MIN_IMAGE_DIMENSION <= height <= _MAX_IMAGE_DIMENSION
                or width * height > _MAX_IMAGE_PIXELS
            ):
                raise ValueError("invalid screenshot image dimensions or format")
            image.verify()
    except (
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ):
        raise ValueError(
            "Firecrawl response did not contain a valid screenshot image"
        ) from None
    return detected_mime


def _read_screenshot(value: str) -> tuple[str, bytes]:
    normalized = value.strip()
    decoded = _decode_inline_screenshot(normalized)
    if decoded is not None:
        declared_mime, raw = decoded
        return _validated_image_mime(raw, declared_mime=declared_mime), raw
    if normalized.casefold().startswith("https://"):
        try:
            raw = _download_https_screenshot(normalized)
        except (OSError, RuntimeError, ValueError):
            raise ValueError(
                "Firecrawl response did not contain a valid screenshot image"
            ) from None
        return _validated_image_mime(raw), raw
    raise ValueError("Firecrawl response did not contain a valid screenshot image")


def _write_screenshot(value: str, image_path: Path) -> dict[str, Any]:
    mime, raw = _read_screenshot(value)
    final_path = image_path.with_suffix(_IMAGE_SUFFIXES[mime])
    temporary_path = final_path.with_name(
        f".{final_path.stem}.{uuid.uuid4().hex}{final_path.suffix}"
    )
    try:
        temporary_path.write_bytes(raw)
        _redact_account_area(temporary_path)
        _crop_compositions_table(temporary_path)
        _validated_image_mime(temporary_path.read_bytes(), declared_mime=mime)
        os.replace(temporary_path, final_path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "image_path": str(final_path),
        "image_bytes": final_path.stat().st_size,
        "image_mime": mime,
    }


def _public_capture_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    """Expose only a closed provider label, never response metadata or signed URLs."""
    backend = str(metadata.get("backend") or "").strip().casefold()
    return {
        "backend": backend if backend in _PUBLIC_CAPTURE_BACKENDS else "unknown"
    }


def _redact_account_area(image_path: Path) -> None:
    from PIL import ImageDraw

    try:
        with Image.open(image_path) as image:
            jpeg = image_path.suffix == ".jpg"
            image = image.convert("RGB" if jpeg else "RGBA")
            draw = ImageDraw.Draw(image)
            width, _height = image.size
            fill = (35, 25, 36) if jpeg else (35, 25, 36, 255)
            draw.rectangle((max(0, width - 420), 0, width, 54), fill=fill)
            image.save(image_path)
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise ValueError("Failed to redact screenshot account area") from exc


def _crop_compositions_table(image_path: Path) -> None:
    try:
        with Image.open(image_path) as image:
            width, height = image.size
            left = int(width * 0.16)
            top = int(height * 0.18)
            right = int(width * 0.84)
            bottom = min(int(height * 0.77), top + int(width * 0.48))
            cropped = image.crop((left, top, right, bottom))
            cropped.save(image_path)
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise ValueError("Failed to crop screenshot compositions table") from exc


def _hsreplay_cookie_header() -> str:
    cookies = [
        f"{cookie['name']}={cookie['value']}"
        for cookie in hsreplay_cookies_for_fetch()
        if cookie.get("name") and cookie.get("value")
    ]
    return "; ".join(cookies)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def capture_compositions_screenshot() -> dict[str, Any]:
    locks = ResourceLockSet(
        [SCREENSHOT_SOURCE_ID],
        metadata={"operation": "capture_compositions_screenshot"},
    )
    try:
        locks.acquire()
    except ResourceLocked as exc:
        return {
            "ok": True,
            "published": False,
            "source_id": SCREENSHOT_SOURCE_ID,
            **exc.as_outcome(),
        }

    try:
        return await _capture_compositions_screenshot_unlocked()
    finally:
        locks.release()


async def _capture_compositions_screenshot_unlocked() -> dict[str, Any]:
    source = Source(
        SCREENSHOT_SOURCE_ID,
        COMPOSITIONS_URL,
        "hsreplay",
        "battlegrounds",
        description="HSReplay Battlegrounds compositions screenshot.",
    )
    scraped = await scrape_source_with_options(
        source,
        formats=["markdown", {"type": "screenshot", "fullPage": True}],
        only_main_content=False,
        headers={
            "Cookie": _hsreplay_cookie_header(),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    if not scraped.screenshot:
        raise RuntimeError("Firecrawl response did not include screenshot")

    out_dir = _screenshot_dir()
    stamp = _safe_stamp()
    image_info = _write_screenshot(scraped.screenshot, out_dir / f"{stamp}")
    payload = {
        "ok": True,
        "source_id": SCREENSHOT_SOURCE_ID,
        "url": COMPOSITIONS_URL,
        "captured_at": _now(),
        "final_url": COMPOSITIONS_URL,
        "status_code": scraped.status_code,
        "markdown_length": len(scraped.markdown),
        "metadata": _public_capture_metadata(scraped.metadata),
        **image_info,
    }
    meta_path = out_dir / f"{stamp}.json"
    _write_json_atomic(meta_path, payload)
    latest_path = out_dir / "latest.json"
    _write_json_atomic(latest_path, payload)
    payload["metadata_path"] = str(meta_path)
    payload["latest_path"] = str(latest_path)
    return payload


def _load_valid_screenshot_metadata(
    path: Path,
    *,
    screenshot_dir: Path,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("ok") is not True:
            return None
        image_path = Path(str(payload.get("image_path") or "")).resolve()
        if not image_path.is_relative_to(screenshot_dir.resolve()):
            return None
        if not image_path.is_file():
            return None
        mime = _validated_image_mime(image_path.read_bytes())
        if image_path.suffix.casefold() != _IMAGE_SUFFIXES[mime]:
            return None
        return payload
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def latest_compositions_screenshot() -> dict[str, Any] | None:
    screenshot_dir = _screenshot_dir()
    latest_path = screenshot_dir / "latest.json"
    if latest_path.exists():
        latest = _load_valid_screenshot_metadata(
            latest_path,
            screenshot_dir=screenshot_dir,
        )
        if latest is not None:
            return latest

    # Recover serving availability after legacy false-success metadata by
    # selecting the newest older artifact that still passes the same image
    # validation. Reading LKG never rewrites production metadata.
    for candidate in sorted(screenshot_dir.glob("*.json"), reverse=True):
        if candidate.name == "latest.json":
            continue
        payload = _load_valid_screenshot_metadata(
            candidate,
            screenshot_dir=screenshot_dir,
        )
        if payload is not None:
            return payload
    return None
