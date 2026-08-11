from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from app.fetcher import (
    _dataset_from_structured,
    _preserve_cached_ok_status,
    _source_uses_residential_proxy,
    fetch_source,
)
from app.firecrawl_backend import FirecrawlScrape
from app.heartharena import (
    fetch_heartharena_tierlist,
    parse_heartharena_tierlist,
)
from app.metastats import CLASSES, fetch_metastats_decks, fetch_metastats_matchups
from app.proxy_errors import ProxyPaymentRequiredError
from app.scrapers.rotator import (
    reset_backend_circuits,
    residential_proxy_circuit_error,
)
from app.sources import Source

HEARTHARENA_SOURCE = Source(
    id="heartharena_tierlist",
    url="https://www.heartharena.com/ru/tierlist",
    site="heartharena",
    category="arena",
)
METASTATS_SOURCE = Source(
    id="metastats_decks",
    url="https://metastats.net/hearthstone/class/decks/DeathKnight/",
    site="metastats",
    category="ranked",
)
METASTATS_MATCHUPS_SOURCE = Source(
    id="metastats_matchups",
    url="https://metastats.net/hearthstone/archetype/matchup/",
    site="metastats",
    category="matchups",
)
HSREPLAY_SOURCE = Source(
    id="hsreplay_cards_gold",
    url="https://hsreplay.net/cards/",
    site="hsreplay",
    category="ranked",
)


def _heartharena_html() -> str:
    sections: list[str] = []
    for class_index, class_id in enumerate(
        ("druid", "hunter", "mage", "paladin", "priest")
    ):
        cards = "".join(
            (
                '<li><dl class="card">'
                f'<dt data-card-image="/TEST_{class_index}_{card_index}.png">'
                f"Card {class_index}-{card_index}</dt>"
                f'<dd class="score">{100 - card_index}</dd>'
                "</dl></li>"
            )
            for card_index in range(60)
        )
        sections.append(
            f'<section class="tierlist" id="{class_id}">'
            '<li class="rarity commons"><li class="tier A">'
            f"<header>A</header><ol class=\"cards\">{cards}</ol>"
            "</li></li></section>"
        )
    return "<html><body>" + "".join(sections) + "</body></html>"


def _metastats_class_html(class_name: str, *, decks: int = 4) -> str:
    rows = "".join(
        (
            '<div class="decklist">'
            f"<h4>{class_name} Archetype {index} #{index}</h4>"
            "<span>#Games: 1,000</span>"
            "<span>#Win Rate: 52.5%</span>"
            "</div>"
        )
        for index in range(decks)
    )
    return f'<html><div class="tab-pane" id="{class_name}">{rows}</div></html>'


def _metastats_matchups_html() -> str:
    headers = "".join(f"<th>Opponent {index}</th>" for index in range(6))
    rows: list[str] = []
    for row_index in range(10):
        archetype = f"Archetype {row_index}"
        cells = "".join(
            (
                "<td><div "
                f'title="Games: 100&#10;{archetype}: 55%&#10;'
                f'Opponent {opponent}: 45%">55%</div></td>'
            )
            for opponent in range(6)
        )
        rows.append(
            f'<tr><td class="playerarch">{archetype}</td>{cells}</tr>'
        )
    return (
        "<html><table><thead><tr>"
        + headers
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></html>"
    )


