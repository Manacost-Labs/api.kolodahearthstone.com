from __future__ import annotations

import logging
import random

import httpx

from ..config import (
    flaresolverr_hsguru_wait_ms,
    flaresolverr_url,
    request_timeout_seconds,
)
from ..hsreplay_auth import hsreplay_cookies_for_fetch
from ..sources import Source
from .base import FetchResult
from .flaresolverr_session import FlareSolverrSession, flaresolverr_request_timeout_ms
from .proxy import (
    assert_proxy_configured,
    proxy_dict_for_flaresolverr,
    source_can_use_flaresolverr_without_proxy,
)

logger = logging.getLogger(__name__)

# source_id passed via module-level for current fetch (set by fetcher)
_current_source_id: str | None = None


def set_flaresolverr_source(source_id: str | None) -> None:
    global _current_source_id
    _current_source_id = source_id


_active_session: FlareSolverrSession | None = None


def set_active_flaresolverr_session(session: FlareSolverrSession | None) -> None:
    global _active_session
    _active_session = session


async def fetch_via_flaresolverr(
    source: Source,
    *,
    wait_ms: int | None = None,
) -> FetchResult:
    if not source_can_use_flaresolverr_without_proxy(source):
        assert_proxy_configured()
    payload: dict = {
        "cmd": "request.get",
        "url": source.url,
        "maxTimeout": flaresolverr_request_timeout_ms(),
    }
    if _active_session and _active_session.session_id:
        payload["session"] = _active_session.session_id
    proxy = proxy_dict_for_flaresolverr(_current_source_id, page_url=source.url)
    if proxy:
        payload["proxy"] = proxy
    if source.site == "hsreplay":
        cookies = hsreplay_cookies_for_fetch()
        if cookies:
            payload["cookies"] = cookies
    if source.site == "hsguru":
        effective_wait_ms = (
            flaresolverr_hsguru_wait_ms() if wait_ms is None else max(0, wait_ms)
        )
        if effective_wait_ms > 0:
            payload["wait"] = effective_wait_ms

    timeout = httpx.Timeout(request_timeout_seconds() + 30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(flaresolverr_url(), json=payload)
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "ok":
            message = body.get("message") or str(body)
            raise RuntimeError(f"FlareSolverr error: {message}")

        solution = body.get("solution") or {}
        html = solution.get("response") or ""
        final_url = solution.get("url") or source.url
        status = int(solution.get("status") or 200)
        if not html.strip():
            raise RuntimeError("FlareSolverr returned empty HTML")

        # HSGuru-specific post-fetch settle for SPA tables.
        # If the snapshot we got has the chrome but very few table rows (common when wait was marginal),
        # give the page a little more time on the FS side by re-requesting once with the same (or slightly higher) wait.
        # This is cheap because browser_protected is serial, and only triggers on actual partial responses.
        if source.site == "hsguru":
            import re

            rough_rows = len(re.findall(r"<tr\b", html, re.I))
            is_meta_like = source.category in {"meta", "matchups", "streamer_decks"}
            if is_meta_like and rough_rows < 8 and len(html) > 5000:
                # partial render: wait a bit and ask FS for a fresher snapshot.
                import asyncio

                await asyncio.sleep(random.uniform(6.0, 10.0))
                base_wait = flaresolverr_hsguru_wait_ms()
                retry_payload = dict(payload)
                retry_payload["wait"] = max(
                    int(retry_payload.get("wait") or 0), base_wait + 15000
                )
                try:
                    retry_resp = await client.post(
                        flaresolverr_url(), json=retry_payload
                    )
                    retry_resp.raise_for_status()
                    retry_body = retry_resp.json()
                    if retry_body.get("status") == "ok":
                        rsol = retry_body.get("solution") or {}
                        rhtml = rsol.get("response") or ""
                        rstatus = int(rsol.get("status") or 200)
                        if (
                            len(rhtml) > len(html)
                            or len(re.findall(r"<tr\b", rhtml, re.I)) > rough_rows
                        ):
                            html, final_url, status = (
                                rhtml,
                                rsol.get("url") or final_url,
                                rstatus,
                            )
                    else:
                        logger.warning(
                            "FlareSolverr HSGuru settle retry returned non-ok for %s: %s",
                            source.id,
                            retry_body.get("message") or retry_body,
                        )
                except Exception as exc:
                    logger.warning(
                        "FlareSolverr HSGuru settle retry failed for %s: %s",
                        source.id,
                        exc,
                    )

    return FetchResult(
        html=html,
        final_url=final_url,
        backend="flaresolverr",
        http_status=status,
    )
