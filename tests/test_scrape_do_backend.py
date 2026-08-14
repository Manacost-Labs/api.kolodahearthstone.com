from __future__ import annotations

import io
import urllib.error
from email.message import Message
from unittest.mock import patch

import pytest

from app.scrape_do_backend import (
    ScrapeDoAccountError,
    ScrapeDoRequestError,
    ScrapeDoTransientError,
    scrape_url_sync,
)


def _http_error(
    status: int,
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
) -> urllib.error.HTTPError:
    response_headers = Message()
    for name, value in (headers or {}).items():
        response_headers[name] = value
    return urllib.error.HTTPError(
        url="https://api.scrape.do/?token=must-not-leak&url=https%3A%2F%2Fexample.com",
        code=status,
        msg="provider error",
        hdrs=response_headers,
        fp=io.BytesIO(body),
    )


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_temporary_provider_failures_are_retryable(status: int) -> None:
    with (
        patch("app.scrape_do_backend.scrape_do_token", return_value="secret"),
        patch(
            "app.scrape_do_backend.urllib.request.urlopen",
            side_effect=_http_error(status, b"temporary failure"),
        ),
        pytest.raises(ScrapeDoTransientError) as captured,
    ):
        scrape_url_sync("https://example.com")

    assert captured.value.status_code == status
    assert captured.value.retryable is True
    assert "must-not-leak" not in str(captured.value)


def test_account_failure_is_not_retryable() -> None:
    with (
        patch("app.scrape_do_backend.scrape_do_token", return_value="secret"),
        patch(
            "app.scrape_do_backend.urllib.request.urlopen",
            side_effect=_http_error(401, b"subscription unavailable"),
        ),
        pytest.raises(ScrapeDoAccountError) as captured,
    ):
        scrape_url_sync("https://example.com")

    assert captured.value.status_code == 401
    assert captured.value.retryable is False


@pytest.mark.parametrize("status", [400, 402, 403, 404, 407, 410, 510])
def test_permanent_request_failures_are_not_retryable(status: int) -> None:
    with (
        patch("app.scrape_do_backend.scrape_do_token", return_value="secret"),
        patch(
            "app.scrape_do_backend.urllib.request.urlopen",
            side_effect=_http_error(status, b"invalid request"),
        ),
        pytest.raises(ScrapeDoRequestError) as captured,
    ):
        scrape_url_sync("https://example.com")

    assert not isinstance(captured.value, ScrapeDoTransientError)
    assert captured.value.status_code == status
    assert captured.value.retryable is False


def test_retry_after_is_preserved_for_concurrency_limit() -> None:
    with (
        patch("app.scrape_do_backend.scrape_do_token", return_value="secret"),
        patch(
            "app.scrape_do_backend.urllib.request.urlopen",
            side_effect=_http_error(
                429,
                b"concurrency exceeded",
                headers={"Retry-After": "7"},
            ),
        ),
        pytest.raises(ScrapeDoTransientError) as captured,
    ):
        scrape_url_sync("https://example.com")

    assert captured.value.retry_after_seconds == 7.0


def _response() -> object:
    return type(
        "Response",
        (),
        {
            "status": 200,
            "headers": Message(),
            "read": lambda self: b"<html>ok</html>",
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: None,
        },
    )()


def test_target_headers_use_extra_headers_by_default() -> None:
    with (
        patch("app.scrape_do_backend.scrape_do_token", return_value="secret"),
        patch(
            "app.scrape_do_backend.urllib.request.urlopen",
            return_value=_response(),
        ) as urlopen,
    ):
        scrape_url_sync(
            "https://example.com",
            headers={"Cookie": "session=value", "Accept-Language": "en-US"},
        )

    request = urlopen.call_args.args[0]
    assert "extraHeaders=true" in request.full_url
    assert "forwardHeaders" not in request.full_url
    assert request.get_header("Sd-cookie") == "session=value"
    assert request.get_header("Cookie") is None


def test_retry_timeout_is_only_sent_for_non_render_requests() -> None:
    with (
        patch("app.scrape_do_backend.scrape_do_token", return_value="secret"),
        patch(
            "app.scrape_do_backend.urllib.request.urlopen",
            return_value=_response(),
        ) as urlopen,
    ):
        scrape_url_sync(
            "https://example.com",
            render=False,
            retry_timeout_ms=1,
        )

    assert "retryTimeout=5000" in urlopen.call_args.args[0].full_url


def test_response_keeps_only_bounded_safe_target_headers() -> None:
    response = _response()
    for name, value in {
        "Date": "Fri, 14 Aug 2026 02:20:02 GMT",
        "Age": "38",
        "ETag": 'W/"safe"',
        "Last-Modified": "Fri, 14 Aug 2026 02:10:32 GMT",
        "Cache-Control": "public, max-age=60",
        "CF-Cache-Status": "HIT",
        "Set-Cookie": "secret=must-not-survive",
        "Scrape.do-Cookies": "secret=must-not-survive",
        "X-Untrusted": "must-not-survive",
        "Expires": "x" * 600,
    }.items():
        response.headers[name] = value
    with (
        patch("app.scrape_do_backend.scrape_do_token", return_value="secret"),
        patch(
            "app.scrape_do_backend.urllib.request.urlopen",
            return_value=response,
        ),
    ):
        result = scrape_url_sync("https://example.com")

    assert result.target_headers == {
        "date": "Fri, 14 Aug 2026 02:20:02 GMT",
        "age": "38",
        "etag": 'W/"safe"',
        "last-modified": "Fri, 14 Aug 2026 02:10:32 GMT",
        "cache-control": "public, max-age=60",
        "cf-cache-status": "HIT",
    }
