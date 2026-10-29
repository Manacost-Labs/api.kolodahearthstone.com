from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from hearthstone.deckstrings import Deck
from hearthstone.enums import FormatType
from hypothesis import given, settings
from hypothesis import strategies as st

from app import fun_decks
from app.fun_decks import (
    MetaReference,
    _balance_and_dedupe,
    _catalog_rows,
    _merge_history,
    _normalize_fun_row,
    _pick_diverse,
    _refresh_fun_decks_unlocked,
    _select_published,
    dedupe_near_duplicates,
    detect_card_packages,
    evaluate_sample,
    jaccard_similarity,
    score_fun_deck,
    strip_format_prefix,
    wilson_interval,
)
from app.storage import load_dataset, save_dataset


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


# ---------------------------------------------------------------------------
# concept-v7: sample / win-rate gate, near-duplicate lists, class balance,
# generic ladder names, clean titles, replay of the published v6 dataset
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "fun_decks"


class _AnyCardIndex(dict):  # type: ignore[type-arg]
    """Stand-in for the HearthstoneJSON card index: every dbfId is a known card."""

    def get(self, key: Any, default: Any = None) -> Any:
        return {"dbfId": key, "collectible": True, "mechanics": [], "name": f"Card {key}"}


def _two_ofs(ids: Any) -> Counter[int]:
    return Counter({card: 2 for card in ids})


def _deckstring(ids: Any) -> str:
    deck = Deck()
    deck.heroes = [7]
    deck.format = FormatType.FT_STANDARD
    deck.cards = [(card, 2) for card in ids]
    return deck.as_deckstring


def _row(
    title: str,
    *,
    code: str | None = None,
    cls: str = "Mage",
    fmt: str = "Standard",
    score: float = 0.8,
    games: int = 100,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "title": title,
        "deck_code": code or title,
        "class": cls,
        "format": fmt,
        "fun_score": score,
        "games": games,
        **extra,
    }


# --- 1. sample and win rate -------------------------------------------------


@pytest.mark.parametrize(
    ("games", "win_rate"),
    [(None, 55.0), (80, None), (None, None), ("", "n/a"), (0, 50.0), (80, 120.0)],
)
def test_deck_without_usable_games_or_win_rate_is_rejected(games: Any, win_rate: Any) -> None:
    verdict = evaluate_sample(games, win_rate)

    assert verdict.ok is False
    assert verdict.reject == "missing_stats"
    assert (verdict.ci_low, verdict.ci_high) == (None, None)


def test_deck_below_minimum_games_is_rejected() -> None:
    assert evaluate_sample(29, 60.0).reject == "too_few_games"
    assert evaluate_sample(30, 60.0).ok is True


def test_deck_whose_win_rate_is_certainly_losing_is_rejected() -> None:
    # 19 wins of 60: the 95% interval tops out at 44.3 < 45.
    losing = evaluate_sample(60, 31.7)
    assert (losing.ok, losing.reject) == (False, "losing_win_rate")
    assert (losing.ci_low, losing.ci_high) == (21.3, 44.3)

    # 20 wins of 60 still reaches 45.9: not provably losing, so it stays.
    borderline = evaluate_sample(60, 33.3)
    assert borderline.ok is True
    assert (borderline.ci_low, borderline.ci_high) == (22.7, 45.9)

    even = evaluate_sample(60, 50.0)
    assert even.ok is True
    assert (even.ci_low, even.ci_high) == (37.7, 62.3)


def test_win_rate_gate_thresholds_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_MIN_GAMES", "50")
    monkeypatch.setenv("HS_FUN_DECK_MIN_CI_HIGH", "50")

    assert evaluate_sample(49, 60.0).reject == "too_few_games"
    assert evaluate_sample(60, 33.3).reject == "losing_win_rate"
    assert evaluate_sample(60, 50.0).ok is True


@pytest.mark.parametrize(
    ("win_rate", "games", "low", "high"),
    [(50.0, 100, 40.4, 59.6), (0.0, 10, 0.0, 27.8), (100.0, 10, 72.2, 100.0)],
)
def test_published_interval_matches_reference_wilson_values(
    win_rate: float, games: int, low: float, high: float
) -> None:
    verdict = evaluate_sample(games, win_rate, min_games=1, min_ci_high=0)

    assert (verdict.ci_low, verdict.ci_high) == (low, high)


