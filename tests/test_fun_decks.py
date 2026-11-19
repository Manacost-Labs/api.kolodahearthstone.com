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

from app import config, fun_decks
from app.fun_decks import (
    CatalogDeck,
    CatalogIndex,
    _balance_and_dedupe,
    _catalog_rows,
    _merge_history,
    _normalize_fun_row,
    _pick_diverse,
    _refresh_fun_decks_unlocked,
    _select_published,
    build_catalog_index,
    compute_fun_decks,
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
# concept-v8: sample / win-rate gate, near-duplicate lists, class balance,
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


def test_losing_cut_uses_the_unrounded_upper_bound() -> None:
    # 28.0% over 33 games: the interval is 15.6-44.97, which rounds to 45.0 for display
    # but is still below the 45 cut, so the deck is dropped.
    just_below = evaluate_sample(33, 28.0)
    assert (just_below.ok, just_below.reject) == (False, "losing_win_rate")
    assert (just_below.ci_low, just_below.ci_high) == (15.6, 45.0)

    # 28.1% over 33 games reaches 45.08 and stays.
    just_above = evaluate_sample(33, 28.1)
    assert just_above.ok is True
    assert (just_above.ci_low, just_above.ci_high) == (15.7, 45.1)


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
    # The better scoring deck has the later deck code, so only fun_score can pick it.
    cards = {"a-low": _two_ofs(range(1, 16)), "b-high": _two_ofs([*range(1, 15), 101])}
    rows = [
        _row("Low score", code="a-low", games=70, score=0.5),
        _row("High score", code="b-high", games=70, score=0.9),
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


def test_same_title_in_different_classes_keeps_every_format_and_class() -> None:
    rows = [
        _row("Reno", code="a", cls="Mage", fmt="Standard", score=0.9),
        _row("Reno", code="b", cls="Priest", fmt="Standard", score=0.8),
        _row("Reno", code="c", cls="Rogue", fmt="Wild", score=0.7),
        _row("Reno", code="d", cls="Druid", fmt="Wild", score=0.6),
    ]

    for focus in (None, "standard"):
        published = _select_published(rows, focus=focus)

        assert sorted((r["format"], r["class"]) for r in published) == [
            ("Standard", "Mage"),
            ("Standard", "Priest"),
            ("Wild", "Druid"),
            ("Wild", "Rogue"),
        ], focus


def test_same_title_in_the_same_format_and_class_keeps_the_higher_fun_score() -> None:
    rows = [
        _row("Reno", code="weak", cls="Mage", fmt="Standard", score=0.7),
        _row("Reno", code="strong", cls="Mage", fmt="Standard", score=0.9),
        _row("Reno", code="wild", cls="Mage", fmt="Wild", score=0.5),
    ]

    for focus in (None, "standard"):
        published = _select_published(rows, focus=focus)

        assert sorted(r["deck_code"] for r in published) == ["strong", "wild"], focus


_classes = st.sampled_from(
    ["Druid", "Hunter", "Mage", "Paladin", "Priest", "Rogue", "Shaman", "Warlock", "Warrior", "DeathKnight", "DemonHunter"]
)


@settings(max_examples=200, deadline=None)
@given(
    spec=st.lists(
        st.tuples(
            st.sampled_from(["Standard", "Wild"]),
            _classes,
            st.integers(0, 100),
            # a few shared names (decks of different classes often share one) and some unique ones
            st.sampled_from(["Reno", "Odd Mill", "XL Yogg", "", ""]),
        ),
        min_size=1,
        max_size=90,
    ),
    focus=st.sampled_from([None, "standard"]),
)
def test_every_format_and_class_with_a_candidate_is_published(
    spec: list[tuple[str, str, int, str]], focus: str | None
) -> None:
    rows = [
        _row(shared or f"Deck {i}", code=f"D{i}", fmt=fmt, cls=cls, score=score / 100)
        for i, (fmt, cls, score, shared) in enumerate(spec)
    ]

    published = _select_published(rows, focus=focus)

    assert {(r["format"], r["class"]) for r in published} == {(r["format"], r["class"]) for r in rows}
    assert len({r["deck_code"] for r in published}) == len(published)


def test_class_seed_prefers_a_deck_that_fits_the_xl_cap_over_a_stronger_xl_deck() -> None:
    rows = [
        _row("XL HL Rogue", cls="Rogue", score=0.99),
        _row("XL HL Priest", cls="Priest", score=0.98),
        _row("Plain Priest", cls="Priest", score=0.60),
        _row("Mill Mage", cls="Mage", score=0.50),
    ]

    picked = _pick_diverse(rows, limit=10, max_per_class=3, max_xl_hl=1)

    # the cap is used up by the Rogue, so the Priest is represented by the plain deck
    assert sorted(r["title"] for r in picked) == ["Mill Mage", "Plain Priest", "XL HL Rogue"]


# --- 4. concept-v8 classification ---------------------------------------------
#
# A ladder deck of the catalog (100 000 games: far above HS_FUN_DECK_LADDER_SHARE_PCT of
# its format) and candidates that share N of its 15 two-ofs. Jaccard of two 15-card
# lists of two-ofs sharing N cards is N / (30 - N):
#   N=0 -> 0.0, N=6 -> 0.25, N=8 -> 0.3636, N=10 -> 0.5, N=11 -> 0.5789, N=13 -> 0.7647

LADDER_GAMES = 100_000
SPICY_CARD = 120231  # Mecha'thun marker: a spicy package on its own


def _deck(
    code: str,
    ids: Any,
    archetype: str,
    *,
    cls: str = "Rogue",
    fmt: str = "standard",
    games: int = 100,
) -> CatalogDeck:
    return CatalogDeck(
        deck_code=code,
        cards=_two_ofs(ids),
        archetype=archetype.casefold(),
        label=archetype,
        hero_class=cls.casefold(),
        format=fmt,
        games=games,
    )


def _ladder(
    archetype: str = "Ayaya Rogue", *, cls: str = "Rogue", fmt: str = "standard"
) -> CatalogDeck:
    return _deck("LADDER", range(1, 16), archetype, cls=cls, fmt=fmt, games=LADDER_GAMES)


def _sharing(n: int, *, extra: Any = (), base: int = 101) -> Counter[int]:
    """A 15 two-of list that shares `n` cards with the ladder deck (`extra` replaces
    the last unshared cards, e.g. with a package marker)."""
    ids = [*range(1, n + 1), *range(base, base + 15 - n)]
    for position, card in enumerate(extra, start=1):
        ids[-position] = card
    return _two_ofs(ids)


def _classify(
    title: str,
    cards: Counter[int],
    *decks: CatalogDeck,
    cls: str = "Rogue",
    fmt: str = "Standard",
    **kwargs: Any,
) -> Any:
    return score_fun_deck(
        deck_code="CANDIDATE",
        title=title,
        format_name=fmt,
        cards=cards,
        hero_class=cls,
        index=CatalogIndex([*decks]),
        **kwargs,
    )


@pytest.mark.parametrize("fmt", ["Standard", "Wild"])
def test_ladder_member_gate_rejects_a_concept_titled_ladder_list(fmt: str) -> None:
    ladder = _ladder(fmt=fmt.casefold())

    scored = _classify("Casino Yogg Mill Rogue", _sharing(11), ladder, fmt=fmt)

    assert scored.ladder_sim == 0.5789
    assert scored.concept_hit is True
    assert scored.is_fun is False
    assert scored.kind is None
    assert "ladder_member_gate" in scored.reasons


@pytest.mark.parametrize("fmt", ["Standard", "Wild"])
def test_ladder_member_gate_rejects_a_ladder_list_with_a_spicy_package(fmt: str) -> None:
    ladder = _ladder(fmt=fmt.casefold())

    scored = _classify("Custom Rogue", _sharing(11, extra=[SPICY_CARD]), ladder, fmt=fmt)

    assert "card_package" in scored.reasons
    assert scored.is_fun is False
    assert "ladder_member_gate" in scored.reasons


def test_ladder_member_gate_lets_a_list_below_the_threshold_through() -> None:
    scored = _classify("Casino Mill Rogue", _sharing(10), _ladder())

    assert scored.ladder_sim == 0.5
    assert "ladder_member_gate" not in scored.reasons
    assert scored.is_fun is True


def test_ladder_member_gate_threshold_is_inclusive_and_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_LADDER_MEMBER_SIM", "0.5")

    scored = _classify("Casino Mill Rogue", _sharing(10), _ladder())

    assert "ladder_member_gate" in scored.reasons
    assert scored.is_fun is False


def test_a_list_close_to_a_rare_archetype_is_not_a_ladder_member() -> None:
    rare = _deck("RARE", range(1, 16), "Imbue Druid", cls="Rogue", games=60)
    filler = _deck("OTHER", range(500, 515), "Other Rogue", games=LADDER_GAMES)

    scored = _classify("Imbue Druid", _sharing(13), rare, filler)

    assert scored.ladder_sim == 0.0
    assert "ladder_member_gate" not in scored.reasons


def test_the_ladder_share_threshold_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middle = _deck("MIDDLE", range(1, 16), "Obscure Rogue", games=700)
    big = _deck("BIG", range(500, 515), "Big Rogue", games=99_300)  # Obscure = 0.7 %

    assert _classify("Casino Mill Rogue", _sharing(11), middle, big).ladder_sim == 0.5789
    monkeypatch.setenv("HS_FUN_DECK_LADDER_SHARE_PCT", "1.0")
    assert _classify("Casino Mill Rogue", _sharing(11), middle, big).ladder_sim == 0.0


def test_ladder_title_gate_rejects_a_ladder_named_list_that_resembles_its_archetype() -> None:
    ladder = _deck("LADDER", range(1, 16), "Treant Druid", cls="Druid", games=LADDER_GAMES)

    scored = _classify("STD Treant Druid", _sharing(8), ladder, cls="Druid")

    assert scored.ladder_sim == 0.3636  # well below the member gate
    assert scored.is_fun is False
    assert "ladder_title_gate" in scored.reasons
    assert "ladder_member_gate" not in scored.reasons


def test_ladder_title_gate_lets_a_distant_list_with_the_same_name_through() -> None:
    ladder = _deck("LADDER", range(1, 16), "Treant Druid", cls="Druid", games=LADDER_GAMES)

    scored = _classify("Treant Druid", _sharing(6), ladder, cls="Druid")  # 0.25

    assert "ladder_title_gate" not in scored.reasons
    assert scored.kind == "brew"


def test_ladder_title_gate_knows_named_ladder_packages_even_with_a_tiny_share() -> None:
    tiny = _deck("TINY", range(1, 16), "Thief Priest", cls="Priest", games=50)
    big = _deck("BIG", range(500, 515), "Other Priest", cls="Priest", games=LADDER_GAMES)

    scored = _classify("Thief Priest", _sharing(8), tiny, big, cls="Priest")

    assert "ladder_title_gate" in scored.reasons
    assert scored.is_fun is False


def test_a_title_of_a_rare_archetype_is_not_a_ladder_title() -> None:
    rare = _deck("RARE", range(1, 16), "Imbue Druid", cls="Druid", games=60)
    big = _deck("BIG", range(500, 515), "Treant Druid", cls="Druid", games=LADDER_GAMES)

    scored = _classify("Imbue Druid", _sharing(8), rare, big, cls="Druid")

    assert "ladder_title_gate" not in scored.reasons


@pytest.mark.parametrize(
    "title",
    ["Aggro Paladin", "Dude Paladin", "STD Mech Paladin", "Face Paladin", "Tempo Paladin"],
)
def test_generic_ladder_gate_rejects_a_style_name_at_any_similarity(title: str) -> None:
    scored = _classify(title, _sharing(0), _ladder("Pure Paladin", cls="Paladin"), cls="Paladin")

    assert scored.ladder_sim == 0.0
    assert scored.is_fun is False
    assert scored.kind is None
    assert "generic_ladder_gate" in scored.reasons


def test_generic_style_names_are_not_forgotten_archetypes() -> None:
    rare = _deck("RARE", range(1, 16), "Dude Paladin", cls="Paladin", games=60)
    big = _deck("BIG", range(500, 515), "Pure Paladin", cls="Paladin", games=LADDER_GAMES)

    scored = _classify("Dude Paladin", _sharing(13), rare, big, cls="Paladin")

    assert scored.kind is None  # not off_meta, although the list has a close rare twin
    assert "generic_ladder_gate" in scored.reasons


def test_generic_ladder_gate_does_not_apply_to_a_list_with_a_spicy_package() -> None:
    scored = _classify(
        "Control Paladin",
        _sharing(0, extra=[SPICY_CARD]),
        _ladder("Pure Paladin", cls="Paladin"),
        cls="Paladin",
    )

    assert "generic_ladder_gate" not in scored.reasons
    assert scored.kind == "brew"


@pytest.mark.parametrize("title", ["Imbue Paladin", "Casino Dude Paladin", "Aggro Paladin Mill"])
def test_titles_that_are_not_a_bare_style_and_class_are_not_generic(title: str) -> None:
    scored = _classify(title, _sharing(0), _ladder("Pure Paladin", cls="Paladin"), cls="Paladin")

    assert "generic_ladder_gate" not in scored.reasons


def test_needs_fun_signal_gate_rejects_a_list_that_is_a_copy_of_an_unnamed_deck() -> None:
    twin = _deck("TWIN", [*range(1, 14), 201, 202], "", games=60)  # 13 shared of 15: 0.7647
    far_ladder = _deck("FAR", range(500, 515), "Other Rogue", games=LADDER_GAMES)

    scored = _classify("Plain Rogue", _sharing(13), twin, far_ladder)

    assert scored.twin_sim == 0.7647
    assert scored.concept_hit is False
    assert scored.is_fun is False
    assert scored.kind is None
    assert "needs_fun_signal_gate" in scored.reasons


def test_brew_by_a_concept_title() -> None:
    twin = _deck("TWIN", [*range(201, 214), 1, 2], "Casino Mill Mage", cls="Mage", games=60)
    big = _deck("BIG", range(500, 515), "Pirate Mage", cls="Mage", games=LADDER_GAMES)

    scored = _classify("Casino Yogg Mill Mage", _sharing(0, base=201), twin, big, cls="Mage")

    assert scored.twin_sim >= 0.75  # not unique, still a brew by its concept
    assert scored.kind == "brew"
    assert scored.is_fun is True
    assert "concept_title" in scored.reasons
    assert "unique_list" not in scored.reasons


def test_brew_by_a_spicy_package() -> None:
    twin = _deck("TWIN", [*range(201, 214), 1, 2], "Custom Warlock", cls="Warlock", games=60)
    big = _deck("BIG", range(500, 515), "Other Warlock", cls="Warlock", games=LADDER_GAMES)

    scored = _classify(
        "Custom Warlock", _sharing(0, base=201, extra=[SPICY_CARD]), twin, big, cls="Warlock"
    )

    assert scored.twin_sim >= 0.75
    assert scored.kind == "brew"
    assert scored.is_fun is True
    assert {"card_package", "pkg:mechathun"} <= set(scored.reasons)


def test_brew_by_a_unique_list() -> None:
    scored = _classify(
        "Imbue Hunter",
        _sharing(0),
        _deck("NEIGHBOUR", range(1, 4), "N'Zoth Mech Hunter", cls="Hunter", games=60),
        cls="Hunter",
    )

    assert scored.twin_sim < 0.75
    assert scored.concept_hit is False
    assert scored.kind == "brew"
    assert scored.is_fun is True
    assert {"unique_list", "far_from_ladder"} <= set(scored.reasons)


def test_off_meta_is_a_rare_named_archetype_without_a_fun_signal() -> None:
    twin = _deck("TWIN", [*range(1, 14), 201, 202], "Imbue Druid", cls="Druid", games=60)  # 0.7647
    ladder = _deck("BIG", range(500, 515), "Treant Druid", cls="Druid", games=LADDER_GAMES)

    scored = _classify("Imbue Druid", _sharing(13), twin, ladder, cls="Druid")

    assert scored.content_archetype == "imbue druid"
    assert scored.content_sim == 0.7647
    assert scored.kind == "off_meta"
    assert scored.is_fun is True
    assert "unique_list" not in scored.reasons
    assert "concept_title" not in scored.reasons


def test_off_meta_does_not_need_the_brew_score_threshold() -> None:
    twin = _deck("TWIN", [*range(1, 14), 201, 202], "Imbue Druid", cls="Druid", games=60)
    big = _deck("BIG", range(500, 515), "Treant Druid", cls="Druid", games=LADDER_GAMES)

    scored = _classify("Imbue Druid", _sharing(13), twin, big, cls="Druid", min_score=0.99)

    assert scored.kind == "off_meta"
    assert scored.is_fun is True


def test_a_brew_below_the_score_threshold_is_not_published() -> None:
    scored = _classify("Plain Rogue", _sharing(0), _ladder(), min_score=0.99)

    assert scored.kind == "brew"
    assert scored.is_fun is False
    assert "below_min_score" in scored.reasons


def test_a_list_with_no_neighbours_in_the_catalog_is_a_unique_brew() -> None:
    scored = _classify("Plain Rogue", _sharing(0))

    assert (scored.ladder_sim, scored.twin_sim) == (0.0, 0.0)
    assert scored.kind == "brew"


def test_the_candidate_does_not_match_itself() -> None:
    own = _deck("CANDIDATE", range(1, 16), "Plain Rogue", games=60)

    scored = _classify("Plain Rogue", _two_ofs(range(1, 16)), own, _ladder("Other Rogue"))

    assert scored.twin_sim == 1.0  # the ladder deck has the same 15 cards...
    ladder_only = _classify("Plain Rogue", _two_ofs(range(1, 16)), own)
    assert ladder_only.twin_sim == 0.0  # ...but its own entry is ignored


def test_only_the_same_format_and_class_are_compared() -> None:
    other_class = _deck("A", range(1, 16), "Pure Paladin", cls="Paladin", games=LADDER_GAMES)
    other_format = _deck("B", range(1, 16), "Ayaya Rogue", fmt="wild", games=LADDER_GAMES)

    scored = _classify("Plain Rogue", _two_ofs(range(1, 16)), other_class, other_format)

    assert (scored.ladder_sim, scored.twin_sim) == (0.0, 0.0)


def test_the_title_mismatch_bonus_and_the_nearest_name_penalties_are_gone() -> None:
    scored = _classify("Imbue Hunter", _sharing(6), _ladder("Egglock"))

    assert scored.kind == "brew"
    for removed in (
        "title_mismatch_vs_nearest",
        "named_ladder_archetype",
        "known_ladder_archetype",
        "near_known_ladder_package",
        "self_named_archetype",
        "similarity_gate",
        "standard_balance_boost",
    ):
        assert removed not in scored.reasons


def test_the_score_adds_up_the_weights_without_a_ceiling() -> None:
    # distance from the ladder 0.40, concept 0.20, package 0.20, uniqueness 0.10, meme/cheese 0.10
    concept = _classify("Casino Mill Mage", _sharing(0), cls="Mage")
    assert concept.fun_score == 0.7  # 0.40 + 0.20 + 0.10 (no neighbour at all)

    with_package = _classify("Casino Mill Mage", _sharing(0, extra=[SPICY_CARD]), cls="Mage")
    assert with_package.fun_score == 0.82  # + 0.12 for one spicy package

    meme = _classify("Casino Mill Mage", _sharing(0), cls="Mage", win_rate=40.0, catalog_games_hint=60)
    assert meme.fun_score == 0.8
    assert "meme_low_winrate" in meme.reasons

    cheese = _classify(
        "Casino Mill Mage", _sharing(0), cls="Mage", win_rate=70.0, catalog_games_hint=60
    )
    assert cheese.fun_score == 0.8
    assert "cheese_high_winrate" in cheese.reasons

    big_sample = _classify(
        "Casino Mill Mage", _sharing(0), cls="Mage", win_rate=70.0, catalog_games_hint=500
    )
    assert "cheese_high_winrate" not in big_sample.reasons


def test_the_closer_to_the_ladder_the_lower_the_score() -> None:
    ladder = _ladder()
    far = _classify("Casino Mill Rogue", _sharing(2), ladder, min_score=0.1)
    near = _classify("Casino Mill Rogue", _sharing(10), ladder, min_score=0.1)

    assert far.is_fun and near.is_fun
    assert far.fun_score > near.fun_score
    assert max(far.fun_score, near.fun_score) < 1.0


def test_the_score_weights_add_up_to_one() -> None:
    assert sum(fun_decks.SCORE_WEIGHTS.values()) == pytest.approx(1.0)


def test_the_new_thresholds_have_defaults() -> None:
    assert (
        config.fun_deck_ladder_share_pct(),
        config.fun_deck_ladder_member_sim(),
        config.fun_deck_twin_sim(),
    ) == (0.5, 0.55, 0.75)


# --- catalog index ---------------------------------------------------------------


def _catalog_deck_row(
    title: str, cls: str, ids: Any, *, games: int, fmt: str = "Standard", archetype: str | None = None
) -> dict[str, Any]:
    return {
        "title": title,
        "archetype": archetype if archetype is not None else title,
        "format": fmt,
        "class": cls,
        "deck_code": _deckstring(ids),
        "games": games,
        "win_rate": 52.0,
    }


def _index_world(standard: list[dict[str, Any]], wild: list[dict[str, Any]], *, legend: list[dict[str, Any]] | None = None) -> None:
    for source_id, rows in (
        ("hsguru_deck_catalog_standard_all", standard),
        ("hsguru_deck_catalog_wild_all", wild),
        ("hsguru_deck_catalog_standard_legend", legend or []),
    ):
        save_dataset(source_id, {"source_id": source_id, **_catalog("patch_test", rows)})


def test_catalog_index_groups_lists_by_format_and_class(fun_deck_world: None) -> None:
    _index_world(
        standard=[
            _catalog_deck_row("STD Egg DK", "DeathKnight", range(1, 16), games=900),
            _catalog_deck_row("Ayaya Rogue", "Rogue", range(21, 36), games=500),
            _catalog_deck_row("Rare Rogue", "Rogue", range(41, 56), games=44),
        ],
        wild=[_catalog_deck_row("Wild Egg DK", "DeathKnight", range(1, 16), games=300, fmt="Wild")],
    )

    index = build_catalog_index()

    egg = index.neighbours("standard", "deathknight")
    assert [(d.archetype, d.games, d.format, d.hero_class) for d in egg] == [
        ("egg dk", 900, "standard", "deathknight")
    ]
    assert [d.archetype for d in index.neighbours("wild", "deathknight")] == ["egg dk"]
    assert [d.archetype for d in index.neighbours("standard", "rogue")] == ["ayaya rogue"]  # 44 games: not compared
    assert index.neighbours("wild", "rogue") == []


def test_catalog_index_counts_a_list_once_across_catalogs(fun_deck_world: None) -> None:
    row = _catalog_deck_row("Ayaya Rogue", "Rogue", range(21, 36), games=500)
    _index_world(
        standard=[row],
        wild=[],
        legend=[{**row, "games": 800}, _catalog_deck_row("Other", "Mage", range(1, 16), games=200)],
    )

    index = build_catalog_index()

    assert [(d.games) for d in index.neighbours("standard", "rogue")] == [800]
    assert index.archetype_share_pct("standard", "ayaya rogue") == 80.0  # 800 of 1000 games


def test_catalog_index_share_is_per_format_and_counts_small_lists(fun_deck_world: None) -> None:
    _index_world(
        standard=[
            _catalog_deck_row("Big", "Rogue", range(1, 16), games=900),
            _catalog_deck_row("Tiny A", "Mage", range(21, 36), games=40, archetype="Obscure"),
            _catalog_deck_row("Tiny B", "Mage", range(41, 56), games=60, archetype="Obscure"),
        ],
        wild=[_catalog_deck_row("Wild Obscure", "Mage", range(1, 16), games=100, fmt="Wild", archetype="Obscure")],
    )

    index = build_catalog_index()

    assert index.archetype_share_pct("standard", "obscure") == 10.0  # (40 + 60) of 1000
    assert index.archetype_share_pct("wild", "obscure") == 100.0
    assert index.archetype_share_pct("standard", "missing") == 0.0
    assert index.is_ladder_archetype("standard", "big") is True
    assert index.is_ladder_archetype("standard", "obscure") is True  # 10 % of the format


def test_catalog_index_prefix_is_stripped_and_named_ladder_packages_are_always_ladder(
    fun_deck_world: None,
) -> None:
    _index_world(
        standard=[
            _catalog_deck_row("Big", "Rogue", range(1, 16), games=100_000),
            _catalog_deck_row("STD WLD Thief Priest", "Priest", range(21, 36), games=60),
            _catalog_deck_row("Imbue Hunter", "Hunter", range(41, 56), games=60),
        ],
        wild=[],
    )

    index = build_catalog_index()

    assert index.is_ladder_archetype("standard", "thief priest") is True  # by name, share 0.06 %
    assert index.is_ladder_archetype("standard", "imbue hunter") is False
    assert "thief priest" in index.ladder_names
    assert "imbue hunter" not in index.ladder_names


def test_catalog_index_ignores_a_catalog_of_another_period(fun_deck_world: None) -> None:
    save_dataset(
        "hsguru_deck_catalog_standard_all",
        {
            "source_id": "hsguru_deck_catalog_standard_all",
            **_catalog("patch_old", [_catalog_deck_row("Big", "Rogue", range(1, 16), games=900)]),
        },
    )

    assert build_catalog_index().neighbours("standard", "rogue") == []


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
        reasons=("far_from_ladder",),
        is_fun=True,
        kind="off_meta",
        ladder_sim=0.1,
        content_sim=0.8,
        twin_sim=0.8,
        content_archetype="imbue dk",
    )
    row = {"deck_code": "X", "title": "STD BFU Handbuff DK", "games": 60, "win_rate": 68.3, "class": "DeathKnight"}

    normalized = _normalize_fun_row(row, scored, seen_at="2026-10-29T00:00:00+00:00")

    assert normalized["title"] == "BFU Handbuff DK"
    assert normalized["source_title"] == "STD BFU Handbuff DK"
    assert (normalized["win_rate_ci_low"], normalized["win_rate_ci_high"]) == (55.7, 78.7)
    assert normalized["variants"] == 0
    assert normalized["win_rate"] == 68.3
    assert normalized["kind"] == "off_meta"
    assert (normalized["ladder_sim"], normalized["content_sim"], normalized["twin_sim"]) == (0.1, 0.8, 0.8)
    assert normalized["content_archetype"] == "imbue dk"


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


def test_refresh_publishes_only_decks_that_pass_every_v8_rule(fun_deck_world: None) -> None:
    mage = range(401, 416)
    _save_world(
        standard=[
            # meta cores: far from every candidate below
            _catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0),
            _catalog_row("Midrange Paladin", "Paladin", range(201, 216), games=300_000, win_rate=51.0),
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
                "Deck": "XL HL Casino DK",
                "Format": "Wild",
                "class": "DeathKnight",
                "deck_code": _deckstring(range(1001, 1016)),
            },
        ],
    )

    result = _refresh_fun_decks_unlocked()

    structured = _structured()
    rows = sorted(structured["rows"], key=lambda r: r["title"])
    assert structured["detector_version"] == "concept-v8"
    assert result["detector_version"] == "concept-v8"
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
    assert {r["kind"] for r in rows} == {"brew"}
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
        standard=[_catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0), rogue]
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
            "data": {"structured": {"detector_version": "concept-v8", "rows": remembered}},
        },
    )

    _refresh_fun_decks_unlocked()

    # The Mage is remembered although the catalog no longer lists it; the Rogue is
    # back in the catalog at 20% over 100 games and must not survive on old numbers.
    assert [r["title"] for r in _structured()["rows"]] == ["Casino Mill Mage"]
    assert _structured()["stats"]["quality_rejected"] == {"losing_win_rate": 1}


