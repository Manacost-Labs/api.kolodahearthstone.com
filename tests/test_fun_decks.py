from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from unittest.mock import patch

from app.fun_decks import (
    MetaReference,
    _balance_and_dedupe,
    _catalog_rows,
    _merge_history,
    detect_card_packages,
    jaccard_similarity,
    score_fun_deck,
)


def test_hsguru_catalog_rows_reject_previous_patch() -> None:
    dataset = {
        "period": "patch_36.1.0",
        "data": [{"archetype": "Old Mage", "deck_code": "OLD"}],
    }
    with (
        patch("app.fun_decks.load_dataset", return_value=dataset),
        patch(
            "app.hsguru_decks.current_hsguru_deck_period",
            return_value="patch_36.2.0",
        ),
    ):
        rows = _catalog_rows("hsguru_deck_catalog_wild_all")

    assert rows == []


def test_jaccard_identical_and_disjoint():
    left = Counter({1: 2, 2: 1, 3: 2})
    assert jaccard_similarity(left, left) == 1.0
    assert jaccard_similarity(left, Counter({9: 2, 8: 2})) == 0.0


def test_score_marks_concept_outlier_as_fun():
    meta = MetaReference(
        deck_code="META",
        cards=Counter({1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1}),
        archetype="Dragon Warrior",
        hero_class="Warrior",
        games=5000,
        format_name="Standard",
        source_id="catalog",
    )
    fun_cards = Counter(
        {100: 2, 101: 2, 102: 2, 103: 2, 104: 2, 105: 1, 106: 1, 107: 1, 108: 1, 109: 1}
    )
    scored = score_fun_deck(
        deck_code="FUN",
        title="Casino Yogg Mill Mage",
        format_name="Standard",
        cards=fun_cards,
        hero_class="Mage",
        references=[meta],
        archetype_popularity={"dragon warrior": 12.0},
        archetype_volume={"dragon warrior": 20000, "egglock": 5000},
        known_ladder={"dragon warrior", "egglock", "leyline mage", "zee shaman"},
        min_score=0.55,
        max_meta_similarity=0.42,
    )
    assert scored.is_fun is True
    assert scored.concept_hit is True
    assert scored.max_meta_similarity < 0.2


def test_score_rejects_weak_known_ladder_archetype():
    cards = Counter({1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1})
    near = Counter(cards)
    near[10] -= 1
    near[11] += 1
    meta = MetaReference(
        deck_code="META",
        cards=cards,
        archetype="Egglock",
        hero_class="Warlock",
        games=4000,
        format_name="Standard",
        source_id="catalog",
    )
    scored = score_fun_deck(
        deck_code="NEAR",
        title="Egglock",
        format_name="Standard",
        cards=near,
        hero_class="Warlock",
        references=[meta],
        archetype_popularity={"egglock": 0.4},
        archetype_volume={"egglock": 9000},
        known_ladder={"egglock", "leyline mage", "zee shaman"},
        min_score=0.55,
        max_meta_similarity=0.42,
    )
    assert scored.is_fun is False


