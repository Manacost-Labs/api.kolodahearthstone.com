from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.graphql_api.repository import PageResult, PostgresGraphQLRepository


def test_cards_cursor_uses_keyset_predicate_and_fetches_one_extra() -> None:
    async def scenario() -> None:
        repository = PostgresGraphQLRepository("postgresql://unused")
        rows = [
            {
                "collection": "constructed",
                "card_id": f"CARD_{index}",
                "name_en": "Card",
            }
            for index in range(3)
        ]
        repository._fetch_page = AsyncMock(
            return_value=PageResult(items=rows, total=10)
        )

        result = await repository.cards(
            search=None,
            collection=None,
            card_type=None,
            active=None,
            after=("constructed", "Previous", "CARD_0"),
            limit=2,
            offset=0,
        )

        call = repository._fetch_page.await_args.kwargs
        assert "after_collection" not in str(call["count_query"])
        assert "COALESCE(name_ru, name_en, '')" in str(call["rows_query"])
        assert call["params"] == {
            "limit": 3,
            "offset": 0,
            "after_collection": "constructed",
            "after_name": "Previous",
            "after_card_id": "CARD_0",
        }
        assert result.items == rows[:2]
        assert result.total == 10
        assert result.has_next_page is True

    asyncio.run(scenario())


def test_heroes_cursor_keeps_total_filter_independent_from_keyset() -> None:
    async def scenario() -> None:
        repository = PostgresGraphQLRepository("postgresql://unused")
        rows = [
            {"card_id": f"HERO_{index}", "name_en": f"Hero {index}"}
            for index in range(3)
        ]
        repository._fetch_page = AsyncMock(
            return_value=PageResult(items=rows, total=20)
        )

        result = await repository.battleground_heroes(
            search=None,
            status="ok",
            after=["Hero 0", "HERO_0"],
            limit=2,
            offset=0,
        )

        call = repository._fetch_page.await_args.kwargs
        assert "after_name" not in str(call["count_query"])
        assert "after_name" in str(call["rows_query"])
        assert call["params"]["limit"] == 3
        assert result.has_next_page is True
        assert result.next_cursor == {"values": ["Hero 1", "HERO_1"]}

    asyncio.run(scenario())


def test_records_cursor_uses_primary_key_tie_breaker() -> None:
    async def scenario() -> None:
        repository = PostgresGraphQLRepository("postgresql://unused")
        repository._collection_metadata = AsyncMock(
            return_value=(
                [
                    {"column_name": "card_id"},
                    {"column_name": "name"},
                ],
                ["card_id"],
                "BASE TABLE",
            )
        )
        repository._fetch_page = AsyncMock(
            return_value=PageResult(
                items=[
                    {"item": {"card_id": "B", "name": "Name"}, "cursor": ["Name", "B"]},
                    {"item": {"card_id": "C", "name": "Name"}, "cursor": ["Name", "C"]},
                ],
                total=10,
            )
        )

        result = await repository.records(
            collection="catalog.cards",
            fields=["card_id", "name"],
            filters=None,
            order_by="name",
            descending=False,
            after={"mode": "keyset", "sort": "Name", "keys": ["A"]},
            limit=1,
            offset=0,
        )

        call = repository._fetch_page.await_args.kwargs
        rendered = str(call["rows_query"])
        assert "after_sort" in rendered
        assert "after_key_0" in rendered
        assert call["params"]["limit"] == 2
        assert result.items == [{"card_id": "B", "name": "Name"}]
        assert result.next_cursor == {
            "mode": "keyset",
            "sort": "Name",
            "keys": ["B"],
        }

    asyncio.run(scenario())