def _remembered_warrior(deck_code: str) -> dict[str, Any]:
    return {
        **_row("Casino Mill Warrior", code=deck_code, cls="Warrior", games=100),
        "win_rate": 55.0,
        "win_rate_ci_low": 45.2,
        "win_rate_ci_high": 64.4,
        "variants": 0,
        "last_seen_at": "2026-10-28T00:00:00+00:00",
    }


def _save_history(rows: list[dict[str, Any]]) -> None:
    save_dataset(
        "hsguru_fun_decks",
        {
            "source_id": "hsguru_fun_decks",
            "data": {"structured": {"detector_version": "concept-v8", "rows": rows}},
        },
    )


def test_refresh_drops_a_remembered_deck_whose_fresh_candidate_is_no_longer_fun_and_losing(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_RETENTION_HOURS", "100000")
    # The candidate shares 14 of 15 two-ofs with the Warrior core: too close to the
    # meta, so it is rejected as not fun. Its fresh numbers (19 wins of 60) are also
    # provably losing, while the remembered row still carries 55.0% over 100 games.
    candidate = _catalog_row("Casino Mill Warrior", "Warrior", [*range(1, 15), 101], games=60, win_rate=31.7)
    _save_world(
        standard=[
            _catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0),
            candidate,
        ]
    )
    _save_history([_remembered_warrior(candidate["deck_code"])])

    _refresh_fun_decks_unlocked()

    structured = _structured()
    assert structured["rows"] == []
    stats = structured["stats"]
    # counted once: as a deck that is not fun, not again as a quality rejection
    assert (stats["candidates"], stats["rejected"], stats["quality_rejected"]) == (1, 1, {})
    assert stats["rejected_by_gate"] == {"ladder_member_gate": 1}
    assert stats["history_dropped"] == 1


