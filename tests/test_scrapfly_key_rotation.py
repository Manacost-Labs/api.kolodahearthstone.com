from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import scrapfly_keys as sk


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
