from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, call, patch

import pytest

from app import hsguru_decks
from app.firecrawl_backend import FirecrawlScrape
from app.hsguru_decks import parse_hsguru_decks_html

EVENLOCK_CODE = "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63pBdCeBu6hBom1BoSZB+C+B43cBwAA"
SECOND_EVENLOCK_CODE = "AAEBAf0GBM30Aof7A6nhBq3ZBw3XzgOI9APG+QPH+QP++gOt6QXQngbuoQaJtQacwQaEmQfb1weN3AcAAA=="


def _card(
    title: str, class_name: str, deck_code: str, games: int, winrate: float
) -> str:
    return f"""
      <div id="deck_stats-42">
        <div class="decklist-info {class_name}">
          <button data-clipboard-text="### {title}\n# Format: Wild\n{deck_code}\n# You can view this deck at https://www.hsguru.com/deck/42\n"></button>
        </div>
        <span class="tag column"><span>{winrate}</span></span>
        <div>Games: {games}</div>
      </div>
    """


def test_parser_returns_only_the_exact_archetype_and_best_sample() -> None:
    html = (
        _card("Mug Shaman", "shaman", EVENLOCK_CODE, 9999, 70.0)
        + _card("Evenlock", "warlock", EVENLOCK_CODE, 4227, 61.1)
        + _card("Evenlock", "warlock", SECOND_EVENLOCK_CODE, 337, 68.5)
    )

    rows = parse_hsguru_decks_html(html, archetype="Evenlock", format_name="wild")

    assert len(rows) == 2
    assert rows[0]["archetype"] == "Evenlock"
    assert rows[0]["class"] == "Warlock"
    assert rows[0]["deck_code"] == EVENLOCK_CODE
    assert rows[0]["games"] == 4227


def test_parser_never_substitutes_another_archetype() -> None:
    html = _card("Mug Shaman", "shaman", EVENLOCK_CODE, 9999, 70.0)

    assert (
        parse_hsguru_decks_html(html, archetype="Big Shaman", format_name="wild") == []
    )


def test_catalog_parser_accepts_multiple_exact_deck_titles() -> None:
    html = _card("Evenlock", "warlock", EVENLOCK_CODE, 4227, 61.1) + _card(
        "Mug Shaman", "shaman", SECOND_EVENLOCK_CODE, 999, 59.0
    )

    rows = parse_hsguru_decks_html(html, archetype="", format_name="wild")

    assert [row["archetype"] for row in rows] == ["Evenlock", "Mug Shaman"]


def test_exact_lookup_prefers_the_broad_legend_catalog() -> None:
    catalog_row = {
        "archetype": "XL Mill Druid",
        "format": "Wild",
        "deck_code": EVENLOCK_CODE,
        "games": 319,
        "win_rate": 53.6,
    }
    live_lookup = AsyncMock(return_value=[])

    with (
        patch.object(
            hsguru_decks, "cached_hsguru_catalog_decks", return_value=[catalog_row]
        ),
        patch.object(hsguru_decks, "_fetch_exact", live_lookup),
    ):
        rows = asyncio.run(
            hsguru_decks.exact_hsguru_decks("XL Mill Druid", "wild", "legend")
        )

    assert rows == [catalog_row]
    live_lookup.assert_not_awaited()


def test_all_rank_catalog_supplies_deck_code_without_mismatched_rank_stats() -> None:
    all_rank_row = {
        "archetype": "XL Mill Druid",
        "format": "Wild",
        "deck_code": EVENLOCK_CODE,
        "games": 319,
        "win_rate": 53.6,
    }
    with patch.object(
        hsguru_decks, "_catalog_rows", return_value=[all_rank_row]
    ) as catalog_rows:
        rows = hsguru_decks.cached_hsguru_catalog_decks(
            "XL Mill Druid",
            "wild",
            "diamond_4to1",
        )

    assert rows == [
        {
            **all_rank_row,
            "games": None,
            "score": None,
            "win_rate": None,
            "sample_rank": "all",
        }
    ]
    catalog_rows.assert_called_once_with(
        "wild",
        "all",
        expected_period="past_30_days",
    )


