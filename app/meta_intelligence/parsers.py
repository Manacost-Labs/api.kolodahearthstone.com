"""Research parser adapters; production collectors retain their own contracts."""
from __future__ import annotations
import re
from typing import Any
from urllib.parse import quote, urlencode
from bs4 import BeautifulSoup
from ..hsguru_decks import parse_hsguru_decks_html
DECK_CODE_RE = re.compile(r"(?<![A-Za-z0-9+/=])AAE[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])")
NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
GAMES_RE = re.compile(r"\(([\d\s,.]+)\)")

def _float(value: Any) -> float | None:
    match = NUMBER_RE.search(str(value or "").replace(",", "."))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _games(value: Any) -> int | None:
    text = str(value or "")
    match = GAMES_RE.search(text)
    if match:
        text = match.group(1)
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z]+", "", value.lower().replace("↓", "").replace("↑", ""))


def _class_from_archetype(name: str) -> str | None:
    normalized = name.strip().lower()
    suffixes = (
        ("death knight", "DeathKnight"),
        ("demon hunter", "DemonHunter"),
        ("warrior", "Warrior"),
        ("warlock", "Warlock"),
        ("shaman", "Shaman"),
        ("rogue", "Rogue"),
        ("priest", "Priest"),
        ("paladin", "Paladin"),
        ("mage", "Mage"),
        ("hunter", "Hunter"),
        ("druid", "Druid"),
        (" dk", "DeathKnight"),
        (" dh", "DemonHunter"),
        ("lock", "Warlock"),
    )
    for suffix, class_name in suffixes:
        if normalized.endswith(suffix):
            return class_name
    return None


def parse_meta_html(
    html: str,
    *,
    format_name: str,
    format_id: int,
    period: str,
    rank: str = "all",
    minimum_games: int = 50,
    source_url: str,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    selected = None
    for table in soup.find_all("table"):
        headers = [
            cell.get_text(" ", strip=True)
            for cell in (table.find("tr").find_all(["th", "td"]) if table.find("tr") else [])
        ]
        keys = {_header_key(header) for header in headers}
        if "archetype" in keys and ("winrate" in keys or "popularity" in keys):
            selected = table
            break
    if selected is None:
        return []

    first = selected.find("tr")
    headers = [cell.get_text(" ", strip=True) for cell in first.find_all(["th", "td"])]
    keys = [_header_key(header) for header in headers]
    rows: list[dict[str, Any]] = []
    for tr in selected.find_all("tr")[1:]:
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
        if not cells:
            continue
        raw = {keys[index]: value for index, value in enumerate(cells[: len(keys)])}
        name = str(raw.get("archetype") or "").strip()
        games = _games(raw.get("popularity") or raw.get("games"))
        if not name or games is None or games < minimum_games:
            continue
        rows.append(
            {
                "format": format_name,
                "format_id": format_id,
                "archetype": name,
                "class": _class_from_archetype(name),
                "games": games,
                "winrate": _float(raw.get("winrate")),
                "popularity_pct": _float(raw.get("popularity")),
                "avg_turns": _float(raw.get("turns")),
                "avg_duration_minutes": _float(raw.get("duration")),
                "climbing_speed_stars_per_hour": _float(
                    raw.get("climbingspeed") or raw.get("climbingspeedh")
                ),
                "period": period,
                "rank": rank,
                "source_url": source_url,
                "archetype_url": f"https://www.hsguru.com/archetype/{quote(name)}",
                "decks_url": (
                    "https://www.hsguru.com/decks?"
                    + urlencode(
                        [
                            ("format", str(format_id)),
                            ("min_games", str(minimum_games)),
                            ("period", period),
                            ("rank", rank),
                            ("player_deck_archetype[]", name),
                        ]
                    )
                ),
                "decks": [],
            }
        )
    return rows


def parse_decks_html(
    html: str,
    *,
    archetype: str,
    format_name: str,
    fetched_at: str,
    limit: int | None = 20,
) -> list[dict[str, Any]]:
    if 'deck_stats-' in html:
        rows = parse_hsguru_decks_html(html, archetype=archetype, format_name=format_name, fetched_at=fetched_at, trust_exact_filter=True)
        return rows[:limit] if limit is not None else rows
    soup = BeautifulSoup(html, "lxml")
    decks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for link in soup.find_all(
        "a",
        href=re.compile(r"(?:https?://www\.hsguru\.com)?/deck/\d+"),
    ):
        container = link.find_parent("tr")
        if container is None:
            container = link
            for parent in link.parents:
                if parent.name in {"body", "html"}:
                    break
                container = parent
                if DECK_CODE_RE.search(str(parent)):
                    break
        tr = container
        if link is None:
            continue
        href = str(link.get("href") or "")
        full_text = tr.get_text(" ", strip=True)
        codes = DECK_CODE_RE.findall(str(tr))
        deck_code = codes[0] if codes else None
        identity = deck_code or href
        if not identity or identity in seen:
            continue
        seen.add(identity)
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
        winrate = next((_float(cell) for cell in cells if "%" in cell), None)
        games = None
        for cell in cells:
            if "game" in cell.lower() or re.fullmatch(r"[\d\s,.]+", cell):
                candidate = _games(cell)
                if candidate:
                    games = candidate
        if winrate is None:
            percent_match = re.search(r"(-?\d+(?:[.,]\d+)?)\s*%", full_text)
            winrate = _float(percent_match.group(1)) if percent_match else None
        if games is None:
            games_match = re.search(r"([\d\s,.]+)\s+games?\b", full_text, re.IGNORECASE)
            games = _games(games_match.group(1)) if games_match else None
        decks.append(
            {
                "source_id": "hsguru_decks",
                "title": archetype,
                "archetype": archetype,
                "class": _class_from_archetype(archetype),
                "format": format_name.title(),
                "deck_code": deck_code,
                "win_rate": winrate,
                "games": games,
                "score": f"{games} games" if games is not None else None,
                "url": href if href.startswith("http") else f"https://www.hsguru.com{href}",
                "updated_at": fetched_at,
                "raw_label": full_text[:300],
            }
        )
    decks.sort(key=lambda row: (row.get("games") or 0, row.get("win_rate") or 0), reverse=True)
    return decks[:limit] if limit is not None else decks
