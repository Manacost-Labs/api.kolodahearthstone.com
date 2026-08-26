from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.hsreplay_legendaries_api import (
    HS_BUCKET_TO_CLASS_KEY,
    _groups_from_class_buckets,
    _load_arena_card_stats_index,
    _normalize_firecrawl_group,
    _package_row_retrieval,
    enrich_legendary_groups,
    fetch_legendary_groups,
    normalize_legendary_package,
)
from app.scrapers.quality import validate_parsed_data
from app.source_contracts import contract_quality_report
from app.source_validators import validate_structured
from app.sources import SOURCE_BY_ID
from app.structured_schema import StructuredSchemaError, validate_structured_schema


class LegendaryGroupsByClassTests(unittest.TestCase):
    @staticmethod
    def _packages() -> list[dict[str, object]]:
        return [
            {
                "package_key_card_id": f"CARD_{index}",
                "package_card_ids": [f"CARD_{index}"],
                "win_rate": 50,
                "pick_rate": 10,
                "offer_rate": 20,
                "score": 1,
            }
            for index in range(10)
        ]

    @classmethod
    def _full_bucket_data(cls) -> dict[str, list[dict[str, object]]]:
        packages = cls._packages()
        return {bucket: list(packages) for bucket in HS_BUCKET_TO_CLASS_KEY}

    def test_api_payload_counts_unique_packages_not_class_duplicates(self) -> None:
        payload = {"data": self._full_bucket_data()}
        with (
            patch(
                "app.hsreplay_legendaries_api.fetch_hsreplay_json",
                new=AsyncMock(return_value=payload),
            ),
            patch(
                "app.hsreplay_legendaries_api._load_arena_card_stats_index",
                new=AsyncMock(return_value=({}, "none")),
            ),
        ):
            result = asyncio.run(fetch_legendary_groups(locale="enUS"))

        self.assertEqual(result["completeness_schema_version"], 1)
        self.assertEqual(result["row_retrieval"]["raw_rows"], 10)
        self.assertEqual(result["row_retrieval"]["eligible_rows"], 10)
        self.assertEqual(result["row_retrieval"]["normalized_rows"], 10)
        self.assertEqual(result["row_retrieval"]["unexplained_drops"], 0)
        self.assertEqual(
            result["row_retrieval"]["bucket_coverage"],
            {
                "expected_buckets": list(HS_BUCKET_TO_CLASS_KEY),
                "observed_buckets": list(HS_BUCKET_TO_CLASS_KEY),
                "missing_buckets": [],
                "unknown_buckets": [],
                "duplicate_bucket_package_keys": [],
            },
        )

    def test_api_payload_publishes_freshness_and_unverifiable_population(self) -> None:
        payload = {
            "metadata": {"meta_period_id": 16},
            "selected_params": [
                "ArenaGameTypeFilter.BGT_UNDERGROUND_ARENA",
                "ArenaTimestampRangeFilter.CURRENT_META_PERIOD_UNDERGROUND",
            ],
            "data": self._full_bucket_data(),
        }
        freshness = {
            "status": "fresh",
            "reason": None,
            "observed_at": "2026-08-14T02:20:00+00:00",
            "age_seconds": 60,
            "evidence": ["last_modified"],
            "response_headers": {},
            "meta_period_id": 16,
            "selected_params": payload["selected_params"],
            "filters_match": True,
        }
        with (
            patch(
                "app.hsreplay_legendaries_api.fetch_hsreplay_json",
                new=AsyncMock(return_value=payload),
            ),
            patch(
                "app.hsreplay_legendaries_api._load_arena_card_stats_index",
                new=AsyncMock(return_value=({}, "none")),
            ),
            patch(
                "app.hsreplay_legendaries_api.get_hsreplay_json_target_headers",
                return_value={"last-modified": "Fri, 14 Aug 2026 02:19:00 GMT"},
            ) as headers,
            patch(
                "app.hsreplay_legendaries_api.build_hsreplay_arena_upstream_freshness",
                return_value=freshness,
            ) as build,
        ):
            result = asyncio.run(fetch_legendary_groups(locale="enUS"))

        self.assertEqual(result["upstream_freshness"], freshness)
        self.assertEqual(result["population_completeness"], "unverifiable")
        headers.assert_called_once_with(
            "https://hsreplay.net/api/v1/arena/card_packages/"
        )
        build.assert_called_once()
        validate_structured_schema(result)
        report = contract_quality_report("hsreplay_arena_legendaries", result)
        self.assertTrue(report["ok"], report["warnings"])
        self.assertTrue(report["retrieval_complete"])

    def test_missing_class_bucket_fails_completeness_contract_and_schema(self) -> None:
        data = self._full_bucket_data()
        data.pop("MAGE")
        groups = _groups_from_class_buckets(data, locale="enUS")
        payload = {
            "type": "arena_legendary_groups",
            "completeness_schema_version": 1,
            "row_retrieval": _package_row_retrieval(data, groups),
            "groups": groups,
        }

        report = contract_quality_report("hsreplay_arena_legendaries", payload)

        self.assertFalse(report["ok"])
        self.assertFalse(report["retrieval_complete"])
        self.assertEqual(
            report["row_retrieval"]["bucket_coverage"]["missing_buckets"],
            ["MAGE"],
        )
        with self.assertRaisesRegex(StructuredSchemaError, "missing_buckets"):
            validate_structured_schema(payload)

    def test_unknown_class_bucket_fails_completeness_contract_and_schema(self) -> None:
        data = self._full_bucket_data()
        data["MONK"] = []
        groups = _groups_from_class_buckets(data, locale="enUS")
        payload = {
            "type": "arena_legendary_groups",
            "completeness_schema_version": 1,
            "row_retrieval": _package_row_retrieval(data, groups),
            "groups": groups,
        }

        report = contract_quality_report("hsreplay_arena_legendaries", payload)

        self.assertFalse(report["ok"])
        self.assertFalse(report["retrieval_complete"])
        self.assertEqual(
            report["row_retrieval"]["bucket_coverage"]["unknown_buckets"],
            ["MONK"],
        )
        with self.assertRaisesRegex(StructuredSchemaError, "unknown_buckets"):
            validate_structured_schema(payload)

    def test_all_only_fallback_is_usable_but_not_retrieval_complete(self) -> None:
        data = {"ALL": self._packages()}
        groups = _groups_from_class_buckets(data, locale="enUS")
        payload = {
            "type": "arena_legendary_groups",
            "completeness_schema_version": 1,
            "row_retrieval": _package_row_retrieval(data, groups),
            "groups": groups,
        }

        report = contract_quality_report("hsreplay_arena_legendaries", payload)

        self.assertEqual(len(groups), 10)
        self.assertFalse(report["ok"])
        self.assertFalse(report["retrieval_complete"])
        self.assertEqual(
            report["row_retrieval"]["bucket_coverage"]["observed_buckets"],
            ["ALL"],
        )
        publishable, reason = validate_parsed_data(
            SOURCE_BY_ID["hsreplay_arena_legendaries"],
            {"structured": payload},
        )
        self.assertFalse(publishable, reason)

    def test_duplicate_package_key_inside_one_bucket_fails_completeness(self) -> None:
        data = self._full_bucket_data()
        data["MAGE"].append(dict(data["MAGE"][0]))
        groups = _groups_from_class_buckets(data, locale="enUS")
        payload = {
            "type": "arena_legendary_groups",
            "completeness_schema_version": 1,
            "row_retrieval": _package_row_retrieval(data, groups),
            "groups": groups,
        }

        report = contract_quality_report("hsreplay_arena_legendaries", payload)

        self.assertFalse(report["ok"])
        self.assertFalse(report["retrieval_complete"])
        self.assertEqual(
            report["row_retrieval"]["bucket_coverage"][
                "duplicate_bucket_package_keys"
            ],
            ["MAGE:CARD_0"],
        )

    def test_package_card_ids_are_strict_nonempty_string_lists(self) -> None:
        for invalid in (None, [], "CARD_1", [""], [False]):
            with self.subTest(invalid=invalid):
                package = dict(self._packages()[0])
                package["package_card_ids"] = invalid
                with self.assertRaises((TypeError, ValueError)):
                    normalize_legendary_package(package, locale="enUS")

    def test_invalid_rates_and_scores_are_not_formatted_as_valid_metrics(self) -> None:
        invalid_rates = (False, float("nan"), float("inf"), -1, 101, "unknown")
        for raw_field in ("win_rate", "pick_rate", "offer_rate"):
            for invalid in invalid_rates:
                with self.subTest(field=raw_field, invalid=invalid):
                    package = dict(self._packages()[0])
                    package[raw_field] = invalid
                    with self.assertRaises((TypeError, ValueError)):
                        normalize_legendary_package(package, locale="enUS")

        for invalid in (False, float("nan"), float("inf"), "unknown"):
            with self.subTest(score=invalid):
                package = dict(self._packages()[0])
                package["score"] = invalid
                with self.assertRaises((TypeError, ValueError)):
                    normalize_legendary_package(package, locale="enUS")

    def test_invalid_present_winrate_cannot_be_explained_by_zero_pick_rate(self) -> None:
        package = dict(self._packages()[0])
        package["win_rate"] = float("nan")
        package["pick_rate"] = 0

        with self.assertRaises(ValueError):
            normalize_legendary_package(package, locale="enUS")

    def test_null_scores_are_explicitly_unavailable(self) -> None:
        data = self._full_bucket_data()
        data["WARRIOR"][0] = {
            **data["WARRIOR"][0],
            "score": None,
        }
        data["ALL"][1] = {
            **data["ALL"][1],
            "score": None,
        }
        groups = _groups_from_class_buckets(data, locale="enUS")
        payload = {
            "type": "arena_legendary_groups",
            "completeness_schema_version": 1,
            "population_completeness": "unverifiable",
            "upstream_freshness": {
                "status": "unknown",
                "reason": "transport_evidence_unavailable",
                "observed_at": "2026-08-14T02:20:00+00:00",
                "age_seconds": None,
                "evidence": [],
                "response_headers": {},
            },
            "row_retrieval": _package_row_retrieval(data, groups),
            "groups": groups,
        }

        warrior = groups[0]["by_class"]["warrior"]
        self.assertIsNone(warrior["score"])
        self.assertEqual(
            warrior["field_availability"]["score"],
            {
                "available": False,
                "reason": "upstream_score_not_reported",
            },
        )
        all_group = next(
            group
            for group in groups
            if group["key_card"]["card_id"] == "CARD_1"
        )
        self.assertIsNone(all_group["score"])
        self.assertIsNone(all_group["by_class"]["all"]["score"])
        expected_unavailable = {
            "available": False,
            "reason": "upstream_score_not_reported",
        }
        self.assertEqual(
            all_group["field_availability"]["score"],
            expected_unavailable,
        )
        self.assertEqual(
            all_group["by_class"]["all"]["field_availability"]["score"],
            expected_unavailable,
        )
        validate_structured_schema(payload)
        semantic_report = validate_structured(
            "hsreplay_arena_legendaries",
            payload,
        )
        self.assertTrue(semantic_report.ok, semantic_report.issues)
        contract_report = contract_quality_report(
            "hsreplay_arena_legendaries",
            payload,
        )
        self.assertTrue(contract_report["ok"], contract_report["warnings"])
        self.assertTrue(contract_report["retrieval_complete"])
        self.assertEqual(
            contract_report["critical_fields"]["score"][
                "explained_unavailable"
            ],
            1,
        )
        self.assertEqual(
            contract_report["critical_fields"]["by_class.score"][
                "explained_unavailable"
            ],
            2,
        )
        self.assertEqual(
            contract_report["critical_fields"]["by_class.score"][
                "retrieval_completeness_rate"
            ],
            1.0,
        )

    def test_strict_score_requires_availability_descriptor(self) -> None:
        data = self._full_bucket_data()
        groups = _groups_from_class_buckets(data, locale="enUS")
        payload = {
            "type": "arena_legendary_groups",
            "completeness_schema_version": 1,
            "population_completeness": "unverifiable",
            "upstream_freshness": {
                "status": "unknown",
                "reason": "transport_evidence_unavailable",
                "observed_at": "2026-08-14T02:20:00+00:00",
                "age_seconds": None,
                "evidence": [],
                "response_headers": {},
            },
            "row_retrieval": _package_row_retrieval(data, groups),
            "groups": groups,
        }

        top_score_availability = groups[0]["field_availability"].pop("score")
        with self.assertRaisesRegex(
            StructuredSchemaError,
            r"groups\[0\]\.field_availability\.score",
        ):
            validate_structured_schema(payload)
        groups[0]["field_availability"]["score"] = top_score_availability
        groups[0]["by_class"]["warrior"]["field_availability"].pop("score")
        with self.assertRaisesRegex(
            StructuredSchemaError,
            r"by_class\.warrior\.field_availability\.score",
        ):
            validate_structured_schema(payload)
        warrior = groups[0]["by_class"]["warrior"]
        warrior["score"] = None
        warrior["field_availability"]["score"] = {
            "available": False,
            "reason": "provider_reported_missing",
        }
        validate_structured_schema(payload)
        semantic_report = validate_structured(
            "hsreplay_arena_legendaries",
            payload,
        )
        self.assertIn(
            "arena_legendary_groups.unexplained_score",
            {issue.code for issue in semantic_report.issues},
        )
        contract_report = contract_quality_report(
            "hsreplay_arena_legendaries",
            payload,
        )
        self.assertFalse(contract_report["ok"])
        self.assertFalse(contract_report["retrieval_complete"])
        self.assertEqual(
            contract_report["critical_fields"]["by_class.score"][
                "availability_conflicts"
            ],
            1,
        )

    def test_schema_rejects_invalid_present_per_class_score(self) -> None:
        for invalid in (False, float("nan"), float("inf"), "unknown"):
            with self.subTest(score=invalid):
                data = self._full_bucket_data()
                groups = _groups_from_class_buckets(data, locale="enUS")
                groups[0]["by_class"]["warrior"]["score"] = invalid
                payload = {
                    "type": "arena_legendary_groups",
                    "completeness_schema_version": 1,
                    "population_completeness": "unverifiable",
                    "upstream_freshness": {
                        "status": "unknown",
                        "reason": "transport_evidence_unavailable",
                        "observed_at": "2026-08-14T02:20:00+00:00",
                        "age_seconds": None,
                        "evidence": [],
                        "response_headers": {},
                    },
                    "row_retrieval": _package_row_retrieval(data, groups),
                    "groups": groups,
                }

                with self.assertRaisesRegex(
                    StructuredSchemaError,
                    r"by_class\.warrior\.score",
                ):
                    validate_structured_schema(payload)

    def test_schema_and_semantic_gates_validate_top_and_per_class_metrics(self) -> None:
        data = self._full_bucket_data()
        groups = _groups_from_class_buckets(data, locale="enUS")
        payload = {
            "type": "arena_legendary_groups",
            "completeness_schema_version": 1,
            "row_retrieval": _package_row_retrieval(data, groups),
            "groups": groups,
        }

        groups[0]["pick_rate"] = "nan%"
        groups[1]["by_class"]["mage"]["score"] = float("inf")

        with self.assertRaisesRegex(StructuredSchemaError, "pick_rate"):
            validate_structured_schema(payload)
        report = validate_structured("hsreplay_arena_legendaries", payload)
        self.assertIn(
            "arena_legendary_groups.invalid_metrics",
            {issue.code for issue in report.issues},
        )

    def test_schema_and_semantic_gates_require_normalized_package_cards(self) -> None:
        data = self._full_bucket_data()
        groups = _groups_from_class_buckets(data, locale="enUS")
        payload = {
            "type": "arena_legendary_groups",
            "completeness_schema_version": 1,
            "row_retrieval": _package_row_retrieval(data, groups),
            "groups": groups,
        }
        groups[0]["cards"] = [{"card_id": "", "count": False}]

        with self.assertRaisesRegex(StructuredSchemaError, r"cards\[0\].card_id"):
            validate_structured_schema(payload)
        report = validate_structured("hsreplay_arena_legendaries", payload)
        self.assertIn(
            "arena_legendary_groups.invalid_package_cards",
            {issue.code for issue in report.issues},
        )

    def test_firecrawl_zero_pick_keeps_availability_without_enrichment(self) -> None:
        group = _normalize_firecrawl_group(
            {
                "cards": [{"name": "Zero Sample Legendary", "card_id": "CARD_ZERO"}],
                "winrate": None,
                "pick_rate": 0,
                "offer_rate": 0,
                "score": 0,
            },
            locale="enUS",
        )

        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual(group["pick_rate"], "0.0%")
        self.assertEqual(group["offer_rate"], "0.0%")
        self.assertEqual(group["score"], 0.0)
        expected = {
            "available": False,
            "reason": "upstream_unavailable_at_zero_pick_rate",
        }
        self.assertEqual(group["field_availability"]["winrate"], expected)
        self.assertEqual(
            group["by_class"]["all"]["field_availability"]["winrate"],
            expected,
        )

        enrich_legendary_groups([group], {})
        self.assertEqual(group["field_availability"]["winrate"], expected)

    def test_enrichment_never_uses_cached_arena_dataset_after_live_failure(self) -> None:
        cached = {
            "data": {
                "structured": {
                    "cards": [
                        {
                            "card_id": "STABLE_CARD",
                            "pick_rate": 2.5,
                        }
                    ]
                }
            }
        }
        with (
            patch(
                "app.hsreplay_arena_api.fetch_arena_card_tiers",
                new=AsyncMock(side_effect=RuntimeError("offline")),
            ),
            patch(
                "app.hsreplay_legendaries_api.load_resolved_public_dataset",
                return_value=cached,
                create=True,
            ) as resolved_loader,
        ):
            stats, backend = asyncio.run(
                _load_arena_card_stats_index(
                    "hsreplay_arena_legendaries",
                    expected_meta_period_id=16,
                )
            )

        resolved_loader.assert_not_called()
        self.assertEqual(backend, "none")
        self.assertEqual(stats, {})

    def test_live_enrichment_requires_fresh_matching_meta_period_and_quality(self) -> None:
        payload = {
            "type": "arena_card_tiers",
            "completeness_schema_version": 1,
            "population_completeness": "unverifiable",
            "upstream_freshness": {
                "status": "fresh",
                "meta_period_id": 16,
            },
            "cards": [{"card_id": "STABLE_CARD", "pick_rate": 2.5}],
        }
        with (
            patch(
                "app.hsreplay_arena_api.fetch_arena_card_tiers",
                new=AsyncMock(return_value=payload),
            ),
            patch(
                "app.hsreplay_legendaries_api.validate_structured_schema",
                return_value={"ok": True},
            ) as schema_gate,
            patch(
                "app.hsreplay_legendaries_api.validate_candidate_for_publish",
                return_value=SimpleNamespace(ok=True),
            ) as publish_gate,
        ):
            stats, backend = asyncio.run(
                _load_arena_card_stats_index(
                    "hsreplay_arena_legendaries",
                    expected_meta_period_id=16,
                )
            )
            mismatched, mismatch_backend = asyncio.run(
                _load_arena_card_stats_index(
                    "hsreplay_arena_legendaries",
                    expected_meta_period_id=17,
                )
            )

        self.assertEqual(backend, "verified_live_hsreplay_arena_api")
        self.assertEqual(stats["STABLE_CARD"]["pick_rate"], 2.5)
        self.assertEqual((mismatched, mismatch_backend), ({}, "none"))
        schema_gate.assert_called_once_with(payload)
        publish_gate.assert_called_once()

    def test_groups_keep_per_class_metrics(self) -> None:
        groups = _groups_from_class_buckets(
            {
                "ALL": [
                    {
                        "package_key_card_id": "JAIL_851",
                        "package_card_ids": ["REV_022", "TRL_520", "TSC_069"],
                        "win_rate": 45.07,
                        "pick_rate": 3.83,
                        "offer_rate": 5.21,
                        "score": 3.4,
                    }
                ],
                "DEATHKNIGHT": [
                    {
                        "package_key_card_id": "JAIL_851",
                        "package_card_ids": ["REV_022", "TRL_520", "TSC_069"],
                        "win_rate": 66.67,
                        "pick_rate": 0.68,
                        "offer_rate": 4.3,
                        "score": 1.2,
                    }
                ],
            },
            locale="enUS",
        )
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["winrate"], "45.07%")
        self.assertEqual(group["pick_rate"], "3.83%")
        self.assertEqual(group["score"], 3.4)
        self.assertEqual(group["by_class"]["all"]["winrate"], "45.07%")
        self.assertEqual(group["by_class"]["death-knight"]["winrate"], "66.67%")
        self.assertEqual(group["by_class"]["death-knight"]["pick_rate"], "0.68%")
        self.assertEqual(group["by_class"]["death-knight"]["score"], 1.2)

    def test_normalize_package_keeps_winrate(self) -> None:
        group = normalize_legendary_package(
            {
                "package_key_card_id": "TOY_813",
                "package_card_ids": ["TOY_813", "TOY_813t"],
                "win_rate": 62.9,
                "pick_rate": 83.3,
                "offer_rate": 3.9,
                "score": 53,
            },
            locale="enUS",
        )
        assert group is not None
        self.assertEqual(group["key_card"]["card_id"], "TOY_813")
        self.assertEqual(group["winrate"], "62.9%")
        self.assertEqual(group["pick_rate"], "83.3%")
        self.assertEqual(group["score"], 53.0)

    def test_enrich_fills_only_missing_all_metrics(self) -> None:
        groups = [
            {
                "key_card": {"card_id": "TOY_813", "name": "Toy Captain Tarim"},
                "winrate": "62.9%",
                "pick_rate": None,
                "offer_rate": None,
                "score": None,
                "by_class": {"all": {"winrate": "62.9%"}},
            }
        ]
        filled = enrich_legendary_groups(
            groups,
            {
                "TOY_813": {
                    "pick_rate": 83.3,
                    "offer_rate": 3.9,
                    "score": 53,
                }
            },
        )
        self.assertEqual(filled["joined"], 1)
        self.assertEqual(groups[0]["pick_rate"], "83.3%")
        self.assertEqual(groups[0]["by_class"]["all"]["score"], 53.0)
        self.assertEqual(
            groups[0]["field_availability"]["score"],
            {"available": True, "reason": None},
        )
        self.assertEqual(
            groups[0]["by_class"]["all"]["field_availability"]["score"],
            {"available": True, "reason": None},
        )

    def test_validator_requires_arenasmith_metrics(self) -> None:
        groups = [
            {
                "key_card": {"card_id": f"CARD_{idx}"},
                "winrate": "50%",
                "pick_rate": None,
                "offer_rate": None,
                "score": None,
            }
            for idx in range(10)
        ]
        report = validate_structured(
            "hsreplay_arena_legendaries",
            {"type": "arena_legendary_groups", "groups": groups},
        )
        self.assertFalse(report.ok)
        for row in groups:
            row["pick_rate"] = "10%"
            row["offer_rate"] = "2%"
            row["score"] = 40
        self.assertTrue(
            validate_structured(
                "hsreplay_arena_legendaries",
                {"type": "arena_legendary_groups", "groups": groups},
            ).ok
        )


class LegendaryGroupsFetchFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_failure_uses_firecrawl_fallback(self) -> None:
        from app import hsreplay_legendaries_api as mod

        fallback_payload = {
            "type": "arena_legendary_groups",
            "groups": [{"key_card": {"card_id": "TOY_813"}}],
            "source": {"backend": "firecrawl+hsreplay_api"},
        }
        with (
            patch.object(mod, "fetch_hsreplay_json", AsyncMock(side_effect=RuntimeError("cf"))),
            patch.object(
                mod,
                "fetch_legendary_groups_via_firecrawl",
                AsyncMock(return_value=fallback_payload),
            ) as firecrawl,
        ):
            result = await mod.fetch_legendary_groups(source_id="hsreplay_arena_legendaries")
        firecrawl.assert_awaited_once()
        self.assertEqual(result["source"]["backend"], "firecrawl+hsreplay_api")


if __name__ == "__main__":
    unittest.main()