def test_legend_catalog_is_preferred_before_all_rank_fallback() -> None:
    legend_row = {
        "archetype": "XL Mill Druid",
        "format": "Wild",
        "deck_code": EVENLOCK_CODE,
        "games": 319,
        "win_rate": 53.6,
    }
    with patch.object(
        hsguru_decks, "_catalog_rows", return_value=[legend_row]
    ) as catalog_rows:
        rows = hsguru_decks.cached_hsguru_catalog_decks(
            "XL Mill Druid", "wild", "legend"
        )

    assert rows == [legend_row]
    catalog_rows.assert_called_once_with(
        "wild",
        "legend",
        expected_period="past_30_days",
    )


def test_all_rank_lookup_uses_its_preloaded_catalog() -> None:
    catalog_row = {
        "archetype": "XL HL Exodia Mage",
        "format": "Wild",
        "deck_code": EVENLOCK_CODE,
        "games": 412,
        "win_rate": 54.2,
    }
    with patch.object(
        hsguru_decks, "_catalog_rows", return_value=[catalog_row]
    ) as catalog_rows:
        rows = hsguru_decks.cached_hsguru_catalog_decks(
            "XL HL Exodia Mage", "wild", "all"
        )

    assert rows == [catalog_row]
    catalog_rows.assert_called_once_with(
        "wild",
        "all",
        expected_period="past_30_days",
    )


def test_all_rank_catalog_targets_meta_archetypes_missing_from_all_rank_cache() -> None:
    meta_payload = {
        "data": {"tables": [{"rows": [["Popular Mage"], ["XL HL Exodia Mage"]]}]},
    }
    with patch.object(
        hsguru_decks,
        "load_resolved_public_dataset",
        return_value=meta_payload,
    ):
        archetypes = hsguru_decks._all_rank_catalog_archetypes(
            "wild",
            [{"archetype": "Popular Mage"}],
        )

    assert archetypes == ["XL HL Exodia Mage"]


def test_fresh_all_rank_catalog_is_reused_without_firecrawl() -> None:
    cached_rows = [
        {
            "archetype": "XL HL Exodia Mage",
            "format": "Wild",
            "deck_code": EVENLOCK_CODE,
        }
    ]
    scrape = AsyncMock()
    with (
        patch.object(hsguru_decks, "_catalog_rows", return_value=cached_rows),
        patch.object(hsguru_decks, "_catalog_snapshot", return_value=None),
        patch.object(
            hsguru_decks,
            "_meta_archetypes",
            return_value=["XL HL Exodia Mage"],
        ),
        patch.object(hsguru_decks, "_fetch_catalog_chunks", scrape),
    ):
        rows = asyncio.run(hsguru_decks.refresh_hsguru_deck_catalog("wild", "all"))

    assert rows == cached_rows
    scrape.assert_not_awaited()


def test_catalog_chunks_limit_each_solver_page_to_twenty_archetypes() -> None:
    archetypes = [f"Deck {index}" for index in range(45)]

    assert hsguru_decks._catalog_chunks(archetypes) == [
        archetypes[:20],
        archetypes[20:40],
        archetypes[40:],
    ]


def test_empty_catalog_requires_explicit_hsguru_zero_marker() -> None:
    incomplete = hsguru_decks._CatalogPage(
        html='<div id="deck_stats_viewport"></div>',
        backend="flaresolverr",
        request_credits=0,
    )
    fetch_page = AsyncMock(return_value=incomplete)
    with (
        patch.object(hsguru_decks, "_fetch_catalog_page", fetch_page),
        pytest.raises(RuntimeError, match="could not be verified"),
    ):
        asyncio.run(
            hsguru_decks._fetch_catalog_chunk(
                "standard",
                ["Quiet Mage"],
                batch_number=1,
                period="patch_36.2.0",
                min_games=10,
                limit=20,
            )
        )


