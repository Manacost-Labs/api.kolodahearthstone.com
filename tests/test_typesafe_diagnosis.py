from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.ai_review import reset_ai_review_budget, review_candidate
from app.config import ai_review_diagnosis_min_confidence, ai_review_diagnosis_model
from app.sources import Source
from app.typesafe_diagnosis import DOMAIN_CRITERIA, SYSTEMONE_URL

SOURCE = Source(
    id="typesafe_test",
    url="https://example.test",
    site="test",
    category="meta",
    description="Synthetic source",
)


@pytest.fixture(autouse=True)
def review_environment(monkeypatch):
    settings = {
        "HS_AI_REVIEW_ENABLED": "true",
        "HS_AI_REVIEW_DIAGNOSE_FAILURES": "true",
        "HS_AI_REVIEW_DIAGNOSIS_PROVIDER": "typesafe",
        "HS_AI_REVIEW_DIAGNOSIS_MODEL": "",
        "HS_AI_REVIEW_DIAGNOSIS_MIN_CONFIDENCE": "0.95",
        "HS_AI_REVIEW_MODEL": "test/chat-model",
        "HS_AI_REVIEW_SOURCE_IDS": SOURCE.id,
        "HS_AI_REVIEW_MODE": "quarantine",
        "HS_AI_REVIEW_RETRY_ATTEMPTS": "1",
        "HS_OPENROUTER_API_KEY": "synthetic-openrouter-key",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    reset_ai_review_budget()
    yield
    reset_ai_review_budget()


def response_body(choice="auth", confidence=0.98):
    return {
        "model": "typesafe/jev-1.13-20260917",
        "provider": "TypeSafe",
        "answers": {
            "failure_domain": {
                "type": "choice",
                "choice": choice,
                "confidence": confidence,
                "probabilities": {key: float(key == choice) for key in DOMAIN_CRITERIA},
            }
        },
        "usage": {"input_tokens": 700, "output_tokens": 45, "cost": 0.0000294},
    }


def run_review(handler, **overrides):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            arguments = {
                "backend": "test-backend",
                "deterministic_ok": False,
                "deterministic_reason": "rejected",
                "review_kind": "failure_diagnosis",
                "client": client,
            }
            arguments.update(overrides)
            return await review_candidate(
                SOURCE,
                {
                    "title": "Sign in",
                    "text_preview": ["upstream-private-sentinel"],
                    "structured": {"raw": "upstream-private-sentinel"},
                },
                **arguments,
            )

    return asyncio.run(run())


def test_openrouter_systemone_request_and_advisory_telemetry():
    def handler(request):
        assert str(request.url) == SYSTEMONE_URL
        assert request.headers["Authorization"] == "Bearer synthetic-openrouter-key"
        body = json.loads(request.content)
        assert set(body) == {"model", "state", "questions"}
        assert body["model"] == "typesafe/jev-1.13"
        assert body["state"]["evidence"]["deterministic_validation"]["passed"] is False
        assert body["questions"]["failure_domain"]["type"] == "choice"
        assert b"upstream-private-sentinel" not in request.content
        return httpx.Response(200, json=response_body())

    result = run_review(handler)
    assert result.state == "ok"
    assert result.verdict is None
    assert not result.should_quarantine
    assert result.diagnosis.failure_domain == "auth"
    assert result.diagnosis.recommended_action == "refresh_auth"
    assert result.diagnosis.evidence_codes == ["deterministic_rejection"]
    assert result.provider == "TypeSafe"
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (
        700,
        45,
        745,
    )
    assert result.cost_usd == 0.0000294
    telemetry = result.telemetry()
    assert telemetry["diagnosis_confidence"] == 0.98
    assert telemetry["diagnosis_probabilities"]["auth"] == 1.0
    assert telemetry["prompt_version"] == "typesafe-diagnosis-v2"
    assert telemetry["model"] == "typesafe/jev-1.13"


@pytest.mark.parametrize(
    "choice,confidence", [("auth", 0.5), ("auth", 0.1), ("unknown", 1.0)]
)
def test_uncertain_diagnosis_has_no_action(choice, confidence):
    result = run_review(
        lambda _: httpx.Response(200, json=response_body(choice, confidence))
    )
    assert result.state == "ok"
    assert result.diagnosis.classification == "inconclusive"
    assert result.diagnosis.failure_domain == "unknown"
    assert result.diagnosis.recommended_action == "none"
    assert not result.should_quarantine


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_answer",
        "bad_choice",
        "missing_probability",
        "bad_sum",
        "negative_probability",
        "wrong_winner",
        "bad_confidence",
        "bool_confidence",
        "nan_confidence",
        "extra_field",
        "chat_response",
    ],
)
def test_malformed_answers_fail_without_a_diagnosis(mutation):
    body = response_body()
    answer = body["answers"]["failure_domain"]
    if mutation == "missing_answer":
        body["answers"] = {}
    elif mutation == "bad_choice":
        answer["choice"] = "publish_anyway"
    elif mutation == "missing_probability":
        del answer["probabilities"]["schema"]
    elif mutation == "bad_sum":
        answer["probabilities"]["auth"] = 0.4
    elif mutation == "negative_probability":
        answer["probabilities"]["schema"] = -0.1
    elif mutation == "wrong_winner":
        answer["choice"] = "schema"
    elif mutation == "bad_confidence":
        answer["confidence"] = 2.0
    elif mutation == "bool_confidence":
        answer["confidence"] = True
    elif mutation == "nan_confidence":
        answer["confidence"] = float("nan")
    elif mutation == "extra_field":
        answer["commands"] = ["publish"]
    elif mutation == "chat_response":
        body = {"choices": [{"message": {"content": "{}"}}]}
    result = run_review(lambda _: httpx.Response(200, content=json.dumps(body)))
    assert result.state == "error"
    assert result.error_type == "invalid_response_schema"
    assert result.diagnosis is None
    assert not result.should_quarantine


