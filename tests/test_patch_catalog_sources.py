from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts.seed_hs_manacost_patches import (
    combined_patch_catalog,
    current_patch_version,
    latest_official_patches,
    validate_full_catalog,
)


OFFICIAL_HTML = """
<html><head>
<script type="application/ld+json">
{
  "mainEntity": {
    "itemListElement": [
      {
        "headline": "36.0 Patch Notes",
        "url": "https://playhearthstone.com/en-us/blog/24287396",
        "datePublished": "2026-06-29T16:55:00+00:00",
        "dateModified": "2026-06-29T19:21:00+00:00",
        "description": "The new expansion patch."
      },
      {"headline": "Expansion Launch Guide", "url": "https://example.test/guide"},
      {"headline": "35.6.2 Patch Notes", "url": "https://example.test/35-6-2"}
    ]
  }
}
</script>
</head></html>
"""


def test_latest_official_patches_reads_json_ld() -> None:
    with patch("scripts.seed_hs_manacost_patches.fetch_text", return_value=OFFICIAL_HTML):
        patches = latest_official_patches(None)

    assert [item["version"] for item in patches] == ["36.0", "35.6.2"]
    assert patches[0]["official_published_at"] == "2026-06-29T16:55:00+00:00"
    assert patches[0]["official_summary"] == "The new expansion patch."


def test_combined_catalog_puts_official_new_patch_before_lagging_wiki() -> None:
    with (
        patch(
            "scripts.seed_hs_manacost_patches.latest_official_patches",
            return_value=[
                {"version": "36.0", "official_url": "https://official.test/36"},
                {"version": "35.6.2", "official_url": "https://official.test/35-6-2"},
            ],
        ),
        patch(
            "scripts.seed_hs_manacost_patches.latest_wiki_versions",
            return_value=["35.6.2.245096", "35.6.0.243002"],
        ),
    ):
        catalog = combined_patch_catalog(None)

    assert [item["version"] for item in catalog] == [
        "36.0",
        "35.6.2.245096",
        "35.6.0.243002",
    ]
    assert catalog[0]["official_url"] == "https://official.test/36"
    assert catalog[1]["official_url"] == "https://official.test/35-6-2"


def test_current_patch_version_uses_newer_wiki_build_when_news_index_lags() -> None:
    with patch(
        "scripts.seed_hs_manacost_patches.combined_patch_catalog",
        return_value=[{"version": "36.2.0.248348"}],
    ):
        assert current_patch_version() == "36.2.0"


def test_full_catalog_guard_rejects_layout_truncation_before_deletion() -> None:
    truncated = [{"version": f"35.{idx}"} for idx in range(20)]

    with pytest.raises(RuntimeError, match="truncation guard"):
        validate_full_catalog(truncated, existing_count=300)


def test_full_catalog_guard_accepts_complete_history() -> None:
    catalog = [{"version": f"{major}.{minor}"} for major in range(1, 32) for minor in range(10)]

    validate_full_catalog(catalog, existing_count=300)
