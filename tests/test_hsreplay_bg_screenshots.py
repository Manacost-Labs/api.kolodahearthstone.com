from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from app import cli
from app.hsreplay_bg_screenshots import (
    SCREENSHOT_SOURCE_ID,
    _accept_compositions_capture,
    _public_capture_metadata,
    _write_screenshot,
    capture_compositions_screenshot,
    latest_compositions_screenshot,
    public_compositions_screenshot,
)
from app.resource_locks import ResourceLocked


def _valid_png_bytes() -> bytes:
    buffer = BytesIO()
    image = Image.new("RGB", (1_200, 800), color=(31, 24, 38))
    draw = ImageDraw.Draw(image)
    for row in range(8):
        top = 160 + row * 65
        draw.rectangle((180, top, 1_020, top + 52), fill=(72, 51, 74))
        draw.rectangle((205, top + 12, 340, top + 40), fill=(138, 104, 143))
        for column in range(7):
            left = 580 + column * 55
            draw.ellipse(
                (left, top + 5, left + 42, top + 47),
                fill=(170 + row * 4, 105 + column * 8, 48 + row * 5),
            )
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _valid_png_data_uri() -> str:
    encoded = base64.b64encode(_valid_png_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _blank_shell_png_data_uri() -> str:
    buffer = BytesIO()
    image = Image.new("RGB", (1_036, 841), color=(22, 12, 26))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 700, 1_036, 841), fill=(34, 24, 35))
    draw.rectangle((0, 735, 145, 750), fill=(240, 235, 240))
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def test_write_screenshot_accepts_and_preserves_a_valid_image(tmp_path: Path) -> None:
    result = _write_screenshot(_valid_png_data_uri(), tmp_path / "capture")

    image_path = Path(result["image_path"])
    assert image_path.suffix == ".png"
    assert image_path.stat().st_size == result["image_bytes"]
    with Image.open(image_path) as image:
        image.verify()


def test_write_screenshot_rejects_a_valid_but_blank_page_shell(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="meaningful compositions content"):
        _write_screenshot(_blank_shell_png_data_uri(), tmp_path / "capture")

    assert list(tmp_path.iterdir()) == []


def test_provider_validation_rejects_blank_shell_and_accepts_sparse_content() -> None:
    assert not _accept_compositions_capture(
        SimpleNamespace(screenshot=_blank_shell_png_data_uri())
    )
    assert _accept_compositions_capture(
        SimpleNamespace(screenshot=_valid_png_data_uri())
    )


@pytest.mark.parametrize(
    "invalid_value",
    [
        "plain text returned instead of an image",
        "<html><body>upstream error</body></html>",
        "data:image/png;base64,"
        + base64.b64encode(b"<html>not a png</html>").decode("ascii"),
    ],
)
def test_write_screenshot_rejects_text_and_html(
    tmp_path: Path,
    invalid_value: str,
) -> None:
    with pytest.raises(ValueError, match="valid screenshot image"):
        _write_screenshot(invalid_value, tmp_path / "capture")

    assert list(tmp_path.iterdir()) == []


def test_public_capture_metadata_never_exposes_provider_response_values() -> None:
    metadata = _public_capture_metadata(
        {
            "backend": "firecrawl",
            "sourceURL": "https://signed.example/?token=private",
            "firecrawl_key_label": "private-key-label",
        }
    )

    assert metadata == {"backend": "firecrawl"}
    assert _public_capture_metadata({"backend": "private-provider-token"}) == {
        "backend": "unknown"
    }


def test_public_screenshot_never_exposes_paths_or_legacy_provider_data() -> None:
    payload = {
        "ok": True,
        "captured_at": "2026-08-04T02:10:00+00:00",
        "image_path": "/private/runtime/capture.png",
        "metadata_path": "/private/runtime/capture.json",
        "firecrawl_screenshot_url": "https://signed.example/?token=private",
        "metadata": {
            "backend": "firecrawl",
            "firecrawl_key_label": "private-key-label",
        },
    }

    assert public_compositions_screenshot(payload) == {
        "ok": True,
        "captured_at": "2026-08-04T02:10:00+00:00",
        "url": "https://hsreplay.net/battlegrounds/compositions/",
        "final_url": "https://hsreplay.net/battlegrounds/compositions/",
        "metadata": {"backend": "firecrawl"},
    }


def test_capture_failure_preserves_last_known_good_latest(tmp_path: Path) -> None:
    old_image = tmp_path / "last-good.png"
    old_image.write_bytes(_valid_png_bytes())
    old_payload = {
        "ok": True,
        "source_id": "hsreplay_battlegrounds_compositions_screenshot",
        "captured_at": "2026-08-04T02:10:00+00:00",
        "image_path": str(old_image),
        "image_bytes": old_image.stat().st_size,
    }
    latest_path = tmp_path / "latest.json"
    latest_path.write_text(json.dumps(old_payload), encoding="utf-8")
    scraped = SimpleNamespace(
        screenshot="<html><body>Firecrawl error</body></html>",
        final_url="https://hsreplay.net/battlegrounds/compositions/",
        status_code=200,
        markdown="",
        metadata={"backend": "firecrawl"},
    )

    with (
        patch(
            "app.hsreplay_bg_screenshots.scrape_source_with_options",
            new=AsyncMock(return_value=scraped),
        ),
        patch("app.hsreplay_bg_screenshots._screenshot_dir", return_value=tmp_path),
        patch(
            "app.hsreplay_bg_screenshots._safe_stamp",
            return_value="20260812T021000Z",
        ),
        pytest.raises(ValueError, match="valid screenshot image"),
    ):
        asyncio.run(capture_compositions_screenshot())

    assert json.loads(latest_path.read_text(encoding="utf-8")) == old_payload
    assert not (tmp_path / "20260812T021000Z.json").exists()
    assert list(tmp_path.glob("*.txt")) == []

    with patch("app.hsreplay_bg_screenshots._screenshot_dir", return_value=tmp_path):
        loaded = latest_compositions_screenshot()
    assert loaded is not None
    assert loaded["image_path"] == old_payload["image_path"]
    assert loaded["captured_at"] == old_payload["captured_at"]


