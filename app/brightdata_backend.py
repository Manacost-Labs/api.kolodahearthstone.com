from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.client import HTTPMessage
from typing import Protocol, cast
from urllib.parse import urlsplit

from .brightdata_state import (
    finish_request,
    reserve_request,
    usage_state_initialized,
)
from .config import (
    brightdata_api_key,
    brightdata_circuit_cooldown_seconds,
    brightdata_circuit_failure_threshold,
    brightdata_monthly_billable_limit,
    brightdata_source_ids,
    brightdata_timeout_seconds,
    brightdata_unlocker_enabled,
    brightdata_unlocker_zone,
)

BRIGHTDATA_UNLOCKER_URL = "https://api.brightdata.com/request"
_MAX_RESPONSE_BYTES = 25 * 1024 * 1024
_ZONE_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class BrightDataPolicyError(RuntimeError):
    pass


class BrightDataRequestError(RuntimeError):
    def __init__(self, message: str, *, billed: bool | None = None) -> None:
        super().__init__(message)
        self.billed: bool | None = billed


@dataclass(frozen=True)
class BrightDataScrape:
    html: str
    status_code: int
    final_url: str
    billable_requests: int
    request_id: str | None
    rendered: bool | None
    budget_remaining: int

    @property
    def content_length(self) -> int:
        return len(self.html.encode("utf-8", errors="replace"))


@dataclass(frozen=True)
class _DebugInfo:
    billed: bool | None = None
    request_id: str | None = None
    rendered: bool | None = None


class _HttpResponse(Protocol):
    headers: HTTPMessage
    status: int

    def read(self, amount: int = -1) -> bytes: ...

    def close(self) -> None: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # pyright: ignore[reportImplicitOverride]
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl


def brightdata_configured_for_source(source_id: str) -> bool:
    zone = brightdata_unlocker_zone()
    return bool(
        brightdata_unlocker_enabled()
        and brightdata_api_key()
        and zone
        and _ZONE_RE.fullmatch(zone)
        and source_id in brightdata_source_ids()
        and brightdata_monthly_billable_limit() > 0
        and usage_state_initialized()
    )


def _assert_public_https_target(url: str) -> None:
    if (
        len(url) > 4096
        or "\\" in url
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in url)
    ):
        raise BrightDataPolicyError("Bright Data target is not allowed")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        raise BrightDataPolicyError("Bright Data target is not allowed") from None
    hostname = parsed.hostname
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise BrightDataPolicyError("Bright Data target is not allowed")
    lowered = hostname.rstrip(".").lower()
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(
        (".localhost", ".local", ".internal", ".home.arpa")
    ):
        raise BrightDataPolicyError("Bright Data target is not allowed")
    try:
        literal = ipaddress.ip_address(lowered)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise BrightDataPolicyError("Bright Data target is not allowed")
        return
    try:
        resolved = {
            item[4][0]
            for item in socket.getaddrinfo(
                lowered,
                443,
                type=socket.SOCK_STREAM,
            )
        }
    except OSError:
        raise BrightDataPolicyError("Bright Data target is not allowed") from None
    if not resolved:
        raise BrightDataPolicyError("Bright Data target is not allowed")
    try:
        addresses = [ipaddress.ip_address(item) for item in resolved]
    except ValueError:
        raise BrightDataPolicyError("Bright Data target is not allowed") from None
    if any(not address.is_global for address in addresses):
        raise BrightDataPolicyError("Bright Data target is not allowed")


def _parse_debug_header(value: str | None) -> _DebugInfo:
    fields: dict[str, str] = {}
    for part in (value or "").split(";"):
        key, separator, raw = part.strip().partition("=")
        if separator and key in {"req_id", "billed", "render"}:
            fields[key] = raw.strip()
    billed = (
        True
        if fields.get("billed") == "true"
        else False
        if fields.get("billed") == "false"
        else None
    )
    rendered = (
        True
        if fields.get("render") == "true"
        else False
        if fields.get("render") == "false"
        else None
    )
    request_id = fields.get("req_id")
    if request_id is not None and not _REQUEST_ID_RE.fullmatch(request_id):
        request_id = None
    return _DebugInfo(
        billed=billed,
        request_id=request_id,
        rendered=rendered,
    )


