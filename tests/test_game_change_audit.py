from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.game_change_audit import (
    CRITICAL_SOURCES,
    POST_PATCH_EARLY_DURATION,
    activate_post_patch_policy,
    audit_critical_sources,
    build_card_snapshot,
    compare_card_snapshots,
    current_patch_from_catalog,
    relevant_wiki_changes,
    run_game_change_audit,
)
from app.parser_control import ParserControlStore


class GameChangeAuditTest(unittest.TestCase):
    def test_new_patch_activates_a_bounded_early_policy_idempotently(self) -> None:
        now = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)
        with TemporaryDirectory() as directory, patch(
            "app.parser_control._now",
            return_value=now,
        ):
            store = ParserControlStore(Path(directory))
            first = activate_post_patch_policy("36.2.2", now=now, store=store)
            revision = store.read_state()[0]["revision"]
            second = activate_post_patch_policy("36.2.2", now=now, store=store)
            state = store.read_state()[0]

        self.assertTrue(first["activated"])
        self.assertFalse(second["activated"])
        self.assertEqual(second["reason"], "already_active")
        self.assertEqual(state["revision"], revision)
        self.assertEqual(state["policy"]["mode"], "early")
        self.assertEqual(
            state["policy"]["earlyUntil"],
            (now + POST_PATCH_EARLY_DURATION).isoformat(),
        )
        self.assertIn("36.2.2", state["policy"]["reason"])

    def test_audit_activates_policy_before_advancing_changed_patch_baseline(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            latest = root / "latest.json"
            history = root / "history.json"
            baseline.write_text(
                json.dumps({"patch": "36.2.1", "cards": {"hashes": {}}}),
                encoding="utf-8",
            )
            activation = {
                "activated": True,
                "reason": "patch_changed",
                "early_until": "2026-08-24T06:00:00+00:00",
            }
            with (
                patch(
                    "app.game_change_audit.audit_paths",
                    return_value=(baseline, latest, history),
                ),
                patch("app.game_change_audit.fetch_wiki_recent_changes", return_value=[]),
                patch("app.game_change_audit.current_patch_from_catalog", return_value="36.2.2"),
                patch("app.game_change_audit.cards_by_id", return_value={}),
                patch("app.game_change_audit.audit_critical_sources", return_value=([], [])),
                patch(
                    "app.game_change_audit.activate_post_patch_policy",
                    return_value=activation,
                ) as activate,
            ):
                report = run_game_change_audit()

        activate.assert_called_once()
        self.assertTrue(report["patch"]["changed"])
        self.assertEqual(report["post_patch_policy"], activation)

    def test_failed_policy_activation_does_not_advance_patch_baseline(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            latest = root / "latest.json"
            history = root / "history.json"
            previous = {"patch": "36.2.1", "cards": {"hashes": {}}}
            baseline.write_text(json.dumps(previous), encoding="utf-8")
            with (
                patch(
                    "app.game_change_audit.audit_paths",
                    return_value=(baseline, latest, history),
                ),
                patch("app.game_change_audit.fetch_wiki_recent_changes", return_value=[]),
                patch("app.game_change_audit.current_patch_from_catalog", return_value="36.2.2"),
                patch("app.game_change_audit.cards_by_id", return_value={}),
                patch("app.game_change_audit.audit_critical_sources", return_value=([], [])),
                patch(
                    "app.game_change_audit.activate_post_patch_policy",
                    side_effect=RuntimeError("control-plane unavailable"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "control-plane unavailable"):
                    run_game_change_audit()

            persisted = json.loads(baseline.read_text(encoding="utf-8"))

        self.assertEqual(persisted["patch"], "36.2.1")
        self.assertFalse(latest.exists())
        self.assertFalse(history.exists())

    def test_card_diff_detects_additions_removals_and_balance_changes(self) -> None:
        before = build_card_snapshot(
            {
                "A": {"id": "A", "name": "A", "cost": 2},
                "B": {"id": "B", "name": "B", "attack": 3},
            },
            {"A": {"id": "A", "name": "А"}, "B": {"id": "B", "name": "Б"}},
        )
        after = build_card_snapshot(
            {
                "A": {"id": "A", "name": "A", "cost": 3},
                "C": {"id": "C", "name": "C"},
            },
            {"A": {"id": "A", "name": "А"}, "C": {"id": "C", "name": "В"}},
        )

        diff = compare_card_snapshots(before, after)

        self.assertEqual(diff["added_ids"], ["C"])
        self.assertEqual(diff["removed_ids"], ["B"])
        self.assertEqual(diff["changed_ids"], ["A"])

    def test_wiki_filter_keeps_game_data_changes(self) -> None:
        rows = [
            {"title": "Patch 36.2.0"},
            {"title": "Battlegrounds/Minion"},
            {"title": "Tavern Brawl"},
        ]

        self.assertEqual(len(relevant_wiki_changes(rows)), 2)

    def test_patch_catalog_build_suffix_is_removed(self) -> None:
        with patch(
            "app.game_change_audit.list_patches",
            return_value={"patches": [{"version": "36.2.0.248348"}]},
        ):
            self.assertEqual(current_patch_from_catalog(), "36.2.0")

    def test_recent_wiki_patch_wins_when_catalog_index_lags(self) -> None:
        recent_changes = [
            {"title": "Patch 36.2.2.249896"},
            {"title": "Battlegrounds/Dark Gift"},
        ]
        with patch(
            "app.game_change_audit.list_patches",
            return_value={"patches": [{"version": "36.2.0.248348"}]},
        ):
            self.assertEqual(
                current_patch_from_catalog(recent_changes=recent_changes),
                "36.2.2",
            )

    def test_audit_excludes_operationally_disabled_source(self) -> None:
        now = datetime(2026, 8, 20, tzinfo=UTC)

        with patch(
            "app.game_change_audit.source_operationally_enabled",
            side_effect=lambda source_id: source_id != "firestone_standard",
        ):
            rows, issues = audit_critical_sources(
                now=now,
                status_loader=lambda _source_id: {"state": "ok"},
                dataset_loader=lambda _source_id: None,
            )

        disabled = next(
            row for row in rows if row["source_id"] == "firestone_standard"
        )
        self.assertEqual(disabled["state"], "excluded")
        self.assertEqual(disabled["exclusion_reason"], "operationally-disabled")
        self.assertNotIn(
            "firestone_standard", {row["source_id"] for row in issues}
        )

    def test_each_strategy_provider_is_checked_independently(self) -> None:
        now = datetime(2026, 8, 6, tzinfo=UTC)
        healthy_time = (now - timedelta(hours=1)).isoformat()

        def status_loader(source_id: str):
            if source_id == "hsreplay_battlegrounds_comps":
                return {
                    "state": "ok",
                    "serving_cached_dataset": True,
                    "last_refresh_state": "partial",
                }
            return {"state": "ok"}

        def dataset_loader(source_id: str):
            return {"fetched_at": healthy_time, "data": {}}

        _, issues = audit_critical_sources(
            now=now,
            status_loader=status_loader,
            dataset_loader=dataset_loader,
        )

        issue_ids = {row["source_id"] for row in issues}
        self.assertIn("hsreplay_battlegrounds_comps", issue_ids)
        self.assertNotIn("firestone_battlegrounds_comps", issue_ids)
        self.assertIn("hsreplay_battlegrounds_comps", CRITICAL_SOURCES)
        self.assertIn("firestone_battlegrounds_comps", CRITICAL_SOURCES)

    def test_audit_uses_resolved_public_dataset_age(self) -> None:
        now = datetime(2026, 8, 6, tzinfo=UTC)
        source_id = "hsguru_meta_standard_legend"
        fresh_candidate = {
            "fetched_at": (now - timedelta(hours=1)).isoformat(),
            "data": {"structured": {"provisional": True}},
        }
        stable_publication = {
            "fetched_at": (now - timedelta(hours=100)).isoformat(),
            "data": {"structured": {"strategies": [{"name": "stable"}]}},
        }

        with patch(
            "app.game_change_audit.resolve_public_dataset",
            side_effect=lambda candidate_source_id, candidate: (
                stable_publication
                if candidate_source_id == source_id
                else candidate
            ),
        ):
            _, issues = audit_critical_sources(
                now=now,
                status_loader=lambda _source_id: {"state": "ok"},
                dataset_loader=lambda _source_id: fresh_candidate,
            )

        issue = next(row for row in issues if row["source_id"] == source_id)
        self.assertEqual(issue["dataset_age_hours"], 100.0)
        self.assertIn("stale>72h", issue["reasons"])


if __name__ == "__main__":
    unittest.main()