def test_empty_catalog_accepts_hsguru_explicit_zero_marker() -> None:
    confirmed_empty = hsguru_decks._CatalogPage(
        html=(
            '<div id="deck_stats_viewport"></div>'
            '<div class="notification is-warning">'
            "No decks available for these filters. Maybe try changing one."
            "</div>"
        ),
        backend="flaresolverr",
        request_credits=0,
    )
    with patch.object(
        hsguru_decks,
        "_fetch_catalog_page",
        AsyncMock(return_value=confirmed_empty),
    ):
        rows, credits, backend, _attempts = asyncio.run(
            hsguru_decks._fetch_catalog_chunk(
                "standard",
                ["Quiet Mage"],
                batch_number=1,
                period="patch_36.2.0",
                min_games=10,
                limit=20,
            )
        )

    assert rows == []
    assert credits == 0
    assert backend == "flaresolverr"


def test_partial_all_rank_catalog_fetches_only_missing_archetypes() -> None:
    existing = [{"archetype": "Popular Mage", "deck_code": EVENLOCK_CODE}]
    fetched = [{"archetype": "Rare Mage", "deck_code": SECOND_EVENLOCK_CODE}]
    fetch_chunks = AsyncMock(
        return_value=(
            fetched,
            1,
            ["scrape_do_super"],
            [{"backend": "scrape_do_super", "request_credits": 1}],
        )
    )
    with (
        patch.object(hsguru_decks, "_catalog_rows", return_value=existing),
        patch.object(hsguru_decks, "_catalog_snapshot", return_value=None),
        patch.object(
            hsguru_decks,
            "_meta_archetypes",
            return_value=["Popular Mage", "Rare Mage"],
        ),
        patch.object(hsguru_decks, "_fetch_catalog_chunks", fetch_chunks),
        patch.object(hsguru_decks, "_write_catalog") as write_catalog,
    ):
        rows = asyncio.run(hsguru_decks._refresh_all_rank_catalog("wild"))

    assert {row["archetype"] for row in rows} == {"Popular Mage", "Rare Mage"}
    fetch_chunks.assert_awaited_once_with(
        "wild",
        ["Rare Mage"],
        period="past_30_days",
    )
    write_catalog.assert_called_once()


def test_all_rank_catalog_records_valid_zero_sample_without_failing() -> None:
    existing = [
        {
            "archetype": "Popular Mage",
            "format": "Wild",
            "deck_code": EVENLOCK_CODE,
        }
    ]
    empty_fetch = (
        [],
        0,
        ["flaresolverr"],
        [
            {
                "backend": "flaresolverr",
                "state": "accepted",
                "request_credits": 0,
            }
        ],
    )
    fetch_chunks = AsyncMock(side_effect=[empty_fetch, empty_fetch])
    with (
        patch.object(hsguru_decks, "_current_deck_period", return_value="patch_36.2.0"),
        patch.object(hsguru_decks, "_catalog_rows", return_value=existing),
        patch.object(hsguru_decks, "_catalog_snapshot", return_value=None),
        patch.object(
            hsguru_decks,
            "_meta_archetypes",
            return_value=["Popular Mage", "No Sample Mage"],
        ),
        patch.object(hsguru_decks, "_fetch_catalog_chunks", fetch_chunks),
        patch.object(hsguru_decks, "_write_catalog") as write_catalog,
    ):
        rows = asyncio.run(hsguru_decks._refresh_all_rank_catalog("wild"))

    assert rows == existing
    assert fetch_chunks.await_count == 2
    assert write_catalog.call_args.kwargs["missing_archetypes"] == []
    assert write_catalog.call_args.kwargs["zero_sample_archetypes"] == [
        "No Sample Mage"
    ]
    assert write_catalog.call_args.kwargs["sample_state"] == "sparse_post_patch"


