from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

from app.brightdata_backend import (
    BRIGHTDATA_UNLOCKER_URL,
    BrightDataPolicyError,
    BrightDataRequestError,
    _send,
    brightdata_configured_for_source,
    scrape_url_sync,
)
from app.brightdata_state import initialize_usage_state


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HS_BRIGHTDATA_UNLOCKER_ENABLED", "true")
    monkeypatch.setenv("HS_BRIGHTDATA_API_KEY", "test-api-key")
    monkeypatch.setenv("HS_BRIGHTDATA_UNLOCKER_ZONE", "test-zone")
    monkeypatch.setenv("HS_BRIGHTDATA_SOURCE_IDS", "allowed_source")
    monkeypatch.setenv("HS_BRIGHTDATA_MONTHLY_BILLABLE_LIMIT", "10")
    initialize_usage_state(monthly_limit=10)


def test_unlocker_sends_one_fixed_endpoint_request_and_reads_raw_html(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    captured = {}

    def send(request, *, timeout_seconds):
        captured["request"] = request
        captured["timeout_seconds"] = timeout_seconds
        return (
            b"<html><body>unlocked</body></html>",
            {
                "x-brd-debug": (
                    "req_id=hl_test_123; billed=true; render=true; "
                    "destination_ip=203.0.113.8"
                )
            },
            200,
        )

    with patch("app.brightdata_backend._send", side_effect=send) as sender:
        result = scrape_url_sync(
            "https://93.184.216.34/page",
            source_id="allowed_source",
            timeout_ms=45_000,
            render=True,
        )

    sender.assert_called_once()
    request = captured["request"]
    assert request.full_url == BRIGHTDATA_UNLOCKER_URL
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer test-api-key"
    payload = json.loads(request.data)
    assert payload == {
        "zone": "test-zone",
        "url": "https://93.184.216.34/page",
        "format": "raw",
        "render": "true",
        "debug": True,
    }
    assert result.html == "<html><body>unlocked</body></html>"
    assert result.status_code == 200
    assert result.billable_requests == 1
    assert result.request_id == "hl_test_123"
    assert result.rendered is True
    assert result.budget_remaining == 9
    assert captured["timeout_seconds"] == 75.0


def test_unlocker_omits_render_for_raw_json_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    captured = {}

    def send(request, *, timeout_seconds):
        captured["request"] = request
        captured["timeout_seconds"] = timeout_seconds
        return (
            b'{"series":{"data":[{"dbfId":1}]}}',
            {"x-brd-debug": "req_id=hl_json_123; billed=true; render=false"},
            200,
        )

    with patch("app.brightdata_backend._send", side_effect=send):
        result = scrape_url_sync(
            "https://93.184.216.34/analytics/query/card_list/",
            source_id="allowed_source",
            render=False,
        )

    payload = json.loads(captured["request"].data)
    assert payload == {
        "zone": "test-zone",
        "url": "https://93.184.216.34/analytics/query/card_list/",
        "format": "raw",
        "debug": True,
    }
    assert result.html == '{"series":{"data":[{"dbfId":1}]}}'
    assert result.rendered is False


def test_unlocker_is_disabled_without_every_explicit_cost_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HS_BRIGHTDATA_UNLOCKER_ENABLED", "true")
    monkeypatch.setenv("HS_BRIGHTDATA_API_KEY", "test-api-key")
    monkeypatch.setenv("HS_BRIGHTDATA_UNLOCKER_ZONE", "test-zone")
    monkeypatch.setenv("HS_BRIGHTDATA_SOURCE_IDS", "allowed_source")
    monkeypatch.delenv("HS_BRIGHTDATA_MONTHLY_BILLABLE_LIMIT", raising=False)

    assert brightdata_configured_for_source("allowed_source") is False


def test_unlocker_fails_closed_for_invalid_monthly_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HS_BRIGHTDATA_UNLOCKER_ENABLED", "true")
    monkeypatch.setenv("HS_BRIGHTDATA_API_KEY", "test-api-key")
    monkeypatch.setenv("HS_BRIGHTDATA_UNLOCKER_ZONE", "test-zone")
    monkeypatch.setenv("HS_BRIGHTDATA_SOURCE_IDS", "allowed_source")
    monkeypatch.setenv("HS_BRIGHTDATA_MONTHLY_BILLABLE_LIMIT", "invalid")

    assert brightdata_configured_for_source("allowed_source") is False


def test_unlocker_fails_closed_until_usage_ledger_is_initialized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HS_BRIGHTDATA_UNLOCKER_ENABLED", "true")
    monkeypatch.setenv("HS_BRIGHTDATA_API_KEY", "test-api-key")
    monkeypatch.setenv("HS_BRIGHTDATA_UNLOCKER_ZONE", "test-zone")
    monkeypatch.setenv("HS_BRIGHTDATA_SOURCE_IDS", "allowed_source")
    monkeypatch.setenv("HS_BRIGHTDATA_MONTHLY_BILLABLE_LIMIT", "10")

    assert brightdata_configured_for_source("allowed_source") is False


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/page",
        "https://127.0.0.1/admin",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/admin",
        "https://user:password@example.com/page",
        "https://example.com/path\\segment",
        "https://example.com/path\nheader",
        "https://example.com/" + "a" * 4097,
    ],
)
def test_unlocker_rejects_non_public_https_targets_before_reserving_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    url: str,
) -> None:
    _configure(monkeypatch, tmp_path)

    with (
        patch("app.brightdata_backend._send") as sender,
        pytest.raises(BrightDataPolicyError, match="target is not allowed") as error,
    ):
        scrape_url_sync(url, source_id="allowed_source")

    sender.assert_not_called()
    assert url not in str(error.value)
    state = json.loads(
        (tmp_path / "brightdata" / "usage.json").read_text(encoding="utf-8")
    )
    assert state["attempts"] == 0
    assert state["reservations"] == {}


