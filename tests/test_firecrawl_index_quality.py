from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.firecrawl_map import (
    MAX_HSREPLAY_CHILD_SITEMAPS,
    _fetch_sitemap,
    _sitemap_locations,
    _validate_map_size,
    build_hsreplay_index,
    fetch_hsreplay_scrape_do_map,
    refresh_hsreplay_map_and_index,
)
from app.scrape_do_backend import ScrapeDoAccountError, ScrapeDoTransientError


def _datasets() -> dict[str, dict]:
    return {
        "hsreplay_cards_legend_1d": {
            "cards": [
                {"id": f"C{idx}", "dbfId": idx, "name": f"Card {idx}", "type": "MINION"}
                for idx in range(120)
            ]
        },
        "hsreplay_battlegrounds_minions": {
            "minions": [
                {"id": f"BG{idx}", "dbfId": 10_000 + idx, "name": f"BG {idx}"}
                for idx in range(160)
            ]
        },
        "hsreplay_battlegrounds_heroes": {
            "heroes": [
                {"hero": f"Hero {idx}", "dbfId": 20_000 + idx}
                for idx in range(40)
            ]
        },
        "hsreplay_meta_archetypes_legend_eu_1d": {
            "classes": [
                {
                    "class": "MAGE",
                    "archetypes": [
                        {
                            "archetype": f"Deck {idx}",
                            "archetype_id": idx,
                            "url": f"https://hsreplay.net/archetypes/{idx}/deck-{idx}",
                        }
                        for idx in range(25)
                    ],
                }
            ]
        },
    }


def test_map_truncation_guard_rejects_collapsed_response() -> None:
    with pytest.raises(RuntimeError, match="truncation guard"):
        _validate_map_size(400, previous_count=3_800)


def test_map_truncation_guard_accepts_stable_response() -> None:
    _validate_map_size(3_700, previous_count=3_800)


def test_hsreplay_map_uses_only_approved_scrape_do_sitemaps() -> None:
    root_xml = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://hsreplay.net/sitemap-cards.xml</loc></sitemap>
      <sitemap><loc>https://hsreplay.net.evil.test/steal.xml</loc></sitemap>
    </sitemapindex>"""
    cards_xml = "<urlset>" + "".join(
        f"<url><loc>https://hsreplay.net/cards/{index}</loc></url>"
        for index in range(600)
    ) + "<url><loc>https://hsreplay.net.evil.test/card</loc></url></urlset>"

    with (
        patch(
            "app.firecrawl_map._fetch_sitemap",
            side_effect=[
                (root_xml, 1, False, 1),
                (cards_xml, 1, False, 1),
            ],
        ) as fetch,
        patch("app.firecrawl_map.load_hsreplay_map", return_value=None),
        patch("app.firecrawl_map._write_json") as write_json,
    ):
        result = fetch_hsreplay_scrape_do_map()

    assert result["provider"] == "scrape_do"
    assert result["schema_version"] == 2
    assert result["provider_policy"] == "scrape_do_only"
    assert result["url_count"] == 600
    assert result["scrape_do_requests"] == 2
    assert result["scrape_do_request_credits"] == 2
    assert "scrape_do_credits_remaining" not in result
    assert [call.args[0] for call in fetch.call_args_list] == [
        "https://hsreplay.net/sitemap.xml",
        "https://hsreplay.net/sitemap-cards.xml",
    ]
    write_json.assert_called_once()


def test_sitemap_locations_ignore_nested_image_and_video_locations() -> None:
    xml = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
      xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"
      xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">
      <url>
        <loc>https://hsreplay.net/cards/1</loc>
        <image:image><image:loc>https://hsreplay.net/images/card-1.png</image:loc></image:image>
        <video:video><video:content_loc>https://hsreplay.net/video/1.mp4</video:content_loc></video:video>
      </url>
    </urlset>"""

    assert _sitemap_locations(xml) == ("urlset", ["https://hsreplay.net/cards/1"])


def test_sitemap_uses_super_only_after_retryable_scrape_do_failure() -> None:
    success = SimpleNamespace(
        html="<urlset><url><loc>https://hsreplay.net/cards/1</loc></url></urlset>",
        request_cost=10,
        credits_remaining=99,
        super_proxy=True,
    )
    with patch(
        "app.firecrawl_map.scrape_url_sync",
        side_effect=[
            ScrapeDoTransientError("temporary", status_code=502),
            success,
        ],
    ) as scrape:
        result = _fetch_sitemap("https://hsreplay.net/sitemap.xml")

    assert result == (success.html, 10, True, 2)
    assert [call.kwargs["super_proxy"] for call in scrape.call_args_list] == [False, True]


def test_sitemap_uses_super_after_a_semantically_blocked_response() -> None:
    blocked = SimpleNamespace(
        html="<html><title>Access denied</title></html>",
        request_cost=1,
        credits_remaining=100,
        super_proxy=False,
    )
    success = SimpleNamespace(
        html="<urlset><url><loc>https://hsreplay.net/cards/1</loc></url></urlset>",
        request_cost=10,
        credits_remaining=99,
        super_proxy=True,
    )
    with patch(
        "app.firecrawl_map.scrape_url_sync",
        side_effect=[blocked, success],
    ) as scrape:
        result = _fetch_sitemap("https://hsreplay.net/sitemap.xml")

    assert result == (success.html, 11, True, 2)
    assert [call.kwargs["super_proxy"] for call in scrape.call_args_list] == [False, True]


