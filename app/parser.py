from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .cards_index import resolve_card_name
from .deck_decode import first_deck_code_from_text
from .hsreplay_extract import extract_for_source, parse_bg_trinkets_api_payload
from .sources import Source
from .structured import build_structured
from .trinket_slices import TRINKET_SLICE_BY_SOURCE_ID, TRINKET_SLICE_SOURCE_IDS

DECK_CODE_RE = re.compile(r"\bAAE[A-Za-z0-9+/=]{24,}\b")


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _deck_code_from_cell(cell: Any) -> str | None:
    candidates = [cell, *cell.find_all(True)]
    for node in candidates:
        for attribute in ("data-clipboard-text", "data-deck-code", "value"):
            value = node.get(attribute)
            if not isinstance(value, str):
                continue
            deck_code = first_deck_code_from_text(value)
            if deck_code:
                return deck_code
    return None


def _extract_tables(
    soup: BeautifulSoup,
    *,
    base_url: str = "",
) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for index, table in enumerate(soup.find_all("table")):
        rows: list[list[str]] = []
        row_links: list[list[str | None]] = []
        row_deck_codes: list[str | None] = []
        for tr in table.find_all("tr"):
            cell_nodes = tr.find_all(["th", "td"])
            cells = [_clean_text(cell.get_text(" ")) for cell in cell_nodes]
            if any(cells):
                rows.append(cells)
                row_links.append(
                    [
                        (
                            urljoin(base_url, str(anchor.get("href")))
                            if (anchor := cell.find("a", href=True)) is not None
                            else None
                        )
                        for cell in cell_nodes
                    ]
                )
                row_deck_codes.append(
                    next(
                        (
                            code
                            for cell in cell_nodes
                            if (code := _deck_code_from_cell(cell)) is not None
                        ),
                        None,
                    )
                )
        if not rows:
            continue
        headers = rows[0] if table.find("th") else []
        data_rows = rows[1:] if headers else rows
        data_links = row_links[1:] if headers else row_links
        data_deck_codes = row_deck_codes[1:] if headers else row_deck_codes
        objects = []
        if headers:
            for row_index, row in enumerate(data_rows):
                item = {}
                for pos, header in enumerate(headers):
                    field = header or f"column_{pos + 1}"
                    item[field] = row[pos] if pos < len(row) else None
                    link = (
                        data_links[row_index][pos]
                        if row_index < len(data_links)
                        and pos < len(data_links[row_index])
                        else None
                    )
                    if link:
                        item[f"{field}_url"] = link
                deck_code = (
                    data_deck_codes[row_index]
                    if row_index < len(data_deck_codes)
                    else None
                )
                if deck_code:
                    item["deck_code"] = deck_code
                objects.append(item)
        tables.append(
            {
                "index": index,
                "headers": headers,
                "rows": data_rows,
                "objects": objects,
            }
        )
    return tables


_HSGURU_CARD_RE = re.compile(r"^/deck/[A-Za-z0-9_-]+/?$")
_HSGURU_STREAMER_LINE_RE = re.compile(
    r"^#?\s*(?:streamer|streamed\s+by|creator|player)\s*:?\s*(.+?)\s*$",
    flags=re.IGNORECASE,
)
_HSGURU_STREAMER_LABELS = frozenset(
    {"streamer", "streamed by", "creator", "player"}
)


def _hsguru_deck_url(value: object, base_url: str) -> str | None:
    """Return only a same-site HSGuru deck detail URL."""
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = urljoin(base_url, raw)
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"hsguru.com", "www.hsguru.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not _HSGURU_CARD_RE.fullmatch(parsed.path)
    ):
        return None
    return parsed._replace(query="", fragment="").geturl()


def _hsguru_streamer_from_card(card: Any, text: str) -> str | None:
    for selector in (
        ".streamer",
        ".streamer-name",
        ".streamer_name",
        ".creator",
        ".player",
        "[data-streamer]",
        "[data-streamer-name]",
        "[data-creator]",
        "[data-player]",
    ):
        node = card.select_one(selector)
        if node:
            value = _clean_text(node.get_text(" "))
            if value:
                return value

    for node in card.find_all(["dt", "th", "label"]):
        label = _clean_text(node.get_text(" ")).rstrip(":").casefold()
        if label not in _HSGURU_STREAMER_LABELS:
            continue
        sibling = node.find_next_sibling()
        if sibling:
            value = _clean_text(sibling.get_text(" "))
            if value:
                return value

    for line in text.splitlines():
        match = _HSGURU_STREAMER_LINE_RE.match(_clean_text(line))
        if match and match.group(1):
            return match.group(1)
    return None


