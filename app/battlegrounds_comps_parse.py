from __future__ import annotations

import asyncio
from html import unescape as html_unescape
import json
import re
from pathlib import Path
from typing import Any

from .cards_index import card_from_id, card_label, cards_by_dbfid, cards_by_id
from .firecrawl_backend import scrape_source
from .hsreplay_client import fetch_hsreplay_html, fetch_hsreplay_markdown, jina_url
from .parsing_normalize import strip_markdown_links
from .sources import Source

# --- simple on-disk cache for comp *detail* pages (to dampen 403/451/504 noise) ---
import time

from .config import bg_comp_detail_cache_ttl_hours, data_dir


def _bg_comp_detail_cache_dir() -> Path:
    d = data_dir() / "cache" / "bg_comp_details"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bg_comp_detail_cache_path(url: str) -> Path | None:
    # urls like https://hsreplay.net/battlegrounds/comps/73/dragons-spells/
    m = re.search(r"/comps/(\d+)/", url)
    if not m:
        return None
    comp_id = m.group(1)
    return _bg_comp_detail_cache_dir() / f"{comp_id}.md"


def _looks_like_bg_comp_markdown(text: str) -> bool:
    """Reject HTML shells that used to poison the on-disk detail cache."""
    sample = (text or "")[:800].lstrip().lower()
    if not sample or sample.startswith("<!doctype") or sample.startswith("<html"):
        return False
    if "<head>" in sample and "<script" in sample:
        return False
    return len(text) > 200


def _read_bg_comp_detail_cache(url: str) -> str | None:
    p = _bg_comp_detail_cache_path(url)
    if not p or not p.exists():
        return None
    ttl = bg_comp_detail_cache_ttl_hours() * 3600
    if time.time() - p.stat().st_mtime > ttl:
        return None
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
        return txt if _looks_like_bg_comp_markdown(txt) else None
    except Exception:
        return None


def _write_bg_comp_detail_cache(url: str, markdown: str) -> None:
    p = _bg_comp_detail_cache_path(url)
    if not p or not _looks_like_bg_comp_markdown(markdown):
        return
    try:
        p.write_text(markdown, encoding="utf-8")
    except Exception:
        pass  # best effort cache

HSREPLAY_COMPS_URL = "https://hsreplay.net/battlegrounds/comps/"
HSREPLAY_ORIGIN = "https://hsreplay.net"


def _abs_hsreplay_url(url: str) -> str:
    if not url:
        return url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"{HSREPLAY_ORIGIN}{url}"
    return f"{HSREPLAY_ORIGIN}/{url.lstrip('/')}"

COMP_MD_HEADER_RE = re.compile(
    r"^\[(.*)\]\(https://hsreplay\.net/battlegrounds/comps/(\d+)/([^)]+)\)\s*$",
    re.MULTILINE,
)
COMP_URL_RE = re.compile(
    r"https://hsreplay\.net/battlegrounds/comps/(\d+)/([a-zA-Z0-9_-]+)/?",
)
HSJSON_IMG_RE = re.compile(
    r"!\[([^\]]*)\]\(https://art\.hearthstonejson\.com/v1/(?:bgs/latest/[A-Za-z_]+/)?256x/([A-Za-z0-9_]+)\.(?:webp|png)\)",
    re.IGNORECASE,
)
MINION_CARD_RE = re.compile(
    r"!\[([^\]]*)\]\([^)]*hearthstonejson\.com[^)]*/(?:bgs/latest/[A-Za-z_]+/)?256x/([A-Za-z0-9_]+)\.(?:webp|png)\)\]"
    r"\(https://hsreplay\.net/battlegrounds/minions/(\d+)/([^)]+)\)",
    re.IGNORECASE,
)
MINION_URL_RE = re.compile(
    r"https://hsreplay\.net/battlegrounds/minions/(\d+)/([a-zA-Z0-9_-]+)/?",
    re.IGNORECASE,
)
HSJSON_ID_RE = re.compile(
    r"hearthstonejson\.com/v1/(?:bgs/latest/[A-Za-z_]+/)?256x/([A-Za-z0-9_]+)\.(?:webp|png)",
    re.IGNORECASE,
)


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").strip().title()