def test_all_rank_retry_budget_advances_across_two_runs() -> None:
    existing = [
        {
            "archetype": "Covered Mage",
            "format": "Wild",
            "deck_code": EVENLOCK_CODE,
        }
    ]
    unresolved = [f"Quiet Deck {index}" for index in range(100)]
    targets = ["Covered Mage", *unresolved]
    empty_fetch = (
        [],
        0,
        ["flaresolverr"],
        [
            {
                "backend": "flaresolverr",
                "state": "accepted",
                "request_credits": 0,
            }
        ],
    )

    first_fetch = AsyncMock(return_value=empty_fetch)
    with (
        patch.object(hsguru_decks, "_catalog_rows", return_value=existing),
        patch.object(hsguru_decks, "_catalog_snapshot", return_value=None),
        patch.object(hsguru_decks, "_meta_archetypes", return_value=targets),
        patch.object(hsguru_decks, "_fetch_catalog_chunks", first_fetch),
        patch.object(hsguru_decks, "_write_catalog") as first_write,
        pytest.raises(hsguru_decks.HSGuruCatalogPartial),
    ):
        asyncio.run(
            hsguru_decks._refresh_all_rank_catalog(
                "wild",
                period="patch_36.2.0",
            )
        )

    first_payload = first_write.call_args.kwargs
    assert first_payload["zero_sample_archetypes"] == unresolved[:64]
    assert first_payload["missing_archetypes"] == unresolved[64:]

    snapshot = hsguru_decks._CatalogSnapshot(
        rows=existing,
        period="patch_36.2.0",
        fetched_at="2026-08-11T00:00:00+00:00",
        backend="flaresolverr",
        provider_backends=("flaresolverr",),
        missing_archetypes=tuple(first_payload["missing_archetypes"]),
        zero_sample_archetypes=tuple(first_payload["zero_sample_archetypes"]),
    )
    second_fetch = AsyncMock(return_value=empty_fetch)
    with (
        patch.object(hsguru_decks, "_catalog_rows", return_value=existing),
        patch.object(hsguru_decks, "_catalog_snapshot", return_value=snapshot),
        patch.object(hsguru_decks, "_meta_archetypes", return_value=targets),
        patch.object(hsguru_decks, "_fetch_catalog_chunks", second_fetch),
        patch.object(hsguru_decks, "_write_catalog") as second_write,
    ):
        rows = asyncio.run(
            hsguru_decks._refresh_all_rank_catalog(
                "wild",
                period="patch_36.2.0",
            )
        )

    assert rows == existing
    exact_retry = second_fetch.await_args_list[1]
    assert exact_retry.args[1][:36] == unresolved[64:]
    second_payload = second_write.call_args.kwargs
    assert second_payload["missing_archetypes"] == []
    assert set(second_payload["zero_sample_archetypes"]) == set(unresolved)
    assert not (
        set(second_payload["missing_archetypes"])
        & set(second_payload["zero_sample_archetypes"])
    )

    third_snapshot = hsguru_decks._CatalogSnapshot(
        rows=existing,
        period="patch_36.2.0",
        fetched_at="2026-08-11T01:00:00+00:00",
        backend="flaresolverr",
        provider_backends=("flaresolverr",),
        zero_sample_archetypes=tuple(second_payload["zero_sample_archetypes"]),
    )
    third_fetch = AsyncMock(return_value=empty_fetch)
    with (
        patch.object(hsguru_decks, "_catalog_rows", return_value=existing),
        patch.object(hsguru_decks, "_catalog_snapshot", return_value=third_snapshot),
        patch.object(hsguru_decks, "_meta_archetypes", return_value=targets),
        patch.object(hsguru_decks, "_fetch_catalog_chunks", third_fetch),
        patch.object(hsguru_decks, "_write_catalog"),
    ):
        asyncio.run(
            hsguru_decks._refresh_all_rank_catalog(
                "wild",
                period="patch_36.2.0",
            )
        )

    third_exact_retry = third_fetch.await_args_list[1]
    assert third_exact_retry.args[1] == unresolved[28:92]
    assert third_exact_retry.args[1] != unresolved[:64]