def test_refresh_drops_a_remembered_deck_that_became_a_ladder_member_despite_good_numbers(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_RETENTION_HOURS", "100000")
    # 14 of 15 two-ofs shared with the Warrior ladder core: a ladder member now. Its
    # fresh numbers are fine (50.0% over 60 games) and the remembered row still carries
    # an old, harmless-looking similarity.
    candidate = _catalog_row("Casino Mill Warrior", "Warrior", [*range(1, 15), 101], games=60, win_rate=50.0)
    _save_world(
        standard=[
            _catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0),
            candidate,
        ]
    )
    _save_history([{**_remembered_warrior(candidate["deck_code"]), "ladder_sim": 0.1}])

    _refresh_fun_decks_unlocked()

    structured = _structured()
    assert structured["rows"] == []
    assert structured["stats"]["history_dropped"] == 1
    assert structured["stats"]["rejected_by_gate"] == {"ladder_member_gate": 1}


def test_refresh_keeps_a_remembered_deck_that_is_not_among_the_fresh_candidates(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_RETENTION_HOURS", "100000")
    _save_world(
        standard=[_catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0)]
    )
    _save_history([_remembered_warrior(_deckstring([*range(1, 15), 101]))])

    _refresh_fun_decks_unlocked()

    assert [r["title"] for r in _structured()["rows"]] == ["Casino Mill Warrior"]
    assert _structured()["stats"]["history_dropped"] == 0


