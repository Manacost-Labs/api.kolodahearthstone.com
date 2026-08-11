from __future__ import annotations

import json
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.api_only_sources import blocks_browser_fallback
from app.dataset_regression import (
    check_dataset_regression,
    estimate_filled_metric_count,
    estimate_metric_count,
)
from app.fetch_routes import _PROXYLESS_API_SOURCE_IDS
from app.fetcher import _fetch_hsreplay_api_source
from app.firestone_standard import (
    FIRESTONE_STANDARD_ARCHETYPES_URL,
    FIRESTONE_STANDARD_DECKS_URL,
    fetch_firestone_standard,
)
from app.parser_control_registry import SOURCE_LABELS_RU, SOURCE_TO_SECTION
from app.source_contracts import contract_quality_report, get_contract
from app.source_tiers import LIGHT_API_IDS
from app.source_validators import validate_structured
from app.sources import SOURCE_BY_ID
from app.structured_schema import StructuredSchemaError, validate_structured_schema

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = SOURCE_BY_ID["firestone_standard"]


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _normalized_payload(*, deck_count: int = 20, archetype_count: int = 20) -> dict:
    last_updated = datetime.now(UTC).isoformat()
    decks = [
        {
            "decklist": f"deck-code-{idx}",
            "deck_code": f"deck-code-{idx}",
            "archetype_id": 1_000 + idx,
            "archetype_name": f"archetype-{idx}",
            "player_class": "mage",
            "games": 100 + idx,
            "wins": 50 + idx,
            "winrate": 0.5,
            "core_cards": ["CORE_CARD_001"],
            "card_variations": {"added": ["CARD_002"], "removed": []},
            "hero_card_ids": ["HERO_08"],
            "format": "standard",
            "rank_bracket": "legend",
            "time_period": "last-patch",
            "last_updated": last_updated,
        }
        for idx in range(deck_count)
    ]
    archetypes = [
        {
            "archetype_id": 2_000 + idx,
            "archetype_name": f"meta-archetype-{idx}",
            "player_class": "mage",
            "games": 200 + idx,
            "wins": 100 + idx,
            "winrate": 0.5,
            "core_cards": ["CORE_CARD_001"],
            "hero_card_ids": ["HERO_08"],
            "format": "standard",
        }
        for idx in range(archetype_count)
    ]
    metadata = {
        "data_points": 50_000,
        "last_updated": last_updated,
        "rank_bracket": "legend",
        "time_period": "last-patch",
        "format": "standard",
    }
    return {
        "type": "firestone_standard",
        "format": "standard",
        "rank_bracket": "legend",
        "time_period": "last-patch",
        "metadata": {"decks": dict(metadata), "archetypes": dict(metadata)},
        "decks": decks,
        "archetypes": archetypes,
        "total_decks": len(decks),
        "total_archetypes": len(archetypes),
    }


class FirestoneStandardFetchTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_both_official_snapshots_and_normalizes_stable_schema(
        self,
    ) -> None:
        decks = httpx.Response(
            200,
            content=json.dumps(_fixture("firestone_standard_decks.json")).encode(),
        )
        archetypes = httpx.Response(
            200,
            content=json.dumps(_fixture("firestone_standard_archetypes.json")).encode(),
        )
        fetch = AsyncMock(side_effect=[decks, archetypes])

        with patch("app.firestone_standard._get_static_json", fetch):
            result = await fetch_firestone_standard(SOURCE)

        self.assertEqual(
            {call.args[0] for call in fetch.await_args_list},
            {FIRESTONE_STANDARD_DECKS_URL, FIRESTONE_STANDARD_ARCHETYPES_URL},
        )
        self.assertEqual(result["type"], "firestone_standard")
        self.assertEqual(result["total_decks"], 2)
        self.assertEqual(result["total_archetypes"], 2)
        self.assertEqual(result["metadata"]["decks"]["data_points"], 55_393)
        self.assertEqual(result["metadata"]["archetypes"]["data_points"], 82_393)
        first = result["decks"][0]
        self.assertEqual(first["deck_code"], first["decklist"])
        self.assertEqual(first["archetype_id"], 40_968)
        self.assertEqual(first["player_class"], "deathknight")
        self.assertEqual(first["games"], 281)
        self.assertEqual(first["wins"], 149)
        self.assertEqual(first["winrate"], 0.5302)
        self.assertEqual(first["core_cards"], ["DINO_410", "DINO_411", "DINO_417"])
        self.assertEqual(first["card_variations"]["added"].count("EDR_814"), 2)
        self.assertEqual(result["archetypes"][0]["archetype_name"], "pure-paladin")
        self.assertEqual(result["_fetch_backend"], "proxyless_direct")
        self.assertTrue(validate_structured_schema(result)["validated"])

    async def test_fetch_dispatch_publishes_firestone_api_backend(self) -> None:
        structured = _normalized_payload()
        with patch(
            "app.firestone_standard.fetch_firestone_standard",
            AsyncMock(return_value=structured),
        ) as fetch:
            parsed = await _fetch_hsreplay_api_source(SOURCE)

        fetch.assert_awaited_once_with(SOURCE)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["_backend"], "firestone_api")
        self.assertEqual(parsed["structured"]["total_decks"], 20)