def test_sitemap_does_not_bypass_scrape_do_account_error() -> None:
    with (
        patch(
            "app.firecrawl_map.scrape_url_sync",
            side_effect=ScrapeDoAccountError("account", status_code=401),
        ) as scrape,
        pytest.raises(ScrapeDoAccountError),
    ):
        _fetch_sitemap("https://hsreplay.net/sitemap.xml")

    scrape.assert_called_once()


def test_sitemap_rejects_unapproved_host_before_transport() -> None:
    with (
        patch("app.firecrawl_map.scrape_url_sync") as scrape,
        pytest.raises(ValueError, match="approved HTTPS host"),
    ):
        _fetch_sitemap("https://hsreplay.net.evil.test/sitemap.xml")

    scrape.assert_not_called()


def test_sitemap_index_rejects_unbounded_paid_fanout() -> None:
    entries = "".join(
        f"<sitemap><loc>https://hsreplay.net/sitemap-{index}.xml</loc></sitemap>"
        for index in range(MAX_HSREPLAY_CHILD_SITEMAPS + 1)
    )
    root_xml = f"<sitemapindex>{entries}</sitemapindex>"
    with (
        patch(
            "app.firecrawl_map._fetch_sitemap",
            return_value=(root_xml, 1, False, 1),
        ) as fetch,
        patch("app.firecrawl_map._write_json") as write_json,
        pytest.raises(RuntimeError, match="safety limit"),
    ):
        fetch_hsreplay_scrape_do_map()

    fetch.assert_called_once_with("https://hsreplay.net/sitemap.xml")
    write_json.assert_not_called()


def test_nested_sitemap_index_is_rejected_without_publication() -> None:
    root_xml = (
        "<sitemapindex><sitemap><loc>https://hsreplay.net/child.xml</loc>"
        "</sitemap></sitemapindex>"
    )
    nested_xml = (
        "<sitemapindex><sitemap><loc>https://hsreplay.net/grandchild.xml</loc>"
        "</sitemap></sitemapindex>"
    )
    with (
        patch(
            "app.firecrawl_map._fetch_sitemap",
            side_effect=[(root_xml, 1, False, 1), (nested_xml, 1, False, 1)],
        ),
        patch("app.firecrawl_map._write_json") as write_json,
        pytest.raises(RuntimeError, match="Nested HSReplay sitemap"),
    ):
        fetch_hsreplay_scrape_do_map()

    write_json.assert_not_called()


def test_map_and_index_are_published_only_after_both_validate() -> None:
    map_payload = {"ok": True, "fetched_at": "now", "url_count": 3_000}
    index_payload = {"ok": True, "map_fetched_at": "now", "counts": {}}

    with (
        patch(
            "app.firecrawl_map.fetch_hsreplay_scrape_do_map",
            return_value=map_payload,
        ) as fetch_map,
        patch(
            "app.firecrawl_map.build_hsreplay_index",
            return_value=index_payload,
        ) as build_index,
        patch("app.firecrawl_map._write_json") as write_json,
    ):
        result = refresh_hsreplay_map_and_index()

    fetch_map.assert_called_once_with(publish=False)
    build_index.assert_called_once_with(map_payload=map_payload, publish=False)
    assert [call.args[1] for call in write_json.call_args_list] == [
        map_payload,
        index_payload,
    ]
    assert result["map_url_count"] == 3_000


def test_derived_index_is_written_only_when_all_inputs_are_complete() -> None:
    datasets = _datasets()

    with (
        patch("app.firecrawl_map._structured", side_effect=lambda source_id: datasets[source_id]),
        patch("app.firecrawl_map.load_hsreplay_map", return_value={"fetched_at": "now", "url_count": 3000}),
        patch("app.firecrawl_map._write_json") as write_json,
    ):
        result = build_hsreplay_index()

    assert result["counts"]["standard_unique_archetypes"] == 25
    write_json.assert_called_once()


def test_derived_index_preserves_previous_file_when_one_input_collapses() -> None:
    datasets = _datasets()
    datasets["hsreplay_battlegrounds_minions"] = {"minions": []}

    with (
        patch("app.firecrawl_map._structured", side_effect=lambda source_id: datasets[source_id]),
        patch("app.firecrawl_map.load_hsreplay_map", return_value={"fetched_at": "now", "url_count": 3000}),
        patch("app.firecrawl_map._write_json") as write_json,
        pytest.raises(RuntimeError, match="battlegrounds_minions too small"),
    ):
        build_hsreplay_index()

    write_json.assert_not_called()


def test_derived_index_quality_counts_unique_entities_not_duplicate_rows() -> None:
    datasets = _datasets()
    duplicate = datasets["hsreplay_battlegrounds_minions"]["minions"][0]
    datasets["hsreplay_battlegrounds_minions"] = {"minions": [duplicate] * 200}

    with (
        patch("app.firecrawl_map._structured", side_effect=lambda source_id: datasets[source_id]),
        patch("app.firecrawl_map.load_hsreplay_map", return_value={"fetched_at": "now", "url_count": 3000}),
        patch("app.firecrawl_map._write_json") as write_json,
        pytest.raises(RuntimeError, match=r"battlegrounds_minions too small \(1 < 150\)"),
    ):
        build_hsreplay_index()

    write_json.assert_not_called()
