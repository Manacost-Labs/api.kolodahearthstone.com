from __future__ import annotations

import io
import json
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

from .config import data_dir
from .firecrawl_backend import (
    _decode_inline_screenshot,
    _download_https_screenshot,
    _image_mime,
    scrape_source_with_options,
)
from .hsreplay_auth import hsreplay_cookies_for_fetch
from .refresh_log import log_action
from .resource_locks import ResourceLocked, ResourceLockSet
from .source_state import SourceState
from .sources import Source
from .storage import save_status

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
_CONTENT_THUMBNAIL_SIZE = (640, 640)
_CONTENT_GRID_COLUMNS = 8
_CONTENT_GRID_ROWS = 6
_BLANK_MAX_QUANTIZED_ENTROPY = 0.8
_BLANK_MIN_DOMINANT_LUMINANCE_FRACTION = 0.90
_BLANK_MIN_ACTIVE_TILES = 8
_BLANK_MIN_ACTIVE_BANDS = 2
_ACTIVE_TILE_MIN_STDDEV = 6.0
_ACTIVE_TILE_MIN_EDGE_FRACTION = 0.015
_EDGE_VALUE_THRESHOLD = 17
_SCREENSHOT_RETRY_MIN_AGE = timedelta(hours=23)
_SAFE_TELEMETRY_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_PUBLIC_CAPTURE_BACKENDS = frozenset(
    {"firecrawl", "scrape_do", "scrape_do_super", "scrapfly"}
)
_PUBLIC_SCREENSHOT_FIELDS = frozenset(
    {
        "ok",
        "source_id",
        "url",
        "captured_at",
        "final_url",
        "status_code",
        "markdown_length",
        "metadata",
        "image_bytes",
        "image_mime",
        "serving_cached_asset",
    }
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
        _validate_compositions_content(temporary_path)
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


def public_compositions_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only stable public fields; never expose paths or provider artifacts."""
    public = {
        key: value for key, value in payload.items() if key in _PUBLIC_SCREENSHOT_FIELDS
    }
    public["url"] = COMPOSITIONS_URL
    public["final_url"] = COMPOSITIONS_URL
    metadata = payload.get("metadata")
    public["metadata"] = _public_capture_metadata(
        metadata if isinstance(metadata, dict) else {}
    )
    return public


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
            cropped = image.crop(_compositions_crop_box(width, height))
            cropped.save(image_path)
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise ValueError("Failed to crop screenshot compositions table") from exc


def _compositions_crop_box(width: int, height: int) -> tuple[int, int, int, int]:
    left = int(width * 0.16)
    top = int(height * 0.18)
    right = int(width * 0.84)
    bottom = min(int(height * 0.77), top + int(width * 0.48))
    return left, top, right, bottom


def _validate_compositions_image(image: Image.Image) -> None:
    """Reject an unloaded page shell without requiring a minimum row count."""
    sample = image.convert("L")
    sample.thumbnail(_CONTENT_THUMBNAIL_SIZE, Image.Resampling.BILINEAR)
    histogram = sample.histogram()
    luminance_buckets = [
        sum(histogram[start : start + 16]) for start in range(0, 256, 16)
    ]
    total_pixels = max(1, sum(luminance_buckets))
    fractions = [count / total_pixels for count in luminance_buckets if count]
    quantized_entropy = -sum(
        fraction * math.log2(fraction) for fraction in fractions
    )
    dominant_fraction = max(fractions, default=1.0)

    width, height = sample.size
    if width > 4 and height > 4:
        sample = sample.crop((2, 2, width - 2, height - 2))
        width, height = sample.size
    edges = sample.filter(ImageFilter.FIND_EDGES)
    active_tiles = 0
    active_bands: set[int] = set()
    for row in range(_CONTENT_GRID_ROWS):
        top = row * height // _CONTENT_GRID_ROWS
        bottom = (row + 1) * height // _CONTENT_GRID_ROWS
        for column in range(_CONTENT_GRID_COLUMNS):
            left = column * width // _CONTENT_GRID_COLUMNS
            right = (column + 1) * width // _CONTENT_GRID_COLUMNS
            tile = sample.crop((left, top, right, bottom))
            edge_tile = edges.crop((left, top, right, bottom))
            edge_histogram = edge_tile.histogram()
            edge_fraction = sum(edge_histogram[_EDGE_VALUE_THRESHOLD:]) / max(
                1, sum(edge_histogram)
            )
            if (
                ImageStat.Stat(tile).stddev[0] >= _ACTIVE_TILE_MIN_STDDEV
                and edge_fraction >= _ACTIVE_TILE_MIN_EDGE_FRACTION
            ):
                active_tiles += 1
                active_bands.add(row)

    # All three signals must indicate an empty shell. This deliberately allows
    # sparse post-patch tables and does not impose a row-count requirement.
    if (
        quantized_entropy < _BLANK_MAX_QUANTIZED_ENTROPY
        and dominant_fraction > _BLANK_MIN_DOMINANT_LUMINANCE_FRACTION
        and (
            active_tiles < _BLANK_MIN_ACTIVE_TILES
            or len(active_bands) < _BLANK_MIN_ACTIVE_BANDS
        )
    ):
        raise ValueError("Screenshot does not contain meaningful compositions content")


def _validate_compositions_content(image_path: Path) -> None:
    try:
        with Image.open(image_path) as image:
            _validate_compositions_image(image)
    except ValueError:
        raise
    except (Image.DecompressionBombError, OSError, SyntaxError) as exc:
        raise ValueError("Failed to validate screenshot content") from exc


def _accept_compositions_capture(scraped: Any) -> bool:
    screenshot = str(getattr(scraped, "screenshot", "") or "")
    try:
        _mime, raw = _read_screenshot(screenshot)
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            cropped = image.crop(_compositions_crop_box(width, height))
            _validate_compositions_image(cropped)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


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


def _capture_source_status(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata")
    backend = metadata.get("backend") if isinstance(metadata, dict) else None
    return {
        "source_id": SCREENSHOT_SOURCE_ID,
        "state": SourceState.OK,
        "fetched_at": payload.get("captured_at") or _now(),
        "http_status": payload.get("status_code"),
        "final_url": payload.get("final_url") or COMPOSITIONS_URL,
        "content_length": payload.get("image_bytes"),
        "backend": backend,
    }


def _capture_is_fresh(payload: dict[str, Any]) -> bool:
    captured_at = payload.get("captured_at")
    if not isinstance(captured_at, str) or not captured_at.strip():
        return False
    try:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        current = datetime.fromisoformat(_now().replace("Z", "+00:00"))
    except ValueError:
        return False
    if captured.tzinfo is None or current.tzinfo is None:
        return False
    age = current.astimezone(timezone.utc) - captured.astimezone(timezone.utc)
    return timedelta(0) <= age < _SCREENSHOT_RETRY_MIN_AGE


def _capture_failure_code(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return "invalid_screenshot_asset"
    if isinstance(exc, TimeoutError):
        return "capture_timeout"

    # Only match a closed set of provider states. The untrusted exception text
    # is never persisted or logged.
    detail = str(exc)
    if "AUTHENTICATION_FAILED" in detail:
        return "provider_authentication_failed"
    if "PAYMENT_REQUIRED" in detail:
        return "provider_credits_exhausted"
    if "RATE_LIMITED" in detail or "CONCURRENT_REQUEST_LIMIT" in detail:
        return "provider_rate_limited"
    if "ROTATION_FAILED" in detail:
        return "provider_rotation_failed"
    if "response failed content validation" in detail:
        return "content_validation_failed"
    if "did not include screenshot" in detail:
        return "missing_screenshot"
    if "no scrape providers configured" in detail:
        return "providers_unconfigured"
    if isinstance(exc, RuntimeError):
        return "provider_chain_failed"
    return "unexpected_capture_failure"


def _capture_failure_type(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return "ScreenshotValidationError"
    if isinstance(exc, TimeoutError):
        return "ScreenshotTimeout"
    if isinstance(exc, RuntimeError):
        return "ProviderChainError"
    return "ScreenshotCaptureError"


def _sanitized_capture_exception(exc: Exception, error_code: str) -> Exception:
    message = f"live screenshot capture failed (code={error_code})"
    if isinstance(exc, ValueError):
        return ValueError(message)
    return RuntimeError(message)


def _safe_telemetry_token(value: object, *, default: str = "unknown") -> str:
    normalized = str(value or "").strip()
    if _SAFE_TELEMETRY_TOKEN.fullmatch(normalized):
        return normalized
    return default


def _safe_telemetry_int(
    value: object,
    *,
    minimum: int = 0,
    maximum: int = 10_000,
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        normalized = int(value)
    except (OverflowError, ValueError):
        return None
    return normalized if minimum <= normalized <= maximum else None


def _observe_capture_result(scraped: Any, accepted: bool) -> None:
    metadata = getattr(scraped, "metadata", None)
    public_metadata = _public_capture_metadata(
        metadata if isinstance(metadata, dict) else {}
    )
    status = _safe_telemetry_int(
        getattr(scraped, "status_code", None),
        minimum=100,
        maximum=599,
    )
    request_credits = _safe_telemetry_int(getattr(scraped, "request_credits", None))
    log_action(
        "screenshot.provider.result",
        source_id=SCREENSHOT_SOURCE_ID,
        state=SourceState.OK if accepted else SourceState.PARTIAL,
        backend=public_metadata["backend"],
        http_status=status,
        extra={
            "accepted": bool(accepted),
            "request_credits": request_credits or 0,
            "error_code": "accepted" if accepted else "content_rejected",
        },
    )


def _observe_capture_failure(event: dict[str, Any]) -> None:
    backend = str(event.get("backend") or "").strip().casefold()
    safe_extra: dict[str, Any] = {
        "error_code": _safe_telemetry_token(event.get("error_code")),
        "request_credits": _safe_telemetry_int(event.get("request_credits")) or 0,
    }
    for key in ("profile_attempt", "provider_attempt"):
        value = _safe_telemetry_int(event.get(key), minimum=1, maximum=100)
        if value is not None:
            safe_extra[key] = value
    if isinstance(event.get("super_proxy"), bool):
        safe_extra["super_proxy"] = event["super_proxy"]

    log_action(
        "screenshot.provider.fail",
        source_id=SCREENSHOT_SOURCE_ID,
        state=SourceState.FETCH_ERROR,
        backend=backend if backend in _PUBLIC_CAPTURE_BACKENDS else "unknown",
        http_status=_safe_telemetry_int(
            event.get("http_status"),
            minimum=100,
            maximum=599,
        ),
        error_type=_safe_telemetry_token(event.get("error_type")),
        extra=safe_extra,
    )


async def capture_compositions_screenshot(
    *,
    allow_cached_on_failure: bool = False,
    stale_only: bool = False,
) -> dict[str, Any]:
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
        if stale_only:
            cached = latest_compositions_screenshot()
            if cached is not None and _capture_is_fresh(cached):
                return {
                    "ok": True,
                    "published": False,
                    "skipped": True,
                    "reason": "fresh_screenshot",
                    "source_id": SCREENSHOT_SOURCE_ID,
                    "state": SourceState.OK,
                    "captured_at": cached.get("captured_at"),
                }
        try:
            captured = await _capture_compositions_screenshot_unlocked()
        except Exception as exc:  # noqa: BLE001 - provider boundary is sanitized below
            refreshed_at = _now()
            cached = latest_compositions_screenshot()
            error_code = _capture_failure_code(exc)
            log_action(
                "screenshot.capture.fail",
                source_id=SCREENSHOT_SOURCE_ID,
                state=SourceState.FETCH_ERROR,
                error_type=_capture_failure_type(exc),
                extra={
                    "error_code": error_code,
                    "cached_available": cached is not None,
                },
            )
            if not allow_cached_on_failure or cached is None:
                save_status(
                    SCREENSHOT_SOURCE_ID,
                    {
                        "source_id": SCREENSHOT_SOURCE_ID,
                        "state": SourceState.FETCH_ERROR,
                        "fetched_at": refreshed_at,
                        "last_refresh_state": SourceState.FETCH_ERROR,
                        "last_refresh_at": refreshed_at,
                        "last_refresh_error": "live screenshot capture failed",
                        "last_refresh_error_code": error_code,
                    },
                )
                raise _sanitized_capture_exception(exc, error_code) from None
            status = _capture_source_status(cached)
            status.update(
                {
                    "state": SourceState.PARTIAL,
                    "serving_cached_dataset": True,
                    "last_refresh_state": SourceState.FETCH_ERROR,
                    "last_refresh_at": refreshed_at,
                    "last_refresh_error": "live screenshot capture failed",
                    "last_refresh_error_code": error_code,
                }
            )
            save_status(SCREENSHOT_SOURCE_ID, status)
            return {
                **cached,
                "ok": True,
                "published": False,
                "serving_cached_dataset": True,
                "state": SourceState.PARTIAL,
                "reason": "capture_failed_lkg_preserved",
                "source_id": SCREENSHOT_SOURCE_ID,
            }
        save_status(SCREENSHOT_SOURCE_ID, _capture_source_status(captured))
        return captured
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
        wait_ms=8_000,
        headers={
            "Cookie": _hsreplay_cookie_header(),
            "Accept-Language": "en-US,en;q=0.9",
        },
        accept_result=_accept_compositions_capture,
        attempt_observer=_observe_capture_result,
        failure_observer=_observe_capture_failure,
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
        _validate_compositions_content(image_path)
        normalized = {
            key: value
            for key, value in payload.items()
            if key in _PUBLIC_SCREENSHOT_FIELDS
        }
        normalized.update(
            {
                "ok": True,
                "source_id": SCREENSHOT_SOURCE_ID,
                "url": COMPOSITIONS_URL,
                "final_url": COMPOSITIONS_URL,
                "metadata": _public_capture_metadata(
                    payload.get("metadata")
                    if isinstance(payload.get("metadata"), dict)
                    else {}
                ),
                "image_path": str(image_path),
                "image_bytes": image_path.stat().st_size,
                "image_mime": mime,
            }
        )
        return normalized
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
            latest["serving_cached_asset"] = False
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
            payload["serving_cached_asset"] = True
            return payload
    return None


def compositions_screenshot_asset_quality_report() -> dict[str, Any]:
    """Validate the resolved public screenshot as a binary image asset."""
    screenshot = latest_compositions_screenshot()
    if screenshot is None:
        return {
            "ok": False,
            "reason": "missing or invalid screenshot asset",
            "asset_type": "image",
            "asset_mime": None,
            "asset_bytes": None,
        }

    mime = screenshot.get("image_mime")
    size = screenshot.get("image_bytes")
    if (
        mime not in _IMAGE_SUFFIXES
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
    ):
        return {
            "ok": False,
            "reason": "missing or invalid screenshot asset",
            "asset_type": "image",
            "asset_mime": None,
            "asset_bytes": None,
        }

    return {
        "ok": True,
        "reason": (
            "valid cached fallback screenshot asset"
            if screenshot.get("serving_cached_asset") is True
            else "ok"
        ),
        "asset_type": "image",
        "asset_mime": mime,
        "asset_bytes": size,
        "captured_at": screenshot.get("captured_at"),
        "serving_cached_asset": screenshot.get("serving_cached_asset") is True,
    }
