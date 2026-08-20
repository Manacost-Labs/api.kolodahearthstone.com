from __future__ import annotations

import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "firecrawl-streamer-decks.py"
SOURCE_ID = "hsguru_streamer_decks_legend_1000"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("streamer_decks_job", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_streamer_job_uses_shared_refresh_and_propagates_failure() -> None:
    module = _load_script()
    module.cli_main = Mock(return_value=1)
    module._load_env = Mock()
    module._scrape_html = Mock(side_effect=AssertionError("legacy direct fetch path used"))
    module._refresh_derived_fun_decks = Mock()

    with patch("app.resource_locks.ResourceLockSet", return_value=nullcontext()):
        result = module.main()

    assert result == 1
    module.cli_main.assert_called_once_with(
        ["refresh", "--source", SOURCE_ID, "--scheduled", "--require-all-ok"]
    )
    module._refresh_derived_fun_decks.assert_not_called()


def test_streamer_job_refreshes_derived_data_only_after_success() -> None:
    module = _load_script()
    module.cli_main = Mock(return_value=0)
    module._load_env = Mock()
    module._scrape_html = Mock(side_effect=AssertionError("legacy direct fetch path used"))
    module._refresh_derived_fun_decks = Mock(return_value={"ok": True})

    with (
        patch("app.resource_locks.ResourceLockSet", return_value=nullcontext()),
        patch("builtins.print"),
    ):
        result = module.main()

    assert result == 0
    module._refresh_derived_fun_decks.assert_called_once_with()


def test_streamer_job_does_not_hide_derived_refresh_failure() -> None:
    module = _load_script()
    module.cli_main = Mock(return_value=0)
    module._refresh_derived_fun_decks = Mock(
        return_value={"ok": False, "error": "derived refresh failed"}
    )

    with patch("builtins.print"):
        result = module.main()

    assert result == 1


def test_streamer_job_forwards_durable_schedule_id() -> None:
    module = _load_script()
    module.cli_main = Mock(return_value=1)

    result = module.main(["--schedule-id", "refresh-streamer-decks"])

    assert result == 1
    assert module.cli_main.call_args.args[0][-2:] == [
        "--schedule-id",
        "refresh-streamer-decks",
    ]
