from __future__ import annotations

import fcntl
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

from . import provider_usage
from .config import decodo_api_key, decodo_request_limit
from .storage import root_dir

DECODO_URL = "https://data.decodo.com/v1/scrape"
MAX_RESPONSE_BYTES = 25 * 1024 * 1024


class DecodoRequestError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int | None = None, terminal: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.terminal = terminal


@dataclass(frozen=True)
class DecodoScrape:
    html: str
    status_code: int
    final_url: str
    usage_call_id: int | None = None


def _reserve_request() -> None:
    """Reserve before sending; failed requests also consume the local ceiling."""
    limit = decodo_request_limit()
    if limit <= 0:
        raise DecodoRequestError("Decodo request budget disabled", terminal=True)
    today = datetime.now(UTC).date()
    cutoff = today - timedelta(days=31)
    try:
        root = root_dir()
        state_path = root / "decodo-usage.json"
        with (root / "decodo-usage.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                counts = json.loads(state_path.read_text())
            except FileNotFoundError:
                counts = {}
            if not isinstance(counts, dict):
                raise TypeError("invalid usage state")
            retained = {}
            for day, count in counts.items():
                parsed_day = date.fromisoformat(day)
                if (
                    type(count) is not int
                    or count < 0
                    or parsed_day > today
                    or parsed_day.isoformat() != day
                ):
                    raise ValueError("invalid usage counter")
                if parsed_day >= cutoff:
                    retained[day] = count
            if sum(retained.values()) >= limit:
                raise DecodoRequestError(
                    "Decodo request budget exhausted", terminal=True
                )
            retained[today.isoformat()] = retained.get(today.isoformat(), 0) + 1
            pending_path = root / "decodo-usage.tmp"
            with pending_path.open("w") as pending:
                json.dump(retained, pending)
                pending.flush()
                os.fsync(pending.fileno())
            os.replace(pending_path, state_path)
    except (OSError, ValueError, TypeError) as exc:
        raise DecodoRequestError(
            "Decodo request budget unavailable", terminal=True
        ) from exc


def scrape_url_sync(
    url: str,
    *,
    render: bool = False,
    super_proxy: bool = False,
    headers: Mapping[str, str] | None = None,
    timeout_ms: int | None = None,
) -> DecodoScrape:
    """Bounded anonymous HSGuru reserve; never forward login credentials."""
    target = urlsplit(url)
    if (
        target.scheme != "https"
        or target.hostname not in {"hsguru.com", "www.hsguru.com"}
        or target.username
        or target.password
        or target.port not in {None, 443}
    ):
        raise DecodoRequestError("Decodo reserve target is not allowed", terminal=True)
    if any(
        key.casefold()
        in {"cookie", "authorization", "proxy-authorization", "x-api-key"}
        for key in headers or {}
    ):
        raise DecodoRequestError(
            "Decodo reserve cannot forward authentication", terminal=True
        )
    key = decodo_api_key()
    if not key:
        raise DecodoRequestError("Decodo reserve is not configured", terminal=True)
    # Standard pool + forced headers was rejected by the live v1 API. Public
    # HSGuru HTML needs no target headers; use the tested URL + pool payload.
    payload: dict[str, object] = {
        "url": url,
        "proxy_pool": "premium" if super_proxy else "standard",
    }
    if render:
        payload["headless"] = "html"
    request = urllib.request.Request(
        DECODO_URL,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    timeout = min(150.0, max(15.0, (timeout_ms or 60_000) / 1000))
    _reserve_request()
    started = time.monotonic()
    usage = {
        "url": url,
        "started": started,
        "profile": f"{payload['proxy_pool']}{'+render' if render else ''}",
        "render": render,
        "cost_basis": "estimated",
    }
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        provider_usage.record_call(
            "decodo",
            status=exc.code,
            outcome="failed",
            reason=f"http_{exc.code}",
            **usage,
        )
        raise DecodoRequestError(
            f"Decodo API returned HTTP {exc.code}",
            status_code=exc.code,
            terminal=exc.code in {400, 401, 402, 403, 422, 429},
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        provider_usage.record_call(
            "decodo",
            outcome="failed",
            reason="timeout" if isinstance(exc, TimeoutError) else "transport",
            **usage,
        )
        raise DecodoRequestError("Decodo reserve transport failed") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        provider_usage.record_call(
            "decodo", outcome="failed", reason="oversize", content=len(raw), **usage
        )
        raise DecodoRequestError("Decodo response exceeded size limit")
    status: object = None
    try:
        body = json.loads(raw)
        results = body["results"]
        if not isinstance(results, list) or len(results) != 1:
            raise ValueError("one result required")
        result = results[0]
        content = result["content"]
        status = result["status_code"]
        final_url = result.get("url") or url
        if not isinstance(content, str) or not content.strip():
            raise ValueError("HTML required")
        if (
            isinstance(status, bool)
            or not isinstance(status, int)
            or not 100 <= status <= 599
        ):
            raise ValueError("target status required")
        final = urlsplit(final_url)
        if (
            final.scheme != "https"
            or final.hostname not in {"hsguru.com", "www.hsguru.com"}
            or final.username
            or final.password
            or final.port not in {None, 443}
        ):
            raise ValueError("offsite result")
    except (ValueError, KeyError, TypeError, AttributeError):
        billable = isinstance(status, int) and 200 <= status <= 299
        provider_usage.record_call(
            "decodo",
            status=status,
            outcome="failed",
            reason="unusable_result",
            content=len(raw),
            # Decodo bills a delivered target 2xx even when it is unusable.
            cost=1 if billable else 0,
            **usage,
        )
        raise DecodoRequestError("Decodo returned an unusable result") from None
    call_id = provider_usage.record_call(
        "decodo",
        status=status,
        content=content,
        cost=1 if 200 <= status <= 299 else 0,
        **usage,
    )
    return DecodoScrape(content, status, final_url, usage_call_id=call_id)
