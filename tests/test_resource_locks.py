from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.fetcher import refresh_sources
from app.resource_locks import ResourceLocked, ResourceLockSet
from app.sources import SOURCES


def test_resource_ids_are_deduplicated_and_sorted(tmp_path) -> None:
    lock_set = ResourceLockSet(
        ["source-zeta", "source-alpha", "source-zeta"],
        lock_dir=tmp_path,
    )

    assert lock_set.resource_ids == ("source-alpha", "source-zeta")


def test_different_resources_do_not_conflict(tmp_path) -> None:
    with (
        ResourceLockSet(["source-alpha"], lock_dir=tmp_path),
        ResourceLockSet(["source-beta"], lock_dir=tmp_path),
    ):
        pass


def test_same_resource_returns_locked_outcome_without_changing_owner_metadata(
    tmp_path,
) -> None:
    owner = ResourceLockSet(
        ["source-alpha"],
        lock_dir=tmp_path,
        metadata={"run_id": "owner-run"},
    )

    with owner:
        path = owner.paths["source-alpha"]
        metadata_before = path.read_text(encoding="utf-8")

        with (
            pytest.raises(ResourceLocked) as raised,
            ResourceLockSet(
                ["source-alpha"],
                lock_dir=tmp_path,
                metadata={"run_id": "contender-run"},
            ),
        ):
            pass

        assert path.read_text(encoding="utf-8") == metadata_before
        assert raised.value.as_outcome() == {
            "state": "locked",
            "skipped": True,
            "reason": "resource_locked",
            "locked_resource": "source-alpha",
            "owner": {
                "pid": os.getpid(),
                "resource_id": "source-alpha",
                "run_id": "owner-run",
                "acquired_at": raised.value.owner["acquired_at"],
            },
        }


def test_partial_acquire_is_released_when_later_resource_is_locked(tmp_path) -> None:
    with ResourceLockSet(["source-beta"], lock_dir=tmp_path):
        with (
            pytest.raises(ResourceLocked),
            ResourceLockSet(
                ["source-beta", "source-alpha"],
                lock_dir=tmp_path,
            ),
        ):
            pass

        with ResourceLockSet(["source-alpha"], lock_dir=tmp_path):
            pass


def test_lock_files_are_not_unlinked_after_release(tmp_path) -> None:
    lock_set = ResourceLockSet(["source-alpha"], lock_dir=tmp_path)

    with lock_set:
        path = lock_set.paths["source-alpha"]
        assert path.exists()

    assert path.exists()


def test_refresh_of_locked_source_returns_skipped_outcome(tmp_path) -> None:
    source_id = next(source.id for source in SOURCES if source.kind == "scrape")
    lock_dir = tmp_path / ".locks"

    with (
        ResourceLockSet(
            [source_id],
            lock_dir=lock_dir,
            metadata={"run_id": "owner-run"},
        ),
        patch("app.resource_locks.data_dir", return_value=tmp_path),
        patch(
            "app.fetcher._refresh_sources_unlocked",
            new_callable=AsyncMock,
        ) as unlocked_refresh,
    ):
        result = asyncio.run(refresh_sources([source_id]))

    assert result == [
        {
            "source_id": source_id,
            "state": "locked",
            "skipped": True,
            "reason": "resource_locked",
            "locked_resource": source_id,
            "owner": {
                "pid": os.getpid(),
                "resource_id": source_id,
                "run_id": "owner-run",
                "acquired_at": result[0]["owner"]["acquired_at"],
            },
        }
    ]
    unlocked_refresh.assert_not_awaited()


def test_refresh_of_different_source_runs_while_other_source_is_locked(
    tmp_path,
) -> None:
    source_ids = [source.id for source in SOURCES if source.kind == "scrape"][:2]
    assert len(source_ids) == 2
    locked_source_id, requested_source_id = source_ids
    expected = [{"source_id": requested_source_id, "state": "ok"}]

    with (
        ResourceLockSet([locked_source_id], lock_dir=tmp_path / ".locks"),
        patch("app.resource_locks.data_dir", return_value=tmp_path),
        patch(
            "app.fetcher._refresh_sources_unlocked",
            new_callable=AsyncMock,
            return_value=expected,
        ) as unlocked_refresh,
    ):
        result = asyncio.run(refresh_sources([requested_source_id]))

    assert result == expected
    unlocked_refresh.assert_awaited_once_with(
        [requested_source_id],
        tier_filter=None,
        respect_section_controls=False,
    )


def test_one_locked_source_does_not_block_another_requested_source(tmp_path) -> None:
    source_ids = [source.id for source in SOURCES if source.kind == "scrape"][:2]
    assert len(source_ids) == 2
    locked_source_id, available_source_id = source_ids
    refreshed = [{"source_id": available_source_id, "state": "ok"}]

    with (
        ResourceLockSet(
            [locked_source_id],
            lock_dir=tmp_path / ".locks",
            metadata={"run_id": "owner-run"},
        ),
        patch("app.resource_locks.data_dir", return_value=tmp_path),
        patch(
            "app.fetcher._refresh_sources_unlocked",
            new_callable=AsyncMock,
            return_value=refreshed,
        ) as unlocked_refresh,
    ):
        result = asyncio.run(refresh_sources([locked_source_id, available_source_id]))

    assert [row for row in result if row.get("state") == "ok"] == refreshed
    locked = [row for row in result if row.get("state") == "locked"]
    assert len(locked) == 1
    assert locked[0]["source_id"] == locked_source_id
    assert locked[0]["locked_resource"] == locked_source_id
    unlocked_refresh.assert_awaited_once_with(
        [available_source_id],
        tier_filter=None,
        respect_section_controls=False,
    )


def test_scheduled_window_is_shared_by_locked_and_available_outcomes(tmp_path) -> None:
    source_ids = [source.id for source in SOURCES if source.kind == "scrape"][:2]
    locked_source_id, available_source_id = source_ids
    window_id = "refresh-all-daily:20260814T050000Z"
    with (
        ResourceLockSet([locked_source_id], lock_dir=tmp_path / ".locks"),
        patch("app.resource_locks.data_dir", return_value=tmp_path),
        patch(
            "app.fetcher._record_reliability_results_best_effort"
        ) as record_reliability,
        patch(
            "app.fetcher._refresh_sources_unlocked",
            new_callable=AsyncMock,
            return_value=[{"source_id": available_source_id, "state": "ok"}],
        ) as unlocked_refresh,
    ):
        asyncio.run(
            refresh_sources(
                [locked_source_id, available_source_id],
                refresh_window_id=window_id,
            )
        )

    assert record_reliability.call_args.kwargs["refresh_window_id"] == window_id
    unlocked_refresh.assert_awaited_once_with(
        [available_source_id],
        tier_filter=None,
        respect_section_controls=False,
        refresh_window_id=window_id,
    )


def test_legacy_orchestration_scripts_delegate_to_shared_locked_refresh() -> None:
    root = Path(__file__).resolve().parents[1]
    streamer = (root / "scripts" / "firecrawl-streamer-decks.py").read_text(
        encoding="utf-8"
    )
    recovery = (root / "scripts" / "recover-failed-sources.sh").read_text(
        encoding="utf-8"
    )

    assert "from app.cli import main" in streamer
    assert '"refresh"' in streamer
    assert '"--source"' in streamer
    assert "SOURCE_ID" in streamer
    assert "RefreshLock" not in streamer
    assert ".refresh.lock" not in recovery
