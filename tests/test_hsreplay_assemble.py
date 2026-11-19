from __future__ import annotations

import asyncio
import copy
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from app import assemble_pools as ap
from app import card_choice_pools as pools
from app import hsreplay_assemble as ha
from app.exit_codes import ExitCode
from app.hsreplay_mulligan import FetchResult
from app.storage import load_dataset, load_status

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/hsreplay_assemble"
# Cards: the 13 Assemble cards and every card the live rows below mention
# (HearthstoneJSON enUS/ruRU of 2026-11-18). Rows: the live
# assemble_choices_by_middle answer of 2026-11-19 (RANKED_STANDARD, LAST_7_DAYS)
# cut to F3TCH3R and Rajh; bases under 1% keep one row, which does not change
# the sums the publication checks read.
CARD_FIXTURE = json.loads((FIXTURES / "cards.json").read_text())
LIVE = json.loads(gzip.decompress((FIXTURES / "standard_live.json.gz").read_bytes()))
NOW = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)

F3TCH3R = 128892
RAJH = 132353
HOURGLASS = 121051  # TIME_100, base of the reference rows
ANTONIDAS = 120426  # CORE_EX1_559
EREDAR_DECEIVER = 127589  # CORE_TTN_843
STATUS_KEYS = {"ok", "no_data", "not_collected", "failed"}


@pytest.fixture(scope="module")
def base() -> pools.CardBase:
    return pools.CardBase(
        CARD_FIXTURE["cards"],
        standard_sets=CARD_FIXTURE["standard_sets"],
        standard_dbf=CARD_FIXTURE["standard_dbf"],
        names_ru=CARD_FIXTURE["names_ru"],
        texts_ru=CARD_FIXTURE["texts_ru"],
    )


@pytest.fixture(scope="module")
def texts(base) -> ap.AssembleTexts:
    return ap.AssembleTexts(base)


# --- text halves -------------------------------------------------------------------


def test_the_13_assemble_cards_come_from_the_text_not_from_mechanics(base) -> None:
    cards = ap.assemble_cards(base)
    assert [card["id"] for card in cards] == [
        "BE_004",
        "BE_044",
        "BE_045",
        "BE_087",
        "BE_114",
        "BE_229",
        "BE_257",
        "BE_262",
        "BE_553",
        "BE_571",
        "BE_702",
        "BE_740",
        "BE_851",
    ]
    assert all("ASSEMBLE" not in (card.get("mechanics") or []) for card in cards)
    assert len(ap.assemble_cards(base, "standard")) == 13


def test_an_escaped_ampersand_is_not_a_separator(base) -> None:
    zok = base.by_id["ETC_386"]
    assert "\\&" in zok["text"] and "&" not in zok["text"].replace("\\&", "")
    assert ap.split_halves(zok["text"]) is None
    assert ap.split_halves("Gain Attack \\& Armor.&\nDraw a card.") == (
        "Gain Attack & Armor.",
        "Draw a card.",
    )


def test_only_the_first_unescaped_ampersand_splits() -> None:
    assert ap.split_halves("When you play a card,& deal 1 & 2 damage.") == (
        "When you play a card,",
        "deal 1 & 2 damage.",
    )
    assert ap.split_halves("No separator here.") is None
    assert ap.split_halves(None) is None
    assert ap.split_halves("Only a trigger,&") is None


def test_x_marker_in_the_middle_of_a_text(base) -> None:
    antonidas = base.by_id["CORE_EX1_559"]
    assert "[x]" in antonidas["text"] or "[x]" in base.texts_ru["CORE_EX1_559"]
    assert ap.split_halves(base.texts_ru["CORE_EX1_559"]) == (
        "Когда вы применяете заклинание,",
        "вы кладете в руку «Огненный шар».",
    )
    assert ap.split_halves("[x]<b>Taunt</b>\nAt the end of\nyour turn,& draw.") == (
        "At the end of your turn,",
        "draw.",
    )


@pytest.mark.parametrize(
    ("text", "trigger"),
    [
        # a keyword before the trigger: new line, comma list, a dot, Russian
        (
            "<b>Divine Shield</b>\nAt the end of your turn,& draw.",
            "At the end of your turn,",
        ),
        (
            "<b>Divine Shield</b>, <b>Taunt</b> At the end of your turn,& draw.",
            "At the end of your turn,",
        ),
        ("<b>Провокация.</b> В конце вашего хода& берет карту.", "В конце вашего хода"),
        ("<b>Гигант +3.</b> В конце вашего хода& берет карту.", "В конце вашего хода"),
        (
            "<b>Божественный щит</b>\nВ конце вашего хода& берет карту.",
            "В конце вашего хода",
        ),
        # a trigger with a colon stays; keywords before it do not
        ("<b>Taunt</b>\n<b>Deathrattle:</b>& Draw.", "Deathrattle:"),
        ("<b>Deathrattle:</b>& Draw.", "Deathrattle:"),
        # a whole sentence is part of the trigger
        (
            "<b>Battlecry:</b> Summon two copies of this. <b>Deathrattle:</b>& Draw.",
            "Battlecry: Summon two copies of this. Deathrattle:",
        ),
        # markers and numbers
        (
            "At the end of your turn,& deal $2 damage to #3 enemies.",
            "At the end of your turn,",
        ),
    ],
)
def test_leading_keywords_are_dropped_and_the_condition_stays(
    text: str, trigger: str
) -> None:
    halves = ap.split_halves(text)
    assert halves is not None and halves[0] == trigger


def test_markers_and_spaces_are_cleaned() -> None:
    assert ap.split_halves("When X,&\xa0deal $2 damage\nto #3  enemies.") == (
        "When X,",
        "deal 2 damage to 3 enemies.",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # HearthstoneJSON ruRU, 2026-11-19: the ``[d]`` hyphenation marker sits
        # inside a word and is followed by a run of no-break spaces.
        (
            "[x]<b>Боевой клич:</b> в начале\nвашего следующего хода\nатака этого существа "
            "ста[d]\xa0\xa0\xa0\xa0\xa0новится БЕСКОНЕЧНОЙ!\xa0\xa0\xa0\xa0",
            "Боевой клич: в начале вашего следующего хода атака этого существа "
            "становится БЕСКОНЕЧНОЙ!",
        ),
        (
            "[x]<b>Перемотка</b>\nМетает в случайных против[d]ников 3\xa0кинжала, наносящих\n"
            "по $2 ед. урона.",
            "Перемотка Метает в случайных противников 3 кинжала, наносящих по 2 ед. урона.",
        ),
        (
            "[x]После того как ваш герой\nатакует, вы сбрасываете\nкарту с наиболь[d]шей стоимостью.",
            "После того как ваш герой атакует, вы сбрасываете карту с наибольшей стоимостью.",
        ),
    ],
)
def test_ru_word_break_marker_is_glued(raw: str, expected: str) -> None:
    assert ap.clean(raw) == expected


def test_english_case_follows_the_joint() -> None:
    assert ap.join_halves("At the end of your turn,", "Summon a 5/5.", "en") == {
        "trigger": "At the end of your turn,",
        "effect": "summon a 5/5.",
    }
    assert (
        ap.join_halves("Deathrattle:", "summon a 5/5.", "en")["effect"]
        == "Summon a 5/5."
    )
    # Acronyms and proper names after a comma stay as they are.
    assert ap.join_halves("When X,", "AI wins.", "en")["effect"] == "AI wins."
    assert (
        ap.join_halves("В конце вашего хода", "призывает.", "ru")["effect"]
        == "призывает."
    )
    assert (
        ap.join_halves("Предсмертный хрип:", "Существа.", "ru")["effect"] == "Существа."
    )


