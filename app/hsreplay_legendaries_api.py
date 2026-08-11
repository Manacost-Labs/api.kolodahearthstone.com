from __future__ import annotations

import logging
import re
from typing import Any

from .cards_index import card_from_id, resolve_card_name
from .hsreplay_client import fetch_hsreplay_json
from .parser_control import load_resolved_public_dataset
from .sources import Source
from .structured import parse_legendary_groups

logger = logging.getLogger(__name__)

LEGENDARIES_URL = "https://hsreplay.net/arena/legendaries/"
# Full packages endpoint includes pick_rate/offer_rate/score and per-class buckets.
LEGENDARIES_API_URL = "https://hsreplay.net/api/v1/arena/card_packages/"
LEGENDARIES_API_URL_FREE = "https://hsreplay.net/api/v1/arena/card_packages/free/"

HS_CLASS_MAP = {
    "DEATHKNIGHT": "Death Knight",
    "DEMONHUNTER": "Demon Hunter",
    "DRUID": "Druid",
    "HUNTER": "Hunter",
    "MAGE": "Mage",
    "PALADIN": "Paladin",
    "PRIEST": "Priest",
    "ROGUE": "Rogue",
    "SHAMAN": "Shaman",
    "WARLOCK": "Warlock",
    "WARRIOR": "Warrior",
    "NEUTRAL": "Neutral",
}

# HSReplay package buckets → arena UI classKey
HS_BUCKET_TO_CLASS_KEY = {
    "ALL": "all",
    "DEATHKNIGHT": "death-knight",
    "DEMONHUNTER": "demon-hunter",
    "DRUID": "druid",
    "HUNTER": "hunter",
    "MAGE": "mage",
    "PALADIN": "paladin",
    "PRIEST": "priest",
    "ROGUE": "rogue",
    "SHAMAN": "shaman",
    "WARLOCK": "warlock",
    "WARRIOR": "warrior",
}

_PCT_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*%?")
_JSON_PACKAGE_RE = re.compile(
    r'\{[^{}]*"package_key_card_id"\s*:\s*"[^"]+"[^{}]*\}',
    re.DOTALL,
)


def _class_name_from_card(card_id: str) -> str | None:
    from .cards_index import cards_by_id

    raw = cards_by_id().get(card_id) or {}
    cc = raw.get("cardClass") or raw.get("class")
    if cc:
        return HS_CLASS_MAP.get(str(cc).upper(), str(cc).replace("_", " ").title())
    return None


