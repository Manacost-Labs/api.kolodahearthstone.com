from __future__ import annotations

import asyncio
import json
import os
import unittest
from collections.abc import Callable
from unittest.mock import patch

import httpx

from app.ai_review import (
    AIPageVerdict,
    AIReviewResult,
    reset_ai_review_budget,
    review_candidate,
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


async def _review_with_transport(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    source: Source = TEST_SOURCE,
    parsed: dict[str, object] | None = None,
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
            client=client,
        )


class AIReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_ai_review_budget()

    def tearDown(self) -> None:
        reset_ai_review_budget()

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
        self.assertFalse(payload["response_format"]["json_schema"]["schema"]["additionalProperties"])
        self.assertNotIn(
            "summary",
            payload["response_format"]["json_schema"]["schema"]["properties"],
        )
        self.assertEqual(payload["provider"]["data_collection"], "deny")
        self.assertTrue(payload["provider"]["zdr"])
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertTrue(payload["provider"]["allow_fallbacks"])

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
                serialized = repr(result) + json.dumps(result.telemetry(), sort_keys=True)
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
        }

        for case, payload in cases.items():
            with self.subTest(case=case):
                verdict = AIPageVerdict.model_validate(payload)
                self.assertEqual(verdict.verdict, "uncertain")
                self.assertIn("deterministic_conflict", verdict.reason_codes)

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
