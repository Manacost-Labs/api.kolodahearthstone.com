from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .cards_index import card_from_id
from .firecrawl_backend import scrape_source_with_options
from .proxy_errors import ProxyPaymentRequiredError, proxy_tunnel_error
from .scrapers.proxy import httpx_client_kwargs
from .source_contracts import contract_quality_ok
from .source_validators import validate_structured
from .sources import Source

logger = logging.getLogger(__name__)

CLASS_MAP = {
    "death-knight": "Death Knight",
    "demon-hunter": "Demon Hunter",
    "druid": "Druid",
    "hunter": "Hunter",
    "mage": "Mage",
    "paladin": "Paladin",
    "priest": "Priest",
    "rogue": "Rogue",
    "shaman": "Shaman",
    "warlock": "Warlock",
    "warrior": "Warrior",
    "any": "Neutral",
}


def parse_heartharena_tierlist(html_content: str) -> dict[str, Any]:
    """Parse a HearthArena tier-list document into the stable source schema."""

    soup = BeautifulSoup(html_content, "html.parser")
    sections = soup.find_all("section", class_="tierlist")

    classes_data: list[dict[str, Any]] = []

    for section in sections:
        class_id = section.get("id")
        if not class_id or class_id == "change-log":
            continue

        class_name = CLASS_MAP.get(class_id, class_id.replace("-", " ").title())
        cards_list: list[dict[str, Any]] = []

        rarity_lis = section.find_all("li", class_="rarity")
        for rarity_li in rarity_lis:
            rarity_classes = rarity_li.get("class") or []
            rarity_name = "common"
            for rc in rarity_classes:
                if rc in ("commons", "rares", "epics", "legendaries"):
                    rarity_name = (
                        rc.replace("commons", "common")
                        .replace("rares", "rare")
                        .replace("epics", "epic")
                        .replace("legendaries", "legendary")
                    )
                    break

            tier_lis = rarity_li.find_all("li", class_="tier")
            for tier_li in tier_lis:
                tier_classes = tier_li.get("class") or []
                tier_id = "unknown"
                for tc in tier_classes:
                    if tc != "tier":
                        tier_id = tc
                        break

                header_el = tier_li.find("header")
                tier_name = (
                    header_el.get_text(strip=True) if header_el else tier_id.title()
                )

                cards_ol = tier_li.find("ol", class_="cards")
                if not cards_ol:
                    continue

                card_items = cards_ol.find_all("li")
                for card_item in card_items:
                    dl_el = card_item.find("dl", class_="card")
                    if not dl_el:
                        continue

                    dt_el = dl_el.find("dt")
                    dd_el = dl_el.find("dd", class_="score")

                    if not dt_el:
                        continue

                    parsed_name = dt_el.get_text(strip=True)

                    score_val = None
                    if dd_el:
                        try:
                            score_val = int(dd_el.get_text(strip=True))
                        except (ValueError, TypeError):
                            pass

                    img_url = dt_el.get("data-card-image") or ""
                    card_id = None
                    if img_url:
                        match = re.search(r"/([^/]+)\.(webp|png|jpg|gif)", img_url)
                        if match:
                            card_id = match.group(1)

                    card_meta = {}
                    if card_id:
                        card_meta = card_from_id(card_id, locale="ruRU")

                    card_entry = {
                        "id": card_id,
                        "card_id": card_id,
                        "dbfId": card_meta.get("dbfId"),
                        "name": card_meta.get("name") or parsed_name,
                        "heartharena_name": parsed_name,
                        "cost": card_meta.get("cost"),
                        "type": card_meta.get("type"),
                        "rarity": card_meta.get("rarity") or rarity_name.upper(),
                        "cardClass": card_meta.get("cardClass"),
                        "image_url": img_url
                        or (
                            f"https://art.hearthstonejson.com/v1/256x/{card_id}.png"
                            if card_id
                            else None
                        ),
                        "score": score_val,
                        "tier_id": tier_id,
                        "tier_name": tier_name,
                    }
                    cards_list.append(card_entry)

        cards_list.sort(
            key=lambda card: (
                card.get("score") if card.get("score") is not None else -999,
                card.get("name") or "",
            ),
            reverse=True,
        )

        classes_data.append(
            {
                "class_id": class_id,
                "class_name": class_name,
                "cards": cards_list,
                "total_cards": len(cards_list),
            }
        )

    return {
        "type": "heartharena_tierlist",
        "classes": classes_data,
        "total_classes": len(classes_data),
        "total_cards": sum(c["total_cards"] for c in classes_data),
    }


