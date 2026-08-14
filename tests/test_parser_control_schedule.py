from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app import cli
from app.sources import SOURCE_BY_ID


class ParserControlScheduleTest(unittest.TestCase):
    def test_scheduled_refresh_enables_section_filtering(self) -> None:
        with patch("app.cli.refresh_sources", return_value=[]) as refresh:
            exit_code = cli.main(
                [
                    "refresh",
                    "--source",
                    "heartharena_tierlist",
                    "--scheduled",
                ]
            )

        self.assertEqual(exit_code, 0)
        refresh.assert_called_once_with(
            ["heartharena_tierlist"],
            tier=None,
            respect_section_controls=True,
        )

    def test_canonical_scheduled_refresh_claims_one_durable_occurrence(self) -> None:
        with patch(
            "app.schedule_ledger.claim_occurrence",
            return_value="refresh-all-daily:20260815T050000Z",
        ) as claim, patch("app.cli.refresh_sources", return_value=[]) as refresh:
            exit_code = cli.main(
                [
                    "refresh",
                    "--all",
                    "--scheduled",
                    "--schedule-id",
                    "refresh-all-daily",
                ]
            )

        self.assertEqual(exit_code, 0)
        claim.assert_called_once()
        self.assertEqual(claim.call_args.args[0], "refresh-all-daily")
        self.assertEqual(
            set(claim.call_args.args[1]),
            {
                source_id
                for source_id, source in SOURCE_BY_ID.items()
                if source.kind == "scrape"
            },
        )
        refresh.assert_called_once_with(
            None,
            tier=None,
            respect_section_controls=True,
            refresh_window_id="refresh-all-daily:20260815T050000Z",
        )

    def test_schedule_id_requires_scheduled_mode(self) -> None:
        with patch("app.cli.refresh_sources") as refresh:
            exit_code = cli.main(
                [
                    "refresh",
                    "--all",
                    "--schedule-id",
                    "refresh-all-daily",
                ]
            )

        self.assertEqual(exit_code, 2)
        refresh.assert_not_called()

    def test_scheduled_pipeline_is_skipped_when_its_section_is_disabled(self) -> None:
        with patch(
            "app.parser_control.is_source_scheduled_enabled", return_value=False
        ), patch(
            "app.hsreplay_archetypes_db.refresh_hsreplay_archetype_database"
        ) as refresh:
            exit_code = cli.main(["refresh-hsreplay-archetypes", "--scheduled"])

        self.assertEqual(exit_code, 0)
        refresh.assert_not_called()

    def test_scheduled_bg_minion_database_is_skipped_with_cards_section(self) -> None:
        with patch(
            "app.parser_control.is_source_scheduled_enabled", return_value=False
        ), patch(
            "app.hsreplay_bg_minions_db.refresh_bg_minion_database_sync"
        ) as refresh:
            exit_code = cli.main(["refresh-bg-minions-db", "--scheduled"])

        self.assertEqual(exit_code, 0)
        refresh.assert_not_called()

    def test_scheduled_check_is_safe_allowlisted_section_guard(self) -> None:
        with patch(
            "app.parser_control.is_source_scheduled_enabled", return_value=False
        ):
            disabled = cli.main([
                "scheduled-check",
                "--source",
                "hsguru_streamer_decks_legend_1000",
            ])
        unknown = cli.main(["scheduled-check", "--source", "not-a-source"])

        self.assertEqual(disabled, 1)
        self.assertEqual(unknown, 2)

    def test_direct_streamer_services_use_scheduled_exec_condition(self) -> None:
        root = Path(__file__).resolve().parent.parent / "systemd"
        for filename in (
            "hs-data-api-docker-firecrawl-streamer.service",
            "hs-data-api-firecrawl-streamer.service",
        ):
            text = (root / filename).read_text(encoding="utf-8")
            self.assertIn("ExecCondition=", text)
            self.assertIn(
                "app.cli scheduled-check --source hsguru_streamer_decks_legend_1000",
                text,
            )

    def test_all_systemd_generic_refreshes_honor_section_controls(self) -> None:
        root = Path(__file__).resolve().parent.parent / "systemd"
        offenders: list[str] = []
        for path in root.glob("*.service"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "app.cli refresh " in line and " --scheduled" not in line:
                    offenders.append(f"{path.name}: {line}")
                if any(
                    command in line
                    for command in (
                        "app.cli refresh-hsreplay-archetypes",
                        "app.cli refresh-bg-hero-details",
                        "app.cli refresh-bg-minions-db",
                    )
                ) and " --scheduled" not in line:
                    offenders.append(f"{path.name}: {line}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