@settings(max_examples=300, deadline=None)
@given(games=st.integers(1, 5000), tenths=st.integers(0, 1000))
def test_interval_always_contains_the_win_rate(games: int, tenths: int) -> None:
    win_rate = tenths / 10
    verdict = evaluate_sample(games, win_rate, min_games=1, min_ci_high=0)

    assert verdict.ok
    assert verdict.ci_low is not None and verdict.ci_high is not None
    assert 0.0 <= verdict.ci_low <= win_rate <= verdict.ci_high <= 100.0


@settings(max_examples=300, deadline=None)
@given(games=st.integers(1, 4000), more=st.integers(1, 4000), tenths=st.integers(0, 1000))
def test_interval_narrows_as_games_grow_at_the_same_win_rate(
    games: int, more: int, tenths: int
) -> None:
    win_rate = tenths / 10
    low_small, high_small = wilson_interval(win_rate, games)
    low_big, high_big = wilson_interval(win_rate, games + more)

    assert high_big - low_big <= high_small - low_small + 1e-9


# --- 2. near-identical lists ------------------------------------------------


def test_lists_with_jaccard_at_least_060_collapse_into_the_better_sampled_deck() -> None:
    cards = {
        "A": _two_ofs(range(1, 16)),
        "B": _two_ofs([*range(1, 13), 101, 102, 103]),  # 3 cards swapped: 24/36 = 0.667
        "C": _two_ofs([*range(1, 15), 101]),  # 1 card swapped: 28/32 = 0.875
    }
    rows = [
        _row("Mill Mage B", code="B", games=60),
        _row("Mill Mage A", code="A", games=100),
        _row("Mill Mage C", code="C", games=80),
    ]

    kept, absorbed = dedupe_near_duplicates(rows, cards)

    assert [(r["title"], r["variants"]) for r in kept] == [("Mill Mage A", 2)]
    assert absorbed == 2


def test_the_jaccard_threshold_is_inclusive_at_060() -> None:
    base = Counter(range(1, 41))
    exactly_060 = Counter([*range(1, 31), *range(101, 111)])  # 30 / 50
    just_below = Counter([*range(1, 30), *range(101, 112)])  # 29 / 51
    rows = [
        _row("Base", code="base", games=100),
        _row("Exactly", code="exactly", games=90),
        _row("Below", code="below", games=80),
    ]
    cards = {"base": base, "exactly": exactly_060, "below": just_below}

    kept, absorbed = dedupe_near_duplicates(rows, cards, threshold=0.6)

    assert [r["title"] for r in kept] == ["Base", "Below"]
    assert absorbed == 1


def test_equal_games_keep_the_higher_fun_score() -> None:
    cards = {"low": _two_ofs(range(1, 16)), "high": _two_ofs([*range(1, 15), 101])}
    rows = [
        _row("Low score", code="low", games=70, score=0.5),
        _row("High score", code="high", games=70, score=0.9),
    ]

    kept, _ = dedupe_near_duplicates(rows, cards)

    assert [r["title"] for r in kept] == ["High score"]


def test_identical_lists_of_different_class_or_format_stay_separate() -> None:
    same = _two_ofs(range(1, 16))
    rows = [
        _row("Standard Mage", code="a", cls="Mage", fmt="Standard"),
        _row("Wild Mage", code="b", cls="Mage", fmt="Wild"),
        _row("Standard Priest", code="c", cls="Priest", fmt="Standard"),
    ]

    kept, absorbed = dedupe_near_duplicates(rows, {"a": same, "b": same, "c": same})

    assert [r["title"] for r in kept] == ["Standard Mage", "Wild Mage", "Standard Priest"]
    assert absorbed == 0
    assert [r["variants"] for r in kept] == [0, 0, 0]


def test_chain_of_similar_lists_does_not_swallow_a_distant_end() -> None:
    a = _two_ofs(range(1, 16))
    b = _two_ofs([*range(1, 13), 101, 102, 103])  # vs a: 0.667
    c = _two_ofs([*range(1, 7), *range(10, 13), 101, 102, 103, 104, 105, 106])  # vs b: 0.667, vs a: 0.429
    rows = [
        _row("A", code="a", games=100),
        _row("B", code="b", games=90),
        _row("C", code="c", games=80),
    ]

    kept, absorbed = dedupe_near_duplicates(rows, {"a": a, "b": b, "c": c})

    assert [(r["title"], r["variants"]) for r in kept] == [("A", 1), ("C", 0)]
    assert absorbed == 1


