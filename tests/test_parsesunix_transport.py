from __future__ import annotations

import asyncio

from web_scraper.fetchers import RawResponse

from app import parsesunix_transport


class FakeTransport:
    response: RawResponse

    def __init__(self, **_kwargs: object) -> None:
        pass

    def fetch(self, _url: str) -> RawResponse:
        return self.response


def test_direct_transport_returns_validated_secret_free_evidence(monkeypatch) -> None:
    body = b"<html><body>" + (b"real content " * 30) + b"</body></html>"
    FakeTransport.response = RawResponse(
        requested_url="https://example.com/list?private=value",
        final_url="https://example.com/list?session=secret",
        status=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=body,
        elapsed_ms=42,
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    evidence = asyncio.run(
        parsesunix_transport.fetch_direct("https://example.com/list?private=value")
    )

    assert evidence.body == body.decode()
    assert evidence.transport_validated is True
    assert evidence.verdict == "OK"
    assert evidence.paid_requests == 0
    telemetry = evidence.telemetry()
    assert telemetry["final_host"] == "example.com"
    assert telemetry["publication_validated"] is None
    assert "body" not in telemetry
    assert "secret" not in repr(telemetry)
    assert "private" not in repr(telemetry)


def test_direct_transport_does_not_accept_soft_block(monkeypatch) -> None:
    body = b"<html><body>Verify you are human" + (b" " * 300) + b"</body></html>"
    FakeTransport.response = RawResponse(
        requested_url="https://example.com",
        final_url="https://example.com",
        status=200,
        headers={"Content-Type": "text/html"},
        body=body,
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    evidence = asyncio.run(parsesunix_transport.fetch_direct("https://example.com"))

    assert evidence.transport_validated is False
    assert evidence.verdict == "SOFT_BLOCK"


def test_truncated_response_is_never_transport_validated(monkeypatch) -> None:
    FakeTransport.response = RawResponse(
        requested_url="https://example.com",
        final_url="https://example.com",
        status=200,
        headers={"Content-Type": "text/html"},
        body=b"x" * 300,
        truncated=True,
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    evidence = asyncio.run(parsesunix_transport.fetch_direct("https://example.com"))

    assert evidence.transport_validated is False
    assert evidence.verdict == "PARSE_FAIL"
    assert "configured" in evidence.reason


def test_transport_error_is_sanitized(monkeypatch) -> None:
    FakeTransport.response = RawResponse(
        requested_url="https://example.com?token=secret",
        final_url="https://example.com?token=secret",
        status=None,
        headers={},
        body=b"",
        transport_error="connection failed for https://example.com?token=secret",
    )
    monkeypatch.setattr(parsesunix_transport, "UrllibTransport", FakeTransport)

    evidence = asyncio.run(
        parsesunix_transport.fetch_direct("https://example.com?token=secret")
    )

    assert evidence.transport_validated is False
    assert evidence.verdict == "ORIGIN_DOWN"
    assert "secret" not in evidence.reason
    assert "secret" not in repr(evidence.telemetry())
