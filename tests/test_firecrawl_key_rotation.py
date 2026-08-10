from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app import firecrawl_keys as fk


@pytest.fixture()
def rotation_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HS_FIRECRAWL_KEY_ROTATION_CREDITS", "1000")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.delenv("HS_FIRECRAWL_API_KEY", raising=False)
    pool = ",".join(  # noqa: FLY002 - static fixture entries
        [
            "alpha@example.com|fc-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|1000",
            "beta@example.com|fc-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb|1000",
            "whitehorses0888@gmail.com|fc-cccccccccccccccccccccccccccccccc|5000",
        ]
    )
    monkeypatch.setenv("HS_FIRECRAWL_API_KEYS", pool)
    return tmp_path


def test_parse_firecrawl_api_keys(rotation_env):
    keys = fk.parse_firecrawl_api_keys()
    assert [item.label for item in keys] == [
        "alpha@example.com",
        "beta@example.com",
        "whitehorses0888@gmail.com",
    ]
    assert keys[-1].credit_limit == 5000


def test_malformed_firecrawl_key_error_never_exposes_raw_entry():
    canary = "fc-CANARYFIRECRAWLSECRET123"

    with pytest.raises(ValueError) as caught:
        fk.parse_firecrawl_api_keys(f"primary|{canary}:invalid|1000")

    message = str(caught.value)
    assert canary not in message
    assert "position 1" in message


def test_rotates_after_credit_limit(rotation_env):
    first = fk.acquire_firecrawl_key()
    assert first.key.label == "alpha@example.com"
    summary = fk.record_firecrawl_credits("alpha@example.com", 1000)
    assert summary["rotated"] is True
    assert summary["active_label"] == "beta@example.com"

    second = fk.acquire_firecrawl_key()
    assert second.key.label == "beta@example.com"
    fk.record_firecrawl_credits("beta@example.com", 1000)

    third = fk.acquire_firecrawl_key()
    assert third.key.label == "whitehorses0888@gmail.com"
    assert third.credits_remaining == 5000

    usage_file = Path(rotation_env) / "firecrawl" / "key-usage.json"
    payload = json.loads(usage_file.read_text(encoding="utf-8"))
    assert payload["keys"]["alpha@example.com"]["exhausted"] is True
    assert payload["keys"]["beta@example.com"]["exhausted"] is True
    assert payload["active_label"] == "whitehorses0888@gmail.com"


def test_mark_exhausted_skips_to_next(rotation_env):
    lease = fk.acquire_firecrawl_key()
    assert lease.key.label == "alpha@example.com"
    fk.mark_firecrawl_key_exhausted("alpha@example.com", reason="insufficient credits")
    nxt = fk.acquire_firecrawl_key()
    assert nxt.key.label == "beta@example.com"


def test_all_keys_exhausted_raises(rotation_env):
    for label in (
        "alpha@example.com",
        "beta@example.com",
        "whitehorses0888@gmail.com",
    ):
        fk.mark_firecrawl_key_exhausted(label, reason="done")
    with pytest.raises(RuntimeError, match="All Firecrawl API keys are exhausted"):
        fk.acquire_firecrawl_key()


def test_monthly_period_auto_reset(rotation_env, monkeypatch):
    monkeypatch.setenv("HS_FIRECRAWL_KEY_RESET_DAY", "22")
    monkeypatch.setattr(fk, "current_credit_period_id", lambda today=None: "2026-07-22")
    monkeypatch.setattr(fk, "next_credit_period_id", lambda today=None: "2026-08-22")

    fk.record_firecrawl_credits("alpha@example.com", 1000)
    snap = fk.firecrawl_key_usage_snapshot()
    assert snap["keys"][0]["exhausted"] is True
    assert snap["period_id"] == "2026-07-22"

    monkeypatch.setattr(fk, "current_credit_period_id", lambda today=None: "2026-08-22")
    monkeypatch.setattr(fk, "next_credit_period_id", lambda today=None: "2026-09-22")
    lease = fk.acquire_firecrawl_key()
    assert lease.key.label == "alpha@example.com"
    assert lease.credits_used == 0
    snap2 = fk.firecrawl_key_usage_snapshot()
    assert snap2["period_id"] == "2026-08-22"
    assert snap2["keys"][0]["credits_used"] == 0
    assert snap2["keys"][0]["exhausted"] is False


def test_period_id_around_reset_day():
    assert fk.current_credit_period_id(date(2026, 7, 22)) == "2026-07-22"
    assert fk.current_credit_period_id(date(2026, 7, 23)) == "2026-07-22"
    assert fk.current_credit_period_id(date(2026, 8, 21)) == "2026-07-22"
    assert fk.current_credit_period_id(date(2026, 8, 22)) == "2026-08-22"


def test_pool_unavailable_error_is_distinct_from_target_error():
    assert fk.is_firecrawl_pool_unavailable(
        RuntimeError("All Firecrawl API keys are exhausted (rotation limit reached)")
    )
    assert not fk.is_firecrawl_pool_unavailable(RuntimeError("target timed out"))
