#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backfill_constructed_images import connect_db, load_php_config


APP_ROOT = Path(os.environ.get("KOLODAHS_APP_ROOT") or Path(__file__).resolve().parents[1])
UPLOAD_ROOT = APP_ROOT / "uploads"
OUTPUT_DIRNAME = "horizontal-art"
OUTPUT_WIDTH = 320
OUTPUT_HEIGHT = 64
SOURCE_CROP_WIDTH = 243
SOURCE_CROP_HEIGHT = 64
VISIBLE_CROP_X = 52
VISIBLE_CROP_WIDTH = SOURCE_CROP_WIDTH - VISIBLE_CROP_X
ART_X = OUTPUT_WIDTH - VISIBLE_CROP_WIDTH
FADE_WIDTH = 70
RECIPE_VERSION = "1-official-crop-soft-black"
USER_AGENT = "db.kolodahs.ru-horizontal-art/1.0"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class Candidate:
    entity_type: str
    entity_id: str
    dbf: int | None
    source_url: str
    source_kind: str


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def identify_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["identify", "-format", "%w %h", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    width, height = result.stdout.strip().split()
    return int(width), int(height)


def normalized_crop(source: Path, target: Path, source_kind: str) -> None:
    width, height = identify_size(source)
    if width < 2 or height < 2:
        raise RuntimeError(f"Source image is too small: {width}x{height}")

    if source_kind == "crop":
        command = [
            "convert",
            str(source),
            "-auto-orient",
            "-resize",
            f"{SOURCE_CROP_WIDTH}x{SOURCE_CROP_HEIGHT}^",
            "-gravity",
            "center",
            "-extent",
            f"{SOURCE_CROP_WIDTH}x{SOURCE_CROP_HEIGHT}",
            str(target),
        ]
    elif source_kind == "card":
        crop_width = min(width, max(1, round(width * 0.602)))
        crop_height = min(height, max(1, round(height * 0.115)))
        crop_x = min(width - crop_width, max(0, round(width * 0.198)))
        crop_y = min(height - crop_height, max(0, round(height * 0.125)))
        command = [
            "convert",
            str(source),
            "-auto-orient",
            "-crop",
            f"{crop_width}x{crop_height}+{crop_x}+{crop_y}",
            "+repage",
            "-resize",
            f"{SOURCE_CROP_WIDTH}x{SOURCE_CROP_HEIGHT}!",
            str(target),
        ]
    elif source_kind == "art":
        crop_width = min(width, max(1, width * 25 // 32))
        crop_height = max(1, round(crop_width * SOURCE_CROP_HEIGHT / SOURCE_CROP_WIDTH))
        if crop_height > height:
            crop_height = height
            crop_width = min(width, max(1, round(crop_height * SOURCE_CROP_WIDTH / SOURCE_CROP_HEIGHT)))

        if height * 100 >= width * 115:
            focus_percent = 29
        elif height * 100 >= width * 104:
            focus_percent = 34
        else:
            focus_percent = 42
        focus_y = height * focus_percent // 100
        crop_y = max(0, min(height - crop_height, focus_y - crop_height // 2))
        command = [
            "convert",
            str(source),
            "-auto-orient",
            "-crop",
            f"{crop_width}x{crop_height}+0+{crop_y}",
            "+repage",
            "-resize",
            f"{SOURCE_CROP_WIDTH}x{SOURCE_CROP_HEIGHT}!",
            str(target),
        ]
    else:
        raise ValueError(f"Unsupported source kind: {source_kind}")

    subprocess.run(command, check=True, capture_output=True, text=True)
    if identify_size(target) != (SOURCE_CROP_WIDTH, SOURCE_CROP_HEIGHT):
        raise RuntimeError("Normalized crop has unexpected dimensions")


def render_horizontal_art(source: Path, target: Path, source_kind: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="horizontal-art-") as tmp_dir:
        normalized = Path(tmp_dir) / "normalized.png"
        normalized_crop(source, normalized, source_kind)
        temporary = Path(tmp_dir) / "output.webp"
        transparent_width = OUTPUT_WIDTH - ART_X - FADE_WIDTH
        subprocess.run(
            [
                "convert",
                "-size",
                f"{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}",
                "xc:black",
                "(",
                str(normalized),
                "-crop",
                f"{VISIBLE_CROP_WIDTH}x{OUTPUT_HEIGHT}+{VISIBLE_CROP_X}+0",
                "+repage",
                ")",
                "-gravity",
                "northwest",
                "-geometry",
                f"+{ART_X}+0",
                "-compose",
                "over",
                "-composite",
                "(",
                "(",
                "-size",
                f"{ART_X}x{OUTPUT_HEIGHT}",
                "xc:rgba(0,0,0,1)",
                ")",
                "(",
                "-size",
                f"{FADE_WIDTH}x{OUTPUT_HEIGHT}",
                "gradient:rgba(0,0,0,1)-rgba(0,0,0,0)",
                "-rotate",
                "-90",
                "+repage",
                "-resize",
                f"{FADE_WIDTH}x{OUTPUT_HEIGHT}!",
                ")",
                "(",
                "-size",
                f"{transparent_width}x{OUTPUT_HEIGHT}",
                "xc:none",
                ")",
                "+append",
                ")",
                "-gravity",
                "northwest",
                "-compose",
                "over",
                "-composite",
                "-strip",
                "-quality",
                "88",
                "-define",
                "webp:method=6",
                str(temporary),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if identify_size(temporary) != (OUTPUT_WIDTH, OUTPUT_HEIGHT):
            raise RuntimeError("Horizontal art has unexpected dimensions")
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)


def output_filename(entity_id: str) -> str:
    if SAFE_ID_RE.fullmatch(entity_id):
        return entity_id + ".webp"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", entity_id).strip("_") or "asset"
    digest = hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:10]
    return f"{safe}-{digest}.webp"


def output_public_path(candidate: Candidate) -> str:
    return f"/uploads/{OUTPUT_DIRNAME}/{candidate.entity_type}/{output_filename(candidate.entity_id)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build horizontal artwork variants for database entities.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=12)
    parser.parse_args()
    raise RuntimeError("Database synchronization is not implemented yet")


if __name__ == "__main__":
    raise SystemExit(main())