def _extract_hsguru_streamer_cards(
    soup: BeautifulSoup,
    *,
    base_url: str,
) -> list[dict[str, Any]]:
    """Parse HSGuru's card layout when the filtered page has no data table."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for card in soup.select('[id^="deck_stats-"]'):
        copy_button = card.select_one("button[data-clipboard-text]")
        clipboard_text = (
            str(copy_button.get("data-clipboard-text") or "")
            if copy_button
            else ""
        )
        card_text = card.get_text("\n", strip=True)
        deck_code = _deck_code_from_cell(card) or first_deck_code_from_text(
            f"{clipboard_text}\n{card_text}"
        )
        if not deck_code:
            continue

        deck_url = next(
            (
                detail_url
                for anchor in card.find_all("a", href=True)
                if (detail_url := _hsguru_deck_url(anchor.get("href"), base_url))
            ),
            None,
        )
        if not deck_url:
            deck_url = next(
                (
                    detail_url
                    for raw_url in re.findall(
                        r"https?://(?:www\.)?hsguru\.com/deck/[A-Za-z0-9_-]+/?",
                        clipboard_text,
                    )
                    if (detail_url := _hsguru_deck_url(raw_url, base_url))
                ),
                None,
            )

        title_match = re.search(
            r"^###\s+(.+?)\s*$", clipboard_text, flags=re.MULTILINE
        )
        title = _clean_text(title_match.group(1)) if title_match else ""
        if not title:
            heading = card.find(["h1", "h2", "h3", "h4", "h5", "h6"])
            title = _clean_text(heading.get_text(" ")) if heading else ""
        if not title:
            deck_anchor = next(
                (
                    anchor
                    for anchor in card.find_all("a", href=True)
                    if _hsguru_deck_url(anchor.get("href"), base_url)
                ),
                None,
            )
            title = _clean_text(deck_anchor.get_text(" ")) if deck_anchor else ""
        if not title or first_deck_code_from_text(title):
            continue

        streamer = _hsguru_streamer_from_card(
            card,
            f"{clipboard_text}\n{card_text}",
        )
        if not streamer:
            continue

        key = (deck_url or "", deck_code)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "Deck": title,
                "Streamer": streamer,
                "deck_code": deck_code,
                "Deck_url": deck_url or "",
            }
        )
    return rows


def _extract_json_scripts(soup: BeautifulSoup) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for script in soup.find_all("script"):
        script_type = (script.get("type") or "").lower()
        script_id = script.get("id") or ""
        text = script.string or script.get_text() or ""
        if not text.strip():
            continue
        if "json" not in script_type and script_id != "__NEXT_DATA__":
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        payloads.append({"id": script_id, "type": script_type, "value": value})
    return payloads


def _extract_links(soup: BeautifulSoup, limit: int = 5000) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        label = _clean_text(anchor.get_text(" "))
        href = anchor["href"]
        if label or href:
            links.append({"text": label, "href": href})
        if len(links) >= limit:
            break
    return links


def _extract_hsreplay_bootstrap(json_scripts: list[dict[str, Any]]) -> dict[str, Any] | None:
    for script in json_scripts:
        if script.get("id") != "userdata":
            continue
        value = script.get("value")
        if isinstance(value, dict):
            return value
    return None


def _tables_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in snapshot.get("tables") or []:
        rows = item.get("rows") or []
        if not rows:
            continue
        headers = rows[0] if rows else []
        data_rows = rows[1:] if len(rows) > 1 else []
        objects = []
        if headers:
            for row in data_rows:
                obj = {}
                for pos, header in enumerate(headers):
                    obj[header or f"column_{pos + 1}"] = row[pos] if pos < len(row) else None
                objects.append(obj)
        out.append({"index": item.get("index", 0), "headers": headers, "rows": data_rows, "objects": objects})
    return out


def _json_body_payload(
    html: str,
    soup: BeautifulSoup,
    snapshot: dict[str, Any] | None,
) -> Any:
    candidates = [html.strip()]
    pre = soup.find("pre")
    if pre:
        candidates.append(pre.get_text().strip())
    if snapshot and snapshot.get("lines"):
        snapshot_text = "\n".join(str(line) for line in snapshot["lines"]).strip()
        snapshot_text = re.sub(r"^```(?:json)?\s*", "", snapshot_text, flags=re.IGNORECASE)
        snapshot_text = re.sub(r"\s*```$", "", snapshot_text)
        candidates.append(snapshot_text)
    for candidate in candidates:
        if not candidate or candidate[:1] not in "[{":
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def parse_html(source: Source, html: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("title")
    title = _clean_text(title_tag.get_text(" ")) if title_tag else ""
    text_lines = [
        _clean_text(line)
        for line in soup.get_text("\n").splitlines()
        if _clean_text(line)
    ]
    if snapshot and snapshot.get("lines"):
        snap_lines = [_clean_text(line) for line in snapshot["lines"] if _clean_text(line)]
        if len(snap_lines) > len(text_lines):
            text_lines = snap_lines
    deck_codes = sorted(set(DECK_CODE_RE.findall(html)))
    json_scripts = _extract_json_scripts(soup)
    tables = _extract_tables(soup, base_url=source.fetch_url)
    if snapshot and snapshot.get("tables"):
        snap_tables = _tables_from_snapshot(snapshot)
        if sum(len(t.get("rows") or []) for t in snap_tables) > sum(
            len(t.get("rows") or []) for t in tables
        ):
            tables = snap_tables
    if source.id == "hsguru_streamer_decks_legend_1000":
        streamer_rows = _extract_hsguru_streamer_cards(
            soup,
            base_url=source.fetch_url,
        )
        if streamer_rows:
            streamer_headers = ["Deck", "Streamer", "deck_code", "Deck_url"]
            tables.append(
                {
                    "index": len(tables),
                    "headers": streamer_headers,
                    "rows": [
                        [row.get(header, "") for header in streamer_headers]
                        for row in streamer_rows
                    ],
                    "objects": streamer_rows,
                }
            )
    if snapshot and snapshot.get("card_rows"):
        objects = []
        stat_headers = [
            "Card",
            "Deck Winrate",
            "Avg Copies",
            "Times Played",
            "Mulligan Winrate",
            "Keep Percentage",
        ]
        for row in snapshot["card_rows"]:
            if len(row) < 2:
                continue
            name = None
            name_idx = 0
            for i, cell in enumerate(row):
                if (
                    len(cell) > 2
                    and not _clean_text(cell).isdigit()
                    and "%" not in cell
                    and (resolve_card_name(cell).get("id") or len(cell) > 4)
                ):
                    name = cell
                    name_idx = i
                    break
            if not name:
                continue
            obj = {"Card": name}
            stats = [c for j, c in enumerate(row) if j != name_idx and c]
            for j, val in enumerate(stats[:5]):
                obj[stat_headers[j + 1] if j + 1 < len(stat_headers) else f"stat_{j}"] = val
            objects.append(obj)
        if objects:
            tables.append(
                {
                    "index": 99,
                    "headers": stat_headers,
                    "rows": [list(o.values()) for o in objects],
                    "objects": objects,
                }
            )
    links = _extract_links(soup)
    hsreplay_bootstrap = _extract_hsreplay_bootstrap(json_scripts) if source.site == "hsreplay" else None
    hsreplay_extracted: dict[str, Any] = {}
    if source.site == "hsreplay":
        if source.id in {
            "hsreplay_battlegrounds_trinkets_lesser",
            "hsreplay_battlegrounds_trinkets_greater",
        } | TRINKET_SLICE_SOURCE_IDS:
            body_payload = _json_body_payload(html, soup, snapshot)
            if source.id in TRINKET_SLICE_SOURCE_IDS:
                api_rows = [
                    *parse_bg_trinkets_api_payload(body_payload, trinket_type="Lesser"),
                    *parse_bg_trinkets_api_payload(body_payload, trinket_type="Greater"),
                ]
                mmr_percentile, time_range = TRINKET_SLICE_BY_SOURCE_ID[source.id]
            else:
                trinket_type = "Lesser" if source.id.endswith("_lesser") else "Greater"
                api_rows = parse_bg_trinkets_api_payload(
                    body_payload,
                    trinket_type=trinket_type,
                )
                mmr_percentile, time_range = "TOP_1_PERCENT", "LAST_7_DAYS"
            if api_rows:
                hsreplay_extracted = {
                    "type": "bg_trinkets",
                    "trinkets": api_rows,
                    "active_trinkets": len(api_rows),
                    "parser_level": "primary",
                    "source": {
                        "backend": "hsreplay_json_api",
                        "mmr_percentile": mmr_percentile,
                        "time_range": time_range,
                    },
                }
        if not hsreplay_extracted:
            hsreplay_extracted = extract_for_source(source.id, soup, html, snapshot)
    structured = build_structured(
        source,
        {
            "text_preview": text_lines,
            "tables": tables,
            "links": links,
            "hsreplay_extracted": hsreplay_extracted,
        },
    )
    return {
        "source_id": source.id,
        "site": source.site,
        "category": source.category,
        "url": source.url,
        "fetch_url": source.fetch_url,
        "fragment": source.fragment,
        "title": title,
        "tables": tables,
        "json_scripts": json_scripts,
        "hsreplay_bootstrap": hsreplay_bootstrap,
        "structured": structured,
        "hsreplay_extracted": hsreplay_extracted,
        "deck_codes": deck_codes,
        "links": links,
        "text_preview": text_lines[:300],
        "counts": {
            "tables": len(soup.find_all("table")),
            "json_scripts": len(_extract_json_scripts(soup)),
            "deck_codes": len(deck_codes),
            "links": len(soup.find_all("a")),
            "text_lines": len(text_lines),
        },
    }
