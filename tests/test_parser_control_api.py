from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.parser_control import ParserControlStore, ParserRunWorker

ORCHESTRATOR_TOKEN = "orchestrator-token-at-least-32-characters"
ADMIN_TOKEN = "admin-token-kept-distinct-from-orchestrator"


class ParserControlApiTest(unittest.TestCase):
    def test_orchestrator_uses_scoped_key_and_exact_run_endpoint(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_API_KEY": "admin-secret",
                "HS_ORCHESTRATOR_API_KEY": ORCHESTRATOR_TOKEN,
            },
            clear=False,
        ):
            store = ParserControlStore(Path(directory))
            run, _ = store.enqueue_run(
                source_ids=["vicious_syndicate_live_beta"],
                requested_by="trigger.dev",
                reason="canary",
                request_id="trigger:run_abc:attempt:1",
            )
            with patch("app.parser_control._STORE", store), patch(
                "app.parser_control._RUN_WORKER"
            ) as worker, TestClient(app) as client:
                worker.enqueue.return_value = (run, True)
                payload = {
                    "requestId": "trigger:run_abc:attempt:1",
                    "sourceIds": ["vicious_syndicate_live_beta"],
                    "sectionIds": [],
                    "reason": "canary",
                }

                self.assertEqual(
                    client.post("/admin/orchestrator/parser-runs", json=payload).status_code,
                    401,
                )
                self.assertEqual(
                    client.post(
                        "/admin/orchestrator/parser-runs",
                        headers={"X-Orchestrator-Key": "admin-secret"},
                        json=payload,
                    ).status_code,
                    401,
                )
                created = client.post(
                    "/admin/orchestrator/parser-runs",
                    headers={"X-Orchestrator-Key": ORCHESTRATOR_TOKEN},
                    json=payload,
                )
                fetched = client.get(
                    f"/admin/orchestrator/parser-runs/{run['id']}",
                    headers={"X-Orchestrator-Key": ORCHESTRATOR_TOKEN},
                )

                self.assertEqual(created.status_code, 202, created.text)
                self.assertTrue(created.json()["deduplicated"])
                self.assertEqual(fetched.status_code, 200, fetched.text)
                self.assertEqual(fetched.json()["run"]["id"], run["id"])
                self.assertEqual(
                    client.get(
                        "/admin/parser-runs",
                        headers={"X-API-Key": ORCHESTRATOR_TOKEN},
                    ).status_code,
                    401,
                )
                worker.enqueue.assert_called_once_with(
                    source_ids=["vicious_syndicate_live_beta"],
                    requested_by="trigger.dev",
                    reason="canary",
                    request_id="trigger:run_abc:attempt:1",
                    attempt_purpose="manual",
                    origin_occurrence_id=None,
                    recovery_chain_id=None,
                )

    def test_orchestrator_rejects_fields_outside_the_scoped_contract(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_ORCHESTRATOR_API_KEY": ORCHESTRATOR_TOKEN,
            },
            clear=False,
        ), TestClient(app) as client:
            response = client.post(
                "/admin/orchestrator/parser-runs",
                headers={"X-Orchestrator-Key": ORCHESTRATOR_TOKEN},
                json={
                    "requestId": "trigger:run_abc:attempt:1",
                    "sourceIds": ["vicious_syndicate_live_beta"],
                    "requestedBy": "admin",
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"],
            {"unexpected_fields": ["requestedBy"]},
        )

    def test_orchestrator_rejects_unsafe_token_configuration(self) -> None:
        payload = {
            "requestId": "trigger:run_abc:attempt:1",
            "sourceIds": ["vicious_syndicate_live_beta"],
        }
        configurations = [
            {"HS_API_KEY": ADMIN_TOKEN, "HS_ORCHESTRATOR_API_KEY": "too-short"},
            {
                "HS_API_KEY": ORCHESTRATOR_TOKEN,
                "HS_ORCHESTRATOR_API_KEY": ORCHESTRATOR_TOKEN,
            },
        ]
        for environment in configurations:
            with self.subTest(environment=environment), patch.dict(
                os.environ,
                environment,
                clear=False,
            ), TestClient(app) as client:
                response = client.post(
                    "/admin/orchestrator/parser-runs",
                    headers={
                        "X-Orchestrator-Key": environment["HS_ORCHESTRATOR_API_KEY"]
                    },
                    json=payload,
                )
                self.assertEqual(response.status_code, 503, response.text)

    def test_orchestrator_rejects_invalid_identifier_before_enqueue(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HS_API_KEY": ADMIN_TOKEN,
                "HS_ORCHESTRATOR_API_KEY": ORCHESTRATOR_TOKEN,
            },
            clear=False,
        ), patch("app.parser_control._RUN_WORKER") as worker, TestClient(app) as client:
            response = client.post(
                "/admin/orchestrator/parser-runs",
                headers={"X-Orchestrator-Key": ORCHESTRATOR_TOKEN},
                json={
                    "requestId": "trigger:run_abc:attempt:1",
                    "sourceIds": ["https://untrusted.example/path"],
                },
            )

        self.assertEqual(response.status_code, 422, response.text)
        worker.enqueue.assert_not_called()

    def test_orchestrator_run_view_does_not_expose_details_or_audit_fields(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_ORCHESTRATOR_API_KEY": ORCHESTRATOR_TOKEN,
            },
            clear=False,
        ):
            store = ParserControlStore(Path(directory))
            run, _ = store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="operator@example.invalid",
                reason="PRIVATE_REASON_MUST_NOT_LEAK",
                request_id="trigger:run_safe:attempt:1",
            )
            store.finish_run(
                run["id"],
                status="partial",
                results=[
                    {
                        "sourceId": "heartharena_tierlist",
                        "state": "fetch_error",
                        "servingCachedDataset": True,
                        "detail": "PRIVATE_ERROR_MUST_NOT_LEAK",
                        "errors": ["PRIVATE_ERROR_MUST_NOT_LEAK"],
                    }
                ],
            )
            with patch("app.parser_control._STORE", store), TestClient(app) as client:
                response = client.get(
                    f"/admin/orchestrator/parser-runs/{run['id']}",
                    headers={"X-Orchestrator-Key": ORCHESTRATOR_TOKEN},
                )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertNotIn("PRIVATE_", response.text)
        self.assertEqual(
            set(payload["run"]),
            {
                "id",
                "status",
                "attemptPurpose",
                "originOccurrenceId",
                "recoveryChainId",
                "sourceIds",
                "totalSources",
                "completedSources",
                "failedSources",
                "results",
            },
        )
        self.assertEqual(
            payload["run"]["results"],
            [
                {
                    "sourceId": "heartharena_tierlist",
                    "state": "fetch_error",
                    "servingCachedDataset": True,
                    "outcome": "lkg_served",
                    "reasonCode": "unknown",
                    "upstreamPending": False,
                }
            ],
        )

    def test_orchestrator_exposes_only_bounded_exact_paid_usage(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_ORCHESTRATOR_API_KEY": ORCHESTRATOR_TOKEN,
            },
            clear=False,
        ):
            store = ParserControlStore(Path(directory))
            run, _ = store.enqueue_run(
                source_ids=["hsguru_meta_standard_legend"],
                requested_by="convergence-controller",
                reason="recover transport",
                request_id="convergence:chain-paid:attempt-1",
                attempt_purpose="recovery",
                origin_occurrence_id="schedule:20260820T100000Z",
                recovery_chain_id="chain-paid",
            )
            store.finish_run(
                run["id"],
                status="succeeded",
                results=[
                    {
                        "sourceId": "hsguru_meta_standard_legend",
                        "state": "ok",
                        "terminalOutcome": "fresh_published",
                        "reasonCode": "none",
                        "independentlyIneligibleReason": "",
                        "paidRequests": 1,
                        "paidCostMicrousd": 290,
                        "paidUsageExact": True,
                    }
                ],
            )
            with patch("app.parser_control._STORE", store), TestClient(app) as client:
                response = client.get(
                    f"/admin/orchestrator/parser-runs/{run['id']}",
                    headers={"X-Orchestrator-Key": ORCHESTRATOR_TOKEN},
                )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["run"]["results"][0]
        self.assertEqual(result["paidRequests"], 1)
        self.assertEqual(result["paidCostMicrousd"], 290)
        self.assertTrue(result["paidUsageExact"])

    def test_orchestrator_accepts_only_a_fully_correlated_recovery_run(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_ORCHESTRATOR_API_KEY": ORCHESTRATOR_TOKEN,
            },
            clear=False,
        ), patch("app.parser_control._RUN_WORKER") as worker, TestClient(app) as client:
            worker.enqueue.return_value = (
                {
                    "id": "0" * 32,
                    "status": "queued",
                    "attemptPurpose": "recovery",
                    "originOccurrenceId": "schedule:20260820T100000Z",
                    "recoveryChainId": "chain-1",
                    "sourceIds": ["hsguru_meta_standard_legend"],
                    "totalSources": 1,
                    "completedSources": 0,
                    "failedSources": 0,
                    "results": [],
                },
                False,
            )
            payload = {
                "requestId": "convergence:chain-1:attempt-1",
                "sourceIds": ["hsguru_meta_standard_legend"],
                "attemptPurpose": "recovery",
                "originOccurrenceId": "schedule:20260820T100000Z",
                "recoveryChainId": "chain-1",
            }

            response = client.post(
                "/admin/orchestrator/parser-runs",
                headers={"X-Orchestrator-Key": ORCHESTRATOR_TOKEN},
                json=payload,
            )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["run"]["attemptPurpose"], "recovery")
        self.assertEqual(response.json()["run"]["recoveryChainId"], "chain-1")
        worker.enqueue.assert_called_once_with(
            source_ids=["hsguru_meta_standard_legend"],
            requested_by="trigger.dev",
            reason=None,
            request_id="convergence:chain-1:attempt-1",
            attempt_purpose="recovery",
            origin_occurrence_id="schedule:20260820T100000Z",
            recovery_chain_id="chain-1",
        )

    def test_parser_runs_exposes_normalized_source_result_contract(self) -> None:
        class SecretError:
            def __repr__(self) -> str:
                return "SECRET_TOKEN_MUST_NOT_LEAK"

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_API_DATA_DIR": directory, "HS_API_KEY": "secret"},
            clear=False,
        ):
            store = ParserControlStore(Path(directory))
            store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="admin:7",
                reason="Проверка контракта",
            )

            async def executor(_source_ids: list[str]) -> list[dict[str, object]]:
                return [
                    {
                        "source_id": "heartharena_tierlist",
                        "state": "partial",
                        "fetched_at": "2026-07-21T12:00:00+00:00",
                        "detail": "Один источник недоступен",
                        "errors": [
                            {
                                "archetype_id": 17,
                                "access_token": "SECRET_DICT_TOKEN_MUST_NOT_LEAK",
                                "error": "origin timeout",
                            },
                            "  publisher returned stale data  ",
                            {"access_token": "SECRET_ONLY_TOKEN_MUST_NOT_LEAK"},
                            SecretError(),
                            "",
                            None,
                        ],
                        "rows_total": 137,
                        "serving_cached_dataset": True,
                    }
                ]

            worker = ParserRunWorker(store, executor=executor)
            with patch(
                "app.parser_control._monotonic_ms", side_effect=[1_000.0, 1_250.4]
            ):
                self.assertTrue(worker.process_next())

            with patch("app.parser_control._STORE", store), patch(
                "app.parser_control._RUN_WORKER"
            ), TestClient(app) as client:
                response = client.get(
                    "/admin/parser-runs", headers={"X-API-Key": "secret"}
                )

            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["runs"][0]["results"][0]
            self.assertEqual(
                result,
                {
                    "sourceId": "heartharena_tierlist",
                    "label": "HearthArena · тир-лист карт",
                    "state": "partial",
                    "fetchedAt": "2026-07-21T12:00:00+00:00",
                    "detail": "Один источник недоступен",
                    "errors": [
                        "archetype_id=17: origin timeout",
                        "publisher returned stale data",
                        "Структурированная ошибка парсера",
                        "Неизвестная ошибка парсера",
                    ],
                    "errorsTotal": 4,
                    "errorsTruncated": False,
                    "servingCachedDataset": True,
                    "terminalOutcome": "lkg_served",
                    "reasonCode": "unknown",
                    "independentlyIneligibleReason": "",
                    "rowsTotal": 137,
                    "durationMs": 250,
                },
            )
            self.assertNotIn("SECRET_TOKEN_MUST_NOT_LEAK", response.text)
            self.assertNotIn("SECRET_DICT_TOKEN_MUST_NOT_LEAK", response.text)
            self.assertNotIn("SECRET_ONLY_TOKEN_MUST_NOT_LEAK", response.text)

    def test_parser_runs_limits_public_errors_and_reports_truncation(self) -> None:
        with TemporaryDirectory() as directory:
            store = ParserControlStore(Path(directory))
            run, _deduplicated = store.enqueue_run(
                source_ids=["heartharena_tierlist"],
                requested_by="admin:7",
                reason="Проверка ограничения ошибок",
            )
            store.record_run_result(
                run["id"],
                {
                    "sourceId": "heartharena_tierlist",
                    "state": "partial",
                    "errors": [f"error-{index:03d}" for index in range(75)],
                },
            )

            result = store.get_run(run["id"])["results"][0]

            self.assertEqual(len(result["errors"]), 50)
            self.assertEqual(result["errors"][0], "error-000")
            self.assertEqual(result["errors"][-1], "error-049")
            self.assertEqual(result["errorsTotal"], 75)
            self.assertTrue(result["errorsTruncated"])

    def test_control_plane_requires_admin_key_and_rejects_stale_revision(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_API_DATA_DIR": directory, "HS_API_KEY": "secret"},
            clear=False,
        ), TestClient(app) as client:
            self.assertEqual(client.get("/admin/parser-control").status_code, 401)

            headers = {"X-API-Key": "secret"}
            initial = client.get("/admin/parser-control", headers=headers)
            self.assertEqual(initial.status_code, 200)
            self.assertEqual(initial.headers.get("cache-control"), "private, no-store")
            payload = initial.json()
            self.assertEqual(payload["revision"], 1)
            self.assertIn("sections", payload)
            self.assertEqual(payload["scheduleInventory"]["schemaVersion"], 2)

            changed = client.patch(
                "/admin/parser-control/policy",
                headers=headers,
                json={
                    "expectedRevision": 1,
                    "mode": "early",
                    "earlyUntil": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    "reason": "Балансный патч",
                    "updatedBy": "admin:7",
                },
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(changed.json()["revision"], 2)

            stale = client.patch(
                "/admin/parser-control/sections",
                headers=headers,
                json={
                    "expectedRevision": 1,
                    "sections": [{"id": "arena-tier-list", "enabled": False}],
                    "updatedBy": "admin:7",
                },
            )
            self.assertEqual(stale.status_code, 409, stale.text)

    def test_corrupted_control_file_does_not_prevent_public_api_startup(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_API_DATA_DIR": directory, "HS_API_KEY": "secret"},
            clear=False,
        ):
            store = ParserControlStore(Path(directory))
            store.state_path.parent.mkdir(parents=True, exist_ok=True)
            store.state_path.write_text("{broken", encoding="utf-8")
            worker = ParserRunWorker(store)
            with patch("app.parser_control._RUN_WORKER", worker), patch(
                "app.refresh_log.log_action"
            ) as log_action, TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                admin = client.get(
                    "/admin/parser-control", headers={"X-API-Key": "secret"}
                )

            self.assertEqual(admin.status_code, 503)
            self.assertTrue(
                any(
                    call.args and call.args[0] == "parser_control.storage_fallback"
                    and call.kwargs.get("extra", {}).get("operation")
                    == "parser_run_worker_start"
                    for call in log_action.call_args_list
                )
            )

    def test_saved_control_mutations_return_warning_when_audit_write_fails(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_API_DATA_DIR": directory, "HS_API_KEY": "secret"},
            clear=False,
        ), TestClient(app) as client:
            headers = {"X-API-Key": "secret"}
            with patch(
                "app.refresh_log.log_action",
                side_effect=OSError("audit volume is read-only"),
            ), self.assertLogs("app.parser_control", level="ERROR") as policy_logs:
                policy = client.patch(
                    "/admin/parser-control/policy",
                    headers=headers,
                    json={
                        "expectedRevision": 1,
                        "mode": "stable",
                        "reason": "Достаточная выборка",
                        "updatedBy": "admin:7",
                    },
                )

            self.assertEqual(policy.status_code, 200, policy.text)
            self.assertEqual(policy.json()["revision"], 2)
            self.assertEqual(policy.json()["warnings"][0]["code"], "AUDIT_WRITE_FAILED")
            self.assertIn("parser_control.policy.update", " ".join(policy_logs.output))

            with patch(
                "app.refresh_log.log_action",
                side_effect=OSError("audit volume is read-only"),
            ), self.assertLogs("app.parser_control", level="ERROR") as section_logs:
                sections = client.patch(
                    "/admin/parser-control/sections",
                    headers=headers,
                    json={
                        "expectedRevision": 2,
                        "sections": [
                            {"id": "arena-tier-list", "enabled": False}
                        ],
                        "updatedBy": "admin:7",
                    },
                )

            self.assertEqual(sections.status_code, 200, sections.text)
            self.assertEqual(sections.json()["revision"], 3)
            self.assertEqual(
                sections.json()["warnings"][0]["code"], "AUDIT_WRITE_FAILED"
            )
            self.assertIn("parser_control.sections.update", " ".join(section_logs.output))

            persisted = client.get("/admin/parser-control", headers=headers)
            self.assertEqual(persisted.status_code, 200, persisted.text)
            self.assertEqual(persisted.json()["revision"], 3)
            arena = next(
                row
                for row in persisted.json()["sections"]
                if row["id"] == "arena-tier-list"
            )
            self.assertFalse(arena["enabled"])


if __name__ == "__main__":
    unittest.main()
