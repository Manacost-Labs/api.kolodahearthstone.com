from __future__ import annotations

import re

_PROXY_TUNNEL_STATUS_PATTERNS = (
    re.compile(
        r"\bconnect tunnel failed,? (?:response|status)\s*[:=]?\s*(402|407)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\breceived http code\s+(402|407)\s+from proxy after connect\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\btunnel connection failed\s*:\s*(402|407)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bproxy connect\b[^\n]{0,80}\b(?:http\s*)?(402|407)\b",
        re.IGNORECASE,
    ),
)
_BARE_HTTPX_PROXY_STATUS_PATTERN = re.compile(
    r"^\s*(?:(402)\s+payment required|(407)\s+proxy authentication required)[.!]?\s*$",
    re.IGNORECASE,
)


class ProxyPaymentRequiredError(RuntimeError):
    """The configured proxy rejected its CONNECT tunnel with HTTP 402/407.

    The historical class name is retained because callers already use it as
    the typed fail-fast signal for an unavailable paid proxy.
    """

    def __init__(self, message: str, *, status_code: int = 407) -> None:
        if status_code not in {402, 407}:
            raise ValueError("proxy CONNECT status must be 402 or 407")
        super().__init__(message)
        self.status_code: int = status_code


def proxy_tunnel_status(exc: BaseException, *, proxy_used: bool) -> int | None:
    """Return a CONNECT 402/407 only for a request known to use a proxy.

    Plain occurrences such as a target URL containing ``402`` or an origin
    response saying "returned 407" are deliberately not classified as proxy
    failures. This keeps the process-wide proxy circuit scoped to actual
    tunnel failures.
    """

    if not proxy_used:
        return None
    if isinstance(exc, ProxyPaymentRequiredError):
        return exc.status_code
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) in {402, 407}:
        # A real origin response travelled through a working proxy. It must not
        # disable the refresh-wide paid-proxy circuit.
        return None
    message = str(exc)
    exc_module = type(exc).__module__.partition(".")[0]
    if type(exc).__name__ == "ProxyError" and exc_module in {"httpcore", "httpx"}:
        match = _BARE_HTTPX_PROXY_STATUS_PATTERN.fullmatch(message)
        if match:
            return int(match.group(1) or match.group(2))
    for pattern in _PROXY_TUNNEL_STATUS_PATTERNS:
        match = pattern.search(message)
        if match:
            return int(match.group(1))
    return None


def proxy_tunnel_error(
    exc: BaseException,
    *,
    proxy_used: bool,
) -> ProxyPaymentRequiredError | None:
    """Convert a verified CONNECT rejection to the stable typed exception."""

    status = proxy_tunnel_status(exc, proxy_used=proxy_used)
    if status is None:
        return None
    if isinstance(exc, ProxyPaymentRequiredError):
        return exc
    return ProxyPaymentRequiredError(
        f"Residential proxy CONNECT tunnel rejected the request (HTTP {status})",
        status_code=status,
    )
