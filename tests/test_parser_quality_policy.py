from __future__ import annotations

from unittest.mock import patch

import pytest
from web_scraper import validate_response
from web_scraper.fetchers import RawResponse

from app.parsesunix_contracts import page_response_contract
from app.post_patch_policy import (
    capture_publication_policy,
    early_policy_changed_since_capture,
    stable_validation_mode,
)
from app.scrapers.quality import looks_like_real_page
from app.sources import SOURCE_BY_ID


def _policy(mode: str, revision: int = 1) -> dict:
    return {
        "effectiveMode": mode,
        "token": f"{mode}-{revision}",
        "revision": revision,
        "capturedAt": "2026-09-04T12:00:00Z",
        "window": None,
    }


def _accepted(body: str) -> tuple[bool, bool]:
    source = SOURCE_BY_ID["hsguru_matchups_legend"]
    response = RawResponse(
        requested_url=source.fetch_url,
        final_url=source.fetch_url,
        status=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=body.encode(),
    )
    return (
        looks_like_real_page(body, source),
        validate_response(response, page_response_contract(source)).transport_validated,
    )


@pytest.mark.parametrize("size", [1999, 2000, 2001, 5075, 24999, 25000, 25001])
@pytest.mark.parametrize("mode,minimum", [("early", 2000), ("stable", 25000)])
def test_page_size_uses_one_captured_policy(size: int, mode: str, minimum: int) -> None:
    body = "<html><body><table>matchup</table></body></html>".ljust(size)
    with (
        patch(
            "app.parser_control.publication_policy_context", return_value=_policy(mode)
        ),
        capture_publication_policy("hsguru_matchups_legend"),
    ):
        assert _accepted(body) == (size >= minimum, size >= minimum)


@pytest.mark.parametrize("size", [5075, 25001])
def test_early_size_does_not_accept_challenge(size: int) -> None:
    # A real interstitial, not a benign cf-chl string in an otherwise valid page.
    body = (
        "<html><head><title>Just a moment...</title></head>"
        "<body><form id='challenge-form'>Checking your browser</form>"
        "<script>window._cf_chl_opt = {};</script>matchup</body></html>"
    ).ljust(size)
    with (
        patch(
            "app.parser_control.publication_policy_context",
            return_value=_policy("early"),
        ),
        capture_publication_policy("hsguru_matchups_legend"),
    ):
        assert _accepted(body) == (False, False)


def test_stable_validation_overrides_captured_early_threshold() -> None:
    body = "<html><body>matchup</body></html>".ljust(5075)
    with (
        patch(
            "app.parser_control.publication_policy_context",
            return_value=_policy("early"),
        ),
        capture_publication_policy("hsguru_matchups_legend"),
    ):
        with stable_validation_mode():
            assert _accepted(body) == (False, False)
        assert _accepted(body) == (True, True)


@pytest.mark.parametrize("changed", [_policy("stable"), _policy("early", 2)])
def test_threshold_snapshot_is_stable_but_changed_policy_is_detected(
    changed: dict,
) -> None:
    source = SOURCE_BY_ID["hsguru_matchups_legend"]
    with (
        patch(
            "app.parser_control.publication_policy_context",
            return_value=_policy("early"),
        ) as current,
        capture_publication_policy(source.id),
    ):
        current.return_value = changed
        assert page_response_contract(source).min_body_bytes == 2000
        assert early_policy_changed_since_capture(source.id)[0] is True


def test_early_policy_does_not_relax_source_without_early_threshold() -> None:
    source = SOURCE_BY_ID["hsguru_streamer_decks_legend_1000"]
    with (
        patch(
            "app.parser_control.publication_policy_context",
            return_value=_policy("early"),
        ),
        capture_publication_policy(source.id),
    ):
        assert page_response_contract(source).min_body_bytes == 8000
