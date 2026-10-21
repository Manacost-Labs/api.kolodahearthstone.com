#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pymysql
from backfill_constructed_images import connect_db, load_php_config
from PIL import Image

APP_ROOT = Path(
    os.environ.get("KOLODAHS_APP_ROOT") or Path(__file__).resolve().parents[1]
)
UPLOAD_ROOT = APP_ROOT / "uploads"
# Each recipe writes beside, never over, the files of the previous one: the
# database row switches to the new URL only after both renditions exist, and
# restoring the old rows rolls the API back to the untouched old files.
OUTPUT_DIRNAME = "horizontal-art/r8"
OUTPUT_WIDTH = 640
OUTPUT_HEIGHT = 128
SMALL_WIDTH = 320
SMALL_HEIGHT = 64
ART_X = 192
ART_WIDTH = OUTPUT_WIDTH - ART_X
ART_ASPECT = ART_WIDTH / OUTPUT_HEIGHT
FADE_END = 384
MAX_SOURCE_PIXELS = 24_000_000
TILE_PLACEHOLDER_FRACTION = 0.25
TILE_MATCH_MIN_SCORE = 0.85
RECIPE_VERSION = "8-hires-focus-2x"
SOURCE_KINDS = ("art", "strip", "tile")
HSJ_ART = "https://art.hearthstonejson.com/v1/"
FOCUS_OVERRIDES_PATH = APP_ROOT / "data" / "horizontal_art_focus.json"
ENTITY_TYPES = (
    "battleground_card",
    "coin",
    "constructed_card",
    "hero",
    "hero_skin",
    "library_card",
    "pet",
    "timewarped_card",
)
USER_AGENT = "db.kolodahs.ru-horizontal-art/1.0"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
REPRINT_PREFIXES = ("CORE_", "VAN_", "LEG_")


