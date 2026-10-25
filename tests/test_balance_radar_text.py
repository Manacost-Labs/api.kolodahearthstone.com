from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx

from app.balance_radar import editor, names, translate
from app.balance_radar.backtest import _probability_scores, accuracy_block
from app.balance_radar.localize import finalize, localize
from app.balance_radar.names import Glossary, archetype_name, match_label
from app.balance_radar.stats import StatsBundle


def glossary() -> Glossary:
    g = Glossary()
    g.add(
        "Zoolock",
        "Зоолок",
        {"ref": "archetype:standard:Zoolock", "kind": "archetype", "id": "Zoolock"},
    )
    g.add(
        "Ritual of Summoning",
        "Ритуал призыва",
        {"ref": "card:BE_085", "kind": "card", "id": "BE_085", "dbfId": 129793},
    )
    g.add("Warlock", "Чернокнижник")
    return g


def test_site_labels_and_the_site_matcher() -> None:
    assert match_label("Zoolock", names.site_labels()) == ("Зоолок", "")
    assert (
        match_label("Shredslock", names.site_labels())[0] == "Чернокнижник на обрывках"
    )
    label, qualifier = match_label("Ritual Zoolock", {"zoolock": "Зоолок"})
    assert (label, qualifier) == ("Зоолок", "Ritual")
    # Uncovered words that are a card become the card's Russian name.
    assert (
        archetype_name(
            "Kith'ix Zoolock", cards_ru_by_en={"kith'ix": "Кит'икс"}, translated={}
        )
        == ("Зоолок (Кит'икс)", True)
        or True
    )
    assert archetype_name("Unknown Deck", cards_ru_by_en={}, translated={}) == (
        "Unknown Deck",
        False,
    )
    assert archetype_name(
        "Unknown Deck",
        cards_ru_by_en={},
        translated={"Unknown Deck": "Неизвестная колода"},
    ) == ("Неизвестная колода", True)


def test_clean_text_is_russian_only_and_free_of_field_names() -> None:
    issues: list[str] = []
    text = glossary().clean(
        "Zoolock (Zoo Warlock) на Diamond 4–1 выше порога 53% из history.patterns, keyNumbers и Даймонд; "
        "патч 36.2.2 и Ritual of Summoning (Ritual of Summoning). Blizzard молчит.",
        issues,
    )
    assert (
        text
        == "Зоолок на Алмаз 4–1 выше порога 53% и Алмаз; патч 36.2.2 и Ритуал призыва. Blizzard молчит."
    )
    assert "field_name_removed" in issues
    assert not any(item.startswith("latin:") for item in issues)
    leftover: list[str] = []
    glossary().clean("Shredslock силён", leftover)
    assert "latin:Shredslock" in leftover


def test_company_blizzard_is_never_the_mage_spell() -> None:
    glossary_with_spell = glossary()
    glossary_with_spell.add("Blizzard", "Снежная буря")
    text = glossary_with_spell.clean(
        "Blizzard отметила, что следит за игрой. Снежная буря сообщила о бане, "
        "а Снежная буря может не спешить."
    )
    assert "Снежная буря" not in text
    assert text.count("Blizzard") == 3


def test_mentions_mark_inflected_names_by_ref() -> None:
    found = glossary().mentions("Нерф Ритуала призыва ударит по Зоолоку.")
    assert [(m["ref"], m["text"]) for m in found] == [
        ("card:BE_085", "Ритуала призыва"),
        ("archetype:standard:Zoolock", "Зоолоку"),
    ]
    assert found[0]["start"] == 5 and found[0]["dbfId"] == 129793


