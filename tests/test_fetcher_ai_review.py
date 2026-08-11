from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.ai_review import AIPageVerdict, AIReviewResult
from app.fetcher import _fetch_source_with_active_lifecycle
from app.source_state import EFFECTIVE_OK_CACHED, SourceState
from app.sources import SOURCE_BY_ID, Source

TEST_SOURCE = SOURCE_BY_ID["firestone_battlegrounds_comps"]
FETCHED_AT = "2026-08-11T12:00:00+00:00"
RAW_PAGE_MARKER = "RAW-PAGE-CONTENT-MUST-NOT-REACH-TELEMETRY"
REVIEW_SUMMARY_MARKER = "PRIVATE-REVIEW-SUMMARY-MUST-NOT-REACH-TELEMETRY"


def _candidate(source: Source = TEST_SOURCE) -> dict[str, object]:
    return {
        "source_id": source.id,
        "site": source.site,
        "category": source.category,
        "structured": {"rows": 42, "winrate": 51.2},
        "counts": {"api_bytes": 128},
        "raw_html": f"<html>{RAW_PAGE_MARKER}</html>",
        "_backend": "firestone_api",
    }


def _serialized_log_calls(log_action: MagicMock) -> str:
    return json.dumps(
        [
            {"args": call.args, "kwargs": call.kwargs}
            for call in log_action.call_args_list
        ],
        ensure_ascii=False,
        default=str,
    )


async def _run_api_lifecycle(source: Source = TEST_SOURCE) -> dict[str, object]:
    return await _fetch_source_with_active_lifecycle(
        None,
        source,
        True,
        started=time.monotonic(),
        fetched_at=FETCHED_AT,
        publication_attempt=None,
        previous={},
        preferred_backend="patchright",
        source_tier="light_api",
        trace_id="ai-review-test-trace",
    )


