from __future__ import annotations

import os
import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from app.parser_control import (
    InvalidControlRequest,
    ParserControlStore,
    ParserRunWorker,
    RevisionConflict,
    _run_pipeline_source,
    effective_publication_mode,
    enabled_section_ids,
    execute_parser_run,
    filter_scheduled_source_ids,
)
from app.parser_control_registry import (
    EARLY_SOURCE_IDS,
    SECTION_BY_ID,
    SOURCE_TO_SECTION,
)
from app.post_patch_policy import (
    EARLY_SOURCE_IDS as POLICY_EARLY_SOURCE_IDS,
)
from app.post_patch_policy import (
    STABLE_PUBLICATION_BASELINE_LABEL,
)
from app.sources import SOURCE_BY_ID


class ParserControlRegistryTest(unittest.TestCase):
    def test_every_configured_source_belongs_to_exactly_one_section(self) -> None:
        self.assertEqual(set(SOURCE_BY_ID), set(SOURCE_TO_SECTION))
        self.assertTrue(all(section_id in SECTION_BY_ID for section_id in SOURCE_TO_SECTION.values()))

    def test_early_mode_is_only_advertised_for_implemented_sources(self) -> None:
        self.assertEqual(EARLY_SOURCE_IDS, POLICY_EARLY_SOURCE_IDS)
        self.assertIn("hsguru_meta_standard_legend", EARLY_SOURCE_IDS)
        self.assertIn("hsguru_matchups_legend", EARLY_SOURCE_IDS)
        self.assertIn("hsreplay_cards_legend_patch", EARLY_SOURCE_IDS)
        self.assertIn("firestone_standard", EARLY_SOURCE_IDS)
        self.assertNotIn("hsreplay_cards_legend_1d", EARLY_SOURCE_IDS)