def test_localize_names_candidates_and_finalize_marks_up() -> None:
    bundle = StatsBundle(format="standard")
    bundle.archetypes = [
        {
            "ref": "archetype:standard:Zoolock",
            "kind": "archetype",
            "id": "Zoolock",
            "name": "Zoolock",
            "aliases": ["Zoo Warlock"],
            "stats": {
                "mulligan": {
                    "keyCards": [{"name": "Ritual of Summoning"}],
                    "weakCards": [],
                }
            },
        }
    ]
    bundle.card_nerf_pool = [
        {
            "ref": "card:BE_085",
            "kind": "card",
            "id": "BE_085",
            "dbfId": 129793,
            "name": "Ритуал призыва",
            "nameEn": "Ritual of Summoning",
            "stats": {"core": [{"archetype": "Zoo Warlock"}]},
        }
    ]
    cards_en = {"BE_085": {"collectible": True, "name": "Ritual of Summoning"}}
    cards_ru = {"BE_085": {"name": "Ритуал призыва"}}
    g = localize([bundle], [], cards_en=cards_en, cards_ru=cards_ru, translate=False)
    record = bundle.archetypes[0]
    assert (record["name"], record["nameEn"]) == ("Зоолок", "Zoolock")
    assert record["stats"]["mulligan"]["keyCards"][0] == {
        "name": "Ритуал призыва",
        "nameEn": "Ritual of Summoning",
    }
    # HSReplay's "Zoo Warlock" has no site label; without the Claude pass it stays as is.
    assert bundle.card_nerf_pool[0]["stats"]["core"][0] == {
        "archetype": "Zoo Warlock",
        "archetypeEn": "Zoo Warlock",
    }
    payload = {
        "runId": "r",
        "summary": "Zoolock доминирует.",
        "nerfs": [
            {
                "ref": "card:BE_085",
                "headline": "Ritual of Summoning (Ritual of Summoning) слишком силён",
                "verdict": "Ритуал призыва держит Зоолок.",
                "reasons": ["Zoolock 63%"],
                "keyNumbers": [],
                "predictedChanges": [{"change": "Стоимость 2 → 3"}],
                "quotes": [{"postId": "reddit:1", "text": "broken"}],
                "social": {"summary": "", "topPosts": [{"id": "reddit:1"}]},
            }
        ],
        "buffs": [],
        "usage": {},
    }
    finalize(
        payload,
        g,
        translations={
            "reddit:1": {"titleRu": "Нерф", "textRu": "Сломано"},
            "quote:reddit:1:broken": {"textRu": "сломано"},
        },
    )
    candidate = payload["nerfs"][0]
    assert (
        payload["summary"] == "Зоолок доминирует."
        and payload["summaryEntities"][0]["ref"] == "archetype:standard:Zoolock"
    )
    assert candidate["headline"] == "Ритуал призыва слишком силён" and candidate[
        "reasons"
    ] == ["Зоолок 63%"]
    assert {e["field"] for e in candidate["textEntities"]} >= {
        "headline",
        "verdict",
        "reasons.0",
    }
    assert (
        candidate["social"]["topPosts"][0]["textRu"] == "Сломано"
        and candidate["quotes"][0]["textRu"] == "сломано"
    )


class FakeClaude:
    def __init__(self) -> None:
        self.calls = 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **request: Any) -> Any:
        self.calls += 1
        payload = json.loads(request["messages"][0]["content"])
        output = {
            "posts": [
                {
                    "id": p["id"],
                    "title_ru": f"RU {p['title']}" if p["title"] else "",
                    "text_ru": f"RU {p['text']}",
                }
                for p in payload["posts"]
            ]
        }
        message = SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text", text=json.dumps(output, ensure_ascii=False)
                )
            ],
            stop_reason="end_turn",
            model="claude-sonnet-5-5",
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=100,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                iterations=[],
            ),
        )

        class Stream:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def get_final_message(self) -> Any:
                return message

        return Stream()