class FetcherAIReviewIntegrationTest(unittest.TestCase):
    def test_reviewer_exception_and_error_result_fail_open_in_observe_mode(self) -> None:
        cases = {
            "exception": {
                "side_effect": RuntimeError(
                    f"reviewer unavailable: {REVIEW_SUMMARY_MARKER}"
                ),
                "expected_error_type": "internal_RuntimeError",
            },
            "error_result": {
                "return_value": AIReviewResult(
                    state="error",
                    model="test-model",
                    error_type="provider_http_502",
                ),
                "expected_error_type": "provider_http_502",
            },
        }

        for case, setup in cases.items():
            with self.subTest(case=case):
                reviewer = AsyncMock(
                    side_effect=setup.get("side_effect"),
                    return_value=setup.get("return_value"),
                )
                save_candidate = MagicMock(return_value=(False, None, {}))
                save_status = MagicMock()
                log_action = MagicMock()

                with (
                    patch.dict(
                        os.environ,
                        {"HS_AI_REVIEW_MODE": "observe"},
                        clear=False,
                    ),
                    patch(
                        "app.fetcher._fetch_hsreplay_api_source",
                        new=AsyncMock(return_value=_candidate()),
                    ),
                    patch(
                        "app.fetcher.validate_candidate_for_publish",
                        return_value=SimpleNamespace(
                            ok=True,
                            reason="deterministic validation passed",
                            extra={"rows_total": 42},
                        ),
                    ),
                    patch(
                        "app.fetcher.quality_metrics",
                        return_value={"quality_score": 0.99, "rows_total": 42},
                    ),
                    patch(
                        "app.fetcher._save_dataset_with_checks",
                        new=save_candidate,
                    ),
                    patch("app.fetcher.save_status", new=save_status),
                    patch("app.fetcher.log_action", new=log_action),
                    patch(
                        "app.fetcher.firecrawl_primary_source_ids",
                        return_value=set(),
                    ),
                    patch(
                        "app.fetcher.firecrawl_fallback_source_ids",
                        return_value=set(),
                    ),
                    patch("app.fetcher.fetch_proxy_url", return_value=""),
                    patch(
                        "app.fetcher.runtime_version_info",
                        return_value={"build_id": "test"},
                    ),
                    patch("app.fetcher.complete_source_trace"),
                    patch("app.ai_review.review_candidate", new=reviewer),
                ):
                    status = asyncio.run(_run_api_lifecycle())

                reviewer.assert_awaited_once()
                save_candidate.assert_called_once()
                save_status.assert_called_once()
                published_dataset = save_candidate.call_args.args[1]
                self.assertEqual(published_dataset["data"]["structured"]["rows"], 42)
                self.assertEqual(status["state"], SourceState.OK)
                self.assertEqual(status["ai_review"]["state"], "error")
                self.assertEqual(
                    status["ai_review"]["error_type"],
                    setup["expected_error_type"],
                )
                self.assertFalse(status["ai_review"]["quarantine"])

                status_text = json.dumps(status, ensure_ascii=False, default=str)
                log_text = _serialized_log_calls(log_action)
                for telemetry_text in (status_text, log_text):
                    self.assertNotIn(RAW_PAGE_MARKER, telemetry_text)
                    self.assertNotIn(REVIEW_SUMMARY_MARKER, telemetry_text)
                    self.assertNotIn('"summary"', telemetry_text)

    def test_quarantine_blocks_candidate_and_preserves_last_good_dataset(self) -> None:
        reviewer = AsyncMock(
            return_value=AIReviewResult(
                state="ok",
                model="test-model",
                verdict=AIPageVerdict(
                    verdict="fail",
                    target_page=False,
                    challenge_detected=False,
                    content_complete=False,
                    parse_compatible=False,
                    confidence=0.99,
                    reason_codes=["identity_mismatch", "schema_mismatch"],
                    summary=REVIEW_SUMMARY_MARKER,
                ),
            )
        )
        save_candidate = MagicMock(return_value=(False, None, {}))
        save_status = MagicMock()
        log_action = MagicMock()
        deterministic_gate = SimpleNamespace(
            ok=True,
            reason="deterministic validation passed",
            extra={"rows_total": 42},
        )
        existing_publication_gate = SimpleNamespace(
            ok=True,
            reason="existing publication validation passed",
            extra={"backend_policy_grandfathered": False},
        )
        last_good_dataset = {
            "fetched_at": "2026-08-10T12:00:00+00:00",
            "http_status": 200,
            "final_url": TEST_SOURCE.url,
            "content_length": 4096,
            "backend": "firestone_api",
            "data": {"structured": {"rows": 40, "winrate": 50.8}},
        }

        with (
            patch.dict(
                os.environ,
                {
                    "HS_AI_REVIEW_MODE": "quarantine",
                    "HS_AI_REVIEW_CONFIDENCE_THRESHOLD": "0.90",
                },
                clear=False,
            ),
            patch(
                "app.fetcher._fetch_hsreplay_api_source",
                new=AsyncMock(return_value=_candidate()),
            ),
            patch(
                "app.fetcher.validate_candidate_for_publish",
                return_value=deterministic_gate,
            ) as validate_candidate,
            patch(
                "app.fetcher.validate_existing_publication_for_serving",
                return_value=existing_publication_gate,
            ) as validate_existing_publication,
            patch(
                "app.fetcher.quality_metrics",
                return_value={"quality_score": 0.99, "rows_total": 42},
            ),
            patch(
                "app.fetcher._save_dataset_with_checks",
                new=save_candidate,
            ),
            patch(
                "app.parser_control.load_resolved_public_dataset",
                return_value=last_good_dataset,
            ),
            patch("app.fetcher.save_status", new=save_status),
            patch("app.fetcher.log_action", new=log_action),
            patch(
                "app.fetcher.firecrawl_primary_source_ids",
                return_value=set(),
            ),
            patch(
                "app.fetcher.firecrawl_fallback_source_ids",
                return_value=set(),
            ),
            patch("app.fetcher.fetch_proxy_url", return_value=""),
            patch(
                "app.fetcher.runtime_version_info",
                return_value={"build_id": "test"},
            ),
            patch("app.fetcher.complete_source_trace"),
            patch("app.fetcher.send_telegram_alert", new=AsyncMock()) as alert,
            patch("app.ai_review.review_candidate", new=reviewer),
        ):
            status = asyncio.run(_run_api_lifecycle())

        reviewer.assert_awaited_once()
        save_candidate.assert_not_called()
        validate_candidate.assert_called_once()
        validate_existing_publication.assert_called_once()
        save_status.assert_called_once()
        alert.assert_not_awaited()

        self.assertEqual(status["state"], SourceState.OK)
        self.assertTrue(status["serving_cached_dataset"])
        self.assertEqual(status["effective_state"], EFFECTIVE_OK_CACHED)
        self.assertEqual(status["last_refresh_state"], SourceState.QUALITY_ERROR)
        self.assertTrue(status["latest_ai_review"]["quarantine"])
        self.assertEqual(status["latest_ai_review"]["verdict"], "fail")
        self.assertEqual(
            status["latest_ai_review"]["reason_codes"],
            ["identity_mismatch", "schema_mismatch"],
        )

        status_text = json.dumps(status, ensure_ascii=False, default=str)
        saved_status_text = json.dumps(
            save_status.call_args.args[1],
            ensure_ascii=False,
            default=str,
        )
        log_text = _serialized_log_calls(log_action)
        for telemetry_text in (status_text, saved_status_text, log_text):
            self.assertNotIn(RAW_PAGE_MARKER, telemetry_text)
            self.assertNotIn(REVIEW_SUMMARY_MARKER, telemetry_text)
            self.assertNotIn('"summary"', telemetry_text)


if __name__ == "__main__":
    unittest.main()
