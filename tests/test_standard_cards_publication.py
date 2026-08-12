from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.dataset_publication_store import (
    DatasetPublicationStore,
    PublicationUnavailable,
    STANDARD_CARDS_SOURCE_ID,
    dataset_version,
)
from app.main import app
from app.publish_gate import PublishGateResult
from app.sources import SOURCE_BY_ID
from app.source_validators import validate_structured


def _acquire_publication_transaction_in_child(
    root: str,
    connection,
) -> None:
    """Process helper proving the publication lock is an OS-level lock."""

    try:
        store = DatasetPublicationStore(Path(root))
        connection.send("ready")
        with store.publication_transaction(STANDARD_CARDS_SOURCE_ID):
            connection.send("acquired")
    finally:
        connection.close()


def _cards(count: int = 30, *, popularity: str = "12.5%") -> list[dict[str, object]]:
    return [
        {
            "id": f"TEST_{index:04d}",
            "dbfId": 100_000 + index,
            "name": f"Card {index}",
            "deck_winrate": "52.5%",
            "deck_popularity": popularity,
            "winrate_when_played": "54.1%",
            "winrate_when_drawn": "53.2%",
            "keep_percentage": "41.0%",
            "opening_hand_winrate": "51.4%",
        }
        for index in range(count)
    ]


def _parsed_payload(count: int = 600, *, popularity: str = "12.5%") -> dict:
    structured = {
        "type": "card_stats",
        "cards": _cards(count, popularity=popularity),
        "blocked": False,
        "sort_mode": "deck_popularity",
        "game_type": "RANKED_STANDARD",
        "rank_range": "LEGEND",
        "time_range": "LAST_1_DAY",
    }
    return {
        "title": "HSReplay Standard card statistics",
        "tables": [],
        "json_scripts": [],
        "deck_codes": [],
        "links": [],
        "text_preview": [],
        "counts": {"api_bytes": 1234},
        "structured": structured,
        "hsreplay_extracted": deepcopy(structured),
    }


def _dataset(
    *,
    fetched_at: datetime | None = None,
    count: int = 30,
    popularity: str = "12.5%",
    backend: str = "test",
) -> dict:
    return {
        "source_id": STANDARD_CARDS_SOURCE_ID,
        "fetched_at": (fetched_at or datetime.now(UTC)).isoformat(),
        "backend": backend,
        "content_length": 1234,
        "data": _parsed_payload(count, popularity=popularity),
    }


class StandardCardsValidatorTest(unittest.TestCase):
    def test_standard_card_row_ceiling_keeps_rotation_headroom(self) -> None:
        for count in (1_152, 1_500):
            with self.subTest(count=count):
                report = validate_structured(
                    STANDARD_CARDS_SOURCE_ID,
                    {"type": "card_stats", "cards": _cards(count)},
                )

                self.assertTrue(report.ok, report.reason)
                self.assertEqual(report.metrics["maximum_cards"], 1_800)

    def test_standard_card_row_ceiling_rejects_format_leaks(self) -> None:
        for count in (1_801, 2_000):
            with self.subTest(count=count):
                report = validate_structured(
                    STANDARD_CARDS_SOURCE_ID,
                    {"type": "card_stats", "cards": _cards(count)},
                )

                self.assertFalse(report.ok)
                self.assertIn(
                    "card_stats.too_many_standard_cards",
                    {issue.code for issue in report.issues},
                )

    def test_rejects_out_of_range_percentages_and_duplicate_identities(self) -> None:
        cards = _cards()
        cards[2]["deck_winrate"] = "100.01%"
        cards[4]["deck_popularity"] = "-0.01%"
        cards[8]["id"] = cards[7]["id"]
        cards[10]["dbfId"] = cards[9]["dbfId"]

        report = validate_structured(
            STANDARD_CARDS_SOURCE_ID,
            {"type": "card_stats", "cards": cards},
        )

        self.assertFalse(report.ok)
        codes = {issue.code for issue in report.issues}
        self.assertIn("card_stats.percent_out_of_range", codes)
        self.assertIn("card_stats.duplicate_card_id", codes)
        self.assertIn("card_stats.duplicate_dbf_id", codes)

    def test_rejects_systemic_99_percent_popularity_cascade(self) -> None:
        cards = _cards()
        for card in cards[:10]:
            card["deck_popularity"] = "99%"

        report = validate_structured(
            STANDARD_CARDS_SOURCE_ID,
            {"type": "card_stats", "cards": cards},
        )

        self.assertFalse(report.ok)
        self.assertIn(
            "card_stats.systemic_popularity_cascade",
            {issue.code for issue in report.issues},
        )
        self.assertEqual(report.metrics["deck_popularity_at_least_80"], 10)

    def test_rejects_provisional_or_post_patch_early_standard_cards(self) -> None:
        for marker in (
            {"provisional": True},
            {"data_phase": "post_patch_early"},
        ):
            with self.subTest(marker=marker):
                structured = {
                    "type": "card_stats",
                    "cards": _cards(),
                    **marker,
                }

                report = validate_structured(
                    STANDARD_CARDS_SOURCE_ID,
                    structured,
                )

                self.assertFalse(report.ok)
                self.assertIn(
                    "card_stats.provisional_not_supported",
                    {issue.code for issue in report.issues},
                )

    def test_publication_rejects_disagreeing_structured_aliases(self) -> None:
        with TemporaryDirectory() as directory:
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            dataset = _dataset(count=600)
            dataset["data"]["hsreplay_extracted"]["cards"][0][
                "deck_popularity"
            ] = "13.5%"

            decision = validate_and_publish_standard_cards_candidate(
                SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                dataset,
                store=DatasetPublicationStore(Path(directory)),
            )

            self.assertFalse(decision.accepted)
            self.assertIn("aliases", decision.reason)

    def test_publication_rejects_future_candidate_after_durable_staging(self) -> None:
        with TemporaryDirectory() as directory:
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            decision = validate_and_publish_standard_cards_candidate(
                SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                _dataset(
                    fetched_at=datetime.now(UTC) + timedelta(minutes=6),
                    count=600,
                ),
                store=store,
            )

            self.assertFalse(decision.accepted)
            self.assertEqual(decision.rejection_kind, "validation")
            self.assertTrue(store.candidate_path(STANDARD_CARDS_SOURCE_ID).exists())
            quarantine = store.list_quarantine(STANDARD_CARDS_SOURCE_ID)
            self.assertEqual(len(quarantine), 1)
            self.assertIn("future", quarantine[0]["reason"])


