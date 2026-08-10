from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from app.refresh_log import _level_for
from app.source_state import (
    EFFECTIVE_OK_CACHED,
    ERROR_STATES,
    FAILURE_STATES,
    WARN_STATES,
    SourceState,
)


class SourceStateWireFormatTest(unittest.TestCase):
    def test_json_dumps_emits_raw_value(self) -> None:
        self.assertEqual(json.dumps({"state": SourceState.OK}), '{"state": "ok"}')
        self.assertEqual(
            json.dumps({"state": SourceState.QUALITY_ERROR}),
            '{"state": "quality_error"}',
        )

    def test_fstring_emits_raw_value(self) -> None:
        self.assertEqual(f"{SourceState.QUALITY_ERROR}", "quality_error")
        self.assertEqual(
            str(SourceState.BLOCKED_BY_PROTECTION), "blocked_by_protection"
        )

    def test_str_enum_equality_with_plain_strings(self) -> None:
        self.assertEqual(SourceState.OK, "ok")
        self.assertEqual("never_fetched", SourceState.NEVER_FETCHED)
        self.assertIn("fetch_error", ERROR_STATES)
        self.assertEqual(SourceState.TIMED_OUT, "timed_out")
        self.assertIn(SourceState.TIMED_OUT, ERROR_STATES)
        self.assertNotIn("quality_error", ERROR_STATES)
        self.assertIn("quality_error", WARN_STATES)
        self.assertIn("quality_error", FAILURE_STATES)

    def test_effective_ok_cached_is_plain_constant(self) -> None:
        self.assertEqual(EFFECTIVE_OK_CACHED, "ok_cached")
        self.assertNotIsInstance(EFFECTIVE_OK_CACHED, SourceState)

    def test_all_values_roundtrip(self) -> None:
        for member in SourceState:
            self.assertIs(SourceState(member.value), member)
            self.assertEqual(json.dumps(member), json.dumps(member.value))

    def test_failure_state_literals_are_centralized(self) -> None:
        root = Path(__file__).resolve().parents[1] / "app"
        pattern = re.compile(
            r'"(?:quality_error|fetch_error|blocked_by_protection|http_error|'
            r'proxy_required|timed_out|ok_cached)"(?!\s*:)'
        )
        offenders = []
        for path in root.rglob("*.py"):
            if path.name == "source_state.py":
                continue
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(root.parent)}:{line_number}")

        self.assertEqual(offenders, [])


class LevelForMappingUnchangedTest(unittest.TestCase):
    def test_error_states(self) -> None:
        for state in (
            "fetch_error",
            "http_error",
            "blocked_by_protection",
            "proxy_required",
            "timed_out",
        ):
            self.assertEqual(_level_for(state, None, None), "error", state)

    def test_warn_states(self) -> None:
        for state in ("quality_error", "partial"):
            self.assertEqual(_level_for(state, None, None), "warn", state)

    def test_info_otherwise(self) -> None:
        for state in ("ok", "never_fetched", None, "something_else"):
            self.assertEqual(_level_for(state, None, None), "info", state)

    def test_explicit_level_and_error_type_precedence(self) -> None:
        self.assertEqual(_level_for("ok", "warn", None), "warn")
        self.assertEqual(_level_for("ok", None, "TimeoutError"), "error")

    def test_enum_members_map_like_strings(self) -> None:
        self.assertEqual(_level_for(SourceState.FETCH_ERROR, None, None), "error")
        self.assertEqual(_level_for(SourceState.TIMED_OUT, None, None), "error")
        self.assertEqual(_level_for(SourceState.QUALITY_ERROR, None, None), "warn")
        self.assertEqual(_level_for(SourceState.OK, None, None), "info")


if __name__ == "__main__":
    unittest.main()