def _heartharena_html_is_usable(html_content: str) -> bool:
    """Fail closed unless both parser validators and the source contract pass."""

    try:
        structured = parse_heartharena_tierlist(html_content)
        contract_ok, _reason, _report = contract_quality_ok(
            "heartharena_tierlist",
            structured,
        )
        return contract_ok and validate_structured(
            "heartharena_tierlist",
            structured,
        ).ok
    except Exception:  # noqa: BLE001 - provider candidate validation fails closed
        return False


async def _fetch_residential_html(
    source_id: str,
    url: str,
    *,
    headers: dict[str, str],
) -> str:
    options = httpx_client_kwargs(source_id, page_url=url)
    try:
        async with httpx.AsyncClient(**options) as client:
            response = await client.get(url, headers=headers)
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


async def _fetch_heartharena_cloud(source: Source) -> tuple[str, str]:
    errors: list[str] = []
    urls = tuple(dict.fromkeys((source.url, "https://www.heartharena.com/tierlist")))
    for url in urls:
        candidate_source = replace(source, url=url)
        try:
            scraped = await scrape_source_with_options(
                candidate_source,
                formats=["html"],
                only_main_content=False,
                max_age_ms=0,
                brightdata_accept_html=_heartharena_html_is_usable,
            )
            html_content = scraped.html or scraped.markdown
            if not _heartharena_html_is_usable(html_content):
                raise RuntimeError("provider response failed the HearthArena contract")
            return html_content, scraped.backend
        except Exception as exc:  # noqa: BLE001 - try the locale-neutral page
            errors.append(type(exc).__name__)
    raise RuntimeError(
        "HearthArena cloud fallback failed validation "
        f"({', '.join(errors) or 'no provider result'})"
    )


async def fetch_heartharena_tierlist(source: Source) -> dict[str, Any]:
    """Fetch HearthArena through the cloud chain, then residential fallback."""

    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-encoding": "gzip, deflate",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    errors: list[str] = []
    try:
        html_content, backend = await _fetch_heartharena_cloud(source)
    except Exception as exc:  # noqa: BLE001 - residential is an independent route
        errors.append(f"cloud:{type(exc).__name__}")
    else:
        structured = parse_heartharena_tierlist(html_content)
        structured["_fetch_backend"] = backend
        return structured

    urls = tuple(dict.fromkeys((source.url, "https://www.heartharena.com/tierlist")))
    for fetch_url in urls:
        try:
            html_content = await _fetch_residential_html(
                source.id,
                fetch_url,
                headers=headers,
            )
        except ProxyPaymentRequiredError as exc:
            from .scrapers.rotator import record_residential_proxy_failure

            record_residential_proxy_failure(exc)
            errors.append(f"residential:HTTP_{exc.status_code}")
            break
        except Exception as exc:  # noqa: BLE001 - try the alternate route
            errors.append(type(exc).__name__)
            continue
        if _heartharena_html_is_usable(html_content):
            structured = parse_heartharena_tierlist(html_content)
            structured["_fetch_backend"] = "residential_httpx"
            return structured
        errors.append("invalid_content")

    raise RuntimeError(
        "HearthArena fetch failed across cloud and residential routes "
        f"({', '.join(errors)})"
    )