def test_a_standard_run_does_not_evict_the_wild_row_of_a_deck_turned_away_in_standard(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_RETENTION_HOURS", "100000")
    candidate = _catalog_row("Casino Mill Warrior", "Warrior", [*range(1, 15), 101], games=60, win_rate=50.0)
    _save_world(
        standard=[
            _catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0),
            candidate,
        ]
    )
    remembered = _remembered_warrior(candidate["deck_code"])
    _save_history([{**remembered, "format": "Standard"}, {**remembered, "format": "Wild"}])

    _refresh_fun_decks_unlocked(format_focus="standard")

    assert [(r["title"], r["format"]) for r in _structured()["rows"]] == [("Casino Mill Warrior", "Wild")]
    assert _structured()["stats"]["history_dropped"] == 1


def test_a_standard_run_does_not_evict_the_wild_row_of_the_same_deck_code(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_RETENTION_HOURS", "100000")
    losing = _catalog_row("Casino Mill Mage", "Mage", range(401, 416), games=58, win_rate=10.0)
    _save_world(
        standard=[
            _catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0),
            losing,
        ]
    )
    remembered = {
        **_row("Casino Mill Mage", code=losing["deck_code"], cls="Mage", games=100),
        "win_rate": 55.0,
        "win_rate_ci_low": 45.2,
        "win_rate_ci_high": 64.4,
        "variants": 0,
        "last_seen_at": "2026-10-28T00:00:00+00:00",
    }
    _save_history([{**remembered, "format": "Standard"}, {**remembered, "format": "Wild"}])

    # The fresh Standard listing of the code is provably losing (10.0% over 58 games).
    _refresh_fun_decks_unlocked(format_focus="standard")

    structured = _structured()
    assert [(r["title"], r["format"]) for r in structured["rows"]] == [("Casino Mill Mage", "Wild")]
    assert structured["stats"]["quality_rejected"] == {"losing_win_rate": 1}
    assert structured["stats"]["history_dropped"] == 1


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
            "data": {"structured": {"detector_version": "concept-v8", "rows": [remembered]}},
        },
    )

    _refresh_fun_decks_unlocked()

    assert _structured()["rows"] == []
    assert _structured()["stats"]["history_dropped"] == 1


def _variants_by_title() -> dict[str, int]:
    return {row["title"]: row["variants"] for row in _structured()["rows"]}


