from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app import hsguru_decks
from app.firecrawl_backend import FirecrawlScrape
from app.hsguru_decks import parse_hsguru_decks_html

EVENLOCK_CODE = "AAEBAf0GBs30Av76A4f7A564BtvXB63ZBwycENfOA4j0A8b5A8f5A63pBdCeBu6hBom1BoSZB+C+B43cBwAA"
SECOND_EVENLOCK_CODE = "AAEBAf0GBM30Aof7A6nhBq3ZBw3XzgOI9APG+QPH+QP++gOt6QXQngbuoQaJtQacwQaEmQfb1weN3AcAAA=="


def _card(title: str, class_name: str, deck_code: str, games: int, winrate: float) -> str:
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

    assert parse_hsguru_decks_html(html, archetype="Big Shaman", format_name="wild") == []


def test_catalog_parser_accepts_multiple_exact_deck_titles() -> None:
    html = (
        _card("Evenlock", "warlock", EVENLOCK_CODE, 4227, 61.1)
        + _card("Mug Shaman", "shaman", SECOND_EVENLOCK_CODE, 999, 59.0)
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
        patch.object(hsguru_decks, "cached_hsguru_catalog_decks", return_value=[catalog_row]),
        patch.object(hsguru_decks, "_fetch_exact", live_lookup),
    ):
        rows = asyncio.run(hsguru_decks.exact_hsguru_decks("XL Mill Druid", "wild", "legend"))

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
    with patch.object(hsguru_decks, "_catalog_rows", return_value=[all_rank_row]) as catalog_rows:
        rows = hsguru_decks.cached_hsguru_catalog_decks(
            "XL Mill Druid",
            "wild",
            "diamond_4to1",
        )

    assert rows == [{
        **all_rank_row,
        "games": None,
        "score": None,
        "win_rate": None,
        "sample_rank": "all",
    }]
    catalog_rows.assert_called_once_with("wild", "all")


def test_legend_catalog_is_preferred_before_all_rank_fallback() -> None:
    legend_row = {
        "archetype": "XL Mill Druid",
        "format": "Wild",
        "deck_code": EVENLOCK_CODE,
        "games": 319,
        "win_rate": 53.6,
    }
    with patch.object(hsguru_decks, "_catalog_rows", return_value=[legend_row]) as catalog_rows:
        rows = hsguru_decks.cached_hsguru_catalog_decks("XL Mill Druid", "wild", "legend")

    assert rows == [legend_row]
    catalog_rows.assert_called_once_with("wild", "legend")


def test_all_rank_lookup_uses_its_preloaded_catalog() -> None:
    catalog_row = {
        "archetype": "XL HL Exodia Mage",
        "format": "Wild",
        "deck_code": EVENLOCK_CODE,
        "games": 412,
        "win_rate": 54.2,
    }
    with patch.object(hsguru_decks, "_catalog_rows", return_value=[catalog_row]) as catalog_rows:
        rows = hsguru_decks.cached_hsguru_catalog_decks("XL HL Exodia Mage", "wild", "all")

    assert rows == [catalog_row]
    catalog_rows.assert_called_once_with("wild", "all")


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
    cached_rows = [{
        "archetype": "XL HL Exodia Mage",
        "format": "Wild",
        "deck_code": EVENLOCK_CODE,
    }]
    scrape = AsyncMock()
    with (
        patch.object(hsguru_decks, "_catalog_rows", return_value=cached_rows),
        patch.object(hsguru_decks, "_all_rank_catalog_archetypes", return_value=[]),
        patch.object(hsguru_decks, "_fetch_catalog_chunks", scrape),
    ):
        rows = asyncio.run(hsguru_decks.refresh_hsguru_deck_catalog("wild", "all"))

    assert rows == cached_rows
    scrape.assert_not_awaited()


def test_catalog_chunks_limit_each_firecrawl_page_to_five_archetypes() -> None:
    archetypes = [f"Deck {index}" for index in range(12)]

    assert hsguru_decks._catalog_chunks(archetypes) == [
        archetypes[:5],
        archetypes[5:10],
        archetypes[10:],
    ]


def test_partial_all_rank_catalog_fetches_only_missing_archetypes() -> None:
    existing = [{"archetype": "Popular Mage", "deck_code": EVENLOCK_CODE}]
    fetched = [{"archetype": "Rare Mage", "deck_code": SECOND_EVENLOCK_CODE}]
    fetch_chunks = AsyncMock(return_value=(fetched, 1))
    with (
        patch.object(hsguru_decks, "_catalog_rows", return_value=existing),
        patch.object(
            hsguru_decks,
            "_all_rank_catalog_archetypes",
            side_effect=[["Rare Mage"], []],
        ),
        patch.object(hsguru_decks, "_fetch_catalog_chunks", fetch_chunks),
        patch.object(hsguru_decks, "_write_catalog") as write_catalog,
    ):
        rows = asyncio.run(hsguru_decks._refresh_all_rank_catalog("wild"))

    assert {row["archetype"] for row in rows} == {"Popular Mage", "Rare Mage"}
    fetch_chunks.assert_awaited_once_with("wild", ["Rare Mage"])
    write_catalog.assert_called_once()


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
    lookup = AsyncMock(side_effect=[RuntimeError("temporary upstream failure"), [exact_row]])

    with patch.object(hsguru_decks, "_fetch_attempt", lookup):
        rows = asyncio.run(hsguru_decks._fetch_exact("Big Shaman", "wild", "legend"))

    assert rows == [exact_row]
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
    scrape = AsyncMock(return_value=FirecrawlScrape(
        html=(
            '<div class="deck_stats_viewport">'
            + _card("Harold DH", "demonhunter", EVENLOCK_CODE, 617, 61.6).replace("# Format: Wild", "# Format: Standard")
            + '</div>'
        ),
        markdown="",
        screenshot=None,
        metadata={"creditsUsed": 1},
        status_code=200,
        final_url="https://www.hsguru.com/decks",
    ))

    with patch.object(hsguru_decks, "scrape_source_with_options", scrape):
        rows = asyncio.run(hsguru_decks._fetch_attempt(
            "Harold DH",
            "standard",
            [("rank", "all"), ("period", "past_30_days"), ("min_games", 10)],
        ))

    assert len(rows) == 1
    assert rows[0]["archetype"] == "Harold DH"
    assert rows[0]["games"] == 617
    assert scrape.await_args.kwargs["max_age_ms"] == 6 * 60 * 60 * 1_000
    assert scrape.await_args.kwargs["timeout_ms"] == 25_000
