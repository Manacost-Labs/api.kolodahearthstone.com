from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from app.sources import SOURCE_BY_ID


def _matrix_dataset(period: str) -> dict:
    return {
        "data": {
            "structured": {
                "current_catalog": {"criteria": {"period": period}},
            }
        }
    }


def test_cached_hsguru_catalog_wins_when_news_patch_catalog_lags() -> None:
    from app.hsguru_meta_matrix import resolve_current_patch_period

    with (
        patch("app.hsguru_meta_matrix.hsguru_current_patch_period", return_value=None),
        patch(
            "scripts.seed_hs_manacost_patches.current_patch_version",
            return_value="36.2.0",
        ),
    ):
        period = resolve_current_patch_period(_matrix_dataset("patch_36.2.2"))

    assert period == "patch_36.2.2"


def test_hsguru_post_patch_scope_preserves_filters_and_adds_current_period() -> None:
    from app.hsguru_post_patch import source_for_current_patch

    source = SOURCE_BY_ID["hsguru_matchups_wild_legend"]
    scoped = source_for_current_patch(
        source,
        cached_matrix=_matrix_dataset("patch_36.2.2"),
    )

    query = parse_qs(urlsplit(scoped.url).query)
    assert scoped.id == source.id
    assert query["format"] == ["1"]
    assert query["rank"] == ["legend"]
    assert query["min_archetype_sample"] == ["100"]
    assert query["min_matchup_sample"] == ["25"]
    assert query["period"] == ["patch_36.2.2"]


def test_fetch_source_scopes_hsguru_to_current_patch_during_early_mode() -> None:
    from app.fetcher import fetch_source

    source = SOURCE_BY_ID["hsguru_meta_standard_diamond_4to1"]

    async def return_url(_client, scoped_source, retry_on_auth_failure=True):
        return {"url": scoped_source.url}

    with (
        patch("app.hsguru_post_patch.policy_for", return_value=object()),
        patch(
            "app.hsguru_post_patch.load_dataset",
            return_value=_matrix_dataset("patch_36.2.2"),
        ),
        patch(
            "app.fetcher._fetch_source_with_captured_policy",
            new=AsyncMock(side_effect=return_url),
        ),
    ):
        result = asyncio.run(fetch_source(None, source))

    query = parse_qs(urlsplit(result["url"]).query)
    assert query["format"] == ["2"]
    assert query["rank"] == ["diamond_4to1"]
    assert query["min_games"] == ["100"]
    assert query["period"] == ["patch_36.2.2"]