_deck_cards = st.lists(st.integers(1, 22), min_size=8, max_size=30)
_dedupe_rows = st.lists(
    st.tuples(
        st.sampled_from(["Standard", "Wild"]),
        st.sampled_from(["Mage", "Priest"]),
        st.integers(30, 400),
        st.integers(0, 100),
        _deck_cards,
    ),
    min_size=1,
    max_size=14,
)


@settings(max_examples=200, deadline=None)
@given(spec=_dedupe_rows, threshold=st.sampled_from([0.6, 0.45, 0.8]))
def test_dedupe_is_idempotent_and_leaves_no_similar_pair(
    spec: list[tuple[str, str, int, int, list[int]]], threshold: float
) -> None:
    rows = [
        _row(f"Deck {i}", code=f"D{i}", fmt=fmt, cls=cls, games=games, score=score / 100)
        for i, (fmt, cls, games, score, _) in enumerate(spec)
    ]
    cards = {f"D{i}": Counter(ids) for i, (*_, ids) in enumerate(spec)}

    kept, absorbed = dedupe_near_duplicates(rows, cards, threshold=threshold)
    again, absorbed_again = dedupe_near_duplicates(kept, cards, threshold=threshold)

    assert again == kept and absorbed_again == 0
    assert len(kept) + absorbed == len(rows)
    assert sum(r["variants"] for r in kept) == absorbed
    for i, left in enumerate(kept):
        for right in kept[i + 1 :]:
            if (left["format"], left["class"]) == (right["format"], right["class"]):
                assert jaccard_similarity(cards[left["deck_code"]], cards[right["deck_code"]]) < threshold
    # a (format, class) never loses its last deck
    assert {(r["format"], r["class"]) for r in kept} == {(r["format"], r["class"]) for r in rows}


# --- 3. class balance -------------------------------------------------------


def test_every_class_gets_a_deck_before_the_strongest_classes_fill_up() -> None:
    rows = [
        _row("Rogue 1", cls="Rogue", score=0.99),
        _row("Rogue 2", cls="Rogue", score=0.98),
        _row("Rogue 3", cls="Rogue", score=0.97),
        _row("Priest 1", cls="Priest", score=0.96),
        _row("Priest 2", cls="Priest", score=0.95),
        _row("Priest 3", cls="Priest", score=0.94),
        _row("Mage 1", cls="Mage", score=0.50),
    ]

    picked = _pick_diverse(rows, limit=6, max_per_class=3)

    assert sorted(r["title"] for r in picked) == [
        "Mage 1",
        "Priest 1",
        "Priest 2",
        "Rogue 1",
        "Rogue 2",
        "Rogue 3",
    ]


def test_a_class_with_only_an_xl_deck_is_kept_even_when_the_xl_cap_is_used_up() -> None:
    rows = [
        _row("XL HL Rogue", cls="Rogue", score=0.99),
        _row("XL HL Druid", cls="Druid", score=0.98),
        _row("Mill Mage", cls="Mage", score=0.50),
    ]

    picked = _pick_diverse(rows, limit=10, max_per_class=3, max_xl_hl=1)

    assert sorted(r["title"] for r in picked) == ["Mill Mage", "XL HL Druid", "XL HL Rogue"]


def test_published_set_keeps_a_low_scoring_class_when_stronger_classes_fill_the_format() -> None:
    strong = [
        _row(f"{cls} {n}", cls=cls, score=0.9 - n * 0.01)
        for cls in ("Rogue", "Priest", "Mage", "Druid", "Hunter", "Shaman", "Paladin", "Warlock")
        for n in range(4)
    ]
    rows = [*strong, _row("Warrior Brew", cls="Warrior", score=0.5)]

    for focus in (None, "standard"):
        published = _select_published(rows, focus=focus)

        assert "Warrior Brew" in [r["title"] for r in published], focus


_classes = st.sampled_from(
    ["Druid", "Hunter", "Mage", "Paladin", "Priest", "Rogue", "Shaman", "Warlock", "Warrior", "DeathKnight", "DemonHunter"]
)