def test_refresh_recounts_variants_only_for_the_format_it_looked_at(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_RETENTION_HOURS", "100000")
    _save_world(standard=[])
    remembered = [
        {
            **_row("Casino Mill Mage", code=_deckstring(range(401, 416)), cls="Mage", games=100),
            "win_rate": 55.0,
            "variants": 5,  # stale: no near-duplicate is in this run's pool
            "last_seen_at": "2026-10-28T00:00:00+00:00",
        },
        {
            **_row("Yogg Mill Priest", code=_deckstring(range(901, 916)), cls="Priest", fmt="Wild", games=90),
            "win_rate": 50.0,
            "variants": 3,  # counted by an earlier full run; a Standard run cannot see Wild lists
            "last_seen_at": "2026-10-28T00:00:00+00:00",
        },
    ]
    save_dataset(
        "hsguru_fun_decks",
        {
            "source_id": "hsguru_fun_decks",
            "data": {"structured": {"detector_version": "concept-v8", "rows": remembered}},
        },
    )

    _refresh_fun_decks_unlocked(format_focus="standard")

    assert _variants_by_title() == {"Casino Mill Mage": 0, "Yogg Mill Priest": 3}

    _refresh_fun_decks_unlocked()

    assert _variants_by_title() == {"Casino Mill Mage": 0, "Yogg Mill Priest": 0}


def test_variants_of_a_wild_deck_survive_a_standard_run_between_two_full_runs(
    fun_deck_world: None,
) -> None:
    _save_world(
        standard=[
            _catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0),
            _catalog_row("Casino Mill Mage", "Mage", range(401, 416), games=100, win_rate=55.0),
        ],
        wild=[
            # a ladder deck keeps the two Priest lists below the ladder share
            _catalog_row("Big Wild Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0, fmt="Wild"),
            _catalog_row("WLD Yogg Mill Priest", "Priest", range(901, 916), games=90, win_rate=50.0, fmt="Wild"),
            _catalog_row(
                "WLD Yogg Mill Priest Alt",
                "Priest",
                [*range(901, 913), 951, 952, 953],
                games=60,
                win_rate=48.0,
                fmt="Wild",
            ),
        ],
    )

    _refresh_fun_decks_unlocked()
    after_full = _variants_by_title()
    _refresh_fun_decks_unlocked(format_focus="standard")
    after_standard = _variants_by_title()
    _refresh_fun_decks_unlocked()
    after_second_full = _variants_by_title()

    assert after_full == {"Casino Mill Mage": 0, "Yogg Mill Priest": 1}
    assert after_standard == after_full
    assert after_second_full == after_full


# --- 4b. packages depend on the meta (F1) ------------------------------------------

_PACKAGE_MARKER_CARDS = {
    "reno_highlander": [2883],
    "odd_even": [48158],
    "mechathun": [120231],
    "togwaggle_mill": [46589],
    "cthun": [102680],
    "whizbang": [50477],
    "sathrovarr": [56189],
    "steal_package": [47211, 52715],
    "yogg_package": [990001],
}


@pytest.fixture
def yogg_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fun_decks, "_package_name_cache_ready", True)
    monkeypatch.setattr(fun_decks, "_package_quest_ids", frozenset())
    monkeypatch.setattr(fun_decks, "_package_yogg_ids", frozenset({990001}))


def _marked_ladder(markers: list[int], *, games: int = LADDER_GAMES, cls: str = "Rogue") -> CatalogDeck:
    """A big catalog deck that carries the package markers."""
    return _deck("MARKED", [*range(500, 515 - len(markers)), *markers], "Meta Rogue", cls=cls, games=games)


@pytest.mark.parametrize("label", sorted(fun_decks._SPICY_PACKAGES))
def test_a_package_played_in_much_of_the_format_is_not_a_fun_signal(
    label: str, yogg_marker: None
) -> None:
    markers = _PACKAGE_MARKER_CARDS[label]
    twin = _deck("TWIN", [*range(1, 14), 201, 202], "", games=60)  # unnamed twin, 0.7647

    scored = _classify(
        "Custom Rogue", _sharing(13, extra=markers), twin, _marked_ladder(markers)
    )

    assert scored.concept_hit is False
    assert "card_package" not in scored.reasons
    assert "neutral_package" in scored.reasons
    assert scored.is_fun is False
    assert "needs_fun_signal_gate" in scored.reasons


@pytest.mark.parametrize("label", sorted(fun_decks._SPICY_PACKAGES))
def test_a_neutral_package_is_reported_as_meta_pkg_not_pkg(label: str, yogg_marker: None) -> None:
    markers = _PACKAGE_MARKER_CARDS[label]

    scored = _classify("Custom Rogue", _sharing(0, extra=markers), _marked_ladder(markers))

    assert f"meta_pkg:{label}" in scored.reasons
    assert not [r for r in scored.reasons if r.startswith("pkg:")]  # the site shows pkg:* as chips


def test_a_sharp_package_is_reported_as_pkg_without_meta_pkg(yogg_marker: None) -> None:
    scored = _classify("Custom Rogue", _sharing(0, extra=[SPICY_CARD]))

    assert "pkg:mechathun" in scored.reasons
    assert not [r for r in scored.reasons if r.startswith("meta_pkg:")]


@pytest.mark.parametrize("label", sorted(fun_decks._SPICY_PACKAGES))
def test_a_package_of_few_games_is_still_a_fun_signal(label: str, yogg_marker: None) -> None:
    markers = _PACKAGE_MARKER_CARDS[label]
    twin = _deck("TWIN", [*range(1, 14), 201, 202], "", games=60)
    big = _deck("BIG", range(500, 515), "Meta Rogue", games=LADDER_GAMES)

    scored = _classify("Custom Rogue", _sharing(13, extra=markers), twin, big)

    assert scored.concept_hit is True
    assert {"card_package", f"pkg:{label}"} <= set(scored.reasons)
    assert scored.kind == "brew"


def test_a_package_does_not_lift_the_generic_ladder_gate_when_it_is_meta(yogg_marker: None) -> None:
    markers = _PACKAGE_MARKER_CARDS["yogg_package"]

    scored = _classify(
        "Beast Rogue", _sharing(0, extra=markers), _marked_ladder(markers)
    )

    assert "generic_ladder_gate" in scored.reasons
    assert scored.kind is None


def test_the_spicy_package_share_is_per_format_and_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, yogg_marker: None
) -> None:
    markers = _PACKAGE_MARKER_CARDS["mechathun"]
    # 5 % of the Standard games carry the marker; in Wild none do.
    carrier = _marked_ladder(markers, games=5_000)
    rest = _deck("REST", range(600, 615), "Meta Rogue", games=95_000)
    cards = _sharing(0, extra=markers)

    assert "card_package" not in _classify("Custom Rogue", cards, carrier, rest).reasons
    assert "card_package" in _classify("Custom Rogue", cards, carrier, rest, fmt="Wild").reasons
    monkeypatch.setenv("HS_FUN_DECK_SPICY_PACKAGE_MAX_SHARE_PCT", "10")
    assert "card_package" in _classify("Custom Rogue", cards, carrier, rest).reasons


def test_the_spicy_package_share_default_is_three_percent() -> None:
    assert config.fun_deck_spicy_package_max_share_pct() == 3.0


# --- 4c. meta popularity makes an archetype a ladder one (F2) -------------------------


def _split_archetype_world(**index_kwargs: Any) -> tuple[Counter[int], CatalogIndex]:
    harold = _deck("HAROLD", range(1, 14), "Harold DK", cls="DeathKnight", games=450)
    big = _deck("BIG", range(500, 515), "Blood DK", cls="DeathKnight", games=99_550)
    return _sharing(13), CatalogIndex([harold, big], **index_kwargs)


def _harold(cards: Counter[int], index: CatalogIndex) -> Any:
    return score_fun_deck(
        deck_code="CANDIDATE",
        title="BBU Harold DK",
        format_name="Standard",
        cards=cards,
        hero_class="DeathKnight",
        index=index,
    )


def test_a_small_archetype_is_not_ladder_without_meta_popularity() -> None:
    cards, index = _split_archetype_world()

    assert _harold(cards, index).kind == "off_meta"  # 0.45 % of the games: below the share floor


def test_an_archetype_popular_in_the_meta_sources_is_ladder() -> None:
    cards, index = _split_archetype_world(
        meta_popularity={("standard", "deathknight", "harold blood dk"): 11.1}
    )

    scored = _harold(cards, index)

    assert index.is_ladder_archetype("standard", "harold dk", "deathknight") is True
    assert scored.is_fun is False
    assert "ladder_member_gate" in scored.reasons


@pytest.mark.parametrize(
    "meta",
    [
        {("standard", "warlock", "harold warlock"): 11.1},  # another class
        {("wild", "deathknight", "harold blood dk"): 11.1},  # another format
        {("standard", "deathknight", "harold blood dk"): 0.9},  # below 1 %
        {("standard", "deathknight", "blood dk"): 11.1},  # another archetype
    ],
)
def test_meta_popularity_matches_only_within_format_class_and_threshold(
    meta: dict[tuple[str, str, str], float],
) -> None:
    cards, index = _split_archetype_world(meta_popularity=meta)

    assert index.is_ladder_archetype("standard", "harold dk", "deathknight") is False
    assert "ladder_member_gate" not in _harold(cards, index).reasons


