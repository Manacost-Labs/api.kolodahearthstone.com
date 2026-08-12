#!/usr/bin/env python3
"""Validate that Battlegrounds card renders and portraits are local derivatives."""

from __future__ import annotations

import argparse
import json
import struct
import urllib.parse
import urllib.request
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
API_URL = "https://api.kolodahearthstone.com/api/v1/cards"
API_HOST = "api.kolodahearthstone.com"
USER_AGENT = "KolodahsFramedAudit/1.0"

# Internal Blizzard fixtures/skin tokens published in card metadata without any
# render in either HearthstoneJSON's card or original-art repositories. They are
# not user-facing Battlegrounds cards, so report them separately instead of
# weakening the framed-image contract for real cards.
NON_RENDERABLE_TECHNICAL_IDS = {
    "TB_BaconShop_HP_022t_SKIN_C",
    "TB_BaconShop_HP_022t_SKIN_C_G",
    "BG23_HERO_201pt_SKIN_A_G",
    "BGMinionAbilityTestCost",
    "BGMinionAbilityTest",
}


def load_cards() -> list[dict]:
    cards: list[dict] = []
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {"page": page, "per_page": 200, "include_variants": "1", "framed_audit": "1"}
        )
        request = urllib.request.Request(f"{API_URL}?{query}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        cards.extend(payload.get("data") or [])
        pagination = payload.get("pagination") or {}
        if not pagination.get("has_next"):
            return cards
        page += 1


def png_geometry(path: Path) -> tuple[int, int] | None:
    try:
        header = path.read_bytes()[:24]
    except OSError:
        return None
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", header[16:24])


def validate_png_asset(
    card_id: str,
    asset_name: str,
    asset_url: str,
    directory: str,
    expected_geometry: tuple[int, int],
) -> str | None:
    parsed = urllib.parse.urlparse(asset_url)
    expected_path = f"/uploads/{directory}/{urllib.parse.quote(card_id)}.png"
    if parsed.hostname != API_HOST or parsed.path != expected_path:
        return f"{card_id}: non-local {asset_name} image {asset_url or '<missing>'}"
    local = APP_ROOT / parsed.path.lstrip("/")
    geometry = png_geometry(local)
    if geometry != expected_geometry:
        return f"{card_id}: {asset_name} geometry {geometry} at {local}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("active", "all"), default="active")
    args = parser.parse_args()

    cards = load_cards()
    if args.scope == "active":
        cards = [card for card in cards if card.get("in_pool")]

    failures: list[str] = []
    skipped_technical: list[str] = []
    for card in cards:
        card_id = str(card.get("card_id") or "")
        if card_id in NON_RENDERABLE_TECHNICAL_IDS:
            skipped_technical.append(card_id)
            continue
        images = card.get("images") or {}
        for asset_name, directory, geometry in (
            ("card", "cards", (256, 388)),
            ("framed", "framed", (300, 350)),
        ):
            failure = validate_png_asset(
                card_id,
                asset_name,
                str(images.get(asset_name) or ""),
                directory,
                geometry,
            )
            if failure:
                failures.append(failure)

    print(
        json.dumps(
            {
                "scope": args.scope,
                "checked": len(cards) - len(skipped_technical),
                "skipped_technical": len(skipped_technical),
                "failures": len(failures),
            },
            ensure_ascii=False,
        )
    )
    for failure in failures[:50]:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