def test_a_locale_without_a_separator_gives_null(texts) -> None:
    # Zok has no separator in either locale; Cairne has one in both.
    zok = next(c for c in CARD_FIXTURE["cards"] if c["id"] == "ETC_386")["dbfId"]
    option = texts.option(HOURGLASS, zok, "effect")
    assert option == {"ru": None, "en": None}
    assert texts.option(HOURGLASS, 69667, "effect")["ru"] is not None


# --- parsing ---------------------------------------------------------------------


def _row(**changes: Any) -> dict[str, Any]:
    row = {
        "middle_dbf_id": HOURGLASS,
        "times_middle_offered": 100,
        "option_position": 1,
        "option_dbf_id": 69667,
        "times_offered": 50,
        "trigger_picks": 5,
        "effect_picks": 20,
        "trigger_win_rate": 40.0,
        "effect_win_rate": 60.0,
    }
    return {**row, **changes}


def test_parse_merges_both_positions_of_a_donor() -> None:
    payload = {
        "as_of": "2026-11-19T05:54:31Z",
        "series": {
            "data": {
                str(F3TCH3R): [
                    _row(),
                    _row(
                        option_position=2,
                        times_offered=30,
                        trigger_picks=5,
                        effect_picks=0,
                        trigger_win_rate=60.0,
                        effect_win_rate=None,
                    ),
                ]
            }
        },
    }
    cards, as_of = ha.parse_payload(payload)
    assert as_of == "2026-11-19T05:54:31Z"
    # [donor, offered, effect picks, trigger picks, effect win, trigger win]
    assert cards == {
        str(F3TCH3R): [[HOURGLASS, 100, [[69667, 80, 20, 10, 60.0, 50.0]]]]
    }


@pytest.mark.parametrize("bad", [None, -1, 101, float("nan"), float("inf"), "x", True])
@pytest.mark.parametrize("field", ["effect_win_rate", "trigger_win_rate"])
def test_a_winrate_that_is_not_a_percent_is_rejected_when_there_are_picks(
    field: str, bad: Any
) -> None:
    picks = {"effect_win_rate": "effect_picks", "trigger_win_rate": "trigger_picks"}[
        field
    ]
    row = _row(**{picks: 3, field: bad})
    with pytest.raises(ha.PayloadRejected):
        ha.parse_payload({"series": {"data": {"1": [row]}}})


_BAD_NUMBERS = [
    None,
    float("nan"),
    float("inf"),
    float("-inf"),
    0.5,
    -1,
    "5",
    "x",
    True,
    [1],
]


@pytest.mark.parametrize("bad", _BAD_NUMBERS)
@pytest.mark.parametrize(
    "field",
    [
        "times_middle_offered",
        "times_offered",
        "effect_picks",
        "trigger_picks",
        "middle_dbf_id",
        "option_dbf_id",
    ],
)
def test_a_bad_count_or_card_id_is_rejected_as_payload_rejected(
    field: str, bad: Any
) -> None:
    with pytest.raises(ha.PayloadRejected):
        ha.parse_payload({"series": {"data": {"1": [_row(**{field: bad})]}}})


def test_integral_floats_are_accepted() -> None:
    row = _row(times_offered=50.0, effect_picks=20.0, trigger_picks=5.0)
    cards, _ = ha.parse_payload({"series": {"data": {"1": [row]}}})
    assert cards["1"][0][2][0][1:4] == [50, 20, 5]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.5, "x"])
def test_a_drifting_counter_keeps_the_last_good_snapshot(isolated, base, bad) -> None:
    settings = ha.Settings(formats=("standard",))
    _refresh(base, FakeHsReplay(base), settings=settings)
    broken = good_payload(base)
    broken["series"]["data"][str(F3TCH3R)][0]["times_offered"] = bad
    fake = FakeHsReplay(
        base, RANKED_STANDARD=FetchResult("ok", payload=broken, credits=1)
    )
    result = _refresh(base, fake, now=NOW + timedelta(hours=21), settings=settings)
    assert result["published"] is False and result["state"] == "fetch_error"
    assert (
        load_status("hsreplay_assemble")["formats"]["standard"]["last_outcome"]
        == "contract"
    )
    kept = load_dataset("hsreplay_assemble")["data"]["structured"]["formats"][
        "standard"
    ]
    assert kept["checked_at"] == NOW.isoformat()


def test_a_missing_winrate_without_picks_is_fine() -> None:
    row = _row(
        effect_picks=0, effect_win_rate=None, trigger_picks=0, trigger_win_rate="x"
    )
    cards, _ = ha.parse_payload({"series": {"data": {"1": [row]}}})
    assert cards["1"][0][2][0][4:] == [None, None]


def test_a_candidate_with_a_bad_winrate_keeps_the_last_good_snapshot(
    isolated, base
) -> None:
    settings = ha.Settings(formats=("standard",))
    _refresh(base, FakeHsReplay(base), settings=settings)
    broken = good_payload(base)
    row = next(
        r for r in broken["series"]["data"][str(F3TCH3R)] if r["effect_picks"] > 0
    )
    row["effect_win_rate"] = float("nan")
    fake = FakeHsReplay(
        base, RANKED_STANDARD=FetchResult("ok", payload=broken, credits=1)
    )
    result = _refresh(base, fake, now=NOW + timedelta(hours=21), settings=settings)
    assert result["published"] is False and result["state"] == "fetch_error"
    status = load_status("hsreplay_assemble")
    assert status["formats"]["standard"]["last_outcome"] == "contract"
    kept = load_dataset("hsreplay_assemble")["data"]["structured"]["formats"][
        "standard"
    ]
    assert kept["checked_at"] == NOW.isoformat()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"series": {"data": {}}},
        {"series": {"data": {"x": []}}},
        {"series": {"data": {"1": {"not": "a list"}}}},
        {"series": {"data": {"1": ["row"]}}},
        {"series": {"data": {"1": [_row(middle_dbf_id=None)]}}},
        {"series": {"data": {"1": [_row(effect_picks=None)]}}},
        {"series": {"data": {"1": [_row(), _row(times_middle_offered=7)]}}},
    ],
)
def test_a_changed_format_is_rejected(payload: Any) -> None:
    with pytest.raises(ha.PayloadRejected):
        ha.parse_payload(payload)


# --- payloads for refresh and reads ----------------------------------------------------


def _filler(card_dbf: int) -> list[dict[str, Any]]:
    """Three bases (40/40/20%) and one donor per position; every donor is taken half the time."""

    rows = []
    donors = {
        HOURGLASS: (69667, 129792),
        129792: (69667, HOURGLASS),
        ANTONIDAS: (69667, 129792),
    }
    for middle, offered in ((HOURGLASS, 200), (129792, 200), (ANTONIDAS, 100)):
        for position, donor in enumerate(donors[middle], start=1):
            rows.append(
                {
                    "middle_dbf_id": middle,
                    "times_middle_offered": offered,
                    "option_position": position,
                    "option_dbf_id": donor,
                    "times_offered": offered,
                    "trigger_picks": offered // 4,
                    "effect_picks": offered // 4,
                    "trigger_win_rate": 50.0,
                    "effect_win_rate": 50.0,
                }
            )
    return rows


def good_payload(base: pools.CardBase) -> dict[str, Any]:
    payload = copy.deepcopy(LIVE)
    for card in ap.assemble_cards(base):
        payload["series"]["data"].setdefault(str(card["dbfId"]), _filler(card["dbfId"]))
    return payload


class FakeHsReplay:
    """The Scrape.do boundary: answers per GameType, records the URLs."""

    def __init__(self, base: pools.CardBase, **answers: FetchResult) -> None:
        good = good_payload(base)
        self.answers = {
            "RANKED_STANDARD": FetchResult("ok", payload=good, credits=1),
            "RANKED_WILD": FetchResult("ok", payload=copy.deepcopy(good), credits=1),
            **answers,
        }
        self.calls: list[str] = []

    async def __call__(self, url: str) -> FetchResult:
        self.calls.append(url)
        query = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
        return self.answers[query["GameType"]]


