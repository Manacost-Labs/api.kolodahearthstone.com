from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import re
import secrets
from urllib.parse import urlparse, urlunparse

import httpx

from ..config import (
    fetch_proxy_url,
    fetch_require_proxy,
    iproyal_rotate_per_fetch,
    iproyal_session_lifetime,
    iproyal_session_per_source,
    proxy_check_url,
    proxy_sticky_mode,
)
from ..proxy_errors import proxy_tunnel_error
from ..refresh_log import log_action

logger = logging.getLogger(__name__)

# geo.iproyal.com + regional gateways (docs.iproyal.com)
_IPROYAL_HOST_SUFFIXES = (
    "geo.iproyal.com",
    "proxy.iproyal.com",
    "us.proxy.iproyal.com",
    "sg.proxy.iproyal.com",
)

# FIX: burned sticky session keys → next fetch gets a fresh IPRoyal session suffix
_BURNED_SESSION_KEYS: set[str] = set()
_SESSION_ROTATION_SUFFIX: dict[str, int] = {}


def _is_iproyal(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return host.endswith("iproyal.com") or host in _IPROYAL_HOST_SUFFIXES


def _iproyal_base_password(password: str) -> str:
    """
    IPRoyal puts targeting in PASSWORD: basepass_country-us_session-abc12345_lifetime-30m.
    Strip prior session/lifetime so we can rotate sticky IP.
    """
    if "_session-" in password:
        return password.split("_session-", 1)[0]
    return password


def _iproyal_session_token(session_key: str) -> str:
    """IPRoyal session id: 8 alphanumeric chars (docs)."""
    return hashlib.md5(session_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


def _iproyal_url_with_session(base: str, session_key: str) -> str:
    """
    IPRoyal sticky session belongs in PASSWORD, not username.
    See https://docs.iproyal.com/proxies/residential/proxy/making-requests
    """
    parsed = urlparse(base)
    username = parsed.username or ""
    password = parsed.password or ""
    if not username or not password:
        return base
    base_pass = _iproyal_base_password(password)
    token = _iproyal_session_token(session_key)
    lifetime = iproyal_session_lifetime()
    session_pass = f"{base_pass}_session-{token}_lifetime-{lifetime}"
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{username}:{session_pass}@{host}{port}"
    return urlunparse(
        (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def _domain_session_key(source_id: str | None, page_url: str | None) -> str:
    """FIX: sticky IP per domain, not per request."""
    if page_url:
        host = (urlparse(page_url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            return re.sub(r"[^a-zA-Z0-9._-]", "_", host)[:48]
    if source_id:
        if source_id.startswith("hsguru_"):
            return "hsguru.com"
        if source_id.startswith("hsreplay_"):
            return "hsreplay.net"
        if source_id.startswith("firestone_"):
            return "firestone.gg"
    return "default"


def proxy_session_key(
    source_id: str | None = None,
    *,
    page_url: str | None = None,
) -> str:
    mode = proxy_sticky_mode()
    if iproyal_rotate_per_fetch() or mode == "rotate":
        return secrets.token_hex(8)
    if iproyal_session_per_source() or mode == "source":
        if source_id:
            return re.sub(r"[^a-zA-Z0-9_-]", "_", source_id)[:48]
        return "default"
    base = _domain_session_key(source_id, page_url)
    if base in _BURNED_SESSION_KEYS:
        rotation = _SESSION_ROTATION_SUFFIX.get(base, 0) + 1
        _SESSION_ROTATION_SUFFIX[base] = rotation
        return f"{base}_burn{rotation}"
    return base


def burn_proxy_session(
    source_id: str | None = None,
    *,
    page_url: str | None = None,
    reason: str | None = None,
) -> None:
    """FIX: blacklist current sticky session after 403/401/429/WAF — forces new IP on retry."""
    key = _domain_session_key(source_id, page_url)
    if iproyal_session_per_source() or proxy_sticky_mode() == "source":
        if source_id:
            key = re.sub(r"[^a-zA-Z0-9_-]", "_", source_id)[:48]
    _BURNED_SESSION_KEYS.add(key)
    logger.warning(
        "Burned proxy session key=%s reason=%s (next fetch rotates IP)",
        key,
        reason or "blocked",
    )
    log_action(
        "proxy.session.burn",
        source_id=source_id,
        detail=reason or "blocked",
        extra={"session_key": key},
        level="warn",
    )


def proxy_url_for_source(
    source_id: str | None = None,
    *,
    fetch_id: str | None = None,
    page_url: str | None = None,
) -> str | None:
    """
    Resolve proxy URL for a fetch.

    FIX: default sticky session per domain (not random IP per connection).
    IPRoyal modes:
    - HS_PROXY_STICKY_MODE=domain (default): hsguru.com / hsreplay.net keep one IP.
    - HS_IPROYAL_SESSION_PER_SOURCE=true: sticky per source_id.
    - HS_IPROYAL_ROTATE_PER_FETCH=true: unique session per call (avoid in production).
    """
    base = fetch_proxy_url()
    if not base or not _is_iproyal(base):
        return base
    if fetch_id or iproyal_rotate_per_fetch():
        key = fetch_id or secrets.token_hex(8)
        return _iproyal_url_with_session(base, key)
    session_key = proxy_session_key(source_id, page_url=page_url)
    return _iproyal_url_with_session(base, session_key)


def httpx_client_kwargs(
    source_id: str | None = None,
    *,
    timeout: float = 45.0,
    fetch_id: str | None = None,
    page_url: str | None = None,
) -> dict:
    """httpx.AsyncClient kwargs: sticky proxy + no keep-alive pool reuse across domains."""
    proxy = proxy_url_for_source(source_id, fetch_id=fetch_id, page_url=page_url)
    kwargs: dict = {
        "timeout": timeout,
        "follow_redirects": True,
        # FIX: disable keep-alive pool so one client == one egress path, but session key stays sticky
        "limits": httpx.Limits(max_connections=4, max_keepalive_connections=0),
    }
    if proxy:
        kwargs["proxy"] = proxy
    return kwargs


def flaresolverr_skip_proxy(source_id: str | None = None, *, page_url: str | None = None) -> bool:
    """
    Local FlareSolverr often fails through residential proxy (Chrome error / HTTP 500).
    HSReplay JSON and HSGuru pages work with the solver's own egress + cf_clearance.
    """
    if page_url:
        host = (urlparse(page_url).hostname or "").lower()
        if host.endswith("hsreplay.net"):
            return True
        if host.endswith("hsguru.com"):
            return True
    if source_id:
        if source_id.startswith("hsguru_"):
            return True
        if source_id.startswith("hsreplay_"):
            return True
    return False


def source_can_use_flaresolverr_without_proxy(source: object) -> bool:
    """True when the source has a FlareSolverr path that intentionally skips proxy."""
    source_id = getattr(source, "id", None)
    page_url = getattr(source, "url", None) or getattr(source, "fetch_url", None)
    return flaresolverr_skip_proxy(source_id, page_url=page_url)


def browser_backend_uses_residential_proxy(source: object, backend: str) -> bool:
    """Return whether this exact local browser route receives the paid proxy.

    This is deliberately narrower than asking whether a proxy is configured:
    HSReplay/HSGuru FlareSolverr and Patchright routes are explicitly direct in
    the current transport policy, while the other local backends receive the
    residential proxy URL.
    """

    if not fetch_proxy_url():
        return False
    normalized = backend.strip().lower()
    source_id = getattr(source, "id", None)
    page_url = getattr(source, "fetch_url", None) or getattr(source, "url", None)
    if normalized in {"flaresolverr", "patchright", "playwright"}:
        return not flaresolverr_skip_proxy(source_id, page_url=page_url)
    return normalized in {
        "camoufox",
        "cloakbrowser",
        "cloudflare_scrape",
        "cloudscraper",
        "curl_cffi",
        "scrapling",
    }


def proxy_dict_for_flaresolverr(
    source_id: str | None = None,
    *,
    page_url: str | None = None,
) -> dict[str, str] | None:
    if flaresolverr_skip_proxy(source_id, page_url=page_url):
        return None
    url = proxy_url_for_source(source_id, page_url=page_url)
    if not url:
        return None
    return {"url": url}


def residential_proxy_url(
    source_id: str | None = None,
    *,
    page_url: str | None = None,
) -> str | None:
    """Residential proxy URL for stealth browsers (CloakBrowser, Scrapling)."""
    return proxy_url_for_source(source_id, page_url=page_url)


def residential_playwright_proxy(
    source_id: str | None = None,
    *,
    page_url: str | None = None,
) -> dict[str, str] | None:
    url = residential_proxy_url(source_id, page_url=page_url)
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.hostname:
        return None
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    out: dict[str, str] = {"server": server}
    if parsed.username:
        out["username"] = parsed.username
    if parsed.password:
        out["password"] = parsed.password
    return out


def playwright_proxy(
    source_id: str | None = None,
    *,
    page_url: str | None = None,
) -> dict[str, str] | None:
    if flaresolverr_skip_proxy(source_id, page_url=page_url):
        return None
    url = proxy_url_for_source(source_id, page_url=page_url)
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.hostname:
        return {"server": url}
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    server = f"{parsed.scheme}://{parsed.hostname}:{port}"
    out: dict[str, str] = {"server": server}
    if parsed.username:
        out["username"] = parsed.username
        out["password"] = parsed.password or ""
    return out


def cloudscraper_proxies(
    source_id: str | None = None,
    *,
    page_url: str | None = None,
) -> dict[str, str] | None:
    url = proxy_url_for_source(source_id, page_url=page_url)
    if not url:
        return None
    return {"http": url, "https": url}


def assert_proxy_configured() -> None:
    if fetch_require_proxy() and not fetch_proxy_url():
        raise RuntimeError(
            "HS_FETCH_PROXY_URL is required (HS_FETCH_REQUIRE_PROXY=true). "
            "Configure a residential proxy so the origin never sees this server's IP."
        )


async def check_proxy_rotation(samples: int = 5) -> dict[str, object]:
    """Sample egress IPs via proxy (different session keys) to verify rotation."""
    assert_proxy_configured()
    ips: list[str] = []
    errors: list[str] = []
    for i in range(max(1, samples)):
        url = proxy_url_for_source(f"rotation_test_{i}", fetch_id=secrets.token_hex(6))
        if not url:
            break
        try:
            async with httpx.AsyncClient(
                proxy=url,
                timeout=30.0,
                limits=httpx.Limits(max_keepalive_connections=0),
            ) as client:
                response = await client.get(proxy_check_url())
                response.raise_for_status()
                ips.append(response.text.strip())
        except Exception as exc:
            typed_proxy_error = proxy_tunnel_error(exc, proxy_used=True)
            if typed_proxy_error is not None:
                if typed_proxy_error is exc:
                    raise
                raise typed_proxy_error from exc
            errors.append(f"sample_{i}: {exc}")
    unique = sorted(set(ips))
    return {
        "samples": len(ips),
        "unique_ips": len(unique),
        "ips": unique[:20],
        "rotating": len(unique) > 1,
        "errors": errors[:5],
    }


async def check_proxy_health() -> dict[str, str]:
    assert_proxy_configured()
    url = proxy_url_for_source("healthcheck", page_url=proxy_check_url())
    if not url:
        raise RuntimeError("HS_FETCH_PROXY_URL is empty")

    log_action("proxy.health.begin", url=proxy_check_url())
    ip = None
    last_err = None
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(
                proxy=url,
                timeout=30.0,
                limits=httpx.Limits(max_keepalive_connections=0),
            ) as client:
                response = await client.get(proxy_check_url())
                response.raise_for_status()
                ip = response.text.strip()
                log_action(
                    "proxy.health.ok",
                    attempt=attempt + 1,
                    detail=ip,
                    http_status=response.status_code,
                )
                break
        except Exception as exc:
            typed_proxy_error = proxy_tunnel_error(exc, proxy_used=True)
            if typed_proxy_error is not None:
                log_action(
                    "proxy.health.fail",
                    attempt=attempt + 1,
                    error_type=type(typed_proxy_error).__name__,
                    detail=str(typed_proxy_error)[:500],
                    level="error",
                    extra={"proxy_status": typed_proxy_error.status_code},
                )
                if typed_proxy_error is exc:
                    raise
                raise typed_proxy_error from exc
            last_err = exc
            logger.warning("Proxy healthcheck attempt %d/5 failed: %s. Retrying...", attempt + 1, exc)
            log_action(
                "proxy.health.fail",
                attempt=attempt + 1,
                error_type=type(exc).__name__,
                detail=str(exc)[:500],
                level="warn",
            )
            await asyncio.sleep(random.uniform(0.5, 1.5))

    if not ip:
        log_action(
            "proxy.health.fail",
            level="error",
            detail=f"failed after 5 attempts: {last_err}",
        )
        raise RuntimeError(f"Proxy healthcheck failed after 5 attempts. Last error: {last_err}")

    rotation = await check_proxy_rotation(3)
    return {
        "proxy_url_host": urlparse(url).hostname or "",
        "egress_ip": ip,
        "session_key": proxy_session_key("healthcheck"),
        "sticky_mode": proxy_sticky_mode(),
        "rotation_unique_ips": str(rotation.get("unique_ips", 0)),
        "rotation_ok": str(rotation.get("rotating", False)),
    }