def test_a_bare_class_archetype_aligns_with_no_title() -> None:
    align = fun_decks._archetypes_align

    assert align("harold dk", "harold blood dk") is True
    assert align("BBU Harold DK", "harold dk") is True
    assert align("tog priest", "priest") is False
    assert align("xl shaman", "shudderwock shaman") is False
    assert align("dragon druid", "treant druid") is False


def test_meta_popularity_is_read_from_the_meta_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    hsreplay = {
        "data": {
            "structured": {
                "classes": [
                    {
                        "class": "DEATHKNIGHT",
                        "archetypes": [
                            {"archetype": "Harold Blood DK", "popularity": "11.10%"},
                            {"archetype": "Tiny DK", "popularity": "0.40%"},
                        ],
                    }
                ]
            }
        }
    }
    guru_wild = {
        "data": {
            "structured": {
                "strategies": [
                    {"Archetype": "Yogg Warrior", "Popularity": "4.7% (26509)"},
                    {"Archetype": "Harold Lock", "Popularity": "2.0% (100)"},
                    {"Archetype": "Mystery Deck", "Popularity": "9.0% (100)"},
                ]
            }
        }
    }
    datasets = {"hsreplay_meta_legend_1d_firecrawl": hsreplay, "hsguru_meta_wild_legend": guru_wild}
    monkeypatch.setattr(fun_decks, "load_resolved_public_dataset", lambda source_id: datasets.get(source_id))

    popularity = fun_decks.build_meta_popularity()

    assert popularity == {
        ("standard", "deathknight", "harold blood dk"): 11.1,
        ("standard", "deathknight", "tiny dk"): 0.4,
        ("wild", "warrior", "yogg warrior"): 4.7,
        ("wild", "warlock", "harold lock"): 2.0,
    }


# --- 4d. a member of its own archetype is not a unique list (F3) -----------------------


def test_a_close_member_of_a_rare_archetype_is_off_meta_not_a_unique_brew() -> None:
    twin = _deck("TWIN", [*range(1, 12), 201, 202, 203, 204], "Imbue Paladin", cls="Paladin", games=60)
    big = _deck("BIG", range(500, 515), "Pure Paladin", cls="Paladin", games=LADDER_GAMES)

    scored = _classify("Imbue Paladin", _sharing(11), twin, big, cls="Paladin")

    assert (scored.twin_sim, scored.content_sim) == (0.5789, 0.5789)
    assert scored.kind == "off_meta"
    assert "unique_list" not in scored.reasons


def test_a_list_far_from_its_own_archetype_is_still_a_unique_brew() -> None:
    twin = _deck("TWIN", [*range(1, 9), 201, 202, 203, 204, 205, 206, 207], "Imbue Hunter", cls="Hunter", games=60)
    big = _deck("BIG", range(500, 515), "Pure Hunter", cls="Hunter", games=LADDER_GAMES)

    scored = _classify("Imbue Hunter", _sharing(8), twin, big, cls="Hunter")  # 0.3636

    assert scored.content_sim < 0.55
    assert scored.kind == "brew"
    assert "unique_list" in scored.reasons


def test_a_concept_title_is_a_brew_even_next_to_its_archetype_but_not_a_unique_list() -> None:
    twin = _deck("TWIN", [*range(1, 12), 201, 202, 203, 204], "Casino Mill Mage", cls="Mage", games=60)
    big = _deck("BIG", range(500, 515), "Pirate Mage", cls="Mage", games=LADDER_GAMES)

    scored = _classify("Casino Mill Mage", _sharing(11), twin, big, cls="Mage")

    assert scored.kind == "brew"
    assert "unique_list" not in scored.reasons


# --- 4e. a concept word backed by a meta package is no concept (F5) ------------------

_WORD_PACKAGE = [
    ("Yogg", "yogg_package"),
    ("Reno", "reno_highlander"),
    ("Highlander", "reno_highlander"),
    ("HL", "reno_highlander"),
    ("Odd", "odd_even"),
    ("Even", "odd_even"),
    ("Tog", "togwaggle_mill"),
    ("Mill", "togwaggle_mill"),
]


def _named_twin(title: str, cls: str = "Priest") -> CatalogDeck:
    """A rare, non-ladder list of the candidate's own name, 13 of 15 cards shared."""
    return _deck("TWIN", [*range(1, 14), 201, 202], title, cls=cls, games=60)


@pytest.mark.parametrize(("word", "label"), _WORD_PACKAGE)
def test_a_concept_word_whose_package_is_meta_in_the_format_gives_no_concept_title(
    word: str, label: str, yogg_marker: None
) -> None:
    title = f"{word} Priest"
    markers = _PACKAGE_MARKER_CARDS.get(label) or [_PACKAGE_MARKER_CARDS["cthun"][0]]
    scored = _classify(
        title,
        _sharing(13),
        _named_twin(title),
        _marked_ladder(markers, cls="Priest"),
        cls="Priest",
    )

    assert scored.concept_hit is False
    assert "concept_title" not in scored.reasons
    assert scored.kind == "off_meta"


@pytest.mark.parametrize(("word", "label"), _WORD_PACKAGE)
def test_a_concept_word_whose_package_is_rare_in_the_format_still_counts(
    word: str, label: str, yogg_marker: None
) -> None:
    title = f"{word} Priest"
    big = _deck("BIG", range(500, 515), "Pure Priest", cls="Priest", games=LADDER_GAMES)

    scored = _classify(title, _sharing(13), _named_twin(title), big, cls="Priest")

    assert scored.concept_hit is True
    assert "concept_title" in scored.reasons
    assert scored.kind == "brew"


def test_standard_yogg_priest_next_to_its_own_archetype_is_off_meta(yogg_marker: None) -> None:
    markers = _PACKAGE_MARKER_CARDS["yogg_package"]
    twin = _named_twin("Yogg Priest")

    scored = _classify("Yogg Priest", _sharing(13, extra=markers), twin, _marked_ladder(markers, cls="Priest"), cls="Priest")

    assert (scored.content_sim, scored.kind) == (0.7647, "off_meta")
    assert scored.is_fun is True


def test_a_second_concept_word_keeps_the_concept_title(yogg_marker: None) -> None:
    markers = _PACKAGE_MARKER_CARDS["yogg_package"]

    scored = _classify(
        "Casino Yogg Priest",
        _sharing(13),
        _named_twin("Casino Yogg Priest"),
        _marked_ladder(markers, cls="Priest"),
        cls="Priest",
    )

    assert "concept_title" in scored.reasons
    assert scored.kind == "brew"


@pytest.mark.parametrize("word", ["Casino", "Rainbow", "Questline", "XL"])
def test_concept_words_without_a_package_work_as_before(word: str, yogg_marker: None) -> None:
    markers = _PACKAGE_MARKER_CARDS["yogg_package"]

    scored = _classify(
        f"{word} Priest",
        _sharing(13),
        _named_twin(f"{word} Priest"),
        _marked_ladder(markers, cls="Priest"),
        cls="Priest",
    )

    assert "concept_title" in scored.reasons
    assert scored.kind == "brew"


# --- 4f. generic names are judged without service prefixes (F6) -------------------------


@pytest.mark.parametrize(
    "title",
    [
        "XL Blood DK",
        "XL Elemental Shaman",
        "HL Aggro Paladin",
        "XL HL Dude Paladin",
        "Highlander Face Hunter",
        "BBU Blood DK",
        "STD XL Frost DK",
        "Unholy DK",
    ],
)
def test_a_generic_name_behind_service_prefixes_is_still_generic(title: str) -> None:
    assert fun_decks._generic_ladder_title(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "XL LPG Mage",
        "XL Protoss Druid",
        "XL Starship Rogue",
        "XL Questline Shaman",
        "XL HL Imbue Rogue",
        "XL BBU Harold DK",
        "FUU Plague DK",
        "BBB Even DK",
        "XL",
        "Blood Dragon Mill",
    ],
)
def test_a_distinctive_name_behind_service_prefixes_is_not_generic(title: str) -> None:
    assert fun_decks._generic_ladder_title(title) is False