@settings(max_examples=150, deadline=None)
@given(
    spec=st.lists(
        st.tuples(st.sampled_from(["Standard", "Wild"]), _classes, st.integers(0, 100), st.booleans()),
        min_size=1,
        max_size=90,
    ),
    focus=st.sampled_from([None, "standard"]),
)
def test_every_format_and_class_with_a_candidate_is_published(
    spec: list[tuple[str, str, int, bool]], focus: str | None
) -> None:
    rows = [
        _row(f"{'XL ' if xl else ''}Deck {i}", code=f"D{i}", fmt=fmt, cls=cls, score=score / 100)
        for i, (fmt, cls, score, xl) in enumerate(spec)
    ]

    published = _select_published(rows, focus=focus)

    assert {(r["format"], r["class"]) for r in published} == {(r["format"], r["class"]) for r in rows}
    assert len({r["deck_code"] for r in published}) == len(published)


# --- 4. generic ladder names ------------------------------------------------


def _generic_case(shared_with_core: int, *, extra_card: int = 0) -> tuple[MetaReference, Counter[int]]:
    """A Paladin meta core and a deck sharing `shared_with_core` of its 15 two-ofs."""
    core = MetaReference(
        deck_code="CORE",
        cards=_two_ofs(range(1, 16)),
        archetype="Midrange Paladin",
        hero_class="Paladin",
        games=4000,
        format_name="Standard",
        source_id="catalog",
    )
    own = [*range(1, shared_with_core + 1), *range(101, 101 + 15 - shared_with_core)]
    if extra_card:
        own[-1] = extra_card
    return core, _two_ofs(own)


def _score_generic(title: str, core: MetaReference, cards: Counter[int], **kwargs: Any) -> Any:
    return score_fun_deck(
        deck_code="DECK",
        title=title,
        format_name="Standard",
        cards=cards,
        hero_class="Paladin",
        references=[core],
        archetype_popularity={},
        archetype_volume={"midrange paladin": 4000},
        known_ladder={"midrange paladin"},
        min_score=0.55,
        max_meta_similarity=0.42,
        **kwargs,
    )


@pytest.mark.parametrize(
    "title",
    [
        "Aggro Paladin",
        "Secret Paladin",
        "Dragon Paladin",
        "Elemental Paladin",
        "Dude Paladin",
        "Big Spell Paladin",
        "STD Aggro Paladin",
    ],
)
def test_generic_ladder_title_close_to_a_meta_core_is_gated(title: str) -> None:
    core, cards = _generic_case(shared_with_core=7)  # Jaccard 14/46 = 0.304

    scored = _score_generic(title, core, cards)

    assert scored.max_meta_similarity == 0.3043
    assert scored.is_fun is False
    assert "generic_ladder_gate" in scored.reasons


def test_generic_ladder_title_far_from_the_meta_is_not_gated() -> None:
    core, cards = _generic_case(shared_with_core=5)  # Jaccard 10/50 = 0.2

    scored = _score_generic("Aggro Paladin", core, cards)

    assert scored.max_meta_similarity == 0.2
    assert "generic_ladder_gate" not in scored.reasons


def test_generic_ladder_gate_similarity_is_configurable() -> None:
    core, cards = _generic_case(shared_with_core=7)

    scored = _score_generic("Dude Paladin", core, cards, generic_name_max_similarity=0.31)

    assert "generic_ladder_gate" not in scored.reasons


def test_generic_ladder_title_with_a_card_package_is_not_gated() -> None:
    core, cards = _generic_case(shared_with_core=7, extra_card=120231)  # Mecha'thun

    scored = _score_generic("Control Paladin", core, cards)

    assert "card_package" in scored.reasons
    assert "generic_ladder_gate" not in scored.reasons


@pytest.mark.parametrize("title", ["Imbue Paladin", "Casino Dude Paladin", "Aggro Paladin Mill"])
def test_titles_that_are_not_a_bare_style_and_class_are_not_generic(title: str) -> None:
    core, cards = _generic_case(shared_with_core=7)

    scored = _score_generic(title, core, cards)

    assert "generic_ladder_gate" not in scored.reasons


