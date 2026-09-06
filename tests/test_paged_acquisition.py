import asyncio
import importlib.util
from unittest.mock import Mock

import httpx
import pytest

from app import paged_acquisition as acquisition

HAS_RUNNER = importlib.util.find_spec("web_scraper.pagination.runner") is not None
requires_runner = pytest.mark.skipif(
    not HAS_RUNNER,
    reason="candidate ParsesUnix runner required; see docs/ACQUISITION.md",
)


def test_missing_core_capability_fails_before_any_requests(monkeypatch):
    monkeypatch.setattr(
        acquisition, "import_module", Mock(side_effect=ModuleNotFoundError("runner"))
    )
    client = Mock()
    with pytest.raises(RuntimeError, match="ParsesUnix pagination runner"):
        asyncio.run(
            acquisition.collect_wordpress_history(
                client, format_name="Standard", category_id=3
            )
        )
    client.get.assert_not_called()


def post(i):
    return {
        "id": i,
        "date": "2026-01-01T00:00:00",
        "modified": "2026-01-01T00:00:00",
        "link": f"https://hearthstone-decks.net/deck-{i}/",
        "categories": [3],
        "title": {"rendered": f"Deck {i}"},
        "content": {"rendered": "fixture"},
    }


@requires_runner
def test_history_follows_total_pages_and_normalizes_short_last_page(monkeypatch):
    monkeypatch.setattr(
        "app.hearthstone_decks._extract_valid_deck_code_from_html",
        lambda _: "fixture-code",
    )
    calls = []

    def handler(request):
        page = int(request.url.params["page"])
        calls.append(page)
        assert request.url.params["orderby"] == "id"
        assert request.url.params["order"] == "asc"
        return httpx.Response(
            200,
            headers={"X-WP-Total": "5", "X-WP-TotalPages": "3"},
            json=[post(i) for i in range((page - 1) * 2 + 1, min(page * 2 + 1, 6))],
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await acquisition.collect_wordpress_history(
                client, format_name="Standard", category_id=3, per_page=2
            )

    result = asyncio.run(run())
    assert result.complete
    assert calls == [1, 2, 3]
    assert [row["wordpress_post_id"] for row in result.cursor.records] == [
        1,
        2,
        3,
        4,
        5,
    ]


@requires_runner
@pytest.mark.parametrize(
    "failure",
    ["missing_totals", "redirect", "wrong_category", "bad_count", "count_changed"],
)
def test_history_rejects_unproven_coverage(monkeypatch, failure):
    monkeypatch.setattr(
        "app.hearthstone_decks._extract_valid_deck_code_from_html",
        lambda _: "fixture-code",
    )
    calls = []

    def handler(request):
        page = int(request.url.params["page"])
        calls.append(page)
        if failure == "redirect":
            return httpx.Response(
                302, headers={"location": "https://untrusted.example/posts"}
            )
        headers = {"X-WP-Total": "3", "X-WP-TotalPages": "2"}
        if failure == "missing_totals":
            headers = {}
        if failure == "bad_count":
            headers["X-WP-Total"] = "secret"
        if failure == "count_changed" and page == 2:
            headers["X-WP-Total"] = "4"
        rows = [post(i) for i in range((page - 1) * 2 + 1, min(page * 2 + 1, 4))]
        if failure == "wrong_category":
            rows[0]["categories"] = [13]
        return httpx.Response(200, headers=headers, json=rows)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            return await acquisition.collect_wordpress_history(
                client, format_name="Standard", category_id=3, per_page=2
            )

    result = asyncio.run(run())
    assert not result.complete
    assert calls == ([1, 2] if failure == "count_changed" else [1])
    assert "secret" not in result.stop_reason


@requires_runner
def test_history_request_budget_resume_and_scope_isolation(monkeypatch):
    from web_scraper.pagination.runner import Limits

    monkeypatch.setattr(
        "app.hearthstone_decks._extract_valid_deck_code_from_html",
        lambda _: "fixture-code",
    )
    calls = []

    def handler(request):
        page = int(request.url.params["page"])
        calls.append(page)
        return httpx.Response(
            200, headers={"X-WP-Total": "2", "X-WP-TotalPages": "2"}, json=[post(page)]
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await acquisition.collect_wordpress_history(
                client,
                format_name="Standard",
                category_id=3,
                per_page=1,
                limits=Limits(max_requests=1),
            )
            assert not first.complete and first.cursor.next_token == "2"
            with pytest.raises(ValueError):
                await acquisition.collect_wordpress_history(
                    client,
                    format_name="Wild",
                    category_id=13,
                    per_page=1,
                    resume=first.cursor,
                )
            return await acquisition.collect_wordpress_history(
                client,
                format_name="Standard",
                category_id=3,
                per_page=1,
                resume=first.cursor,
            )

    assert asyncio.run(run()).complete
    assert calls == [1, 2]