def test_retry_429_uses_same_endpoint_and_existing_budget(monkeypatch):
    monkeypatch.setenv("HS_AI_REVIEW_RETRY_ATTEMPTS", "2")
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json=response_body("freshness"))

    result = run_review(handler)
    assert result.state == "ok"
    assert calls == [SYSTEMONE_URL, SYSTEMONE_URL]
    assert result.request_attempts == 2
    assert result.diagnosis.recommended_action == "retry_existing_route"


def test_timeout_is_advisory(monkeypatch):
    async def handler(_):
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=response_body())

    monkeypatch.setattr("app.ai_review.ai_review_timeout_seconds", lambda: 0.001)
    result = run_review(handler)
    assert result.state == "error"
    assert result.error_type == "total_timeout"
    assert result.diagnosis is None
    assert not result.should_quarantine


@pytest.mark.parametrize("status", [401, 503])
def test_http_errors_remain_non_authoritative(status):
    result = run_review(lambda _: httpx.Response(status))
    assert result.error_type == f"http_{status}"
    assert result.diagnosis is None


def test_error_payload_does_not_accept_answers():
    body = response_body()
    body["error"] = {"code": 503}
    result = run_review(lambda _: httpx.Response(200, json=body))
    assert result.state == "error"
    assert result.diagnosis is None


def test_disabled_and_successful_candidates_never_call_typesafe(monkeypatch):
    def forbidden(_):
        pytest.fail("unexpected network call")

    assert run_review(forbidden, deterministic_ok=True).state == "skipped"
    monkeypatch.setenv("HS_AI_REVIEW_ENABLED", "false")
    assert run_review(forbidden).state == "disabled"


def test_candidate_review_still_uses_chat():
    def handler(request):
        assert request.url.path == "/api/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "test/chat-model"
        assert "questions" not in body
        return httpx.Response(503)

    result = run_review(handler, review_kind="candidate", deterministic_ok=True)
    assert result.error_type == "http_503"


def test_invalid_provider_fails_before_network(monkeypatch):
    monkeypatch.setenv("HS_AI_REVIEW_DIAGNOSIS_PROVIDER", "typo")
    result = run_review(lambda _: pytest.fail("unexpected request"))
    assert result.error_type == "invalid_diagnosis_provider"


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "2", "invalid"])
def test_confidence_config_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("HS_AI_REVIEW_DIAGNOSIS_MIN_CONFIDENCE", value)
    assert ai_review_diagnosis_min_confidence() == 0.95


def test_opt_in_defaults_preserve_chat_diagnosis(monkeypatch):
    monkeypatch.delenv("HS_AI_REVIEW_DIAGNOSIS_PROVIDER")
    assert ai_review_diagnosis_model() == "test/chat-model"


def test_systemone_response_size_is_bounded():
    result = run_review(lambda _: httpx.Response(200, content=b" " * (128 * 1024 + 1)))
    assert result.error_type == "response_too_large"
    assert result.diagnosis is None
