from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import (
    fetch_direct_enabled,
    fetch_proxy_url,
    fetch_require_proxy,
    flaresolverr_url,
    refresh_preflight_probe_hsreplay,
    refresh_preflight_strict,
)
from .fetch_routes import (
    source_can_run_without_residential_proxy,
    source_has_cloud_html_route,
    source_local_route_requires_flaresolverr,
)
from .refresh_log import log_action
from .scrapers.proxy import check_proxy_health
from .source_tiers import SourceTier, tier_for
from .sources import Source

logger = logging.getLogger(__name__)

HSREPLAY_PROBE_URL = "https://hsreplay.net/api/v1/arena/card_stats/"


def selection_needs_proxy_preflight(
    selected: list[Source],
    *,
    configured_backends: list[str],
) -> bool:
    """Return whether every safe route for this selection needs the proxy."""
    if not fetch_require_proxy() or fetch_direct_enabled():
        return False

    proxy_only_sources = [
        source
        for source in selected
        if tier_for(source.id)
        in {SourceTier.BROWSER_PATCHRIGHT, SourceTier.BROWSER_PROTECTED}
        and not source_can_run_without_residential_proxy(
            source,
            default_backends=configured_backends,
        )
    ]
    if not proxy_only_sources:
        return False

    independent_sources = [
        source for source in selected if source not in proxy_only_sources
    ]
    return not independent_sources


def selection_needs_flaresolverr_preflight(
    selected: list[Source],
    *,
    configured_backends: list[str],
) -> bool:
    """Gate only selections whose useful routes all need local FlareSolverr."""
    if fetch_direct_enabled():
        return False
    flaresolverr_only_sources = [
        source
        for source in selected
        if tier_for(source.id)
        in {SourceTier.BROWSER_PATCHRIGHT, SourceTier.BROWSER_PROTECTED}
        and not source_has_cloud_html_route(source)
        and source_local_route_requires_flaresolverr(
            source,
            default_backends=configured_backends,
        )
    ]
    if not flaresolverr_only_sources:
        return False

    independent_sources = [
        source for source in selected if source not in flaresolverr_only_sources
    ]
    return not independent_sources


@dataclass
class PreflightResult:
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    proxy_info: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": self.checks,
            "warnings": self.warnings,
            "errors": self.errors,
            "proxy_info": self.proxy_info,
        }


async def check_flaresolverr(*, probe_functional: bool = True) -> dict[str, Any]:
    """Check that FlareSolverr is reachable and (optionally) can perform a simple fetch."""
    url = flaresolverr_url().rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json={"cmd": "sessions.list"})
            response.raise_for_status()
            body = response.json()
            if body.get("status") != "ok":
                return {"ok": False, "detail": body.get("message") or "status not ok"}
            result: dict[str, Any] = {
                "ok": True,
                "version": body.get("version"),
                "sessions": len(body.get("sessions") or []),
            }
            if not probe_functional:
                return result

            # Lightweight functional probe inside the same client context:
            # ask FS to fetch a tiny public endpoint (no proxy) to verify the solver can actually perform fetches.
            probe_payload = {
                "cmd": "request.get",
                "url": "https://api.ipify.org?format=json",
                "maxTimeout": 15000,
            }
            try:
                probe_resp = await client.post(url, json=probe_payload, timeout=20.0)
                probe_resp.raise_for_status()
                probe_body = probe_resp.json()
                if probe_body.get("status") == "ok":
                    sol = probe_body.get("solution") or {}
                    if int(sol.get("status") or 0) == 200 and "ip" in (sol.get("response") or ""):
                        result["functional"] = True
                    else:
                        result["functional"] = False
                        result["functional_detail"] = "probe returned non-200 or no ip"
                else:
                    result["functional"] = False
                    result["functional_detail"] = probe_body.get("message") or "probe status not ok"
            except Exception as probe_exc:
                result["functional"] = False
                result["functional_detail"] = str(probe_exc)[:200]
            return result
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:500]}


async def probe_hsreplay_api() -> dict[str, Any]:
    """Lightweight HSReplay JSON probe using configured channels."""
    from .config import hsreplay_json_channels
    from .hsreplay_client import (
        extract_json_payload,
        fetch_text_via_curl_cffi,
        fetch_text_via_flaresolverr,
        jina_url,
    )

    errors: list[str] = []
    headers = {"User-Agent": "Hearthstone-Parses-Preflight/1.0", "Accept": "application/json,*/*"}

    async def try_direct() -> bool:
        try:
            kwargs: dict[str, Any] = {"timeout": 25.0, "follow_redirects": True}
            if fetch_require_proxy() and not fetch_direct_enabled():
                from .scrapers.proxy import proxy_url_for_source

                proxy = proxy_url_for_source("preflight_hsreplay")
                if proxy:
                    kwargs["proxy"] = proxy
            async with httpx.AsyncClient(headers=headers, **kwargs) as client:
                response = await client.get(HSREPLAY_PROBE_URL)
                if response.status_code == 403:
                    errors.append("direct: 403")
                    return False
                response.raise_for_status()
                payload = extract_json_payload(response.text)
                return isinstance(payload, (dict, list))
        except Exception as exc:
            errors.append(f"direct: {exc}")
        return False

    async def try_channel(label: str) -> bool:
        try:
            if label == "direct":
                return await try_direct()
            if label == "jina":
                fetch_url = jina_url(HSREPLAY_PROBE_URL)
                async with httpx.AsyncClient(headers=headers, timeout=25.0) as client:
                    response = await client.get(fetch_url)
                    response.raise_for_status()
                    body = response.text
            elif label == "flaresolverr":
                body = await fetch_text_via_flaresolverr(
                    HSREPLAY_PROBE_URL, source_id="preflight_hsreplay"
                )
            elif label == "curl_cffi":
                body = await fetch_text_via_curl_cffi(
                    HSREPLAY_PROBE_URL, source_id="preflight_hsreplay"
                )
            else:
                return False
            payload = extract_json_payload(body)
            if isinstance(payload, (dict, list)):
                return True
            errors.append(f"{label}: not json")
        except Exception as exc:
            errors.append(f"{label}: {exc}")
        return False

    for label in hsreplay_json_channels():
        if await try_channel(label):
            return {"ok": True, "channel": label}
    return {"ok": False, "detail": "; ".join(errors[:6])}


