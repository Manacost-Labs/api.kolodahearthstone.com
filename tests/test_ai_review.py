from __future__ import annotations

import asyncio
import json
import os
import unittest
from collections.abc import Callable
from unittest.mock import patch

import httpx

from app.ai_review import (
    AIFailureDiagnosis,
    AIPageVerdict,
    AIReviewResult,
    reset_ai_review_budget,
    review_candidate,
)
from app.ai_review_evidence import build_ai_review_evidence_v2, evidence_sha256
from app.config import (
    ai_review_confidence_threshold,
    ai_review_max_per_refresh,
    ai_review_timeout_seconds,
)
from app.sources import Source

TEST_SOURCE = Source(
    id="ai_review_test_source",
    url="https://example.test/hearthstone",
    site="test-site",
    category="meta",
    description="Synthetic Hearthstone meta page",
)
TEST_MODEL = "test/openrouter-structured-model"
TEST_API_KEY = "test-openrouter-key-not-a-production-secret"


def _environment(**overrides: str) -> dict[str, str]:
    values = {
        "HS_AI_REVIEW_ENABLED": "true",
        "HS_AI_REVIEW_MODE": "observe",
        "HS_AI_REVIEW_MODEL": TEST_MODEL,
        "HS_AI_REVIEW_SOURCE_IDS": TEST_SOURCE.id,
        "HS_AI_REVIEW_CONFIDENCE_THRESHOLD": "0.90",
        "HS_AI_REVIEW_MAX_CONCURRENCY": "2",
        "HS_AI_REVIEW_MAX_PER_REFRESH": "20",
        "HS_AI_REVIEW_MAX_FAILURES_PER_REFRESH": "20",
        "HS_AI_REVIEW_RETRY_ATTEMPTS": "1",
        "HS_OPENROUTER_API_KEY": TEST_API_KEY,
    }
    values.update(overrides)
    return values


def _verdict_content(
    *,
    verdict: str = "pass",
    confidence: float = 0.98,
) -> dict[str, object]:
    return {
        "verdict": verdict,
        "target_page": True,
        "challenge_detected": False,
        "content_complete": verdict != "fail",
        "parse_compatible": verdict != "fail",
        "confidence": confidence,
        "reason_codes": ["none" if verdict == "pass" else "schema_mismatch"],
        "summary": "Structured evidence is internally consistent.",
    }


def _openrouter_response(
    *,
    verdict: str = "pass",
    confidence: float = 0.98,
) -> dict[str, object]:
    return {
        "id": "generation-test",
        "model": TEST_MODEL,
        "provider": "DeepInfra",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        _verdict_content(verdict=verdict, confidence=confidence)
                    ),
                },
            }
        ],
        "usage": {
            "prompt_tokens": 101,
            "completion_tokens": 29,
            "total_tokens": 130,
            "cost": 0.00042,
        },
    }


def _diagnosis_content() -> dict[str, object]:
    return {
        "classification": "anomalous",
        "failure_domain": "regression",
        "evidence_codes": ["regression_count_drop", "deterministic_rejection"],
        "recommended_action": "preserve_lkg",
        "confidence_band": "high",
    }


def _diagnosis_response() -> dict[str, object]:
    payload = _openrouter_response()
    payload["choices"] = [
        {
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": json.dumps(_diagnosis_content()),
            },
        }
    ]
    return payload


async def _review_with_transport(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    source: Source = TEST_SOURCE,
    parsed: dict[str, object] | None = None,
    review_kind: str = "candidate",
):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        return await review_candidate(
            source,
            parsed or {"structured": {"rows": 42, "winrate": 51.2}},
            backend="test-backend",
            deterministic_ok=True,
            deterministic_reason="deterministic validation passed",
            deterministic_extra={"rows_total": 42},
            quality={"quality_score": 0.99},
            review_kind=review_kind,  # type: ignore[arg-type]
            client=client,
        )


class _SlowTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=_openrouter_response(), request=request)


class AIReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_ai_review_budget()

    def tearDown(self) -> None:
        reset_ai_review_budget()

    def test_safe_runtime_defaults_match_documented_example(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ai_review_timeout_seconds(), 15.0)
            self.assertEqual(ai_review_confidence_threshold(), 0.95)
            self.assertEqual(ai_review_max_per_refresh(), 10)

    def test_disabled_review_never_sends_a_request(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            raise AssertionError("disabled AI review must not contact OpenRouter")

        with patch.dict(
            os.environ,
            _environment(HS_AI_REVIEW_ENABLED="false"),
            clear=False,
        ):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.state, "disabled")
        self.assertIsNone(result.verdict)
        self.assertEqual(requests, [])

    def test_enabled_review_with_empty_allowlist_skips_without_request(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            raise AssertionError("an empty allowlist must not contact OpenRouter")

        with patch.dict(
            os.environ,
            _environment(HS_AI_REVIEW_SOURCE_IDS=""),
            clear=False,
        ):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.state, "skipped")
        self.assertEqual(result.error_type, "source_allowlist_empty")
        self.assertIsNone(result.verdict)
        self.assertEqual(requests, [])

    def test_explicit_wildcard_allowlist_reviews_every_source(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=_openrouter_response())

        unlisted_source = Source(
            id="explicit_wildcard_source",
            url="https://example.test/wildcard",
            site="test-site",
            category="meta",
        )
        with patch.dict(
            os.environ,
            _environment(HS_AI_REVIEW_SOURCE_IDS="*"),
            clear=False,
        ):
            result = asyncio.run(
                _review_with_transport(handler, source=unlisted_source)
            )

        self.assertEqual(result.state, "ok")
        self.assertIsNotNone(result.verdict)
        self.assertEqual(len(requests), 1)

    def test_strict_structured_openrouter_response_is_accepted(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_openrouter_response())

        with patch.dict(os.environ, _environment(), clear=False):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.state, "ok")
        self.assertIsNotNone(result.verdict)
        self.assertEqual(result.verdict.verdict, "pass")  # type: ignore[union-attr]
        self.assertEqual(result.model, TEST_MODEL)
        self.assertEqual(result.provider, "DeepInfra")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.prompt_tokens, 101)
        self.assertEqual(result.completion_tokens, 29)
        self.assertEqual(result.total_tokens, 130)
        self.assertEqual(result.cost_usd, 0.00042)
        self.assertEqual(len(captured), 1)

        request = captured[0]
        self.assertEqual(
            str(request.url),
            "https://openrouter.ai/api/v1/chat/completions",
        )
        self.assertEqual(request.headers["Authorization"], f"Bearer {TEST_API_KEY}")
        payload = json.loads(request.content)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(
            payload["reasoning"],
            {"effort": "none", "exclude": True},
        )
        self.assertEqual(payload["plugins"], [{"id": "response-healing"}])
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertFalse(
            payload["response_format"]["json_schema"]["schema"]["additionalProperties"]
        )
        self.assertNotIn(
            "summary",
            payload["response_format"]["json_schema"]["schema"]["properties"],
        )
        self.assertEqual(payload["provider"]["data_collection"], "deny")
        self.assertTrue(payload["provider"]["zdr"])
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertTrue(payload["provider"]["allow_fallbacks"])

    def test_failure_diagnosis_uses_separate_strict_schema_and_never_quarantines(
        self,
    ) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json=_diagnosis_response())

        with patch.dict(os.environ, _environment(), clear=False):
            result = asyncio.run(
                _review_with_transport(handler, review_kind="failure_diagnosis")
            )

        self.assertEqual(result.state, "ok")
        self.assertIsNone(result.verdict)
        self.assertIsInstance(result.diagnosis, AIFailureDiagnosis)
        self.assertEqual(result.diagnosis.failure_domain, "regression")  # type: ignore[union-attr]
        self.assertFalse(result.should_quarantine)
        self.assertEqual(result.telemetry()["review_kind"], "failure_diagnosis")
        payload = json.loads(captured[0].content)
        self.assertEqual(
            payload["response_format"]["json_schema"]["name"],
            "hearthstone_failure_diagnosis",
        )
        properties = payload["response_format"]["json_schema"]["schema"]["properties"]
        self.assertIn("failure_domain", properties)
        self.assertNotIn("target_page", properties)

    def test_success_budget_cannot_exhaust_failure_diagnosis_budget(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            body = json.loads(request.content)
            task = json.loads(body["messages"][1]["content"])["task"]
            response = (
                _diagnosis_response()
                if task == "diagnose_rejected_candidate"
                else _openrouter_response()
            )
            return httpx.Response(200, json=response)

        async def scenario() -> tuple[AIReviewResult, AIReviewResult, AIReviewResult]:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                success = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 42}},
                    backend="test",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
                exhausted = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 43}},
                    backend="test",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
                diagnosis = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 1}},
                    backend="test",
                    deterministic_ok=False,
                    deterministic_reason="rejected",
                    review_kind="failure_diagnosis",
                    client=client,
                )
            return success, exhausted, diagnosis

        with patch.dict(
            os.environ,
            _environment(
                HS_AI_REVIEW_MAX_PER_REFRESH="1",
                HS_AI_REVIEW_MAX_FAILURES_PER_REFRESH="1",
            ),
            clear=False,
        ):
            success, exhausted, diagnosis = asyncio.run(scenario())

        self.assertEqual(success.state, "ok")
        self.assertEqual(exhausted.error_type, "refresh_budget_exhausted")
        self.assertEqual(diagnosis.state, "ok")
        self.assertEqual(len(requests), 2)

    def test_known_budget_exhaustion_does_not_wait_for_concurrency(self) -> None:
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        class SlowClient:
            async def post(self, *_args, **_kwargs) -> httpx.Response:
                request_started.set()
                await release_request.wait()
                return httpx.Response(200, json=_openrouter_response())

        async def scenario() -> tuple[AIReviewResult, AIReviewResult]:
            client = SlowClient()
            first_task = asyncio.create_task(
                review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 41}},
                    backend="test",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,  # type: ignore[arg-type]
                )
            )
            await request_started.wait()
            exhausted = await asyncio.wait_for(
                review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 42}},
                    backend="test",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,  # type: ignore[arg-type]
                ),
                timeout=0.5,
            )
            release_request.set()
            return await first_task, exhausted

        with patch.dict(
            os.environ,
            _environment(
                HS_AI_REVIEW_MAX_CONCURRENCY="1",
                HS_AI_REVIEW_MAX_PER_REFRESH="1",
            ),
            clear=False,
        ):
            first, exhausted = asyncio.run(scenario())

        self.assertEqual(first.state, "ok")
        self.assertEqual(exhausted.state, "skipped")
        self.assertEqual(exhausted.error_type, "refresh_budget_exhausted")
        self.assertEqual(exhausted.request_attempts, 0)

    def test_transient_http_error_retries_with_bounded_attempt_count(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(503, headers={"Retry-After": "0"})
            return httpx.Response(200, json=_openrouter_response())

        with patch.dict(
            os.environ,
            _environment(
                HS_AI_REVIEW_RETRY_ATTEMPTS="2",
                HS_AI_REVIEW_RETRY_BASE_SECONDS="0",
            ),
            clear=False,
        ):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.state, "ok")
        self.assertEqual(result.request_attempts, 2)
        self.assertEqual(request_count, 2)

    def test_retry_attempts_cannot_exceed_refresh_request_budget(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(503, headers={"Retry-After": "0"})

        with patch.dict(
            os.environ,
            _environment(
                HS_AI_REVIEW_MAX_PER_REFRESH="1",
                HS_AI_REVIEW_RETRY_ATTEMPTS="3",
                HS_AI_REVIEW_RETRY_BASE_SECONDS="0",
            ),
            clear=False,
        ):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.state, "skipped")
        self.assertEqual(result.error_type, "refresh_budget_exhausted")
        self.assertEqual(result.request_attempts, 1)
        self.assertEqual(request_count, 1)

    def test_total_deadline_bounds_all_attempts(self) -> None:
        async def scenario() -> AIReviewResult:
            async with httpx.AsyncClient(transport=_SlowTransport()) as client:
                return await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 42}},
                    backend="test",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )

        with (
            patch.dict(
                os.environ,
                _environment(HS_AI_REVIEW_RETRY_ATTEMPTS="3"),
                clear=False,
            ),
            patch("app.ai_review.ai_review_timeout_seconds", return_value=0.05),
        ):
            result = asyncio.run(scenario())

        self.assertEqual(result.state, "error")
        self.assertEqual(result.error_type, "total_timeout")
        self.assertEqual(result.request_attempts, 1)
        self.assertLess(result.latency_ms or 1000, 150)

    def test_oversized_decoded_response_is_stopped_before_json_buffering(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * (128 * 1024 + 1))

        with patch.dict(os.environ, _environment(), clear=False):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.state, "error")
        self.assertEqual(result.error_type, "response_too_large")
        self.assertEqual(result.request_attempts, 1)

    def test_http_200_transient_provider_error_retries(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(
                    200,
                    json={
                        "error": {
                            "message": "provider unavailable",
                            "metadata": {"error_type": "provider_unavailable"},
                        }
                    },
                )
            return httpx.Response(200, json=_openrouter_response())

        with patch.dict(
            os.environ,
            _environment(
                HS_AI_REVIEW_RETRY_ATTEMPTS="2",
                HS_AI_REVIEW_RETRY_BASE_SECONDS="0",
            ),
            clear=False,
        ):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.state, "ok")
        self.assertEqual(result.request_attempts, 2)
        self.assertEqual(request_count, 2)

    def test_http_200_server_and_numeric_provider_errors_retry(self) -> None:
        for provider_error in (
            {"metadata": {"error_type": "server"}},
            {"code": 503},
        ):
            with self.subTest(provider_error=provider_error):
                reset_ai_review_budget()
                request_count = 0

                def handler(
                    _request: httpx.Request,
                    value: dict[str, object] = provider_error,
                ) -> httpx.Response:
                    nonlocal request_count
                    request_count += 1
                    if request_count == 1:
                        return httpx.Response(200, json={"error": value})
                    return httpx.Response(200, json=_openrouter_response())

                with patch.dict(
                    os.environ,
                    _environment(
                        HS_AI_REVIEW_RETRY_ATTEMPTS="2",
                        HS_AI_REVIEW_RETRY_BASE_SECONDS="0",
                    ),
                    clear=False,
                ):
                    result = asyncio.run(_review_with_transport(handler))

                self.assertEqual(result.state, "ok")
                self.assertEqual(result.request_attempts, 2)
                self.assertEqual(request_count, 2)

    def test_router_and_response_healing_metadata_is_bounded(self) -> None:
        response = _openrouter_response()
        response["provider"] = "SiliconFlow"
        response["openrouter_metadata"] = {
            "strategy": "fallback",
            "attempts": [{"status": 503}, {"status": 200}],
            "pipeline": [
                {"id": "response-healing", "status": "applied"},
            ],
        }

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response)

        with patch.dict(os.environ, _environment(), clear=False):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.provider, "SiliconFlow")
        self.assertEqual(result.router_attempts, 2)
        self.assertEqual(result.router_strategy, "fallback")
        self.assertTrue(result.response_healing_applied)

    def test_request_evidence_excludes_sensitive_page_material(self) -> None:
        secret_values = {
            "raw_html": "<html><body>RAW-PAGE-MARKER</body></html>",
            "cookie": "session=COOKIE-MARKER",
            "token": "TOKEN-MARKER-DO-NOT-SEND",
            "url": "https://private.example.test/URL-MARKER",
            "deck_code": "DECK-CODE-MARKER",
            "secret": "SECRET-MARKER",
        }
        source = Source(
            id=TEST_SOURCE.id,
            url="https://private.example.test/source?token=SOURCE-URL-MARKER",
            site=TEST_SOURCE.site,
            category=TEST_SOURCE.category,
            description=TEST_SOURCE.description,
        )
        parsed: dict[str, object] = {
            **secret_values,
            "structured": {
                "rows": [
                    {
                        "name": "Mage",
                        "winrate": 52.4,
                        "url": secret_values["url"],
                        "deck_code": secret_values["deck_code"],
                        "raw_html": secret_values["raw_html"],
                    }
                ],
                "cookie": secret_values["cookie"],
                "token": secret_values["token"],
                "secret": secret_values["secret"],
            },
        }
        captured_body: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.append(request.content.decode("utf-8"))
            return httpx.Response(200, json=_openrouter_response())

        with patch.dict(os.environ, _environment(), clear=False):
            result = asyncio.run(
                _review_with_transport(handler, source=source, parsed=parsed)
            )

        self.assertEqual(result.state, "ok")
        self.assertEqual(len(captured_body), 1)
        body = captured_body[0]
        for sensitive_value in [*secret_values.values(), "SOURCE-URL-MARKER"]:
            with self.subTest(sensitive_value=sensitive_value):
                self.assertNotIn(sensitive_value, body)

        payload = json.loads(body)
        user_content = json.loads(payload["messages"][1]["content"])
        evidence_text = json.dumps(user_content["evidence"], ensure_ascii=False)
        for sensitive_key in secret_values:
            with self.subTest(sensitive_key=sensitive_key):
                self.assertNotIn(f'"{sensitive_key}"', evidence_text)

    def test_malformed_incomplete_and_error_responses_use_safe_error_subtypes(
        self,
    ) -> None:
        remote_secret = "REMOTE-BODY-SECRET-MUST-NOT-LEAK"
        responses = {
            "http_error": (
                httpx.Response(502, text=f"provider failure: {remote_secret}"),
                "http_502",
            ),
            "malformed_json": (
                httpx.Response(200, text=f"not-json {remote_secret}"),
                "invalid_response_json",
            ),
            "incomplete": (
                httpx.Response(
                    200,
                    json={
                        **_openrouter_response(),
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {"content": remote_secret},
                            }
                        ],
                    },
                ),
                "invalid_response_finish_reason",
            ),
            "error_payload": (
                httpx.Response(
                    200,
                    json={"error": {"message": remote_secret}},
                ),
                "invalid_response_error_payload",
            ),
            "choices_missing": (
                httpx.Response(
                    200,
                    json={"model": TEST_MODEL, "choices": []},
                ),
                "invalid_response_choices_missing",
            ),
        }

        for case, (response, expected_error_type) in responses.items():
            with self.subTest(case=case):
                reset_ai_review_budget()

                def handler(_request: httpx.Request, value=response) -> httpx.Response:
                    return value

                with patch.dict(os.environ, _environment(), clear=False):
                    result = asyncio.run(_review_with_transport(handler))

                self.assertEqual(result.state, "error")
                self.assertIsNone(result.verdict)
                self.assertEqual(result.error_type, expected_error_type)
                serialized = repr(result) + json.dumps(
                    result.telemetry(), sort_keys=True
                )
                self.assertNotIn(remote_secret, serialized)

    def test_invalid_content_preserves_only_safe_response_metadata(self) -> None:
        remote_secret = "REMOTE-VALIDATION-TEXT-MUST-NOT-LEAK"
        invalid_content = {
            **_verdict_content(),
            "confidence": "not-a-number",
            "raw_validation_text": remote_secret,
        }
        response_payload = {
            **_openrouter_response(),
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(invalid_content)},
                }
            ],
        }
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=response_payload)

        with patch.dict(os.environ, _environment(), clear=False):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.state, "error")
        self.assertEqual(result.error_type, "invalid_response_schema")
        self.assertEqual(result.model, TEST_MODEL)
        self.assertEqual(result.provider, "DeepInfra")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.prompt_tokens, 101)
        self.assertEqual(result.completion_tokens, 29)
        self.assertEqual(result.total_tokens, 130)
        self.assertEqual(result.cost_usd, 0.00042)
        self.assertEqual(request_count, 1)
        serialized = repr(result) + json.dumps(result.telemetry(), sort_keys=True)
        self.assertNotIn(remote_secret, serialized)

    def test_unknown_remote_model_and_provider_labels_are_not_retained(self) -> None:
        remote_secret = "synthetic-untrusted-provider-label-xyz987654"
        response_payload = {
            **_openrouter_response(),
            "model": remote_secret,
            "provider": remote_secret,
        }

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response_payload)

        with patch.dict(os.environ, _environment(), clear=False):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.state, "ok")
        self.assertEqual(result.model, TEST_MODEL)
        self.assertIsNone(result.provider)
        serialized = repr(result) + json.dumps(result.telemetry(), sort_keys=True)
        self.assertNotIn(remote_secret, serialized)

    def test_unknown_router_and_provider_error_labels_are_bounded(self) -> None:
        private_marker = "private_marker"
        response_payload = {
            **_openrouter_response(),
            "provider": private_marker,
            "openrouter_metadata": {"strategy": private_marker},
            "error": {
                "message": "untrusted remote failure",
                "metadata": {"error_type": private_marker},
            },
        }

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response_payload)

        with patch.dict(
            os.environ,
            _environment(HS_AI_REVIEW_RETRY_ATTEMPTS="1"),
            clear=False,
        ):
            result = asyncio.run(_review_with_transport(handler))

        self.assertEqual(result.state, "error")
        self.assertEqual(result.error_type, "provider_unmapped")
        self.assertIsNone(result.provider)
        self.assertIsNone(result.router_strategy)
        serialized = repr(result) + json.dumps(result.telemetry(), sort_keys=True)
        self.assertNotIn(private_marker, serialized)

    def test_contradictory_verdict_normalizes_to_uncertain_without_opening_circuit(
        self,
    ) -> None:
        request_count = 0
        remote_summary = "REMOTE-SUMMARY-MUST-NOT-BE-RETAINED"

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                contradictory = {
                    **_verdict_content(verdict="pass", confidence=0.99),
                    "reason_codes": ["schema_mismatch"],
                    "summary": remote_summary,
                }
                return httpx.Response(
                    200,
                    json={
                        **_openrouter_response(),
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": json.dumps(contradictory)},
                            }
                        ],
                    },
                )
            return httpx.Response(200, json=_openrouter_response())

        async def scenario() -> tuple[AIReviewResult, AIReviewResult]:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                first = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 41}},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
                second = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 42}},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
            return first, second

        with patch.dict(
            os.environ,
            _environment(
                HS_AI_REVIEW_MODE="quarantine",
                HS_AI_REVIEW_CIRCUIT_FAILURE_THRESHOLD="1",
            ),
            clear=False,
        ):
            first, second = asyncio.run(scenario())

        self.assertEqual(first.state, "ok")
        self.assertEqual(first.verdict.verdict, "uncertain")  # type: ignore[union-attr]
        self.assertIn(  # type: ignore[union-attr]
            "deterministic_conflict",
            first.verdict.reason_codes,
        )
        self.assertFalse(first.should_quarantine)
        serialized = repr(first) + json.dumps(first.telemetry(), sort_keys=True)
        self.assertNotIn(remote_summary, serialized)
        self.assertEqual(second.state, "ok")
        self.assertEqual(request_count, 2)

    def test_all_cross_field_contradictions_are_non_actionable(self) -> None:
        cases = {
            "declared_pass_with_failed_check": {
                **_verdict_content(verdict="pass"),
                "target_page": False,
                "reason_codes": ["identity_mismatch"],
            },
            "declared_fail_with_positive_checks": {
                **_verdict_content(verdict="fail"),
                "content_complete": True,
                "parse_compatible": True,
                "reason_codes": ["schema_mismatch"],
            },
            "none_mixed_with_failure_reason": {
                **_verdict_content(verdict="pass"),
                "reason_codes": ["none", "schema_mismatch"],
            },
            "challenge_reason_without_challenge_signal": {
                **_verdict_content(verdict="fail"),
                "reason_codes": ["challenge_page"],
            },
            "schema_reason_with_only_completeness_failure": {
                **_verdict_content(verdict="fail"),
                "parse_compatible": True,
                "reason_codes": ["schema_mismatch"],
            },
            "actionable_reason_mixed_with_reserved_conflict": {
                **_verdict_content(verdict="fail"),
                "challenge_detected": True,
                "reason_codes": ["challenge_page", "deterministic_conflict"],
            },
            "actionable_reason_mixed_with_sparse_context": {
                **_verdict_content(verdict="fail"),
                "reason_codes": ["schema_mismatch", "low_sample_expected"],
            },
        }

        for case, payload in cases.items():
            with self.subTest(case=case):
                verdict = AIPageVerdict.model_validate(payload)
                self.assertEqual(verdict.verdict, "uncertain")
                self.assertIn("deterministic_conflict", verdict.reason_codes)

    def test_arbitrary_mapping_cannot_cross_prepared_evidence_boundary(self) -> None:
        prepared = build_ai_review_evidence_v2(
            TEST_SOURCE,
            {"structured": {"rows": 42}},
            backend="test-backend",
            stage="candidate_validation",
            deterministic_ok=True,
        )
        secret = "SECRET-IN-FORGED-PREPARED-EVIDENCE"
        forged = dict(prepared)
        forged["unexpected_secret"] = secret
        forged["evidence_hash"] = evidence_sha256(forged)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=_openrouter_response())

        async def scenario() -> AIReviewResult:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await review_candidate(
                    TEST_SOURCE,
                    {},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="prepared_evidence",
                    prepared_evidence=forged,  # type: ignore[arg-type]
                    client=client,
                )

        with patch.dict(os.environ, _environment(), clear=False):
            result = asyncio.run(scenario())

        self.assertEqual(result.state, "error")
        self.assertEqual(result.error_type, "invalid_prepared_evidence")
        self.assertEqual(requests, [])

    def test_nested_mutation_of_prepared_view_is_not_sent(self) -> None:
        prepared = build_ai_review_evidence_v2(
            TEST_SOURCE,
            {"structured": {"rows": 42}},
            backend="test-backend",
            stage="candidate_validation",
            deterministic_ok=True,
        )
        secret = "SECRET-IN-MUTATED-PREPARED-VIEW"
        prepared["source"]["unexpected_secret"] = secret
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=_openrouter_response())

        async def scenario() -> AIReviewResult:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return await review_candidate(
                    TEST_SOURCE,
                    {},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="prepared_evidence",
                    prepared_evidence=prepared,
                    client=client,
                )

        with patch.dict(os.environ, _environment(), clear=False):
            result = asyncio.run(scenario())

        self.assertEqual(result.state, "ok")
        self.assertEqual(len(requests), 1)
        self.assertNotIn(secret.encode(), requests[0].content)

    def test_high_confidence_failure_quarantines_only_in_quarantine_mode(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_openrouter_response(verdict="fail", confidence=0.97),
            )

        for mode, expected in (("observe", False), ("quarantine", True)):
            with self.subTest(mode=mode):
                reset_ai_review_budget()
                with patch.dict(
                    os.environ,
                    _environment(HS_AI_REVIEW_MODE=mode),
                    clear=False,
                ):
                    result = asyncio.run(_review_with_transport(handler))
                    self.assertEqual(result.state, "ok")
                    self.assertEqual(result.verdict.verdict, "fail")  # type: ignore[union-attr]
                    self.assertEqual(result.should_quarantine, expected)
                    self.assertEqual(result.telemetry()["quarantine"], expected)

    def test_non_actionable_reason_codes_can_never_create_fail_verdict(self) -> None:
        for reason_code in ("deterministic_conflict", "low_sample_expected"):
            with self.subTest(reason_code=reason_code):
                verdict = AIPageVerdict.model_validate(
                    {
                        "verdict": "fail",
                        "target_page": True,
                        "challenge_detected": False,
                        "content_complete": False,
                        "parse_compatible": False,
                        "confidence": 0.99,
                        "reason_codes": [reason_code],
                    }
                )

                self.assertEqual(verdict.verdict, "uncertain")
                self.assertIn("deterministic_conflict", verdict.reason_codes)

    def test_per_refresh_budget_limits_requests_until_explicit_reset(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json=_openrouter_response())

        async def scenario() -> list[object]:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                first = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 42}},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
                second = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 43}},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
                exhausted = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 44}},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
                reset_ai_review_budget()
                after_reset = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 45}},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
            return [first, second, exhausted, after_reset]

        with patch.dict(
            os.environ,
            _environment(HS_AI_REVIEW_MAX_PER_REFRESH="2"),
            clear=False,
        ):
            first, second, exhausted, after_reset = asyncio.run(scenario())

        self.assertEqual(first.state, "ok")
        self.assertEqual(second.state, "ok")
        self.assertEqual(exhausted.state, "skipped")
        self.assertEqual(exhausted.error_type, "refresh_budget_exhausted")
        self.assertEqual(after_reset.state, "ok")
        self.assertEqual(request_count, 3)

    def test_circuit_opens_after_consecutive_remote_failures(self) -> None:
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(502, text="provider unavailable")

        async def scenario() -> list[object]:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                return [
                    await review_candidate(
                        TEST_SOURCE,
                        {"structured": {"rows": row_count}},
                        backend="test-backend",
                        deterministic_ok=True,
                        deterministic_reason="ok",
                        client=client,
                    )
                    for row_count in (41, 42, 43)
                ]

        with patch.dict(
            os.environ,
            _environment(
                HS_AI_REVIEW_CIRCUIT_FAILURE_THRESHOLD="2",
                HS_AI_REVIEW_MAX_PER_REFRESH="10",
            ),
            clear=False,
        ):
            reset_ai_review_budget()
            first, second, circuit_open = asyncio.run(scenario())

        self.assertEqual(first.state, "error")
        self.assertEqual(second.state, "error")
        self.assertEqual(circuit_open.state, "skipped")
        self.assertEqual(circuit_open.error_type, "circuit_open")
        self.assertEqual(request_count, 2)

    def test_candidate_failures_do_not_open_failure_diagnosis_circuit(self) -> None:
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            body = json.loads(request.content)
            task = json.loads(body["messages"][1]["content"])["task"]
            if task == "diagnose_rejected_candidate":
                return httpx.Response(200, json=_diagnosis_response())
            return httpx.Response(502, text="provider unavailable")

        async def scenario() -> tuple[AIReviewResult, AIReviewResult, AIReviewResult]:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                candidate_one = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 41}},
                    backend="test",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
                candidate_two = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 42}},
                    backend="test",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
                diagnosis = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 1}},
                    backend="test",
                    deterministic_ok=False,
                    deterministic_reason="rejected",
                    review_kind="failure_diagnosis",
                    client=client,
                )
            return candidate_one, candidate_two, diagnosis

        with patch.dict(
            os.environ,
            _environment(HS_AI_REVIEW_CIRCUIT_FAILURE_THRESHOLD="2"),
            clear=False,
        ):
            first, second, diagnosis = asyncio.run(scenario())

        self.assertEqual(first.state, "error")
        self.assertEqual(second.state, "error")
        self.assertEqual(diagnosis.state, "ok")
        self.assertEqual(request_count, 3)

    def test_failure_diagnosis_has_capacity_independent_from_success_sampling(
        self,
    ) -> None:
        candidate_started = asyncio.Event()
        release_candidate = asyncio.Event()

        class PriorityClient:
            async def post(self, *_args, **kwargs) -> httpx.Response:
                request = kwargs["json"]
                task = json.loads(request["messages"][1]["content"])["task"]
                if task == "validate_parsed_page":
                    candidate_started.set()
                    await release_candidate.wait()
                    return httpx.Response(200, json=_openrouter_response())
                return httpx.Response(200, json=_diagnosis_response())

        async def scenario() -> tuple[AIReviewResult, AIReviewResult]:
            client = PriorityClient()
            candidate_task = asyncio.create_task(
                review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 42}},
                    backend="test",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,  # type: ignore[arg-type]
                )
            )
            await candidate_started.wait()
            diagnosis = await asyncio.wait_for(
                review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 1}},
                    backend="test",
                    deterministic_ok=False,
                    deterministic_reason="rejected",
                    review_kind="failure_diagnosis",
                    client=client,  # type: ignore[arg-type]
                ),
                timeout=0.5,
            )
            release_candidate.set()
            return await candidate_task, diagnosis

        with patch.dict(
            os.environ,
            _environment(HS_AI_REVIEW_MAX_CONCURRENCY="1"),
            clear=False,
        ):
            candidate, diagnosis = asyncio.run(scenario())

        self.assertEqual(candidate.state, "ok")
        self.assertEqual(diagnosis.state, "ok")
        self.assertEqual(diagnosis.review_kind, "failure_diagnosis")

    def test_local_queue_timeout_does_not_open_provider_circuit(self) -> None:
        import app.ai_review as ai_review_module

        async def scenario() -> tuple[AIReviewResult, AIReviewResult]:
            semaphore = ai_review_module._review_semaphore("failure_diagnosis")
            await semaphore.acquire()
            try:
                queued = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 1}},
                    backend="test",
                    deterministic_ok=False,
                    deterministic_reason="rejected",
                    review_kind="failure_diagnosis",
                    client=object(),  # type: ignore[arg-type]
                )
            finally:
                semaphore.release()

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, json=_diagnosis_response())
                )
            ) as client:
                recovered = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 1}},
                    backend="test",
                    deterministic_ok=False,
                    deterministic_reason="rejected",
                    review_kind="failure_diagnosis",
                    client=client,
                )
            return queued, recovered

        with (
            patch.dict(
                os.environ,
                _environment(
                    HS_AI_REVIEW_CIRCUIT_FAILURE_THRESHOLD="1",
                    HS_AI_REVIEW_MAX_CONCURRENCY="1",
                ),
                clear=False,
            ),
            patch("app.ai_review.ai_review_timeout_seconds", return_value=0.02),
        ):
            queued, recovered = asyncio.run(scenario())

        self.assertEqual(queued.state, "skipped")
        self.assertEqual(queued.error_type, "queue_timeout")
        self.assertEqual(recovered.state, "ok")

    def test_deep_nested_content_opens_circuit_without_escaping_parser(self) -> None:
        request_count = 0
        deeply_nested = "{}"
        for _ in range(1500):
            deeply_nested = '{"nested":' + deeply_nested + "}"

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            payload = {
                **_openrouter_response(),
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": deeply_nested},
                    }
                ],
            }
            return httpx.Response(200, json=payload)

        async def scenario() -> tuple[AIReviewResult, AIReviewResult]:
            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                first = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 41}},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
                second = await review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 42}},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,
                )
            return first, second

        with patch.dict(
            os.environ,
            _environment(HS_AI_REVIEW_CIRCUIT_FAILURE_THRESHOLD="1"),
            clear=False,
        ):
            reset_ai_review_budget()
            first, second = asyncio.run(scenario())

        self.assertEqual(first.state, "error")
        self.assertIn(
            first.error_type,
            {"invalid_response_content_json", "invalid_response_schema"},
        )
        self.assertEqual(second.state, "skipped")
        self.assertEqual(second.error_type, "circuit_open")
        self.assertEqual(request_count, 1)

    def test_circuit_stops_candidates_already_waiting_for_concurrency(self) -> None:
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        class QueuedClient:
            def __init__(self) -> None:
                self.request_count = 0

            async def post(self, *_args, **_kwargs) -> httpx.Response:
                self.request_count += 1
                request_started.set()
                await release_request.wait()
                return httpx.Response(502, text="provider unavailable")

        async def scenario() -> tuple[object, object, int]:
            client = QueuedClient()
            first_task = asyncio.create_task(
                review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 41}},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,  # type: ignore[arg-type]
                )
            )
            await request_started.wait()
            waiting_task = asyncio.create_task(
                review_candidate(
                    TEST_SOURCE,
                    {"structured": {"rows": 42}},
                    backend="test-backend",
                    deterministic_ok=True,
                    deterministic_reason="ok",
                    client=client,  # type: ignore[arg-type]
                )
            )
            # Let the second candidate reserve its budget and queue behind the
            # semaphore before the first failure opens the circuit.
            await asyncio.sleep(0)
            release_request.set()
            first, waiting = await asyncio.gather(first_task, waiting_task)
            return first, waiting, client.request_count

        with patch.dict(
            os.environ,
            _environment(
                HS_AI_REVIEW_CIRCUIT_FAILURE_THRESHOLD="1",
                HS_AI_REVIEW_MAX_CONCURRENCY="1",
            ),
            clear=False,
        ):
            reset_ai_review_budget()
            first, waiting, request_count = asyncio.run(scenario())

        self.assertEqual(first.state, "error")
        self.assertEqual(waiting.state, "skipped")
        self.assertEqual(waiting.error_type, "circuit_open")
        self.assertEqual(request_count, 1)


if __name__ == "__main__":
    unittest.main()
