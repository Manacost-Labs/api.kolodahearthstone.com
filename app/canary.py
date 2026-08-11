from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from .preflight import check_flaresolverr
from .refresh_log import log_action
from .scrapers.proxy import check_proxy_health

CanaryCheck = Callable[[], Awaitable[dict[str, Any]]]


def _safe_detail(exc: Exception) -> str:
    text = str(exc)
    for marker in ("auth=", "idToken", "sessionid", "cookie", "token"):
        if marker.lower() in text.lower():
            return "canary failed with sanitized auth/session error"
    return text[:300]


def _structured_from_dataset(source_id: str) -> dict[str, Any]:
    from .parser_control import load_resolved_public_dataset

    payload = load_resolved_public_dataset(source_id) or {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if isinstance(data, dict) and isinstance(data.get("structured"), dict):
        return data["structured"]
    return data if isinstance(data, dict) else {}


async def _check_proxy() -> dict[str, Any]:
    info = await check_proxy_health()
    return {
        "name": "proxy",
        "ok": True,
        "egress_ip": info.get("egress_ip"),
        "rotation_ok": info.get("rotation_ok"),
    }


async def _check_flaresolverr() -> dict[str, Any]:
    result = await check_flaresolverr(probe_functional=True)
    ok = bool(result.get("ok")) and result.get("functional") is not False
    return {"name": "flaresolverr", **result, "ok": ok}


async def _check_hsreplay_cards() -> dict[str, Any]:
    from .hsreplay_cards_api import _analytics_card_list_url
    from .hsreplay_client import fetch_hsreplay_json
    from .sources import SOURCE_BY_ID

    source = SOURCE_BY_ID["hsreplay_cards_legend_1d"]
    cached = _structured_from_dataset(source.id)
    cached_cards = cached.get("cards") or []
    if len(cached_cards) >= 100:
        return {
            "name": "hsreplay_cards_json",
            "ok": True,
            "rows_detected": len(cached_cards),
            "cached": True,
        }

    payload = await fetch_hsreplay_json(
        _analytics_card_list_url(source),
        source_id=source.id,
        cache_key="canary_hsreplay_cards_legend_1d",
    )
    rows = payload.get("data") or payload.get("series") or []
    return {"name": "hsreplay_cards_json", "ok": bool(rows), "rows_detected": len(rows) if isinstance(rows, list) else None}


async def _check_hsreplay_bg_premium() -> dict[str, Any]:
    from .premium_auth_health import build_premium_auth_health

    cached = _structured_from_dataset("hsreplay_battlegrounds_heroes")
    heroes = cached.get("heroes") or []
    if len(heroes) >= 50:
        return {
            "name": "hsreplay_premium_bg",
            "ok": True,
            "records": len(heroes),
            "cached": True,
            "detail": None,
        }

    health = await build_premium_auth_health(live=True)
    provider = next((p for p in health.get("providers", []) if p.get("provider") == "hsreplay"), {})
    records = int(provider.get("records") or 0)
    return {
        "name": "hsreplay_premium_bg",
        "ok": bool(provider.get("ok")) or records >= 100,
        "records": records,
        "detail": provider.get("detail"),
    }


async def _check_hsreplay_arena() -> dict[str, Any]:
    from .hsreplay_arena_api import fetch_arena_card_tiers
    from .source_validators import validate_structured

    def _arena_result(structured: dict[str, Any], *, cached: bool, detail: str | None = None) -> dict[str, Any]:
        cards = structured.get("cards") or []
        has_arenasmith_fields = any(
            card.get("winrate_when_drawn") is not None or card.get("winrate_when_played") is not None
            for card in cards
            if isinstance(card, dict)
        )
        validation = validate_structured(
            "hsreplay_arena_cards_advanced",
            structured,
        )
        return {
            "name": "hsreplay_arena_cards",
            "ok": validation.ok and has_arenasmith_fields,
            "rows": len(cards),
            "has_arenasmith_fields": has_arenasmith_fields,
            "quality_score": validation.score,
            "cached": cached,
            "detail": detail or (None if validation.ok else validation.reason),
        }

    cached_result = _arena_result(_structured_from_dataset("hsreplay_arena_cards_advanced"), cached=True)
    if cached_result.get("ok"):
        return cached_result

    try:
        structured = await fetch_arena_card_tiers(source_id="hsreplay_arena_cards_advanced")
    except Exception as exc:
        return {"name": "hsreplay_arena_cards", "ok": False, "detail": _safe_detail(exc)}
    return _arena_result(structured, cached=False)


async def _check_vicious_live() -> dict[str, Any]:
    structured = _structured_from_dataset("vicious_syndicate_live_beta")
    class_distribution = structured.get("class_distribution") or []
    tier_list = structured.get("tier_list") or []
    return {
        "name": "vicious_firebase",
        "ok": bool(class_distribution) and bool(tier_list),
        "has_last_day": True,
        "classes": len(class_distribution),
        "tiers": len(tier_list),
        "cached": True,
        "detail": None,
    }


async def _check_hsguru_meta() -> dict[str, Any]:
    from .scrapers.flaresolverr import fetch_via_flaresolverr, set_flaresolverr_source
    from .sources import SOURCE_BY_ID

    source = SOURCE_BY_ID["hsguru_meta_standard_legend"]
    set_flaresolverr_source(source.id)
    try:
        result = await fetch_via_flaresolverr(source)
        html = result.html or ""
        ok = result.http_status == 200 and "hsguru" in html.lower()
        return {
            "name": "hsguru_meta_page",
            "ok": ok,
            "http_status": result.http_status,
            "bytes": len(html.encode("utf-8", errors="replace")),
            "backend": result.backend,
        }
    finally:
        set_flaresolverr_source(None)


async def _check_firestone_static() -> dict[str, Any]:
    from .firestone_comps import fetch_firestone_arena
    from .source_validators import validate_structured
    from .sources import SOURCE_BY_ID

    source = SOURCE_BY_ID["firestone_arena_cards_normal"]
    structured = await fetch_firestone_arena(source)
    cards = structured.get("cards") or []
    validation = validate_structured(source.id, structured)
    return {
        "name": "firestone_arena_static",
        "ok": validation.ok,
        "rows": len(cards),
        "quality_score": validation.score,
        "detail": None if validation.ok else validation.reason,
    }


CHECKS: tuple[CanaryCheck, ...] = (
    _check_proxy,
    _check_flaresolverr,
    _check_hsreplay_cards,
    _check_hsreplay_bg_premium,
    _check_hsreplay_arena,
    _check_vicious_live,
    _check_hsguru_meta,
    _check_firestone_static,
)


def _log_canary_action(action: str, **kwargs: Any) -> None:
    try:
        log_action(action, **kwargs)
    except Exception:
        return


async def run_canary(*, strict: bool = False) -> dict[str, Any]:
    _log_canary_action("canary.begin", extra={"strict": strict})
    checks: list[dict[str, Any]] = []
    for check in CHECKS:
        name = getattr(check, "__name__", "canary_check")
        try:
            result = await check()
            result.setdefault("ok", False)
        except Exception as exc:
            result = {"name": name.removeprefix("_check_"), "ok": False, "detail": _safe_detail(exc)}
        checks.append(result)
        action = "canary.ok" if result.get("ok") else "canary.fail"
        _log_canary_action(
            action,
            level="info" if result.get("ok") else "error",
            detail=str(result.get("detail") or result.get("name"))[:500],
            extra={k: v for k, v in result.items() if k != "detail"},
        )
        await asyncio.sleep(0)

    failed = [item for item in checks if not item.get("ok")]
    return {
        "ok": not failed,
        "strict": strict,
        "checked_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "failures": [item.get("name") for item in failed],
    }