class DatasetPublicationStoreTest(unittest.TestCase):
    def test_publication_transaction_blocks_another_process(self) -> None:
        with TemporaryDirectory() as directory:
            store = DatasetPublicationStore(Path(directory))
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe(duplex=False)

            with store.publication_transaction(STANDARD_CARDS_SOURCE_ID):
                process = context.Process(
                    target=_acquire_publication_transaction_in_child,
                    args=(directory, child),
                )
                process.start()
                child.close()
                self.assertEqual(parent.recv(), "ready")
                self.assertFalse(
                    parent.poll(0.2),
                    "child acquired the cross-process transaction lock too early",
                )

            self.assertTrue(parent.poll(2.0), "child never acquired the released lock")
            self.assertEqual(parent.recv(), "acquired")
            process.join(timeout=2.0)
            self.assertEqual(process.exitcode, 0)
            parent.close()

    def test_concurrent_validations_are_serialized_and_newer_snapshot_wins(self) -> None:
        with TemporaryDirectory() as directory:
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            older_entered_validation = threading.Event()
            release_older = threading.Event()
            newer_started = threading.Event()
            newer_entered_validation = threading.Event()
            decisions: dict[str, object] = {}
            failures: list[BaseException] = []

            def gate(_source, _parsed, *, backend):
                if backend == "older":
                    older_entered_validation.set()
                    if not release_older.wait(2.0):
                        raise AssertionError("test did not release older validation")
                else:
                    newer_entered_validation.set()
                return PublishGateResult(ok=True, reason="ok", extra={})

            def publish(name: str, dataset: dict) -> None:
                if name == "newer":
                    newer_started.set()
                try:
                    decisions[name] = validate_and_publish_standard_cards_candidate(
                        source,
                        dataset,
                        store=store,
                    )
                except BaseException as exc:  # surfaced on the test thread below
                    failures.append(exc)

            older = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                count=600,
                backend="older",
            )
            newer = _dataset(count=600, backend="newer")
            with patch(
                "app.publish_gate.validate_candidate_for_publish",
                side_effect=gate,
            ):
                older_thread = threading.Thread(target=publish, args=("older", older))
                newer_thread = threading.Thread(target=publish, args=("newer", newer))
                older_thread.start()
                self.assertTrue(older_entered_validation.wait(2.0))
                newer_thread.start()
                self.assertTrue(newer_started.wait(2.0))
                try:
                    self.assertFalse(
                        newer_entered_validation.wait(0.2),
                        "newer validation entered before the older transaction finished",
                    )
                finally:
                    release_older.set()
                older_thread.join(timeout=3.0)
                newer_thread.join(timeout=3.0)

            self.assertFalse(older_thread.is_alive())
            self.assertFalse(newer_thread.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(decisions["older"].accepted)
            self.assertTrue(decisions["newer"].accepted)
            published = store.read_published_unbounded(STANDARD_CARDS_SOURCE_ID)
            self.assertIsNotNone(published)
            assert published is not None
            self.assertEqual(published["backend"], "newer")

    def test_older_candidate_cannot_replace_a_newer_published_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            newer = _dataset(count=600, backend="newer")
            older = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=5),
                count=600,
                backend="older",
            )

            accepted = validate_and_publish_standard_cards_candidate(
                source, newer, store=store
            )
            rejected = validate_and_publish_standard_cards_candidate(
                source, older, store=store
            )

            self.assertTrue(accepted.accepted)
            self.assertFalse(rejected.accepted)
            self.assertEqual(rejected.rejection_kind, "obsolete")
            published = store.read_published_unbounded(STANDARD_CARDS_SOURCE_ID)
            self.assertIsNotNone(published)
            assert published is not None
            self.assertEqual(published["backend"], "newer")

    def test_stage_happens_before_publish_gate_is_called(self) -> None:
        with TemporaryDirectory() as directory:
            store = DatasetPublicationStore(Path(directory))
            dataset = _dataset()

            def assert_staged(*_args, **_kwargs):
                self.assertTrue(store.candidate_path(STANDARD_CARDS_SOURCE_ID).exists())
                return PublishGateResult(ok=True, reason="ok", extra={})

            with patch(
                "app.publish_gate.validate_candidate_for_publish",
                side_effect=assert_staged,
            ):
                from app.dataset_publication_store import (
                    validate_and_publish_standard_cards_candidate,
                )

                decision = validate_and_publish_standard_cards_candidate(
                    SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                    dataset,
                    store=store,
                )

            self.assertTrue(decision.accepted)

    def test_candidate_is_durable_before_validation_and_survives_restart(self) -> None:
        with TemporaryDirectory() as directory:
            store = DatasetPublicationStore(Path(directory))
            dataset = _dataset()
            candidate = store.stage_candidate(STANDARD_CARDS_SOURCE_ID, dataset)

            candidate_path = store.candidate_path(STANDARD_CARDS_SOURCE_ID)
            self.assertTrue(candidate_path.exists())
            on_disk = json.loads(candidate_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["dataset_version"], candidate["dataset_version"])

            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                candidate["dataset_version"],
                validation={"ok": True, "reason": "ok", "diagnostics": {}},
            )
            restarted = DatasetPublicationStore(Path(directory))
            published = restarted.read_published(
                STANDARD_CARDS_SOURCE_ID,
                current_dataset=dataset,
                status={"state": "ok"},
            )

            self.assertEqual(published.dataset_version, candidate["dataset_version"])
            self.assertFalse(published.stale)
            self.assertIsNone(published.fallback_reason)

    def test_immutable_history_retains_current_and_previous_across_restart(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_STANDARD_CARDS_PUBLICATION_RETENTION": "2"},
            clear=False,
        ):
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            store = DatasetPublicationStore(Path(directory))
            decisions = []
            for index in range(3):
                decisions.append(
                    validate_and_publish_standard_cards_candidate(
                        source,
                        _dataset(
                            fetched_at=datetime.now(UTC)
                            + timedelta(seconds=index),
                            count=600,
                            backend=f"version-{index}",
                        ),
                        store=store,
                    )
                )

            restarted = DatasetPublicationStore(Path(directory))
            history = restarted.list_published_versions(STANDARD_CARDS_SOURCE_ID)
            published = restarted.read_published_unbounded(STANDARD_CARDS_SOURCE_ID)

            self.assertTrue(all(decision.accepted for decision in decisions))
            self.assertEqual(len(history), 2)
            self.assertEqual(
                [row["dataset_version"] for row in history],
                [decisions[2].dataset_version, decisions[1].dataset_version],
            )
            self.assertIsNotNone(published)
            assert published is not None
            self.assertEqual(published["backend"], "version-2")
            self.assertTrue(
                all(
                    restarted.version_path(
                        STANDARD_CARDS_SOURCE_ID,
                        decision.dataset_version,
                    ).exists()
                    for decision in decisions[1:]
                )
            )
            self.assertFalse(
                restarted.version_path(
                    STANDARD_CARDS_SOURCE_ID,
                    decisions[0].dataset_version,
                ).exists()
            )

    def test_manifest_checksum_detects_structurally_valid_history_reordering(self) -> None:
        with TemporaryDirectory() as directory:
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            first = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                    count=600,
                    backend="first",
                ),
                store=store,
            )
            second = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                    count=600,
                    backend="second",
                ),
                store=store,
            )
            manifest_path = store.published_path(STANDARD_CARDS_SOURCE_ID)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["current_version"] = first.dataset_version
            manifest["versions"] = [first.dataset_version, second.dataset_version]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(
                PublicationUnavailable, "published_corrupt"
            ):
                store.read_published_unbounded(STANDARD_CARDS_SOURCE_ID)

    def test_repairing_corrupt_manifest_preserves_valid_immutable_history(self) -> None:
        with TemporaryDirectory() as directory:
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            decisions = []
            for index in range(2):
                decisions.append(
                    validate_and_publish_standard_cards_candidate(
                        source,
                        _dataset(
                            fetched_at=datetime.now(UTC)
                            - timedelta(minutes=2 - index),
                            count=600,
                            backend=f"before-corruption-{index}",
                        ),
                        store=store,
                    )
                )
            manifest_path = store.published_path(STANDARD_CARDS_SOURCE_ID)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["manifest_checksum"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            repair = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(count=600, backend="repair"),
                store=store,
            )
            history = store.list_published_versions(STANDARD_CARDS_SOURCE_ID)

            self.assertTrue(repair.accepted)
            self.assertEqual(history[0]["dataset_version"], repair.dataset_version)
            self.assertTrue(
                {decision.dataset_version for decision in decisions}.issubset(
                    {row["dataset_version"] for row in history}
                )
            )

    def test_corrupt_manifest_still_uses_freshest_immutable_for_ordering(self) -> None:
        with TemporaryDirectory() as directory:
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            oldest = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=3),
                    count=600,
                    backend="oldest",
                ),
                store=store,
            )
            freshest = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                    count=600,
                    backend="freshest-immutable",
                ),
                store=store,
            )
            manifest_path = store.published_path(STANDARD_CARDS_SOURCE_ID)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["manifest_checksum"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            downgrade = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                    count=600,
                    backend="older-than-freshest",
                ),
                store=store,
            )

            self.assertTrue(oldest.accepted)
            self.assertTrue(freshest.accepted)
            self.assertFalse(downgrade.accepted)
            self.assertEqual(downgrade.rejection_kind, "obsolete")
            self.assertFalse(
                store.version_path(
                    STANDARD_CARDS_SOURCE_ID, downgrade.dataset_version
                ).exists()
            )
            with self.assertRaisesRegex(
                PublicationUnavailable, "published_corrupt"
            ):
                store.read_published(STANDARD_CARDS_SOURCE_ID)

    def test_missing_or_corrupt_manifest_uses_freshest_immutable_as_regression_baseline(
        self,
    ) -> None:
        for manifest_state in ("missing", "corrupt"):
            with self.subTest(manifest_state=manifest_state), TemporaryDirectory() as directory:
                from app.dataset_publication_store import (
                    validate_and_publish_standard_cards_candidate,
                )

                store = DatasetPublicationStore(Path(directory))
                source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
                baseline = validate_and_publish_standard_cards_candidate(
                    source,
                    _dataset(
                        fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                        count=1300,
                        backend=f"{manifest_state}-large-baseline",
                    ),
                    store=store,
                )
                manifest_path = store.published_path(STANDARD_CARDS_SOURCE_ID)
                if manifest_state == "missing":
                    manifest_path.unlink()
                else:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["manifest_checksum"] = "0" * 64
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

                regressed = validate_and_publish_standard_cards_candidate(
                    source,
                    _dataset(count=600, backend=f"{manifest_state}-regression"),
                    store=store,
                )

                self.assertTrue(baseline.accepted)
                self.assertFalse(regressed.accepted)
                self.assertEqual(regressed.rejection_kind, "regression")
                self.assertFalse(
                    store.version_path(
                        STANDARD_CARDS_SOURCE_ID, regressed.dataset_version
                    ).exists()
                )
                self.assertTrue(
                    store.version_path(
                        STANDARD_CARDS_SOURCE_ID, baseline.dataset_version
                    ).exists()
                )

    def test_missing_manifest_uses_freshest_immutable_for_ordering(self) -> None:
        with TemporaryDirectory() as directory:
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            freshest = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                    count=600,
                    backend="freshest-before-manifest-loss",
                ),
                store=store,
            )
            store.published_path(STANDARD_CARDS_SOURCE_ID).unlink()

            older = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                    count=600,
                    backend="older-after-manifest-loss",
                ),
                store=store,
            )

            self.assertTrue(freshest.accepted)
            self.assertFalse(older.accepted)
            self.assertEqual(older.rejection_kind, "obsolete")
            self.assertFalse(
                store.version_path(
                    STANDARD_CARDS_SOURCE_ID, older.dataset_version
                ).exists()
            )
            self.assertTrue(
                store.version_path(
                    STANDARD_CARDS_SOURCE_ID, freshest.dataset_version
                ).exists()
            )

    def test_public_read_is_linearizable_against_retention_pruning(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_STANDARD_CARDS_PUBLICATION_RETENTION": "2"},
            clear=False,
        ):
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            store = DatasetPublicationStore(Path(directory))
            first_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=3),
                count=600,
                backend="v1",
            )
            first = validate_and_publish_standard_cards_candidate(
                source, first_dataset, store=store
            )
            reader_before_version_open = threading.Event()
            release_reader = threading.Event()
            publisher_done = threading.Event()
            reader_results: list[dict] = []
            failures: list[BaseException] = []
            original_verify = DatasetPublicationStore._verified_version_record

            def block_reader(self, source_id, version, *, reason="published_corrupt"):
                if (
                    threading.current_thread().name == "publication-reader"
                    and version == first.dataset_version
                ):
                    reader_before_version_open.set()
                    if not release_reader.wait(3.0):
                        raise AssertionError("test did not release publication reader")
                return original_verify(self, source_id, version, reason=reason)

            def read_publication() -> None:
                try:
                    dataset = DatasetPublicationStore(Path(directory)).read_published_unbounded(
                        STANDARD_CARDS_SOURCE_ID
                    )
                    assert dataset is not None
                    reader_results.append(dataset)
                except BaseException as exc:
                    failures.append(exc)

            def publish_two_generations() -> None:
                try:
                    for index in (2, 3):
                        validate_and_publish_standard_cards_candidate(
                            source,
                            _dataset(
                                fetched_at=datetime.now(UTC)
                                - timedelta(minutes=3 - index),
                                count=600,
                                backend=f"v{index}",
                            ),
                            store=DatasetPublicationStore(Path(directory)),
                        )
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    publisher_done.set()

            with patch.object(
                DatasetPublicationStore,
                "_verified_version_record",
                new=block_reader,
            ):
                reader = threading.Thread(
                    target=read_publication, name="publication-reader"
                )
                publisher = threading.Thread(target=publish_two_generations)
                reader.start()
                self.assertTrue(reader_before_version_open.wait(2.0))
                publisher.start()
                try:
                    self.assertFalse(
                        publisher_done.wait(0.2),
                        "publisher pruned a version while a reader held its snapshot",
                    )
                finally:
                    release_reader.set()
                reader.join(timeout=3.0)
                publisher.join(timeout=3.0)

            self.assertFalse(reader.is_alive())
            self.assertFalse(publisher.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(len(reader_results), 1)
            self.assertEqual(reader_results[0]["backend"], "v1")

    def test_prune_failure_does_not_invert_committed_publication(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_STANDARD_CARDS_PUBLICATION_RETENTION": "2"},
            clear=False,
        ):
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            decisions = []
            for index in range(2):
                decisions.append(
                    validate_and_publish_standard_cards_candidate(
                        source,
                        _dataset(
                            fetched_at=datetime.now(UTC)
                            - timedelta(minutes=2 - index),
                            count=600,
                            backend=f"initial-{index}",
                        ),
                        store=store,
                    )
                )
            stale_path = store.version_path(
                STANDARD_CARDS_SOURCE_ID, decisions[0].dataset_version
            )
            original_unlink = Path.unlink

            def fail_only_stale(path: Path, *args, **kwargs):
                if path == stale_path:
                    raise OSError("simulated retention cleanup failure")
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", new=fail_only_stale):
                committed = validate_and_publish_standard_cards_candidate(
                    source,
                    _dataset(count=600, backend="committed"),
                    store=store,
                )

            self.assertTrue(committed.accepted)
            self.assertEqual(
                store.current_dataset_version(STANDARD_CARDS_SOURCE_ID),
                committed.dataset_version,
            )
            self.assertEqual(
                len(store.list_published_versions(STANDARD_CARDS_SOURCE_ID)), 2
            )
            self.assertTrue(stale_path.exists(), "failed cleanup should leave an orphan")

    def test_corrupt_current_cache_falls_back_to_lkg_without_exposing_candidate(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            store = DatasetPublicationStore(Path(directory))
            valid = _dataset()
            candidate = store.stage_candidate(STANDARD_CARDS_SOURCE_ID, valid)
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                candidate["dataset_version"],
                validation={"ok": True, "reason": "ok", "diagnostics": {}},
            )
            store.stage_candidate(
                STANDARD_CARDS_SOURCE_ID,
                _dataset(popularity="99%", backend="broken-refresh"),
            )

            published = store.read_published(
                STANDARD_CARDS_SOURCE_ID,
                current_error=ValueError("corrupt json"),
                status={"state": "quality_error"},
            )

            self.assertTrue(published.stale)
            self.assertEqual(published.fallback_reason, "current_dataset_corrupt")
            cards = published.dataset["data"]["structured"]["cards"]
            self.assertEqual(cards[0]["deck_popularity"], "12.5%")

    def test_public_representation_schema_version_participates_in_revision(self) -> None:
        with TemporaryDirectory() as directory:
            store = DatasetPublicationStore(Path(directory))
            dataset = _dataset(count=600)
            candidate = store.stage_candidate(STANDARD_CARDS_SOURCE_ID, dataset)
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                candidate["dataset_version"],
                validation={"ok": True, "reason": "ok", "diagnostics": {}},
            )
            first = store.read_published(
                STANDARD_CARDS_SOURCE_ID,
                current_dataset=dataset,
                status={"state": "ok"},
            )

            with patch(
                "app.dataset_publication_store.PUBLIC_STANDARD_CARDS_REPRESENTATION_SCHEMA_VERSION",
                "2",
            ):
                second = store.read_published(
                    STANDARD_CARDS_SOURCE_ID,
                    current_dataset=dataset,
                    status={"state": "ok"},
                )

            self.assertNotEqual(
                first.representation_revision,
                second.representation_revision,
            )

    def test_corrupt_or_expired_lkg_is_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            store = DatasetPublicationStore(Path(directory))
            old = _dataset(fetched_at=datetime.now(UTC) - timedelta(hours=37))
            candidate = store.stage_candidate(STANDARD_CARDS_SOURCE_ID, old)
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                candidate["dataset_version"],
                validation={"ok": True, "reason": "ok", "diagnostics": {}},
            )

            with self.assertRaisesRegex(PublicationUnavailable, "published_too_old"):
                store.read_published(STANDARD_CARDS_SOURCE_ID, max_stale_hours=36)

            store.published_path(STANDARD_CARDS_SOURCE_ID).write_text(
                "{broken", encoding="utf-8"
            )
            with self.assertRaisesRegex(PublicationUnavailable, "published_corrupt"):
                store.read_published(STANDARD_CARDS_SOURCE_ID, max_stale_hours=36)

    def test_future_timestamp_beyond_clock_skew_is_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            store = DatasetPublicationStore(Path(directory))
            future = _dataset(fetched_at=datetime.now(UTC) + timedelta(minutes=6))
            candidate = store.stage_candidate(STANDARD_CARDS_SOURCE_ID, future)
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                candidate["dataset_version"],
                validation={"ok": True, "reason": "ok", "diagnostics": {}},
            )

            with self.assertRaisesRegex(PublicationUnavailable, "published_from_future"):
                store.read_published(STANDARD_CARDS_SOURCE_ID)

    def test_non_finite_stale_limits_fall_back_to_safe_default(self) -> None:
        with TemporaryDirectory() as directory:
            store = DatasetPublicationStore(Path(directory))
            old = _dataset(fetched_at=datetime.now(UTC) - timedelta(hours=40))
            candidate = store.stage_candidate(STANDARD_CARDS_SOURCE_ID, old)
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                candidate["dataset_version"],
                validation={"ok": True, "reason": "ok", "diagnostics": {}},
            )

            for configured in ("nan", "inf", "-inf"):
                with self.subTest(configured=configured), patch.dict(
                    os.environ,
                    {"HS_STANDARD_CARDS_MAX_STALE_HOURS": configured},
                    clear=False,
                ):
                    with self.assertRaisesRegex(
                        PublicationUnavailable, "published_too_old"
                    ):
                        store.read_published(STANDARD_CARDS_SOURCE_ID)

            for supplied in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(supplied=supplied):
                    with self.assertRaisesRegex(
                        PublicationUnavailable, "published_too_old"
                    ):
                        store.read_published(
                            STANDARD_CARDS_SOURCE_ID,
                            max_stale_hours=supplied,
                        )

    def test_invalid_candidates_enter_bounded_quarantine_with_diagnostics(self) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {"HS_STANDARD_CARDS_QUARANTINE_LIMIT": "2"},
                clear=False,
            ),
        ):
            store = DatasetPublicationStore(Path(directory))
            for index in range(3):
                candidate = store.stage_candidate(
                    STANDARD_CARDS_SOURCE_ID,
                    _dataset(backend=f"test-{index}"),
                )
                store.quarantine_candidate(
                    STANDARD_CARDS_SOURCE_ID,
                    candidate["dataset_version"],
                    reason=f"bad-{index}",
                    diagnostics={"cascade_count": 10 + index},
                )

            rows = store.list_quarantine(STANDARD_CARDS_SOURCE_ID)

            self.assertEqual(len(rows), 2)
            self.assertEqual({row["reason"] for row in rows}, {"bad-1", "bad-2"})
            self.assertEqual({row["backend"] for row in rows}, {"test-1", "test-2"})
            self.assertTrue(all(row.get("fetched_at") for row in rows))
            self.assertTrue(all(row.get("diagnostics") for row in rows))

    def test_quarantine_prune_failure_does_not_invert_durable_rejection(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_STANDARD_CARDS_QUARANTINE_LIMIT": "1"},
            clear=False,
        ):
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            first = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(count=600, popularity="99%", backend="rejected-one"),
                store=store,
            )
            first_path = next(store.quarantine_dir(source.id).glob("*.json"))
            original_unlink = Path.unlink

            def fail_only_old_quarantine(path: Path, *args, **kwargs):
                if path == first_path:
                    raise OSError("simulated quarantine retention failure")
                return original_unlink(path, *args, **kwargs)

            with (
                patch.object(Path, "unlink", new=fail_only_old_quarantine),
                patch("app.refresh_log.log_action") as audit,
            ):
                second = validate_and_publish_standard_cards_candidate(
                    source,
                    _dataset(
                        count=600,
                        popularity="99%",
                        backend="rejected-two",
                    ),
                    store=store,
                )

            self.assertFalse(first.accepted)
            self.assertFalse(second.accepted)
            self.assertTrue(first_path.exists(), "cleanup failure should leave an orphan")
            self.assertEqual(len(store.list_quarantine(source.id)), 2)
            self.assertTrue(
                any(
                    call.args and call.args[0] == "dataset.quarantine.prune.fail"
                    for call in audit.call_args_list
                )
            )

    def test_regression_is_quarantined_and_does_not_replace_published_lkg(self) -> None:
        with TemporaryDirectory() as directory:
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            first = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(count=1300),
                store=store,
            )
            regressed = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(count=600),
                store=store,
            )

            self.assertTrue(first.accepted)
            self.assertFalse(regressed.accepted)
            self.assertEqual(regressed.rejection_kind, "regression")
            published = store.read_published_unbounded(STANDARD_CARDS_SOURCE_ID)
            self.assertIsNotNone(published)
            assert published is not None
            self.assertEqual(
                len(published["data"]["structured"]["cards"]),
                1300,
            )
            quarantine = store.list_quarantine(STANDARD_CARDS_SOURCE_ID)
            self.assertEqual(len(quarantine), 1)
            self.assertIn("regression", quarantine[0]["reason"].lower())


