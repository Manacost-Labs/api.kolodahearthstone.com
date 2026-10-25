from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Self

import pytest

from app.balance_radar import analysis
from app.balance_radar.analysis import (
    AnalysisError,
    analyze,
    parse_message,
    validate_output,
)
from app.balance_radar.schema import ModelOutput


def candidate(ref: str, **overrides: Any) -> dict[str, Any]:
    return {
        "ref": ref,
        "confidence": 70,
        "headline": "Сильная карта",
        "verdict": "Винрейт 55,9% при 18,8% популярности.",
        "reasons": ["Высокий винрейт", "Растущая популярность"],
        "suggested_change": "Стоимость 2 → 3",
        "sentiment": "negative",
        "community_stance": "wants_nerf",
        "social_summary": "Игроки просят нерф.",
        "evidence_post_ids": ["reddit:1"],
        "precedent_ids": [],
        **overrides,
    }


def output(**overrides: Any) -> dict[str, Any]:
    return {
        "summary": "Чернокнижник доминирует.",
        "nerfs": [candidate("card:BE_RIT")],
        "buffs": [],
        **overrides,
    }


def message(
    text: str, *, stop_reason: str = "end_turn", model: str = "claude-sonnet-5-5"
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber")
        if stop_reason == "refusal"
        else None,
        model=model,
        usage=SimpleNamespace(
            input_tokens=80_000,
            output_tokens=10_000,
            cache_read_input_tokens=4_000,
            cache_creation_input_tokens=0,
            iterations=[SimpleNamespace(type="message")],
        ),
        _request_id="req_test",
    )


def test_valid_structured_output_parses() -> None:
    parsed = parse_message(message(json.dumps(output(), ensure_ascii=False)))
    assert isinstance(parsed, ModelOutput)
    assert parsed.nerfs[0].ref == "card:BE_RIT" and parsed.nerfs[0].confidence == 70


@pytest.mark.parametrize(
    "broken",
    [
        output(nerfs=[candidate("card:BE_RIT", confidence=150)]),
        output(nerfs=[candidate("card:BE_RIT", sentiment="angry")]),
        output(
            nerfs=[
                {k: v for k, v in candidate("card:BE_RIT").items() if k != "verdict"}
            ]
        ),
        output(nerfs=[candidate("card:BE_RIT", url="https://made.up")]),
        {"summary": "без списков"},
    ],
    ids=["confidence-range", "enum", "missing-field", "extra-field", "missing-lists"],
)
def test_schema_violations_are_rejected(broken: dict[str, Any]) -> None:
    with pytest.raises(AnalysisError, match="schema validation"):
        parse_message(message(json.dumps(broken, ensure_ascii=False)))


@pytest.mark.parametrize(
    ("text", "stop_reason", "error"),
    [
        ("{}", "refusal", "refused"),
        ('{"summary": "обрыв', "max_tokens", "max_tokens"),
        ("", "end_turn", "no text"),
        ("not json", "end_turn", "schema validation"),
    ],
)
def test_unusable_messages_raise(text: str, stop_reason: str, error: str) -> None:
    with pytest.raises(AnalysisError, match=error):
        parse_message(message(text, stop_reason=stop_reason))


def test_unknown_refs_posts_and_duplicates_are_dropped() -> None:
    raw = ModelOutput.model_validate(
        output(
            nerfs=[
                candidate("card:BE_RIT", evidence_post_ids=["reddit:1", "x:invented"]),
                candidate("card:INVENTED"),
                candidate("card:BE_RIT"),
            ],
            buffs=[
                candidate("card:BE_RIT"),
                *[candidate(f"class:standard:C{i}") for i in range(12)],
            ],
        )
    )
    allowed = {"card:BE_RIT", *(f"class:standard:C{i}" for i in range(12))}
    cleaned, dropped = validate_output(raw, allowed, {"reddit:1"})

    assert [c.ref for c in cleaned.nerfs] == ["card:BE_RIT"]
    assert cleaned.nerfs[0].evidence_post_ids == ["reddit:1"]
    assert len(cleaned.buffs) == analysis.MAX_CANDIDATES
    assert "card:BE_RIT" not in [
        c.ref for c in cleaned.buffs
    ]  # same entity cannot be both
    assert "nerfs:card:INVENTED:unknown_ref" in dropped
    assert "nerfs:card:BE_RIT:post:x:invented" in dropped
    assert "buffs:card:BE_RIT:duplicate" in dropped
    assert any(item.endswith("over_limit") for item in dropped)


class FakeStream:
    def __init__(self, msg: Any) -> None:
        self.msg = msg

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def get_final_message(self) -> Any:
        return self.msg


class FakeClaude:
    def __init__(self, msg: Any) -> None:
        self.requests: list[dict[str, Any]] = []
        self.msg = msg
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **request: Any) -> FakeStream:
        self.requests.append(request)
        return FakeStream(self.msg)


def test_analyze_sends_structured_output_request_and_prices_usage(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    fake = FakeClaude(message(json.dumps(output(), ensure_ascii=False)))

    result = analyze(
        {"format": "standard"},
        allowed_refs={"card:BE_RIT"},
        allowed_posts={"reddit:1"},
        model="claude-sonnet-5-5",
        client=fake,
    )

    request = fake.requests[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["output_format"] is ModelOutput
    assert request["thinking"] == {"type": "adaptive"}
    assert request["fallbacks"] == "default" and request["betas"] == [
        analysis.FALLBACK_BETA
    ]
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert json.loads(request["messages"][0]["content"]) == {"format": "standard"}
    # 80k in × $2 + 10k out × $10 + 4k cache reads × $0.20 per million tokens.
    assert result.usage["costUsd"] == 0.2608
    assert result.usage["fallbackUsed"] is False
    assert result.model == "claude-sonnet-5-5"


def test_model_override_comes_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("RADAR_ANALYSIS_MODEL", "claude-opus-5-5")
    assert analysis.analysis_model() == "claude-opus-5-5"
    monkeypatch.delenv("RADAR_ANALYSIS_MODEL")
    assert analysis.analysis_model() == "claude-sonnet-5-5"


def test_sdk_side_validation_error_becomes_an_analysis_error(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))

    class Raising(FakeClaude):
        def stream(self, **request: Any) -> FakeStream:
            ModelOutput.model_validate({"summary": "x"})  # raises ValidationError
            raise AssertionError("unreachable")

    with pytest.raises(AnalysisError, match="schema validation"):
        analyze({}, allowed_refs=set(), allowed_posts=set(), client=Raising(None))


def test_missing_api_key_is_reported_without_a_traceback(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_PROFILE", "balance-radar-test-missing-profile")
    monkeypatch.setenv("HOME", "/nonexistent-balance-radar-home")
    with pytest.raises(AnalysisError, match="not configured"):
        analyze({}, allowed_refs=set(), allowed_posts=set())
