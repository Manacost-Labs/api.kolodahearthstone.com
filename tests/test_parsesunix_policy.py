from __future__ import annotations

import pytest

from app.config import (
    parsesunix_allowed_providers,
    parsesunix_max_body_bytes,
    parsesunix_max_concurrency,
    parsesunix_mode_for_source,
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


def test_state_is_isolated_below_the_api_data_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))

    assert parsesunix_state_dir() == tmp_path / "parsesunix"