async def _telegram_preflight_alert(state: str, detail: str) -> None:
    try:
        from .fetcher import send_telegram_alert

        await send_telegram_alert("_preflight", state, detail, "https://hsreplay.net/")
    except Exception as exc:
        logger.warning("Preflight Telegram alert failed: %s", exc)


async def run_refresh_preflight(
    *,
    needs_proxy: bool,
    needs_flaresolverr: bool,
) -> PreflightResult:
    result = PreflightResult(ok=True)

    log_action("preflight.begin", extra={"needs_proxy": needs_proxy, "needs_flaresolverr": needs_flaresolverr})

    if needs_proxy:
        if not fetch_require_proxy():
            result.checks.append({"name": "proxy", "ok": True, "skipped": True, "detail": "proxy not required"})
            log_action("preflight.proxy.skip", extra={"reason": "proxy not required"})
        elif fetch_require_proxy() and not fetch_proxy_url():
            result.ok = False
            result.errors.append("HS_FETCH_PROXY_URL is not set")
            result.checks.append({"name": "proxy", "ok": False, "detail": "missing proxy url"})
        else:
            try:
                result.proxy_info = await check_proxy_health()
                result.checks.append(
                    {
                        "name": "proxy",
                        "ok": True,
                        "egress_ip": result.proxy_info.get("egress_ip"),
                        "rotation_ok": result.proxy_info.get("rotation_ok"),
                    }
                )
            except Exception as exc:
                result.ok = False
                msg = f"proxy health failed: {exc}"
                result.errors.append(msg)
                result.checks.append({"name": "proxy", "ok": False, "detail": str(exc)[:500]})
                log_action("preflight.proxy.fail", level="error", detail=msg)

    if needs_flaresolverr:
        fs = await check_flaresolverr(probe_functional=True)
        result.checks.append({"name": "flaresolverr", **fs})
        functional_failed = fs.get("ok") and fs.get("functional") is False
        if not fs.get("ok") or functional_failed:
            result.ok = False
            detail_fs = str(fs.get("detail") or fs.get("functional_detail") or "functional probe failed")[:500]
            result.errors.append(f"flaresolverr: {detail_fs}")
            log_action("preflight.flaresolverr.fail", level="error", detail=detail_fs)
            await _telegram_preflight_alert(
                "flaresolverr_down",
                detail_fs or "FlareSolverr check failed",
            )
        else:
            log_action("preflight.flaresolverr.ok", extra={"version": fs.get("version")})

    if refresh_preflight_probe_hsreplay() and needs_proxy:
        probe = await probe_hsreplay_api()
        result.checks.append({"name": "hsreplay_api", **probe})
        if not probe.get("ok"):
            result.warnings.append(f"HSReplay API probe failed: {probe.get('detail')}")
            log_action(
                "preflight.hsreplay.warn",
                level="warn",
                detail=str(probe.get("detail"))[:500],
            )
        else:
            log_action(
                "preflight.hsreplay.ok",
                extra={"channel": probe.get("channel")},
            )

    if result.ok:
        log_action("preflight.ok", extra={"checks": len(result.checks)})
    else:
        log_action("preflight.fail", level="error", detail="; ".join(result.errors)[:500])

    return result


async def ensure_refresh_preflight(
    *,
    full_refresh: bool,
    needs_proxy: bool,
    needs_flaresolverr: bool,
) -> dict[str, str]:
    """
    Run preflight before refresh. Returns proxy_info dict (may be empty).
    Raises if strict mode and preflight failed.
    """
    if not needs_proxy and not needs_flaresolverr:
        return {}

    if not full_refresh and needs_proxy and not needs_flaresolverr:
        try:
            return await check_proxy_health()
        except Exception as exc:
            if refresh_preflight_strict():
                raise RuntimeError(f"Proxy healthcheck failed: {exc}") from exc
            logger.warning("Proxy healthcheck failed (continuing): %s", exc)
            return {}

    pf = await run_refresh_preflight(needs_proxy=needs_proxy, needs_flaresolverr=needs_flaresolverr)
    if not pf.ok and refresh_preflight_strict():
        raise RuntimeError(
            "Refresh preflight failed (HS_REFRESH_PREFLIGHT_STRICT=true): "
            + "; ".join(pf.errors)
        )
    for warning in pf.warnings:
        logger.warning("Preflight warning: %s", warning)
    return pf.proxy_info