class StandardCardsFetcherPublicationTest(unittest.IsolatedAsyncioTestCase):
    async def test_ai_pass_promotes_standard_cards_candidate_exactly_once(self) -> None:
        from app.ai_review import AIPageVerdict, AIReviewResult
        from app.dataset_publication_store import (
            validate_and_publish_standard_cards_candidate,
        )
        from app.fetcher import fetch_source

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_FETCH_REQUIRE_PROXY": "false",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                "HS_AI_REVIEW_ENABLED": "true",
                "HS_AI_REVIEW_MODE": "quarantine",
                "HS_AI_REVIEW_SOURCE_IDS": STANDARD_CARDS_SOURCE_ID,
            },
            clear=False,
        ):
            valid = _parsed_payload(count=600)
            valid["_backend"] = "hsreplay_cards_api"
            reviewer_result = AIReviewResult(
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
                    summary="Candidate matches the Standard cards contract.",
                ),
            )
            with (
                patch(
                    "app.fetcher._fetch_hsreplay_api_source",
                    new_callable=AsyncMock,
                    return_value=valid,
                ),
                patch(
                    "app.ai_review.review_candidate",
                    new_callable=AsyncMock,
                    return_value=reviewer_result,
                ) as reviewer,
                patch(
                    "app.dataset_publication_store.validate_and_publish_standard_cards_candidate",
                    wraps=validate_and_publish_standard_cards_candidate,
                ) as promote,
                patch("app.fetcher.log_action"),
            ):
                result = await fetch_source(
                    None,
                    SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                )

            reviewer.assert_awaited_once()
            promote.assert_called_once()
            self.assertEqual(result["state"], "ok")
            self.assertFalse(result.get("serving_cached_dataset", False))
            self.assertEqual(result["ai_review"]["verdict"], "pass")
            published = DatasetPublicationStore(
                Path(directory)
            ).read_published_unbounded(STANDARD_CARDS_SOURCE_ID)
            self.assertIsNotNone(published)
            assert published is not None
            self.assertEqual(len(published["data"]["structured"]["cards"]), 600)

    async def test_ai_quarantine_skips_promotion_and_serves_existing_lkg(self) -> None:
        from app.ai_review import AIPageVerdict, AIReviewResult
        from app.dataset_publication_store import (
            validate_and_publish_standard_cards_candidate,
        )
        from app.fetcher import fetch_source
        from app.reliability_telemetry import classify_terminal_status

        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_FETCH_REQUIRE_PROXY": "false",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                "HS_AI_REVIEW_ENABLED": "true",
                "HS_AI_REVIEW_MODE": "quarantine",
                "HS_AI_REVIEW_CONFIDENCE_THRESHOLD": "0.90",
                "HS_AI_REVIEW_SOURCE_IDS": STANDARD_CARDS_SOURCE_ID,
            },
            clear=False,
        ):
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            store = DatasetPublicationStore(Path(directory))
            seeded = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(count=600, backend="seeded-lkg"),
                store=store,
            )
            self.assertTrue(seeded.accepted)
            original_version = store.pointer_dataset_version(source.id)
            original_manifest = store.published_path(source.id).read_bytes()
            original_candidate = store.candidate_path(source.id).read_bytes()

            candidate = _parsed_payload(count=700)
            candidate["_backend"] = "hsreplay_cards_api"
            reviewer_result = AIReviewResult(
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
                    summary="Candidate does not match the expected page.",
                ),
            )
            with (
                patch(
                    "app.fetcher._fetch_hsreplay_api_source",
                    new_callable=AsyncMock,
                    return_value=candidate,
                ),
                patch(
                    "app.ai_review.review_candidate",
                    new_callable=AsyncMock,
                    return_value=reviewer_result,
                ) as reviewer,
                patch(
                    "app.dataset_publication_store.validate_and_publish_standard_cards_candidate",
                    wraps=validate_and_publish_standard_cards_candidate,
                ) as promote,
                patch("app.fetcher.log_action"),
            ):
                result = await fetch_source(None, source)

            reviewer.assert_awaited_once()
            promote.assert_not_called()
            self.assertEqual(store.pointer_dataset_version(source.id), original_version)
            self.assertEqual(store.published_path(source.id).read_bytes(), original_manifest)
            self.assertEqual(store.candidate_path(source.id).read_bytes(), original_candidate)
            self.assertEqual(store.list_quarantine(source.id), [])
            published = store.read_published_unbounded(source.id)
            self.assertIsNotNone(published)
            assert published is not None
            self.assertEqual(len(published["data"]["structured"]["cards"]), 600)
            self.assertEqual(result["state"], "ok")
            self.assertTrue(result["serving_cached_dataset"])
            self.assertEqual(result["last_refresh_state"], "quality_error")
            self.assertTrue(result["ai_review"]["quarantine"])
            self.assertEqual(classify_terminal_status(result), "lkg_served")

    async def test_fetcher_promotes_valid_candidate_and_quarantines_cascade(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_FETCH_REQUIRE_PROXY": "false",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ),
        ):
            valid = _parsed_payload()
            valid["_backend"] = "hsreplay_cards_api"
            cascade = _parsed_payload(popularity="99%")
            cascade["_backend"] = "hsreplay_cards_api"
            with (
                patch(
                    "app.fetcher._fetch_hsreplay_api_source",
                    new_callable=AsyncMock,
                    side_effect=[valid, cascade],
                ),
                patch("app.fetcher.log_action"),
            ):
                from app.fetcher import fetch_source

                source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
                accepted = await fetch_source(None, source)
                rejected = await fetch_source(None, source)

            self.assertEqual(accepted["state"], "ok")
            self.assertEqual(rejected["state"], "ok")
            self.assertTrue(rejected["serving_cached_dataset"])
            self.assertEqual(rejected["last_refresh_state"], "quality_error")
            store = DatasetPublicationStore(Path(directory))
            published = store.read_published_unbounded(STANDARD_CARDS_SOURCE_ID)
            self.assertIsNotNone(published)
            assert published is not None
            self.assertEqual(
                published["data"]["structured"]["cards"][0]["deck_popularity"],
                "12.5%",
            )
            quarantine = store.list_quarantine(STANDARD_CARDS_SOURCE_ID)
            self.assertEqual(len(quarantine), 1)
            self.assertIn("systemic", quarantine[0]["reason"])
            public_read = store.read_published(STANDARD_CARDS_SOURCE_ID)
            self.assertTrue(public_read.stale)
            self.assertEqual(
                public_read.fallback_reason, "latest_refresh_failed:quality_error"
            )

    async def test_fetcher_returns_degraded_success_after_postcommit_sync_failure(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_FETCH_REQUIRE_PROXY": "false",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            valid = _parsed_payload()
            valid["_backend"] = "hsreplay_cards_api"
            with (
                patch(
                    "app.fetcher._fetch_hsreplay_api_source",
                    new_callable=AsyncMock,
                    return_value=valid,
                ),
                patch(
                    "app.dataset_publication_store.DatasetPublicationStore.reconcile_current_publication",
                    side_effect=RuntimeError("injected postcommit sync failure"),
                ),
                patch("app.fetcher.log_action"),
            ):
                from app.fetcher import fetch_source

                source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
                result = await fetch_source(None, source)

            store = DatasetPublicationStore(Path(directory))
            self.assertEqual(result["state"], "ok")
            self.assertFalse(result["cache_synced"])
            self.assertFalse(result["status_synced"])
            self.assertIn("injected postcommit", result["warnings"][0])
            self.assertEqual(
                store.pointer_dataset_version(source.id), result["dataset_version"]
            )

    async def test_corrupt_status_cannot_leak_publication_attempt_or_abort_fetch(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_FETCH_REQUIRE_PROXY": "false",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app import storage
            from app.fetcher import _standard_publication_attempt, fetch_source

            storage.status_path(STANDARD_CARDS_SOURCE_ID).write_text(
                "{broken", encoding="utf-8"
            )
            valid = _parsed_payload()
            valid["_backend"] = "hsreplay_cards_api"
            with (
                patch(
                    "app.fetcher._fetch_hsreplay_api_source",
                    new_callable=AsyncMock,
                    return_value=valid,
                ),
                patch("app.fetcher.log_action"),
            ):
                result = await fetch_source(
                    None, SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
                )

            self.assertEqual(result["state"], "ok")
            self.assertIsNone(_standard_publication_attempt.get())

    async def test_postcommit_log_and_trace_failures_do_not_change_success(self) -> None:
        for failure_target in ("route_log_once", "route_log_always", "trace_once", "trace_always"):
            with self.subTest(failure_target=failure_target), TemporaryDirectory() as directory, patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_FETCH_REQUIRE_PROXY": "false",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ):
                from app.fetcher import _standard_publication_attempt, fetch_source

                valid = _parsed_payload()
                valid["_backend"] = "hsreplay_cards_api"
                failures = 0

                def fail_route_log(action: str, *args, **kwargs) -> None:
                    nonlocal failures
                    if action != "api.route.ok":
                        return
                    if failure_target == "route_log_once" and failures:
                        return
                    failures += 1
                    raise RuntimeError(f"injected {failure_target}")

                trace_failure = RuntimeError(f"injected {failure_target}")
                with (
                    patch(
                        "app.fetcher._fetch_hsreplay_api_source",
                        new_callable=AsyncMock,
                        return_value=valid,
                    ),
                    patch(
                        "app.fetcher.log_action",
                        side_effect=(
                            fail_route_log
                            if failure_target.startswith("route_log")
                            else None
                        ),
                    ),
                    patch(
                        "app.fetcher.complete_source_trace",
                        side_effect=(
                            trace_failure
                            if failure_target.startswith("trace")
                            else None
                        ),
                    ),
                ):
                    result = await fetch_source(
                        None, SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
                    )

                self.assertEqual(result["state"], "ok")
                self.assertFalse(result.get("serving_cached_dataset", False))
                self.assertNotIn("last_refresh_state", result)
                self.assertIsNone(_standard_publication_attempt.get())
                self.assertIsNotNone(
                    DatasetPublicationStore(Path(directory)).read_published_unbounded(
                        STANDARD_CARDS_SOURCE_ID
                    )
                )


class StandardCardsPublicationApiTest(unittest.TestCase):
    def test_bootstrap_rebuilds_missing_or_corrupt_manifest_from_revalidated_immutable(
        self,
    ) -> None:
        for manifest_state in ("missing", "corrupt"):
            for cache_state in ("missing", "exact", "invalid"):
                with self.subTest(
                    manifest_state=manifest_state, cache_state=cache_state
                ), TemporaryDirectory() as directory, patch.dict(
                    os.environ,
                    {
                        "HS_API_DATA_DIR": directory,
                        "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                    },
                    clear=False,
                ):
                    from app import main, storage
                    from app.dataset_publication_store import (
                        validate_and_publish_standard_cards_candidate,
                    )

                    dataset = _dataset(count=600, backend="bootstrap-immutable")
                    store = DatasetPublicationStore(Path(directory))
                    original = validate_and_publish_standard_cards_candidate(
                        SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                        dataset,
                        store=store,
                    )
                    self.assertTrue(original.accepted)
                    if cache_state == "exact":
                        storage.save_dataset(STANDARD_CARDS_SOURCE_ID, dataset)
                    elif cache_state == "invalid":
                        storage.save_dataset(
                            STANDARD_CARDS_SOURCE_ID,
                            _dataset(
                                count=600,
                                popularity="99%",
                                backend="invalid-mutable-bootstrap",
                            ),
                        )
                    else:
                        storage.dataset_path(STANDARD_CARDS_SOURCE_ID).unlink(
                            missing_ok=True
                        )
                    manifest_path = store.published_path(STANDARD_CARDS_SOURCE_ID)
                    if manifest_state == "missing":
                        manifest_path.unlink()
                    else:
                        manifest = json.loads(
                            manifest_path.read_text(encoding="utf-8")
                        )
                        manifest["manifest_checksum"] = "0" * 64
                        manifest_path.write_text(
                            json.dumps(manifest), encoding="utf-8"
                        )

                    with patch("app.refresh_log.log_action"):
                        main.bootstrap_standard_cards_publication()

                    published = store.read_published_unbounded(
                        STANDARD_CARDS_SOURCE_ID
                    )
                    history = store.list_published_versions(
                        STANDARD_CARDS_SOURCE_ID
                    )
                    self.assertIsNotNone(published)
                    assert published is not None
                    self.assertEqual(published["backend"], "bootstrap-immutable")
                    self.assertEqual(history[0]["dataset_version"], original.dataset_version)

    def test_bootstrap_selects_freshest_fully_valid_recovery_candidate(self) -> None:
        for manifest_state in ("missing", "corrupt"):
            for immutable_kind in ("expired", "semantic_invalid", "older_valid"):
                with self.subTest(
                    manifest_state=manifest_state,
                    immutable_kind=immutable_kind,
                ), TemporaryDirectory() as directory, patch.dict(
                    os.environ,
                    {
                        "HS_API_DATA_DIR": directory,
                        "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                    },
                    clear=False,
                ):
                    from app import main, storage

                    store = DatasetPublicationStore(Path(directory))
                    immutable = _dataset(
                        fetched_at=(
                            datetime.now(UTC) - timedelta(hours=40)
                            if immutable_kind == "expired"
                            else datetime.now(UTC) - timedelta(minutes=2)
                        ),
                        count=600,
                        popularity=(
                            "99%" if immutable_kind == "semantic_invalid" else "12.5%"
                        ),
                        backend=f"bootstrap-{immutable_kind}-immutable",
                    )
                    candidate = store.stage_candidate(source_id=STANDARD_CARDS_SOURCE_ID, dataset=immutable)
                    store.promote_candidate(
                        STANDARD_CARDS_SOURCE_ID,
                        candidate["dataset_version"],
                        validation={"ok": True, "reason": "fixture", "diagnostics": {}},
                    )
                    mutable = _dataset(count=600, backend="bootstrap-newer-mutable")
                    storage.save_dataset(STANDARD_CARDS_SOURCE_ID, mutable)
                    manifest_path = store.published_path(STANDARD_CARDS_SOURCE_ID)
                    if manifest_state == "missing":
                        manifest_path.unlink()
                    else:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        manifest["manifest_checksum"] = "0" * 64
                        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

                    with patch("app.refresh_log.log_action"):
                        main.bootstrap_standard_cards_publication()

                    published = store.read_published_unbounded(
                        STANDARD_CARDS_SOURCE_ID
                    )
                    assert published is not None
                    self.assertEqual(published["backend"], "bootstrap-newer-mutable")
                    self.assertEqual(
                        store.pointer_dataset_version(STANDARD_CARDS_SOURCE_ID),
                        dataset_version(mutable),
                    )

    def test_bootstrap_selects_newer_fresh_orphan_for_expired_intact_pointer(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app import main, storage

            store = DatasetPublicationStore(Path(directory))
            expired = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(hours=40),
                count=600,
                backend="bootstrap-expired-current",
            )
            expired_candidate = store.stage_candidate(
                STANDARD_CARDS_SOURCE_ID, expired
            )
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                expired_candidate["dataset_version"],
                validation={"ok": True, "reason": "fixture", "diagnostics": {}},
            )
            orphan = _dataset(count=600, backend="bootstrap-fresh-orphan")
            orphan_version = dataset_version(orphan)
            store.version_path(STANDARD_CARDS_SOURCE_ID, orphan_version).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_id": STANDARD_CARDS_SOURCE_ID,
                        "dataset_version": orphan_version,
                        "published_at": datetime.now(UTC).isoformat(),
                        "validation": {
                            "ok": True,
                            "reason": "fixture",
                            "diagnostics": {},
                        },
                        "dataset": orphan,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            storage.dataset_path(STANDARD_CARDS_SOURCE_ID).unlink(missing_ok=True)

            with patch("app.refresh_log.log_action"):
                main.bootstrap_standard_cards_publication()

            self.assertEqual(
                store.pointer_dataset_version(STANDARD_CARDS_SOURCE_ID),
                orphan_version,
            )
            published = store.read_published_unbounded(STANDARD_CARDS_SOURCE_ID)
            assert published is not None
            self.assertEqual(published["backend"], "bootstrap-fresh-orphan")

    def test_bootstrap_audit_failure_cannot_invert_committed_recovery(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app import main, storage

            legacy = _dataset(count=600, backend="bootstrap-audit-failure")
            storage.save_dataset(STANDARD_CARDS_SOURCE_ID, legacy)

            def fail_final_audit(action: str, *args, **kwargs) -> None:
                if action == "dataset.publication.bootstrap.ok":
                    raise RuntimeError("injected final bootstrap audit failure")

            with patch(
                "app.refresh_log.log_action", side_effect=fail_final_audit
            ):
                main.bootstrap_standard_cards_publication()

            self.assertEqual(
                DatasetPublicationStore(Path(directory)).pointer_dataset_version(
                    STANDARD_CARDS_SOURCE_ID
                ),
                dataset_version(legacy),
            )

    def test_bootstrap_recovers_from_invalid_future_pointer(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app import main, storage

            store = DatasetPublicationStore(Path(directory))
            future = _dataset(
                fetched_at=datetime.now(UTC) + timedelta(minutes=6),
                count=600,
                backend="bootstrap-invalid-future-current",
            )
            candidate = store.stage_candidate(STANDARD_CARDS_SOURCE_ID, future)
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                candidate["dataset_version"],
                validation={"ok": True, "reason": "fixture", "diagnostics": {}},
            )
            recovery = _dataset(
                fetched_at=datetime.now(UTC),
                count=600,
                backend="bootstrap-safe-nonfuture-recovery",
            )
            storage.save_dataset(STANDARD_CARDS_SOURCE_ID, recovery)

            with patch("app.refresh_log.log_action"):
                main.bootstrap_standard_cards_publication()

            self.assertEqual(
                store.pointer_dataset_version(STANDARD_CARDS_SOURCE_ID),
                dataset_version(recovery),
            )

    def test_bootstrap_invalid_immutable_is_not_used_as_gate_baseline(self) -> None:
        for invalid_kind in ("newer_semantic", "newer_huge", "future"):
            with self.subTest(invalid_kind=invalid_kind), TemporaryDirectory() as directory, patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ):
                from app import main, storage

                store = DatasetPublicationStore(Path(directory))
                invalid = _dataset(
                    fetched_at=(
                        datetime.now(UTC) + timedelta(minutes=6)
                        if invalid_kind == "future"
                        else datetime.now(UTC)
                    ),
                    count=2000 if invalid_kind == "newer_huge" else 600,
                    popularity=(
                        "99%" if invalid_kind == "newer_semantic" else "12.5%"
                    ),
                    backend=f"bootstrap-invalid-baseline-{invalid_kind}",
                )
                invalid_candidate = store.stage_candidate(
                    STANDARD_CARDS_SOURCE_ID, invalid
                )
                store.promote_candidate(
                    STANDARD_CARDS_SOURCE_ID,
                    invalid_candidate["dataset_version"],
                    validation={"ok": True, "reason": "fixture", "diagnostics": {}},
                )
                store.published_path(STANDARD_CARDS_SOURCE_ID).unlink()
                recovery = _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                    count=600,
                    backend=f"bootstrap-valid-recovery-{invalid_kind}",
                )
                storage.save_dataset(STANDARD_CARDS_SOURCE_ID, recovery)

                with patch("app.refresh_log.log_action"):
                    main.bootstrap_standard_cards_publication()

                self.assertEqual(
                    store.pointer_dataset_version(STANDARD_CARDS_SOURCE_ID),
                    dataset_version(recovery),
                )

    def test_admin_can_revalidate_and_roll_back_to_retained_previous_version(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ),
        ):
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )
            from app.storage import save_dataset

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            previous_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                count=600,
                backend="previous",
            )
            current_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                count=600,
                backend="current",
            )
            previous = validate_and_publish_standard_cards_candidate(
                source, previous_dataset, store=store
            )
            current = validate_and_publish_standard_cards_candidate(
                source, current_dataset, store=store
            )
            save_dataset(STANDARD_CARDS_SOURCE_ID, current_dataset)

            with patch("app.refresh_log.log_action") as audit, TestClient(app) as client:
                denied = client.post(
                    f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}/publication/rollback",
                    json={"datasetVersion": previous.dataset_version},
                )
                rolled_back = client.post(
                    f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}/publication/rollback",
                    headers={"X-API-Key": "secret"},
                    json={"datasetVersion": previous.dataset_version},
                )
                public = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")

            self.assertTrue(previous.accepted)
            self.assertTrue(current.accepted)
            self.assertEqual(denied.status_code, 401)
            self.assertEqual(rolled_back.status_code, 200, rolled_back.text)
            self.assertEqual(rolled_back.headers["cache-control"], "private, no-store")
            self.assertEqual(
                rolled_back.json()["dataset_version"], previous.dataset_version
            )
            self.assertEqual(public.status_code, 200, public.text)
            self.assertEqual(public.json()["backend"], "previous")
            self.assertEqual(
                public.json()["publication"]["dataset_version"],
                previous.dataset_version,
            )
            restarted = DatasetPublicationStore(Path(directory))
            self.assertEqual(
                restarted.list_published_versions(STANDARD_CARDS_SOURCE_ID)[0][
                    "dataset_version"
                ],
                previous.dataset_version,
            )
            self.assertTrue(
                any(
                    call.args and call.args[0] == "dataset.publication.rollback"
                    for call in audit.call_args_list
                )
            )

    def test_admin_upload_returns_degraded_success_after_postcommit_sync_failure(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_API_KEY": "secret",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ), TestClient(app) as client, patch(
            "app.dataset_publication_store.DatasetPublicationStore.reconcile_current_publication",
            side_effect=RuntimeError("injected admin postcommit sync failure"),
        ):
            response = client.put(
                f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                headers={"X-API-Key": "secret"},
                json=_parsed_payload(),
            )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["ok"])
            self.assertTrue(response.json()["degraded"])
            self.assertFalse(response.json()["cache_synced"])
            self.assertFalse(response.json()["status_synced"])
            self.assertIn("injected admin postcommit", response.json()["warnings"][0])
            self.assertEqual(
                DatasetPublicationStore(Path(directory)).pointer_dataset_version(
                    STANDARD_CARDS_SOURCE_ID
                ),
                response.json()["dataset_version"],
            )

    def test_admin_upload_audits_accept_reject_and_surfaces_audit_failure(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_API_KEY": "secret",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ), TestClient(app) as client:
            with patch("app.refresh_log.log_action") as audit:
                accepted = client.put(
                    f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                    headers={"X-API-Key": "secret"},
                    json=_parsed_payload(),
                )
                rejected = client.put(
                    f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                    headers={"X-API-Key": "secret"},
                    json=_parsed_payload(popularity="99%"),
                )

            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(rejected.status_code, 422, rejected.text)
            calls = {
                call.args[0]: call
                for call in audit.call_args_list
                if call.args
                and call.args[0].startswith("dataset.publication.admin_upload")
            }
            self.assertIn("dataset.publication.admin_upload.accept", calls)
            self.assertIn("dataset.publication.admin_upload.reject", calls)
            for call in calls.values():
                self.assertEqual(call.kwargs["extra"]["actor"], "admin_api_key")
                self.assertTrue(call.kwargs["extra"]["candidate_dataset_version"])
                self.assertIn("cache_synced", call.kwargs["extra"])
                self.assertIn("status_synced", call.kwargs["extra"])

            def fail_upload_audit(action: str, *args, **kwargs) -> None:
                if action == "dataset.publication.admin_upload.accept":
                    raise RuntimeError("injected admin upload audit failure")

            newer_payload = _parsed_payload()
            newer_payload["title"] = "accepted despite audit failure"
            with patch(
                "app.refresh_log.log_action", side_effect=fail_upload_audit
            ):
                audit_failed = client.put(
                    f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                    headers={"X-API-Key": "secret"},
                    json=newer_payload,
                )

            self.assertEqual(audit_failed.status_code, 200, audit_failed.text)
            self.assertFalse(audit_failed.json()["audit_recorded"])
            self.assertIn("injected admin upload audit", audit_failed.json()["warnings"][-1])

    def test_rollback_returns_degraded_success_after_postcommit_sync_failure(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_API_KEY": "secret",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            previous = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                    count=600,
                    backend="postcommit-previous",
                ),
                store=store,
            )
            current = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                    count=600,
                    backend="postcommit-current",
                ),
                store=store,
            )
            self.assertTrue(previous.accepted)
            self.assertTrue(current.accepted)
            with TestClient(app) as client, patch(
                "app.dataset_publication_store.DatasetPublicationStore.reconcile_current_publication",
                side_effect=RuntimeError("injected rollback postcommit sync failure"),
            ):
                response = client.post(
                    f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}/publication/rollback",
                    headers={"X-API-Key": "secret"},
                    json={"datasetVersion": previous.dataset_version},
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["ok"])
            self.assertFalse(response.json()["cache_synced"])
            self.assertFalse(response.json()["status_synced"])
            self.assertIn("injected rollback postcommit", response.json()["warnings"][0])
            self.assertEqual(
                store.pointer_dataset_version(source.id), previous.dataset_version
            )

    def test_admin_rollback_rejects_retained_version_that_fails_revalidation(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ),
        ):
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )
            from app.storage import save_dataset

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            invalid = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                count=600,
                popularity="99%",
                backend="historical-invalid",
            )
            invalid_candidate = store.stage_candidate(
                STANDARD_CARDS_SOURCE_ID, invalid
            )
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                invalid_candidate["dataset_version"],
                validation={"ok": True, "reason": "legacy", "diagnostics": {}},
            )
            current_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                count=600,
                backend="current-valid",
            )
            current = validate_and_publish_standard_cards_candidate(
                source, current_dataset, store=store
            )
            save_dataset(STANDARD_CARDS_SOURCE_ID, current_dataset)

            with patch("app.refresh_log.log_action") as audit, TestClient(app) as client:
                rejected = client.post(
                    f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}/publication/rollback",
                    headers={"X-API-Key": "secret"},
                    json={},
                )

            self.assertTrue(current.accepted)
            self.assertEqual(rejected.status_code, 422, rejected.text)
            self.assertEqual(
                store.current_dataset_version(STANDARD_CARDS_SOURCE_ID),
                current.dataset_version,
            )
            self.assertFalse(
                any(
                    call.args and call.args[0] == "dataset.publication.rollback"
                    for call in audit.call_args_list
                )
            )
            self.assertTrue(
                any(
                    call.args
                    and call.args[0] == "dataset.publication.rollback.reject"
                    for call in audit.call_args_list
                )
            )
            rejection_audit = next(
                call
                for call in audit.call_args_list
                if call.args
                and call.args[0] == "dataset.publication.rollback.reject"
            )
            self.assertEqual(
                rejection_audit.kwargs["extra"]["to_dataset_version"],
                invalid_candidate["dataset_version"],
            )

    def test_admin_rollback_recovers_valid_n_minus_one_when_current_file_is_corrupt(
        self,
    ) -> None:
        for rollback_payload in ({}, {"explicit": True}):
            with self.subTest(rollback_payload=rollback_payload), TemporaryDirectory() as directory, patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ):
                from app.dataset_publication_store import (
                    validate_and_publish_standard_cards_candidate,
                )

                store = DatasetPublicationStore(Path(directory))
                source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
                previous = validate_and_publish_standard_cards_candidate(
                    source,
                    _dataset(
                        fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                        count=600,
                        backend="recoverable-n-minus-one",
                    ),
                    store=store,
                )
                current = validate_and_publish_standard_cards_candidate(
                    source,
                    _dataset(
                        fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                        count=600,
                        backend="corrupt-current",
                    ),
                    store=store,
                )
                store.version_path(
                    STANDARD_CARDS_SOURCE_ID, current.dataset_version
                ).write_text("{broken", encoding="utf-8")
                request_payload = (
                    {"datasetVersion": previous.dataset_version}
                    if rollback_payload
                    else {}
                )

                with TestClient(app) as client:
                    unavailable = client.get(
                        f"/datasets/{STANDARD_CARDS_SOURCE_ID}"
                    )
                    manifest_before = json.loads(
                        store.published_path(STANDARD_CARDS_SOURCE_ID).read_text(
                            encoding="utf-8"
                        )
                    )
                    recovered = client.post(
                        f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}/publication/rollback",
                        headers={"X-API-Key": "secret"},
                        json=request_payload,
                    )
                    public = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")

                self.assertEqual(unavailable.status_code, 503, unavailable.text)
                self.assertEqual(unavailable.headers["retry-after"], "60")
                self.assertEqual(
                    manifest_before["current_version"], current.dataset_version
                )
                self.assertEqual(recovered.status_code, 200, recovered.text)
                self.assertEqual(
                    recovered.json()["dataset_version"], previous.dataset_version
                )
                self.assertEqual(public.status_code, 200, public.text)
                self.assertEqual(public.json()["backend"], "recoverable-n-minus-one")
                history = DatasetPublicationStore(Path(directory)).list_published_versions(
                    STANDARD_CARDS_SOURCE_ID
                )
                self.assertEqual(history[0]["dataset_version"], previous.dataset_version)
                self.assertNotIn(
                    current.dataset_version,
                    {row["dataset_version"] for row in history},
                )

    def test_admin_rollback_sanitizes_unrelated_corrupt_retained_history(self) -> None:
        for explicit in (False, True):
            with self.subTest(explicit=explicit), TemporaryDirectory() as directory, patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ):
                from app.dataset_publication_store import (
                    validate_and_publish_standard_cards_candidate,
                )

                store = DatasetPublicationStore(Path(directory))
                source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
                versions = []
                for index in range(3):
                    versions.append(
                        validate_and_publish_standard_cards_candidate(
                            source,
                            _dataset(
                                fetched_at=datetime.now(UTC)
                                - timedelta(minutes=3 - index),
                                count=600,
                                backend=f"retained-{index + 1}",
                            ),
                            store=store,
                        )
                    )
                store.version_path(
                    STANDARD_CARDS_SOURCE_ID, versions[0].dataset_version
                ).write_text("{broken", encoding="utf-8")
                request_payload = (
                    {"datasetVersion": versions[1].dataset_version}
                    if explicit
                    else {}
                )

                with TestClient(app) as client:
                    response = client.post(
                        f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}/publication/rollback",
                        headers={"X-API-Key": "secret"},
                        json=request_payload,
                    )

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    response.json()["dataset_version"], versions[1].dataset_version
                )
                history = store.list_published_versions(STANDARD_CARDS_SOURCE_ID)
                self.assertEqual(history[0]["dataset_version"], versions[1].dataset_version)
                self.assertNotIn(
                    versions[0].dataset_version,
                    {row["dataset_version"] for row in history},
                )

    def test_admin_rollback_reconciles_after_corrupt_mutable_status(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_API_KEY": "secret",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app import storage
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            previous_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                count=600,
                backend="corrupt-status-rollback-target",
            )
            current_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                count=600,
                backend="corrupt-status-current",
            )
            previous = validate_and_publish_standard_cards_candidate(
                source, previous_dataset, store=store
            )
            validate_and_publish_standard_cards_candidate(
                source, current_dataset, store=store
            )
            storage.save_dataset(STANDARD_CARDS_SOURCE_ID, current_dataset)
            status_path = storage.status_path(STANDARD_CARDS_SOURCE_ID)
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text("{broken", encoding="utf-8")

            with patch("app.refresh_log.log_action") as audit, TestClient(app) as client:
                response = client.post(
                    f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}/publication/rollback",
                    headers={"X-API-Key": "secret"},
                    json={"datasetVersion": previous.dataset_version},
                )

            self.assertEqual(response.status_code, 200, response.text)
            published = store.read_published_unbounded(STANDARD_CARDS_SOURCE_ID)
            cached = storage.load_dataset(STANDARD_CARDS_SOURCE_ID)
            status = storage.load_status(STANDARD_CARDS_SOURCE_ID)
            assert published is not None and cached is not None and status is not None
            self.assertEqual(published["backend"], "corrupt-status-rollback-target")
            self.assertEqual(dataset_version(cached), previous.dataset_version)
            self.assertEqual(status["dataset_version"], previous.dataset_version)
            self.assertTrue(
                any(
                    call.args and call.args[0] == "dataset.publication.rollback"
                    for call in audit.call_args_list
                )
            )

    def test_upload_publication_and_cache_status_reconciliation_are_serialized(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_API_KEY": "secret",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ), TestClient(app) as client:
            from app import storage

            entered_sync = threading.Event()
            release_sync = threading.Event()
            second_done = threading.Event()
            call_lock = threading.Lock()
            call_count = 0
            failures: list[BaseException] = []
            responses: dict[str, object] = {}
            real_save_dataset = storage.save_dataset

            def block_first_cache_sync(source_id: str, payload: dict) -> None:
                nonlocal call_count
                with call_lock:
                    call_count += 1
                    should_block = call_count == 1
                if should_block:
                    entered_sync.set()
                    if not release_sync.wait(3.0):
                        raise AssertionError("test did not release cache sync")
                real_save_dataset(source_id, payload)

            first_payload = _parsed_payload()
            first_payload["title"] = "serialized-upload-a"
            second_payload = _parsed_payload()
            second_payload["title"] = "serialized-upload-b"

            def upload(name: str, payload: dict, done: threading.Event | None = None) -> None:
                try:
                    responses[name] = client.put(
                        f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                        headers={"X-API-Key": "secret"},
                        json=payload,
                    )
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    if done is not None:
                        done.set()

            with patch("app.storage.save_dataset", side_effect=block_first_cache_sync):
                first = threading.Thread(target=upload, args=("first", first_payload))
                second = threading.Thread(
                    target=upload,
                    args=("second", second_payload, second_done),
                )
                first.start()
                self.assertTrue(entered_sync.wait(2.0))
                second.start()
                try:
                    self.assertFalse(
                        second_done.wait(0.2),
                        "a newer publish passed the older cache/status sync",
                    )
                finally:
                    release_sync.set()
                first.join(timeout=3.0)
                second.join(timeout=3.0)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(responses["first"].status_code, 200)
            self.assertEqual(responses["second"].status_code, 200)
            public = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")
            cached = storage.load_dataset(STANDARD_CARDS_SOURCE_ID)
            status = storage.load_status(STANDARD_CARDS_SOURCE_ID)
            self.assertEqual(public.status_code, 200, public.text)
            assert cached is not None and status is not None
            final_version = public.json()["publication"]["dataset_version"]
            self.assertEqual(cached["data"]["title"], "serialized-upload-b")
            self.assertEqual(dataset_version(cached), final_version)
            self.assertEqual(status["dataset_version"], final_version)
            self.assertEqual(status["published_dataset_version"], final_version)

    def test_delayed_older_reconciliation_cannot_overwrite_winning_status(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_API_KEY": "secret",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ), TestClient(app) as client:
            from app import storage

            older_waiting = threading.Event()
            release_older = threading.Event()
            newer_done = threading.Event()
            call_lock = threading.Lock()
            call_count = 0
            failures: list[BaseException] = []
            responses: dict[str, object] = {}
            original_reconcile = (
                DatasetPublicationStore.reconcile_current_publication
            )

            def delay_first_reconciliation(self, source_id, **kwargs):
                nonlocal call_count
                with call_lock:
                    call_count += 1
                    should_delay = call_count == 1
                if should_delay:
                    older_waiting.set()
                    if not release_older.wait(3.0):
                        raise AssertionError("test did not release older reconciliation")
                return original_reconcile(self, source_id, **kwargs)

            older_payload = _parsed_payload()
            older_payload["title"] = "delayed-older-publication"
            newer_payload = _parsed_payload()
            newer_payload["title"] = "winning-newer-publication"

            def upload(name: str, payload: dict, done: threading.Event | None = None) -> None:
                try:
                    responses[name] = client.put(
                        f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                        headers={"X-API-Key": "secret"},
                        json=payload,
                    )
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    if done is not None:
                        done.set()

            with patch.object(
                DatasetPublicationStore,
                "reconcile_current_publication",
                new=delay_first_reconciliation,
            ):
                older = threading.Thread(
                    target=upload, args=("older", older_payload)
                )
                newer = threading.Thread(
                    target=upload,
                    args=("newer", newer_payload, newer_done),
                )
                older.start()
                self.assertTrue(older_waiting.wait(2.0))
                newer.start()
                try:
                    self.assertTrue(
                        newer_done.wait(2.0),
                        "newer publication did not finish before delayed reconciliation",
                    )
                finally:
                    release_older.set()
                older.join(timeout=3.0)
                newer.join(timeout=3.0)

            self.assertFalse(older.is_alive())
            self.assertFalse(newer.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(responses["older"].status_code, 200)
            self.assertEqual(responses["newer"].status_code, 200)
            public = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")
            cached = storage.load_dataset(STANDARD_CARDS_SOURCE_ID)
            status = storage.load_status(STANDARD_CARDS_SOURCE_ID)
            assert cached is not None and status is not None
            winning_version = public.json()["publication"]["dataset_version"]
            self.assertEqual(cached["data"]["title"], "winning-newer-publication")
            self.assertEqual(dataset_version(cached), winning_version)
            self.assertEqual(status["candidate_dataset_version"], winning_version)
            self.assertEqual(status["published_dataset_version"], winning_version)
            self.assertEqual(status["dataset_version"], winning_version)
            self.assertNotIn("last_refresh_state", status)
            self.assertNotIn("last_refresh_error", status)

    def test_delayed_accepted_reconciliation_preserves_newer_rejection_provenance(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_API_DATA_DIR": directory},
            clear=False,
        ):
            from app import storage
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            accepted_a_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=3),
                count=600,
                backend="accepted-a-delayed",
            )
            accepted_b_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                count=600,
                backend="accepted-b-winner",
            )
            accepted_a = validate_and_publish_standard_cards_candidate(
                source, accepted_a_dataset, store=store
            )
            accepted_b = validate_and_publish_standard_cards_candidate(
                source, accepted_b_dataset, store=store
            )
            winner = store.reconcile_current_publication(
                source.id,
                candidate_dataset_version=accepted_b.dataset_version,
                expected_dataset_version=accepted_b.dataset_version,
                status={"source_id": source.id, "state": "ok"},
            )
            rejected_c = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                    count=600,
                    popularity="99%",
                    backend="rejected-c-latest-attempt",
                ),
                store=store,
            )
            rejected = store.reconcile_current_publication(
                source.id,
                candidate_dataset_version=rejected_c.dataset_version,
                expected_dataset_version=None,
                status={
                    "source_id": source.id,
                    "state": "quality_error",
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "detail": rejected_c.reason,
                },
            )

            delayed_a = store.reconcile_current_publication(
                source.id,
                candidate_dataset_version=accepted_a.dataset_version,
                expected_dataset_version=accepted_a.dataset_version,
                status={"source_id": source.id, "state": "ok"},
            )

            cached = storage.load_dataset(source.id)
            status = storage.load_status(source.id)
            assert cached is not None and status is not None
            self.assertTrue(accepted_a.accepted)
            self.assertTrue(accepted_b.accepted)
            self.assertFalse(rejected_c.accepted)
            self.assertEqual(winner.dataset_version, accepted_b.dataset_version)
            self.assertEqual(rejected.dataset_version, accepted_b.dataset_version)
            self.assertTrue(delayed_a.superseded)
            self.assertEqual(dataset_version(cached), accepted_b.dataset_version)
            self.assertEqual(status["dataset_version"], accepted_b.dataset_version)
            self.assertEqual(
                status["published_dataset_version"], accepted_b.dataset_version
            )
            self.assertEqual(
                status["candidate_dataset_version"], rejected_c.dataset_version
            )
            self.assertTrue(status["serving_cached_dataset"])
            self.assertEqual(status["last_refresh_state"], "quality_error")
            self.assertEqual(status["last_refresh_error"], rejected_c.reason)

    def test_attempt_generation_preserves_newer_rejection_on_same_publication(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_API_DATA_DIR": directory},
            clear=False,
        ):
            from app import storage
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            attempt_a = store.begin_publication_attempt(source.id)
            accepted_a = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(count=600, backend="same-version-accepted-a"),
                store=store,
                publication_attempt=attempt_a,
            )
            attempt_b = store.begin_publication_attempt(source.id)
            rejected_b = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    count=600,
                    popularity="99%",
                    backend="same-version-rejected-b",
                ),
                store=store,
                publication_attempt=attempt_b,
            )
            store.reconcile_current_publication(
                source.id,
                candidate_dataset_version=rejected_b.dataset_version,
                expected_dataset_version=accepted_a.dataset_version,
                status={
                    "source_id": source.id,
                    "state": "quality_error",
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "detail": rejected_b.reason,
                },
                attempt_generation=attempt_b.generation,
                attempt_id=attempt_b.attempt_id,
                attempt_started_at=attempt_b.started_at,
            )

            delayed_a = store.reconcile_current_publication(
                source.id,
                candidate_dataset_version=accepted_a.dataset_version,
                expected_dataset_version=accepted_a.dataset_version,
                status={"source_id": source.id, "state": "ok"},
                attempt_generation=attempt_a.generation,
                attempt_id=attempt_a.attempt_id,
                attempt_started_at=attempt_a.started_at,
            )

            status = storage.load_status(source.id)
            assert status is not None
            self.assertTrue(delayed_a.superseded)
            self.assertEqual(
                status["candidate_dataset_version"], rejected_b.dataset_version
            )
            self.assertEqual(status["last_refresh_state"], "quality_error")
            self.assertEqual(status["last_refresh_error"], rejected_b.reason)
            self.assertEqual(
                status["publication_attempt_generation"], attempt_b.generation
            )

    def test_begin_attempt_repairs_valid_but_stale_attempt_state_from_status(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_API_DATA_DIR": directory},
            clear=False,
        ):
            from app import storage

            store = DatasetPublicationStore(Path(directory))
            source_id = STANDARD_CARDS_SOURCE_ID
            store.attempt_state_path(source_id).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "source_id": source_id,
                        "last_generation": 3,
                        "last_attempt_id": "stale",
                        "last_started_at": datetime.now(UTC).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            storage.save_status(
                source_id,
                {
                    "source_id": source_id,
                    "state": "ok",
                    "publication_attempt_generation": 5,
                },
            )

            attempt = store.begin_publication_attempt(source_id)

            self.assertEqual(attempt.generation, 6)
            state = json.loads(
                store.attempt_state_path(source_id).read_text(encoding="utf-8")
            )
            self.assertEqual(state["last_generation"], 6)

    def test_cold_failure_status_is_attempt_aware_and_cannot_overwrite_newer(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_API_DATA_DIR": directory},
            clear=False,
        ):
            from app import storage

            store = DatasetPublicationStore(Path(directory))
            source_id = STANDARD_CARDS_SOURCE_ID
            older = store.begin_publication_attempt(source_id)
            newer = store.begin_publication_attempt(source_id)
            newer_status = {
                "source_id": source_id,
                "state": "fetch_error",
                "fetched_at": newer.started_at,
                "detail": "newer cold failure",
            }
            older_status = {
                "source_id": source_id,
                "state": "fetch_error",
                "fetched_at": older.started_at,
                "detail": "delayed older cold failure",
            }

            winner = store.record_status_without_publication(
                source_id,
                status=newer_status,
                attempt_generation=newer.generation,
                attempt_id=newer.attempt_id,
                attempt_started_at=newer.started_at,
            )
            delayed = store.record_status_without_publication(
                source_id,
                status=older_status,
                attempt_generation=older.generation,
                attempt_id=older.attempt_id,
                attempt_started_at=older.started_at,
            )

            disk_status = storage.load_status(source_id)
            assert disk_status is not None
            self.assertEqual(winner["detail"], "newer cold failure")
            self.assertEqual(delayed["detail"], "newer cold failure")
            self.assertEqual(disk_status["detail"], "newer cold failure")
            self.assertEqual(
                disk_status["publication_attempt_generation"], newer.generation
            )

    def test_rejected_cold_admin_upload_uses_locked_status_recording(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_API_KEY": "secret",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ), TestClient(app) as client, patch(
            "app.dataset_publication_store.DatasetPublicationStore.reconcile_current_publication",
            side_effect=PublicationUnavailable("published_corrupt"),
        ), patch(
            "app.dataset_publication_store.DatasetPublicationStore.record_status_without_publication",
            wraps=DatasetPublicationStore(Path(directory)).record_status_without_publication,
        ) as locked_record:
            response = client.put(
                f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                headers={"X-API-Key": "secret"},
                json=_parsed_payload(popularity="99%"),
            )

            self.assertEqual(response.status_code, 422, response.text)
            self.assertTrue(locked_record.called)

    def test_delayed_pre_candidate_failure_cannot_overwrite_newer_success_status(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_API_DATA_DIR": directory},
            clear=False,
        ):
            from app import storage
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )
            from app.fetcher import _save_failure_status

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            seed_attempt = store.begin_publication_attempt(source.id)
            seed = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                    count=600,
                    backend="pre-candidate-seed",
                ),
                store=store,
                publication_attempt=seed_attempt,
            )
            store.reconcile_current_publication(
                source.id,
                candidate_dataset_version=seed.dataset_version,
                expected_dataset_version=seed.dataset_version,
                status={"source_id": source.id, "state": "ok"},
                attempt_generation=seed_attempt.generation,
                attempt_id=seed_attempt.attempt_id,
                attempt_started_at=seed_attempt.started_at,
            )
            delayed_failure_attempt = store.begin_publication_attempt(source.id)
            winner_attempt = store.begin_publication_attempt(source.id)
            winner = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(count=600, backend="pre-candidate-newer-winner"),
                store=store,
                publication_attempt=winner_attempt,
            )
            store.reconcile_current_publication(
                source.id,
                candidate_dataset_version=winner.dataset_version,
                expected_dataset_version=winner.dataset_version,
                status={"source_id": source.id, "state": "ok"},
                attempt_generation=winner_attempt.generation,
                attempt_id=winner_attempt.attempt_id,
                attempt_started_at=winner_attempt.started_at,
            )

            delayed = _save_failure_status(
                source,
                {
                    "source_id": source.id,
                    "state": "fetch_error",
                    "fetched_at": delayed_failure_attempt.started_at,
                    "detail": "older network failure",
                },
                publication_attempt=delayed_failure_attempt,
            )

            cached = storage.load_dataset(source.id)
            status = storage.load_status(source.id)
            assert cached is not None and status is not None
            self.assertEqual(dataset_version(cached), winner.dataset_version)
            self.assertEqual(delayed["dataset_version"], winner.dataset_version)
            self.assertEqual(status["dataset_version"], winner.dataset_version)
            self.assertEqual(
                status["publication_attempt_generation"], winner_attempt.generation
            )
            self.assertNotIn("last_refresh_state", status)
            self.assertNotIn("last_refresh_error", status)

    def test_pre_rollback_attempt_cannot_repromote_after_admin_rollback(self) -> None:
        with TemporaryDirectory() as directory:
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            old = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=3),
                    count=600,
                    backend="rollback-old",
                ),
                store=store,
            )
            current = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                    count=600,
                    backend="rollback-current",
                ),
                store=store,
            )
            delayed_parser_attempt = store.begin_publication_attempt(source.id)
            admin_rollback_attempt = store.begin_publication_attempt(source.id)
            store.rollback_to_version(
                source.id,
                old.dataset_version,
                validation={"ok": True, "reason": "admin rollback", "diagnostics": {}},
            )

            delayed = validate_and_publish_standard_cards_candidate(
                source,
                _dataset(
                    fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                    count=600,
                    backend="delayed-pre-rollback-parser",
                ),
                store=store,
                publication_attempt=delayed_parser_attempt,
            )

            self.assertTrue(old.accepted)
            self.assertTrue(current.accepted)
            self.assertGreater(
                admin_rollback_attempt.generation,
                delayed_parser_attempt.generation,
            )
            self.assertFalse(delayed.accepted)
            self.assertEqual(delayed.rejection_kind, "superseded")
            self.assertIn("superseded", delayed.reason)
            self.assertEqual(
                store.pointer_dataset_version(source.id), old.dataset_version
            )

    def test_rollback_and_new_publish_reconciliation_are_serialized(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_API_KEY": "secret",
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ), TestClient(app) as client:
            from app import storage
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            previous_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                count=600,
                backend="rollback-race-previous",
            )
            current_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                count=600,
                backend="rollback-race-current",
            )
            validate_and_publish_standard_cards_candidate(
                source, previous_dataset, store=store
            )
            validate_and_publish_standard_cards_candidate(
                source, current_dataset, store=store
            )
            storage.save_dataset(STANDARD_CARDS_SOURCE_ID, current_dataset)

            entered_sync = threading.Event()
            release_sync = threading.Event()
            publish_done = threading.Event()
            call_lock = threading.Lock()
            call_count = 0
            failures: list[BaseException] = []
            responses: dict[str, object] = {}
            real_save_dataset = storage.save_dataset

            def block_first_cache_sync(source_id: str, payload: dict) -> None:
                nonlocal call_count
                with call_lock:
                    call_count += 1
                    should_block = call_count == 1
                if should_block:
                    entered_sync.set()
                    if not release_sync.wait(3.0):
                        raise AssertionError("test did not release rollback cache sync")
                real_save_dataset(source_id, payload)

            def rollback() -> None:
                try:
                    responses["rollback"] = client.post(
                        f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}/publication/rollback",
                        headers={"X-API-Key": "secret"},
                        json={},
                    )
                except BaseException as exc:
                    failures.append(exc)

            new_payload = _parsed_payload()
            new_payload["title"] = "publish-after-rollback"

            def publish() -> None:
                try:
                    responses["publish"] = client.put(
                        f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                        headers={"X-API-Key": "secret"},
                        json=new_payload,
                    )
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    publish_done.set()

            with patch("app.storage.save_dataset", side_effect=block_first_cache_sync):
                rollback_thread = threading.Thread(target=rollback)
                publish_thread = threading.Thread(target=publish)
                rollback_thread.start()
                self.assertTrue(entered_sync.wait(2.0))
                publish_thread.start()
                try:
                    self.assertFalse(
                        publish_done.wait(0.2),
                        "publish passed rollback cache/status reconciliation",
                    )
                finally:
                    release_sync.set()
                rollback_thread.join(timeout=3.0)
                publish_thread.join(timeout=3.0)

            self.assertFalse(rollback_thread.is_alive())
            self.assertFalse(publish_thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(responses["rollback"].status_code, 200)
            self.assertEqual(responses["publish"].status_code, 200)
            public = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")
            cached = storage.load_dataset(STANDARD_CARDS_SOURCE_ID)
            status = storage.load_status(STANDARD_CARDS_SOURCE_ID)
            self.assertEqual(public.status_code, 200, public.text)
            assert cached is not None and status is not None
            final_version = public.json()["publication"]["dataset_version"]
            self.assertEqual(cached["data"]["title"], "publish-after-rollback")
            self.assertEqual(dataset_version(cached), final_version)
            self.assertEqual(status["dataset_version"], final_version)
            self.assertEqual(status["published_dataset_version"], final_version)

    def test_public_etag_tracks_exact_lkg_even_if_mutable_cache_save_crashes(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ),
        ):
            from app.storage import save_dataset

            store = DatasetPublicationStore(Path(directory))
            first_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=1),
                count=600,
                backend="first",
            )
            first_candidate = store.stage_candidate(
                STANDARD_CARDS_SOURCE_ID, first_dataset
            )
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                first_candidate["dataset_version"],
                validation={"ok": True, "reason": "ok", "diagnostics": {}},
            )
            save_dataset(STANDARD_CARDS_SOURCE_ID, first_dataset)

            with TestClient(app) as client:
                first = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")
                self.assertEqual(first.status_code, 200, first.text)

                second_dataset = _dataset(
                    count=600,
                    backend="promoted-before-cache-crash",
                )
                second_candidate = store.stage_candidate(
                    STANDARD_CARDS_SOURCE_ID, second_dataset
                )
                store.promote_candidate(
                    STANDARD_CARDS_SOURCE_ID,
                    second_candidate["dataset_version"],
                    validation={"ok": True, "reason": "ok", "diagnostics": {}},
                )

                changed = client.get(
                    f"/datasets/{STANDARD_CARDS_SOURCE_ID}",
                    headers={"If-None-Match": first.headers["etag"]},
                )
                unchanged = client.get(
                    f"/datasets/{STANDARD_CARDS_SOURCE_ID}",
                    headers={"If-None-Match": changed.headers["etag"]},
                )

            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertNotEqual(changed.headers["etag"], first.headers["etag"])
            self.assertEqual(
                changed.json()["publication"]["dataset_version"],
                second_candidate["dataset_version"],
            )
            self.assertEqual(changed.json()["backend"], "promoted-before-cache-crash")
            self.assertNotIn("age_hours", changed.json()["publication"])
            self.assertNotIn(
                "x-hs-publication-representation",
                {key.lower() for key in changed.headers},
            )
            self.assertTrue(changed.json()["publication"]["stale"])
            self.assertEqual(
                changed.json()["publication"]["fallback_reason"],
                "current_dataset_not_published",
            )
            self.assertEqual(unchanged.status_code, 304)
            self.assertEqual(unchanged.content, b"")

    def test_head_uses_exact_lkg_etag_cache_and_conditional_semantics(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app import storage
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )
            from app.public_cache import PUBLIC_CACHE_CONTROL

            dataset = _dataset(count=600, backend="head-exact-lkg")
            decision = validate_and_publish_standard_cards_candidate(
                SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                dataset,
                store=DatasetPublicationStore(Path(directory)),
            )
            storage.save_dataset(STANDARD_CARDS_SOURCE_ID, dataset)
            self.assertTrue(decision.accepted)
            path = f"/datasets/{STANDARD_CARDS_SOURCE_ID}"
            with TestClient(app) as client:
                get_response = client.get(path)
                head_response = client.head(path)
                conditional = client.head(
                    path,
                    headers={"If-None-Match": head_response.headers["etag"]},
                )

            self.assertEqual(get_response.status_code, 200, get_response.text)
            self.assertEqual(head_response.status_code, 200, head_response.text)
            self.assertEqual(head_response.content, b"")
            self.assertEqual(
                head_response.headers["etag"], get_response.headers["etag"]
            )
            self.assertEqual(
                head_response.headers["cache-control"], PUBLIC_CACHE_CONTROL
            )
            self.assertNotIn(
                "x-hs-publication-representation",
                {key.lower() for key in head_response.headers},
            )
            self.assertEqual(conditional.status_code, 304, conditional.text)
            self.assertEqual(conditional.content, b"")
            self.assertEqual(
                conditional.headers["etag"], head_response.headers["etag"]
            )

    def test_cold_head_returns_controlled_uncacheable_503(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ), TestClient(app) as client:
            response = client.head(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")

            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.content, b"")
            self.assertEqual(response.headers["retry-after"], "60")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("etag", response.headers)

    def test_startup_bootstraps_valid_legacy_dataset_without_public_outage(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ),
        ):
            legacy_path = (
                Path(directory) / "datasets" / f"{STANDARD_CARDS_SOURCE_ID}.json"
            )
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text(
                json.dumps(_dataset(count=600), ensure_ascii=False),
                encoding="utf-8",
            )

            with TestClient(app) as client:
                response = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["publication"]["channel"], "stable")
            self.assertEqual(
                response.json()["publication"]["storage_channel"], "published_lkg"
            )

    def test_startup_repairs_expired_lkg_from_newer_valid_legacy_dataset(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ),
        ):
            store = DatasetPublicationStore(Path(directory))
            expired = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(hours=40),
                count=600,
            )
            old_candidate = store.stage_candidate(STANDARD_CARDS_SOURCE_ID, expired)
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                old_candidate["dataset_version"],
                validation={"ok": True, "reason": "ok", "diagnostics": {}},
            )
            current = _dataset(count=600)
            expected_version = dataset_version(current)
            legacy_path = (
                Path(directory) / "datasets" / f"{STANDARD_CARDS_SOURCE_ID}.json"
            )
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text(
                json.dumps(current, ensure_ascii=False), encoding="utf-8"
            )

            with TestClient(app) as client:
                response = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(
                response.json()["publication"]["dataset_version"], expected_version
            )

    def test_startup_does_not_replace_expired_lkg_with_older_equal_or_future_legacy(
        self,
    ) -> None:
        existing_time = datetime.now(UTC) - timedelta(hours=40)
        cases = {
            "older": existing_time - timedelta(hours=40),
            "equal": existing_time,
            "future": datetime.now(UTC) + timedelta(minutes=6),
        }
        for name, legacy_time in cases.items():
            with self.subTest(name=name), TemporaryDirectory() as directory, patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ):
                store = DatasetPublicationStore(Path(directory))
                existing = _dataset(
                    fetched_at=existing_time,
                    count=600,
                    backend="expired-current",
                )
                current_candidate = store.stage_candidate(
                    STANDARD_CARDS_SOURCE_ID, existing
                )
                store.promote_candidate(
                    STANDARD_CARDS_SOURCE_ID,
                    current_candidate["dataset_version"],
                    validation={"ok": True, "reason": "ok", "diagnostics": {}},
                )
                legacy = _dataset(
                    fetched_at=legacy_time,
                    count=600,
                    backend=f"legacy-{name}",
                )
                legacy_path = (
                    Path(directory)
                    / "datasets"
                    / f"{STANDARD_CARDS_SOURCE_ID}.json"
                )
                legacy_path.parent.mkdir(parents=True, exist_ok=True)
                legacy_path.write_text(
                    json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
                )

                with TestClient(app):
                    pass

                self.assertEqual(
                    store.list_published_versions(STANDARD_CARDS_SOURCE_ID)[0][
                        "dataset_version"
                    ],
                    current_candidate["dataset_version"],
                )

    def test_admin_upload_uses_gate_and_quarantine_endpoint_requires_admin(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ),
            TestClient(app) as client,
        ):
            invalid = _parsed_payload(popularity="99%")
            rejected = client.put(
                f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                headers={"X-API-Key": "secret"},
                json=invalid,
            )

            self.assertEqual(rejected.status_code, 422, rejected.text)
            self.assertEqual(
                client.get(
                    f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}/quarantine"
                ).status_code,
                401,
            )
            allowed = client.get(
                f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}/quarantine",
                headers={"X-API-Key": "secret"},
            )
            self.assertEqual(allowed.status_code, 200, allowed.text)
            self.assertEqual(allowed.headers["cache-control"], "private, no-store")
            self.assertEqual(len(allowed.json()["quarantine"]), 1)
            self.assertIn("systemic", allowed.json()["quarantine"][0]["reason"])

    def test_rejected_upload_preserves_served_version_and_records_candidate_version(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ),
            TestClient(app) as client,
        ):
            from app.storage import load_status

            accepted = client.put(
                f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                headers={"X-API-Key": "secret"},
                json=_parsed_payload(),
            )
            rejected = client.put(
                f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                headers={"X-API-Key": "secret"},
                json=_parsed_payload(popularity="99%"),
            )
            public = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")
            status = load_status(STANDARD_CARDS_SOURCE_ID)

            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(rejected.status_code, 422, rejected.text)
            self.assertEqual(public.status_code, 200, public.text)
            self.assertIsNotNone(status)
            assert status is not None
            published_version = accepted.json()["dataset_version"]
            candidate_version = rejected.json()["detail"]["dataset_version"]
            self.assertEqual(status["state"], "ok")
            self.assertTrue(status["serving_cached_dataset"])
            self.assertEqual(status["last_refresh_state"], "quality_error")
            self.assertEqual(status["candidate_dataset_version"], candidate_version)
            self.assertEqual(status["published_dataset_version"], published_version)
            self.assertEqual(status["dataset_version"], published_version)
            self.assertEqual(
                public.json()["publication"]["dataset_version"], published_version
            )

    def test_ops_health_reports_corrupt_or_expired_standard_publication(self) -> None:
        for state in ("corrupt", "expired"):
            with self.subTest(state=state), TemporaryDirectory() as directory, patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ):
                from app import main

                dataset = _dataset(
                    fetched_at=(
                        datetime.now(UTC) - timedelta(hours=40)
                        if state == "expired"
                        else datetime.now(UTC)
                    ),
                    count=600,
                )
                store = DatasetPublicationStore(Path(directory))
                candidate = store.stage_candidate(STANDARD_CARDS_SOURCE_ID, dataset)
                store.promote_candidate(
                    STANDARD_CARDS_SOURCE_ID,
                    candidate["dataset_version"],
                    validation={"ok": True, "reason": "ok", "diagnostics": {}},
                )
                if state == "corrupt":
                    store.published_path(STANDARD_CARDS_SOURCE_ID).write_text(
                        "{broken", encoding="utf-8"
                    )
                status = {
                    "source_id": STANDARD_CARDS_SOURCE_ID,
                    "state": "ok",
                    "fetched_at": dataset["fetched_at"],
                }
                source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
                with (
                    patch.object(main, "SOURCES", [source]),
                    patch.object(main, "load_status", return_value=status),
                    patch.object(main, "load_dataset", return_value=dataset),
                    patch("app.stale_monitor.find_stale_sources", return_value=[]),
                ):
                    health = main.build_health_diagnostics()

                self.assertFalse(health["ok"])
                self.assertFalse(health["serving_ok"])
                self.assertEqual(
                    health["publication_failed_sources"],
                    [STANDARD_CARDS_SOURCE_ID],
                )
                self.assertEqual(
                    health["publication_failures"][0]["reason"],
                    "published_corrupt" if state == "corrupt" else "published_too_old",
                )

    def test_ops_health_reports_lkg_fallback_when_mutable_cache_is_missing_or_corrupt(
        self,
    ) -> None:
        for cache_state in ("missing", "corrupt"):
            with self.subTest(cache_state=cache_state), TemporaryDirectory() as directory, patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ):
                from app import main
                from app.dataset_publication_store import (
                    validate_and_publish_standard_cards_candidate,
                )

                dataset = _dataset(count=600, backend=f"health-{cache_state}")
                decision = validate_and_publish_standard_cards_candidate(
                    SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                    dataset,
                    store=DatasetPublicationStore(Path(directory)),
                )
                self.assertTrue(decision.accepted)
                status = {
                    "source_id": STANDARD_CARDS_SOURCE_ID,
                    "state": "ok",
                    "fetched_at": dataset["fetched_at"],
                }
                dataset_result = (
                    None
                    if cache_state == "missing"
                    else ValueError("corrupt mutable dataset json")
                )
                load_dataset_patch = (
                    patch.object(main, "load_dataset", return_value=None)
                    if dataset_result is None
                    else patch.object(main, "load_dataset", side_effect=dataset_result)
                )
                with (
                    patch.object(
                        main,
                        "SOURCES",
                        [SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]],
                    ),
                    patch.object(main, "load_status", return_value=status),
                    load_dataset_patch,
                    patch("app.stale_monitor.find_stale_sources", return_value=[]),
                ):
                    health = main.build_health_diagnostics()

                self.assertTrue(health["ok"])
                self.assertTrue(health["serving_ok"])
                self.assertFalse(health["freshness_ok"])
                self.assertTrue(health["degraded"])
                self.assertEqual(
                    health["publication_stale_sources"],
                    [STANDARD_CARDS_SOURCE_ID],
                )
                self.assertEqual(
                    health["publication_stale_details"][0]["fallback_reason"],
                    f"current_dataset_{cache_state}",
                )

    def test_ops_health_uses_served_lkg_when_status_is_missing_corrupt_or_failed(
        self,
    ) -> None:
        for status_state in ("missing", "corrupt", "failed"):
            with self.subTest(status_state=status_state), TemporaryDirectory() as directory, patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ):
                from app import main
                from app.dataset_publication_store import (
                    validate_and_publish_standard_cards_candidate,
                )

                dataset = _dataset(count=600, backend=f"status-{status_state}")
                decision = validate_and_publish_standard_cards_candidate(
                    SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                    dataset,
                    store=DatasetPublicationStore(Path(directory)),
                )
                self.assertTrue(decision.accepted)
                if status_state == "missing":
                    status_patch = patch.object(main, "load_status", return_value=None)
                    expected_reason = "status_missing"
                elif status_state == "corrupt":
                    status_patch = patch.object(
                        main,
                        "load_status",
                        side_effect=ValueError("corrupt status json"),
                    )
                    expected_reason = "status_corrupt"
                else:
                    status_patch = patch.object(
                        main,
                        "load_status",
                        return_value={
                            "source_id": STANDARD_CARDS_SOURCE_ID,
                            "state": "quality_error",
                            "fetched_at": dataset["fetched_at"],
                        },
                    )
                    expected_reason = "latest_refresh_failed:quality_error"
                with (
                    patch.object(
                        main,
                        "SOURCES",
                        [SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]],
                    ),
                    status_patch,
                    patch.object(main, "load_dataset", return_value=dataset),
                    patch("app.stale_monitor.find_stale_sources", return_value=[]),
                ):
                    health = main.build_health_diagnostics()

                self.assertTrue(health["ok"])
                self.assertTrue(health["serving_ok"])
                self.assertFalse(health["freshness_ok"])
                self.assertTrue(health["degraded"])
                self.assertEqual(health["hard_failed_sources"], [])
                self.assertEqual(
                    health["publication_stale_details"][0]["fallback_reason"],
                    expected_reason,
                )

    def test_ops_health_validates_served_lkg_instead_of_invalid_mutable_cache(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app import main
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            published_dataset = _dataset(count=600, backend="health-served-lkg")
            decision = validate_and_publish_standard_cards_candidate(
                SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                published_dataset,
                store=DatasetPublicationStore(Path(directory)),
            )
            self.assertTrue(decision.accepted)
            invalid_mutable = _dataset(
                count=600,
                popularity="99%",
                backend="invalid-mutable-candidate",
            )
            status = {
                "source_id": STANDARD_CARDS_SOURCE_ID,
                "state": "ok",
                "fetched_at": published_dataset["fetched_at"],
            }
            with (
                patch.object(
                    main,
                    "SOURCES",
                    [SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]],
                ),
                patch.object(main, "load_status", return_value=status),
                patch.object(main, "load_dataset", return_value=invalid_mutable),
                patch("app.stale_monitor.find_stale_sources", return_value=[]),
            ):
                health = main.build_health_diagnostics()

            self.assertTrue(health["ok"])
            self.assertTrue(health["serving_ok"])
            self.assertEqual(health["semantic_failed_sources"], [])
            self.assertFalse(health["freshness_ok"])
            self.assertEqual(
                health["publication_stale_details"][0]["fallback_reason"],
                "current_dataset_not_published",
            )

    def test_demo_serves_lkg_with_degraded_metadata_when_status_is_corrupt(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app import storage
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            dataset = _dataset(count=600, backend="demo-corrupt-status")
            decision = validate_and_publish_standard_cards_candidate(
                SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                dataset,
                store=DatasetPublicationStore(Path(directory)),
            )
            self.assertTrue(decision.accepted)
            storage.save_dataset(STANDARD_CARDS_SOURCE_ID, dataset)
            storage.status_path(STANDARD_CARDS_SOURCE_ID).write_text(
                "{broken", encoding="utf-8"
            )

            with TestClient(app) as client:
                response = client.get(f"/demo/view/{STANDARD_CARDS_SOURCE_ID}")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["publication"]["stale"])
            self.assertEqual(
                response.json()["publication"]["fallback_reason"],
                "status_corrupt",
            )

    def test_ops_health_survives_stale_monitor_rereading_corrupt_files(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app import main, storage
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            dataset = _dataset(count=600, backend="health-corrupt-files")
            decision = validate_and_publish_standard_cards_candidate(
                SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID],
                dataset,
                store=DatasetPublicationStore(Path(directory)),
            )
            self.assertTrue(decision.accepted)
            storage.dataset_path(STANDARD_CARDS_SOURCE_ID).write_text(
                "{broken", encoding="utf-8"
            )
            storage.status_path(STANDARD_CARDS_SOURCE_ID).write_text(
                "{broken", encoding="utf-8"
            )
            with patch.object(
                main,
                "SOURCES",
                [SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]],
            ):
                health = main.build_health_diagnostics()

            self.assertTrue(health["serving_ok"])
            self.assertFalse(health["freshness_ok"])
            self.assertTrue(health["degraded"])
            self.assertIn(STANDARD_CARDS_SOURCE_ID, health["stale_sources"])
            self.assertEqual(health["freshness_monitor_errors"], [])

    def test_stale_monitor_keeps_other_stale_rows_when_standard_files_are_corrupt(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"HS_API_DATA_DIR": directory, "HS_STALE_HOURS": "12"},
            clear=False,
        ):
            from app import stale_monitor, storage

            standard = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            other = SOURCE_BY_ID["hsreplay_cards_wild_legend_1d"]
            storage.status_path(standard.id).write_text("{broken", encoding="utf-8")
            storage.dataset_path(standard.id).write_text("{broken", encoding="utf-8")
            stale_time = (datetime.now(UTC) - timedelta(hours=20)).isoformat()
            storage.save_status(
                other.id,
                {"source_id": other.id, "state": "ok", "fetched_at": stale_time},
            )
            storage.save_dataset(
                other.id,
                {
                    "source_id": other.id,
                    "fetched_at": stale_time,
                    "data": {},
                },
            )

            with patch.object(stale_monitor, "SOURCES", (standard, other)):
                rows = stale_monitor.find_stale_sources(include_ok=True)

            by_source = {row["source_id"]: row for row in rows}
            self.assertIn(standard.id, by_source)
            self.assertIn("corrupt", by_source[standard.id]["reason"])
            self.assertIn(other.id, by_source)
            self.assertEqual(by_source[other.id]["reason"], "ok_but_stale")

    def test_ops_health_degrades_when_status_versions_do_not_match_publication(
        self,
    ) -> None:
        with TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HS_API_DATA_DIR": directory,
                "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
            },
            clear=False,
        ):
            from app import main, storage
            from app.dataset_publication_store import (
                validate_and_publish_standard_cards_candidate,
            )

            store = DatasetPublicationStore(Path(directory))
            source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
            first_dataset = _dataset(
                fetched_at=datetime.now(UTC) - timedelta(minutes=2),
                count=600,
                backend="status-version-a",
            )
            first = validate_and_publish_standard_cards_candidate(
                source, first_dataset, store=store
            )
            storage.save_status(
                source.id,
                {
                    "source_id": source.id,
                    "state": "ok",
                    "dataset_version": first.dataset_version,
                    "published_dataset_version": first.dataset_version,
                },
            )
            second_dataset = _dataset(count=600, backend="status-version-b")
            second = validate_and_publish_standard_cards_candidate(
                source, second_dataset, store=store
            )
            storage.save_dataset(source.id, second_dataset)
            with (
                patch.object(main, "SOURCES", [source]),
                patch("app.stale_monitor.find_stale_sources", return_value=[]),
            ):
                health = main.build_health_diagnostics()

            self.assertTrue(first.accepted)
            self.assertTrue(second.accepted)
            self.assertTrue(health["serving_ok"])
            self.assertFalse(health["freshness_ok"])
            self.assertTrue(health["degraded"])
            self.assertEqual(
                health["publication_stale_details"][0]["fallback_reason"],
                "status_not_published",
            )

    def test_valid_upload_promotes_public_version_and_corrupt_cache_uses_lkg(
        self,
    ) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ),
            TestClient(app) as client,
        ):
            accepted = client.put(
                f"/admin/datasets/{STANDARD_CARDS_SOURCE_ID}",
                headers={"X-API-Key": "secret"},
                json=_parsed_payload(),
            )
            self.assertEqual(accepted.status_code, 200, accepted.text)
            version = accepted.json()["dataset_version"]

            public = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")
            self.assertEqual(public.status_code, 200, public.text)
            self.assertEqual(public.json()["publication"]["dataset_version"], version)
            self.assertEqual(public.json()["publication"]["mode"], "stable")
            self.assertEqual(public.json()["publication"]["channel"], "stable")
            self.assertEqual(
                public.json()["publication"]["storage_channel"], "published_lkg"
            )
            self.assertFalse(public.json()["publication"]["stale"])
            demo = client.get(f"/demo/view/{STANDARD_CARDS_SOURCE_ID}")
            self.assertEqual(demo.status_code, 200, demo.text)
            self.assertEqual(demo.json()["publication"]["dataset_version"], version)
            self.assertEqual(demo.json()["publication"]["mode"], "stable")
            self.assertEqual(demo.json()["publication"]["channel"], "stable")
            self.assertEqual(
                demo.json()["publication"]["storage_channel"], "published_lkg"
            )

            cache_path = (
                Path(directory) / "datasets" / f"{STANDARD_CARDS_SOURCE_ID}.json"
            )
            cache_path.write_text("{broken", encoding="utf-8")
            fallback = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")

            self.assertEqual(fallback.status_code, 200, fallback.text)
            self.assertTrue(fallback.json()["publication"]["stale"])
            self.assertEqual(
                fallback.json()["publication"]["fallback_reason"],
                "current_dataset_corrupt",
            )
            self.assertEqual(fallback.json()["publication"]["dataset_version"], version)

    def test_too_old_or_corrupt_published_dataset_returns_controlled_503(self) -> None:
        with (
            TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "HS_API_DATA_DIR": directory,
                    "HS_API_KEY": "secret",
                    "HS_STANDARD_CARDS_MAX_STALE_HOURS": "36",
                },
                clear=False,
            ),
            TestClient(app) as client,
        ):
            store = DatasetPublicationStore(Path(directory))
            candidate = store.stage_candidate(
                STANDARD_CARDS_SOURCE_ID,
                _dataset(fetched_at=datetime.now(UTC) - timedelta(hours=37)),
            )
            store.promote_candidate(
                STANDARD_CARDS_SOURCE_ID,
                candidate["dataset_version"],
                validation={"ok": True, "reason": "ok", "diagnostics": {}},
            )

            expired = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")
            expired_demo = client.get(f"/demo/view/{STANDARD_CARDS_SOURCE_ID}")
            self.assertEqual(expired.status_code, 503, expired.text)
            self.assertEqual(expired.json()["detail"]["reason"], "published_too_old")
            for response in (expired, expired_demo):
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.headers["retry-after"], "60")
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("etag", response.headers)

            store.published_path(STANDARD_CARDS_SOURCE_ID).write_text(
                "{broken", encoding="utf-8"
            )
            corrupt = client.get(f"/datasets/{STANDARD_CARDS_SOURCE_ID}")
            corrupt_demo = client.get(f"/demo/view/{STANDARD_CARDS_SOURCE_ID}")
            self.assertEqual(corrupt.status_code, 503, corrupt.text)
            self.assertEqual(corrupt.json()["detail"]["reason"], "published_corrupt")
            for response in (corrupt, corrupt_demo):
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.headers["retry-after"], "60")
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertNotIn("etag", response.headers)


if __name__ == "__main__":
    unittest.main()
