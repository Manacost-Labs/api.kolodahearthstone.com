from __future__ import annotations

import logging
import math
import re
from typing import Any

from .cards_index import card_from_id, resolve_card_name
from .completeness import (
    ARENA_LEGENDARY_EXPECTED_BUCKETS,
    COMPLETENESS_SCHEMA_VERSION,
    build_hsreplay_arena_upstream_freshness,
    build_hsreplay_transport_evidence_unavailable,
    row_retrieval_evidence,
)
from .hsreplay_client import fetch_hsreplay_json, get_hsreplay_json_target_headers
from .publish_gate import validate_candidate_for_publish
from .sources import SOURCE_BY_ID, Source
from .structured import parse_legendary_groups
from .structured_schema import validate_structured_schema

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
assert tuple(HS_BUCKET_TO_CLASS_KEY) == ARENA_LEGENDARY_EXPECTED_BUCKETS

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


def _percent_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("%"):
            text = text[:-1].strip()
        text = text.replace(",", ".")
    try:
        number = float(text if isinstance(value, str) else value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0.0 <= number <= 100.0:
        return None
    return number


def _format_pct(value: Any) -> str | None:
    if value is None:
        return None
    number = _percent_number(value)
    if number is None:
        raise ValueError("percentage must be finite numeric in 0..100")
    return f"{number}%"


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("numeric value must be finite")
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if not value:
            raise ValueError("numeric value must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric value must be finite") from exc
    if not math.isfinite(number):
        raise ValueError("numeric value must be finite")
    return number


def _winrate_availability(
    *,
    winrate: object,
    pick_rate: object,
) -> dict[str, object]:
    if winrate not in (None, ""):
        return {"available": True, "reason": None}
    if _percent_number(pick_rate) == 0:
        return {
            "available": False,
            "reason": "upstream_unavailable_at_zero_pick_rate",
        }
    return {"available": False, "reason": None}


def _score_availability(*, score: object) -> dict[str, object]:
    if score is not None:
        return {"available": True, "reason": None}
    return {
        "available": False,
        "reason": "upstream_score_not_reported",
    }


def _legendary_field_availability(
    *,
    winrate: object,
    pick_rate: object,
    score: object,
) -> dict[str, dict[str, object]]:
    return {
        "winrate": _winrate_availability(
            winrate=winrate,
            pick_rate=pick_rate,
        ),
        "score": _score_availability(score=score),
    }


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


def _required_package_card_ids(pkg: dict[str, Any]) -> list[str]:
    value = pkg.get("package_card_ids")
    if not isinstance(value, list):
        raise TypeError("package_card_ids must be a list")
    if not value:
        raise ValueError("package_card_ids must not be empty")
    if any(not isinstance(card_id, str) or not card_id.strip() for card_id in value):
        raise ValueError("package_card_ids must contain non-empty strings")
    return [card_id.strip() for card_id in value]


def _metrics_from_package(pkg: dict[str, Any]) -> dict[str, Any]:
    pick_rate = pkg.get("pick_rate") if pkg.get("pick_rate") is not None else pkg.get("pickRate")
    offer_rate = pkg.get("offer_rate") if pkg.get("offer_rate") is not None else pkg.get("offerRate")
    score = pkg.get("score") if pkg.get("score") is not None else pkg.get("arenasmith_score")
    winrate = _format_pct(pkg.get("win_rate"))
    formatted_pick_rate = _format_pct(pick_rate)
    normalized_score = _as_number(score)
    return {
        "winrate": winrate,
        "pick_rate": formatted_pick_rate,
        "offer_rate": _format_pct(offer_rate),
        "score": normalized_score,
        "field_availability": _legendary_field_availability(
            winrate=winrate,
            pick_rate=formatted_pick_rate,
            score=normalized_score,
        ),
    }


def _first_non_none(row: dict[str, Any], *fields: str) -> Any:
    for field in fields:
        if field in row and row[field] is not None:
            return row[field]
    return None


def normalize_legendary_package(pkg: dict[str, Any], *, locale: str = "ruRU") -> dict[str, Any] | None:
    key_id = pkg.get("package_key_card_id")
    if not isinstance(key_id, str) or not key_id.strip():
        return None
    key_id = key_id.strip()
    key_card = {"card_id": key_id, **card_from_id(key_id, locale=locale)}
    included = _group_package_cards(
        _required_package_card_ids(pkg),
        locale=locale,
    )
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


async def _load_arena_card_stats_index(
    source_id: str,
    *,
    expected_meta_period_id: int | None,
) -> tuple[dict[str, dict[str, Any]], str]:
    """Load enrichment only from a verified live snapshot in the same period."""

    if (
        isinstance(expected_meta_period_id, bool)
        or not isinstance(expected_meta_period_id, int)
        or expected_meta_period_id <= 0
    ):
        return {}, "none"
    try:
        from .hsreplay_arena_api import fetch_arena_card_tiers

        payload = await fetch_arena_card_tiers(source_id=source_id)
        freshness = payload.get("upstream_freshness")
        if (
            not isinstance(freshness, dict)
            or freshness.get("status") != "fresh"
            or freshness.get("meta_period_id") != expected_meta_period_id
            or payload.get("population_completeness") != "unverifiable"
        ):
            return {}, "none"
        validate_structured_schema(payload)
        gate = validate_candidate_for_publish(
            SOURCE_BY_ID["hsreplay_arena_cards_advanced"],
            {"structured": payload},
            backend="hsreplay_api",
        )
        if not gate.ok:
            return {}, "none"
        cards = [row for row in (payload.get("cards") or []) if isinstance(row, dict)]
        if cards:
            return _stats_index_from_cards(cards), "verified_live_hsreplay_arena_api"
    except Exception as exc:
        logger.warning(
            "Verified live arena card stats unavailable for legendaries enrich (%s)",
            type(exc).__name__,
        )
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
        if stats:
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
        group["field_availability"] = _legendary_field_availability(
            winrate=group.get("winrate"),
            pick_rate=group.get("pick_rate"),
            score=group.get("score"),
        )
        all_bucket["field_availability"] = _legendary_field_availability(
            winrate=all_bucket.get("winrate"),
            pick_rate=all_bucket.get("pick_rate"),
            score=all_bucket.get("score"),
        )
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
            package_card_ids = _required_package_card_ids(pkg)
            raw_key_id = pkg.get("package_key_card_id")
            if not isinstance(raw_key_id, str) or not raw_key_id.strip():
                raise ValueError("package_key_card_id must be a non-empty string")
            key_id = raw_key_id.strip()
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
                    cards = _group_package_cards(package_card_ids, locale=locale)
                    if cards:
                        group["cards"] = cards
                    for field, value in metrics.items():
                        if field == "field_availability":
                            continue
                        if value is not None:
                            group[field] = value
                    group["field_availability"] = metrics["field_availability"]
            group.setdefault("by_class", {})[class_key] = metrics

    groups = list(groups_by_id.values())
    for group in groups:
        by_class = group.get("by_class") or {}
        all_metrics = by_class.get("all")
        # Top-level metrics are always the global ALL slice.
        if isinstance(all_metrics, dict):
            for field in ("winrate", "pick_rate", "offer_rate", "score"):
                group[field] = all_metrics.get(field)
            group["field_availability"] = all_metrics.get(
                "field_availability",
                _legendary_field_availability(
                    winrate=group.get("winrate"),
                    pick_rate=group.get("pick_rate"),
                    score=group.get("score"),
                ),
            )
        else:
            for field in ("winrate", "pick_rate", "offer_rate", "score"):
                group[field] = None
            group["field_availability"] = _legendary_field_availability(
                winrate=None,
                pick_rate=None,
                score=None,
            )
        group["by_class"] = by_class
    return groups


def _bucket_coverage(data: dict[str, Any]) -> dict[str, list[str]]:
    seen_bucket_package_keys: set[tuple[str, str]] = set()
    duplicate_bucket_package_keys: set[str] = set()
    for bucket, rows in data.items():
        canonical_bucket = str(bucket).strip().upper()
        if canonical_bucket not in HS_BUCKET_TO_CLASS_KEY or not isinstance(rows, list):
            continue
        for package in rows:
            if not isinstance(package, dict):
                continue
            key = str(package.get("package_key_card_id") or "").strip()
            if key:
                pair = (canonical_bucket, key)
                if pair in seen_bucket_package_keys:
                    duplicate_bucket_package_keys.add(f"{canonical_bucket}:{key}")
                seen_bucket_package_keys.add(pair)
    expected_buckets = list(ARENA_LEGENDARY_EXPECTED_BUCKETS)
    observed_buckets = [
        expected
        for expected in expected_buckets
        if any(
            str(bucket).strip().upper() == expected and isinstance(rows, list)
            for bucket, rows in data.items()
        )
    ]
    return {
        "expected_buckets": expected_buckets,
        "observed_buckets": observed_buckets,
        "missing_buckets": [
            bucket for bucket in expected_buckets if bucket not in observed_buckets
        ],
        "unknown_buckets": sorted(
            {
                str(bucket).strip().upper()
                for bucket in data
                if str(bucket).strip().upper() not in HS_BUCKET_TO_CLASS_KEY
            }
        ),
        "duplicate_bucket_package_keys": sorted(duplicate_bucket_package_keys),
    }


def _package_row_retrieval(
    data: dict[str, Any],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    unique_package_keys: set[str] = set()
    invalid_rows = 0
    for bucket, rows in data.items():
        if str(bucket).strip().upper() not in HS_BUCKET_TO_CLASS_KEY or not isinstance(rows, list):
            continue
        for package in rows:
            if not isinstance(package, dict):
                invalid_rows += 1
                continue
            key = str(package.get("package_key_card_id") or "").strip()
            if key:
                unique_package_keys.add(key)
            else:
                invalid_rows += 1
    normalizer_rejected = max(0, len(unique_package_keys) - len(groups))
    evidence = row_retrieval_evidence(
        raw_rows=len(unique_package_keys) + invalid_rows,
        eligible_rows=len(unique_package_keys),
        normalized_rows=len(groups),
        unexplained_reasons={
            "invalid_or_missing_package_key": invalid_rows,
            "normalizer_rejected": normalizer_rejected,
        },
        scope="unique_package_keys_across_class_buckets",
    )
    evidence["bucket_coverage"] = _bucket_coverage(data)
    return evidence


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
    raw_cards = raw.get("cards")
    if not isinstance(raw_cards, list) or not raw_cards:
        return None
    if any(not isinstance(row, dict) for row in raw_cards):
        return None
    cards_raw = raw_cards
    cards: list[dict[str, Any]] = []
    for row in cards_raw:
        count = row.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            return None
        name = str(row.get("name") or "").strip()
        resolved = resolve_card_name(name) if name else {}
        card_id = resolved.get("card_id") or resolved.get("id") or row.get("card_id")
        if not card_id:
            return None
        cards.append(
            {
                "count": count,
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

    winrate = _format_pct(_first_non_none(raw, "winrate", "win_rate"))
    pick_rate = _format_pct(_first_non_none(raw, "pick_rate", "pickRate"))
    score = _as_number(_first_non_none(raw, "score", "arenasmith_score"))
    metrics = {
        "winrate": winrate,
        "pick_rate": pick_rate,
        "offer_rate": _format_pct(
            _first_non_none(raw, "offer_rate", "offerRate")
        ),
        "score": score,
        "field_availability": _legendary_field_availability(
            winrate=winrate,
            pick_rate=pick_rate,
            score=score,
        ),
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
    row_retrieval: dict[str, Any]
    parse_mode = "embedded_json"
    if packages:
        # Firecrawl usually captures a flat list; treat as ALL.
        fake_data = {"ALL": packages}
        groups = _groups_from_class_buckets(fake_data, locale=locale)
        row_retrieval = _package_row_retrieval(fake_data, groups)
    else:
        parse_mode = "page_text"
        parsed = parse_legendary_groups(_lines_from_firecrawl(scraped.markdown, scraped.html))
        for raw in parsed:
            group = _normalize_firecrawl_group(raw, locale=locale)
            if group:
                groups.append(group)
        row_retrieval = row_retrieval_evidence(
            raw_rows=len(parsed),
            eligible_rows=len(parsed),
            normalized_rows=len(groups),
            unexplained_reasons={
                "normalizer_rejected": len(parsed) - len(groups),
            },
            scope="firecrawl_page_groups",
        )
        row_retrieval["bucket_coverage"] = _bucket_coverage({"ALL": parsed})

    if len(groups) < 10:
        raise RuntimeError(
            f"Firecrawl legendaries fallback produced too few groups ({len(groups)}); mode={parse_mode}"
        )

    # HTML fallback does not prove the card-package representation period, so
    # mixing any secondary stats into it would create unverifiable provenance.
    stats_index: dict[str, dict[str, Any]] = {}
    stats_backend = "none"
    enrich_stats = enrich_legendary_groups(groups, stats_index)

    return {
        "type": "arena_legendary_groups",
        "completeness_schema_version": COMPLETENESS_SCHEMA_VERSION,
        "upstream_freshness": build_hsreplay_transport_evidence_unavailable(),
        "population_completeness": "unverifiable",
        "row_retrieval": row_retrieval,
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
    row_retrieval: dict[str, Any] | None = None
    backend = "hsreplay_api"
    api_payload: dict[str, Any] = {}

    try:
        last_error: Exception | None = None
        for url in (LEGENDARIES_API_URL, LEGENDARIES_API_URL_FREE):
            try:
                candidate = await fetch_hsreplay_json(url, source_id=source_id)
                data = _payload_data(candidate if isinstance(candidate, dict) else {})
                built = _groups_from_class_buckets(data, locale=locale)
                if len(built) >= 10:
                    groups = built
                    row_retrieval = _package_row_retrieval(data, groups)
                    api_url = url
                    api_payload = candidate
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

    upstream_freshness = build_hsreplay_arena_upstream_freshness(
        api_payload,
        response_headers=get_hsreplay_json_target_headers(api_url),
    )
    expected_meta_period_id = (
        upstream_freshness.get("meta_period_id")
        if upstream_freshness.get("status") == "fresh"
        else None
    )
    # Only enrich missing ALL metrics from a separately verified live card
    # snapshot in the exact same Arena meta period.
    stats_index, stats_backend = await _load_arena_card_stats_index(
        source_id,
        expected_meta_period_id=(
            expected_meta_period_id
            if isinstance(expected_meta_period_id, int)
            and not isinstance(expected_meta_period_id, bool)
            else None
        ),
    )
    enrich_stats = enrich_legendary_groups(groups, stats_index)
    class_bucket_count = max((len(g.get("by_class") or {}) for g in groups), default=0)

    return {
        "type": "arena_legendary_groups",
        "completeness_schema_version": COMPLETENESS_SCHEMA_VERSION,
        "upstream_freshness": upstream_freshness,
        "population_completeness": "unverifiable",
        "row_retrieval": row_retrieval
        or row_retrieval_evidence(
            raw_rows=len(groups),
            eligible_rows=len(groups),
            normalized_rows=len(groups),
            scope="normalized_groups",
        ),
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
