from __future__ import annotations

from app.balance_radar.stats import (
    archetype_aliases,
    build_stats,
    class_from_archetype,
    latest_card_set,
)
from tests.balance_radar_fixtures import CARDS_EN, loader


def test_archetypes_use_the_ban_window_and_report_trends() -> None:
    bundle = build_stats("standard", loader=loader, cards_en=CARDS_EN)

    assert bundle.periods["primary"] == "black_empire_ban"
    assert (
        bundle.periods["ban"] == "black_empire_ban"
        and bundle.periods["current"] == "black_empire"
    )
    zoo = bundle.entities["archetype:standard:Zoolock"]
    stats = zoo["stats"]
    assert (stats["winrate"], stats["popularity"], stats["games"]) == (
        63.0,
        9.8,
        139_517,
    )
    assert stats["legendWinrate"] == 59.0
    assert stats["trend"]["winrateDelta"] == -0.3  # past_day − past_week
    assert stats["trend"]["popularityDelta"] == 4.5
    assert stats["trend"]["banWinrateDelta"] == 0.1  # ban window − whole expansion
    assert {(p["rank"], p["period"]) for p in stats["periods"]} >= {
        ("all", "past_day"),
        ("legend", "black_empire_ban"),
    }
    assert zoo["class"] == "WARLOCK" and zoo["className"] == "Чернокнижник"
    # Class comes from the archetype name when the catalog has no decks.
    assert bundle.entities["archetype:standard:C'Thun DH"]["class"] == "DEMONHUNTER"
    # Too few games: not a candidate at all.
    assert "archetype:standard:Tiny Rogue" not in bundle.entities
    assert zoo["scores"]["nerf"] > 0 and zoo["scores"]["buff"] == 0
    druid = bundle.entities["archetype:standard:Beast Druid"]
    assert druid["scores"]["buff"] > 0 and druid["scores"]["nerf"] == 0


def test_classes_aggregate_games_weighted_winrate() -> None:
    bundle = build_stats("standard", loader=loader, cards_en=CARDS_EN)
    warlock = bundle.entities["class:standard:WARLOCK"]
    assert warlock["stats"]["games"] == 139_517
    assert warlock["stats"]["winrate"] == 63.0
    druid = bundle.entities["class:standard:DRUID"]
    assert druid["scores"]["buff"] > 0
    assert druid["aliases"] == ["Druid"]


def test_card_pools_rank_on_deck_winrate_not_played_winrate() -> None:
    bundle = build_stats("standard", loader=loader, cards_en=CARDS_EN)
    nerf_refs = [card["ref"] for card in bundle.card_nerf_pool]
    buff_refs = [card["ref"] for card in bundle.card_buff_pool]

    assert nerf_refs[0] == "card:BE_RIT"
    assert "card:OLD_1" in nerf_refs
    # 76% "winrate when played" in 44% decks is survivorship bias, not power.
    assert "card:BE_BIG" not in nerf_refs
    assert "card:BE_WEAK" in buff_refs
    ritual = bundle.entities["card:BE_RIT"]
    assert (
        ritual["name"] == "Ритуал призыва" and ritual["nameEn"] == "Ritual of Summoning"
    )
    assert ritual["newSet"] is True and bundle.entities["card:OLD_1"]["newSet"] is False
    assert ritual["stats"]["source"] == "hsreplay"
    assert (ritual["stats"]["rank"], ritual["stats"]["period"]) == ("legend", "7d")
    assert ritual["stats"]["popularity"] == 18.8 and ritual["stats"]["winrate"] == 55.9
    assert ritual["stats"]["trend"]["popularityDelta"] == 8.0  # 1d − 7d
    assert ritual["imageUrl"].endswith("/BE_RIT.png")
    assert set(bundle.aliases["card:BE_RIT"]) == {
        "Ritual of Summoning",
        "Ритуал призыва",
    }
    assert any(source["id"] == "hsreplay_cards_legend_7d" for source in bundle.sources)


def test_missing_datasets_produce_an_empty_bundle() -> None:
    bundle = build_stats("wild", loader=lambda _source: None, cards_en={})
    assert bundle.empty and bundle.sources == []


def test_archetype_name_helpers() -> None:
    assert class_from_archetype("Zee C'Thun Shaman") == "SHAMAN"
    assert class_from_archetype("Shredslock") == "WARLOCK"
    assert class_from_archetype("Quest DH") == "DEMONHUNTER"
    assert class_from_archetype("Mystery Deck") is None
    assert archetype_aliases("Zoolock", "WARLOCK") == ["Zoo Warlock", "Zoolock"]
    assert "C'Thun Demon Hunter" in archetype_aliases("C'Thun DH", "DEMONHUNTER")
    assert latest_card_set(CARDS_EN) == "BE"
