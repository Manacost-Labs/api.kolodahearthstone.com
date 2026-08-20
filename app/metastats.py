from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .cards_index import card_from_id
from .firecrawl_backend import scrape_source_with_options
from .proxy_errors import ProxyPaymentRequiredError, proxy_tunnel_error
from .scrapers.proxy import httpx_client_kwargs
from .source_contracts import contract_quality_ok
from .sources import Source

logger = logging.getLogger(__name__)

CLASSES = [
    "DeathKnight",
    "DemonHunter",
    "Druid",
    "Hunter",
    "Mage",
    "Paladin",
    "Priest",
    "Rogue",
    "Shaman",
    "Warlock",
    "Warrior",
]

_MAX_CLASS_FETCH_CONCURRENCY = 2
_HTML_HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,image/apng,*/*;q=0.8"
    ),
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def parse_decklist_div(dl, archetype_id: str, archetype_name: str, class_name: str) -> dict[str, Any] | None:
    h4 = dl.find("h4")
    if not h4:
        return None

    h4_text = h4.get_text(strip=True)
    deck_id = ""
    if "#" in h4_text:
        deck_id = h4_text.split("#")[-1].strip()
    else:
        a_tag = h4.find("a")
        if a_tag and a_tag.get("href"):
            href = a_tag["href"]
            match = re.search(r"/deck/(\d+)/", href)
            if match:
                deck_id = match.group(1)

    title = h4_text.strip()

    games = None
    win_rate = None

    text = dl.get_text()
    games_match = re.search(r"#Games:\s*([\d,]+)", text)
    if games_match:
        try:
            games = int(games_match.group(1).replace(",", ""))
        except ValueError:
            pass

    wr_match = re.search(r"#Win\s*Rate:\s*([\d.]+\s*%)", text, re.IGNORECASE)
    if wr_match:
        win_rate = wr_match.group(1).strip()

    deck_code = ""
    btn = dl.find(class_="copytoclipboard")
    if btn and btn.get("data-clipboard-text"):
        raw_text = btn["data-clipboard-text"]
        from .deck_decode import first_deck_code_from_text

        deck_code = first_deck_code_from_text(raw_text) or ""

    cards = []
    card_list_items = dl.find_all(class_="card-list-item")
    for item in card_list_items:
        name_div = item.find(class_="card-name")
        card_name = ""
        if name_div:
            a_link = name_div.find("a")
            if a_link:
                card_name = a_link.get_text(strip=True)
            else:
                card_name = name_div.get_text(strip=True)

        qty_div = item.find(class_="card-quantity")
        quantity = 1
        if qty_div:
            qty_text = qty_div.get_text(strip=True).lower()
            qty_match = re.search(r"(\d+)", qty_text)
            if qty_match:
                quantity = int(qty_match.group(1))

        cost_div = item.find(class_="card-gem")
        cost = None
        if cost_div:
            try:
                cost = int(cost_div.get_text(strip=True))
            except ValueError:
                pass

        card_id = None
        img_hover = item.find(id="card-image-hover")
        if img_hover and img_hover.find("img"):
            img_src = img_hover.find("img").get("src") or ""
            card_id_match = re.search(r"/([^/]+)\.png", img_src)
            if card_id_match:
                card_id = card_id_match.group(1)

        if not card_id and name_div and name_div.find("img"):
            bars_src = name_div.find("img").get("src") or ""
            card_id_match = re.search(r"/([^/]+)\.png", bars_src)
            if card_id_match:
                card_id = card_id_match.group(1)

        card_meta = {}
        if card_id:
            card_meta = card_from_id(card_id, locale="ruRU")

        cards.append({
            "id": card_id,
            "card_id": card_id,
            "dbfId": card_meta.get("dbfId"),
            "name": card_meta.get("name") or card_name,
            "metastats_name": card_name,
            "cost": card_meta.get("cost") or cost,
            "count": quantity,
        })

    return {
        "deck_id": deck_id,
        "title": title,
        "class": class_name,
        "archetype_id": archetype_id,
        "archetype_name": archetype_name,
        "games": games,
        "win_rate": win_rate,
        "deck_code": deck_code,
        "cards": cards,
    }


def parse_metastats_class_page(html_content: str, class_name: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_content, "lxml")
    decks = []

    panes = soup.find_all(class_="tab-pane")
    if not panes:
        decklists = soup.find_all(class_="decklist")
        for dl in decklists:
            deck_info = parse_decklist_div(dl, archetype_id="Unknown", archetype_name="Unknown", class_name=class_name)
            if deck_info:
                decks.append(deck_info)
    else:
        for pane in panes:
            pane_id = pane.get("id") or "Unknown"
            decklists = pane.find_all(class_="decklist")
            for dl in decklists:
                h4 = dl.find("h4")
                h4_text = h4.get_text(strip=True) if h4 else ""
                archetype_name = h4_text.split("#")[0].strip() if "#" in h4_text else h4_text.strip()
                if not archetype_name:
                    archetype_name = re.sub(r"(\x1b)?([A-Z])", r" \2", pane_id).strip()

                deck_info = parse_decklist_div(dl, archetype_id=pane_id, archetype_name=archetype_name, class_name=class_name)
                if deck_info:
                    decks.append(deck_info)
    return decks


async def _fetch_residential_html(source_id: str, url: str) -> str:
    options = httpx_client_kwargs(source_id, page_url=url)
    try:
        async with httpx.AsyncClient(**options) as client:
            response = await client.get(url, headers=_HTML_HEADERS)
            response.raise_for_status()
            return response.text
    except Exception as exc:
        typed_proxy_error = proxy_tunnel_error(
            exc,
            proxy_used=bool(options.get("proxy")),
        )
        if typed_proxy_error is not None:
            raise typed_proxy_error from exc
        raise


async def _fetch_cloud_html(
    source: Source,
    *,
    accept_html: Callable[[str], bool],
) -> tuple[str, str]:
    # Deliberately pass no request headers or cookies: this keeps Bright Data
    # eligible while preventing authentication material from entering its path.
    scraped = await scrape_source_with_options(
        source,
        formats=["html"],
        only_main_content=False,
        max_age_ms=0,
        brightdata_accept_html=accept_html,
    )
    html_content = scraped.html or scraped.markdown
    if not accept_html(html_content):
        raise RuntimeError("MetaStats cloud response failed source validation")
    return html_content, scraped.backend


def _metastats_class_html_is_usable(html_content: str, class_name: str) -> bool:
    try:
        if parse_metastats_class_page(html_content, class_name):
            return True
        soup = BeautifulSoup(html_content, "lxml")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        heading = next(
            (
                tag
                for tag in soup.find_all("h1", class_="page-header")
                if tag.get_text(" ", strip=True) == f"{class_name} Decks"
            ),
            None,
        )
        page_text = re.sub(r"[^a-z]", "", soup.get_text(" ").casefold())
        expected_classes = {
            re.sub(r"[^a-z]", "", candidate.casefold()) for candidate in CLASSES
        }
        blocked = any(
            marker in html_content.casefold()
            for marker in ("cf-chl-", "just a moment", "access denied")
        )
        return bool(
            title.startswith(f"{class_name} Decks (")
            and heading is not None
            and heading.find_parent(id="page-wrapper") is not None
            and all(candidate in page_text for candidate in expected_classes)
            and not blocked
        )
    except Exception:  # noqa: BLE001 - provider candidate validation fails closed
        return False


def _metastats_contract_is_usable(
    source_id: str,
    structured: dict[str, Any],
) -> bool:
    try:
        ok, _reason, _report = contract_quality_ok(source_id, structured)
        return ok
    except Exception:  # noqa: BLE001 - source contract validation fails closed
        return False


async def _fetch_metastats_class(
    source: Source,
    class_name: str,
    *,
    semaphore: asyncio.Semaphore,
    proxy_unavailable: asyncio.Event,
) -> tuple[str, list[dict[str, Any]], str]:
    url = f"https://metastats.net/hearthstone/class/decks/{class_name}/"
    class_source = replace(source, url=url)

    async with semaphore:
        html_content = ""
        backend = ""
        try:
            html_content, backend = await _fetch_cloud_html(
                class_source,
                accept_html=lambda candidate: _metastats_class_html_is_usable(
                    candidate,
                    class_name,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - residential is independent
            logger.warning(
                "MetaStats cloud class fetch failed class=%s error=%s",
                class_name,
                type(exc).__name__,
            )

        if (
            not _metastats_class_html_is_usable(html_content, class_name)
            and not proxy_unavailable.is_set()
        ):
            try:
                html_content = await _fetch_residential_html(source.id, url)
                backend = "residential_httpx"
            except ProxyPaymentRequiredError as exc:
                from .scrapers.rotator import record_residential_proxy_failure

                record_residential_proxy_failure(exc)
                proxy_unavailable.set()
            except Exception as exc:  # noqa: BLE001 - source failure is handled below
                logger.warning(
                    "MetaStats residential class fetch failed class=%s error=%s",
                    class_name,
                    type(exc).__name__,
                )

        decks = parse_metastats_class_page(html_content, class_name)
        if not _metastats_class_html_is_usable(html_content, class_name):
            raise RuntimeError(f"MetaStats class {class_name} returned invalid content")
        return class_name, decks, backend


async def fetch_metastats_decks(source: Source) -> dict[str, Any]:
    """Fetch all class pages with bounded concurrency and all-or-nothing output."""

    semaphore = asyncio.Semaphore(_MAX_CLASS_FETCH_CONCURRENCY)
    proxy_unavailable = asyncio.Event()
    results = await asyncio.gather(
        *(
            _fetch_metastats_class(
                source,
                class_name,
                semaphore=semaphore,
                proxy_unavailable=proxy_unavailable,
            )
            for class_name in CLASSES
        ),
        return_exceptions=True,
    )

    failures: list[str] = []
    by_class: dict[str, list[dict[str, Any]]] = {}
    class_backends: dict[str, str] = {}
    for class_name, result in zip(CLASSES, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(
                "MetaStats class unavailable class=%s error=%s",
                class_name,
                type(result).__name__,
            )
            failures.append(class_name)
            continue
        parsed_class, decks, backend = result
        by_class[parsed_class] = decks
        class_backends[parsed_class] = backend

    if failures or set(by_class) != set(CLASSES):
        raise RuntimeError(
            "MetaStats class coverage incomplete "
            f"({len(by_class)}/{len(CLASSES)}; failed={','.join(failures)})"
        )

    all_decks = [deck for class_name in CLASSES for deck in by_class[class_name]]
    backends = sorted(set(class_backends.values()))
    fetch_backend = (
        backends[0] if len(backends) == 1 else f"mixed[{','.join(backends)}]"
    )
    structured = {
        "type": "metastats_decks",
        "decks": all_decks,
        "classes_parsed": list(CLASSES),
        "empty_classes": [
            class_name for class_name in CLASSES if not by_class[class_name]
        ],
        "total_decks": len(all_decks),
        "_fetch_backend": fetch_backend,
    }
    ok, reason, _report = contract_quality_ok(source.id, structured)
    if not ok:
        raise RuntimeError(f"MetaStats decks failed source contract: {reason}")
    return structured


def parse_metastats_matchups(html_content: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_content, "lxml")
    table = soup.find("table")
    if not table:
        return {"type": "metastats_matchups", "matchups": [], "archetypes": []}

    th_elements = table.find_all("th")
    headers = [th.get_text(strip=True) for th in th_elements]
    headers = [h for h in headers if h]

    matchups = []
    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else table.find_all("tr")

    for tr in rows:
        row_arch_el = tr.find(class_="playerarch")
        if not row_arch_el:
            continue
        row_arch = row_arch_el.get_text(strip=True)

        tds = tr.find_all("td")
        opponent_tds = tds[1:]
        for col_idx, td in enumerate(opponent_tds):
            if col_idx >= len(headers):
                break
            opp_arch = headers[col_idx]

            div = td.find("div")
            if not div:
                continue

            title_attr = div.get("title") or ""
            title_text = html.unescape(title_attr)

            games = None
            winrate = None
            vs_winrate = None

            games_match = re.search(r"Games:\s*(\d+)", title_text)
            if games_match:
                games = int(games_match.group(1))

            lines = [
                line.strip()
                for line in re.split(
                    r"<br/?>|\n",
                    title_text,
                    flags=re.IGNORECASE,
                )
                if line.strip()
            ]
            for line in lines:
                if ":" in line:
                    parts = line.split(":", 1)
                    name = parts[0].strip()
                    val = parts[1].strip()
                    if name.lower() == row_arch.lower():
                        winrate = val
                    elif name.lower() == opp_arch.lower():
                        vs_winrate = val

            if not winrate:
                cell_text = td.get_text(strip=True)
                if cell_text and cell_text != "-":
                    winrate = cell_text

            if games is None and not winrate:
                continue

            matchups.append({
                "archetype": row_arch,
                "vs": opp_arch,
                "games": games,
                "winrate": winrate,
                "vs_winrate": vs_winrate,
            })

    return {
        "type": "metastats_matchups",
        "matchups": matchups,
        "archetypes": headers,
    }


async def fetch_metastats_matchups(source: Source) -> dict[str, Any]:
    html_content = ""
    backend = ""
    try:
        html_content, backend = await _fetch_cloud_html(
            source,
            accept_html=lambda candidate: _metastats_contract_is_usable(
                source.id,
                parse_metastats_matchups(candidate),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - residential is an independent route
        logger.warning(
            "MetaStats cloud matchup fetch failed error=%s",
            type(exc).__name__,
        )

    cloud_candidate = parse_metastats_matchups(html_content)
    if not _metastats_contract_is_usable(source.id, cloud_candidate):
        try:
            html_content = await _fetch_residential_html(source.id, source.url)
            backend = "residential_httpx"
        except ProxyPaymentRequiredError as exc:
            from .scrapers.rotator import record_residential_proxy_failure

            record_residential_proxy_failure(exc)
            raise RuntimeError(
                "MetaStats matchups failed across cloud and residential routes"
            ) from exc
        except Exception as exc:
            logger.warning(
                "MetaStats residential matchup fetch failed error=%s",
                type(exc).__name__,
            )
            raise RuntimeError(
                "MetaStats matchups failed across cloud and residential routes"
            ) from exc
    structured = parse_metastats_matchups(html_content)
    ok, reason, _report = contract_quality_ok(source.id, structured)
    if not ok:
        raise RuntimeError(f"MetaStats matchups failed source contract: {reason}")
    structured["_fetch_backend"] = backend
    return structured