def _clean_md_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\\", " ")).strip()


def _strip_comp_season(title: str) -> str:
    return re.sub(r"\s+Comp\s+Season\s+\d+\s*$", "", title, flags=re.I).strip()


def _split_comp_title(title: str) -> tuple[str, str]:
    clean = _strip_comp_season(title)
    if " - " in clean:
        family, strategy = [part.strip() for part in clean.split(" - ", 1)]
        return family, clean if strategy else family
    return clean, clean


def _group_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card in cards:
        key = str(card.get("id") or card.get("card_id") or card.get("dbfId") or "")
        if not key:
            continue
        if key in grouped:
            grouped[key]["count"] = int(grouped[key].get("count") or 1) + 1
        else:
            grouped[key] = dict(card)
            grouped[key]["count"] = 1
    return list(grouped.values())


def _card_from_hsjson(name: str, card_id: str, dbf: int | None = None) -> dict[str, Any]:
    meta = cards_by_id().get(card_id) or card_label(cards_by_dbfid().get(int(dbf)) if dbf else None)
    if meta.get("name") in (None, "Unknown") and name:
        meta["name"] = name.strip()
    return {
        "count": 1,
        "card_id": card_id,
        "dbfId": meta.get("dbfId") or dbf,
        "id": meta.get("id") or card_id,
        "name": meta.get("name") or name,
        "image_url": f"https://art.hearthstonejson.com/v1/256x/{card_id}.png",
        **{k: meta[k] for k in ("cost", "type", "rarity") if meta.get(k)},
    }