@pytest.fixture()
def isolated(tmp_path, monkeypatch, base):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.card_choice_pools.load_card_base", lambda: base)
    monkeypatch.setattr(
        "app.hsreplay_client._hsreplay_cookie_header", lambda: "sessionid=s"
    )
    return tmp_path


def _refresh(base, fake, *, now=NOW, settings=None, max_requests=None):
    return asyncio.run(
        ha.refresh_hsreplay_assemble(
            settings=settings or ha.Settings(),
            call_json=fake,
            now=now,
            base=base,
            max_requests=max_requests,
        )
    )


# --- live fixture and checks -------------------------------------------------------------------


def test_the_live_rows_pass_every_publication_check(base) -> None:
    cards, as_of = ha.parse_payload(good_payload(base))
    assert as_of == "2026-11-19T05:54:31Z" and len(cards) == 13
    assert ha.check_snapshot(cards, base, "standard") == []


def _break(base, change) -> list[str]:
    payload = good_payload(base)
    change(payload["series"]["data"])
    cards, _ = ha.parse_payload(payload)
    return ha.check_snapshot(cards, base, "standard")


def _drop_card(data) -> None:
    del data["129801"]


def _unknown_bases(data) -> None:
    # Two of three bases are cards the base does not know: the shown bases cover 20%.
    for row in data[str(129089)]:
        if row["middle_dbf_id"] != ANTONIDAS:
            row["middle_dbf_id"] += 9_000_000


def _short_donors(data) -> None:
    for row in data[str(F3TCH3R)]:
        if row["middle_dbf_id"] == HOURGLASS:
            row["times_offered"] //= 2


def _no_picks(data) -> None:
    for row in data[str(RAJH)]:
        row["effect_picks"] = row["trigger_picks"] = 0


def _unknown_donors(data) -> None:
    for rows in data.values():
        for row in rows:
            row["option_dbf_id"] += 9_000_000


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (_drop_card, "missing_cards: BE_257"),
        (_unknown_bases, "BE_045: bases offer sum 20.0"),
        (_short_donors, f"BE_044: base {HOURGLASS} donors offer sum"),
        (_no_picks, "BE_004: any-half pick rate 0.0"),
        (_unknown_donors, "unresolved rows"),
    ],
)
def test_each_publication_check_catches_its_defect(base, change, reason) -> None:
    reasons = _break(base, change)
    assert any(reason in item for item in reasons), reasons


def test_a_small_unresolved_share_is_tolerated(base) -> None:
    payload = good_payload(base)
    rows = payload["series"]["data"][str(F3TCH3R)]
    for row in rows[:20]:  # 20 of 1657+ rows, about 0.5%
        row["option_dbf_id"] += 9_000_000
    cards, _ = ha.parse_payload(payload)
    assert ha.check_snapshot(cards, base, "standard") == []


# --- refresh ---------------------------------------------------------------------


def test_refresh_reads_one_url_per_format_and_publishes(isolated, base) -> None:
    fake = FakeHsReplay(base)
    result = _refresh(base, fake)
    assert result["ok"] and result["published"] and result["state"] == "ok"
    assert result["requests"] == 2 and result["scrape_do_credits"] == 2
    queries = [parse_qs(urlsplit(url).query) for url in fake.calls]
    assert all(url.startswith(ha.QUERY_API + "?") for url in fake.calls)
    assert queries == [
        {"GameType": ["RANKED_STANDARD"], "TimeRange": ["LAST_7_DAYS"]},
        {"GameType": ["RANKED_WILD"], "TimeRange": ["LAST_7_DAYS"]},
    ]
    dataset = load_dataset("hsreplay_assemble")
    formats = dataset["data"]["structured"]["formats"]
    assert set(formats) == {"standard", "wild"}
    standard = formats["standard"]
    assert standard["game_type"] == "RANKED_STANDARD" and len(standard["cards"]) == 13
    assert standard["checked_at"] == NOW.isoformat()
    assert standard["as_of"] == "2026-11-19T05:54:31Z"
    status = load_status("hsreplay_assemble")
    assert (
        status["state"] == "ok"
        and status["formats"]["standard"]["last_outcome"] == "ok"
    )


def test_a_format_is_read_again_after_20_hours(isolated, base) -> None:
    fake = FakeHsReplay(base)
    _refresh(base, fake)
    assert (
        _refresh(base, fake, now=NOW + timedelta(hours=19, minutes=50))["requests"] == 0
    )
    later = _refresh(base, fake, now=NOW + timedelta(hours=20, minutes=10))
    assert later["requests"] == 2 and later["published"]


def test_settings_follow_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("HS_ASSEMBLE_FORMATS", raising=False)
    monkeypatch.delenv("HS_ASSEMBLE_SCRAPE_DO_DAILY_CREDITS", raising=False)
    assert ha.Settings.from_env() == ha.Settings(("standard", "wild"), 6)
    monkeypatch.setenv("HS_ASSEMBLE_FORMATS", "wild, nope")
    monkeypatch.setenv("HS_ASSEMBLE_SCRAPE_DO_DAILY_CREDITS", "3")
    assert ha.Settings.from_env().formats == ("wild",)
    assert ha.Settings.from_env().scrape_do_daily_credits == 3


def test_a_candidate_that_fails_a_check_keeps_the_last_good_snapshot(
    isolated, base
) -> None:
    _refresh(base, FakeHsReplay(base))
    before = load_dataset("hsreplay_assemble")
    broken = good_payload(base)
    del broken["series"]["data"][str(F3TCH3R)]
    fake = FakeHsReplay(
        base, RANKED_STANDARD=FetchResult("ok", payload=broken, credits=1)
    )
    later = NOW + timedelta(hours=21)
    result = _refresh(base, fake, now=later)
    assert result["ok"] is False and result["state"] == "partial"
    assert result["published_formats"] == ["wild"]  # standard was rejected
    after = load_dataset("hsreplay_assemble")
    standard = after["data"]["structured"]["formats"]["standard"]
    assert standard == before["data"]["structured"]["formats"]["standard"]
    assert standard["checked_at"] == NOW.isoformat()
    status = load_status("hsreplay_assemble")
    assert status["formats"]["standard"]["last_outcome"] == "rejected"
    assert "missing_cards: BE_044" in status["detail"]
    assert status["last_refresh_failure_class"] == "fetch_error"
    # The API still serves the last good snapshot with its old checked_at.
    from app.main import app

    body = (
        TestClient(app)
        .get("/v1/hsreplay/assemble", params={"card": "BE_044", "base": "top"})
        .json()
    )
    assert body["data"]["stats"]["state"] == "ok"
    assert body["data"]["stats"]["checked_at"] == NOW.isoformat()


def test_a_rejected_first_read_publishes_nothing(isolated, base) -> None:
    broken = good_payload(base)
    del broken["series"]["data"][str(F3TCH3R)]
    fake = FakeHsReplay(
        base, RANKED_STANDARD=FetchResult("ok", payload=broken, credits=1)
    )
    result = _refresh(base, fake, settings=ha.Settings(formats=("standard",)))
    assert result["state"] == "fetch_error" and result["published"] is False
    assert load_dataset("hsreplay_assemble") is None
    assert "missing_cards" in load_status("hsreplay_assemble")["detail"]


