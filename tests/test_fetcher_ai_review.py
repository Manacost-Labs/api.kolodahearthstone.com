from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.ai_review import AIFailureDiagnosis, AIPageVerdict, AIReviewResult
from app.fetcher import (
    _begin_deferred_ai_collection,
    _diagnose_refresh_failure_with_ai,
    _fetch_source_with_active_lifecycle,
    _flush_deferred_ai_jobs,
    _regression_evidence_for_ai,
    _review_candidate_with_ai,
    _try_firecrawl_html,
)
from app.firecrawl_backend import FirecrawlScrape
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
    def test_disabled_or_unselected_ai_never_retains_deferred_payload(self) -> None:
        from app import fetcher

        async def scenario() -> None:
            for environment in (
                {"HS_AI_REVIEW_ENABLED": "false"},
                {
                    "HS_AI_REVIEW_ENABLED": "true",
                    "HS_AI_REVIEW_SOURCE_IDS": "different-source",
                },
            ):
                _begin_deferred_ai_collection()
                with patch.dict(os.environ, environment, clear=False):
                    await _review_candidate_with_ai(
                        TEST_SOURCE,
                        _candidate(),
                        backend="firestone_api",
                        deterministic_reason="ok",
                        deterministic_extra={"rows_total": 42},
                        quality={"quality_score": 0.99},
                    )
                self.assertEqual(fetcher._deferred_ai_jobs.get(), [])

        asyncio.run(scenario())

    def test_observe_review_runs_after_terminal_status_is_available(self) -> None:
        reviewer = AsyncMock(
            return_value=AIReviewResult(
                state="ok",
                model="test-model",
                verdict=AIPageVerdict(
                    verdict="pass",
                    target_page=True,
                    challenge_detected=False,
                    content_complete=True,
                    parse_compatible=True,
                    confidence=0.99,
                    reason_codes=["none"],
                ),
            )
        )
        status = {"source_id": TEST_SOURCE.id, "state": SourceState.OK}

        async def scenario() -> tuple[object, dict[str, object]]:
            _begin_deferred_ai_collection()
            immediate = await _review_candidate_with_ai(
                TEST_SOURCE,
                _candidate(),
                backend="firestone_api",
                deterministic_reason="ok",
                deterministic_extra={"rows_total": 42},
                quality={"quality_score": 0.99},
            )
            self.assertEqual(reviewer.await_count, 0)
            await _flush_deferred_ai_jobs("deferred-run", [status])
            return immediate, status

        with (
            patch.dict(
                os.environ,
                {
                    "HS_AI_REVIEW_ENABLED": "true",
                    "HS_AI_REVIEW_MODE": "observe",
                    "HS_AI_REVIEW_SOURCE_IDS": TEST_SOURCE.id,
                },
                clear=False,
            ),
            patch("app.ai_review.review_candidate", new=reviewer),
            patch("app.fetcher.save_status"),
            patch("app.fetcher._update_reliability_ai_best_effort") as update,
            patch("app.fetcher.log_action"),
        ):
            immediate, audited_status = asyncio.run(scenario())

        self.assertEqual(immediate, (None, False, None))
        reviewer.assert_awaited_once()
        self.assertEqual(audited_status["ai_review"]["verdict"], "pass")
        update.assert_called_once()

    def test_deferred_lane_finishes_retrying_job_before_starting_next_job(
        self,
    ) -> None:
        events: list[str] = []
        call_number = 0

        async def review_side_effect(*_args, **_kwargs) -> AIReviewResult:
            nonlocal call_number
            call_number += 1
            current = call_number
            events.append(f"start-{current}")
            await asyncio.sleep(0.01)
            events.append(f"end-{current}")
            return AIReviewResult(
                state="ok",
                model="test-model",
                verdict=AIPageVerdict(
                    verdict="pass",
                    target_page=True,
                    challenge_detected=False,
                    content_complete=True,
                    parse_compatible=True,
                    confidence=0.99,
                    reason_codes=["none"],
                ),
            )

        async def scenario() -> None:
            _begin_deferred_ai_collection()
            for rows in (41, 42):
                candidate = _candidate()
                candidate["structured"] = {"rows": rows}
                await _review_candidate_with_ai(
                    TEST_SOURCE,
                    candidate,
                    backend="firestone_api",
                    deterministic_reason="ok",
                    deterministic_extra={"rows_total": rows},
                    quality={"quality_score": 0.99},
                )
            await _flush_deferred_ai_jobs(
                "ordered-run",
                [{"source_id": TEST_SOURCE.id, "state": SourceState.OK}],
            )

        with (
            patch.dict(
                os.environ,
                {
                    "HS_AI_REVIEW_ENABLED": "true",
                    "HS_AI_REVIEW_MODE": "observe",
                    "HS_AI_REVIEW_SOURCE_IDS": TEST_SOURCE.id,
                    "HS_AI_REVIEW_CANDIDATE_MAX_CONCURRENCY": "1",
                },
                clear=False,
            ),
            patch(
                "app.ai_review.review_candidate",
                new=AsyncMock(side_effect=review_side_effect),
            ),
            patch("app.fetcher.save_status"),
            patch("app.fetcher._update_reliability_ai_best_effort"),
            patch("app.fetcher.log_action"),
        ):
            asyncio.run(scenario())

        self.assertEqual(events, ["start-1", "end-1", "start-2", "end-2"])

    def test_terminal_transport_failure_gets_neutral_fetch_stage_diagnosis(
        self,
    ) -> None:
        reviewer = AsyncMock(
            return_value=AIReviewResult(
                state="ok",
                model="test-model",
                review_kind="failure_diagnosis",
                diagnosis=AIFailureDiagnosis(
                    classification="anomalous",
                    failure_domain="protection",
                    evidence_codes=["deterministic_rejection"],
                    recommended_action="retry_existing_route",
                    confidence_band="medium",
                ),
            )
        )
        status = {
            "source_id": TEST_SOURCE.id,
            "state": SourceState.HTTP_ERROR,
            "http_status": 502,
            "backend": "scrape_do",
        }

        async def scenario() -> None:
            _begin_deferred_ai_collection()
            await _flush_deferred_ai_jobs("transport-run", [status])

        with (
            patch.dict(
                os.environ,
                {
                    "HS_AI_REVIEW_ENABLED": "true",
                    "HS_AI_REVIEW_DIAGNOSE_FAILURES": "true",
                },
                clear=False,
            ),
            patch("app.ai_review.review_candidate", new=reviewer),
            patch("app.fetcher.save_status"),
            patch("app.fetcher._update_reliability_ai_best_effort"),
            patch("app.fetcher.log_action"),
        ):
            asyncio.run(scenario())

        evidence = reviewer.await_args.kwargs["prepared_evidence"]
        self.assertEqual(evidence["stage"], "fetch")
        self.assertFalse(evidence["contract_validation"]["evaluated"])
        self.assertFalse(evidence["semantic_validation"]["evaluated"])
        self.assertEqual(
            evidence["deterministic_validation"]["issue_codes"],
            ["deterministic.http_5xx"],
        )
        self.assertEqual(status["ai_diagnosis"]["failure_domain"], "protection")

    def test_refresh_level_dependency_failure_uses_one_ai_call_for_all_sources(
        self,
    ) -> None:
        sources = [
            SOURCE_BY_ID["hsguru_meta_standard_legend"],
            SOURCE_BY_ID["hsreplay_battlegrounds_heroes"],
        ]
        reviewer = AsyncMock(
            return_value=AIReviewResult(
                state="ok",
                model="test-model",
                review_kind="failure_diagnosis",
                diagnosis=AIFailureDiagnosis(
                    classification="anomalous",
                    failure_domain="scope",
                    evidence_codes=["deterministic_rejection"],
                    recommended_action="inspect_upstream",
                    confidence_band="high",
                ),
            )
        )
        statuses = [
            {
                "source_id": source.id,
                "state": SourceState.FETCH_ERROR,
                "failure_reason_code": "dependency",
            }
            for source in sources
        ]

        async def scenario() -> None:
            _begin_deferred_ai_collection()
            await _diagnose_refresh_failure_with_ai(
                sources,
                phase="dependency",
                backend="hearthstonejson",
            )
            await _flush_deferred_ai_jobs(
                "dependency-run",
                statuses,
                enqueue_terminal_failures=False,
                persist_statuses=False,
            )

        with (
            patch.dict(
                os.environ,
                {
                    "HS_AI_REVIEW_ENABLED": "true",
                    "HS_AI_REVIEW_DIAGNOSE_FAILURES": "true",
                },
                clear=False,
            ),
            patch("app.ai_review.review_candidate", new=reviewer),
            patch("app.fetcher.save_status") as save_status,
            patch("app.fetcher._update_reliability_ai_best_effort") as update,
            patch("app.fetcher.log_action"),
        ):
            asyncio.run(scenario())

        reviewer.assert_awaited_once()
        evidence = reviewer.await_args.kwargs["prepared_evidence"]
        self.assertEqual(
            evidence["deterministic_validation"]["issue_codes"],
            ["deterministic.dependency"],
        )
        self.assertEqual(
            evidence["deterministic_validation"]["pipeline_numeric_metrics"][
                "affected_sources"
            ],
            2,
        )
        self.assertTrue(all("ai_diagnosis" in status for status in statuses))
        save_status.assert_not_called()
        update.assert_called_once()

    def test_rejected_firecrawl_fallback_owns_final_failure_diagnosis(self) -> None:
        source = SOURCE_BY_ID["hsguru_meta_standard_legend"]
        scraped = FirecrawlScrape(
            html="<html>rejected final fallback</html>",
            markdown="",
            screenshot=None,
            metadata={"backend": "scrape_do", "scrapeDoCreditsUsed": 1},
            status_code=200,
            final_url=source.url,
        )
        diagnosis = {
            "state": "ok",
            "classification": "anomalous",
            "failure_domain": "schema",
        }

        with (
            patch(
                "app.firecrawl_backend.scrape_source",
                new=AsyncMock(return_value=scraped),
            ),
            patch("app.fetcher.firecrawl_primary_source_ids", return_value=set()),
            patch(
                "app.fetcher.firecrawl_fallback_source_ids",
                return_value={source.id},
            ),
            patch(
                "app.fetcher.firecrawl_fallback_max_attempts_per_refresh",
                return_value=8,
            ),
            patch(
                "app.fetcher.firecrawl_fallback_max_attempts_per_source", return_value=2
            ),
            patch("app.fetcher.parse_html", return_value={"structured": {"rows": 1}}),
            patch(
                "app.fetcher.validate_candidate_for_publish",
                return_value=SimpleNamespace(
                    ok=False,
                    reason="final Firecrawl contract rejection",
                    extra={"rows_total": 1},
                ),
            ),
            patch("app.fetcher.quality_metrics", return_value={"rows_total": 1}),
            patch(
                "app.fetcher._diagnose_candidate_with_ai",
                new=AsyncMock(return_value=diagnosis),
            ) as diagnose,
            patch("app.fetcher._published_data_for_ai", return_value=None),
            patch(
                "app.fetcher._save_failure_status",
                side_effect=lambda _source, value: value,
            ),
            patch("app.fetcher.log_action"),
        ):
            status = asyncio.run(
                _try_firecrawl_html(
                    source,
                    fetched_at=FETCHED_AT,
                    reason="quality_error:origin",
                )
            )

        self.assertIsNotNone(status)
        self.assertEqual(status["backend"], "scrape_do")
        self.assertEqual(status["detail"], "final Firecrawl contract rejection")
        self.assertEqual(status["ai_diagnosis"], diagnosis)
        self.assertEqual(diagnose.await_args.kwargs["backend"], "scrape_do")
        self.assertEqual(
            diagnose.await_args.kwargs["deterministic_reason"],
            "final Firecrawl contract rejection",
        )

    def test_rejected_candidate_is_diagnosed_but_cannot_be_published(self) -> None:
        reviewer = AsyncMock(
            return_value=AIReviewResult(
                state="ok",
                model="test-model",
                review_kind="failure_diagnosis",
                diagnosis=AIFailureDiagnosis(
                    classification="healthy",
                    failure_domain="none",
                    evidence_codes=["insufficient_evidence"],
                    recommended_action="none",
                    confidence_band="low",
                ),
            )
        )
        save_candidate = MagicMock()

        with (
            patch.dict(
                os.environ,
                {
                    "HS_AI_REVIEW_ENABLED": "true",
                    "HS_AI_REVIEW_MODE": "observe",
                    "HS_AI_REVIEW_SOURCE_IDS": TEST_SOURCE.id,
                },
                clear=False,
            ),
            patch(
                "app.fetcher._fetch_hsreplay_api_source",
                new=AsyncMock(return_value=_candidate()),
            ),
            patch(
                "app.fetcher.validate_candidate_for_publish",
                return_value=SimpleNamespace(
                    ok=False,
                    reason="source contract failed: too few rows",
                    extra={"reason_code": "contract_failure", "rows_total": 1},
                ),
            ),
            patch(
                "app.fetcher.quality_metrics",
                return_value={"quality_score": 0.2, "rows_total": 1},
            ),
            patch("app.fetcher._save_dataset_with_checks", new=save_candidate),
            patch(
                "app.fetcher._save_failure_status",
                side_effect=lambda _source, status: status,
            ),
            patch("app.fetcher.log_action"),
            patch("app.fetcher.firecrawl_primary_source_ids", return_value=set()),
            patch("app.fetcher.firecrawl_fallback_source_ids", return_value=set()),
            patch("app.fetcher.fetch_proxy_url", return_value=""),
            patch(
                "app.fetcher.runtime_version_info", return_value={"build_id": "test"}
            ),
            patch("app.fetcher.complete_source_trace"),
            patch("app.fetcher.send_telegram_alert", new=AsyncMock()),
            patch(
                "app.parser_control.load_resolved_public_dataset",
                side_effect=OSError("corrupt publication"),
            ),
            patch("app.fetcher.load_dataset", side_effect=OSError("corrupt LKG")),
            patch("app.ai_review.review_candidate", new=reviewer),
        ):
            status = asyncio.run(_run_api_lifecycle())

        reviewer.assert_awaited_once()
        self.assertEqual(
            reviewer.await_args.kwargs["review_kind"],
            "failure_diagnosis",
        )
        self.assertEqual(
            reviewer.await_args.kwargs["stage"],
            "deterministic_rejection",
        )
        save_candidate.assert_not_called()
        self.assertEqual(status["state"], SourceState.QUALITY_ERROR)
        self.assertEqual(status["ai_diagnosis"]["classification"], "healthy")
        self.assertNotIn("serving_cached_dataset", status)

    def test_regression_remains_blocked_and_gets_anomaly_diagnosis(self) -> None:
        async def review_side_effect(*_args, **kwargs):
            if kwargs.get("review_kind") == "failure_diagnosis":
                return AIReviewResult(
                    state="ok",
                    model="test-model",
                    review_kind="failure_diagnosis",
                    diagnosis=AIFailureDiagnosis(
                        classification="anomalous",
                        failure_domain="regression",
                        evidence_codes=["regression_count_drop"],
                        recommended_action="preserve_lkg",
                        confidence_band="high",
                    ),
                )
            return AIReviewResult(
                state="ok",
                model="test-model",
                verdict=AIPageVerdict(
                    verdict="pass",
                    target_page=True,
                    challenge_detected=False,
                    content_complete=True,
                    parse_compatible=True,
                    confidence=0.99,
                    reason_codes=["none"],
                ),
            )

        reviewer = AsyncMock(side_effect=review_side_effect)
        save_candidate = MagicMock(
            return_value=(True, "Dataset regression: metric count dropped", {})
        )
        save_status = MagicMock()

        with (
            patch.dict(
                os.environ,
                {
                    "HS_AI_REVIEW_ENABLED": "true",
                    "HS_AI_REVIEW_MODE": "observe",
                    "HS_AI_REVIEW_SOURCE_IDS": TEST_SOURCE.id,
                },
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
            patch("app.fetcher._save_dataset_with_checks", new=save_candidate),
            patch(
                "app.fetcher._regression_evidence_for_ai",
                return_value=(
                    {
                        "detected": True,
                        "reason_code": "row_count_drop",
                        "extra": {"rows_before": 80, "rows_after": 42},
                    },
                    {"structured": {"rows": 80}},
                ),
            ) as regression_evidence,
            patch(
                "app.fetcher._save_failure_status",
                side_effect=lambda _source, status: status,
            ),
            patch("app.fetcher.save_status", new=save_status),
            patch("app.fetcher.log_action"),
            patch("app.fetcher.firecrawl_primary_source_ids", return_value=set()),
            patch("app.fetcher.firecrawl_fallback_source_ids", return_value=set()),
            patch("app.fetcher.fetch_proxy_url", return_value=""),
            patch(
                "app.fetcher.runtime_version_info", return_value={"build_id": "test"}
            ),
            patch("app.fetcher.complete_source_trace"),
            patch("app.fetcher.send_telegram_alert", new=AsyncMock()),
            patch("app.ai_review.review_candidate", new=reviewer),
        ):
            status = asyncio.run(_run_api_lifecycle())

        self.assertEqual(reviewer.await_count, 1)
        self.assertEqual(
            reviewer.await_args.kwargs["review_kind"],
            "failure_diagnosis",
        )
        self.assertEqual(
            reviewer.await_args.kwargs["stage"],
            "regression_rejection",
        )
        save_candidate.assert_called_once()
        regression_evidence.assert_called_once()
        self.assertEqual(regression_evidence.call_args.args[0], TEST_SOURCE)
        self.assertEqual(
            regression_evidence.call_args.kwargs["authoritative_reason"],
            "Dataset regression: metric count dropped",
        )
        save_status.assert_not_called()
        self.assertEqual(status["state"], SourceState.PARTIAL)
        self.assertNotIn("ai_review", status)
        self.assertEqual(status["ai_diagnosis"]["failure_domain"], "regression")

    def test_authoritative_policy_rejection_is_not_reclassified_as_no_regression(
        self,
    ) -> None:
        with (
            patch("app.fetcher._published_data_for_ai", return_value=None),
            patch(
                "app.fetcher.check_dataset_regression",
                return_value=(False, None, {}),
            ),
        ):
            evidence, lkg = _regression_evidence_for_ai(
                TEST_SOURCE,
                _candidate(),
                authoritative_reason=(
                    "Publication policy changed after early validation; "
                    "candidate was not saved"
                ),
            )

        self.assertIsNone(lkg)
        self.assertTrue(evidence["detected"])
        self.assertEqual(evidence["reason_code"], "policy_changed")

    def test_reviewer_exception_and_error_result_fail_open_in_observe_mode(
        self,
    ) -> None:
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
                        {
                            "HS_AI_REVIEW_ENABLED": "true",
                            "HS_AI_REVIEW_MODE": "observe",
                            "HS_AI_REVIEW_SOURCE_IDS": TEST_SOURCE.id,
                        },
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
                    "HS_AI_REVIEW_ENABLED": "true",
                    "HS_AI_REVIEW_MODE": "quarantine",
                    "HS_AI_REVIEW_SOURCE_IDS": TEST_SOURCE.id,
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
