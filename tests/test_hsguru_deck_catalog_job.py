from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "refresh-hsguru-deck-catalog.py"
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