def test_patch_catalog_targets_exact_current_matrix_period() -> None:
    matrix = {
        "data": {
            "structured": {
                "current_catalog": {
                    "criteria": {"period": "patch_36.2.0"},
                    "archetypes": [
                        {"format": "wild", "archetype": "Current Wild Mage"},
                        {"format": "standard", "archetype": "Current Standard DK"},
                    ],
                }
            }
        }
    }
    with patch.object(
        hsguru_decks,
        "load_resolved_public_dataset",
        return_value=matrix,
    ):
        current = hsguru_decks._meta_archetypes(
            "wild",
            period="patch_36.2.0",
        )
        mismatched = hsguru_decks._meta_archetypes(
            "wild",
            period="patch_36.1.0",
        )

    assert current == ["Current Wild Mage"]
    assert mismatched == []


def test_catalog_snapshot_isolated_by_patch_period(tmp_path) -> None:
    path = tmp_path / "catalog.json"
    hsguru_decks.write_json(
        path,
        {
            "source_id": "hsguru_deck_catalog_wild_all",
            "state": "ok",
            "fetched_at": datetime.now(UTC).isoformat(),
            "period": "patch_36.1.0",
            "backend": "flaresolverr",
            "provider_backends": ["flaresolverr"],
            "data": [
                {
                    "archetype": "Old Patch Mage",
                    "format": "Wild",
                    "deck_code": EVENLOCK_CODE,
                }
            ],
        },
    )
    hsguru_decks._catalog_memory.clear()

    with patch.object(hsguru_decks, "dataset_path", return_value=path):
        matching = hsguru_decks._catalog_rows(
            "wild",
            "all",
            expected_period="patch_36.1.0",
        )
        mismatched = hsguru_decks._catalog_rows(
            "wild",
            "all",
            expected_period="patch_36.2.0",
        )

    assert len(matching) == 1
    assert mismatched == []


def test_legacy_catalog_without_period_is_not_reused_for_patch(tmp_path) -> None:
    path = tmp_path / "catalog.json"
    hsguru_decks.write_json(
        path,
        {
            "source_id": "hsguru_deck_catalog_wild_all",
            "state": "ok",
            "fetched_at": datetime.now(UTC).isoformat(),
            "data": [
                {
                    "archetype": "Legacy Mage",
                    "format": "Wild",
                    "deck_code": EVENLOCK_CODE,
                }
            ],
        },
    )
    hsguru_decks._catalog_memory.clear()

    with patch.object(hsguru_decks, "dataset_path", return_value=path):
        rows = hsguru_decks._catalog_rows(
            "wild",
            "all",
            expected_period="patch_36.2.0",
        )

    assert rows == []


def test_legend_catalog_accepts_sparse_valid_post_patch_sample() -> None:
    page = hsguru_decks._CatalogPage(
        html='<div class="deck_stats_viewport">'
        + _card(
            "Evenlock",
            "warlock",
            EVENLOCK_CODE,
            12,
            51.0,
        )
        + "</div>",
        backend="flaresolverr",
        request_credits=0,
        acquisition=(
            {
                "backend": "flaresolverr",
                "state": "accepted",
                "request_credits": 0,
            },
        ),
    )
    with (
        patch.object(hsguru_decks, "_current_deck_period", return_value="patch_36.2.0"),
        patch.object(hsguru_decks, "_fetch_catalog_page", AsyncMock(return_value=page)),
        patch.object(hsguru_decks, "_canonicalize_catalog_archetypes", AsyncMock()),
        patch.object(hsguru_decks, "_write_catalog") as write_catalog,
    ):
        rows = asyncio.run(hsguru_decks.refresh_hsguru_deck_catalog("wild", "legend"))

    assert len(rows) == 1
    assert write_catalog.call_args.kwargs["period"] == "patch_36.2.0"
    assert write_catalog.call_args.kwargs["sample_state"] == "sparse_post_patch"


