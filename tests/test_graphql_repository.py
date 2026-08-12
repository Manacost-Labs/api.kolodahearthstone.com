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