def test_capture_returns_honest_skip_when_source_is_locked() -> None:
    lock = MagicMock()
    lock.acquire.side_effect = ResourceLocked(
        SCREENSHOT_SOURCE_ID,
        {"pid": 4242, "operation": "capture_compositions_screenshot"},
    )
    scrape = AsyncMock()

    with (
        patch(
            "app.hsreplay_bg_screenshots.ResourceLockSet",
            return_value=lock,
        ) as lock_set,
        patch(
            "app.hsreplay_bg_screenshots.scrape_source_with_options",
            new=scrape,
        ),
    ):
        result = asyncio.run(capture_compositions_screenshot())

    assert result == {
        "ok": True,
        "published": False,
        "source_id": SCREENSHOT_SOURCE_ID,
        "state": "locked",
        "skipped": True,
        "reason": "resource_locked",
        "locked_resource": SCREENSHOT_SOURCE_ID,
        "owner": {"pid": 4242, "operation": "capture_compositions_screenshot"},
    }
    lock_set.assert_called_once_with(
        [SCREENSHOT_SOURCE_ID],
        metadata={"operation": "capture_compositions_screenshot"},
    )
    lock.release.assert_not_called()
    scrape.assert_not_awaited()


def test_capture_cli_treats_a_resource_lock_as_successful_skip(
    capsys: pytest.CaptureFixture[str],
) -> None:
    locked_result = {
        "ok": True,
        "published": False,
        "source_id": SCREENSHOT_SOURCE_ID,
        "state": "locked",
        "skipped": True,
        "reason": "resource_locked",
        "locked_resource": SCREENSHOT_SOURCE_ID,
        "owner": {"pid": 4242},
    }
    with (
        patch(
            "app.hsreplay_bg_screenshots.capture_compositions_screenshot",
            new=AsyncMock(return_value=locked_result),
        ),
        patch("app.reliability_telemetry.record_terminal_results"),
    ):
        exit_code = cli.main(["capture-bg-compositions-screenshot"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == locked_result


def test_latest_rejects_a_text_artifact_marked_as_success(tmp_path: Path) -> None:
    invalid_image = tmp_path / "capture.txt"
    invalid_image.write_text("<html>not an image</html>", encoding="utf-8")
    (tmp_path / "latest.json").write_text(
        json.dumps(
            {
                "ok": True,
                "source_id": "hsreplay_battlegrounds_compositions_screenshot",
                "image_path": str(invalid_image),
                "image_bytes": invalid_image.stat().st_size,
            }
        ),
        encoding="utf-8",
    )

    with patch("app.hsreplay_bg_screenshots._screenshot_dir", return_value=tmp_path):
        assert latest_compositions_screenshot() is None


def test_latest_falls_back_to_newest_valid_historical_image(tmp_path: Path) -> None:
    invalid_image = tmp_path / "20260812T021000Z.txt"
    invalid_image.write_text("<html>not an image</html>", encoding="utf-8")
    invalid_payload = {
        "ok": True,
        "image_path": str(invalid_image),
        "captured_at": "2026-08-12T02:10:00+00:00",
    }
    (tmp_path / "latest.json").write_text(
        json.dumps(invalid_payload),
        encoding="utf-8",
    )

    old_image = tmp_path / "20260804T021000Z.png"
    old_image.write_bytes(_valid_png_bytes())
    old_payload = {
        "ok": True,
        "image_path": str(old_image),
        "image_bytes": old_image.stat().st_size,
        "captured_at": "2026-08-04T02:10:00+00:00",
    }
    (tmp_path / "20260804T021000Z.json").write_text(
        json.dumps(old_payload),
        encoding="utf-8",
    )

    with patch("app.hsreplay_bg_screenshots._screenshot_dir", return_value=tmp_path):
        loaded = latest_compositions_screenshot()
    assert loaded is not None
    assert loaded["image_path"] == old_payload["image_path"]
    assert loaded["captured_at"] == old_payload["captured_at"]


def test_latest_falls_back_when_newest_image_is_a_blank_page_shell(
    tmp_path: Path,
) -> None:
    blank_image = tmp_path / "20260812T120148Z.png"
    blank_encoded = _blank_shell_png_data_uri().partition(",")[2]
    blank_image.write_bytes(base64.b64decode(blank_encoded))
    blank_payload = {
        "ok": True,
        "image_path": str(blank_image),
        "image_bytes": blank_image.stat().st_size,
        "captured_at": "2026-08-12T12:01:48+00:00",
    }
    (tmp_path / "latest.json").write_text(
        json.dumps(blank_payload),
        encoding="utf-8",
    )

    old_image = tmp_path / "20260804T021000Z.png"
    old_image.write_bytes(_valid_png_bytes())
    old_payload = {
        "ok": True,
        "image_path": str(old_image),
        "image_bytes": old_image.stat().st_size,
        "captured_at": "2026-08-04T02:10:00+00:00",
    }
    (tmp_path / "20260804T021000Z.json").write_text(
        json.dumps(old_payload),
        encoding="utf-8",
    )

    with patch("app.hsreplay_bg_screenshots._screenshot_dir", return_value=tmp_path):
        loaded = latest_compositions_screenshot()
    assert loaded is not None
    assert loaded["image_path"] == old_payload["image_path"]
    assert loaded["captured_at"] == old_payload["captured_at"]