class ParserControlStoreTest(unittest.TestCase):
    def test_worker_persists_exact_parsesunix_paid_usage(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            run, _ = store.enqueue_run(
                source_ids=["hsguru_meta_standard_legend"],
                requested_by="convergence-controller",
                reason="recover transport",
                attempt_purpose="recovery",
                origin_occurrence_id="schedule:20260820T100000Z",
                recovery_chain_id="chain-paid-usage",
            )

            async def executor(_source_ids: list[str]) -> list[dict[str, object]]:
                return [
                    {
                        "source_id": "hsguru_meta_standard_legend",
                        "state": "ok",
                        "parsesunix_transport": {
                            "verdict": "OK",
                            "paid_requests": 1,
                            "paid_cost_usd": "0.000290",
                            "cost_certainty": "exact",
                        },
                    }
                ]

            self.assertTrue(ParserRunWorker(store, executor=executor).process_next())
            persisted = store.get_run(run["id"])
            self.assertIsNotNone(persisted)
            assert persisted is not None
            result = persisted["results"][0]
            self.assertEqual(result["paidRequests"], 1)
            self.assertEqual(result["paidCostMicrousd"], 290)
            self.assertTrue(result["paidUsageExact"])

    def test_worker_never_reports_unknown_paid_cost_as_zero(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            run, _ = store.enqueue_run(
                source_ids=["hsguru_meta_standard_legend"],
                requested_by="convergence-controller",
                reason="recover transport",
                attempt_purpose="recovery",
                origin_occurrence_id="schedule:20260820T100000Z",
                recovery_chain_id="chain-unknown-cost",
            )

            async def executor(_source_ids: list[str]) -> list[dict[str, object]]:
                return [
                    {
                        "source_id": "hsguru_meta_standard_legend",
                        "state": "ok",
                        "parsesunix_transport": {
                            "verdict": "OK",
                            "paid_requests": 1,
                            "paid_cost_usd": "0.000290",
                            "cost_certainty": "unknown",
                        },
                    }
                ]

            self.assertTrue(ParserRunWorker(store, executor=executor).process_next())
            persisted = store.get_run(run["id"])
            self.assertIsNotNone(persisted)
            assert persisted is not None
            result = persisted["results"][0]
            self.assertEqual(result["paidRequests"], 1)
            self.assertNotIn("paidCostMicrousd", result)
            self.assertFalse(result["paidUsageExact"])

    def test_recovery_run_requires_stable_correlation_and_deduplicates_it(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            kwargs = {
                "source_ids": ["hsguru_meta_standard_legend"],
                "requested_by": "convergence-controller",
                "reason": "recover provisional",
                "request_id": "convergence:chain-1:attempt-1",
                "attempt_purpose": "recovery",
                "origin_occurrence_id": "schedule:20260820T100000Z",
                "recovery_chain_id": "chain-1",
            }

            created, deduplicated = store.enqueue_run(**kwargs)
            repeated, repeated_deduplicated = store.enqueue_run(**kwargs)

            self.assertFalse(deduplicated)
            self.assertTrue(repeated_deduplicated)
            self.assertEqual(repeated["id"], created["id"])
            self.assertEqual(created["attemptPurpose"], "recovery")
            self.assertEqual(
                created["originOccurrenceId"],
                kwargs["origin_occurrence_id"],
            )
            self.assertEqual(created["recoveryChainId"], "chain-1")
            with self.assertRaisesRegex(
                InvalidControlRequest,
                "selection or attempt context",
            ):
                store.enqueue_run(
                    **{
                        **kwargs,
                        "recovery_chain_id": "chain-2",
                    }
                )

    def test_recovery_run_without_chain_fails_closed(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))

            with self.assertRaisesRegex(
                InvalidControlRequest,
                "require a recovery chain ID",
            ):
                store.enqueue_run(
                    source_ids=["hsguru_meta_standard_legend"],
                    requested_by="convergence-controller",
                    reason="recover provisional",
                    attempt_purpose="recovery",
                )

    def test_worker_records_recovery_without_rewriting_origin_window(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ParserControlStore(root)
            run, _ = store.enqueue_run(
                source_ids=["hsguru_meta_standard_legend"],
                requested_by="convergence-controller",
                reason="recover provisional",
                request_id="convergence:chain-1:attempt-1",
                attempt_purpose="recovery",
                origin_occurrence_id="schedule:20260820T100000Z",
                recovery_chain_id="chain-1",
            )

            async def executor(_source_ids: list[str]) -> list[dict[str, object]]:
                return [
                    {
                        "source_id": "hsguru_meta_standard_legend",
                        "state": "ok",
                    }
                ]

            worker = ParserRunWorker(store, executor=executor)
            self.assertTrue(worker.process_next())

            with sqlite3.connect(root / "parser-telemetry.sqlite3") as connection:
                stored = connection.execute(
                    """
                    SELECT
                        refresh_window_id,
                        attempt_purpose,
                        origin_occurrence_id,
                        recovery_chain_id,
                        outcome
                    FROM source_attempts
                    """
                ).fetchone()

            self.assertEqual(
                stored,
                (
                    f"recovery:chain-1:{run['id']}",
                    "recovery",
                    "schedule:20260820T100000Z",
                    "chain-1",
                    "fresh_published",
                ),
            )

    def test_worker_does_not_count_provisional_as_fresh(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ParserControlStore(root)
            run, _ = store.enqueue_run(
                source_ids=["hsguru_meta_standard_legend"],
                requested_by="convergence-controller",
                reason="recover candidate",
                attempt_purpose="recovery",
                origin_occurrence_id="schedule:20260820T100000Z",
                recovery_chain_id="chain-provisional",
            )

            async def executor(_source_ids: list[str]) -> list[dict[str, object]]:
                return [
                    {
                        "source_id": "hsguru_meta_standard_legend",
                        "state": "ok",
                        "provisional": True,
                    }
                ]

            self.assertTrue(ParserRunWorker(store, executor=executor).process_next())
            persisted = store.get_run(run["id"])
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertEqual(persisted["status"], "partial")
            self.assertEqual(
                persisted["results"][0]["terminalOutcome"],
                "provisional",
            )
            with sqlite3.connect(root / "parser-telemetry.sqlite3") as connection:
                outcome = connection.execute(
                    "SELECT outcome FROM source_attempts"
                ).fetchone()
            self.assertEqual(outcome, ("provisional",))

    def test_worker_does_not_count_lkg_as_fresh(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ParserControlStore(root)
            run, _ = store.enqueue_run(
                source_ids=["hsguru_meta_standard_legend"],
                requested_by="convergence-controller",
                reason="recover transport",
                attempt_purpose="recovery",
                origin_occurrence_id="schedule:20260820T100000Z",
                recovery_chain_id="chain-lkg",
            )

            async def executor(_source_ids: list[str]) -> list[dict[str, object]]:
                return [
                    {
                        "source_id": "hsguru_meta_standard_legend",
                        "state": "fetch_error",
                        "serving_cached_dataset": True,
                        "failure_reason_code": "transport",
                    }
                ]

            self.assertTrue(ParserRunWorker(store, executor=executor).process_next())
            persisted = store.get_run(run["id"])
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertEqual(persisted["status"], "partial")
            self.assertEqual(persisted["results"][0]["terminalOutcome"], "lkg_served")
            with sqlite3.connect(root / "parser-telemetry.sqlite3") as connection:
                stored = connection.execute(
                    "SELECT outcome, reason_code FROM source_attempts"
                ).fetchone()
            self.assertEqual(stored, ("lkg_served", "transport"))

    def test_policy_update_is_persisted_and_uses_optimistic_revision(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            until = datetime.now(UTC) + timedelta(days=2)

            updated = store.update_policy(
                expected_revision=1,
                mode="early",
                early_until=until.isoformat(),
                reason="Балансный патч",
                updated_by="admin:7",
            )

            self.assertEqual(updated["revision"], 2)
            self.assertEqual(updated["policy"]["mode"], "early")
            self.assertEqual(ParserControlStore(Path(directory)).snapshot()["revision"], 2)
            with self.assertRaises(RevisionConflict):
                store.update_policy(
                    expected_revision=1,
                    mode="stable",
                    early_until=None,
                    reason="",
                    updated_by="admin:8",
                )

    def test_section_update_filters_only_scheduled_runs(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            updated = store.update_sections(
                expected_revision=1,
                changes={"arena-tier-list": False},
                updated_by="admin:7",
            )

            selected = [
                "hsreplay_arena_cards_advanced",
                "hsguru_meta_standard_legend",
            ]
            filtered = filter_scheduled_source_ids(selected, store=store)

            self.assertEqual(updated["revision"], 2)
            self.assertEqual(filtered, ["hsguru_meta_standard_legend"])
            # A manual run uses the original allow-listed selection and is not filtered.
            self.assertEqual(selected, [
                "hsreplay_arena_cards_advanced",
                "hsguru_meta_standard_legend",
            ])

    def test_expired_early_policy_falls_back_to_stable(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            now = datetime.now(UTC)
            store.update_policy(
                expected_revision=1,
                mode="early",
                early_until=(now + timedelta(hours=1)).isoformat(),
                reason="Первые данные",
                updated_by="admin:7",
            )

            self.assertEqual(
                effective_publication_mode(
                    "hsreplay_arena_cards_advanced", at=now, store=store
                ),
                "early",
            )
            self.assertEqual(
                effective_publication_mode(
                    "hsreplay_arena_cards_advanced",
                    at=now + timedelta(hours=2),
                    store=store,
                ),
                "stable",
            )
            self.assertEqual(
                effective_publication_mode("hsreplay_cards_legend_1d", at=now, store=store),
                "stable",
            )

    def test_persisted_stable_mode_overrides_early_environment_fallback(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_ARENA_POST_PATCH_ENABLED": "true",
                "HS_ARENA_POST_PATCH_FROM": "2026-07-21",
                "HS_ARENA_POST_PATCH_UNTIL": "2026-07-28",
            },
            clear=False,
        ):
            store = ParserControlStore(Path(directory))
            store.update_policy(
                expected_revision=1,
                mode="stable",
                early_until=None,
                reason="Достаточная выборка",
                updated_by="admin:7",
            )

            self.assertEqual(
                effective_publication_mode(
                    "hsreplay_arena_cards_advanced",
                    at=datetime(2026, 7, 23, 12, tzinfo=UTC),
                    store=store,
                ),
                "stable",
            )

    def test_section_edits_and_run_enqueue_preserve_environment_policy_provenance(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_ARENA_POST_PATCH_ENABLED": "true",
                "HS_ARENA_POST_PATCH_FROM": "2026-07-21",
                "HS_ARENA_POST_PATCH_UNTIL": "2026-07-28",
            },
            clear=False,
        ):
            store = ParserControlStore(Path(directory))
            section_snapshot = store.update_sections(
                expected_revision=1,
                changes={"traditional-wild-meta": False},
                updated_by="admin:7",
            )
            store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="admin:7",
                reason="Проверка",
            )

            policy = store.snapshot(
                at=datetime(2026, 7, 23, 12, tzinfo=UTC)
            )["policy"]

            self.assertFalse(section_snapshot["policyConfigured"])
            self.assertFalse(policy["policyConfigured"])
            self.assertEqual(policy["managedBy"], "environment")
            self.assertEqual(policy["effectiveMode"], "early")
            self.assertEqual(
                effective_publication_mode(
                    "hsreplay_arena_cards_advanced",
                    at=datetime(2026, 7, 23, 12, tzinfo=UTC),
                    store=store,
                ),
                "early",
            )

    def test_run_queue_is_persisted_and_deduplicates_equal_active_selection(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            first, first_deduplicated = store.enqueue_run(
                source_ids=["heartharena_tierlist", "hsreplay_arena_cards_advanced"],
                requested_by="admin:7",
                reason="После патча",
            )
            second, second_deduplicated = ParserControlStore(Path(directory)).enqueue_run(
                source_ids=["hsreplay_arena_cards_advanced", "heartharena_tierlist"],
                requested_by="admin:7",
                reason="Повтор",
            )

            self.assertFalse(first_deduplicated)
            self.assertTrue(second_deduplicated)
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(first["totalSources"], 2)
            self.assertEqual(first["completedSources"], 0)
            self.assertEqual(first["failedSources"], 0)
            self.assertEqual(store.list_runs()["activeRun"]["status"], "queued")

    def test_orchestrator_request_id_is_idempotent_after_terminal_run(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            first, first_deduplicated = store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="trigger.dev",
                reason="scheduled canary",
                request_id="trigger:run_123:attempt:1",
            )
            store.finish_run(first["id"], status="failed", results=[])

            repeated, repeated_deduplicated = store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="trigger.dev",
                reason="retry after lost response",
                request_id="trigger:run_123:attempt:1",
            )

            self.assertFalse(first_deduplicated)
            self.assertTrue(repeated_deduplicated)
            self.assertEqual(repeated["id"], first["id"])
            self.assertEqual(repeated["status"], "failed")
            self.assertEqual(repeated["requestId"], "trigger:run_123:attempt:1")

    def test_orchestrator_request_id_survives_full_active_run_deduplication(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            covering, _ = store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="systemd",
                reason="existing schedule",
            )
            deduplicated, was_deduplicated = store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="trigger.dev",
                reason="trigger canary",
                request_id="trigger:run_dedup:attempt:1",
            )
            store.finish_run(
                covering["id"],
                status="succeeded",
                results=[{"sourceId": "heartharena_tierlist", "state": "ok"}],
            )

            repeated, repeated_deduplicated = ParserControlStore(
                Path(directory)
            ).enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="trigger.dev",
                reason="retry after lost response",
                request_id="trigger:run_dedup:attempt:1",
            )

            self.assertTrue(was_deduplicated)
            self.assertEqual(deduplicated["id"], covering["id"])
            self.assertTrue(repeated_deduplicated)
            self.assertEqual(repeated["id"], covering["id"])
            self.assertEqual(repeated["status"], "succeeded")

    def test_orchestrator_partial_overlap_aggregates_dependency_failure(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            dependency, _ = store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="systemd",
                reason="already active",
            )
            aggregate, deduplicated = store.enqueue_run(
                source_ids=[
                    "heartharena_tierlist",
                    "hsreplay_arena_cards_advanced",
                ],
                requested_by="trigger.dev",
                reason="overlapping canary",
                request_id="trigger:run_overlap:attempt:1",
            )
            store.finish_run(
                dependency["id"],
                status="failed",
                results=[
                    {
                        "sourceId": "heartharena_tierlist",
                        "state": "fetch_error",
                        "servingCachedDataset": False,
                    }
                ],
            )

            async def executor(source_ids: list[str]) -> list[dict[str, object]]:
                self.assertEqual(source_ids, ["hsreplay_arena_cards_advanced"])
                return [
                    {
                        "source_id": "hsreplay_arena_cards_advanced",
                        "state": "ok",
                    }
                ]

            worker = ParserRunWorker(store, executor=executor)
            self.assertTrue(worker.process_next())
            finished = store.get_run(aggregate["id"])

            self.assertTrue(deduplicated)
            self.assertIsNotNone(finished)
            assert finished is not None
            self.assertEqual(finished["status"], "partial")
            self.assertEqual(finished["totalSources"], 2)
            self.assertEqual(
                {result["sourceId"]: result["state"] for result in finished["results"]},
                {
                    "heartharena_tierlist": "fetch_error",
                    "hsreplay_arena_cards_advanced": "ok",
                },
            )

    def test_orchestrator_collective_coverage_aggregates_all_active_runs(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            first, _ = store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="systemd",
                reason="first active",
            )
            second, _ = store.enqueue_run(
                source_ids=["hsreplay_arena_cards_advanced"],
                requested_by="systemd",
                reason="second active",
            )
            aggregate, deduplicated = store.enqueue_run(
                source_ids=[
                    "heartharena_tierlist",
                    "hsreplay_arena_cards_advanced",
                ],
                requested_by="trigger.dev",
                reason="collective coverage",
                request_id="trigger:run_collective:attempt:1",
            )
            store.finish_run(
                first["id"],
                status="succeeded",
                results=[{"sourceId": "heartharena_tierlist", "state": "ok"}],
            )
            store.finish_run(
                second["id"],
                status="failed",
                results=[
                    {
                        "sourceId": "hsreplay_arena_cards_advanced",
                        "state": "fetch_error",
                    }
                ],
            )

            executor = AsyncMock(return_value=[])
            worker = ParserRunWorker(store, executor=executor)
            self.assertTrue(worker.process_next())
            finished = store.get_run(aggregate["id"])

            self.assertTrue(deduplicated)
            executor.assert_not_called()
            self.assertIsNotNone(finished)
            assert finished is not None
            self.assertEqual(finished["status"], "partial")
            self.assertEqual(finished["failedSources"], 1)

    def test_orchestrator_subset_ignores_unrequested_covering_failure(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            covering, _ = store.enqueue_run(
                source_ids=[
                    "heartharena_tierlist",
                    "hsreplay_arena_cards_advanced",
                ],
                requested_by="systemd",
                reason="covering superset",
            )
            aggregate, deduplicated = store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="trigger.dev",
                reason="requested subset",
                request_id="trigger:run_subset:attempt:1",
            )
            store.finish_run(
                covering["id"],
                status="partial",
                results=[
                    {"sourceId": "heartharena_tierlist", "state": "ok"},
                    {
                        "sourceId": "hsreplay_arena_cards_advanced",
                        "state": "fetch_error",
                    },
                ],
            )

            executor = AsyncMock(return_value=[])
            worker = ParserRunWorker(store, executor=executor)
            self.assertTrue(worker.process_next())
            finished = store.get_run(aggregate["id"])

            self.assertTrue(deduplicated)
            executor.assert_not_called()
            self.assertIsNotNone(finished)
            assert finished is not None
            self.assertEqual(finished["status"], "succeeded")
            self.assertEqual(finished["sourceIds"], ["heartharena_tierlist"])
            self.assertEqual(finished["failedSources"], 0)

    def test_run_queue_deduplicates_sources_already_covered_by_other_active_runs(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            first, _ = store.enqueue_run(
                source_ids=["heartharena_tierlist", "hsreplay_arena_cards_advanced"],
                requested_by="admin:7",
                reason="Первый запуск",
            )
            second, deduplicated = store.enqueue_run(
                source_ids=["hsreplay_arena_cards_advanced", "hsguru_meta_standard_legend"],
                requested_by="admin:7",
                reason="Пересекающийся запуск",
            )

            self.assertTrue(deduplicated)
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(second["sourceIds"], ["hsguru_meta_standard_legend"])
            self.assertEqual(
                second["requestedSourceIds"],
                ["hsguru_meta_standard_legend", "hsreplay_arena_cards_advanced"],
            )
            self.assertEqual(
                second["deduplicatedSourceIds"],
                ["hsreplay_arena_cards_advanced"],
            )

    def test_worker_persists_each_result_and_recovery_skips_completed_sources(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            run, _ = store.enqueue_run(
                source_ids=["heartharena_tierlist", "hsreplay_arena_cards_advanced"],
                requested_by="admin:7",
                reason="Проверка восстановления",
            )
            store.claim_next_run()
            store.record_run_result(
                run["id"],
                {"sourceId": "heartharena_tierlist", "state": "ok"},
            )
            store.recover_interrupted_runs()
            calls: list[list[str]] = []

            async def executor(source_ids: list[str]) -> list[dict[str, object]]:
                calls.append(source_ids)
                return [{"source_id": source_ids[0], "state": "ok"}]

            worker = ParserRunWorker(store, executor=executor)
            self.assertTrue(worker.process_next())

            finished = store.list_runs()["runs"][0]
            self.assertEqual(calls, [["hsreplay_arena_cards_advanced"]])
            self.assertEqual(finished["status"], "succeeded")
            self.assertEqual(finished["completedSources"], 2)
            self.assertEqual(
                {row["sourceId"] for row in finished["results"]},
                {"heartharena_tierlist", "hsreplay_arena_cards_advanced"},
            )

    def test_worker_records_duration_and_errors_when_executor_raises(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="admin:7",
                reason="Проверка ошибки",
            )

            async def executor(_source_ids: list[str]) -> list[dict[str, object]]:
                raise RuntimeError("origin exploded")

            worker = ParserRunWorker(store, executor=executor)
            with patch(
                "app.parser_control._monotonic_ms", side_effect=[2_000.0, 2_075.2]
            ):
                self.assertTrue(worker.process_next())

            finished = store.list_runs()["runs"][0]
            result = finished["results"][0]
            self.assertEqual(finished["status"], "failed")
            self.assertEqual(result["durationMs"], 75)
            self.assertEqual(result["errors"], ["RuntimeError: origin exploded"])
            self.assertEqual(result["errorsTotal"], 1)
            self.assertFalse(result["errorsTruncated"])
            self.assertEqual(result["detail"], "RuntimeError: origin exploded")
            self.assertNotIn("rowsTotal", result)

    def test_terminal_run_counts_missing_results_as_failed(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            run, _ = store.enqueue_run(
                source_ids=["heartharena_tierlist", "hsreplay_arena_cards_advanced"],
                requested_by="admin:7",
                reason="Проверка",
            )
            store.claim_next_run()
            store.finish_run(
                run["id"],
                status="partial",
                results=[{"sourceId": "heartharena_tierlist", "state": "ok"}],
            )

            finished = store.list_runs()["runs"][0]

            self.assertEqual(finished["totalSources"], 2)
            self.assertEqual(finished["completedSources"], 2)
            self.assertEqual(finished["failedSources"], 1)

    def test_worker_marks_all_cached_results_as_usable_partial(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="trigger.dev",
                reason="cached fallback",
            )

            async def executor(_source_ids: list[str]) -> list[dict[str, object]]:
                return [
                    {
                        "source_id": "heartharena_tierlist",
                        "state": "fetch_error",
                        "serving_cached_dataset": True,
                        "detail": "origin unavailable",
                    }
                ]

            worker = ParserRunWorker(store, executor=executor)
            self.assertTrue(worker.process_next())

            finished = store.list_runs()["runs"][0]
            self.assertEqual(finished["status"], "partial")
            self.assertEqual(finished["failedSources"], 1)

    def test_worker_marks_publishable_partial_result_as_degraded(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="trigger.dev",
                reason="publishable partial",
            )

            async def executor(_source_ids: list[str]) -> list[dict[str, object]]:
                return [
                    {
                        "source_id": "heartharena_tierlist",
                        "state": "partial",
                        "serving_cached_dataset": False,
                        "rows_total": 14,
                    }
                ]

            worker = ParserRunWorker(store, executor=executor)
            self.assertTrue(worker.process_next())

            finished = store.list_runs()["runs"][0]
            self.assertEqual(finished["status"], "partial")
            self.assertEqual(finished["failedSources"], 1)

    def test_corrupted_control_file_fails_open_for_scheduled_sections_and_logs(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            store.state_path.parent.mkdir(parents=True, exist_ok=True)
            store.state_path.write_text("{broken", encoding="utf-8")

            with patch("app.refresh_log.log_action") as log_action:
                enabled = enabled_section_ids(store=store)
                filtered = filter_scheduled_source_ids(
                    ["heartharena_tierlist", "hsguru_meta_standard_legend"],
                    store=store,
                )

            self.assertEqual(enabled, set(SECTION_BY_ID))
            self.assertEqual(
                filtered,
                ["heartharena_tierlist", "hsguru_meta_standard_legend"],
            )
            self.assertTrue(
                any(
                    call.args and call.args[0] == "parser_control.storage_fallback"
                    and call.kwargs.get("extra", {}).get("fallback") == "all_sections_enabled"
                    for call in log_action.call_args_list
                )
            )

    def test_snapshot_reports_cached_failure_and_effective_stable_publication(self) -> None:
        source_id = "hsreplay_arena_cards_advanced"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ParserControlStore(root)
            store.update_policy(
                expected_revision=1,
                mode="stable",
                early_until=None,
                reason="Стабильная публикация",
                updated_by="admin:7",
            )
            (root / "datasets").mkdir(parents=True)
            (root / "statuses").mkdir(parents=True)
            (root / "baselines").mkdir(parents=True)
            (root / "datasets" / f"{source_id}.json").write_text(
                '{"fetched_at":"2026-07-21T10:00:00+00:00","data":{"structured":{"cards":[{"card_id":"EARLY"}],"provisional":true}}}',
                encoding="utf-8",
            )
            (root / "baselines" / f"{source_id}.{STABLE_PUBLICATION_BASELINE_LABEL}.json").write_text(
                '{"fetched_at":"2026-07-20T08:00:00+00:00","data":{"structured":{"cards":[{"card_id":"STABLE"}]}}}',
                encoding="utf-8",
            )
            (root / "statuses" / f"{source_id}.json").write_text(
                '{"state":"ok","fetched_at":"2026-07-20T08:00:00+00:00","serving_cached_dataset":true,"last_refresh_state":"fetch_error","last_refresh_at":"2026-07-21T11:00:00+00:00","last_refresh_error":"origin timeout","rows_total":20}',
                encoding="utf-8",
            )

            snapshot = store.snapshot()
            row = next(
                source
                for section in snapshot["sections"]
                for source in section["sources"]
                if source["id"] == source_id
            )

            self.assertEqual(row["state"], "warning")
            self.assertEqual(row["health"], "warning")
            self.assertEqual(row["sourceState"], "ok")
            self.assertEqual(row["candidateFetchedAt"], "2026-07-21T10:00:00+00:00")
            self.assertEqual(row["publishedFetchedAt"], "2026-07-20T08:00:00+00:00")
            self.assertEqual(row["lastSuccessAt"], "2026-07-20T08:00:00+00:00")
            self.assertEqual(row["lastAttemptAt"], "2026-07-21T11:00:00+00:00")
            self.assertEqual(row["lastError"], "origin timeout")
            self.assertEqual(row["publicationChannel"], "stable_baseline")
            self.assertTrue(row["stableBaselineAvailable"])
            self.assertEqual(row["rowsTotal"], 1)

    def test_snapshot_distinguishes_confirmed_upstream_publication_pending(self) -> None:
        source_id = "vicious_syndicate_radars"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ParserControlStore(root)
            (root / "datasets").mkdir(parents=True)
            (root / "statuses").mkdir(parents=True)
            (root / "datasets" / f"{source_id}.json").write_text(
                '{"fetched_at":"2026-08-13T22:28:58+00:00","data":{"structured":{"type":"vicious_syndicate_radars","radars":[]}}}',
                encoding="utf-8",
            )
            (root / "statuses" / f"{source_id}.json").write_text(
                '{"state":"ok","fetched_at":"2026-08-13T22:28:58+00:00","serving_cached_dataset":true,"cached_content_temporally_grandfathered":true,"last_refresh_state":"quality_error","last_refresh_at":"2026-08-16T20:23:09+00:00","last_refresh_error":"Vicious upstream publication pending","upstream_state":"upstream_publication_pending","last_refresh_upstream_state":"upstream_publication_pending"}',
                encoding="utf-8",
            )

            snapshot = store.snapshot()
            row = next(
                source
                for section in snapshot["sections"]
                for source in section["sources"]
                if source["id"] == source_id
            )

        self.assertEqual(row["state"], "upstream_pending")
        self.assertEqual(row["health"], "upstream_pending")
        self.assertEqual(row["sourceState"], "ok")
        self.assertTrue(row["servingCachedDataset"])

    def test_snapshot_keeps_unverified_upstream_pending_cache_as_warning(self) -> None:
        source_id = "vicious_syndicate_radars"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ParserControlStore(root)
            (root / "datasets").mkdir(parents=True)
            (root / "statuses").mkdir(parents=True)
            (root / "datasets" / f"{source_id}.json").write_text(
                '{"fetched_at":"2026-08-13T22:28:58+00:00","data":{"structured":{"type":"vicious_syndicate_radars","radars":[]}}}',
                encoding="utf-8",
            )
            (root / "statuses" / f"{source_id}.json").write_text(
                '{"state":"ok","serving_cached_dataset":true,"last_refresh_upstream_state":"upstream_publication_pending"}',
                encoding="utf-8",
            )

            snapshot = store.snapshot()
            row = next(
                source
                for section in snapshot["sections"]
                for source in section["sources"]
                if source["id"] == source_id
            )

        self.assertEqual(row["health"], "warning")

    def test_snapshot_marks_operationally_disabled_source_as_disabled(self) -> None:
        source_id = "firestone_standard"
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_FIRESTONE_STANDARD_AUTHORIZED": "false"},
            clear=False,
        ):
            snapshot = ParserControlStore(Path(directory)).snapshot()
            row = next(
                source
                for section in snapshot["sections"]
                for source in section["sources"]
                if source["id"] == source_id
            )

        self.assertEqual(row["health"], "disabled")
        self.assertFalse(row["operationallyEnabled"])
        self.assertFalse(row["canRunManually"])
        self.assertFalse(row["enabled"])
        self.assertIsNone(row["nextRunAt"])


class ParserPipelineStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_bg_minion_aggregate_preserves_database_diagnostics(self) -> None:
        source_id = "hsreplay_battlegrounds_minions"
        base_result = {
            "source_id": source_id,
            "state": "ok",
            "detail": "source dataset updated",
            "errors": ["source warning"],
        }
        database_result = {
            "source_id": source_id,
            "state": "partial",
            "detail": "database rows incomplete",
            "errors": ["database warning"],
            "errors_total": 3,
            "errors_truncated": True,
            "rows_total": 173,
        }
        with patch(
            "app.fetcher.refresh_sources",
            new=AsyncMock(return_value=[base_result]),
        ) as refresh_sources, patch(
            "app.parser_control._run_pipeline_source",
            new=AsyncMock(return_value=database_result),
        ):
            results = await execute_parser_run([source_id])

        refresh_sources.assert_awaited_once_with(
            [source_id],
            persist_reliability=False,
        )

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["state"], "partial")
        self.assertEqual(
            result["detail"], "source dataset updated; database rows incomplete"
        )
        self.assertEqual(result["errors"], ["source warning", "database warning"])
        self.assertEqual(result["errors_total"], 4)
        self.assertTrue(result["errors_truncated"])
        self.assertEqual(result["rows_total"], 173)

    async def test_bg_minion_aggregate_keeps_scrape_rows_total(self) -> None:
        source_id = "hsreplay_battlegrounds_minions"
        base_result = {
            "source_id": source_id,
            "state": "ok",
            "rows_total": 180,
        }
        database_result = {
            "source_id": source_id,
            "state": "ok",
            "rows_total": 173,
        }
        with patch(
            "app.fetcher.refresh_sources",
            new=AsyncMock(return_value=[base_result]),
        ), patch(
            "app.parser_control._run_pipeline_source",
            new=AsyncMock(return_value=database_result),
        ):
            results = await execute_parser_run([source_id])

        self.assertEqual(results[0]["rows_total"], 180)

    async def test_pipeline_reports_processed_rows_and_normalizes_structured_errors(self) -> None:
        upstream = {
            "ok": False,
            "state": "partial",
            "heroes": 91,
            "errors": [{"dbfId": 42, "error": "origin timeout"}],
            "serving_cached_dataset": True,
        }
        with patch(
            "app.hsreplay_bg_hero_details.refresh_bg_hero_details",
            new=AsyncMock(return_value=upstream),
        ):
            result = await _run_pipeline_source(
                "hsreplay_battlegrounds_hero_details"
            )

        self.assertEqual(result["rows_total"], 91)
        self.assertEqual(result["errors"], ["dbfId=42: origin timeout"])
        self.assertEqual(result["errors_total"], 1)
        self.assertFalse(result["errors_truncated"])
        self.assertEqual(result["detail"], "dbfId=42: origin timeout")

    async def test_bg_minion_pipeline_maps_quality_result_contract(self) -> None:
        upstream = {
            "ok": False,
            "state": "partial",
            "minions_total": 180,
            "minions_ok": 173,
            "quality_errors": [
                "BG minion rows stored incompletely (173/180)",
                "BG minion stats fill too low (120/180; 66.7%)",
            ],
        }
        with patch(
            "app.hsreplay_bg_minions_db.refresh_bg_minion_database_sync",
            return_value=upstream,
        ):
            result = await _run_pipeline_source(
                "hsreplay_battlegrounds_minions"
            )

        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["rows_total"], 173)
        self.assertEqual(result["errors"], upstream["quality_errors"])
        self.assertEqual(result["errors_total"], 2)
        self.assertFalse(result["errors_truncated"])
        self.assertIn("stored incompletely", result["detail"])

    async def test_pipeline_preserves_partial_state_errors_and_cached_flag(self) -> None:
        upstream = {
            "ok": True,
            "state": "partial",
            "errors": ["один источник недоступен"],
            "serving_cached_dataset": True,
        }
        with patch(
            "app.hsreplay_bg_hero_details.refresh_bg_hero_details",
            new=AsyncMock(return_value=upstream),
        ):
            result = await _run_pipeline_source(
                "hsreplay_battlegrounds_hero_details"
            )

        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["errors"], upstream["errors"])
        self.assertTrue(result["serving_cached_dataset"])
        self.assertIn("один источник", result["detail"])


if __name__ == "__main__":
    unittest.main()
