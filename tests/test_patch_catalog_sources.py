from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from scripts import seed_hs_manacost_patches as patch_seed
from scripts.seed_hs_manacost_patches import (
    combined_patch_catalog,
    current_patch_version,
    fetch_text,
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


def test_latest_official_patches_prefers_current_sticky_feed_over_stale_json_ld() -> None:
    sticky = [
        {
            "title": "36.4 Patch Notes",
            "defaultUrl": "https://playhearthstone.com/en-us/blog/24293283",
            "publish": 1787590500000,
            "updated_at": "2026-08-24T18:03:01.810Z",
            "summary": "The new Class Sets and Arena season.",
        }
    ]
    sticky_script = (
        '<script type="text/javascript">var stickyBlogList = '
        f"{json.dumps(sticky)};</script>"
    )
    page = OFFICIAL_HTML.replace(
        '<script type="application/ld+json">',
        f'{sticky_script}<script type="application/ld+json">',
        1,
    )

    with patch("scripts.seed_hs_manacost_patches.fetch_text", return_value=page):
        patches = latest_official_patches(None)

    assert [item["version"] for item in patches] == ["36.4", "36.0", "35.6.2"]
    assert patches[0]["official_url"] == (
        "https://playhearthstone.com/en-us/blog/24293283"
    )
    assert patches[0]["official_published_at"] == "2026-08-24T16:55:00+00:00"
    assert patches[0]["official_summary"] == "The new Class Sets and Arena season."


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


def test_fetch_text_retries_a_transient_network_error() -> None:
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"complete"

    with (
        patch(
            "scripts.seed_hs_manacost_patches._open_url",
            side_effect=[urllib.error.URLError("temporary"), response],
        ) as open_url,
        patch("scripts.seed_hs_manacost_patches.time.sleep") as sleep,
    ):
        assert fetch_text("https://example.test/data") == "complete"

    assert open_url.call_count == 2
    sleep.assert_called_once_with(1.0)


def test_fetch_text_does_not_retry_a_not_found_response() -> None:
    error = urllib.error.HTTPError(
        "https://example.test/missing",
        404,
        "Not Found",
        hdrs=None,
        fp=None,
    )
    with (
        patch("scripts.seed_hs_manacost_patches._open_url", side_effect=error) as open_url,
        patch("scripts.seed_hs_manacost_patches.time.sleep") as sleep,
        pytest.raises(urllib.error.HTTPError),
    ):
        fetch_text("https://example.test/missing")

    open_url.assert_called_once()
    sleep.assert_not_called()


def test_fetch_text_stops_after_bounded_transient_retries() -> None:
    with (
        patch(
            "scripts.seed_hs_manacost_patches._open_url",
            side_effect=urllib.error.URLError("temporary"),
        ) as open_url,
        patch("scripts.seed_hs_manacost_patches.time.sleep") as sleep,
        pytest.raises(urllib.error.URLError),
    ):
        fetch_text("https://example.test/data")

    assert open_url.call_count == patch_seed.FETCH_ATTEMPTS
    assert [call.args[0] for call in sleep.call_args_list] == [1.0, 3.0]


def test_safe_redirect_handler_rejects_external_location_before_request() -> None:
    handler = patch_seed.SafeRedirectHandler(patch_seed.ALLOWED_HS_MANACOST_HOSTS)
    request = patch_seed.urllib.request.Request("https://hs-manacost.ru/sitemap.xml")

    with pytest.raises(urllib.error.HTTPError, match="approved HTTPS host"):
        handler.redirect_request(
            request,
            MagicMock(),
            302,
            "Found",
            {},
            "https://169.254.169.254/latest/meta-data/",
        )


def test_hs_manacost_sitemap_rejects_external_post_sitemap_before_fetch() -> None:
    root_xml = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://hs-manacost.ru/post-sitemap.xml</loc></sitemap>
      <sitemap><loc>https://hs-manacost.ru.evil.test/post-sitemap.xml</loc></sitemap>
    </sitemapindex>"""
    with (
        patch.object(patch_seed, "fetch_text", return_value=root_xml) as fetch,
        pytest.raises(RuntimeError, match="unapproved host"),
    ):
        patch_seed.hs_manacost_post_urls()

    fetch.assert_called_once_with(patch_seed.HS_MANACOST_SITEMAP_URL)


def test_main_continues_after_one_patch_detail_fails(capsys: pytest.CaptureFixture[str]) -> None:
    catalog = [{"version": "36.2"}, {"version": "36.1"}]
    good_patch = {
        "version": "36.1",
        "wiki_rank": 1,
        "hs_manacost_version": "36.1",
        "wiki_url": "https://hearthstone.wiki.gg/wiki/Patch_36.1",
        "official_url": None,
        "title": "Patch 36.1",
        "source_url": "https://hs-manacost.ru/patch-36-1/",
        "match_state": "matched",
    }

    with (
        patch.object(patch_seed.sys, "argv", ["seed_hs_manacost_patches.py", "--all"]),
        patch.object(patch_seed, "combined_patch_catalog", return_value=catalog),
        patch.object(patch_seed, "count_patches", return_value=2),
        patch.object(patch_seed, "validate_full_catalog"),
        patch.object(patch_seed, "hs_manacost_post_urls", return_value=[]),
        patch.object(
            patch_seed,
            "find_patch_url",
            side_effect=[
                ("https://hs-manacost.ru/patch-36-2/", "36.2"),
                ("https://hs-manacost.ru/patch-36-1/", "36.1"),
            ],
        ),
        patch.object(patch_seed, "build_patch", side_effect=[TimeoutError(), good_patch]),
        patch.object(patch_seed, "upsert_patch") as upsert_patch,
        patch.object(patch_seed, "delete_patches_not_in", return_value=0) as delete_stale,
    ):
        exit_code = patch_seed.main()

    result = json.loads(capsys.readouterr().out)
    assert exit_code == patch_seed.PARTIAL_EXIT_CODE
    assert result["ok"] is False
    assert result["state"] == "partial"
    assert result["failed_versions"] == [{"version": "36.2", "error_type": "TimeoutError"}]
    assert result["stored_count"] == 1
    assert result["not_attempted_count"] == 0
    upsert_patch.assert_called_once_with(good_patch)
    delete_stale.assert_not_called()


def test_main_opens_circuit_after_repeated_transient_detail_failures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog = [{"version": f"36.{minor}"} for minor in range(4, 0, -1)]

    with (
        patch.object(patch_seed.sys, "argv", ["seed_hs_manacost_patches.py", "--all"]),
        patch.object(patch_seed, "combined_patch_catalog", return_value=catalog),
        patch.object(patch_seed, "count_patches", return_value=4),
        patch.object(patch_seed, "validate_full_catalog"),
        patch.object(patch_seed, "hs_manacost_post_urls", return_value=[]),
        patch.object(
            patch_seed,
            "find_patch_url",
            side_effect=[(f"https://hs-manacost.ru/patch-36-{minor}/", f"36.{minor}") for minor in range(4, 0, -1)],
        ),
        patch.object(patch_seed, "build_patch", side_effect=TimeoutError("upstream timeout")) as build,
        patch.object(patch_seed, "upsert_patch") as upsert_patch,
        patch.object(patch_seed, "delete_patches_not_in") as delete_stale,
    ):
        exit_code = patch_seed.main()

    result = json.loads(capsys.readouterr().out)
    assert exit_code == patch_seed.PARTIAL_EXIT_CODE
    assert result["state"] == "partial"
    assert result["circuit_open"] is True
    assert result["failed_count"] == patch_seed.MAX_CONSECUTIVE_DETAIL_FAILURES
    assert result["not_attempted_versions"] == ["36.1"]
    assert build.call_count == patch_seed.MAX_CONSECUTIVE_DETAIL_FAILURES
    upsert_patch.assert_not_called()
    delete_stale.assert_not_called()


def test_main_stops_before_processing_when_global_deadline_is_reached(
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog = [{"version": "36.2"}, {"version": "36.1"}]

    with (
        patch.object(patch_seed.sys, "argv", ["seed_hs_manacost_patches.py", "--all"]),
        patch.object(patch_seed.time, "monotonic", side_effect=[0.0, patch_seed.MAX_RUN_SECONDS]),
        patch.object(patch_seed, "combined_patch_catalog", return_value=catalog),
        patch.object(patch_seed, "count_patches", return_value=2),
        patch.object(patch_seed, "validate_full_catalog"),
        patch.object(patch_seed, "hs_manacost_post_urls", return_value=[]),
        patch.object(patch_seed, "find_patch_url") as find_patch,
        patch.object(patch_seed, "upsert_patch") as upsert_patch,
        patch.object(patch_seed, "delete_patches_not_in") as delete_stale,
    ):
        exit_code = patch_seed.main()

    result = json.loads(capsys.readouterr().out)
    assert exit_code == patch_seed.PARTIAL_EXIT_CODE
    assert result["deadline_reached"] is True
    assert result["not_attempted_versions"] == ["36.2", "36.1"]
    find_patch.assert_not_called()
    upsert_patch.assert_not_called()
    delete_stale.assert_not_called()


def test_main_never_downgrades_a_previously_matched_patch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    catalog = [{"version": "36.2"}]
    existing = {"version": "36.2", "match_state": "matched"}

    with (
        patch.object(patch_seed.sys, "argv", ["seed_hs_manacost_patches.py", "--all"]),
        patch.object(patch_seed, "combined_patch_catalog", return_value=catalog),
        patch.object(patch_seed, "count_patches", return_value=1),
        patch.object(patch_seed, "validate_full_catalog"),
        patch.object(patch_seed, "hs_manacost_post_urls", return_value=[]),
        patch.object(patch_seed, "find_patch_url", return_value=(None, None)),
        patch.object(patch_seed, "get_patch", return_value=existing),
        patch.object(patch_seed, "upsert_patch") as upsert_patch,
        patch.object(patch_seed, "delete_patches_not_in") as delete_stale,
    ):
        exit_code = patch_seed.main()

    result = json.loads(capsys.readouterr().out)
    assert exit_code == patch_seed.PARTIAL_EXIT_CODE
    assert result["preserved_matched_versions"] == ["36.2"]
    assert result["failed_versions"] == [
        {"version": "36.2", "error_type": "PreviouslyMatchedArticleMissing"}
    ]
    upsert_patch.assert_not_called()
    delete_stale.assert_not_called()