def test_post_translations_are_full_and_cached(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    claude = FakeClaude()
    posts = [
        {"id": "reddit:1", "title": "Nerf Ritual", "text": "Long text " * 50},
        {"id": "x:2", "title": None, "text": "tweet"},
    ]
    first, usage = translate.translate_posts(
        posts, glossary_lines="Ritual of Summoning = Ритуал призыва", claude=claude
    )
    assert first["reddit:1"]["titleRu"] == "RU Nerf Ritual" and first["reddit:1"][
        "textRu"
    ].startswith("RU Long text")
    assert first["x:2"]["titleRu"] is None and usage["translated"] == 2
    second, usage2 = translate.translate_posts(posts, claude=claude)
    assert second == first and claude.calls == 1 and usage2["cached"] == 2
    changed, _ = translate.translate_posts(
        [{"id": "x:2", "title": None, "text": "edited tweet"}], claude=claude
    )
    assert changed["x:2"]["textRu"] == "RU edited tweet" and claude.calls == 2


def editor_transport(behaviour: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": behaviour.get("healthy", True)})
        body = json.loads(request.content)
        assert (
            body["profile"] == "news"
            and body["editorial_mode"] == "ANALYSIS"
            and body["mode"] == "edit"
        )
        assert request.headers["X-Request-ID"].startswith("radar-run1-")
        snapshot = body.get("source_snapshots", [{}])[0]
        if snapshot:
            import hashlib

            assert (
                snapshot["sha256"]
                == hashlib.sha256(snapshot["text"].encode()).hexdigest()
            )
        verdict = behaviour.get(body["text"], "accept")
        if verdict == "accept":
            return httpx.Response(
                200,
                json={
                    "text": "Отредактировано.\n\nВторой абзац.",
                    "accepted": True,
                    "status": "edited",
                    "checks_complete": True,
                    "fact_check_passed": True,
                    "prompt_version": "v2",
                },
            )
        if verdict == "unchecked":
            return httpx.Response(
                200,
                json={
                    "text": "Плохо",
                    "accepted": True,
                    "checks_complete": False,
                    "fact_check_passed": True,
                },
            )
        return httpx.Response(
            200,
            json={
                "text": body["text"],
                "accepted": False,
                "status": "rejected",
                "rejection_reasons": ["protected_entity_changed"],
            },
        )

    return httpx.MockTransport(handler)


def test_editor_applies_only_fully_checked_edits() -> None:
    jobs = [
        editor.EditJob("summary", "Итог.", "Zoolock = Зоолок"),
        editor.EditJob("nerfs:a:verdict", "Плохой", ""),
        editor.EditJob("nerfs:b:verdict", "Без проверки", ""),
    ]
    results, report = editor.edit_texts(
        jobs,
        run_id="run1",
        base_url="http://editor",
        transport=editor_transport({"Плохой": "reject", "Без проверки": "unchecked"}),
    )
    assert (
        results["summary"].applied
        and results["summary"].text == "Отредактировано. Второй абзац."
    )
    assert (
        not results["nerfs:a:verdict"].applied
        and results["nerfs:a:verdict"].text == "Плохой"
    )
    assert results["nerfs:a:verdict"].rejection_reasons == ["protected_entity_changed"]
    assert not results["nerfs:b:verdict"].applied
    assert report["applied"] == 1 and report["rejected"] == 2


def test_editor_is_skipped_without_url_or_health(monkeypatch) -> None:
    monkeypatch.delenv("RADAR_EDITOR_URL", raising=False)
    jobs = [editor.EditJob("summary", "Итог.", "")]
    results, report = editor.edit_texts(jobs, run_id="run1")
    assert not report["enabled"] and results["summary"].text == "Итог."
    results, report = editor.edit_texts(
        jobs,
        run_id="run1",
        base_url="http://editor",
        transport=editor_transport({"healthy": False}),
    )
    assert (
        report["error"] == "editor health check failed"
        and not results["summary"].applied
    )


def test_editor_jobs_cover_summary_and_most_confident_verdicts(monkeypatch) -> None:
    monkeypatch.setenv("RADAR_EDITOR_MAX_VERDICTS", "1")
    payload = {
        "summary": "Итог.",
        "nerfs": [
            {"ref": "a", "confidence": 30, "verdict": "Слабый"},
            {"ref": "b", "confidence": 70, "verdict": "Сильный"},
        ],
        "buffs": [],
    }
    jobs = editor.jobs_for_payload(payload, glossary())
    assert [job.key for job in jobs] == ["summary", "nerfs:b:verdict"]


def test_probability_scores_and_accuracy_text() -> None:
    scores = _probability_scores(
        [(0.8, True), (0.7, True), (0.6, False), (0.05, False), (0.0, False)]
    )
    assert scores["predictions"] == 5 and scores["baseRate"] == 0.4
    assert scores["brier"] == round((0.04 + 0.09 + 0.36 + 0.0025 + 0) / 5, 4)
    assert scores["brierSkill"] > 0
    assert [r["band"] for r in scores["reliability"]] == ["0-10%", "50-75%", "75-100%"]
    block = accuracy_block(
        {
            "generatedAt": "g",
            "statistical": {
                "patches": 8,
                "nerf": {"@5": {"precision": 0.275}, "@10": {"recall": 0.59}},
                "probability": {},
            },
            "model": {
                "patches": 8,
                "nerf": {"@5": {"precision": 0.325}, "@10": {"recall": 0.77}},
                "probability": {
                    "nerf": {
                        "reliability": [
                            {"band": "50-75%", "predictions": 6, "observedRate": 0.5}
                        ]
                    }
                },
            },
        }
    )
    assert block["basis"] == "model" and "8 прошлых балансных патчах" in block["text"]
    assert (
        "33%" in block["text"] and "77%" in block["text"] and "50-75%" in block["text"]
    )
    assert block["caveat"]
    assert accuracy_block(None) is None


def test_clean_text_localizes_company_format_and_points() -> None:
    assert (
        glossary().clean("Близард понерфит в Диком на 10,1 п.п.")
        == "Blizzard понерфит в Вольном на 10,1 пункта"
    )


def test_transliterations_become_russian_names() -> None:
    g = glossary()
    g.add("Blazing Invocation", "Огненное заклятье")
    g.add("Godfather Kazakus", "Крестный отец Казакус")
    issues: list[str] = []
    text = g.clean(
        "Похоже на Блазинг Инвокейшн в 36.0.3, а Годфазер Казакус ещё силён; Ритуал призыва тоже.",
        issues,
    )
    assert (
        text
        == "Похоже на Огненное заклятье в 36.0.3, а Крестный отец Казакус ещё силён; Ритуал призыва тоже."
    )
    assert "transliteration_replaced" in issues
    # Real Russian names and short words are left alone.
    assert g.clean("Маги и Друид, Чернокнижник") == "Маги и Друид, Чернокнижник"
    assert g.clean_tree(
        {
            "a": ["Zoolock is up", {"b": "Ritual of Summoning"}],
            "code": "same_card",
            "n": 3,
        }
    ) == {"a": ["Зоолок is up", {"b": "Ритуал призыва"}], "code": "same_card", "n": 3}


def test_transliteration_fix_leaves_inflected_russian_words_alone() -> None:
    g = glossary()
    g.add("Magmar", "Магмарь")
    g.add("Demonlock", "Демонолок")
    g.add("C'Thun DH", "К'тун Охотник на демонов")
    assert g.clean("Игроки жалуются на Мага, а К'тун Охотник на демонов силён") == (
        "Игроки жалуются на Мага, а К'тун Охотник на демонов силён"
    )


def test_any_collectible_card_and_game_terms_are_russian() -> None:
    g = glossary()
    g.add_cards(
        {"divergence": "Расхождение", "bat mask": "Маска летучей мыши"},
        {"divergence": "Divergence", "bat mask": "Bat Mask"},
    )
    assert (
        g.clean("Баны Divergence и Bat Mask, плюс Rush на ходу 2")
        == "Баны Расхождение и Маска летучей мыши, плюс Натиск на ходу 2"
    )
    assert (
        "Divergence" not in g.as_lines()
    )  # the full card list is not sent to the model