@pytest.mark.parametrize(
    "answer",
    [
        FetchResult("processing", status=202, credits=1),
        FetchResult("error", status=502, credits=1, detail="http_502"),
        FetchResult("denied", status=403, credits=1, detail="http_403"),
        FetchResult("ok", payload={"series": {}}, credits=1),
    ],
)
def test_202_and_errors_are_retried_by_the_next_run(isolated, base, answer) -> None:
    settings = ha.Settings(formats=("standard",))
    good = FakeHsReplay(base)
    _refresh(base, good, settings=settings)
    failing = FakeHsReplay(base, RANKED_STANDARD=answer)
    stale = NOW + timedelta(hours=21)
    first = _refresh(base, failing, now=stale, settings=settings)
    assert first["state"] == "fetch_error" and first["requests"] == 1
    # The snapshot is 22 h old either way, so make sure it is the failure that
    # makes the format due: read again one hour later, not only after 20 h.
    retry = _refresh(base, failing, now=stale + timedelta(hours=1), settings=settings)
    assert retry["requests"] == 1
    fixed = _refresh(base, good, now=stale + timedelta(hours=2), settings=settings)
    assert fixed["ok"] and fixed["published"]
    assert (
        load_status("hsreplay_assemble")["formats"]["standard"]["last_outcome"] == "ok"
    )


def test_a_failed_attempt_is_retried_even_when_the_snapshot_is_fresh(
    isolated, base
) -> None:
    settings = ha.Settings(formats=("standard",))
    _refresh(base, FakeHsReplay(base), settings=settings)
    # Fake a failed attempt that happened early: state is last_outcome != ok.
    status = load_status("hsreplay_assemble")
    status["formats"]["standard"]["last_outcome"] = "processing"
    from app.storage import save_status

    save_status("hsreplay_assemble", status)
    again = _refresh(
        base, FakeHsReplay(base), now=NOW + timedelta(hours=6), settings=settings
    )
    assert again["requests"] == 1 and again["published"]


def test_the_daily_credit_cap_is_kept(isolated, base) -> None:
    settings = ha.Settings(scrape_do_daily_credits=1)
    fake = FakeHsReplay(base)
    first = _refresh(base, fake, settings=settings)
    assert first["requests"] == 1 and first["scrape_do_credits"] == 1
    assert first["stop_reason"] == "scrape_do_budget" and first["state"] == "partial"
    assert first["formats"] == {"standard": "ok", "wild": "budget"}
    # Same UTC day: nothing is sent.
    same_day = _refresh(base, fake, now=NOW + timedelta(hours=6), settings=settings)
    assert same_day["requests"] == 0 and len(fake.calls) == 1
    assert same_day["scrape_do_spent_today"] == 1
    # The next UTC day wild is read.
    next_day = _refresh(base, fake, now=NOW + timedelta(hours=17), settings=settings)
    assert next_day["requests"] == 1 and next_day["published_formats"] == ["wild"]
    assert next_day["scrape_do_spent_today"] == 1


def test_max_requests_caps_a_single_run(isolated, base) -> None:
    fake = FakeHsReplay(base)
    result = _refresh(base, fake, max_requests=1)
    assert result["requests"] == 1 and result["scrape_do_credit_cap"] == 1
    assert len(fake.calls) == 1


def test_a_concurrent_run_is_skipped(isolated, base) -> None:
    from app.resource_locks import ResourceLockSet

    held = ResourceLockSet(["hsreplay_assemble"], metadata={"operation": "test"})
    held.acquire()
    try:
        fake = FakeHsReplay(base)
        result = _refresh(base, fake)
    finally:
        held.release()
    assert result["published"] is False and fake.calls == []


# --- read API ----------------------------------------------------------------------


@pytest.fixture()
def client(isolated, base):
    _refresh(base, FakeHsReplay(base))
    from app.main import app

    return TestClient(app)


def _assemble(client, **params: str) -> Any:
    return client.get("/v1/hsreplay/assemble", params={"card": "BE_044", **params})


def test_the_card_list_has_all_13_cards_with_state_and_checked_at(client) -> None:
    body = client.get("/v1/hsreplay/assemble/cards").json()
    assert (
        body["meta"]["source_id"] == "hsreplay_assemble" and body["meta"]["count"] == 13
    )
    assert body["meta"]["stale"] is False
    rows = body["data"]
    assert len(rows) == 13 and {row["state"] for row in rows} == {"ok"}
    f3tch3r = next(row for row in rows if row["card"]["card_id"] == "BE_044")
    assert f3tch3r["card"] == {
        "card_id": "BE_044",
        "dbf_id": F3TCH3R,
        "name": "F3TCH3R",
        "name_ru": "РЕ-3-ВЕР",
        "card_class": "NEUTRAL",
        "cost": 3,
        "type": "MINION",
        "rarity": "COMMON",
    }
    assert f3tch3r["formats"] == ["standard", "wild"]
    assert f3tch3r["checked_at"] == NOW.isoformat() and f3tch3r["stale"] is False
    assert f3tch3r["bases_total"] == 38 and 45 < f3tch3r["win_rate"] < 52
    assert body["meta"]["fetched_at"] == NOW.isoformat()


def test_the_reference_rows_of_the_hourglass_base(client) -> None:
    body = _assemble(client, base=str(HOURGLASS)).json()
    data = body["data"]
    assert data["card"]["text_ru"] == "Боевой клич: собирает существо за (4)."
    assert data["card"]["text"] == "Battlecry: Assemble a 4-Cost minion."
    assert data["format"] == "standard" and data["game_type"] == "RANKED_STANDARD"
    assert data["time_range"] == "LAST_7_DAYS" and data["original_class_only"] is False
    stats = data["stats"]
    assert stats["state"] == "ok" and stats["checked_at"] == NOW.isoformat()
    assert stats["as_of"] == "2026-11-19T05:54:31Z" and stats["stale"] is False
    assert stats["source_url"] == "https://hsreplay.net/cards/assemble-pools/"
    assert data["summary"]["bases_total"] == 38
    assert body["meta"]["count"] == 38

    shown = [item for item in data["bases"] if item["donors"] is not None]
    assert [item["card"]["dbf_id"] for item in shown] == [HOURGLASS]
    base_row = shown[0]
    assert base_row["card"]["name_ru"] == "Смотритель песочных часов"
    assert base_row["offer_rate"] == pytest.approx(980 / 14615 * 100)
    assert base_row["donors_total"] == 63 == len(base_row["donors"])

    donors = {item["card"]["name_ru"]: item for item in base_row["donors"]}
    cases = [
        # donor, half, trigger, effect, pick, win, low sample
        (
            "Кэрн Кровавое Копыто",
            0,
            "В конце вашего хода",
            "призывает Бейна Кровавое Копыто 5/5.",
            84.3,
            69.5,
            False,
        ),
        (
            "Кэрн Кровавое Копыто",
            1,
            "Предсмертный хрип:",
            "существа у вас в руке получают +1/+1.",
            2.9,
            50.0,
            True,
        ),
        (
            "Бес из стоков",
            0,
            "В конце вашего хода",
            "наносит 2 ед. урона всем противникам.",
            61.8,
            57.1,
            False,
        ),
        (
            "Мама-утка",
            0,
            "В конце вашего хода",
            "призывает трех утят 1/1 с «Натиском».",
            19.8,
            68.7,
            False,
        ),
        (
            "Шагоход ОС91",
            0,
            "В конце вашего хода",
            "вы берете карту.",
            22.0,
            37.0,
            False,
        ),
        (
            "Шагоход ОС91",
            1,
            "После того как вы разыгрываете существо,",
            "существа у вас в руке получают +1/+1.",
            15.4,
            42.1,
            False,
        ),
        (
            "Танковый полк",
            1,
            "Боевой клич: призывает две копии этого существа. Предсмертный хрип:",
            "существа у вас в руке получают +1/+1.",
            35.7,
            60.0,
            True,
        ),
    ]
    for name, index, trigger, effect, pick, win, low in cases:
        option = donors[name]["options"][index]
        assert option["half"] == ("effect", "trigger")[index]
        assert option["text"]["ru"] == {"trigger": trigger, "effect": effect}
        assert (
            round(option["pick_rate"], 1) == pick
            and round(option["win_rate"], 1) == win
        )
        assert option["low_sample"] is low

    cairne = donors["Кэрн Кровавое Копыто"]
    assert (
        cairne["card"]["card_id"] == "CORE_EX1_110"
        and cairne["card"]["rarity"] == "LEGENDARY"
    )
    assert [option["half"] for option in cairne["options"]] == ["effect", "trigger"]
    assert cairne["options"][0]["picks"] == 59 and cairne["options"][1]["picks"] == 2
    assert cairne["options"][0]["text"]["en"] == {
        "trigger": "At the end of your turn,",
        "effect": "summon a 5/5 Baine Bloodhoof.",
    }
    assert cairne["options"][1]["text"]["en"] == {
        "trigger": "Deathrattle:",
        "effect": "Give all minions in your hand +1/+1.",
    }
    assert cairne["picks"] == 61 and cairne["low_sample"] is False
    assert round(cairne["offer_rate"], 1) == 7.1
    # Two donors are offered at a time: the donors' offers sum to about 200%.
    assert sum(donor["offer_rate"] for donor in base_row["donors"]) == pytest.approx(
        200
    )
    assert [donor["offer_rate"] for donor in base_row["donors"]] == sorted(
        (donor["offer_rate"] for donor in base_row["donors"]), reverse=True
    )