class FirestoneStandardIntegrationTest(unittest.TestCase):
    def test_source_is_api_only_proxyless_and_admin_visible(self) -> None:
        self.assertIn(SOURCE.id, LIGHT_API_IDS)
        self.assertIn(SOURCE.id, _PROXYLESS_API_SOURCE_IDS)
        self.assertTrue(blocks_browser_fallback(SOURCE.id))
        self.assertEqual(SOURCE_TO_SECTION[SOURCE.id], "traditional-standard-meta")
        self.assertIn("Firestone", SOURCE_LABELS_RU[SOURCE.id])
        contract = get_contract(SOURCE.id)
        self.assertIsNotNone(contract)
        assert contract is not None
        self.assertFalse(contract.allow_browser_fallback)
        self.assertEqual(contract.fallback_policy, "api_only")

    def test_contract_requires_minimum_rows_in_both_collections(self) -> None:
        valid = _normalized_payload()
        report = contract_quality_report(SOURCE.id, valid)

        self.assertTrue(report["ok"], report["warnings"])
        self.assertEqual(report["rows_total"], 40)
        self.assertEqual(report["minimum_rows"], 20)
        self.assertEqual(report["minimum_collections"]["decks"]["minimum_rows"], 10)
        self.assertEqual(
            report["minimum_collections"]["archetypes"]["minimum_rows"], 10
        )

        sparse_decks = _normalized_payload(deck_count=9, archetype_count=31)
        report = contract_quality_report(SOURCE.id, sparse_decks)
        self.assertFalse(report["ok"])
        self.assertIn("decks has too few rows", "; ".join(report["warnings"]))

        sparse_archetypes = _normalized_payload(deck_count=31, archetype_count=9)
        report = contract_quality_report(SOURCE.id, sparse_archetypes)
        self.assertFalse(report["ok"])
        self.assertIn("archetypes has too few rows", "; ".join(report["warnings"]))

    def test_semantic_validator_checks_both_collections_and_deck_decodability(
        self,
    ) -> None:
        structured = _normalized_payload(deck_count=10, archetype_count=10)
        fake_module = SimpleNamespace(
            decode_deck_code=lambda code: {"ok": not str(code).endswith(("-8", "-9"))}
        )
        with patch.dict(sys.modules, {"app.deck_decode": fake_module}):
            report = validate_structured(SOURCE.id, structured)

        self.assertFalse(report.ok)
        self.assertEqual(report.metrics["decks"], 10)
        self.assertEqual(report.metrics["archetypes"], 10)
        self.assertEqual(report.metrics["decodable_decks"], 8)
        self.assertIn(
            "firestone_standard.invalid_deck_codes",
            {issue.code for issue in report.issues},
        )

        fake_module.decode_deck_code = lambda _code: {"ok": True}
        with patch.dict(sys.modules, {"app.deck_decode": fake_module}):
            valid_report = validate_structured(SOURCE.id, structured)
        self.assertTrue(valid_report.ok, valid_report.reason)

    def test_fixture_deck_code_is_decodable_by_shared_decoder(self) -> None:
        try:
            from app.deck_decode import decode_deck_code
        except ModuleNotFoundError as exc:
            self.skipTest(f"optional test dependency unavailable: {exc}")

        code = _fixture("firestone_standard_decks.json")["deckStats"][0]["decklist"]
        with patch("app.deck_decode.cards_by_dbfid", return_value={}):
            decoded = decode_deck_code(code)
        self.assertTrue(decoded["ok"], decoded)
        self.assertEqual(decoded["card_count"], 30)

    def test_schema_rejects_invalid_variation_shape(self) -> None:
        structured = _normalized_payload()
        structured["decks"][0]["card_variations"]["added"] = "CARD_002"

        with self.assertRaisesRegex(
            StructuredSchemaError,
            r"card_variations\.added must be a list",
        ):
            validate_structured_schema(structured)

    def test_schema_and_semantics_reject_wrong_scope(self) -> None:
        structured = _normalized_payload()
        structured["decks"][0]["format"] = "wild"
        structured["decks"][0]["rank_bracket"] = "gold"
        structured["decks"][0]["time_period"] = "all-time"
        structured["archetypes"][0]["format"] = "wild"

        with self.assertRaisesRegex(StructuredSchemaError, r"format must be standard"):
            validate_structured_schema(structured)

        report = validate_structured(SOURCE.id, structured)
        self.assertFalse(report.ok)
        issue_codes = {issue.code for issue in report.issues}
        self.assertIn("firestone_standard.invalid_deck_scope", issue_codes)
        self.assertIn("firestone_standard.invalid_archetype_scope", issue_codes)

    def test_schema_and_semantics_reject_invalid_or_stale_upstream_time(self) -> None:
        now = datetime(2026, 8, 11, 21, tzinfo=UTC)
        structured = _normalized_payload()
        structured["metadata"]["decks"]["last_updated"] = "not-a-timestamp"
        structured["metadata"]["archetypes"]["last_updated"] = (
            now - timedelta(hours=37)
        ).isoformat()

        with self.assertRaisesRegex(StructuredSchemaError, r"ISO timestamp"):
            validate_structured_schema(structured)

        with patch("app.source_validators._validation_now_utc", return_value=now):
            report = validate_structured(SOURCE.id, structured)

        self.assertFalse(report.ok)
        issue_codes = {issue.code for issue in report.issues}
        self.assertIn("firestone_standard.invalid_upstream_timestamp", issue_codes)
        self.assertIn("firestone_standard.stale_upstream_snapshot", issue_codes)

        future = _normalized_payload()
        for metadata in future["metadata"].values():
            metadata["last_updated"] = (now + timedelta(hours=7)).isoformat()
        with patch("app.source_validators._validation_now_utc", return_value=now):
            future_report = validate_structured(SOURCE.id, future)
        self.assertIn(
            "firestone_standard.future_upstream_timestamp",
            {issue.code for issue in future_report.issues},
        )

    def test_regression_counts_both_collections_and_complete_metrics(self) -> None:
        structured = _normalized_payload(deck_count=12, archetype_count=11)
        dataset = {"structured": structured}

        self.assertEqual(estimate_metric_count(SOURCE, dataset), 23)
        self.assertEqual(estimate_filled_metric_count(SOURCE, dataset), 23)

    def test_early_post_patch_mode_allows_small_but_complete_reset(self) -> None:
        previous = {"structured": _normalized_payload(deck_count=150, archetype_count=40)}
        current = {"structured": _normalized_payload(deck_count=10, archetype_count=10)}

        regressed, _message, _extra = check_dataset_regression(
            SOURCE,
            previous_data=previous,
            new_data=current,
        )
        self.assertTrue(regressed)

        with patch("app.dataset_regression.policy_for", return_value=SimpleNamespace()):
            regressed, message, extra = check_dataset_regression(
                SOURCE,
                previous_data=previous,
                new_data=current,
            )

        self.assertFalse(regressed)
        self.assertIsNone(message)
        self.assertTrue(extra["post_patch_regression_bypass"])

    def test_regression_blocks_loss_of_one_collection_even_if_total_stays_large(
        self,
    ) -> None:
        previous = {
            "structured": _normalized_payload(deck_count=157, archetype_count=41)
        }
        current = {
            "structured": _normalized_payload(deck_count=157, archetype_count=10)
        }

        regressed, message, extra = check_dataset_regression(
            SOURCE,
            previous_data=previous,
            new_data=current,
        )

        self.assertTrue(regressed)
        self.assertIn("archetypes count dropped 41 -> 10", message or "")
        self.assertEqual(extra["collections"]["archetypes"]["threshold"], 20)