def _send(
    request: urllib.request.Request,
    *,
    timeout_seconds: float,
) -> tuple[bytes, Mapping[str, str], int]:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        response = cast(
            _HttpResponse,
            opener.open(request, timeout=timeout_seconds),
        )
        try:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            debug_header = response.headers.get("x-brd-debug")
            headers = (
                {"x-brd-debug": str(debug_header)} if debug_header is not None else {}
            )
            status_code = response.status
        finally:
            response.close()
    except urllib.error.HTTPError as exc:
        debug = _parse_debug_header(
            exc.headers.get("x-brd-debug") if exc.headers else None
        )
        raise BrightDataRequestError(
            f"Bright Data API HTTP {int(exc.code)}",
            billed=debug.billed,
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise BrightDataRequestError("Bright Data transport error") from None
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise BrightDataRequestError("Bright Data response is too large")
    return raw, headers, status_code


def _parse_raw_response(raw: bytes, status_code: int) -> tuple[int, str]:
    if (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or not 100 <= status_code <= 599
    ):
        raise BrightDataRequestError("Bright Data response is malformed")
    if not 200 <= status_code <= 299:
        raise BrightDataRequestError(f"Bright Data API HTTP {status_code}")
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BrightDataRequestError("Bright Data response is not valid UTF-8") from None
    if not body.strip():
        raise BrightDataRequestError("Bright Data returned an empty body")
    return status_code, body


def scrape_url_sync(
    url: str,
    *,
    source_id: str,
    timeout_ms: int | None = None,
    accept_html: Callable[[str], bool] | None = None,
    render: bool = False,
) -> BrightDataScrape:
    if not brightdata_configured_for_source(source_id):
        raise BrightDataPolicyError("Bright Data fallback is not configured")
    _assert_public_https_target(url)
    api_key = brightdata_api_key()
    zone = brightdata_unlocker_zone()
    if not api_key or not zone or not _ZONE_RE.fullmatch(zone):
        raise BrightDataPolicyError("Bright Data fallback is not configured")

    monthly_limit = brightdata_monthly_billable_limit()
    failure_threshold = brightdata_circuit_failure_threshold()
    cooldown_seconds = brightdata_circuit_cooldown_seconds()
    reservation = reserve_request(
        monthly_limit=monthly_limit,
        circuit_failure_threshold=failure_threshold,
        circuit_cooldown_seconds=cooldown_seconds,
    )
    debug = _DebugInfo()
    request_started = False
    try:
        payload: dict[str, object] = {
            "zone": zone,
            "url": url,
            "format": "raw",
            "debug": True,
        }
        if render:
            payload["render"] = "true"
        request = urllib.request.Request(
            BRIGHTDATA_UNLOCKER_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        configured_timeout = brightdata_timeout_seconds()
        timeout_seconds = configured_timeout
        if timeout_ms is not None:
            caller_timeout = max(30.0, float(timeout_ms) / 1000.0 + 30.0)
            timeout_seconds = min(configured_timeout, caller_timeout)
        request_started = True
        raw, response_headers, response_status = _send(
            request,
            timeout_seconds=timeout_seconds,
        )
        debug = _parse_debug_header(response_headers.get("x-brd-debug"))
        status_code, html = _parse_raw_response(raw, response_status)
        if accept_html is not None:
            try:
                accepted = bool(accept_html(html))
            except Exception:  # noqa: BLE001 - untrusted validation callback
                accepted = False
            if not accepted:
                raise BrightDataRequestError(
                    "Bright Data response failed content validation",
                    billed=debug.billed,
                )
    except BrightDataRequestError as exc:
        billed = (
            exc.billed if exc.billed is not None else debug.billed
        ) if request_started else False
        _ = finish_request(
            reservation.reservation_id,
            monthly_limit=monthly_limit,
            billed=billed,
            succeeded=False,
            circuit_failure_threshold=failure_threshold,
            circuit_cooldown_seconds=cooldown_seconds,
        )
        raise
    except Exception:  # noqa: BLE001 - always close the paid-request reservation
        _ = finish_request(
            reservation.reservation_id,
            monthly_limit=monthly_limit,
            billed=debug.billed if request_started else False,
            succeeded=False,
            circuit_failure_threshold=failure_threshold,
            circuit_cooldown_seconds=cooldown_seconds,
        )
        raise BrightDataRequestError("Bright Data request failed") from None

    snapshot = finish_request(
        reservation.reservation_id,
        monthly_limit=monthly_limit,
        billed=debug.billed,
        succeeded=True,
        circuit_failure_threshold=failure_threshold,
        circuit_cooldown_seconds=cooldown_seconds,
    )
    return BrightDataScrape(
        html=html,
        status_code=status_code,
        final_url=url,
        billable_requests=1 if debug.billed is not False else 0,
        request_id=debug.request_id,
        rendered=debug.rendered,
        budget_remaining=snapshot.remaining_requests,
    )