def test_rajh_antonidas_and_the_eredar_deceiver(client) -> None:
    data = _assemble(client, card="BE_004", base=str(ANTONIDAS)).json()["data"]
    antonidas = next(item for item in data["bases"] if item["donors"] is not None)
    assert antonidas["card"]["name_ru"] == "Верховный маг Антонидас"
    deceiver = next(
        donor
        for donor in antonidas["donors"]
        if donor["card"]["dbf_id"] == EREDAR_DECEIVER
    )
    assert deceiver["card"]["name_ru"] == "Эредар-обманщик"
    effect, trigger = deceiver["options"]
    assert effect["text"]["ru"] == {
        "trigger": "Когда вы применяете заклинание,",
        "effect": "призывает демона 1/1 с «Натиском».",
    }
    assert trigger["text"]["ru"] == {
        "trigger": "Когда вы берете карту,",
        "effect": "вы кладете в руку «Огненный шар».",
    }
    # Both positions of the donor are merged: 144 + 168 offers, 28 + 35 effect picks.
    assert effect["picks"] == 28 + 35 and trigger["picks"] == 35 + 46
    assert effect["pick_rate"] == pytest.approx((28 + 35) / (144 + 168) * 100)
    assert effect["win_rate"] == pytest.approx((35.71 * 28 + 60.0 * 35) / 63)


def test_the_base_selector(client) -> None:
    full = _assemble(client).json()["data"]
    assert all(item["donors"] is not None for item in full["bases"])
    assert [item["offer_rate"] for item in full["bases"]] == sorted(
        (item["offer_rate"] for item in full["bases"]), reverse=True
    )
    assert sum(item["offer_rate"] for item in full["bases"]) > 99.9
    top = _assemble(client, base="top").json()["data"]
    assert top["bases"][0]["donors"] is not None and top["bases"][0]["donors_total"] > 0
    assert all(item["donors"] is None for item in top["bases"][1:])
    assert all(item["donors_total"] > 0 for item in top["bases"][1:])
    assert top["bases"][0]["card"]["dbf_id"] == full["bases"][0]["card"]["dbf_id"]
    assert _assemble(client, base="999999999").status_code == 404
    assert _assemble(client, base="nope").status_code == 422


def test_card_by_id_or_dbf_and_not_assemble_cards(client) -> None:
    by_dbf = _assemble(client, card=str(F3TCH3R), base="top")
    assert by_dbf.json()["data"]["card"]["card_id"] == "BE_044"
    assert _assemble(client, card="be_044", base="top").status_code == 200
    assert _assemble(client, card="CORE_EX1_110").status_code == 404
    assert _assemble(client, card="NOT_A_CARD").status_code == 404
    assert client.get("/v1/hsreplay/assemble").status_code == 422
    assert _assemble(client, format="arena").status_code == 422


def test_undervalued_and_low_samples_follow_card_choices(client) -> None:
    data = _assemble(client, base=str(HOURGLASS)).json()["data"]
    donors = next(item for item in data["bases"] if item["donors"] is not None)[
        "donors"
    ]
    options = [option for donor in donors for option in donor["options"]]
    assert all(option["low_sample"] == (option["picks"] < 10) for option in options)
    solid = [option for option in options if not option["low_sample"]]
    assert len(solid) >= 3
    import statistics

    mean_win = statistics.fmean(option["win_rate"] for option in solid)
    median_pick = statistics.median(option["pick_rate"] for option in solid)
    expected = {
        id(option)
        for option in solid
        if option["win_rate"] >= mean_win + 1 and option["pick_rate"] < median_pick
    }
    assert (
        expected
        and {id(option) for option in options if option["undervalued"]} == expected
    )
    assert all(
        option["undervalued"] is False for option in options if option["low_sample"]
    )


def test_a_format_that_is_not_collected(isolated, base) -> None:
    _refresh(base, FakeHsReplay(base), settings=ha.Settings(formats=("standard",)))
    from app.main import app

    client = TestClient(app)
    wild = client.get(
        "/v1/hsreplay/assemble", params={"card": "BE_044", "format": "wild"}
    ).json()
    assert wild["data"]["stats"]["state"] == "not_collected"
    assert wild["data"]["bases"] == [] and wild["data"]["stats"]["checked_at"] is None
    assert wild["meta"]["count"] == 0
    rows = client.get("/v1/hsreplay/assemble/cards", params={"format": "wild"}).json()[
        "data"
    ]
    assert len(rows) == 13 and {row["state"] for row in rows} == {"not_collected"}
    assert rows[0]["checked_at"] is None and rows[0]["bases_total"] is None
    # An unknown base of a format without data is not an error.
    assert (
        client.get(
            "/v1/hsreplay/assemble",
            params={"card": "BE_044", "format": "wild", "base": "5"},
        ).status_code
        == 200
    )


def test_before_the_first_run_every_card_is_not_collected(isolated, base) -> None:
    from app.main import app

    client = TestClient(app)
    rows = client.get("/v1/hsreplay/assemble/cards").json()["data"]
    assert len(rows) == 13 and {row["state"] for row in rows} == {"not_collected"}
    one = client.get("/v1/hsreplay/assemble", params={"card": "BE_044"}).json()
    assert (
        one["data"]["stats"]["state"] == "not_collected" and one["data"]["bases"] == []
    )


def test_a_failed_read_without_a_snapshot_is_failed(isolated, base) -> None:
    fake = FakeHsReplay(
        base, RANKED_STANDARD=FetchResult("error", status=502, credits=1)
    )
    _refresh(base, fake, settings=ha.Settings(formats=("standard",)))
    from app.main import app

    body = (
        TestClient(app).get("/v1/hsreplay/assemble", params={"card": "BE_044"}).json()
    )
    assert body["data"]["stats"]["state"] == "failed"
    assert {
        row["state"]
        for row in TestClient(app).get("/v1/hsreplay/assemble/cards").json()["data"]
    } == {"failed"}


