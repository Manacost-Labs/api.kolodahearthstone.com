from __future__ import annotations

import asyncio
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import firecrawl_api_key


FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
SCRAPE_DO_URL = "https://api.scrape.do/"


@dataclass(frozen=True)
class CardPeriodFetch:
    payload: dict[str, Any]
    backend: str
    attempts: tuple[dict[str, str], ...]


def _scrape_do_token() -> str | None:
    value = (
        os.environ.get("HS_SCRAPE_DO_TOKEN")
        or os.environ.get("SCRAPE_DO_TOKEN")
        or ""
    ).strip()
    return value or None


def _timeout_seconds() -> float:
    return max(15.0, float(os.environ.get("HS_CARD_PERIOD_PROXY_TIMEOUT_SECONDS", "90")))


def _json_document(value: str) -> dict[str, Any]:
    candidate = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    pre = re.search(r"<pre[^>]*>(.*?)</pre>", candidate, flags=re.DOTALL | re.IGNORECASE)
    if pre:
        candidate = html.unescape(re.sub(r"<[^>]+>", "", pre.group(1))).strip()
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise RuntimeError("HSReplay card-list response is not a JSON object")
    return parsed


def _firecrawl_fetch(url: str) -> dict[str, Any]:
    api_key = firecrawl_api_key()
    if not api_key:
        raise RuntimeError("Firecrawl is not configured")
    timeout = _timeout_seconds()
    request = urllib.request.Request(
        FIRECRAWL_SCRAPE_URL,
        data=json.dumps(
            {
                "url": url,
                "formats": ["rawHtml"],
                "onlyMainContent": False,
                "maxAge": 0,
                "waitFor": 0,
                "timeout": int(timeout * 1000),
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout + 30) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Firecrawl HTTP {exc.code}: {detail[:240]}") from exc
    if not envelope.get("success"):
        raise RuntimeError("Firecrawl did not return a successful scrape")
    data = envelope.get("data") or {}
    raw = data.get("rawHtml") or data.get("html") or data.get("markdown") or ""
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("Firecrawl response did not include the card-list body")
    return _json_document(raw)


def _scrape_do_fetch(url: str) -> dict[str, Any]:
    token = _scrape_do_token()
    if not token:
        raise RuntimeError("Scrape.do is not configured")
    parameters = {
        'token': token,
        'url': url,
        'render': 'false',
    }
    endpoint = f"{SCRAPE_DO_URL}?{urllib.parse.urlencode(parameters)}"
    request = urllib.request.Request(
        endpoint,
        headers={"User-Agent": "HSDataAPI/0.1 HSReplay card periods"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # Never include exc.url: Scrape.do puts the secret token in it.
        raise RuntimeError(f"Scrape.do HTTP {exc.code}: {detail[:240]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Scrape.do transport error: {exc.reason}") from exc
    return _json_document(raw)


def _fetch_sync(url: str) -> CardPeriodFetch:
    attempts: list[dict[str, str]] = []
    for backend, fetch in (
        ("firecrawl", _firecrawl_fetch),
        ("scrape_do", _scrape_do_fetch),
    ):
        try:
            payload = fetch(url)
            attempts.append({"backend": backend, "state": "ok"})
            return CardPeriodFetch(payload=payload, backend=backend, attempts=tuple(attempts))
        except Exception as exc:
            attempts.append({
                "backend": backend,
                "state": "failed",
                "error_type": type(exc).__name__,
            })
    summary = ", ".join(f"{item['backend']}={item['state']}" for item in attempts)
    raise RuntimeError(f"HSReplay card-period proxy fallback failed ({summary})")


async def fetch_hsreplay_card_period_json(url: str) -> CardPeriodFetch:
    return await asyncio.to_thread(_fetch_sync, url)
