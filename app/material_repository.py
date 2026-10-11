"""Read only public search entities and their card descriptions."""

from __future__ import annotations

import html
import re
from typing import Any

from .graphql_api.repository import RepositoryUnavailable, get_graphql_repository

KINDS = frozenset({"card", "hero", "minion", "archetype", "source"})
PUBLIC_DOCUMENTS = """
    SELECT searched.*,
           concat_ws(' ', searched.name, searched.name_ru, searched.subtitle,
               constructed.name_en, constructed.text_ru, constructed.text_en,
               to_jsonb(constructed)->>'class_slug',
               to_jsonb(constructed)->>'minion_type',
               to_jsonb(bg)->>'text_ru', to_jsonb(bg)->>'text_en', bg.notes,
               to_jsonb(bg)->>'creature_type', hero.hero_description,
               searched.metadata::text) AS content
    FROM hub.unified_search AS searched
    LEFT JOIN catalog.constructed_cards AS constructed
      ON searched.kind = 'card'
     AND searched.entity_id = 'constructed:' || constructed.card_id
    LEFT JOIN catalog.battlegrounds_cards AS bg
      ON (searched.kind = 'card'
          AND searched.entity_id = 'battlegrounds:' || bg.card_id)
      OR (searched.kind = 'minion'
          AND searched.metadata->>'cardId' = bg.card_id)
    LEFT JOIN catalog.battlegrounds_heroes AS hero
      ON searched.kind = 'hero' AND searched.entity_id = hero.card_id
"""


class MaterialRepository:
    async def documents(
        self, *, after: str = "", limit: int = 500, keys: list[str] | None = None
    ) -> list[dict[str, Any]]:
        conditions = "(searched.kind || ':' || searched.entity_id) > %(after)s"
        params: dict[str, Any] = {"after": after, "limit": limit}
        if keys is not None:
            conditions = "(searched.kind || ':' || searched.entity_id) = ANY(%(keys)s)"
            params["keys"] = keys
        try:
            repository = get_graphql_repository()
            pool = await repository._get_pool()
            async with pool.connection() as connection, connection.cursor() as cursor:
                await cursor.execute(
                    PUBLIC_DOCUMENTS
                    + f" WHERE {conditions} ORDER BY searched.kind || ':' || searched.entity_id LIMIT %(limit)s",
                    params,
                )
                rows = await cursor.fetchall()
        except RepositoryUnavailable:
            raise
        except Exception as exc:
            raise RepositoryUnavailable("Material catalog unavailable") from exc
        for row in rows:
            row["content"] = html.unescape(
                re.sub(r"<[^>]+>", " ", row["content"] or "")
            )[:4000]
        return rows

    async def lexical(
        self, query: str, kinds: list[str] | None, limit: int
    ) -> list[dict[str, Any]]:
        result = await get_graphql_repository().search(
            query=query, kinds=kinds, limit=limit, offset=0, after=None
        )
        return result.items


material_repository = MaterialRepository()