def test_a_card_missing_from_a_snapshot_has_no_data(isolated, base) -> None:
    from app.storage import save_dataset

    save_dataset(
        "hsreplay_assemble",
        {
            "state": "ok",
            "fetched_at": NOW.isoformat(),
            "data": {
                "structured": {
                    "formats": {
                        "standard": {
                            "checked_at": NOW.isoformat(),
                            "as_of": None,
                            "cards": {str(RAJH): [[ANTONIDAS, 5, []]]},
                        }
                    }
                }
            },
        },
    )
    from app.main import app

    data = (
        TestClient(app)
        .get("/v1/hsreplay/assemble", params={"card": "BE_044"})
        .json()["data"]
    )
    assert data["stats"]["state"] == "no_data" and data["bases"] == []


def test_a_snapshot_older_than_48_hours_is_stale(isolated, base) -> None:
    _refresh(base, FakeHsReplay(base), settings=ha.Settings(formats=("standard",)))
    data = ha.get_assemble("BE_044", now=NOW + timedelta(hours=49), base=base)
    assert data["stats"]["state"] == "ok" and data["stats"]["stale"] is True
    fresh = ha.get_assemble("BE_044", now=NOW + timedelta(hours=47), base=base)
    assert fresh["stats"]["stale"] is False
    rows = ha.list_assemble_cards(now=NOW + timedelta(hours=49), base=base)
    assert all(row["stale"] for row in rows)


def test_unresolved_rows_are_counted_and_left_out(isolated, base) -> None:
    payload = good_payload(base)
    # The live rows already hold tokens and copies that are not collectible cards.
    known = ha.summarize_card(
        base, ap.AssembleTexts(base), ha.parse_payload(payload)[0][str(F3TCH3R)]
    )["unresolved_rows"]
    assert known > 0
    unknown = 9_999_001
    donor_row = {**_row(), "middle_dbf_id": HOURGLASS, "option_dbf_id": unknown}
    payload["series"]["data"][str(F3TCH3R)] += [
        {
            **donor_row,
            "times_middle_offered": 980,
            "times_offered": 3,
            "option_position": 1,
        }
    ]
    _refresh(
        base,
        FakeHsReplay(
            base, RANKED_STANDARD=FetchResult("ok", payload=payload, credits=1)
        ),
    )
    data = ha.get_assemble("BE_044", base=base)
    assert data["stats"]["unresolved_rows"] == known + 1
    hourglass = next(
        item for item in data["bases"] if item["card"]["dbf_id"] == HOURGLASS
    )
    assert hourglass["donors_total"] == 63


def test_the_assemble_answers_are_never_cached_by_etag(client) -> None:
    for path in ("/v1/hsreplay/assemble", "/v1/hsreplay/assemble/cards"):
        response = client.get(path, params={"card": "BE_044"})
        assert response.status_code == 200
        assert "etag" not in response.headers
        assert "no-store" in response.headers["cache-control"]


def test_the_source_is_registered_as_a_daily_pipeline() -> None:
    from app.sources import SOURCE_BY_ID

    source = SOURCE_BY_ID["hsreplay_assemble"]
    assert source.kind == "pipeline" and source.stale_hours == 36
    assert source.url == "https://hsreplay.net/cards/assemble-pools/"


# --- full donor pool ------------------------------------------------------------------------


def _card(
    card_id: str,
    dbf: int,
    cost: int,
    text: str = "At the end of your turn,& draw a card.",
    *,
    card_class: str = "NEUTRAL",
    card_set: str = "CORE",
    card_type: str = "MINION",
    name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": card_id,
        "dbfId": dbf,
        "name": name or card_id,
        "text": text,
        "set": card_set,
        "type": card_type,
        "cost": cost,
        "cardClass": card_class,
        "collectible": True,
    }


def _assemble_card(card_id: str, dbf: int, card_class: str) -> dict[str, Any]:
    return _card(
        card_id,
        dbf,
        3,
        "<b>Battlecry:</b> <b>Assemble</b> a minion.",
        card_class=card_class,
        card_set="BE",
    )


def _mini_base(cards: list[dict[str, Any]]) -> pools.CardBase:
    return pools.CardBase(
        [
            _assemble_card("BE_044", 1, "NEUTRAL"),
            _assemble_card("BE_702", 2, "ROGUE"),
            _assemble_card("BE_045", 3, "PRIEST"),
            _assemble_card("BE_004", 4, "PALADIN"),
            *cards,
        ],
        standard_sets=["CORE", "BE"],
    )


def _rules(cards: list[dict[str, Any]], assemble: str = "BE_044", fmt="standard"):
    card_base = _mini_base(cards)
    card = card_base.by_id[assemble]
    return card_base, ap.DonorPools(card_base, ap.AssembleTexts(card_base), card, fmt)


def _pool_ids(
    card_base, rules, base_dbf: int, player_class=None, **kwargs
) -> list[str]:
    return [
        str(card["id"]) for card in rules.candidates(base_dbf, player_class, **kwargs)
    ]


def test_donor_cost_is_the_base_cost_up_to_plus_two() -> None:
    cards = [_card("BASE", 100, 3)] + [
        _card(f"D{cost}", 110 + cost, cost, f"After you draw {cost},& draw a card.")
        for cost in range(8)
    ]
    card_base, rules = _rules(cards)
    assert _pool_ids(card_base, rules, 100) == ["D3", "D4", "D5"]
    assert rules.reject_reason(100, 112) == "cost"
    assert rules.reject_reason(100, 116) == "cost"


@pytest.mark.parametrize("base_cost", [8, 9, 10])
def test_a_base_of_8_to_10_gets_donors_of_8_to_10(base_cost) -> None:
    cards = [_card("BASE", 100, base_cost)] + [
        _card(f"D{cost}", 110 + cost, cost, f"After you draw {cost},& draw a card.")
        for cost in range(6, 11)
    ]
    card_base, rules = _rules(cards)
    assert _pool_ids(card_base, rules, 100) == ["D8", "D9", "D10"]


def test_mismatcher_wants_donors_of_five_and_more_for_any_base() -> None:
    cards = [_card("BASE", 100, 1)] + [
        _card(f"D{cost}", 110 + cost, cost, f"After you draw {cost},& draw a card.")
        for cost in range(1, 10)
    ]
    card_base, rules = _rules(cards, "BE_045")
    assert _pool_ids(card_base, rules, 100) == [f"D{cost}" for cost in range(5, 10)]


def test_rajhs_pool_is_a_curated_list_of_any_class_and_cost(base, texts) -> None:
    rajh = base.by_id["BE_004"]
    rules = ap.DonorPools(base, texts, rajh, "standard")
    antonidas = base.by_dbf[ANTONIDAS]
    ids = {str(card["id"]) for card in rules.candidates(ANTONIDAS, None)}
    expected = set(ap.CURATED_DONORS["BE_004"]) - {"CORE_EX1_559"}
    assert ids == expected - {
        card_id
        for card_id in expected
        if ap.same_condition(
            texts.halves(base.by_id[card_id]["dbfId"], "en")[0],
            texts.halves(ANTONIDAS, "en")[0],
        )
    }
    assert {base.by_id[card_id]["cost"] for card_id in ids} >= {2, 3, 4, 5}
    assert {pools.card_classes(base.by_id[card_id])[0] for card_id in ids} > {"NEUTRAL"}
    assert rules.reject_reason(ANTONIDAS, 130973) is not None  # not in the list
    assert antonidas["cost"] == 7
    assert ap.player_class_of(rajh, "MAGE") is None