def test_generic_gate_fires_for_a_prefixed_generic_name_but_not_with_a_spicy_package() -> None:
    cards = _sharing(0)
    big = _deck("BIG", range(500, 515), "Pure Shaman", cls="Shaman", games=LADDER_GAMES)

    gated = _classify("XL Elemental Shaman", cards, big, cls="Shaman")
    spicy = _classify("XL Elemental Shaman", _sharing(0, extra=[SPICY_CARD]), big, cls="Shaman")

    assert "generic_ladder_gate" in gated.reasons
    assert gated.is_fun is False
    assert "generic_ladder_gate" not in spicy.reasons


# --- 6. publication: brews first, off-meta after them -------------------------


def _kind_row(title: str, cls: str, kind: str | None, score: float, fmt: str = "Standard") -> dict[str, Any]:
    extra = {} if kind is None else {"kind": kind}
    return _row(title, cls=cls, fmt=fmt, score=score, **extra)


_SIX_BREWS = [
    _kind_row(f"Brew {cls}", cls, "brew", 0.5)
    for cls in ("Mage", "Hunter", "Rogue", "Priest", "Warrior", "Shaman")
]


def test_off_meta_follows_the_brews_one_per_class_and_at_most_a_third() -> None:
    off = [
        _kind_row("Off Druid A", "Druid", "off_meta", 0.95),
        _kind_row("Off Druid B", "Druid", "off_meta", 0.90),
        _kind_row("Off Mage", "Mage", "off_meta", 0.85),
        _kind_row("Off Hunter", "Hunter", "off_meta", 0.80),
        _kind_row("Off Rogue", "Rogue", "off_meta", 0.75),
    ]

    picked = _pick_diverse([*_SIX_BREWS, *off], limit=20)

    assert {r["title"] for r in picked if r["kind"] == "brew"} == {r["title"] for r in _SIX_BREWS}
    # 6 brews: off-meta takes at most 6 // 2 = 3 places (a third of the 9 published)
    assert sorted(r["title"] for r in picked if r["kind"] == "off_meta") == [
        "Off Druid A",
        "Off Hunter",
        "Off Mage",
    ]


def test_off_meta_never_displaces_a_brew_when_the_limit_is_tight() -> None:
    off = [_kind_row("Off Druid", "Druid", "off_meta", 0.99), _kind_row("Off Paladin", "Paladin", "off_meta", 0.98)]

    picked = _pick_diverse([*_SIX_BREWS, *off], limit=7)

    assert [r["title"] for r in picked if r["kind"] == "off_meta"] == ["Off Druid"]
    assert sum(1 for r in picked if r["kind"] == "brew") == 6


def test_off_meta_alone_or_next_to_one_brew_is_not_published() -> None:
    off = _kind_row("Off Druid", "Druid", "off_meta", 0.99)

    assert _pick_diverse([off], limit=10) == []
    assert _pick_diverse([_SIX_BREWS[0], off], limit=10) == [_SIX_BREWS[0]]


def test_a_row_without_kind_counts_as_a_brew() -> None:
    old = [_kind_row("Old Mage", "Mage", None, 0.6), _kind_row("Old Rogue", "Rogue", None, 0.6)]
    off = [_kind_row("Off Druid", "Druid", "off_meta", 0.99)]

    assert [r["title"] for r in _pick_diverse([*old, *off], limit=10)] == [
        "Old Mage",
        "Old Rogue",
        "Off Druid",
    ]


@pytest.mark.parametrize("focus", ["standard", None])
def test_both_publication_paths_keep_off_meta_behind_the_brews_per_format(focus: str | None) -> None:
    standard = [*_SIX_BREWS, _kind_row("Off Druid", "Druid", "off_meta", 0.99), _kind_row("Off Paladin", "Paladin", "off_meta", 0.98)]
    wild = [
        _kind_row("Wild Brew Mage", "Mage", "brew", 0.5, "Wild"),
        _kind_row("Wild Brew Rogue", "Rogue", "brew", 0.5, "Wild"),
        _kind_row("Wild Off Druid", "Druid", "off_meta", 0.99, "Wild"),
        _kind_row("Wild Off Priest", "Priest", "off_meta", 0.98, "Wild"),
    ]

    published = _select_published([*standard, *wild], focus=focus)

    off_by_format = Counter(r["format"] for r in published if r["kind"] == "off_meta")
    assert off_by_format == {"Standard": 2, "Wild": 1}  # 6 // 2 = 3 allowed in Standard, only 2 exist
    assert sum(1 for r in published if r["kind"] == "brew") == 8


# --- the run: pure computation and the written dataset -------------------------


def _v8_world() -> None:
    _save_world(
        standard=[
            _catalog_row("Ayaya Rogue", "Rogue", range(1, 16), games=900_000, win_rate=52.0),
            # a ladder variant: 11 of the 15 cards of Ayaya Rogue
            _catalog_row("Garona Rogue", "Rogue", [*range(1, 12), 101, 102, 103, 104], games=60, win_rate=55.0),
            _catalog_row("Casino Mill Mage", "Mage", range(401, 416), games=100, win_rate=55.0),
            _catalog_row("Even Mill Hunter", "Hunter", range(701, 716), games=70, win_rate=46.0),
            # a rare archetype and its variant: no concept, no package, not unique
            _catalog_row("Imbue Druid", "Druid", range(601, 616), games=70, win_rate=52.5),
            _catalog_row("Imbue Druid", "Druid", [*range(601, 614), 801, 802], games=60, win_rate=51.0),
        ]
    )


def test_compute_fun_decks_classifies_the_catalog_and_writes_nothing(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("compute_fun_decks must not write")

    monkeypatch.setattr(fun_decks, "save_dataset", refuse)
    monkeypatch.setattr(fun_decks, "save_status", refuse)
    _v8_world()

    run = compute_fun_decks(None, [])

    assert {(r["title"], r["kind"]) for r in run.published} == {
        ("Casino Mill Mage", "brew"),
        ("Even Mill Hunter", "brew"),
        ("Imbue Druid", "off_meta"),
    }
    assert run.rejected_by_gate == {"ladder_member_gate": 1}
    verdicts = {row["title"]: score for row, score in run.scored}
    assert "ladder_member_gate" in verdicts["Garona Rogue"].reasons
    assert verdicts["Garona Rogue"].ladder_sim >= 0.55
    assert load_dataset("hsguru_fun_decks") is None


def test_refresh_publishes_concept_v8_rows_with_kind_and_distances(fun_deck_world: None) -> None:
    _v8_world()

    result = _refresh_fun_decks_unlocked()

    structured = _structured()
    assert structured["detector_version"] == "concept-v8"
    assert result["detector_version"] == "concept-v8"
    rows = {r["title"]: r for r in structured["rows"]}
    assert set(rows) == {"Casino Mill Mage", "Even Mill Hunter", "Imbue Druid"}
    assert rows["Imbue Druid"]["kind"] == "off_meta"
    assert rows["Imbue Druid"]["variants"] == 1
    assert rows["Casino Mill Mage"]["kind"] == "brew"
    for row in rows.values():
        assert row["ladder_sim"] < 0.55
        assert {"content_sim", "twin_sim", "content_archetype", "fun_score"} <= set(row)
    assert structured["filters"]["ladder_member_sim"] == 0.55
    assert structured["stats"]["rejected_by_gate"] == {"ladder_member_gate": 1}
    assert structured["stats"]["published_by_kind"] == {"brew": 2, "off_meta": 1}


def test_history_of_the_previous_detector_version_is_dropped(fun_deck_world: None) -> None:
    _save_world(standard=[])
    stale = {
        **_row("Casino Mill Mage", code=_deckstring(range(401, 416)), cls="Mage", games=100),
        "win_rate": 55.0,
        "last_seen_at": datetime.now(UTC).isoformat(),
    }
    save_dataset(
        "hsguru_fun_decks",
        {"source_id": "hsguru_fun_decks", "data": {"structured": {"detector_version": "concept-v7", "rows": [stale]}}},
    )

    _refresh_fun_decks_unlocked()

    assert _structured()["rows"] == []


# --- one deck code in both formats (F8) ----------------------------------------------


def _both_formats_row(fmt: str, **extra: Any) -> dict[str, Any]:
    code = _deckstring(range(401, 416))
    return {
        **_row("Casino Mill Mage", code=code, cls="Mage", fmt=fmt, games=100),
        "win_rate": 55.0,
        "win_rate_ci_low": 45.2,
        "win_rate_ci_high": 64.4,
        "variants": 0,
        "kind": "brew",
        "last_seen_at": "2026-10-28T00:00:00+00:00",
        **extra,
    }


def test_merge_history_keeps_the_rows_of_one_code_in_both_formats_apart() -> None:
    standard = _both_formats_row("Standard", win_rate=51.0)
    wild = _both_formats_row("Wild", win_rate=62.0)
    fresh = _both_formats_row("Standard", win_rate=53.0, last_seen_at="2026-10-29T00:00:00+00:00")

    merged = _merge_history(
        [standard, wild], [fresh], retention_hours=10**6, now=datetime(2026, 10, 29, tzinfo=UTC)
    )

    assert {(r["format"], r["win_rate"]) for r in merged} == {("Standard", 53.0), ("Wild", 62.0)}


def test_refresh_publishes_a_code_listed_by_both_catalogs_in_both_formats(fun_deck_world: None) -> None:
    core = _catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0)
    wild_core = {**core, "format": "Wild"}
    _save_world(
        standard=[core, _catalog_row("Casino Mill Mage", "Mage", range(401, 416), games=100, win_rate=55.0)],
        wild=[wild_core, _catalog_row("Casino Mill Mage", "Mage", range(401, 416), games=90, win_rate=50.0, fmt="Wild")],
    )

    _refresh_fun_decks_unlocked()

    assert sorted((r["format"], r["games"]) for r in _structured()["rows"]) == [("Standard", 100), ("Wild", 90)]


