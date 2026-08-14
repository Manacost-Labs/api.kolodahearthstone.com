from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field

from .config import scrape_do_timeout_seconds, scrape_do_token

SCRAPE_DO_URL = "https://api.scrape.do/"
SCRAPE_DO_MIN_TIMEOUT_MS = 5_000
SCRAPE_DO_MAX_TIMEOUT_MS = 120_000
SCRAPE_DO_MIN_RETRY_TIMEOUT_MS = 5_000
SCRAPE_DO_MAX_RETRY_TIMEOUT_MS = 55_000

_TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_ACCOUNT_HTTP_STATUSES = frozenset({401})
SAFE_TARGET_RESPONSE_HEADERS = frozenset(
    {
        "date",
        "age",
        "etag",
        "last-modified",
        "cache-control",
        "cf-cache-status",
    }
)
MAX_SAFE_TARGET_HEADER_VALUE_LENGTH = 512


class ScrapeDoRequestError(RuntimeError):
    """A Scrape.do failure with an explicit retry classification."""

    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        request_cost: int = 0,
        super_proxy: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.request_cost = max(0, int(request_cost))
        self.super_proxy = super_proxy


class ScrapeDoTransientError(ScrapeDoRequestError):
    """A temporary provider, concurrency, or transport failure."""

    retryable = True


class ScrapeDoAccountError(ScrapeDoRequestError):
    """The Scrape.do subscription or token cannot currently serve requests."""


@dataclass(frozen=True)
class ScrapeDoScrape:
    html: str
    status_code: int
    final_url: str
    request_cost: int
    credits_remaining: int | None
    super_proxy: bool
    screenshot: str | None = None
    target_headers: dict[str, str] = field(default_factory=dict)

    @property
    def content_length(self) -> int:
        return len(self.html.encode("utf-8", errors="replace"))


class ScrapeDoContentError(RuntimeError):
    """A billed Scrape.do response that failed response-body decoding."""

    def __init__(self, message: str, *, scrape: ScrapeDoScrape) -> None:
        super().__init__(message)
        self.scrape = scrape


def _header_int(headers: Mapping[str, str], name: str) -> int | None:
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed)