def test_a_donor_with_the_base_condition_is_not_offered() -> None:
    cards = [
        _card("BASE", 100, 3, "At the end of your turn,& give +1/+1."),
        _card("PUNCT", 101, 3, "at the end of your turn& draw a card."),
        _card("OTHER", 102, 3, "At the start of your turn,& draw a card."),
        _card("BASE_B", 103, 3, "Battlecry:& draw a card."),
        _card("BC_DR", 104, 3, "<b>Battlecry and Deathrattle:</b>& draw a card."),
        _card("DR", 105, 3, "<b>Deathrattle:</b>& draw a card."),
        _card("COND_BC", 106, 3, "<b>Battlecry:</b> If you hold a Dragon,& draw."),
        _card("SPELL_A", 107, 3, "After you cast a spell,& draw a card."),
        _card("SPELL_B", 108, 3, "Whenever you cast a spell,& draw a card."),
        _card("SPELL_C", 109, 3, "After you cast a Holy spell,& draw a card."),
    ]
    card_base, rules = _rules(cards)
    assert rules.reject_reason(100, 101) == "same_trigger"
    assert rules.reject_reason(100, 100) == "self"
    assert rules.reject_reason(100, 102) is None
    assert rules.reject_reason(103, 104) == "same_trigger"
    assert rules.reject_reason(103, 106) == "same_trigger"
    assert rules.reject_reason(103, 105) is None
    assert rules.reject_reason(104, 105) == "same_trigger"
    assert rules.reject_reason(107, 108) == "same_trigger"
    assert rules.reject_reason(107, 109) is None
    assert "BASE" not in _pool_ids(card_base, rules, 100)


def test_formats_collectible_text_and_unknown_cards() -> None:
    cards = [
        _card("BASE", 100, 3, "At the end of your turn,& give +1/+1."),
        _card("OLD", 101, 3, "After you draw,& draw.", card_set="NAXX"),
        _card("WEAPON", 102, 3, "After your hero attacks,& draw.", card_type="WEAPON"),
        _card(
            "SPELL", 103, 3, "At the start of your next turn,& draw.", card_type="SPELL"
        ),
        _card("PLAIN", 104, 3, "Draw a card."),
        {**_card("HIDDEN", 105, 3, "After you heal,& draw."), "collectible": False},
    ]
    card_base, standard = _rules(cards, fmt="standard")
    _, wild = _rules(cards, fmt="wild")
    assert standard.reject_reason(100, 101) == "format"
    assert wild.reject_reason(100, 101) is None
    assert standard.reject_reason(100, 102) is None  # weapons and spells are donors too
    assert standard.reject_reason(100, 103) is None
    assert standard.reject_reason(100, 104) == "no_separator"
    assert standard.reject_reason(100, 105) == "unknown_card"
    assert standard.reject_reason(100, 999) == "unknown_card"
    assert standard.reject_reason(999, 102) == "unknown_card"
    assert set(_pool_ids(card_base, standard, 100)) == {"WEAPON", "SPELL"}


def test_a_core_copy_and_its_original_are_one_donor() -> None:
    cards = [
        _card("BASE", 100, 3, "At the end of your turn,& give +1/+1."),
        _card("CORE_X_1", 101, 3, "After you draw,& draw.", name="Twin"),
        _card("X_1", 102, 3, "After you draw,& draw.", card_set="NAXX", name="Twin"),
        _card("Y_1", 103, 3, "After you heal,& draw.", card_set="NAXX", name="Same"),
        _card("Y_2", 104, 3, "After you heal,& draw.", card_set="NAXX", name="Same"),
        {
            **_card("Z_2", 106, 3, "After you play,& draw.", name="Reprint"),
            "countAsCopyOfDbfId": 105,
        },
        _card("Z_1", 105, 3, "After you play,& draw.", card_set="NAXX", name="Reprint"),
    ]
    card_base, rules = _rules(cards, fmt="wild")
    ids = _pool_ids(card_base, rules, 100)
    assert "CORE_X_1" in ids and "X_1" not in ids
    # Equal cards that are not a CORE_ pair stay separate donors; countAsCopyOf joins.
    assert {"Y_1", "Y_2"} <= set(ids) and len({"Z_1", "Z_2"} & set(ids)) == 1
    # HSReplay's copy wins, and a copy seen next to the base always stays.
    assert "X_1" in _pool_ids(card_base, rules, 100, prefer=[102])
    assert "CORE_X_1" not in _pool_ids(card_base, rules, 100, prefer=[102])
    kept = _pool_ids(card_base, rules, 100, keep=[101, 102])
    assert {"CORE_X_1", "X_1"} <= set(kept)


def test_the_player_class_gives_its_own_and_neutral_donors() -> None:
    cards = [
        _card("BASE", 100, 3, "At the end of your turn,& give +1/+1."),
        _card("MAGE_D", 101, 3, "After you draw,& draw.", card_class="MAGE"),
        _card("ROGUE_D", 102, 3, "After you heal,& draw.", card_class="ROGUE"),
        _card("NEUTRAL_D", 103, 3, "After you play,& draw."),
    ]
    card_base, rules = _rules(cards)
    assert _pool_ids(card_base, rules, 100, "MAGE") == ["MAGE_D", "NEUTRAL_D"]
    assert _pool_ids(card_base, rules, 100, "ROGUE") == ["ROGUE_D", "NEUTRAL_D"]
    assert _pool_ids(card_base, rules, 100) == ["MAGE_D", "ROGUE_D", "NEUTRAL_D"]
    # A class donor seen next to the base is only kept for the player's class.
    assert _pool_ids(card_base, rules, 100, "MAGE", keep=[102]) == [
        "MAGE_D",
        "NEUTRAL_D",
    ]


def test_player_class_and_class_dependence_of_the_13_cards(base) -> None:
    by_id = {card["id"]: card for card in ap.assemble_cards(base)}
    assert ap.player_class_of(by_id["BE_044"]) is None
    assert ap.player_class_of(by_id["BE_044"], "mage") == "MAGE"
    assert ap.player_class_of(by_id["BE_702"]) == "ROGUE"
    assert ap.player_class_of(by_id["BE_702"], "MAGE") == "MAGE"
    assert ap.player_class_of(by_id["BE_004"]) is None
    assert [
        card_id for card_id, card in by_id.items() if ap.is_class_dependent(card)
    ] == ["BE_044"]


# --- the check against a snapshot --------------------------------------------------------------


def test_the_live_rows_of_the_two_cards_are_covered_by_the_pool(base, texts) -> None:
    rows, _ = ha.parse_payload(LIVE)
    for card_id in ("BE_044", "BE_004"):
        card = base.by_id[card_id]
        rules = ap.DonorPools(base, texts, card, "standard")
        check = ha._donor_pool_check(rules, rows[str(card["dbfId"])])
        assert check["mismatch"] is False, check
        assert check["uncovered_offer_share"] < 0.1
        assert check["observed_pairs"] > 100
    # Every donor with statistics (the live pairs the rules cover) stands in the pool.
    f3tch3r = base.by_id["BE_044"]
    rules = ap.DonorPools(base, texts, f3tch3r, "standard")
    for middle, _offered, donor_rows in rows[str(F3TCH3R)]:
        covered = {
            int(row[0])
            for row in donor_rows
            if rules.reject_reason(int(middle), int(row[0])) is None
        }
        pool = {
            int(card["dbfId"])
            for card in rules.candidates(int(middle), None, keep=covered)
        }
        assert covered <= pool


def _store_and_read(base, mutate, *, card="BE_044", fmt="standard", **params):
    from app.storage import load_dataset, save_dataset

    dataset = load_dataset("hsreplay_assemble")
    snapshot = dataset["data"]["structured"]["formats"][fmt]
    mutate(snapshot)
    snapshot["content_hash"] = ha.content_hash(snapshot["cards"])
    save_dataset("hsreplay_assemble", dataset)
    return ha.get_assemble(card, format_name=fmt, base=base, **params)


