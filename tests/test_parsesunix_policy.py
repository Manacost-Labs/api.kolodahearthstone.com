from __future__ import annotations

import pytest

from app.config import (
    parsesunix_allowed_providers,
    parsesunix_max_body_bytes,
    parsesunix_max_concurrency,
    parsesunix_mode_for_source,
    parsesunix_scrape_do_daily_credit_limit,
    parsesunix_scrape_do_max_requests_per_refresh,
    parsesunix_scrape_do_strategies,
    parsesunix_state_dir,
    parsesunix_timeout_seconds,
)


def test_parsesunix_is_fail_closed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HS_PARSESUNIX_ENABLED",
        "HS_PARSESUNIX_SHADOW_SOURCE_IDS",
        "HS_PARSESUNIX_ACTIVE_SOURCE_IDS",
        "HS_PARSESUNIX_ALLOWED_PROVIDERS",
    ):
        monkeypatch.delenv(name, raising=False)

    assert parsesunix_mode_for_source("hsguru_matchups") == "legacy"
    assert parsesunix_allowed_providers() == ()


def test_source_modes_are_explicit_and_disjoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HS_PARSESUNIX_ENABLED", "true")
    monkeypatch.setenv("HS_PARSESUNIX_SHADOW_SOURCE_IDS", "metastats_decks")
    monkeypatch.setenv("HS_PARSESUNIX_ACTIVE_SOURCE_IDS", "hearthstone_decks")

    assert parsesunix_mode_for_source("metastats_decks") == "shadow"
    assert parsesunix_mode_for_source("hearthstone_decks") == "parsesunix"
    assert parsesunix_mode_for_source("firestone_standard") == "legacy"


def test_overlapping_source_modes_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HS_PARSESUNIX_ENABLED", "true")
    monkeypatch.setenv("HS_PARSESUNIX_SHADOW_SOURCE_IDS", "metastats_decks")
    monkeypatch.setenv("HS_PARSESUNIX_ACTIVE_SOURCE_IDS", "metastats_decks")

    with pytest.raises(ValueError, match="overlap"):
        parsesunix_mode_for_source("metastats_decks")


def test_only_scrape_do_can_be_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HS_PARSESUNIX_ALLOWED_PROVIDERS", "scrape.do")
    assert parsesunix_allowed_providers() == ("scrape.do",)

    monkeypatch.setenv("HS_PARSESUNIX_ALLOWED_PROVIDERS", "scrape.do,brightdata")
    with pytest.raises(ValueError, match="brightdata"):
        parsesunix_allowed_providers()


def test_invalid_boolean_does_not_enable_the_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HS_PARSESUNIX_ENABLED", "1")

    with pytest.raises(ValueError, match="must be 'true' or 'false'"):
        parsesunix_mode_for_source("metastats_decks")


def test_resource_limits_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HS_PARSESUNIX_MAX_CONCURRENCY", "9")
    with pytest.raises(ValueError, match="between 1 and 8"):
        parsesunix_max_concurrency()

    monkeypatch.setenv("HS_PARSESUNIX_TIMEOUT_SECONDS", "301")
    with pytest.raises(ValueError, match="between 5 and 300"):
        parsesunix_timeout_seconds()

    monkeypatch.setenv("HS_PARSESUNIX_MAX_BODY_BYTES", "1048575")
    with pytest.raises(ValueError, match="between 1048576 and 33554432"):
        parsesunix_max_body_bytes()

    monkeypatch.setenv("HS_PARSESUNIX_SCRAPE_DO_DAILY_CREDIT_LIMIT", "-1")
    with pytest.raises(ValueError, match="between 0 and 10000000"):
        parsesunix_scrape_do_daily_credit_limit()

    monkeypatch.setenv("HS_PARSESUNIX_SCRAPE_DO_MAX_REQUESTS_PER_REFRESH", "1001")
    with pytest.raises(ValueError, match="between 0 and 1000"):
        parsesunix_scrape_do_max_requests_per_refresh()


def test_scrape_do_paid_layer_is_fail_closed_and_strategy_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "HS_PARSESUNIX_SCRAPE_DO_DAILY_CREDIT_LIMIT",
        "HS_PARSESUNIX_SCRAPE_DO_MAX_REQUESTS_PER_REFRESH",
        "HS_PARSESUNIX_SCRAPE_DO_STRATEGIES",
    ):
        monkeypatch.delenv(name, raising=False)

    assert parsesunix_scrape_do_daily_credit_limit() == 0
    assert parsesunix_scrape_do_max_requests_per_refresh() == 0
    assert parsesunix_scrape_do_strategies() == ("normal",)

    monkeypatch.setenv(
        "HS_PARSESUNIX_SCRAPE_DO_STRATEGIES",
        "normal,render",
    )
    assert parsesunix_scrape_do_strategies() == ("normal", "render")

    monkeypatch.setenv(
        "HS_PARSESUNIX_SCRAPE_DO_STRATEGIES",
        "normal,brightdata",
    )
    with pytest.raises(ValueError, match="brightdata"):
        parsesunix_scrape_do_strategies()


def test_state_is_isolated_below_the_api_data_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))

    assert parsesunix_state_dir() == tmp_path / "parsesunix"