def test_unlocker_rejects_hostname_when_dns_contains_private_address(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    private_answer = [(2, 1, 6, "", ("10.0.0.4", 443))]

    with (
        patch("app.brightdata_backend.socket.getaddrinfo", return_value=private_answer),
        patch("app.brightdata_backend._send") as sender,
        pytest.raises(BrightDataPolicyError, match="target is not allowed"),
    ):
        scrape_url_sync(
            "https://allowed.example/page",
            source_id="allowed_source",
        )

    sender.assert_not_called()


def test_provider_http_error_never_reflects_remote_body_or_request_url() -> None:
    reflected_url = "https://provider.invalid/?key=reflected-secret"
    reflected_body = b"zone=secret-zone target=https://sensitive.invalid/"
    remote_error = urllib.error.HTTPError(
        reflected_url,
        401,
        "reflected-secret-zone",
        Message(),
        BytesIO(reflected_body),
    )
    opener = type(
        "FailingOpener",
        (),
        {"open": lambda self, request, timeout: (_ for _ in ()).throw(remote_error)},
    )()
    request = urllib.request.Request(BRIGHTDATA_UNLOCKER_URL)

    with (
        patch(
            "app.brightdata_backend.urllib.request.build_opener", return_value=opener
        ),
        pytest.raises(BrightDataRequestError) as error,
    ):
        _send(request, timeout_seconds=30)

    assert str(error.value) == "Bright Data API HTTP 401"
    assert reflected_url not in str(error.value)
    assert reflected_body.decode() not in str(error.value)


def test_unlocker_does_not_retry_or_reflect_sensitive_request_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    target = "https://93.184.216.34/private?token=target-secret"

    with (
        patch(
            "app.brightdata_backend._send",
            side_effect=BrightDataRequestError("Bright Data transport error"),
        ) as sender,
        pytest.raises(BrightDataRequestError) as error,
    ):
        scrape_url_sync(target, source_id="allowed_source")

    sender.assert_called_once()
    message = str(error.value)
    assert "test-api-key" not in message
    assert "test-zone" not in message
    assert target not in message
    state = json.loads(
        (tmp_path / "brightdata" / "usage.json").read_text(encoding="utf-8")
    )
    # Unknown transport outcomes are counted conservatively so the hard cap
    # cannot be exceeded after an ambiguous provider failure.
    assert state["billed_requests"] == 1
    assert state["consecutive_failures"] == 1


def test_unlocker_rejects_unsuccessful_api_response_without_exposing_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    reflected = "secret response https://private.invalid/?token=abc"
    with (
        patch(
            "app.brightdata_backend._send",
            side_effect=BrightDataRequestError(
                "Bright Data API HTTP 403",
                billed=False,
            ),
        ),
        pytest.raises(BrightDataRequestError, match="API HTTP 403") as error,
    ):
        scrape_url_sync(
            "https://93.184.216.34/page",
            source_id="allowed_source",
        )

    assert reflected not in str(error.value)
    state = json.loads(
        (tmp_path / "brightdata" / "usage.json").read_text(encoding="utf-8")
    )
    assert state["billed_requests"] == 0
    assert state["consecutive_failures"] == 1


def test_unlocker_honors_shorter_caller_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    response = b"<html>valid</html>"

    with patch(
        "app.brightdata_backend._send",
        return_value=(response, {"x-brd-debug": "billed=false"}, 200),
    ) as sender:
        scrape_url_sync(
            "https://93.184.216.34/page",
            source_id="allowed_source",
            timeout_ms=25_000,
        )

    assert sender.call_args.kwargs["timeout_seconds"] == 55.0


@pytest.mark.parametrize(
    "body",
    [
        "<html><title>Just a moment</title>challenges.cloudflare.com</html>",
        "<html>too short</html>",
    ],
)
def test_rejected_content_is_billed_but_counts_as_circuit_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: str,
) -> None:
    _configure(monkeypatch, tmp_path)
    response = body.encode()

    with (
        patch(
            "app.brightdata_backend._send",
            return_value=(response, {"x-brd-debug": "billed=true"}, 200),
        ),
        pytest.raises(
            BrightDataRequestError,
            match="response failed content validation",
        ),
    ):
        scrape_url_sync(
            "https://93.184.216.34/page",
            source_id="allowed_source",
            accept_html=lambda html: len(html) >= 2_000
            and "just a moment" not in html.lower(),
        )

    state = json.loads(
        (tmp_path / "brightdata" / "usage.json").read_text(encoding="utf-8")
    )
    assert state["billed_requests"] == 1
    assert state["successful_requests"] == 0
    assert state["consecutive_failures"] == 1


def test_fractional_api_status_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    response = b"<html>invalid</html>"

    with (
        patch(
            "app.brightdata_backend._send",
            return_value=(response, {"x-brd-debug": "billed=false"}, 200.9),
        ),
        pytest.raises(BrightDataRequestError, match="response is malformed"),
    ):
        scrape_url_sync(
            "https://93.184.216.34/page",
            source_id="allowed_source",
        )


def test_unlocker_returns_raw_target_json_without_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure(monkeypatch, tmp_path)
    raw_json = b'{"series":{"data":{"rows":[{"dbf_id":1}]}}}'

    with patch(
        "app.brightdata_backend._send",
        return_value=(raw_json, {"x-brd-debug": "billed=true"}, 200),
    ):
        result = scrape_url_sync(
            "https://93.184.216.34/data.json",
            source_id="allowed_source",
        )

    assert result.html == raw_json.decode()
    assert result.status_code == 200