def _format_pct(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if "%" in text:
            return text.replace(",", ".")
        match = _PCT_RE.fullmatch(text.replace(",", "."))
        if match:
            return f"{match.group(1)}%"
        return text
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return f"{num}%" if abs(num) <= 100 else f"{num:.2f}%"


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    match = _PCT_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _group_package_cards(card_ids: list[str], *, locale: str = "ruRU") -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for card_id in card_ids:
        if not card_id:
            continue
        if card_id in grouped:
            grouped[card_id]["count"] = int(grouped[card_id].get("count") or 1) + 1
        else:
            grouped[card_id] = {"count": 1, "card_id": card_id, **card_from_id(card_id, locale=locale)}
    return list(grouped.values())


def _metrics_from_package(pkg: dict[str, Any]) -> dict[str, Any]:
    pick_rate = pkg.get("pick_rate") if pkg.get("pick_rate") is not None else pkg.get("pickRate")
    offer_rate = pkg.get("offer_rate") if pkg.get("offer_rate") is not None else pkg.get("offerRate")
    score = pkg.get("score") if pkg.get("score") is not None else pkg.get("arenasmith_score")
    return {
        "winrate": _format_pct(pkg.get("win_rate")),
        "pick_rate": _format_pct(pick_rate),
        "offer_rate": _format_pct(offer_rate),
        "score": _as_number(score),
    }


def normalize_legendary_package(pkg: dict[str, Any], *, locale: str = "ruRU") -> dict[str, Any] | None:
    key_id = pkg.get("package_key_card_id")
    if not key_id:
        return None
    key_card = {"card_id": key_id, **card_from_id(str(key_id), locale=locale)}
    included = _group_package_cards(list(pkg.get("package_card_ids") or []), locale=locale)
    metrics = _metrics_from_package(pkg)
    return {
        "key_card": key_card,
        "legendary_card": key_card,
        "cards": included,
        **metrics,
        "class": _class_name_from_card(str(key_id)),
        "by_class": {},
    }


def _card_id_from_row(row: dict[str, Any]) -> str | None:
    for key in ("card_id", "id", "cardId"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def _stats_index_from_cards(cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in cards:
        if not isinstance(row, dict):
            continue
        card_id = _card_id_from_row(row)
        if not card_id:
            continue
        index[card_id] = {
            "pick_rate": row.get("pick_rate") if row.get("pick_rate") is not None else row.get("pickRate"),
            "offer_rate": row.get("offer_rate") if row.get("offer_rate") is not None else row.get("offerRate"),
            "score": row.get("score") if row.get("score") is not None else row.get("arenasmith_score"),
            "win_rate": row.get("win_rate") if row.get("win_rate") is not None else row.get("winrate"),
        }
    return index


async def _load_arena_card_stats_index(source_id: str) -> tuple[dict[str, dict[str, Any]], str]:
    try:
        from .hsreplay_arena_api import fetch_arena_card_tiers

        payload = await fetch_arena_card_tiers(source_id=source_id)
        cards = [row for row in (payload.get("cards") or []) if isinstance(row, dict)]
        if cards:
            return _stats_index_from_cards(cards), "hsreplay_arena_api"
    except Exception as exc:
        logger.warning("Live arena card stats unavailable for legendaries enrich: %s", exc)

    dataset = load_resolved_public_dataset("hsreplay_arena_cards_advanced") or {}
    data = dataset.get("data") or {}
    structured = data.get("structured") or data.get("hsreplay_extracted") or {}
    cards = [row for row in (structured.get("cards") or []) if isinstance(row, dict)]
    if cards:
        return _stats_index_from_cards(cards), "cached_hsreplay_arena_cards_advanced"
    return {}, "none"


def enrich_legendary_groups(
    groups: list[dict[str, Any]],
    stats_by_card_id: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """Fill missing ALL metrics from Arenasmith card stats (fallback only)."""
    filled = {"pick_rate": 0, "offer_rate": 0, "score": 0, "joined": 0}
    for group in groups:
        key = group.get("key_card") or group.get("legendary_card") or {}
        card_id = _card_id_from_row(key) if isinstance(key, dict) else None
        if not card_id:
            continue
        stats = stats_by_card_id.get(card_id)
        if not stats:
            continue
        filled["joined"] += 1
        if group.get("pick_rate") is None and stats.get("pick_rate") is not None:
            group["pick_rate"] = _format_pct(stats["pick_rate"])
            filled["pick_rate"] += 1
        if group.get("offer_rate") is None and stats.get("offer_rate") is not None:
            group["offer_rate"] = _format_pct(stats["offer_rate"])
            filled["offer_rate"] += 1
        if group.get("score") is None and stats.get("score") is not None:
            group["score"] = _as_number(stats["score"])
            filled["score"] += 1
        by_class = group.setdefault("by_class", {})
        all_bucket = by_class.setdefault("all", {})
        if all_bucket.get("pick_rate") is None and group.get("pick_rate") is not None:
            all_bucket["pick_rate"] = group["pick_rate"]
        if all_bucket.get("offer_rate") is None and group.get("offer_rate") is not None:
            all_bucket["offer_rate"] = group["offer_rate"]
        if all_bucket.get("score") is None and group.get("score") is not None:
            all_bucket["score"] = group["score"]
        if all_bucket.get("winrate") is None and group.get("winrate") is not None:
            all_bucket["winrate"] = group["winrate"]
    return filled


def _groups_from_class_buckets(data: dict[str, Any], *, locale: str = "ruRU") -> list[dict[str, Any]]:
    """Merge ALL + per-class package rows into one group list with by_class metrics."""
    groups_by_id: dict[str, dict[str, Any]] = {}

    for bucket, rows in data.items():
        if not isinstance(rows, list):
            continue
        class_key = HS_BUCKET_TO_CLASS_KEY.get(str(bucket).upper())
        if not class_key:
            continue
        for pkg in rows:
            if not isinstance(pkg, dict):
                continue
            key_id = str(pkg.get("package_key_card_id") or "")
            if not key_id:
                continue
            metrics = _metrics_from_package(pkg)
            group = groups_by_id.get(key_id)
            if group is None:
                group = normalize_legendary_package(pkg, locale=locale)
                if not group:
                    continue
                group["by_class"] = {}
                groups_by_id[key_id] = group
            else:
                # Prefer richer package_card_ids / metrics when ALL arrives later/earlier.
                if class_key == "all":
                    cards = _group_package_cards(list(pkg.get("package_card_ids") or []), locale=locale)
                    if cards:
                        group["cards"] = cards
                    for field, value in metrics.items():
                        if value is not None:
                            group[field] = value
            group.setdefault("by_class", {})[class_key] = metrics

    groups = list(groups_by_id.values())
    for group in groups:
        by_class = group.get("by_class") or {}
        all_metrics = by_class.get("all") or {}
        # Top-level metrics are always the global ALL slice.
        for field in ("winrate", "pick_rate", "offer_rate", "score"):
            if all_metrics.get(field) is not None:
                group[field] = all_metrics[field]
            elif group.get(field) is not None:
                all_metrics[field] = group[field]
        by_class["all"] = all_metrics
        group["by_class"] = by_class
    return groups


def _payload_data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return data if isinstance(data, dict) else {}


def _extract_packages_from_html(html: str) -> list[dict[str, Any]]:
    if not html:
        return []
    packages: list[dict[str, Any]] = []
    for match in _JSON_PACKAGE_RE.finditer(html):
        raw = match.group(0)
        try:
            import json

            pkg = json.loads(raw)
        except Exception:
            continue
        if isinstance(pkg, dict) and pkg.get("package_key_card_id"):
            packages.append(pkg)
    return packages


def _normalize_firecrawl_group(raw: dict[str, Any], *, locale: str = "ruRU") -> dict[str, Any] | None:
    cards_raw = [row for row in (raw.get("cards") or []) if isinstance(row, dict)]
    cards: list[dict[str, Any]] = []
    for row in cards_raw:
        name = str(row.get("name") or "").strip()
        resolved = resolve_card_name(name) if name else {}
        card_id = resolved.get("card_id") or resolved.get("id") or row.get("card_id")
        if not card_id and name:
            cards.append({"count": int(row.get("count") or 1), "name": name, **resolved})
            continue
        if not card_id:
            continue
        cards.append(
            {
                "count": int(row.get("count") or 1),
                "card_id": str(card_id),
                **card_from_id(str(card_id), locale=locale),
                "name": name or card_from_id(str(card_id), locale=locale).get("name"),
            }
        )

    key_card: dict[str, Any] | None = None
    for row in cards:
        rarity = str(row.get("rarity") or "").upper()
        if rarity == "LEGENDARY" and row.get("card_id"):
            key_card = {k: v for k, v in row.items() if k != "count"}
            break
    if key_card is None and cards:
        first = cards[0]
        if first.get("card_id"):
            key_card = {k: v for k, v in first.items() if k != "count"}
        elif first.get("name"):
            resolved = resolve_card_name(str(first["name"]))
            card_id = resolved.get("card_id") or resolved.get("id")
            if card_id:
                key_card = {"card_id": str(card_id), **card_from_id(str(card_id), locale=locale)}

    if not key_card or not key_card.get("card_id"):
        return None

    metrics = {
        "winrate": _format_pct(raw.get("winrate") or raw.get("win_rate")),
        "pick_rate": _format_pct(raw.get("pick_rate") or raw.get("pickRate")),
        "offer_rate": _format_pct(raw.get("offer_rate") or raw.get("offerRate")),
        "score": _as_number(raw.get("score") or raw.get("arenasmith_score")),
    }
    return {
        "key_card": key_card,
        "legendary_card": key_card,
        "cards": cards,
        **metrics,
        "class": _class_name_from_card(str(key_card["card_id"])),
        "by_class": {"all": dict(metrics)},
    }


def _lines_from_firecrawl(markdown: str, html: str) -> list[str]:
    text = markdown or ""
    if not text and html:
        text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
        text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "\n", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


async def fetch_legendary_groups_via_firecrawl(
    *,
    source_id: str = "hsreplay_arena_legendaries",
    locale: str = "ruRU",
) -> dict[str, Any]:
    from .firecrawl_backend import scrape_source_with_options
    from .hsreplay_auth import hsreplay_cookies_for_fetch

    cookie = "; ".join(
        f"{item['name']}={item['value']}"
        for item in hsreplay_cookies_for_fetch()
        if item.get("name") and item.get("value")
    )
    source = Source(
        source_id,
        LEGENDARIES_URL,
        "hsreplay",
        "arena",
        description="HSReplay Arena legendaries (Firecrawl fallback).",
    )
    scraped = await scrape_source_with_options(
        source,
        formats=["markdown", "html"],
        only_main_content=False,
        headers={
            "Cookie": cookie,
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        },
        wait_ms=8000,
        max_age_ms=0,
    )

    packages = _extract_packages_from_html(scraped.html)
    groups: list[dict[str, Any]] = []
    parse_mode = "embedded_json"
    if packages:
        # Firecrawl usually captures a flat list; treat as ALL.
        fake_data = {"ALL": packages}
        groups = _groups_from_class_buckets(fake_data, locale=locale)
    else:
        parse_mode = "page_text"
        parsed = parse_legendary_groups(_lines_from_firecrawl(scraped.markdown, scraped.html))
        for raw in parsed:
            group = _normalize_firecrawl_group(raw, locale=locale)
            if group:
                groups.append(group)

    if len(groups) < 10:
        raise RuntimeError(
            f"Firecrawl legendaries fallback produced too few groups ({len(groups)}); mode={parse_mode}"
        )

    stats_index, stats_backend = await _load_arena_card_stats_index(source_id)
    enrich_stats = enrich_legendary_groups(groups, stats_index)

    return {
        "type": "arena_legendary_groups",
        "groups": groups,
        "source": {
            "key": "hsreplay",
            "url": LEGENDARIES_URL,
            "api_url": LEGENDARIES_API_URL,
            "backend": "firecrawl+hsreplay_api",
            "firecrawl": {
                "ok": True,
                "status_code": scraped.status_code,
                "final_url": scraped.final_url,
                "content_length": scraped.content_length,
                "parse_mode": parse_mode,
                "credits_used": scraped.metadata.get("creditsUsed"),
            },
            "enrich": {
                "stats_backend": stats_backend,
                **enrich_stats,
            },
        },
    }


async def fetch_legendary_groups(
    *,
    source_id: str = "hsreplay_arena_legendaries",
    locale: str = "ruRU",
) -> dict[str, Any]:
    api_url = LEGENDARIES_API_URL
    groups: list[dict[str, Any]] = []
    backend = "hsreplay_api"

    try:
        last_error: Exception | None = None
        for url in (LEGENDARIES_API_URL, LEGENDARIES_API_URL_FREE):
            try:
                candidate = await fetch_hsreplay_json(url, source_id=source_id)
                data = _payload_data(candidate if isinstance(candidate, dict) else {})
                built = _groups_from_class_buckets(data, locale=locale)
                if len(built) >= 10:
                    groups = built
                    api_url = url
                    break
                last_error = RuntimeError(f"{url} returned too few groups ({len(built)})")
            except Exception as exc:
                last_error = exc
                continue
        if not groups:
            raise last_error or RuntimeError("card_packages returned no groups")
    except Exception as exc:
        logger.warning("HSReplay legendaries API failed (%s); trying Firecrawl fallback", exc)
        return await fetch_legendary_groups_via_firecrawl(source_id=source_id, locale=locale)

    # Only enrich missing ALL metrics; never overwrite per-class package stats.
    stats_index, stats_backend = await _load_arena_card_stats_index(source_id)
    enrich_stats = enrich_legendary_groups(groups, stats_index)
    class_bucket_count = max((len(g.get("by_class") or {}) for g in groups), default=0)

    return {
        "type": "arena_legendary_groups",
        "groups": groups,
        "source": {
            "key": "hsreplay",
            "url": LEGENDARIES_URL,
            "api_url": api_url,
            "backend": backend,
            "class_buckets": class_bucket_count,
            "enrich": {
                "stats_backend": stats_backend,
                **enrich_stats,
            },
        },
    }