def test_merge_history_keeps_first_seen():
    previous = [
        {
            "deck_code": "A",
            "fun_score": 0.7,
            "first_seen_at": "2026-07-20T00:00:00+00:00",
            "last_seen_at": "2026-07-21T00:00:00+00:00",
        }
    ]
    fresh = [
        {
            "deck_code": "A",
            "fun_score": 0.8,
            "first_seen_at": "2026-07-23T00:00:00+00:00",
            "last_seen_at": "2026-07-23T00:00:00+00:00",
        }
    ]
    merged = _merge_history(
        previous,
        fresh,
        retention_hours=168,
        now=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert len(merged) == 1
    assert merged[0]["first_seen_at"] == "2026-07-20T00:00:00+00:00"
    assert merged[0]["fun_score"] == 0.8


def test_detect_card_packages_reno_and_quest():
    # Reno Jackson + Zephrys markers
    packages = detect_card_packages(Counter({2883: 1, 53756: 1, 1: 1, 2: 1}))
    assert "reno_highlander" in packages


def test_score_boosts_card_package_without_concept_title():
    meta = MetaReference(
        deck_code="META",
        cards=Counter({1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1}),
        archetype="Dragon Warrior",
        hero_class="Warrior",
        games=5000,
        format_name="Standard",
        source_id="catalog",
    )
    # Far from meta + Mecha'thun marker, bland title.
    fun_cards = Counter(
        {
            120231: 1,
            200: 2,
            201: 2,
            202: 2,
            203: 2,
            204: 1,
            205: 1,
            206: 1,
            207: 1,
            208: 1,
        }
    )
    scored = score_fun_deck(
        deck_code="FUNPKG",
        title="Custom Warlock",
        format_name="Standard",
        cards=fun_cards,
        hero_class="Warlock",
        references=[meta],
        archetype_popularity={"dragon warrior": 12.0},
        archetype_volume={"dragon warrior": 20000},
        known_ladder={"dragon warrior"},
        min_score=0.55,
        max_meta_similarity=0.42,
    )
    assert "card_package" in scored.reasons
    assert scored.is_fun is True


def test_rejects_thief_priest_and_soothsayer_ladder_names():
    meta = MetaReference(
        deck_code="META",
        cards=Counter({1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1}),
        archetype="Egg Priest",
        hero_class="Priest",
        games=4000,
        format_name="Standard",
        source_id="catalog",
    )
    # Far from meta + a quest marker must NOT save named ladder decks.
    cards = Counter(
        {
            117544: 1,
            200: 2,
            201: 2,
            202: 2,
            203: 2,
            204: 1,
            205: 1,
            206: 1,
            207: 1,
            208: 1,
        }
    )
    for title in ("Thief Priest", "Soothsayer Priest", "Quest Hunter"):
        scored = score_fun_deck(
            deck_code="LADDER",
            title=title,
            format_name="Standard",
            cards=cards,
            hero_class="Priest",
            references=[meta],
            archetype_popularity={"egg priest": 0.5},
            archetype_volume={"egg priest": 2000},
            known_ladder={"egg priest"},
            min_score=0.55,
            max_meta_similarity=0.42,
        )
        assert scored.is_fun is False, (title, scored.reasons, scored.fun_score)
        assert (
            "ladder_name_gate" in scored.reasons
            or "named_ladder_archetype" in scored.reasons
        )


def test_yogg_mill_not_blocked_by_unrelated_nearest_ladder_name():
    meta = MetaReference(
        deck_code="META",
        cards=Counter({1: 2, 2: 2, 3: 2, 4: 2, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1}),
        archetype="Egg Priest",
        hero_class="Priest",
        games=4000,
        format_name="Standard",
        source_id="catalog",
    )
    fun_cards = Counter(
        {100: 2, 101: 2, 102: 2, 103: 2, 104: 2, 105: 1, 106: 1, 107: 1, 108: 1, 109: 1}
    )
    scored = score_fun_deck(
        deck_code="FUN",
        title="Casino Yogg Mill Mage",
        format_name="Standard",
        cards=fun_cards,
        hero_class="Mage",
        references=[meta],
        archetype_popularity={"egg priest": 0.5},
        archetype_volume={"egg priest": 2000},
        known_ladder={"egg priest"},
        min_score=0.55,
        max_meta_similarity=0.42,
    )
    assert scored.is_fun is True
    assert "ladder_name_gate" not in scored.reasons


def test_steal_package_requires_two_markers():
    assert "steal_package" not in detect_card_packages(
        Counter({47211: 1, 1: 1})
    )  # Tess alone
    assert "steal_package" in detect_card_packages(
        Counter({47211: 1, 52715: 1, 1: 1})
    )  # Tess+Lazul


def test_balance_limits_xl_hl_and_class_spam():
    rows = []
    for i in range(8):
        rows.append(
            {
                "title": f"XL HL Rogue {i}",
                "format": "Wild",
                "class": "Rogue",
                "deck_code": f"CODE{i}",
                "fun_score": 1.0 - i * 0.01,
                "max_meta_similarity": 0.1,
            }
        )
    rows.append(
        {
            "title": "Switcheroo Priest",
            "format": "Wild",
            "class": "Priest",
            "deck_code": "PRIEST1",
            "fun_score": 0.9,
            "max_meta_similarity": 0.2,
        }
    )
    rows.append(
        {
            "title": "Big Spell Mage",
            "format": "Standard",
            "class": "Mage",
            "deck_code": "MAGE1",
            "fun_score": 0.85,
            "max_meta_similarity": 0.2,
        }
    )
    picked = _balance_and_dedupe(rows, per_format_keep=6)
    xl = [r for r in picked if "xl" in r["title"].lower()]
    assert len(xl) <= 4
    assert any(r["title"] == "Switcheroo Priest" for r in picked)
    assert any(r["title"] == "Big Spell Mage" for r in picked)
