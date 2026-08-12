from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import Headers, MutableHeaders

from . import config
from .api_tokens import (
    ApiTokenError,
    ApiTokenPrincipal,
    authenticate_api_token,
    extract_api_token,
    get_api_token_store,
)
from .redis_cache import get_tiered_cache

Send = Callable[[dict[str, Any]], Awaitable[None]]
logger = logging.getLogger(__name__)


def _protected_surface(path: str) -> bool:
    return path.startswith(("/v1", "/api", "/datasets", "/admin"))


async def _send_limit_error(
    send: Send,
    *,
    error: ApiTokenError,
    principal: ApiTokenPrincipal,
    rate_count: int,
    rate_layer: str,
) -> None:
    body = json.dumps(
        {"error": {"code": error.code, "message": error.message}},
        separators=(",", ":"),
    ).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"private, no-store"),
        (b"retry-after", b"60"),
        (b"x-ratelimit-limit", str(principal.rate_limit_per_minute).encode()),
        (
            b"x-ratelimit-remaining",
            str(max(0, principal.rate_limit_per_minute - rate_count)).encode(),
        ),
        (b"x-ratelimit-layer", rate_layer.encode()),
        (b"x-quota-limit", str(principal.monthly_quota).encode()),
    ]
    if error.code == "MONTHLY_QUOTA_EXCEEDED":
        headers.append((b"x-quota-remaining", b"0"))
    await send({"type": "http.response.start", "status": 429, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class ApiTokenLimitsMiddleware:
    """Apply distributed burst limits and durable monthly token quotas."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Send,
    ) -> None:
        if scope.get("type") != "http" or not _protected_surface(
            str(scope.get("path") or "")
        ):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        try:
            supplied = extract_api_token(
                headers.get("authorization"),
                headers.get("x-api-key"),
            )
            principal = authenticate_api_token(
                supplied,
                required_scope=None,
                legacy_key=config.api_key(),
            )
        except ApiTokenError:
            # Authentication semantics belong to the protected route. Public
            # routes remain public even when no credential was supplied.
            await self.app(scope, receive, send)
            return

        rate_count, rate_layer = await get_tiered_cache().increment(
            f"rate:{principal.id}",
            ttl_seconds=60,
        )
        if rate_count > principal.rate_limit_per_minute:
            await _send_limit_error(
                send,
                error=ApiTokenError(
                    "RATE_LIMIT_EXCEEDED",
                    "API token request rate has been exceeded",
                    status_code=429,
                ),
                principal=principal,
                rate_count=rate_count,
                rate_layer=rate_layer,
            )
            return

        usage = None
        store = get_api_token_store()
        if not principal.is_legacy:
            try:
                usage = await asyncio.to_thread(store.reserve_request, principal.id)
            except ApiTokenError as error:
                await _send_limit_error(
                    send,
                    error=error,
                    principal=principal,
                    rate_count=rate_count,
                    rate_layer=rate_layer,
                )
                return

        response_status = 500
        response_bytes = 0

        async def send_with_limits(message: dict[str, Any]) -> None:
            nonlocal response_status, response_bytes
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status") or 500)
                response_headers = MutableHeaders(scope=message)
                response_headers["X-RateLimit-Limit"] = str(
                    principal.rate_limit_per_minute
                )
                response_headers["X-RateLimit-Remaining"] = str(
                    max(0, principal.rate_limit_per_minute - rate_count)
                )
                response_headers["X-RateLimit-Layer"] = rate_layer
                response_headers["X-Quota-Limit"] = str(principal.monthly_quota)
                if usage is not None:
                    response_headers["X-Quota-Remaining"] = str(
                        max(0, principal.monthly_quota - usage.request_count)
                    )
            elif message.get("type") == "http.response.body":
                response_bytes += len(message.get("body") or b"")
            await send(message)

        try:
            await self.app(scope, receive, send_with_limits)
        finally:
            if usage is not None:
                try:
                    await asyncio.to_thread(
                        store.complete_request,
                        principal.id,
                        status=response_status,
                        response_bytes=response_bytes,
                    )
                except Exception:
                    logger.exception(
                        "Could not finalize usage counters for API token %s",
                        principal.id,
                    )