def test_a_spoiled_snapshot_turns_the_pool_off(isolated, base) -> None:
    _refresh(base, FakeHsReplay(base))
    good = ha.get_assemble("BE_044", base_selector="top", base=base)
    assert good["donor_pool_status"]["state"] == "ok"
    assert good["bases"][0]["donor_pool"]

    def spoil(snapshot) -> None:
        rows = snapshot["cards"][str(F3TCH3R)]
        # One donor outside every rule (cost 0 Moonfire-like row) with 5% of the shows.
        total = sum(row[1] for middle in rows for row in middle[2])
        rows[0][2].append([ANTONIDAS, int(total * 0.05), 0, 0, None, None])
        rows[0][0] = HOURGLASS

    bad = _store_and_read(base, spoil, base_selector="top")
    status = bad["donor_pool_status"]
    assert status["state"] == "mismatch" and status["uncovered_offer_share"] > 1.0
    assert status["uncovered_pairs"] >= 1 and status["observed_pairs"] > 100
    assert all(item["donor_pool"] is None for item in bad["bases"])
    # The rest of the answer stays as it was.
    assert bad["stats"]["state"] == "ok" and bad["bases"][0]["donors"]


def test_without_a_snapshot_the_pool_is_unavailable(isolated, base) -> None:
    data = ha.get_assemble("BE_044", base_selector="top", base=base)
    assert data["stats"]["state"] == "not_collected"
    assert data["donor_pool_status"] == {
        "state": "unavailable",
        "class_dependent": True,
        "player_class": None,
        "observed_pairs": None,
        "uncovered_pairs": None,
        "uncovered_offer_share": None,
    }


# --- the contract ---------------------------------------------------------------------------


def _pool(client, **params):
    body = _assemble(client, **params).json()["data"]
    chosen = [item for item in body["bases"] if item["donor_pool"] is not None]
    return body, chosen


def test_the_f3tch3r_pool_has_stats_donors_first_then_the_rest(client) -> None:
    body, chosen = _pool(client, base=str(HOURGLASS))
    assert len(chosen) == 1 and chosen[0]["card"]["dbf_id"] == HOURGLASS
    status = body["donor_pool_status"]
    assert status["state"] == "ok" and status["class_dependent"] is True
    assert status["player_class"] is None and status["observed_pairs"] > 100
    pool = chosen[0]["donor_pool"]
    donors = chosen[0]["donors"]
    flags = [entry["has_stats"] for entry in pool]
    assert flags == sorted(flags, reverse=True) and not all(flags)
    with_stats = [entry["card"]["dbf_id"] for entry in pool if entry["has_stats"]]
    assert with_stats == [donor["card"]["dbf_id"] for donor in donors]
    rest = [entry["card"] for entry in pool if not entry["has_stats"]]
    assert [card["name_ru"] or "" for card in rest] == sorted(
        card["name_ru"] or "" for card in rest
    )
    first_entry = pool[0]
    assert set(first_entry["card"]) == {
        "card_id",
        "dbf_id",
        "name",
        "name_ru",
        "card_class",
        "cost",
        "type",
        "rarity",
    }
    assert [option["half"] for option in first_entry["options"]] == [
        "effect",
        "trigger",
    ]
    for entry in pool:
        for option in entry["options"]:
            assert set(option) == {"half", "text"}
            assert set(option["text"]) == {"ru", "en"}
            assert set(option["text"]["en"]) == {"trigger", "effect"}
    # The text is the very one of donors[].options[].text.
    first = donors[0]
    entry = next(
        item for item in pool if item["card"]["dbf_id"] == first["card"]["dbf_id"]
    )
    assert [option["text"] for option in entry["options"]] == [
        option["text"] for option in first["options"]
    ]
    assert {item["card"]["cost"] for item in pool} <= {4, 5, 6}


def test_the_class_parameter_limits_the_donors(client) -> None:
    body = _assemble(client, base=str(HOURGLASS), **{"class": "mage"}).json()["data"]
    assert body["donor_pool_status"]["player_class"] == "MAGE"
    pool = next(item for item in body["bases"] if item["donor_pool"] is not None)[
        "donor_pool"
    ]
    assert pool and {entry["card"]["card_class"] for entry in pool} == {
        "MAGE",
        "NEUTRAL",
    }
    assert _assemble(client, base=str(HOURGLASS), **{"class": "FOO"}).status_code == 422
    assert _assemble(client, **{"class": ""}).status_code == 422
    assert _assemble(client, base="top", **{"class": "Warlock"}).status_code == 200


def test_rajh_ignores_the_class_parameter(client) -> None:
    plain = _assemble(client, card="BE_004", base=str(ANTONIDAS))
    other = _assemble(client, card="BE_004", base=str(ANTONIDAS), **{"class": "MAGE"})
    assert plain.status_code == 200 and plain.content == other.content
    status = plain.json()["data"]["donor_pool_status"]
    assert status["player_class"] is None and status["class_dependent"] is False
    chosen = next(
        item for item in plain.json()["data"]["bases"] if item["donor_pool"] is not None
    )
    ids = {entry["card"]["card_id"] for entry in chosen["donor_pool"]}
    assert ids <= ap.CURATED_DONORS["BE_004"] and "CORE_EX1_559" not in ids
    assert len({entry["card"]["card_class"] for entry in chosen["donor_pool"]}) > 3


def test_a_classed_card_gets_its_class_and_neutral_donors(isolated, base) -> None:
    _refresh(base, FakeHsReplay(base))
    data = ha.get_assemble("BE_702", base_selector="top", base=base)
    assert data["donor_pool_status"]["player_class"] == "ROGUE"
    assert data["donor_pool_status"]["class_dependent"] is False
    top = data["bases"][0]
    classes = {entry["card"]["card_class"] for entry in top["donor_pool"] or []}
    assert classes <= {"ROGUE", "NEUTRAL"}


def test_without_a_base_every_pool_is_null_and_other_fields_stay(client) -> None:
    full = _assemble(client).json()["data"]
    assert all(item["donor_pool"] is None for item in full["bases"])
    top = _assemble(client, base="top").json()["data"]
    stripped = {
        **top,
        "donor_pool_status": None,
        "bases": [
            {key: value for key, value in item.items() if key != "donor_pool"}
            for item in top["bases"]
        ],
    }
    plain = {
        **full,
        "donor_pool_status": None,
        "bases": [
            {key: value for key, value in item.items() if key != "donor_pool"}
            for item in full["bases"]
        ],
    }
    assert set(full) == {
        "card",
        "format",
        "game_type",
        "time_range",
        "original_class_only",
        "stats",
        "summary",
        "donor_pool_status",
        "bases",
    }
    assert set(stripped) == set(plain)
    assert stripped["bases"][0]["donors"] == plain["bases"][0]["donors"]


def test_the_donor_pool_report_runs_on_a_snapshot_file(
    isolated, base, tmp_path, capsys
) -> None:
    _refresh(base, FakeHsReplay(base), settings=ha.Settings(formats=("standard",)))
    from app import cli
    from app.storage import load_dataset

    path = tmp_path / "assemble.json"
    path.write_text(json.dumps(load_dataset("hsreplay_assemble")))
    code = cli.main(
        [
            "assemble-donor-pool-report",
            "--format",
            "standard",
            "--snapshot",
            str(path),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    # The filler rows of the other 11 cards do not follow the rules: DEGRADED.
    assert code == int(ExitCode.DEGRADED) and report["ok"] is False
    by_card = {row["card_id"]: row for row in report["rows"]}
    assert set(by_card) == {card["id"] for card in ap.assemble_cards(base)}
    assert by_card["BE_044"]["state"] == "ok"
    assert by_card["BE_044"]["worst"] is not None
    assert by_card["BE_044"]["surplus"]["min_base_offered"] == 300
    assert report["mismatch_share_threshold"] == 1.0