@dataclass(frozen=True, slots=True)
class Candidate:
    entity_type: str
    entity_id: str
    dbf: int | None
    source_url: str
    source_kind: str
    fallback_sources: tuple[tuple[str, str], ...] = ()
    # In-game deck tiles locate the subject that designers framed for decks.
    focus_tiles: tuple[str, ...] = ()


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS horizontal_art_assets (
                entity_type VARCHAR(32) NOT NULL,
                entity_id VARCHAR(160) NOT NULL,
                source_url VARCHAR(1024) NOT NULL,
                source_kind VARCHAR(16) NOT NULL,
                source_signature CHAR(64) NOT NULL,
                local_image_url VARCHAR(512) DEFAULT NULL,
                image_sha256 CHAR(64) DEFAULT NULL,
                recipe_version VARCHAR(64) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'ready',
                last_error TEXT DEFAULT NULL,
                generated_at TIMESTAMP NULL DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (entity_type, entity_id),
                KEY idx_horizontal_art_status (status),
                KEY idx_horizontal_art_generated (generated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    conn.commit()


def query_rows(conn, sql: str) -> list[dict[str, Any]]:
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return list(cur.fetchall())
    except pymysql.err.ProgrammingError as exc:
        if exc.args and int(exc.args[0]) == 1146:
            return []
        raise


def available_sources(
    row: dict[str, Any], candidates: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for column, source_kind in candidates:
        value = str(row.get(column) or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append((value, source_kind))
    return result


def game_card_ids(card_id: str) -> list[str]:
    """The card itself, then the original card of a Core/Classic/Legacy reprint."""
    if not card_id or not SAFE_ID_RE.fullmatch(card_id):
        return []
    ids = [card_id]
    for prefix in REPRINT_PREFIXES:
        if card_id.startswith(prefix) and len(card_id) > len(prefix):
            ids.append(card_id[len(prefix) :])
            break
    return ids


def hsj_original_art(card_ids: list[str]) -> list[tuple[str, str]]:
    # HearthstoneJSON's original textures are the game's own illustrations,
    # without a frame, text or rarity gem.
    return [
        (HSJ_ART + "orig/" + urllib.parse.quote(card_id, safe="") + ".png", "art")
        for card_id in card_ids
    ]


def hsj_tiles(card_ids: list[str]) -> list[str]:
    return [
        HSJ_ART + "tiles/" + urllib.parse.quote(card_id, safe="") + ".webp"
        for card_id in card_ids
    ]


def official_strips(payload_json: Any) -> list[tuple[str, str]]:
    """Illustration strips retained from reveal data of not yet released cards."""
    try:
        payload = json.loads(payload_json or "{}")
    except (ValueError, TypeError):
        return []
    result: list[tuple[str, str]] = []
    if not isinstance(payload, dict):
        return result
    for key in (
        "official_reveal_en",
        "official_reveal_ru",
        "blizzard_en",
        "blizzard_ru",
    ):
        item = payload.get(key)
        url = item.get("cropImage") if isinstance(item, dict) else None
        if not isinstance(url, str):
            continue
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme == "https"
            and parsed.netloc == "d15f34w2p8l1cc.cloudfront.net"
            and parsed.path.startswith("/hearthstone/")
            and (url, "strip") not in result
        ):
            result.append((url, "strip"))
    return result


def make_candidate(
    entity_type: str,
    entity_id: str,
    dbf: int | None,
    sources: list[tuple[str, str]],
    focus_tiles: list[str] | None = None,
) -> Candidate | None:
    unique: list[tuple[str, str]] = []
    for source in sources:
        if source[1] in SOURCE_KINDS and source not in unique:
            unique.append(source)
    if not unique:
        return None
    return Candidate(
        entity_type,
        entity_id,
        dbf,
        unique[0][0],
        unique[0][1],
        tuple(unique[1:]),
        tuple(focus_tiles or ()),
    )


def collect_candidates(conn) -> list[Candidate]:
    """List clean illustration sources; rendered cards are never a source.

    Local and wiki full art come first because of their resolution, then the
    512px HearthstoneJSON texture. A constructed card's in-game deck tile is the
    last-resort, low-resolution source and also locates the subject in the full
    art. Other entity types do not appear in decks; their tiles use a generic
    framing and are not used.
    """
    candidates: list[Candidate] = []

    def dbf_of(row: dict[str, Any]) -> int | None:
        return int(row["dbf"]) if row.get("dbf") is not None else None

    for row in query_rows(
        conn,
        """
        SELECT card_id, dbf, local_wiki_full_art_url, wiki_full_art_url,
               CASE WHEN card_id LIKE 'blizzard:%' THEN source_payload_json
                    ELSE NULL END AS source_payload_json
          FROM constructed_cards
        """,
    ):
        card_id = str(row["card_id"])
        ids = game_card_ids(card_id)
        sources = available_sources(row, [("local_wiki_full_art_url", "art")])
        sources += hsj_original_art(ids)
        sources += available_sources(row, [("wiki_full_art_url", "art")])
        if card_id.startswith("blizzard:"):
            sources += official_strips(row.get("source_payload_json"))
        sources += [(url, "tile") for url in hsj_tiles(ids)]
        candidate = make_candidate(
            "constructed_card", card_id, dbf_of(row), sources, hsj_tiles(ids)
        )
        if candidate:
            candidates.append(candidate)

    query_specs = [
        (
            "battleground_card",
            "SELECT card_id, card_id AS game_id, dbf, art_image FROM battlegrounds_cards",
            ["art_image"],
            lambda row: str(row["card_id"]),
        ),
        (
            "library_card",
            (
                "SELECT library, card_id, card_id AS game_id, dbf, local_full_art_url, "
                "full_art_source_url FROM battlegrounds_library_cards"
            ),
            ["local_full_art_url", "full_art_source_url"],
            lambda row: f"{row['library']}:{row['card_id']}",
        ),
        (
            "hero",
            (
                "SELECT card_id, card_id AS game_id, dbf, hero_full_art_url "
                "FROM battlegrounds_heroes"
            ),
            ["hero_full_art_url"],
            lambda row: str(row["card_id"]),
        ),
        (
            "hero_skin",
            "SELECT card_id, card_id AS game_id, dbf, full_art_url FROM hero_skins",
            ["full_art_url"],
            lambda row: str(row["card_id"]),
        ),
        (
            # Pets have no HearthstoneJSON texture; their end-of-game scene is
            # the only frameless illustration. Card renders are excluded.
            "pet",
            (
                "SELECT variant_id, card_id, NULL AS game_id, dbf, "
                "end_screen_background_url FROM hearthstone_pets"
            ),
            ["end_screen_background_url"],
            lambda row: str(row.get("card_id") or f"variant:{row['variant_id']}"),
        ),
        (
            # The coin wiki image and image_url are rendered coins.
            "coin",
            "SELECT card_id, card_id AS game_id, dbf FROM hearthstone_coins",
            [],
            lambda row: str(row["card_id"]),
        ),
        (
            "timewarped_card",
            (
                "SELECT card_id, card_id AS game_id, dbf, art_image_url "
                "FROM battlegrounds_timewarped_cards"
            ),
            ["art_image_url"],
            lambda row: str(row["card_id"]),
        ),
    ]
    for entity_type, sql, columns, entity_id_for in query_specs:
        for row in query_rows(conn, sql):
            sources = available_sources(row, [(column, "art") for column in columns])
            game_art = hsj_original_art(game_card_ids(str(row.get("game_id") or "")))
            # Wiki hero art is sometimes a wide promotional panorama; the game
            # texture keeps the portrait the hero is recognized by.
            remote = sources and not sources[0][0].startswith("/uploads/")
            sources = game_art + sources if remote else sources + game_art
            candidate = make_candidate(
                entity_type, entity_id_for(row), dbf_of(row), sources
            )
            if candidate:
                candidates.append(candidate)

    deduplicated = {(item.entity_type, item.entity_id): item for item in candidates}
    return sorted(
        deduplicated.values(), key=lambda item: (item.entity_type, item.entity_id)
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def identify_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def load_focus_overrides() -> dict[str, tuple[float, float]]:
    """Manual subject positions for artwork the heuristics frame poorly."""
    try:
        data = json.loads(FOCUS_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    result = {}
    for key, value in (data.get("focus") or {}).items():
        x, y = float(value["x"]), float(value["y"])
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError(f"Focus override {key} is outside the artwork")
        result[str(key)] = (x, y)
    return result


def load_art(path: Path) -> Image.Image:
    with Image.open(path) as image:
        image.load()
        if image.width < 2 or image.height < 2:
            raise RuntimeError(
                f"Source image is too small: {image.width}x{image.height}"
            )
        if image.width * image.height > MAX_SOURCE_PIXELS:
            image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
        return image.convert("RGBA")


def trim_padding(image: Image.Image) -> Image.Image:
    """Remove uniform white, gray, black or transparent padding on every edge.

    Unity textures of new cards pad portrait art with white columns; older
    textures contain gray packing bars. A row or column is padding when most of
    its pixels are neutral light, black or transparent and these pixels share
    one color; bright subjects touching an edge are colorful or textured.
    """
    pixels = np.asarray(image, dtype=np.float32)
    rgb, alpha = pixels[..., :3], pixels[..., 3]
    low, high = rgb.min(-1), rgb.max(-1)
    pad = (alpha < 16) | ((low >= 196) & (high - low <= 14)) | (high <= 10)

    def padding(line_pad: np.ndarray, line_rgb: np.ndarray) -> bool:
        if line_pad.size == 0 or line_pad.mean() < 0.6:
            return False
        colors = line_rgb[line_pad]
        return colors.size == 0 or float(colors.std(0).max()) <= 14

    height, width = pad.shape
    top, bottom, left, right = 0, height, 0, width
    while top < height * 0.35 and padding(pad[top, left:right], rgb[top, left:right]):
        top += 1
    while bottom > height * 0.65 and padding(
        pad[bottom - 1, left:right], rgb[bottom - 1, left:right]
    ):
        bottom -= 1
    while left < width * 0.35 and padding(pad[top:bottom, left], rgb[top:bottom, left]):
        left += 1
    while right > width * 0.65 and padding(
        pad[top:bottom, right - 1], rgb[top:bottom, right - 1]
    ):
        right -= 1
    # Texture edges often carry a one-pixel light or dark seam.
    inset_x = max(1, round((right - left) * 0.006))
    inset_y = max(1, round((bottom - top) * 0.006))
    return image.crop(
        (left + inset_x, top + inset_y, right - inset_x, bottom - inset_y)
    )


def gray(image: Image.Image, size: tuple[int, int]) -> np.ndarray:
    return np.asarray(
        image.convert("L").resize(size, Image.Resampling.BOX), dtype=np.float32
    )


def normalized_correlation(art: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Normalized cross-correlation of a template at every valid position."""
    art_height, art_width = art.shape
    height, width = template.shape
    centered = template - template.mean()
    template_norm = float(np.sqrt((centered * centered).sum())) + 1e-6
    integral = np.pad(art, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    integral_sq = np.pad(art * art, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def window_sum(table: np.ndarray) -> np.ndarray:
        return (
            table[height:, width:]
            - table[:-height, width:]
            - table[height:, :-width]
            + table[:-height, :-width]
        )

    total, total_sq = window_sum(integral), window_sum(integral_sq)
    variance = np.maximum(total_sq - total * total / (height * width), 1e-3)
    shape = (art_height + height, art_width + width)
    correlation = np.fft.irfft2(
        np.fft.rfft2(art, shape) * np.conj(np.fft.rfft2(centered, shape)), shape
    )[: art_height - height + 1, : art_width - width + 1]
    return correlation / (np.sqrt(variance) * template_norm)


def locate_game_tile(
    art: Image.Image, tile: Image.Image
) -> tuple[float, float, float, float, float]:
    """Find the in-game deck tile inside the full illustration.

    Returns the normalized x, y, width and height of the tile's region and the
    correlation score. The tile is a stretched view, so width and height are
    searched independently. New HearthstoneJSON tiles carry a white placeholder
    on the left; only the remainder is compared.
    """
    skip = TILE_PLACEHOLDER_FRACTION
    tile = tile.crop((round(tile.width * skip), 0, tile.width, tile.height))
    best = (-2.0, 0.0, 0.0, 0.0, 0.0)
    size = 96
    coarse = gray(art, (size, size))
    for width_fraction in np.linspace(0.30, 1.0, 29):
        width = round(size * width_fraction * (1 - skip))
        for height_fraction in np.linspace(0.08, 0.55, 24):
            height = round(size * height_fraction)
            if height < 5 or width < 8:
                continue
            scores = normalized_correlation(coarse, gray(tile, (width, height)))
            y, x = np.unravel_index(int(np.argmax(scores)), scores.shape)
            if scores[y, x] > best[0]:
                best = (
                    float(scores[y, x]),
                    x / size,
                    y / size,
                    width / size,
                    height / size,
                )

    size = 288
    fine_art = gray(art, (size, size))
    _, best_x, best_y, best_width, best_height = best
    fine = best
    for width_delta in range(-6, 7, 2):
        width = round(best_width * size) + width_delta
        for height_delta in range(-6, 7, 2):
            height = round(best_height * size) + height_delta
            if width < 8 or height < 5:
                continue
            left = max(0, int(best_x * size) - 9)
            top = max(0, int(best_y * size) - 9)
            window = fine_art[
                top : min(size, int(best_y * size) + height + 9),
                left : min(size, int(best_x * size) + width + 9),
            ]
            if window.shape[0] < height or window.shape[1] < width:
                continue
            scores = normalized_correlation(window, gray(tile, (width, height)))
            y, x = np.unravel_index(int(np.argmax(scores)), scores.shape)
            if scores[y, x] > fine[0]:
                fine = (
                    float(scores[y, x]),
                    (left + x) / size,
                    (top + y) / size,
                    width / size,
                    height / size,
                )
    score, x, y, width, height = fine
    full_width = width / (1 - skip)
    return x - full_width * skip, y, full_width, height, score


def subject_focus(image: Image.Image) -> tuple[float, float]:
    """Find a deterministic high-detail subject area without an ML model."""
    size = 96
    pixels = np.asarray(
        image.convert("RGB").resize((size, size), Image.Resampling.BOX),
        dtype=np.float32,
    )
    luminance = pixels @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    colorfulness = pixels.max(-1) - pixels.min(-1)
    normalized = pixels / np.maximum(1.0, pixels.sum(-1, keepdims=True))
    skin_distance = np.sqrt(
        ((normalized - np.array([0.44, 0.32, 0.24], dtype=np.float32)) ** 2).sum(-1)
    )
    skin = np.where(
        (luminance >= 45) & (luminance <= 245) & (colorfulness >= 12),
        np.maximum(0.0, 1.0 - skin_distance / 0.19),
        0.0,
    )
    edges = np.zeros_like(luminance)
    edges[1:-1, 1:-1] = np.abs(luminance[1:-1, 2:] - luminance[1:-1, :-2]) + np.abs(
        luminance[2:, 1:-1] - luminance[:-2, 1:-1]
    )
    detail = np.zeros_like(luminance)
    detail[1:-1, 1:-1] = (
        edges + colorfulness * 0.10 + skin * np.minimum(1.0, edges / 28.0) * 240.0
    )[1:-1, 1:-1]

    radius = 8
    weights = (radius + 1 - np.abs(np.arange(-radius, radius + 1))).astype(np.float32)
    padded = np.pad(detail, radius)
    rows = sum(padded[:, i : i + size] * weights[i] for i in range(2 * radius + 1))
    windows = sum(rows[i : i + size, :] * weights[i] for i in range(2 * radius + 1))

    portrait = image.height >= image.width
    expected_y = 0.31 if portrait else 0.44
    axis = np.arange(size) / (size - 1)
    prior = np.maximum(0.48, 1.0 - np.abs(axis - expected_y) * 1.22)[:, None]
    prior = prior * np.maximum(0.88, 1.0 - np.abs(axis - 0.5) * 0.16)[None, :]
    scores = np.full_like(windows, -1.0)
    bottom = 36 if portrait else 70
    scores[12:bottom, 12:84] = (windows * prior)[12:bottom, 12:84]
    y, x = np.unravel_index(int(np.argmax(scores)), scores.shape)
    focus_x, focus_y = x / (size - 1), y / (size - 1)
    if portrait:
        # Portrait Hearthstone art normally keeps the face in the upper torso.
        # Salient weapons, hands and spell effects below it must not steal focus.
        focus_x = max(0.30, min(0.58, focus_x))
        focus_y = max(0.18, min(0.32, focus_y))
    return focus_x, focus_y


def plan_crop(
    width: int, height: int, focus_x: float, focus_y: float
) -> tuple[float, float, float, float]:
    """Choose an undistorted crop of the art area's aspect around the subject."""
    subject_x, subject_y = focus_x * width, focus_y * height
    if width / height > ART_ASPECT:
        crop_height = float(height)
        crop_width = crop_height * ART_ASPECT
    else:
        crop_width = float(width)
        crop_height = crop_width / ART_ASPECT
        # A subject on the far left would sit under the text fade: zoom in.
        if subject_x / crop_width < 0.45:
            crop_width = max(width * 0.62, subject_x / 0.5)
            crop_height = crop_width / ART_ASPECT
    left = min(max(0.0, subject_x - crop_width * 0.6), width - crop_width)
    top = min(max(0.0, subject_y - crop_height * 0.45), height - crop_height)
    return left, top, crop_width, crop_height


def compose(art: Image.Image, crop: tuple[float, float, float, float]) -> Image.Image:
    left, top, crop_width, crop_height = crop
    region = art.resize(
        (ART_WIDTH, OUTPUT_HEIGHT),
        Image.Resampling.LANCZOS,
        box=(left, top, left + crop_width, top + crop_height),
    )
    canvas = Image.new("RGBA", (OUTPUT_WIDTH, OUTPUT_HEIGHT), (0, 0, 0, 0))
    canvas.paste(region, (ART_X, 0))
    pixels = np.asarray(canvas, dtype=np.float32).copy()
    # Fade the artwork's alpha, rather than painting a black overlay.
    # Smoothstep has flat ends, so the tile background has no hard seam.
    fade = np.clip(
        (np.arange(OUTPUT_WIDTH, dtype=np.float32) - ART_X) / (FADE_END - ART_X), 0, 1
    )
    pixels[..., 3] *= (fade * fade * (3 - 2 * fade))[None, :]
    return Image.fromarray(np.clip(pixels + 0.5, 0, 255).astype(np.uint8), "RGBA")


def quality_report(image: Image.Image) -> dict[str, float]:
    """Measure artifacts in the fully visible part of a rendered image."""
    pixels = np.asarray(image, dtype=np.float32)[:, FADE_END:]
    rgb, alpha = pixels[..., :3], pixels[..., 3]
    low, high = rgb.min(-1), rgb.max(-1)
    luminance = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    return {
        "transparent": round(float((alpha < 250).mean()), 4),
        "white_columns": round(float(((low >= 235).mean(0) >= 0.92).mean()), 4),
        "black_columns": round(float(((high <= 6).mean(0) >= 0.95).mean()), 4),
        "detail": round(float(luminance.std()), 2),
    }


def check_quality(report: dict[str, float]) -> None:
    if report["detail"] < 4:
        raise RuntimeError("Horizontal art is blank")
    if report["transparent"] > 0.02:
        raise RuntimeError("Horizontal art has transparent holes")
    if report["white_columns"] > 0.05:
        raise RuntimeError("Horizontal art has a white margin")
    if report["black_columns"] > 0.25:
        raise RuntimeError("Horizontal art has a black block")


def retina_path(path: Path) -> Path:
    """The 640x128 rendition lives beside the 320x64 public file."""
    return path.with_name(path.name.removesuffix(".webp") + "@2x.webp")


def render_horizontal_art(
    source: Path,
    target: Path,
    source_kind: str,
    *,
    tile: Path | None = None,
    focus: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Write the 320x64 target and its 2x companion; return the framing report."""
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"Unsupported source kind: {source_kind}")
    art = trim_padding(load_art(source))
    method = "override" if focus else "subject"
    score = None
    if focus is None and tile is not None and source_kind == "art":
        x, y, width, height, score = locate_game_tile(art, load_art(tile))
        if score >= TILE_MATCH_MIN_SCORE:
            focus = (
                min(1.0, max(0.0, x + width / 2)),
                min(1.0, max(0.0, y + height / 2)),
            )
            method = "game_tile"
    if focus is None and source_kind in ("strip", "tile"):
        # Illustration strips are already framed around the subject; the left
        # quarter of game tiles holds the placeholder and the text gradient.
        focus = (0.62, 0.5)
        method = "strip"
    if focus is None:
        focus = subject_focus(art)
    image = compose(art, plan_crop(art.width, art.height, *focus))
    report = quality_report(image)
    check_quality(report)

    target.parent.mkdir(parents=True, exist_ok=True)
    for path, rendition in (
        (retina_path(target), image),
        (target, image.resize((SMALL_WIDTH, SMALL_HEIGHT), Image.Resampling.LANCZOS)),
    ):
        # The temporary file must share the target's filesystem: renaming from
        # /tmp onto an upload path fails with EXDEV.
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".tmp-", suffix=".webp", delete=False
        ) as handle:
            temporary = Path(handle.name)
        try:
            rendition.save(temporary, "WEBP", quality=88, method=6)
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "focus_method": method,
        "focus": [round(focus[0], 3), round(focus[1], 3)],
        "tile_score": None if score is None else round(score, 3),
        "art_size": [art.width, art.height],
        **report,
    }


def output_filename(entity_id: str) -> str:
    if SAFE_ID_RE.fullmatch(entity_id):
        return entity_id + ".webp"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", entity_id).strip("_") or "asset"
    digest = hashlib.sha256(entity_id.encode("utf-8")).hexdigest()[:10]
    return f"{safe}-{digest}.webp"


def output_public_path(candidate: Candidate) -> str:
    return f"/uploads/{OUTPUT_DIRNAME}/{candidate.entity_type}/{output_filename(candidate.entity_id)}"


def local_upload_path(public_path: str) -> Path:
    if not public_path.startswith("/uploads/"):
        raise ValueError(f"Unexpected local upload path: {public_path}")
    candidate = APP_ROOT / public_path.lstrip("/")
    if candidate.name in ("", ".", ".."):
        raise ValueError(f"Upload path escapes media root: {public_path}")
    # Keep the final link: offload replaces staged files with links into the
    # S3 mount, and renaming onto the resolved remote file fails with EXDEV.
    resolved = candidate.parent.resolve() / candidate.name
    allowed = UPLOAD_ROOT.resolve()
    if allowed not in resolved.parents:
        raise ValueError(f"Upload path escapes media root: {public_path}")
    return resolved


def local_source_path(source_url: str) -> Path | None:
    parsed = urllib.parse.urlparse(source_url)
    public_path = source_url if source_url.startswith("/uploads/") else parsed.path
    if not public_path.startswith("/uploads/"):
        return None
    candidate = local_upload_path(public_path)
    return candidate if candidate.is_file() and candidate.stat().st_size > 0 else None


def source_signature(
    candidate: Candidate, focus: tuple[float, float] | None = None
) -> str:
    source_values = []
    for source_url, source_kind in (
        (candidate.source_url, candidate.source_kind),
        *candidate.fallback_sources,
    ):
        local = local_source_path(source_url)
        if local is not None:
            stat = local.stat()
            source_values.append(
                f"local:{local}:{stat.st_size}:{stat.st_mtime_ns}:{source_kind}"
            )
        else:
            source_values.append(f"remote:{source_url}:{source_kind}")
    source_values += [f"tile:{url}" for url in candidate.focus_tiles]
    if focus is not None:
        source_values.append(f"focus:{focus[0]:.4f},{focus[1]:.4f}")
    value = "|".join((*source_values, RECIPE_VERSION))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def allowed_image_content_type(content_type: str) -> bool:
    return (
        content_type == ""
        or content_type.startswith("image/")
        or content_type == "application/octet-stream"
    )


def download_source(
    source_url: str, target: Path, max_bytes: int = 32 * 1024 * 1024
) -> None:
    parsed = urllib.parse.urlparse(source_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Remote image URL must use http or https")
    request = urllib.request.Request(
        source_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*",
        },
    )
    total = 0
    with (
        urllib.request.urlopen(request, timeout=90) as response,
        target.open("wb") as handle,
    ):
        content_type = (
            str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        )
        if not allowed_image_content_type(content_type):
            raise RuntimeError(f"Unexpected source content type: {content_type}")
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError("Source image exceeds 32 MiB")
            handle.write(chunk)
    if total == 0:
        raise RuntimeError("Source image is empty")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_existing_assets(conn) -> dict[tuple[str, str], dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM horizontal_art_assets")
        return {
            (str(row["entity_type"]), str(row["entity_id"])): row
            for row in cur.fetchall()
        }


def fetch_to(source_url: str, directory: Path, name: str) -> Path:
    local = local_source_path(source_url)
    if local is not None:
        return local
    downloaded = directory / name
    download_source(source_url, downloaded)
    return downloaded


def process_candidate(
    candidate: Candidate,
    existing: dict[str, Any] | None,
    *,
    force: bool,
    dry_run: bool,
    focus_overrides: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    public_path = output_public_path(candidate)
    target = local_upload_path(public_path)
    focus = (focus_overrides or {}).get(
        f"{candidate.entity_type}:{candidate.entity_id}"
    )
    signature = source_signature(candidate, focus)
    if (
        not force
        and existing is not None
        and str(existing.get("status") or "") == "ready"
        and str(existing.get("source_signature") or "") == signature
        and str(existing.get("recipe_version") or "") == RECIPE_VERSION
        and str(existing.get("local_image_url") or "") == public_path
        and all(
            path.is_file() and path.stat().st_size > 0
            for path in (target, retina_path(target))
        )
    ):
        return {"action": "unchanged", "candidate": candidate}

    if dry_run:
        return {
            "action": "would_generate",
            "candidate": candidate,
            "source_signature": signature,
            "public_path": public_path,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o755)
    failures = []
    not_found_count = 0
    sources = (
        (candidate.source_url, candidate.source_kind),
        *candidate.fallback_sources,
    )
    with tempfile.TemporaryDirectory(prefix="horizontal-source-") as tmp_dir:
        tile = None
        for tile_url in candidate.focus_tiles:
            try:
                tile = fetch_to(tile_url, Path(tmp_dir), "tile")
                break
            except Exception:  # noqa: BLE001, S112 -- the subject heuristic still applies
                continue
        for index, (source_url, source_kind) in enumerate(sources):
            try:
                source = fetch_to(source_url, Path(tmp_dir), f"source-{index}")
                report = render_horizontal_art(
                    source, target, source_kind, tile=tile, focus=focus
                )
                return {
                    "action": "generated",
                    "candidate": candidate,
                    "source_url": source_url,
                    "source_kind": source_kind,
                    "source_signature": signature,
                    "public_path": public_path,
                    "image_sha256": sha256_file(target),
                    "report": report,
                }
            except Exception as exc:  # noqa: BLE001 -- try the next trusted art source
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                    not_found_count += 1
                failures.append(f"{source_url}: {exc}")
    return {
        "action": "unavailable" if not_found_count == len(sources) else "error",
        "candidate": candidate,
        "error": " | ".join(failures),
    }


def persist_result(
    conn, result: dict[str, Any], existing: dict[str, Any] | None
) -> None:
    action = str(result["action"])
    candidate: Candidate = result["candidate"]
    if action == "generated":
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO horizontal_art_assets (
                    entity_type, entity_id, source_url, source_kind, source_signature,
                    local_image_url, image_sha256, recipe_version, status,
                    last_error, generated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ready', NULL, %s)
                ON DUPLICATE KEY UPDATE
                    source_url = VALUES(source_url),
                    source_kind = VALUES(source_kind),
                    source_signature = VALUES(source_signature),
                    local_image_url = VALUES(local_image_url),
                    image_sha256 = VALUES(image_sha256),
                    recipe_version = VALUES(recipe_version),
                    status = 'ready',
                    last_error = NULL,
                    generated_at = VALUES(generated_at)
                """,
                (
                    candidate.entity_type,
                    candidate.entity_id,
                    result["source_url"],
                    result["source_kind"],
                    result["source_signature"],
                    result["public_path"],
                    result["image_sha256"],
                    RECIPE_VERSION,
                    utc_now(),
                ),
            )
    elif action in {"error", "unavailable"}:
        error = str(result.get("error") or "Unknown horizontal art error")[:65535]
        if (
            action == "error"
            and existing
            and str(existing.get("status") or "") == "ready"
        ):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE horizontal_art_assets
                       SET last_error = %s
                     WHERE entity_type = %s AND entity_id = %s
                    """,
                    (error, candidate.entity_type, candidate.entity_id),
                )
        else:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO horizontal_art_assets (
                        entity_type, entity_id, source_url, source_kind, source_signature,
                        recipe_version, status, last_error
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        source_url = VALUES(source_url),
                        source_kind = VALUES(source_kind),
                        source_signature = VALUES(source_signature),
                        recipe_version = VALUES(recipe_version),
                        status = VALUES(status),
                        last_error = VALUES(last_error)
                    """,
                    (
                        candidate.entity_type,
                        candidate.entity_id,
                        candidate.source_url,
                        candidate.source_kind,
                        source_signature(candidate),
                        RECIPE_VERSION,
                        action,
                        error,
                    ),
                )


def disable_framed_card_assets(conn) -> int:
    """Hide legacy assets cut from rendered cards or crops of unknown framing."""
    placeholders = ", ".join("%s" for _ in SOURCE_KINDS)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE horizontal_art_assets "
            "SET status = 'unavailable', last_error = %s, recipe_version = %s "
            f"WHERE status = 'ready' AND source_kind NOT IN ({placeholders})",
            (
                "Pure artwork required; framed card/crop sources disabled",
                RECIPE_VERSION,
                *SOURCE_KINDS,
            ),
        )
        return int(cur.rowcount)


def is_current(
    candidate: Candidate,
    existing: dict[str, Any] | None,
    focus: tuple[float, float] | None,
) -> bool:
    return (
        existing is not None
        and str(existing.get("status") or "") == "ready"
        and str(existing.get("recipe_version") or "") == RECIPE_VERSION
        and str(existing.get("local_image_url") or "") == output_public_path(candidate)
        and str(existing.get("source_signature") or "")
        == source_signature(candidate, focus)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build horizontal artwork variants for database entities."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--entity", choices=ENTITY_TYPES, action="append")
    parser.add_argument("--id", action="append", dest="ids", help="entity ID")
    # Rendering is CPU-bound: keep the scheduled job small and bounded, so it
    # neither starves the server nor holds the global sync lock for hours.
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument(
        "--max-generate",
        type=int,
        default=2500,
        help="render at most this many outdated assets per run (0: no limit)",
    )
    args = parser.parse_args()

    conn = connect_db(load_php_config())
    try:
        ensure_schema(conn)
        focus_overrides = load_focus_overrides()
        candidates = collect_candidates(conn)
        complete = not (args.entity or args.ids or args.limit is not None)
        if args.entity:
            candidates = [
                item for item in candidates if item.entity_type in args.entity
            ]
        if args.ids:
            candidates = [item for item in candidates if item.entity_id in args.ids]
        if args.limit is not None:
            candidates = candidates[: max(0, args.limit)]
        existing_assets = load_existing_assets(conn)

        def key(candidate: Candidate) -> tuple[str, str]:
            return candidate.entity_type, candidate.entity_id

        def override(candidate: Candidate) -> tuple[float, float] | None:
            return focus_overrides.get(f"{candidate.entity_type}:{candidate.entity_id}")

        pending = [
            candidate
            for candidate in candidates
            if args.force
            or not is_current(
                candidate, existing_assets.get(key(candidate)), override(candidate)
            )
        ]
        deferred = 0
        if args.max_generate > 0 and len(pending) > args.max_generate:
            deferred = len(pending) - args.max_generate
            pending = pending[: args.max_generate]
            complete = False

        results: list[dict[str, Any]] = []
        unpersisted = 0
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, args.workers)
        ) as executor:
            futures = [
                executor.submit(
                    process_candidate,
                    candidate,
                    existing_assets.get(key(candidate)),
                    force=args.force,
                    dry_run=args.dry_run,
                    focus_overrides=focus_overrides,
                )
                for candidate in pending
            ]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                results.append(result)
                if args.dry_run:
                    continue
                # Each row switches to its new URL as soon as both files exist.
                persist_result(
                    conn, result, existing_assets.get(key(result["candidate"]))
                )
                unpersisted += 1
                if unpersisted >= 100:
                    conn.commit()
                    unpersisted = 0

        framed_assets_disabled = 0
        if not args.dry_run:
            if complete:
                framed_assets_disabled = disable_framed_card_assets(conn)
            conn.commit()

        counts: dict[str, int] = {
            "unchanged": len(candidates) - len(pending) - deferred
        }
        errors = []
        kept_previous = []
        by_type: dict[str, int] = {}
        focus_methods: dict[str, int] = {}
        quality_flags = []
        for result in results:
            action = str(result["action"])
            counts[action] = counts.get(action, 0) + 1
            candidate = result["candidate"]
            by_type[candidate.entity_type] = by_type.get(candidate.entity_type, 0) + 1
            report = result.get("report")
            if report:
                method = str(report["focus_method"])
                focus_methods[method] = focus_methods.get(method, 0) + 1
                if report["white_columns"] or report["black_columns"]:
                    quality_flags.append({"entity_id": candidate.entity_id, **report})
            if action == "error":
                error = {
                    "entity_type": candidate.entity_type,
                    "entity_id": candidate.entity_id,
                    "error": result.get("error"),
                }
                # A source outage that leaves the previous asset in the API is
                # reported, but must not fail the job and skip the tile step.
                existing = existing_assets.get(key(candidate)) or {}
                if str(existing.get("status") or "") == "ready":
                    kept_previous.append(error)
                else:
                    errors.append(error)
        summary = {
            "status": "ok" if not errors else "partial",
            "dry_run": args.dry_run,
            "recipe_version": RECIPE_VERSION,
            "candidates": len(candidates),
            "deferred": deferred,
            "framed_assets_disabled": framed_assets_disabled,
            "counts": counts,
            "by_type": dict(sorted(by_type.items())),
            "focus_methods": focus_methods,
            "quality_flags": quality_flags[:50],
            "errors": errors[:100],
            "kept_previous": kept_previous[:100],
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if not errors else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
