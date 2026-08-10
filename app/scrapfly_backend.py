from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass

from .config import scrapfly_timeout_seconds
from .scrapfly_keys import (
    acquire_scrapfly_key,
    is_scrapfly_credit_error,
    mark_scrapfly_key_exhausted,
    parse_scrapfly_api_keys,
    peek_scrapfly_key,
    record_scrapfly_credits,
)

SCRAPFLY_URL = "https://api.scrapfly.io/scrape"


@dataclass(frozen=True)
class ScrapflyScrape:
    html: str
    status_code: int
    final_url: str
    request_cost: int
    credits_remaining: int | None
    asp: bool
    render_js: bool
    screenshot: str | None = None
    key_label: str | None = None
    key_fingerprint: str | None = None
    key_rotation: dict | None = None

    @property
    def content_length(self) -> int:
        return len(self.html.encode("utf-8", errors="replace"))


def scrapfly_configured() -> bool:
    return peek_scrapfly_key() is not None or bool(parse_scrapfly_api_keys())


def _header_int(headers: Mapping[str, str], name: str) -> int | None:
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _scrape_once(
    url: str,
    *,
    api_key: str,
    render_js: bool = True,
    asp: bool = False,
    headers: Mapping[str, str] | None = None,
    wait_ms: int | None = None,
    timeout_ms: int | None = None,
    screenshot: bool = False,
    full_screenshot: bool = False,
) -> ScrapflyScrape:
    params: dict[str, str] = {
        "key": api_key,
        "url": url,
        "render_js": str(bool(render_js)).lower(),
        "asp": str(bool(asp)).lower(),
    }
    if wait_ms is not None and render_js:
        params["rendering_wait"] = str(max(0, int(wait_ms)))
    if timeout_ms is not None:
        # Scrapfly custom timeouts require retry=false and depend on features.
        use_js = bool(render_js or screenshot)
        if asp:
            minimum, maximum = 60_000, 150_000
        elif use_js:
            minimum, maximum = 30_000, 60_000
        else:
            minimum, maximum = 15_000, 30_000
        params["timeout"] = str(min(maximum, max(minimum, int(timeout_ms))))
        params["retry"] = "false"
    if screenshot:
        params["screenshots[main]"] = "fullpage" if full_screenshot else "viewport"
        params["render_js"] = "true"
    if headers:
        for name, value in headers.items():
            params[f"headers[{name}]"] = str(value)

    endpoint = f"{SCRAPFLY_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        endpoint,
        headers={
            "User-Agent": "HSDataAPI/0.1 Scrapfly fallback",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=scrapfly_timeout_seconds(),
        ) as response:
            raw = response.read()
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # exc.url contains the provider key and must never enter logs/errors.
        raise RuntimeError(f"Scrapfly HTTP {exc.code}: {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Scrapfly transport error: {exc.reason}") from exc

    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Scrapfly response is not valid JSON") from exc

    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise RuntimeError(  # noqa: TRY004 - malformed remote response
            "Scrapfly response did not include result"
        )
    if result.get("success") is False:
        error = result.get("error") or payload.get("error") or "scrape failed"
        raise RuntimeError(f"Scrapfly scrape failed: {error}")

    content = result.get("content")
    html = str(content) if isinstance(content, str) else ""
    image: str | None = None
    shots = result.get("screenshots")
    if isinstance(shots, dict) and shots:
        first = next(iter(shots.values()))
        if isinstance(first, dict):
            shot_url = first.get("url")
            if isinstance(shot_url, str) and shot_url.strip():
                image = shot_url.strip()
    if not html.strip() and not image:
        raise RuntimeError("Scrapfly returned an empty body")

    context = payload.get("context") if isinstance(payload, dict) else None
    cost_body = None
    if isinstance(context, dict):
        cost = context.get("cost")
        if isinstance(cost, dict) and cost.get("total") is not None:
            cost_body = cost.get("total")
        elif cost is not None:
            cost_body = cost

    request_cost = _header_int(response_headers, "x-scrapfly-api-cost")
    if request_cost is None:
        try:
            request_cost = int(cost_body) if cost_body is not None else None
        except (TypeError, ValueError):
            request_cost = None
    if request_cost is None:
        request_cost = 5 if render_js else 1
        if asp:
            request_cost = max(request_cost, 25)

    return ScrapflyScrape(
        html=html,
        status_code=int(result.get("status_code") or 200),
        final_url=str(result.get("url") or url),
        request_cost=request_cost,
        credits_remaining=_header_int(
            response_headers,
            "x-scrapfly-remaining-api-credit",
        ),
        asp=asp,
        render_js=render_js or screenshot,
        screenshot=image,
    )


def scrape_url_sync(
    url: str,
    *,
    render_js: bool = True,
    asp: bool = False,
    headers: Mapping[str, str] | None = None,
    wait_ms: int | None = None,
    timeout_ms: int | None = None,
    screenshot: bool = False,
    full_screenshot: bool = False,
) -> ScrapflyScrape:
    errors: list[str] = []
    attempt_limit = max(4, len(parse_scrapfly_api_keys()) or 1)
    for _ in range(attempt_limit):
        lease = acquire_scrapfly_key()
        try:
            scraped = _scrape_once(
                url,
                api_key=lease.key.key,
                render_js=render_js,
                asp=asp,
                headers=headers,
                wait_ms=wait_ms,
                timeout_ms=timeout_ms,
                screenshot=screenshot,
                full_screenshot=full_screenshot,
            )
        except Exception as exc:
            if is_scrapfly_credit_error(exc):
                mark_scrapfly_key_exhausted(lease.key.label, reason=str(exc))
                errors.append(f"{lease.key.label}: {exc}")
                continue
            raise

        rotation = record_scrapfly_credits(lease.key.label, scraped.request_cost)
        return ScrapflyScrape(
            html=scraped.html,
            status_code=scraped.status_code,
            final_url=scraped.final_url,
            request_cost=scraped.request_cost,
            credits_remaining=scraped.credits_remaining,
            asp=scraped.asp,
            render_js=scraped.render_js,
            screenshot=scraped.screenshot,
            key_label=lease.key.label,
            key_fingerprint=lease.key.fingerprint,
            key_rotation=rotation,
        )

    detail = "; ".join(errors) if errors else "no available keys"
    raise RuntimeError(f"Scrapfly scrape failed after key rotation attempts: {detail}")


async def scrape_url(
    url: str,
    *,
    render_js: bool = True,
    asp: bool = False,
    headers: Mapping[str, str] | None = None,
    wait_ms: int | None = None,
    timeout_ms: int | None = None,
    screenshot: bool = False,
    full_screenshot: bool = False,
) -> ScrapflyScrape:
    return await asyncio.to_thread(
        scrape_url_sync,
        url,
        render_js=render_js,
        asp=asp,
        headers=headers,
        wait_ms=wait_ms,
        timeout_ms=timeout_ms,
        screenshot=screenshot,
        full_screenshot=full_screenshot,
    )