class ProviderSourceFallbackTest(unittest.TestCase):
    def test_heartharena_proxy_402_switches_to_scrape_do_candidate(self) -> None:
        html = _heartharena_html()

        async def cloud(source: Source, **options):
            self.assertEqual(source.id, HEARTHARENA_SOURCE.id)
            self.assertNotIn("headers", options)
            self.assertTrue(options["brightdata_accept_html"](html))
            return FirecrawlScrape(
                html=html,
                markdown="",
                screenshot=None,
                metadata={"backend": "scrape_do", "scrapeDoCreditsUsed": 5},
                status_code=200,
                final_url=source.url,
            )

        with (
            patch(
                "app.heartharena._fetch_residential_html",
                new=AsyncMock(
                    side_effect=ProxyPaymentRequiredError(
                        "Residential proxy CONNECT rejected",
                        status_code=402,
                    )
                ),
            ) as residential,
            patch(
                "app.heartharena.scrape_source_with_options",
                side_effect=cloud,
            ) as provider,
            patch("app.heartharena.card_from_id", return_value={}),
        ):
            async def recover():
                reset_backend_circuits()
                candidate = await fetch_heartharena_tierlist(HEARTHARENA_SOURCE)
                return candidate, residential_proxy_circuit_error()

            structured, circuit_error = asyncio.run(recover())

        self.assertEqual(structured["total_cards"], 300)
        self.assertEqual(structured["_fetch_backend"], "scrape_do")
        self.assertIsNotNone(circuit_error)
        assert circuit_error is not None
        self.assertEqual(circuit_error.status_code, 402)
        residential.assert_awaited_once()
        provider.assert_awaited_once()

        parsed = _dataset_from_structured(
            HEARTHARENA_SOURCE,
            structured,
            backend="heartharena_api",
        )
        self.assertEqual(parsed["_backend"], "heartharena_api")
        self.assertEqual(parsed["_transport_backend"], "scrape_do")
        self.assertNotIn("_fetch_backend", parsed["structured"])
        self.assertNotIn("_fetch_backend", parsed["hsreplay_extracted"])
        with patch.dict(
            "os.environ",
            {"HS_FETCH_PROXY_URL": "http://user:pass@geo.iproyal.com:1234"},
        ):
            self.assertFalse(
                _source_uses_residential_proxy(
                    HEARTHARENA_SOURCE,
                    parsed["_transport_backend"],
                )
            )

    def test_invalid_cloud_candidate_fails_and_common_gate_preserves_lkg(self) -> None:
        valid_html = _heartharena_html()
        invalid = FirecrawlScrape(
            html="<html><body>challenge</body></html>",
            markdown="",
            screenshot=None,
            metadata={"backend": "scrape_do"},
            status_code=200,
            final_url=HEARTHARENA_SOURCE.url,
        )

        with (
            patch(
                "app.heartharena._fetch_residential_html",
                new=AsyncMock(
                    side_effect=ProxyPaymentRequiredError(
                        "Residential proxy CONNECT rejected",
                        status_code=402,
                    )
                ),
            ),
            patch(
                "app.heartharena.scrape_source_with_options",
                new=AsyncMock(return_value=invalid),
            ),
            patch("app.heartharena.card_from_id", return_value={}),
            self.assertRaises(RuntimeError) as raised,
        ):
            asyncio.run(fetch_heartharena_tierlist(HEARTHARENA_SOURCE))

        structured = parse_heartharena_tierlist(valid_html)
        cached = {
            "fetched_at": "2026-08-10T00:00:00+00:00",
            "backend": "heartharena_api",
            "transport_backend": "scrape_do",
            "content_length": len(valid_html),
            "data": {
                "title": "HearthArena card tier-list",
                "structured": structured,
                "hsreplay_extracted": structured,
            },
        }
        failed = {
            "state": "fetch_error",
            "fetched_at": "2026-08-11T00:00:00+00:00",
            "detail": type(raised.exception).__name__,
        }
        with (
            patch(
                "app.parser_control.load_resolved_public_dataset",
                return_value=cached,
            ),
            patch("app.fetcher.save_status"),
            patch("app.fetcher.log_action"),
            patch.dict(
                "os.environ",
                {"HS_FETCH_PROXY_URL": "http://user:pass@geo.iproyal.com:1234"},
            ),
        ):
            status = _preserve_cached_ok_status(HEARTHARENA_SOURCE, failed)

        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status["state"], "ok")
        self.assertEqual(status["backend"], "heartharena_api")
        self.assertEqual(status["transport_backend"], "scrape_do")
        self.assertFalse(status["used_residential_proxy"])
        self.assertTrue(status["serving_cached_dataset"])
        self.assertEqual(status["last_refresh_state"], "fetch_error")

    def test_cloud_transport_passes_real_publish_gate_without_leaking(self) -> None:
        structured = {
            "type": "metastats_decks",
            "decks": [
                {
                    "archetype_name": f"Deck {index}",
                    "win_rate": "52%",
                    "games": 100,
                }
                for index in range(44)
            ],
            "classes_parsed": list(CLASSES),
            "total_decks": 44,
            "_fetch_backend": "scrape_do",
        }
        parsed = _dataset_from_structured(
            METASTATS_SOURCE,
            structured,
            backend="metastats_api",
        )
        self.assertEqual(parsed["_backend"], "metastats_api")
        self.assertEqual(parsed["_transport_backend"], "scrape_do")

        with (
            TemporaryDirectory() as temp_dir,
            patch("app.storage.data_dir", return_value=Path(temp_dir)),
            patch.dict(
                "os.environ",
                {"HS_FETCH_PROXY_URL": "http://user:pass@geo.iproyal.com:1234"},
            ),
            patch(
                "app.fetcher._fetch_hsreplay_api_source",
                new=AsyncMock(return_value=parsed),
            ),
            patch(
                "app.fetcher.quality_metrics",
                return_value={"quality_score": 1.0, "rows_total": 44},
            ),
            patch(
                "app.fetcher._review_candidate_with_ai",
                new=AsyncMock(return_value=(None, False, None)),
            ),
            patch(
                "app.fetcher._save_dataset_with_checks",
                return_value=(False, None, {}),
            ) as save_dataset,
            patch("app.fetcher.firecrawl_primary_source_ids", return_value=set()),
            patch("app.fetcher.firecrawl_fallback_source_ids", return_value=set()),
            patch("app.fetcher.log_action") as log_action,
        ):
            status = asyncio.run(fetch_source(None, METASTATS_SOURCE))

        self.assertEqual(status["state"], "ok")
        self.assertEqual(status["backend"], "metastats_api")
        self.assertEqual(status["transport_backend"], "scrape_do")
        self.assertFalse(status["used_residential_proxy"])
        self.assertNotIn("_fetch_backend", parsed["structured"])
        self.assertNotIn("_transport_backend", parsed)
        saved_dataset = save_dataset.call_args.args[1]
        self.assertEqual(saved_dataset["backend"], "metastats_api")
        self.assertEqual(saved_dataset["transport_backend"], "scrape_do")
        self.assertFalse(saved_dataset["used_residential_proxy"])
        self.assertNotIn("_transport_backend", saved_dataset["data"])
        telemetry_extras = [
            logged.kwargs.get("extra")
            for logged in log_action.call_args_list
            if logged.args
            and logged.args[0] in {"api.validate.ok", "api.route.ok"}
        ]
        self.assertEqual(len(telemetry_extras), 2)
        self.assertTrue(
            all(
                isinstance(extra, dict)
                and extra.get("transport_backend") == "scrape_do"
                for extra in telemetry_extras
            )
        )

    def test_transport_backend_is_allowlisted_and_mixed_value_is_canonical(self) -> None:
        structured = {
            "type": "metastats_decks",
            "decks": [],
            "_fetch_backend": "mixed[scrape_do,residential_httpx,scrape_do]",
        }
        parsed = _dataset_from_structured(
            METASTATS_SOURCE,
            structured,
            backend="metastats_api",
        )

        self.assertEqual(
            parsed["_transport_backend"],
            "mixed[residential_httpx,scrape_do]",
        )

        structured["_fetch_backend"] = "mixed[residential_httpx,unknown]"
        rejected = _dataset_from_structured(
            METASTATS_SOURCE,
            structured,
            backend="metastats_api",
        )
        self.assertNotIn("_transport_backend", rejected)
        self.assertNotIn("_fetch_backend", rejected["structured"])

    def test_hsreplay_scrape_do_transport_is_not_counted_as_residential(self) -> None:
        with patch(
            "app.hsreplay_client.consume_hsreplay_json_transport_backend",
            return_value="scrape_do",
        ):
            parsed = _dataset_from_structured(
                HSREPLAY_SOURCE,
                {
                    "type": "card_stats",
                    "cards": [{"id": "TEST_001", "deck_winrate": 52.0}],
                },
                backend="hsreplay_cards_api",
            )

        self.assertEqual(parsed["_backend"], "hsreplay_cards_api")
        self.assertEqual(parsed["_transport_backend"], "scrape_do")
        with patch.dict(
            "os.environ",
            {"HS_FETCH_PROXY_URL": "http://user:pass@geo.iproyal.com:1234"},
        ):
            self.assertFalse(
                _source_uses_residential_proxy(
                    HSREPLAY_SOURCE,
                    parsed["_transport_backend"],
                )
            )

    def test_proxyless_transport_labels_do_not_change_legacy_backend_accounting(self) -> None:
        with patch.dict(
            "os.environ",
            {"HS_FETCH_PROXY_URL": "http://user:pass@geo.iproyal.com:1234"},
        ):
            self.assertTrue(_source_uses_residential_proxy(HSREPLAY_SOURCE, "direct"))
            self.assertTrue(
                _source_uses_residential_proxy(HSREPLAY_SOURCE, "curl_cffi")
            )
            self.assertFalse(
                _source_uses_residential_proxy(
                    HSREPLAY_SOURCE,
                    "proxyless_direct",
                )
            )
            self.assertFalse(
                _source_uses_residential_proxy(
                    HSREPLAY_SOURCE,
                    "proxyless_curl_cffi",
                )
            )

    def test_metastats_fetches_all_classes_with_concurrency_at_most_two(self) -> None:
        active = 0
        maximum_active = 0
        seen: list[str] = []

        async def residential(_source_id: str, url: str) -> str:
            nonlocal active, maximum_active
            class_name = url.rstrip("/").rsplit("/", 1)[-1]
            active += 1
            maximum_active = max(maximum_active, active)
            seen.append(class_name)
            try:
                await asyncio.sleep(0.001)
                return _metastats_class_html(class_name)
            finally:
                active -= 1

        with (
            patch(
                "app.metastats._fetch_residential_html",
                side_effect=residential,
            ),
            patch(
                "app.metastats._fetch_cloud_html",
                new=AsyncMock(),
            ) as cloud,
        ):
            structured = asyncio.run(fetch_metastats_decks(METASTATS_SOURCE))

        self.assertLessEqual(maximum_active, 2)
        self.assertEqual(set(seen), set(CLASSES))
        self.assertEqual(structured["classes_parsed"], CLASSES)
        self.assertEqual(structured["total_decks"], len(CLASSES) * 4)
        self.assertEqual(structured["_fetch_backend"], "residential_httpx")
        parsed = _dataset_from_structured(
            METASTATS_SOURCE,
            structured,
            backend="metastats_api",
        )
        self.assertEqual(parsed["_backend"], "metastats_api")
        self.assertEqual(parsed["_transport_backend"], "residential_httpx")
        self.assertNotIn("_fetch_backend", parsed["structured"])
        with patch.dict(
            "os.environ",
            {"HS_FETCH_PROXY_URL": "http://user:pass@geo.iproyal.com:1234"},
        ):
            self.assertTrue(
                _source_uses_residential_proxy(
                    METASTATS_SOURCE,
                    parsed["_transport_backend"],
                )
            )
        cloud.assert_not_awaited()

    def test_metastats_matchups_proxy_402_uses_validated_cloud_candidate(self) -> None:
        html = _metastats_matchups_html()

        async def cloud(source: Source, **options):
            self.assertEqual(source.id, METASTATS_MATCHUPS_SOURCE.id)
            self.assertNotIn("headers", options)
            self.assertTrue(options["brightdata_accept_html"](html))
            return FirecrawlScrape(
                html=html,
                markdown="",
                screenshot=None,
                metadata={"backend": "scrape_do", "scrapeDoCreditsUsed": 5},
                status_code=200,
                final_url=source.url,
            )

        with (
            patch(
                "app.metastats._fetch_residential_html",
                new=AsyncMock(
                    side_effect=ProxyPaymentRequiredError(
                        "Residential proxy CONNECT rejected",
                        status_code=402,
                    )
                ),
            ) as residential,
            patch(
                "app.metastats.scrape_source_with_options",
                side_effect=cloud,
            ) as provider,
        ):
            async def recover():
                reset_backend_circuits()
                candidate = await fetch_metastats_matchups(
                    METASTATS_MATCHUPS_SOURCE
                )
                return candidate, residential_proxy_circuit_error()

            structured, circuit_error = asyncio.run(recover())

        self.assertEqual(len(structured["matchups"]), 60)
        self.assertEqual(structured["_fetch_backend"], "scrape_do")
        self.assertIsNotNone(circuit_error)
        assert circuit_error is not None
        self.assertEqual(circuit_error.status_code, 402)
        residential.assert_awaited_once()
        provider.assert_awaited_once()

    def test_metastats_aggregates_mixed_class_backend_provenance(self) -> None:
        async def class_result(
            _source: Source,
            class_name: str,
            **_options,
        ) -> tuple[str, list[dict[str, object]], str]:
            backend = "residential_httpx" if class_name == CLASSES[0] else "scrape_do"
            decks: list[dict[str, object]] = [
                {
                    "archetype_name": f"{class_name} {index}",
                    "win_rate": "52%",
                    "games": 100,
                }
                for index in range(4)
            ]
            return class_name, decks, backend

        with patch(
            "app.metastats._fetch_metastats_class",
            side_effect=class_result,
        ):
            structured = asyncio.run(fetch_metastats_decks(METASTATS_SOURCE))

        self.assertEqual(
            structured["_fetch_backend"],
            "mixed[residential_httpx,scrape_do]",
        )

    def test_metastats_proxy_circuit_uses_cloud_without_partial_output(self) -> None:
        residential_calls = 0
        cloud_classes: list[str] = []

        async def residential(_source_id: str, _url: str) -> str:
            nonlocal residential_calls
            residential_calls += 1
            raise ProxyPaymentRequiredError(
                "Residential proxy CONNECT rejected",
                status_code=402,
            )

        async def cloud(
            source: Source,
            *,
            accept_html,
        ) -> tuple[str, str]:
            class_name = source.url.rstrip("/").rsplit("/", 1)[-1]
            cloud_classes.append(class_name)
            if class_name == "Warrior":
                return "<html>invalid</html>", "scrape_do"
            candidate = _metastats_class_html(class_name)
            self.assertTrue(accept_html(candidate))
            return candidate, "scrape_do"

        with (
            patch(
                "app.metastats._fetch_residential_html",
                side_effect=residential,
            ),
            patch("app.metastats._fetch_cloud_html", side_effect=cloud),
            patch(
                "app.scrapers.rotator.record_residential_proxy_failure"
            ) as record_failure,
            self.assertRaisesRegex(RuntimeError, "coverage incomplete"),
        ):
            asyncio.run(fetch_metastats_decks(METASTATS_SOURCE))

        self.assertLessEqual(residential_calls, 2)
        self.assertEqual(record_failure.call_count, residential_calls)
        self.assertEqual(set(cloud_classes), set(CLASSES))


if __name__ == "__main__":
    unittest.main()
