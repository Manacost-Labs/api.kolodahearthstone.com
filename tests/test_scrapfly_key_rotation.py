from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Self
from unittest.mock import patch

import pytest

from app import scrapfly_keys as sk
from app.scrapfly_backend import _scrape_once


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.headers: dict[str, str] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


@pytest.fixture()
def rotation_env(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HS_SCRAPFLY_KEY_ROTATION_CREDITS", "1000")
    monkeypatch.delenv("SCRAPFLY_API_KEY", raising=False)
    monkeypatch.delenv("HS_SCRAPFLY_API_KEY", raising=False)
    monkeypatch.setenv(
        "HS_SCRAPFLY_API_KEYS",
        (
            "primary|scp-live-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1|1000,"
            "reserve|scp-live-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2|500"
        ),
    )
    return tmp_path


def test_parse_scrapfly_api_keys(rotation_env: Path) -> None:
    keys = sk.parse_scrapfly_api_keys()
    assert [item.label for item in keys] == ["primary", "reserve"]
    assert keys[1].credit_limit == 500


def test_malformed_scrapfly_key_error_never_exposes_raw_entry() -> None:
    canary = "scp-live-CANARYSCRAPFLYSECRET123"

    with pytest.raises(ValueError) as caught:
        sk.parse_scrapfly_api_keys(f"primary|{canary}:invalid|1000")

    message = str(caught.value)
    assert canary not in message
    assert "position 1" in message


def test_scrapfly_remote_errors_never_expose_key_url_or_payload() -> None:
    provider_credential = "-".join(("scp", "live", "CANARYSCRAPFLYAPIKEY123"))
    target_canary = "TARGETQUERYCANARY456"
    target_url = f"https://example.com/page?private={target_canary}"
    reflected = f"request key={provider_credential} url={target_url}"

    with (
        patch.object(
            urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError(
                "https://api.scrapfly.io/scrape"
                f"?key={provider_credential}&url={target_url}",
                403,
                "forbidden",
                {},
                io.BytesIO(reflected.encode("utf-8")),
            ),
        ),
        pytest.raises(RuntimeError) as caught,
    ):
        _scrape_once(target_url, api_key=provider_credential)

    message = str(caught.value)
    assert provider_credential not in message
    assert target_canary not in message
    assert reflected not in message
    assert message == "Scrapfly HTTP 403"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True

    with (
        patch.object(
            urllib.request,
            "urlopen",
            return_value=_Response(
                {
                    "result": {
                        "success": False,
                        "error": reflected,
                        "status_code": 403,
                    }
                }
            ),
        ),
        pytest.raises(RuntimeError) as caught_payload,
    ):
        _scrape_once(target_url, api_key=provider_credential)

    payload_message = str(caught_payload.value)
    assert provider_credential not in payload_message
    assert target_canary not in payload_message
    assert reflected not in payload_message
    assert payload_message == "Scrapfly scrape failed (HTTP 403)"


def test_rotates_after_credit_limit(rotation_env: Path) -> None:
    first = sk.acquire_scrapfly_key()
    assert first.key.label == "primary"
    summary = sk.record_scrapfly_credits("primary", 1000)
    assert summary["rotated"] is True
    assert summary["active_label"] == "reserve"

    second = sk.acquire_scrapfly_key()
    assert second.key.label == "reserve"

    usage_file = rotation_env / "scrapfly" / "key-usage.json"
    payload = json.loads(usage_file.read_text(encoding="utf-8"))
    assert payload["keys"]["primary"]["exhausted"] is True


def test_all_keys_exhausted_raises(rotation_env: Path) -> None:
    sk.mark_scrapfly_key_exhausted("primary", reason="done")
    sk.mark_scrapfly_key_exhausted("reserve", reason="done")
    with pytest.raises(RuntimeError, match="All Scrapfly API keys are exhausted"):
        sk.acquire_scrapfly_key()