def test_a_standard_run_updates_its_row_and_leaves_the_wild_row_of_the_same_code(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_RETENTION_HOURS", "100000")
    _save_world(
        standard=[
            _catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0),
            _catalog_row("Casino Mill Mage", "Mage", range(401, 416), games=100, win_rate=55.0),
        ]
    )
    _save_history(
        [
            _both_formats_row("Standard", games=70, win_rate=50.0),
            _both_formats_row("Wild", games=90, win_rate=62.0),
        ]
    )

    _refresh_fun_decks_unlocked(format_focus="standard")

    rows = {r["format"]: r for r in _structured()["rows"]}
    assert set(rows) == {"Standard", "Wild"}
    assert (rows["Standard"]["games"], rows["Standard"]["win_rate"]) == (100, 55.0)  # recalculated
    assert (rows["Wild"]["games"], rows["Wild"]["win_rate"]) == (90, 62.0)  # untouched


# --- the fresh run wins over the remembered score (F8 review) --------------------------


def test_merge_history_with_a_higher_remembered_score_keeps_one_fresh_row() -> None:
    old = _both_formats_row(
        "Standard",
        fun_score=0.95,
        kind="brew",
        reasons=["concept_title"],
        ladder_sim=0.1,
        first_seen_at="2026-10-20T00:00:00+00:00",
        last_seen_at="2026-10-28T00:00:00+00:00",
    )
    fresh = _both_formats_row(
        "Standard",
        fun_score=0.4,
        kind="off_meta",
        reasons=["far_from_ladder"],
        ladder_sim=0.3,
        first_seen_at="2026-10-29T00:00:00+00:00",
        last_seen_at="2026-10-29T00:00:00+00:00",
    )

    merged = _merge_history([old], [fresh], retention_hours=10**6, now=datetime(2026, 10, 29, tzinfo=UTC))

    assert len(merged) == 1
    row = merged[0]
    assert (row["fun_score"], row["kind"], row["reasons"], row["ladder_sim"]) == (0.4, "off_meta", ["far_from_ladder"], 0.3)
    assert row["first_seen_at"] == "2026-10-20T00:00:00+00:00"
    assert row["last_seen_at"] == "2026-10-29T00:00:00+00:00"
    assert set(row) == set(fresh)


def test_two_refreshes_in_a_row_do_not_multiply_rows_or_variants(
    fun_deck_world: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HS_FUN_DECK_RETENTION_HOURS", "100000")
    _save_world(
        standard=[
            _catalog_row("Dragon Warrior", "Warrior", range(1, 16), games=500_000, win_rate=52.0),
            _catalog_row("Casino Mill Mage", "Mage", range(401, 416), games=100, win_rate=55.0),
            _catalog_row("Casino Mill Mage Alt", "Mage", [*range(401, 413), 501, 502, 503], games=60, win_rate=52.0),
        ]
    )
    # remembered with a score above anything the run can give now
    _save_history([_both_formats_row("Standard", fun_score=2.0, games=70, win_rate=50.0)])

    first = _refresh_fun_decks_unlocked()
    rows_after_first = _structured()["rows"]
    second = _refresh_fun_decks_unlocked()
    rows_after_second = _structured()["rows"]

    assert len(rows_after_first) == 1 and len(rows_after_second) == 1
    assert (first["fun_retained"], second["fun_retained"]) == (1, 1)
    assert rows_after_first[0]["variants"] == rows_after_second[0]["variants"] == 1
    assert rows_after_second[0]["fun_score"] < 2.0  # the fresh score, not the remembered one
    assert rows_after_second[0]["games"] == 100
    assert set(rows_after_second[0]) == set(rows_after_first[0])


# --- regression on a real catalog slice ----------------------------------------


def _golden_verdicts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, Any], Any]]:
    for name in (
        "HS_FUN_DECK_MIN_SCORE",
        "HS_FUN_DECK_LADDER_SHARE_PCT",
        "HS_FUN_DECK_LADDER_MEMBER_SIM",
        "HS_FUN_DECK_TWIN_SIM",
    ):
        monkeypatch.delenv(name, raising=False)
    sliced = json.loads((FIXTURES.parent / "fun_decks_catalog_slice.json").read_text())
    golden = json.loads((FIXTURES.parent / "fun_decks_golden.json").read_text())
    with patch("app.deck_decode.cards_by_dbfid", return_value=_AnyCardIndex()):
        index = CatalogIndex(
            [
                CatalogDeck(
                    deck_code=d["deck_code"],
                    cards=fun_decks.deck_card_multiset(d["deck_code"]),
                    archetype=d["archetype"].casefold(),
                    label=d["archetype"],
                    hero_class=d["class"],
                    format=d["format"],
                    games=d["games"],
                )
                for d in sliced["decks"]
            ],
            archetype_games={tuple(k.split("|", 1)): v for k, v in sliced["archetype_games"].items()},
            format_games=sliced["format_games"],
            meta_popularity={tuple(k.split("|")): v for k, v in sliced["meta_popularity"].items()},
            package_shares={tuple(k.split("|")): v for k, v in sliced["package_shares"].items()},
        )
        return [
            (
                entry,
                score_fun_deck(
                    deck_code=entry["deck_code"],
                    title=entry["title"],
                    format_name=entry["format"],
                    hero_class=entry["class"],
                    win_rate=entry["win_rate"],
                    catalog_games_hint=entry["games"],
                    index=index,
                ),
            )
            for entry in golden["decks"]
        ]


def test_real_catalog_slice_rejects_every_ladder_list_and_keeps_the_brews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdicts = _golden_verdicts(monkeypatch)
    assert len(verdicts) == 45

    passed = [e["title"] for e, s in verdicts if e["expected"] == "reject" and s.is_fun]
    lost = [e["title"] for e, s in verdicts if e["expected"] == "brew" and not (s.is_fun and s.kind == "brew")]
    off_as_brew = [e["title"] for e, s in verdicts if e["expected"] == "off_meta" and s.kind == "brew"]

    assert passed == []
    # at most 2 of the 17 brews may be lost: Yogg Druid is a Yogg variant now (F5)
    assert lost == ["Yogg Druid", "Y'Shaarj Paladin"]
    assert off_as_brew == []
    assert {e["title"] for e, s in verdicts if e["expected"] == "off_meta" and s.is_fun} == {
        "Imbue Druid",
        "Imbue Paladin",
        "Yogg Priest",
    }


@pytest.mark.parametrize(
    ("title", "gate"),
    [
        ("Garona Rogue", "ladder_member_gate"),
        ("BBB N'Zoth Handbuff DK", "ladder_member_gate"),
        ("Yogg Shaman", "ladder_member_gate"),
        ("Mill Rogue", "ladder_title_gate"),
        ("Infinity Paladin", "ladder_title_gate"),
        ("Aggro Paladin", "generic_ladder_gate"),
        ("Dude Paladin", "generic_ladder_gate"),
        ("Mech Rogue", "generic_ladder_gate"),
        ("XL Blood DK", "generic_ladder_gate"),
        ("XL Elemental Shaman", "generic_ladder_gate"),
    ],
)
def test_real_catalog_slice_names_the_gate_of_each_audited_reject(
    monkeypatch: pytest.MonkeyPatch, title: str, gate: str
) -> None:
    verdicts = {e["title"]: s for e, s in _golden_verdicts(monkeypatch)}

    assert gate in verdicts[title].reasons
    assert verdicts[title].is_fun is False


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
