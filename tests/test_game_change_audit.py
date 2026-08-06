from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from app.game_change_audit import (
    CRITICAL_SOURCES,
    audit_critical_sources,
    build_card_snapshot,
    compare_card_snapshots,
    relevant_wiki_changes,
)


class GameChangeAuditTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