# --- 5. titles --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("STD BFU Handbuff DK", "BFU Handbuff DK"),
        ("WLD Rainbow Mage", "Rainbow Mage"),
        ("Wild Yogg Priest", "Yogg Priest"),
        ("std: Imbue Rogue", "Imbue Rogue"),
        ("STD WLD Odd Paladin", "Odd Paladin"),
        ("Wild Pyromancer Rogue", "Wild Pyromancer Rogue"),
        ("STDX Control", "STDX Control"),
        ("Wilder Druid", "Wilder Druid"),
        ("STD", "STD"),
        ("Rainbow DK", "Rainbow DK"),
    ],
)
def test_format_prefixes_are_removed_from_the_start_of_a_title(raw: str, clean: str) -> None:
    assert strip_format_prefix(raw) == clean


def test_normalized_row_keeps_the_source_title_and_adds_the_interval() -> None:
    scored = fun_decks.FunDeckScore(
        fun_score=0.9,
        max_meta_similarity=0.1,
        nearest_archetype="Unholy DK",
        nearest_deck_code=None,
        catalog_games=60,
        archetype_popularity_pct=None,
        reasons=("meta_core_outlier",),
        is_fun=True,
    )
    row = {"deck_code": "X", "title": "STD BFU Handbuff DK", "games": 60, "win_rate": 68.3, "class": "DeathKnight"}

    normalized = _normalize_fun_row(row, scored, seen_at="2026-10-29T00:00:00+00:00")

    assert normalized["title"] == "BFU Handbuff DK"
    assert normalized["source_title"] == "STD BFU Handbuff DK"
    assert (normalized["win_rate_ci_low"], normalized["win_rate_ci_high"]) == (55.7, 78.7)
    assert normalized["variants"] == 0
    assert normalized["win_rate"] == 68.3


# --- the whole refresh on fixtures -------------------------------------------


def _catalog(period: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"period": period, "data": {"rows": rows}}


def _catalog_row(
    title: str, cls: str, ids: Any, *, games: int, win_rate: float, fmt: str = "Standard"
) -> dict[str, Any]:
    return {
        "title": title,
        "archetype": title,
        "format": fmt,
        "class": cls,
        "deck_code": _deckstring(ids),
        "games": games,
        "win_rate": win_rate,
        "url": f"https://www.hsguru.com/deck/{abs(hash(title)) % 10**6}",
    }