def parse_hearthstonejson_images(text: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for match in HSJSON_IMG_RE.finditer(text):
        cards.append(_card_from_hsjson(match.group(1), match.group(2)))
    return cards


def parse_hsreplay_minion_cards(section: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()

    for match in MINION_CARD_RE.finditer(section):
        key = match.group(2)
        if key in seen:
            continue
        seen.add(key)
        cards.append(
            {
                **_card_from_hsjson(match.group(1), match.group(2), int(match.group(3))),
                "url": f"https://hsreplay.net/battlegrounds/minions/{match.group(3)}/{match.group(4).strip('/')}/",
            }
        )

    for dbf, slug in MINION_URL_RE.findall(section):
        key = f"dbf:{dbf}"
        if key in seen:
            continue
        seen.add(key)
        cards.append(
            {
                **_card_from_hsjson(_title_from_slug(slug), "", int(dbf)),
                "url": f"https://hsreplay.net/battlegrounds/minions/{dbf}/{slug}/",
            }
        )

    for card_id in HSJSON_ID_RE.findall(section):
        if card_id in seen:
            continue
        seen.add(card_id)
        cards.append(_card_from_hsjson("", card_id))
    return cards


def _section_after_heading(markdown: str, heading_re: str) -> str:
    match = re.search(heading_re, markdown, flags=re.I | re.M)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^#{2,3}\s+", markdown[start:], flags=re.M)
    end = start + next_heading.start() if next_heading else len(markdown)
    return markdown[start:end].strip()


def _cards_from_section(section: str) -> list[dict[str, Any]]:
    return _group_cards(parse_hsreplay_minion_cards(section) + parse_hearthstonejson_images(section))


def _summary_text_from_section(section: str) -> str:
    lines = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line or line.startswith("[![") or line.startswith("!["):
            continue
        if "hearthstonejson.com" in line or "hsreplay.net/battlegrounds/minions/" in line:
            continue
        line = strip_markdown_links(line)
        line = _clean_md_text(line)
        if line:
            lines.append(line)
    return " ".join(lines)[:1200]


def parse_hsreplay_comp_detail_markdown(markdown: str, *, url: str = "") -> dict[str, Any]:
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    title_line = next((line for line in lines if line.startswith("# ")), "")
    title = _strip_comp_season(title_line[2:].strip()) if title_line else ""
    name, strategy_title = _split_comp_title(title or _title_from_slug(url.rstrip("/").split("/")[-1]))
    difficulty = next((line for line in lines[:12] if line in {"Easy", "Medium", "Hard"}), None)
    tier = next((line.upper() for line in lines[:12] if line.lower() in {"s", "a", "b", "c", "d", "e", "f"}), None)

    core_section = _section_after_heading(markdown, r"^##\s+Core Cards for .+$")
    addon_section = _section_after_heading(markdown, r"^##\s+Addon Cards for .+$")
    how_section = _section_after_heading(markdown, r"^##\s+How to Play .+$")
    commit_section = _section_after_heading(markdown, r"^##\s+When to Commit to .+$")
    enabler_section = _section_after_heading(markdown, r"^##\s+Common Enablers for .+$")

    main_cards = _cards_from_section(core_section)
    additional_cards = _dedupe_additional(main_cards, _cards_from_section(addon_section))
    how_cards = _dedupe_additional([], _cards_from_section(how_section))
    when_to_commit_cards = _dedupe_additional([], _cards_from_section(commit_section))
    enabler_cards = _dedupe_additional([], _cards_from_section(enabler_section))

    return {
        "name": name,
        "title": strategy_title,
        "strategy_title": strategy_title,
        "tier": tier,
        "difficulty": difficulty,
        "main_cards": main_cards,
        "core_cards": main_cards,
        "additional_cards": additional_cards,
        "addon_cards": additional_cards,
        "how_to_play": _summary_text_from_section(how_section),
        "how_to_play_cards": how_cards,
        "when_to_commit": _summary_text_from_section(commit_section),
        "when_to_commit_cards": when_to_commit_cards,
        "enabler_cards": enabler_cards,
        "minions": [c.get("name") for c in main_cards + additional_cards if c.get("name")],
    }


def _find_comp_headers(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    headers: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for index, line in enumerate(lines):
        md_match = COMP_MD_HEADER_RE.match(line.strip())
        if md_match:
            key = (md_match.group(2), md_match.group(3).strip("/"))
            if key not in seen:
                seen.add(key)
                headers.append(
                    {
                        "line": index,
                        "caption": md_match.group(1),
                        "remote_id": key[0],
                        "slug": key[1],
                        "url": f"https://hsreplay.net/battlegrounds/comps/{key[0]}/{key[1]}/",
                    }
                )

    for match in COMP_URL_RE.finditer(markdown):
        key = (match.group(1), match.group(2).strip("/"))
        if key in seen:
            continue
        seen.add(key)
        line_no = markdown[: match.start()].count("\n")
        caption = lines[line_no][:200] if line_no < len(lines) else ""
        headers.append(
            {
                "line": line_no,
                "caption": caption,
                "remote_id": key[0],
                "slug": key[1],
                "url": f"https://hsreplay.net/battlegrounds/comps/{key[0]}/{key[1]}/",
            }
        )

    headers.sort(key=lambda item: item["line"])
    return headers


def _split_detail_sections(markdown: str) -> tuple[str, str]:
    """Core trios (HSJSON images) usually appear before minion detail links."""
    marker = markdown.lower().find("battlegrounds/minions/")
    if marker > 100:
        return markdown[:marker], markdown[marker:]
    return markdown, ""


def _dedupe_additional(
    main_cards: list[dict[str, Any]], additional_cards: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    main_keys = {
        str(c.get("id") or c.get("card_id") or c.get("dbfId") or "")
        for c in main_cards
        if c.get("id") or c.get("card_id") or c.get("dbfId")
    }
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in additional_cards:
        key = str(card.get("id") or card.get("card_id") or card.get("dbfId") or "")
        if not key or key in main_keys or key in seen:
            continue
        seen.add(key)
        out.append(card)
    return out


_REACT_CONTEXT_TIER = {1: "S", 2: "A", 3: "B", 4: "C", 5: "D"}
_REACT_CONTEXT_DIFFICULTY = {1: "Easy", 2: "Medium", 3: "Hard"}


def _comps_from_react_context(soup: Any) -> list[dict[str, Any]]:
    """Parse the current HSReplay comps embedded JSON.

    HSReplay stopped rendering the live composition list as ordinary links in
    August 2026. The authoritative list is now embedded in `#react_context`;
    hidden historical guides remain in the payload and must not be published.
    """
    script = soup.find("script", id="react_context")
    if script is None:
        return []
    raw_text = script.string or script.get_text() or ""
    try:
        payload = json.loads(html_unescape(raw_text))
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []

    card_index = cards_by_dbfid()
    comps: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("comp_hidden") is True:
            continue
        try:
            comp_id = int(item.get("comp_id") or 0)
        except (TypeError, ValueError):
            continue
        slug = str(item.get("comp_slug") or "").strip()
        title = str(item.get("comp_name") or "").strip()
        if comp_id <= 0 or not slug or not title:
            continue

        main_cards: list[dict[str, Any]] = []
        for raw_dbf_id in item.get("comp_core_cards") or []:
            try:
                dbf_id = int(raw_dbf_id)
            except (TypeError, ValueError):
                continue
            metadata = card_label(card_index.get(dbf_id))
            card_id = str(metadata.get("id") or "").strip()
            if not card_id:
                continue
            main_cards.append(
                _card_from_hsjson(str(metadata.get("name") or ""), card_id, dbf_id)
            )

        name, strategy_title = _split_comp_title(title)
        try:
            tier_number = int(item.get("comp_tier") or 0)
        except (TypeError, ValueError):
            tier_number = 0
        try:
            difficulty_number = int(item.get("comp_difficulty") or 0)
        except (TypeError, ValueError):
            difficulty_number = 0
        url = f"{HSREPLAY_ORIGIN}/battlegrounds/comps/{comp_id}/{slug}/"
        comps.append(
            {
                "id": f"hsreplay-{comp_id}",
                "comp_id": comp_id,
                "source": "hsreplay",
                "source_id": str(comp_id),
                "slug": slug,
                "name": name,
                "title": strategy_title,
                "strategy_title": strategy_title,
                "tier": _REACT_CONTEXT_TIER.get(tier_number),
                "difficulty": _REACT_CONTEXT_DIFFICULTY.get(difficulty_number),
                "description": str(item.get("comp_summary") or "").strip(),
                "main_cards": main_cards,
                "core_cards": main_cards,
                "additional_cards": [],
                "addon_cards": [],
                "minions": [card.get("name") for card in main_cards if card.get("name")],
                "url": url,
                "last_updated": item.get("comp_last_updated"),
                "_fetch_detail": False,
                "_detail_url": url,
            }
        )
    return comps


def _comps_from_html(html: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup

    from .hsreplay_extract import extract_bg_comps, extract_bg_comps_from_links

    soup = BeautifulSoup(html, "html.parser")
    embedded = _comps_from_react_context(soup)
    if len(embedded) >= 3:
        return embedded

    raw = extract_bg_comps(soup)
    if len(raw) < 3:
        links = [
            {"text": a.get_text(), "href": a.get("href", "")}
            for a in soup.find_all("a", href=True)
        ]
        raw = extract_bg_comps_from_links(links)
    comps: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        comp_id = int(item.get("comp_id") or 0)
        slug = str(item.get("slug") or "")
        comps.append(
            {
                "id": f"hsreplay-{comp_id}",
                "comp_id": comp_id,
                "source": "hsreplay",
                "source_id": str(comp_id),
                "slug": slug,
                "name": item.get("name") or _title_from_slug(slug),
                "title": item.get("name") or _title_from_slug(slug),
                "description": item.get("description") or "",
                "main_cards": [],
                "additional_cards": [],
                "minions": item.get("minions") or [],
                "url": _abs_hsreplay_url(
                    item.get("url")
                    or f"/battlegrounds/comps/{comp_id}/{slug}/"
                ),
                "_fetch_detail": index < 24,
                "_detail_url": _abs_hsreplay_url(
                    item.get("url")
                    or f"/battlegrounds/comps/{comp_id}/{slug}/"
                ),
            }
        )
    return comps


async def parse_hsreplay_comp_detail(url: str, *, source_id: str) -> dict[str, Any]:
    # Try cache first (reduces load and transient 4xx on detail pages)
    cached = _read_bg_comp_detail_cache(url)
    if cached:
        try:
            all_images = parse_hearthstonejson_images(cached)
            all_minions = parse_hsreplay_minion_cards(cached)
            main_cards: list[dict[str, Any]] = []
            seen_main: set[str] = set()
            for card in all_images:
                cid = str(card.get("card_id") or card.get("id") or "")
                if not cid or cid in seen_main:
                    continue
                seen_main.add(cid)
                main_cards.append(card)
                if len(main_cards) >= 6:
                    break
            additional_cards = _dedupe_additional(main_cards, all_minions)
            return {"main_cards": main_cards, "additional_cards": additional_cards, "_from_cache": True}
        except Exception:
            pass  # fall through to live fetch

    last_error: Exception | None = None
    markdown_used = ""
    for attempt in range(3):
        try:
            markdown, _backend = await fetch_hsreplay_markdown(url, source_id=source_id)
            markdown_used = markdown
            all_images = parse_hearthstonejson_images(markdown)
            all_minions = parse_hsreplay_minion_cards(markdown)
            main_cards: list[dict[str, Any]] = []
            seen_main: set[str] = set()
            for card in all_images:
                cid = str(card.get("card_id") or card.get("id") or "")
                if not cid or cid in seen_main:
                    continue
                seen_main.add(cid)
                main_cards.append(card)
                if len(main_cards) >= 6:
                    break
            additional_cards = _dedupe_additional(main_cards, all_minions)
            # populate cache on success
            _write_bg_comp_detail_cache(url, markdown)
            return {"main_cards": main_cards, "additional_cards": additional_cards}
        except Exception as exc:
            last_error = exc
    if last_error:
        import logging

        logging.getLogger(__name__).warning("Comp detail fetch failed for %s: %s", url, last_error)
    # even on final failure, if we had a markdown from a previous attempt in the loop (unlikely), cache it? no.
    return {}


_TIER_LETTER_RE = re.compile(r"^[sabcdef]$", re.I)


def _tier_letter(value: str | None) -> str | None:
    text = str(value or "").strip()
    if _TIER_LETTER_RE.fullmatch(text):
        return text.upper()
    return None


def parse_hsreplay_markdown(markdown: str, *, detail_limit: int = 12) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    headers = _find_comp_headers(markdown)
    comps: list[dict[str, Any]] = []
    # Carry-forward: Firecrawl dumps put many image lines between comps, so a
    # fixed lookback window misses the S/A/B markers and leaves half the list tierless.
    current_tier: str | None = None
    cursor = 0

    for i, header in enumerate(headers):
        next_line = headers[i + 1]["line"] if i + 1 < len(headers) else len(lines)
        for line in lines[cursor : header["line"]]:
            found = _tier_letter(line)
            if found:
                current_tier = found
        cursor = header["line"]
        section = "\n".join(lines[header["line"] + 1 : next_line])

        caption = header.get("caption", "")
        main_cards = _group_cards(parse_hearthstonejson_images(caption))
        additional_cards = _group_cards(
            parse_hsreplay_minion_cards(section) + parse_hearthstonejson_images(section)
        )
        caption_text = _clean_md_text(re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", caption))
        difficulty = next((d for d in ("Easy", "Medium", "Hard") if re.search(rf"\b{d}\b", caption_text)), None)
        tier = current_tier
        title = _title_from_slug(header["slug"])
        if " - " not in title:
            title = title.replace(" ", " - ", 1)
        name, strategy_title = _split_comp_title(title)

        comps.append(
            {
                "id": f"hsreplay-{header['remote_id']}",
                "comp_id": int(header["remote_id"]),
                "source": "hsreplay",
                "source_id": header["remote_id"],
                "slug": header["slug"],
                "name": name,
                "title": strategy_title,
                "strategy_title": strategy_title,
                "tier": tier,
                "difficulty": difficulty,
                "description": "",
                "main_cards": main_cards,
                "core_cards": main_cards,
                "additional_cards": additional_cards,
                "addon_cards": additional_cards,
                "when_to_commit_cards": [],
                "enabler_cards": [],
                "minions": [c.get("name") for c in additional_cards if c.get("name")],
                "url": _abs_hsreplay_url(header["url"]),
                "_fetch_detail": i < detail_limit,
                "_detail_url": _abs_hsreplay_url(header["url"]),
            }
        )

    return comps


async def _enrich_comp_cards(
    comp: dict[str, Any],
    *,
    source_id: str,
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    if comp.get("main_cards") or comp.get("additional_cards"):
        return comp
    url = _abs_hsreplay_url(comp.get("url") or comp.get("_detail_url") or "")
    if not url:
        return comp
    async with sem:
        detail = await parse_hsreplay_comp_detail(url, source_id=source_id)
    main_cards = _group_cards(detail.get("main_cards") or [])
    additional_cards = _group_cards(detail.get("additional_cards") or [])
    if main_cards or additional_cards:
        comp = dict(comp)
        comp.update(detail)
        comp["main_cards"] = main_cards or comp.get("main_cards") or []
        comp["core_cards"] = comp["main_cards"]
        comp["additional_cards"] = additional_cards or comp.get("additional_cards") or []
        comp["addon_cards"] = comp["additional_cards"]
        comp["minions"] = [c.get("name") for c in comp["main_cards"] + comp["additional_cards"] if c.get("name")]
    return comp


async def _firecrawl_detail(url: str, *, source_id: str) -> dict[str, Any]:
    source = Source(f"{source_id}_detail", url, "hsreplay", "battlegrounds", description=f"HSReplay BG comp detail {url}")
    scraped = await scrape_source(source)
    detail = parse_hsreplay_comp_detail_markdown(scraped.markdown, url=url)
    detail["_detail_backend"] = "firecrawl"
    return detail


async def fetch_battlegrounds_comps_firecrawl(
    *,
    source_id: str = "hsreplay_battlegrounds_comps",
    detail_limit: int = 40,
) -> dict[str, Any]:
    source = Source(source_id, HSREPLAY_COMPS_URL, "hsreplay", "battlegrounds", description="HSReplay Battlegrounds comps.")
    scraped = await scrape_source(source)
    comps = parse_hsreplay_markdown(scraped.markdown, detail_limit=detail_limit)
    errors: list[str] = []
    sem = asyncio.Semaphore(4)

    async def enrich(comp: dict[str, Any]) -> dict[str, Any]:
        url = _abs_hsreplay_url(comp.get("url") or "")
        if not url:
            return comp
        async with sem:
            try:
                detail = await _firecrawl_detail(url, source_id=source_id)
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:160]}")
                return comp
        listing_tier = _tier_letter(comp.get("tier"))
        merged = {**comp, **{k: v for k, v in detail.items() if v not in (None, "", [])}}
        detail_tier = _tier_letter(detail.get("tier"))
        # Prefer listing tier (Jeef table columns); detail pages often omit it.
        merged["tier"] = listing_tier or detail_tier or merged.get("tier") or None
        merged["url"] = url
        merged["main_cards"] = merged.get("main_cards") or []
        merged["core_cards"] = merged["main_cards"]
        merged["additional_cards"] = merged.get("additional_cards") or []
        merged["addon_cards"] = merged["additional_cards"]
        merged["minions"] = [c.get("name") for c in merged["main_cards"] + merged["additional_cards"] if c.get("name")]
        return merged

    comps = await asyncio.gather(*[enrich(comp) for comp in comps[:detail_limit]])
    with_cards = sum(1 for c in comps if c.get("main_cards") or c.get("additional_cards"))
    return {
        "type": "bg_comps",
        "comps": list(comps),
        "blocked": len(comps) < 3,
        "source": {
            "key": "hsreplay",
            "url": HSREPLAY_COMPS_URL,
            "backend": "firecrawl",
            "listing_final_url": scraped.final_url,
            "comps_with_cards": with_cards,
            "comps_total": len(comps),
            "errors": errors,
        },
    }


async def fetch_battlegrounds_comps(
    *,
    source_id: str = "hsreplay_battlegrounds_comps",
    detail_limit: int = 40,
) -> dict[str, Any]:
    backend = "hsreplay_flaresolverr"
    markdown = ""
    errors: list[str] = []
    comps: list[dict[str, Any]] = []
    try:
        firecrawl_result = await fetch_battlegrounds_comps_firecrawl(
            source_id=source_id, detail_limit=detail_limit
        )
        if len(firecrawl_result.get("comps") or []) >= 3:
            return firecrawl_result
        errors.append("firecrawl: listing returned fewer than three live comps")
    except Exception as exc:
        errors.append(f"firecrawl: {type(exc).__name__}: {str(exc)[:180]}")
    try:
        markdown, backend = await fetch_hsreplay_markdown(
            HSREPLAY_COMPS_URL, source_id=source_id
        )
        comps = parse_hsreplay_markdown(markdown, detail_limit=detail_limit)
    except Exception as exc:
        errors.append(f"markdown: {type(exc).__name__}: {str(exc)[:180]}")
    if len(comps) < 3:
        try:
            html, backend = await fetch_hsreplay_html(HSREPLAY_COMPS_URL, source_id=source_id)
            comps = _comps_from_html(html)
        except Exception as exc:
            errors.append(f"html: {type(exc).__name__}: {str(exc)[:180]}")
    if not comps:
        headers = _find_comp_headers(markdown)
        comps = [
            {
                "id": f"hsreplay-{h['remote_id']}",
                "comp_id": int(h["remote_id"]),
                "source": "hsreplay",
                "source_id": h["remote_id"],
                "slug": h["slug"],
                "name": _title_from_slug(h["slug"]),
                "title": _title_from_slug(h["slug"]),
                "description": "",
                "main_cards": [],
                "additional_cards": [],
                "minions": [],
                "url": h["url"],
            }
            for h in headers[:detail_limit]
        ]
    if not comps:
        return {
            "type": "bg_comps",
            "comps": [],
            "blocked": True,
            "source": {
                "key": "hsreplay",
                "url": HSREPLAY_COMPS_URL,
                "backend": backend,
                "comps_with_cards": 0,
                "comps_total": 0,
                "errors": errors,
            },
        }

    sem = asyncio.Semaphore(4)
    enriched = await asyncio.gather(
        *[_enrich_comp_cards(c, source_id=source_id, sem=sem) for c in comps[:detail_limit]]
    )
    comps = list(enriched)

    with_cards = sum(1 for c in comps if c.get("main_cards") or c.get("additional_cards"))

    return {
        "type": "bg_comps",
        "comps": comps,
        "blocked": len(comps) < 3,
        "source": {
            "key": "hsreplay",
            "url": HSREPLAY_COMPS_URL,
            "backend": backend,
            "comps_with_cards": with_cards,
            "comps_total": len(comps),
            "errors": errors,
        },
    }