def _safe_target_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Keep only bounded, non-secret target representation metadata."""

    safe: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).strip().lower()
        if name not in SAFE_TARGET_RESPONSE_HEADERS or not isinstance(raw_value, str):
            continue
        value = raw_value.strip()
        if (
            not value
            or len(value) > MAX_SAFE_TARGET_HEADER_VALUE_LENGTH
            or "\r" in value
            or "\n" in value
        ):
            continue
        safe[name] = value
    return safe


def _clamp_timeout_ms(timeout_ms: int) -> int:
    return min(
        SCRAPE_DO_MAX_TIMEOUT_MS,
        max(SCRAPE_DO_MIN_TIMEOUT_MS, int(timeout_ms)),
    )


def _clamp_retry_timeout_ms(retry_timeout_ms: int) -> int:
    return min(
        SCRAPE_DO_MAX_RETRY_TIMEOUT_MS,
        max(SCRAPE_DO_MIN_RETRY_TIMEOUT_MS, int(retry_timeout_ms)),
    )


def scrape_url_sync(
    url: str,
    *,
    render: bool = True,
    super_proxy: bool = False,
    headers: Mapping[str, str] | None = None,
    forward_headers: bool = False,
    wait_ms: int | None = None,
    timeout_ms: int | None = None,
    retry_timeout_ms: int | None = None,
    screenshot: bool = False,
    full_screenshot: bool = False,
) -> ScrapeDoScrape:
    token = scrape_do_token()
    if not token:
        raise RuntimeError("Scrape.do token is not configured")
    params = {
        "token": token,
        "url": url,
        "render": str(bool(render)).lower(),
        **({"super": "true"} if super_proxy else {}),
    }
    if headers:
        params["forwardHeaders" if forward_headers else "extraHeaders"] = "true"
    if wait_ms is not None and render:
        params["customWait"] = str(max(0, int(wait_ms)))
    if timeout_ms is not None:
        params["timeout"] = str(_clamp_timeout_ms(timeout_ms))
    # retryTimeout is supported only by Scrape.do's non-rendered request path.
    if retry_timeout_ms is not None and not render:
        params["retryTimeout"] = str(_clamp_retry_timeout_ms(retry_timeout_ms))
    if screenshot:
        params["returnJSON"] = "true"
        params["fullScreenShot" if full_screenshot else "screenShot"] = "true"
    endpoint = f"{SCRAPE_DO_URL}?{urllib.parse.urlencode(params)}"
    request_headers = (
        dict(headers or {})
        if forward_headers
        else {f"Sd-{name}": value for name, value in (headers or {}).items()}
    )
    request_headers.setdefault("User-Agent", "HSDataAPI/0.1 Scrape.do fallback")
    request = urllib.request.Request(
        endpoint,
        headers=request_headers,
    )
    request_timeout = scrape_do_timeout_seconds()
    if timeout_ms is not None:
        request_timeout = min(
            request_timeout,
            (_clamp_timeout_ms(timeout_ms) / 1000) + 15,
        )
    try:
        with urllib.request.urlopen(
            request,
            timeout=request_timeout,
        ) as response:
            raw = response.read()
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            status_code = int(response.status)
            final_url = str(
                response_headers.get("scrape.do-resolved-url")
                or response_headers.get("scrape.do-final-url")
                or url
            )
    except urllib.error.HTTPError as exc:
        # Do not include exc.url: Scrape.do puts the secret token in it.
        detail = exc.read().decode("utf-8", errors="replace")
        detail = detail.replace(token, "<redacted>")
        error_type: type[ScrapeDoRequestError]
        if exc.code in _TRANSIENT_HTTP_STATUSES:
            error_type = ScrapeDoTransientError
        elif exc.code in _ACCOUNT_HTTP_STATUSES:
            error_type = ScrapeDoAccountError
        else:
            error_type = ScrapeDoRequestError
        error_headers = {
            key.lower(): value for key, value in (exc.headers or {}).items()
        }
        raise error_type(
            f"Scrape.do HTTP {exc.code}: {detail[:300]}",
            status_code=exc.code,
            retry_after_seconds=_retry_after_seconds(exc.headers or {}),
            request_cost=_header_int(
                error_headers,
                "scrape.do-request-cost",
            )
            or 0,
            super_proxy=super_proxy,
        ) from exc
    except urllib.error.URLError as exc:
        raise ScrapeDoTransientError(
            f"Scrape.do transport error: {exc.reason}",
            super_proxy=super_proxy,
        ) from exc
    except TimeoutError as exc:
        raise ScrapeDoTransientError(
            "Scrape.do request timed out",
            super_proxy=super_proxy,
        ) from exc
    body = raw.decode("utf-8", errors="replace")
    target_headers = _safe_target_response_headers(response_headers)
    request_cost = _header_int(response_headers, "scrape.do-request-cost")
    if request_cost is None:
        request_cost = (
            25 if render and super_proxy else 10 if super_proxy else 5 if render else 1
        )

    def content_error(message: str, *, parsed_html: str = "") -> ScrapeDoContentError:
        return ScrapeDoContentError(
            message,
            scrape=ScrapeDoScrape(
                html=parsed_html,
                status_code=status_code,
                final_url=final_url,
                request_cost=request_cost,
                credits_remaining=_header_int(
                    response_headers,
                    "scrape.do-remaining-credits",
                ),
                super_proxy=super_proxy,
                target_headers=target_headers,
            ),
        )

    image: str | None = None
    html = body
    if screenshot:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise content_error(
                "Scrape.do screenshot response is not valid JSON"
            ) from exc
        shots = payload.get("screenShots") if isinstance(payload, dict) else None
        if isinstance(shots, list) and shots and isinstance(shots[0], dict):
            value = shots[0].get("image")
            image = str(value) if value else None
        content = payload.get("content") if isinstance(payload, dict) else None
        html = str(content) if isinstance(content, str) else ""
        if not image:
            raise content_error(
                "Scrape.do screenshot response did not include an image",
                parsed_html=html,
            )
    if not html.strip() and not image:
        raise content_error("Scrape.do returned an empty body")
    return ScrapeDoScrape(
        html=html,
        status_code=status_code,
        final_url=final_url,
        request_cost=request_cost,
        credits_remaining=_header_int(
            response_headers,
            "scrape.do-remaining-credits",
        ),
        super_proxy=super_proxy,
        screenshot=image,
        target_headers=target_headers,
    )


async def scrape_url(
    url: str,
    *,
    render: bool = True,
    super_proxy: bool = False,
    headers: Mapping[str, str] | None = None,
    forward_headers: bool = False,
    wait_ms: int | None = None,
    timeout_ms: int | None = None,
    retry_timeout_ms: int | None = None,
    screenshot: bool = False,
    full_screenshot: bool = False,
) -> ScrapeDoScrape:
    return await asyncio.to_thread(
        scrape_url_sync,
        url,
        render=render,
        super_proxy=super_proxy,
        headers=headers,
        forward_headers=forward_headers,
        wait_ms=wait_ms,
        timeout_ms=timeout_ms,
        retry_timeout_ms=retry_timeout_ms,
        screenshot=screenshot,
        full_screenshot=full_screenshot,
    )