@pytest.fixture
def fun_deck_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Isolated storage, no HearthstoneJSON download, no HSGuru: only fixtures."""
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    index = _AnyCardIndex()
    monkeypatch.setattr(fun_decks, "_package_name_cache_ready", True)
    monkeypatch.setattr(fun_decks, "_package_quest_ids", frozenset())
    monkeypatch.setattr(fun_decks, "_package_yogg_ids", frozenset())
    with (
        patch("app.deck_decode.cards_by_dbfid", return_value=index),
        patch("app.hsguru_decks.current_hsguru_deck_period", return_value="patch_test"),
    ):
        yield


def _save_world(
    standard: list[dict[str, Any]],
    wild: list[dict[str, Any]] | None = None,
    streamer: list[dict[str, Any]] | None = None,
) -> None:
    save_dataset(
        "hsguru_deck_catalog_standard_all",
        {"source_id": "hsguru_deck_catalog_standard_all", **_catalog("patch_test", standard)},
    )
    save_dataset(
        "hsguru_deck_catalog_wild_all",
        {"source_id": "hsguru_deck_catalog_wild_all", **_catalog("patch_test", wild or [])},
    )
    save_dataset(
        "hsguru_streamer_decks_legend_1000",
        {
            "source_id": "hsguru_streamer_decks_legend_1000",
            "data": {"structured": {"rows": streamer or []}},
        },
    )


def _structured() -> dict[str, Any]:
    dataset = load_dataset("hsguru_fun_decks")
    assert dataset is not None
    return dataset["data"]["structured"]


def test_refresh_publishes_only_decks_that_pass_every_v7_rule(fun_deck_world: None) -> None:
    mage = range(401, 416)
    _save_world(
        standard=[
            # meta cores: far from every candidate below
            _catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=5000, win_rate=52.0),
            _catalog_row("Midrange Paladin", "Paladin", range(201, 216), games=3000, win_rate=51.0),
            # fun decks
            _catalog_row("STD Casino Mill Mage", "Mage", mage, games=100, win_rate=55.0),
            _catalog_row(
                "Casino Mill Mage Alt", "Mage", [*range(401, 413), 501, 502, 503], games=60, win_rate=52.0
            ),
            _catalog_row("Odd Mill Rogue", "Rogue", range(601, 616), games=80, win_rate=48.0),
            _catalog_row("Even Mill Hunter", "Hunter", range(701, 716), games=70, win_rate=46.0),
            # provably losing: 10% over 58 games
            _catalog_row("Rainbow Mill DK", "DeathKnight", range(801, 816), games=58, win_rate=10.0),
            # generic ladder name sharing 7 of 15 cards with the Paladin core
            _catalog_row(
                "Dude Paladin", "Paladin", [*range(201, 208), *range(301, 309)], games=90, win_rate=50.0
            ),
        ],
        wild=[
            _catalog_row("WLD Yogg Mill Priest", "Priest", range(901, 916), games=90, win_rate=50.0, fmt="Wild"),
        ],
        streamer=[
            # a streamer list has no games / win rate in this dataset
            {
                "Deck": "XL HL Blood DK",
                "Format": "Wild",
                "class": "DeathKnight",
                "deck_code": _deckstring(range(1001, 1016)),
            },
        ],
    )

    result = _refresh_fun_decks_unlocked()

    structured = _structured()
    rows = sorted(structured["rows"], key=lambda r: r["title"])
    assert structured["detector_version"] == "concept-v7"
    assert result["detector_version"] == "concept-v7"
    assert [r["title"] for r in rows] == [
        "Casino Mill Mage",
        "Even Mill Hunter",
        "Odd Mill Rogue",
        "Yogg Mill Priest",
    ]
    mage_row = rows[0]
    assert mage_row["source_title"] == "STD Casino Mill Mage"
    assert (mage_row["win_rate_ci_low"], mage_row["win_rate_ci_high"]) == (45.2, 64.4)
    assert mage_row["variants"] == 1
    assert [r["source_title"] for r in rows[1:]] == ["Even Mill Hunter", "Odd Mill Rogue", "WLD Yogg Mill Priest"]
    assert [r["variants"] for r in rows[1:]] == [0, 0, 0]
    assert all(r["win_rate_ci_low"] <= r["win_rate"] <= r["win_rate_ci_high"] for r in rows)
    stats = structured["stats"]
    assert stats["candidates"] == 8
    assert stats["quality_rejected"] == {"losing_win_rate": 1, "missing_stats": 1}
    assert stats["near_duplicates_absorbed"] == 1
    assert stats["rejected_by_gate"]["generic_ladder_gate"] == 1
    assert stats["published_by_class"] == {"Mage": 1, "Rogue": 1, "Hunter": 1, "Priest": 1}


def test_refresh_drops_a_remembered_deck_once_fresh_numbers_disqualify_it(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_RETENTION_HOURS", "100000")
    rogue = _catalog_row("Odd Mill Rogue", "Rogue", range(601, 616), games=100, win_rate=20.0)
    _save_world(
        standard=[_catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=5000, win_rate=52.0), rogue]
    )
    kept_cards = _catalog_row("Casino Mill Mage", "Mage", range(401, 416), games=100, win_rate=55.0)
    remembered = [
        {
            **_row("Casino Mill Mage", code=kept_cards["deck_code"], cls="Mage", games=100),
            "win_rate": 55.0,
            "win_rate_ci_low": 45.2,
            "win_rate_ci_high": 64.4,
            "variants": 0,
            "last_seen_at": "2026-10-28T00:00:00+00:00",
        },
        {
            **_row("Odd Mill Rogue", code=rogue["deck_code"], cls="Rogue", games=80),
            "win_rate": 48.0,
            "win_rate_ci_low": 37.4,
            "win_rate_ci_high": 58.8,
            "variants": 0,
            "last_seen_at": "2026-10-28T00:00:00+00:00",
        },
    ]
    save_dataset(
        "hsguru_fun_decks",
        {
            "source_id": "hsguru_fun_decks",
            "data": {"structured": {"detector_version": "concept-v7", "rows": remembered}},
        },
    )

    _refresh_fun_decks_unlocked()

    # The Mage is remembered although the catalog no longer lists it; the Rogue is
    # back in the catalog at 20% over 100 games and must not survive on old numbers.
    assert [r["title"] for r in _structured()["rows"]] == ["Casino Mill Mage"]
    assert _structured()["stats"]["quality_rejected"] == {"losing_win_rate": 1}


def test_refresh_forgets_remembered_decks_that_no_longer_meet_the_thresholds(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_RETENTION_HOURS", "100000")
    monkeypatch.setenv("HS_FUN_DECK_MIN_GAMES", "150")
    _save_world(standard=[])
    remembered = {
        **_row("Casino Mill Mage", code=_deckstring(range(401, 416)), cls="Mage", games=100),
        "win_rate": 55.0,
        "last_seen_at": "2026-10-28T00:00:00+00:00",
    }
    save_dataset(
        "hsguru_fun_decks",
        {
            "source_id": "hsguru_fun_decks",
            "data": {"structured": {"detector_version": "concept-v7", "rows": [remembered]}},
        },
    )

    _refresh_fun_decks_unlocked()

    assert _structured()["rows"] == []
    assert _structured()["stats"]["history_dropped"] == 1


# --- replay of the published concept-v6 dataset --------------------------------


def _replayed(row: dict[str, Any]) -> dict[str, Any]:
    """A published v6 row passed through the v7 row builder, as a refresh would."""
    scored = fun_decks.FunDeckScore(
        fun_score=row["fun_score"],
        max_meta_similarity=row["max_meta_similarity"],
        nearest_archetype=row["nearest_archetype"],
        nearest_deck_code=None,
        catalog_games=row["catalog_games"],
        archetype_popularity_pct=None,
        reasons=tuple(row["reasons"]),
        is_fun=True,
        concept_hit=row["concept_hit"],
        rare_archetype=row["rare_archetype"],
    )
    return _normalize_fun_row(row, scored, seen_at=row["last_seen_at"])


def test_replaying_the_prod_snapshot_with_the_v7_rules() -> None:
    snapshot = json.loads((FIXTURES / "prod_snapshot_concept_v6.json").read_text())
    rows = snapshot["rows"]
    assert (snapshot["detector_version"], len(rows)) == ("concept-v6", 46)

    verdicts = [(row, evaluate_sample(row["games"], row["win_rate"])) for row in rows]
    dropped = Counter(v.reject for _, v in verdicts if not v.ok)
    dropped_titles = {row["title"] for row, v in verdicts if not v.ok}
    survivors = [_replayed(row) for row, v in verdicts if v.ok]

    # rule 1: 9 provably losing lists + 1 list without games / win rate
    assert dropped == {"losing_win_rate": 9, "missing_stats": 1}
    assert dropped_titles == {
        "Secret Mage",
        "Rainbow DK",
        "XL HL Blood DK",
        "UU Miracle Chad Warlock",
        "Aggro Paladin",
        "Dragon Mage",
        "J-Lock",
        "BBF N'Zoth Handbuff DK",
        "Imbue Priest",
        "Y'Shaarj Druid",
    }
    assert len(survivors) == 36

    # rule 2: the one pair above 0.6 collapses into the better sampled list
    with patch("app.deck_decode.cards_by_dbfid", return_value=_AnyCardIndex()):
        cards = {r["deck_code"]: fun_decks.deck_card_multiset(r["deck_code"]) for r in survivors}
    merged, absorbed = dedupe_near_duplicates(survivors, cards)
    assert absorbed == 1
    assert [(r["source_title"], r["variants"]) for r in merged if r["variants"]] == [
        ("STD BFU Handbuff DK", 1)
    ]
    assert dedupe_near_duplicates(merged, cards)[1] == 0

    # rules 3 and 5: balanced selection, clean titles, intervals on every deck
    published = _select_published(merged, focus="standard")
    assert len(published) == 35
    assert Counter(r["class"] for r in published) == {
        "Priest": 6,
        "Paladin": 5,
        "DeathKnight": 4,
        "Shaman": 4,
        "Warlock": 3,
        "Mage": 3,
        "Druid": 3,
        "Hunter": 3,
        "Warrior": 2,
        "DemonHunter": 1,
        "Rogue": 1,
    }
    assert Counter(r["format"] for r in published) == {"Standard": 20, "Wild": 15}
    assert not [r["title"] for r in published if r["title"].upper().startswith(("STD ", "WLD "))]
    assert min(r["win_rate_ci_high"] for r in published) == 45.4
    assert all(r["win_rate_ci_low"] <= r["win_rate"] <= r["win_rate_ci_high"] for r in published)
