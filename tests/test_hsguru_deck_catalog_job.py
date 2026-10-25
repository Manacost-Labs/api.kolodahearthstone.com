from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "refresh-hsguru-deck-catalog.py"
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hsguru_deck_catalog_job", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_partial_catalogs_are_handled_degradation() -> None:
    module = _load_script()
    module.refresh_all_deck_catalogs = AsyncMock(
        return_value={
            "state": "partial",
            "datasets": {"standard_legend": {"decks": 10}},
            "errors": {"wild_legend": "upstream unavailable"},
        }
    )

    assert asyncio.run(module.main()) == 10


def test_catalog_failure_without_usable_data_is_error() -> None:
    module = _load_script()
    module.refresh_all_deck_catalogs = AsyncMock(
        return_value={"state": "partial", "datasets": {}, "errors": {"all": "failed"}}
    )

    assert asyncio.run(module.main()) == 1


def test_catalog_run_held_by_another_run_is_degraded_not_an_error() -> None:
    module = _load_script()
    module.refresh_all_deck_catalogs = AsyncMock(
        return_value={"state": "locked", "skipped": True, "datasets": {}, "errors": {}}
    )

    assert asyncio.run(module.main()) == 10


def test_second_catalog_run_skips_while_the_first_owns_the_catalogs() -> None:
    from app.hsguru_deck_catalog_refresh import (
        CATALOG_LOCK_ID,
        refresh_all_deck_catalogs,
    )
    from app.resource_locks import ResourceLockSet

    refresh = AsyncMock(return_value=[{"deck_code": "AAECA"}])
    with ResourceLockSet([CATALOG_LOCK_ID]):
        result = asyncio.run(
            refresh_all_deck_catalogs(
                refresh=refresh,
                refresh_fallback=AsyncMock(),
                join=lambda: {"ok": True},
                catalog_periods=lambda: (),
            )
        )

    assert result["state"] == "locked"
    assert result["locked_resource"] == CATALOG_LOCK_ID
    refresh.assert_not_awaited()