def test_legend_catalog_rejects_empty_post_patch_sample() -> None:
    page = hsguru_decks._CatalogPage(
        html='<div class="deck_stats_viewport"></div>',
        backend="flaresolverr",
        request_credits=0,
    )
    with (
        patch.object(hsguru_decks, "_current_deck_period", return_value="patch_36.2.0"),
        patch.object(hsguru_decks, "_fetch_catalog_page", AsyncMock(return_value=page)),
        patch.object(hsguru_decks, "_write_catalog") as write_catalog,
        pytest.raises(RuntimeError, match="could not be verified"),
    ):
        asyncio.run(hsguru_decks.refresh_hsguru_deck_catalog("wild", "legend"))

    write_catalog.assert_not_called()


def test_exact_cache_key_changes_with_patch_period() -> None:
    hsguru_decks._cache.clear()
    hsguru_decks._inflight.clear()
    fetch = AsyncMock(
        side_effect=[
            [{"deck_code": EVENLOCK_CODE}],
            [{"deck_code": SECOND_EVENLOCK_CODE}],
        ]
    )

    async def run_lookups() -> tuple[list[dict], list[dict]]:
        with (
            patch.object(
                hsguru_decks,
                "_current_deck_period",
                side_effect=[
                    "patch_36.1.0",
                    "patch_36.2.0",
                ],
            ),
            patch.object(hsguru_decks, "cached_hsguru_catalog_decks", return_value=[]),
            patch.object(hsguru_decks, "_fetch_exact", fetch),
        ):
            first = await hsguru_decks.exact_hsguru_decks("Evenlock", "wild", "legend")
            second = await hsguru_decks.exact_hsguru_decks("Evenlock", "wild", "legend")
        return first, second

    first, second = asyncio.run(run_lookups())

    assert first != second
    assert fetch.await_args_list == [
        call("Evenlock", "wild", "legend", period="patch_36.1.0"),
        call("Evenlock", "wild", "legend", period="patch_36.2.0"),
    ]


def test_catalog_publication_separates_acquisition_from_retained_snapshot() -> None:
    retained = hsguru_decks._CatalogSnapshot(
        rows=[{"archetype": "Old Mage", "deck_code": EVENLOCK_CODE}],
        period="patch_36.2.0",
        fetched_at="2026-08-10T00:00:00+00:00",
        backend="scrape_do_super",
        provider_backends=("scrape_do_super",),
    )
    rows = [
        {"archetype": "Old Mage", "deck_code": EVENLOCK_CODE},
        {"archetype": "New Mage", "deck_code": SECOND_EVENLOCK_CODE},
    ]
    attempts = [
        {
            "backend": "flaresolverr",
            "state": "accepted",
            "request_credits": 0,
        }
    ]
    with patch.object(hsguru_decks, "write_json") as write_json:
        hsguru_decks._write_catalog(
            "wild",
            "all",
            rows,
            period="patch_36.2.0",
            credits_used=0,
            backends=["flaresolverr"],
            attempts=attempts,
            retained_snapshot=retained,
            acquired_rows=1,
        )

    payload = write_json.call_args.args[1]
    assert payload["backend"] == "flaresolverr"
    assert payload["acquisition"]["candidate_rows"] == 1
    assert payload["acquisition"]["attempts"] == attempts
    assert payload["retained_snapshot"] == {
        "period": "patch_36.2.0",
        "fetched_at": "2026-08-10T00:00:00+00:00",
        "backend": "scrape_do_super",
        "provider_backends": ["scrape_do_super"],
        "row_count": 1,
        "missing_count": 0,
        "zero_sample_count": 0,
    }


def test_exact_filtered_page_accepts_specific_build_title() -> None:
    html = _card("FUU Plague DK", "deathknight", EVENLOCK_CODE, 231, 50.0)

    rows = parse_hsguru_decks_html(
        html,
        archetype="Plague DK",
        format_name="wild",
        trust_exact_filter=True,
    )

    assert len(rows) == 1
    assert rows[0]["archetype"] == "Plague DK"
    assert rows[0]["title"] == "FUU Plague DK"
    assert rows[0]["class"] == "DeathKnight"


def test_lookup_continues_after_a_failed_fresh_slice() -> None:
    exact_row = {
        "archetype": "Big Shaman",
        "deck_code": EVENLOCK_CODE,
    }
    lookup = AsyncMock(
        side_effect=[RuntimeError("temporary upstream failure"), [exact_row]]
    )

    with patch.object(hsguru_decks, "_fetch_attempt", lookup):
        rows = asyncio.run(hsguru_decks._fetch_exact("Big Shaman", "wild", "legend"))

    assert rows == [
        {
            **exact_row,
            "games": None,
            "score": None,
            "win_rate": None,
            "sample_rank": "all",
            "sample_period": "past_30_days",
        }
    ]
    assert lookup.await_count == 2


def test_all_rank_lookup_uses_one_broad_slice() -> None:
    lookup = AsyncMock(return_value=[])

    with patch.object(hsguru_decks, "_fetch_attempt", lookup):
        rows = asyncio.run(hsguru_decks._fetch_exact("Harold DH", "standard", "all"))

    assert rows == []
    lookup.assert_awaited_once_with(
        "Harold DH",
        "standard",
        [("rank", "all"), ("period", "past_30_days"), ("min_games", 10)],
    )


def test_fetch_attempt_uses_cached_firecrawl_html() -> None:
    async def scrape(source, **_kwargs):
        return FirecrawlScrape(
            html=(
                '<div class="deck_stats_viewport">'
                + _card(
                    "Harold DH",
                    "demonhunter",
                    EVENLOCK_CODE,
                    617,
                    61.6,
                ).replace("# Format: Wild", "# Format: Standard")
                + "</div>"
            ),
            markdown="",
            screenshot=None,
            metadata={"creditsUsed": 1},
            status_code=200,
            final_url=source.url,
        )

    scrape_mock = AsyncMock(side_effect=scrape)
    with patch.object(hsguru_decks, "scrape_source_with_options", scrape_mock):
        rows = asyncio.run(
            hsguru_decks._fetch_attempt(
                "Harold DH",
                "standard",
                [("rank", "all"), ("period", "past_30_days"), ("min_games", 10)],
            )
        )

    assert len(rows) == 1
    assert rows[0]["archetype"] == "Harold DH"
    assert rows[0]["games"] == 617
    # The provider receives the exact filtered URL so semantic query
    # validation cannot silently accept an unfiltered redirect.
    assert scrape_mock.await_args.kwargs["max_age_ms"] == 6 * 60 * 60 * 1_000
    assert scrape_mock.await_args.kwargs["timeout_ms"] == 25_000


def test_exact_fetch_rejects_unverified_empty_dom() -> None:
    page = hsguru_decks._CatalogPage(
        html='<div id="deck_stats_viewport"></div>',
        backend="flaresolverr",
        request_credits=0,
    )
    with (
        patch.object(
            hsguru_decks,
            "_fetch_catalog_page",
            AsyncMock(return_value=page),
        ),
        pytest.raises(RuntimeError, match="could not be verified"),
    ):
        asyncio.run(
            hsguru_decks._fetch_attempt(
                "Quiet Mage",
                "standard",
                [("rank", "all"), ("period", "patch_36.2.0")],
            )
        )


def test_exact_fetch_accepts_explicit_empty_result() -> None:
    page = hsguru_decks._CatalogPage(
        html=(
            '<div id="deck_stats_viewport"></div>'
            "<p>No decks available for these filters.</p>"
        ),
        backend="flaresolverr",
        request_credits=0,
    )
    with patch.object(
        hsguru_decks,
        "_fetch_catalog_page",
        AsyncMock(return_value=page),
    ):
        rows = asyncio.run(
            hsguru_decks._fetch_attempt(
                "Quiet Mage",
                "standard",
                [("rank", "all"), ("period", "patch_36.2.0")],
            )
        )

    assert rows == []
