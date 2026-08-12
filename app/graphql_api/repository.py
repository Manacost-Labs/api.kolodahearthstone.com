from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


class RepositoryUnavailable(RuntimeError):
    """Raised when the central data store cannot serve a GraphQL request."""


class RepositoryValidationError(ValueError):
    """Raised when a generic database query is outside the safe contract."""


@dataclass(frozen=True, slots=True)
class PageResult:
    items: list[dict[str, Any]]
    total: int
    has_next_page: bool | None = None
    next_cursor: dict[str, Any] | None = None


ALLOWED_DATABASE_SCHEMAS = frozenset({"catalog", "analytics", "raw", "platform", "hub"})
COLLECTION_RE = re.compile(r"^(?P<schema>[a-z][a-z0-9_]*)\.(?P<table>[a-z][a-z0-9_]*)$")


class GraphQLRepository(Protocol):
    async def health(self) -> dict[str, Any]: ...

    async def cards(self, **filters: Any) -> PageResult: ...

    async def card(
        self, card_id: str, collection: str | None
    ) -> dict[str, Any] | None: ...

    async def battleground_heroes(self, **filters: Any) -> PageResult: ...

    async def statistics(self, **filters: Any) -> PageResult: ...

    async def archetypes(self, **filters: Any) -> PageResult: ...

    async def battleground_minions(self, **filters: Any) -> PageResult: ...

    async def sources(self, **filters: Any) -> PageResult: ...

    async def datasets(self, **filters: Any) -> PageResult: ...

    async def dataset(
        self, source_id: str, dataset_version: str | None
    ) -> dict[str, Any] | None: ...

    async def collections(self, **filters: Any) -> PageResult: ...

    async def records(self, **filters: Any) -> PageResult: ...

    async def close(self) -> None: ...


class PostgresGraphQLRepository:
    """Small, read-only query layer over the stable ``hub`` views."""

    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = database_url
        self._pool: AsyncConnectionPool[Any] | None = None
        self._pool_lock = asyncio.Lock()

    def _conninfo(self) -> str:
        value = self._database_url or os.environ.get("HS_GRAPHQL_DATABASE_URL", "")
        value = value.strip()
        if not value:
            raise RepositoryUnavailable("Central PostgreSQL is not configured")
        return value

    async def _get_pool(self) -> AsyncConnectionPool[Any]:
        async with self._pool_lock:
            if self._pool is None:
                self._pool = AsyncConnectionPool(
                    conninfo=self._conninfo(),
                    min_size=0,
                    max_size=8,
                    open=False,
                    kwargs={
                        "autocommit": True,
                        "row_factory": dict_row,
                        "options": "-c default_transaction_read_only=on -c statement_timeout=8000",
                    },
                )
                try:
                    await self._pool.open(wait=True, timeout=10)
                except Exception as exc:
                    await self._pool.close()
                    self._pool = None
                    raise RepositoryUnavailable(
                        "Central PostgreSQL is unavailable"
                    ) from exc
        return self._pool

    async def _fetch_one(
        self, query: Any, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        try:
            pool = await self._get_pool()
            async with (
                pool.connection() as connection,
                connection.cursor() as cursor,
            ):
                await cursor.execute(query, params or {})
                return await cursor.fetchone()
        except RepositoryUnavailable:
            raise
        except Exception as exc:
            raise RepositoryUnavailable("Central PostgreSQL query failed") from exc

    async def _fetch_page(
        self,
        *,
        count_query: Any,
        rows_query: Any,
        params: dict[str, Any],
    ) -> PageResult:
        try:
            pool = await self._get_pool()
            async with (
                pool.connection() as connection,
                connection.cursor() as cursor,
            ):
                await cursor.execute(count_query, params)
                count_row = await cursor.fetchone()
                await cursor.execute(rows_query, params)
                rows = await cursor.fetchall()
            return PageResult(
                items=list(rows), total=int(count_row["total"] if count_row else 0)
            )
        except RepositoryUnavailable:
            raise
        except Exception as exc:
            raise RepositoryUnavailable("Central PostgreSQL query failed") from exc

    @staticmethod
    def _keyset_result(
        result: PageResult,
        *,
        limit: int,
        cursor: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> PageResult:
        items = result.items[:limit]
        has_next_page = len(result.items) > limit
        return PageResult(
            items=items,
            total=result.total,
            has_next_page=has_next_page,
            next_cursor=cursor(items[-1]) if has_next_page and items else None,
        )

    @staticmethod
    def _page_sql(
        base: str,
        where: list[str],
        order_by: str,
        *,
        count_where: list[str] | None = None,
    ) -> tuple[str, str]:
        predicate = " AND ".join(where) if where else "TRUE"
        count_predicate = " AND ".join(count_where) if count_where else "TRUE"
        if count_where is None:
            count_predicate = predicate
        count_query = (
            f"SELECT count(*)::bigint AS total FROM {base} WHERE {count_predicate}"
        )
        rows_query = (
            f"SELECT * FROM {base} WHERE {predicate} ORDER BY {order_by} "
            "LIMIT %(limit)s OFFSET %(offset)s"
        )
        return count_query, rows_query

    async def health(self) -> dict[str, Any]:
        row = await self._fetch_one(
            """
            WITH source_ids AS (
                SELECT source_id FROM hub.integration_status
                UNION
                SELECT source_id FROM raw.datasets
            )
            SELECT
                now() AS checked_at,
                (SELECT count(*)::bigint FROM source_ids) AS source_count,
                (SELECT count(*)::bigint
                   FROM hub.integration_status
                  WHERE is_enabled
                    AND COALESCE(last_run_state, '') NOT IN ('success', 'ok')
                ) AS unhealthy_source_count,
                GREATEST(
                    (SELECT max(last_synced_at) FROM hub.integration_status),
                    (SELECT max(COALESCE(fetched_at, imported_at)) FROM raw.datasets)
                ) AS latest_sync_at
            """
        )
        return row or {}

    async def cards(
        self,
        *,
        search: str | None,
        collection: str | None,
        card_type: str | None,
        active: bool | None,
        after: tuple[str, str, str] | None,
        limit: int,
        offset: int,
    ) -> PageResult:
        where: list[str] = []
        params: dict[str, Any] = {
            "limit": limit + 1,
            "offset": 0 if after else offset,
        }
        if search:
            where.append(
                "(card_id ILIKE %(search)s OR name_ru ILIKE %(search)s OR name_en ILIKE %(search)s)"
            )
            params["search"] = f"%{search}%"
        if collection:
            where.append("collection = %(collection)s")
            params["collection"] = collection
        if card_type:
            where.append("card_type = %(card_type)s")
            params["card_type"] = card_type
        if active is not None:
            where.append("is_active = %(active)s")
            params["active"] = active
        predicate = " AND ".join(where) if where else "TRUE"
        count_query = (
            f"SELECT count(*)::bigint AS total FROM hub.card_catalog WHERE {predicate}"
        )
        rows_where = list(where)
        if after:
            rows_where.append(
                "(collection, COALESCE(name_ru, name_en, ''), card_id) "
                "> (%(after_collection)s, %(after_name)s, %(after_card_id)s)"
            )
            params.update(
                {
                    "after_collection": after[0],
                    "after_name": after[1],
                    "after_card_id": after[2],
                }
            )
        rows_predicate = " AND ".join(rows_where) if rows_where else "TRUE"
        rows_query = (
            "SELECT * FROM hub.card_catalog "
            f"WHERE {rows_predicate} "
            "ORDER BY collection, COALESCE(name_ru, name_en, ''), card_id "
            "LIMIT %(limit)s OFFSET %(offset)s"
        )
        result = await self._fetch_page(
            count_query=count_query, rows_query=rows_query, params=params
        )
        return self._keyset_result(
            result,
            limit=limit,
            cursor=lambda row: {
                "values": [
                    str(row["collection"]),
                    str(row.get("name_ru") or row.get("name_en") or ""),
                    str(row["card_id"]),
                ]
            },
        )

    async def card(self, card_id: str, collection: str | None) -> dict[str, Any] | None:
        params: dict[str, Any] = {"card_id": card_id}
        collection_filter = ""
        if collection:
            collection_filter = " AND collection = %(collection)s"
            params["collection"] = collection
        return await self._fetch_one(
            "SELECT * FROM hub.card_catalog WHERE card_id = %(card_id)s"
            + collection_filter
            + " ORDER BY collection LIMIT 1",
            params,
        )

    async def battleground_heroes(
        self,
        *,
        search: str | None,
        status: str | None,
        after: list[Any] | None,
        limit: int,
        offset: int,
    ) -> PageResult:
        where: list[str] = []
        params: dict[str, Any] = {
            "limit": limit + 1,
            "offset": 0 if after else offset,
        }
        if search:
            where.append(
                "(card_id ILIKE %(search)s OR name_ru ILIKE %(search)s OR name_en ILIKE %(search)s)"
            )
            params["search"] = f"%{search}%"
        if status:
            where.append("status = %(status)s")
            params["status"] = status
        base = """(
            SELECT card_id, dbf, hero_id, name_en, name_ru, health, armor, duos_armor,
                   armor_text, race, hero_description, hero_image_url, hero_full_art_url,
                   hero_power_dbf, hero_power_json, buddy_dbf, buddy_json,
                   availability_json, status, fetched_at, updated_at
            FROM catalog.battlegrounds_heroes
        ) AS bg_heroes"""
        count_where = list(where)
        if after:
            if len(after) != 2 or not all(isinstance(value, str) for value in after):
                raise RepositoryValidationError("after is not a valid heroes cursor")
            where.append(
                "(COALESCE(name_ru, name_en, ''), card_id) "
                "> (%(after_name)s, %(after_card_id)s)"
            )
            params.update({"after_name": after[0], "after_card_id": after[1]})
        count_query, rows_query = self._page_sql(
            base,
            where,
            "COALESCE(name_ru, name_en, ''), card_id",
            count_where=count_where,
        )
        result = await self._fetch_page(
            count_query=count_query, rows_query=rows_query, params=params
        )
        return self._keyset_result(
            result,
            limit=limit,
            cursor=lambda row: {
                "values": [
                    str(row.get("name_ru") or row.get("name_en") or ""),
                    str(row["card_id"]),
                ]
            },
        )

    async def statistics(
        self,
        *,
        search: str | None,
        domain: str | None,
        entity_type: str | None,
        source_id: str | None,
        format_name: str | None,
        rank_range: str | None,
        mode: str | None,
        patch: str | None,
        after: list[Any] | None,
        limit: int,
        offset: int,
    ) -> PageResult:
        where: list[str] = []
        params: dict[str, Any] = {
            "limit": limit + 1,
            "offset": 0 if after else offset,
        }
        for column, value in (
            ("domain", domain),
            ("entity_type", entity_type),
            ("source_id", source_id),
            ("format_name", format_name),
            ("rank_range", rank_range),
            ("mode", mode),
            ("patch", patch),
        ):
            if value:
                where.append(f"{column} = %({column})s")
                params[column] = value
        if search:
            where.append(
                "(entity_key ILIKE %(search)s OR card_id ILIKE %(search)s "
                "OR name ILIKE %(search)s OR name_ru ILIKE %(search)s)"
            )
            params["search"] = f"%{search}%"
        count_where = list(where)
        order_columns = (
            "source_id",
            "dataset_version",
            "domain",
            "snapshot_entity_type",
            "format_name",
            "rank_range",
            "period",
            "mode",
            "rating_bracket",
            "entity_key",
        )
        if after:
            if len(after) != 12 or not isinstance(after[0], bool):
                raise RepositoryValidationError(
                    "after is not a valid statistics cursor"
                )
            where.append(
                "(fetched_at IS NULL, COALESCE(-extract(epoch FROM fetched_at), 0), "
                + ", ".join(order_columns)
                + ") > (%(after_null)s, %(after_epoch)s, "
                + ", ".join(f"%(after_{column})s" for column in order_columns)
                + ")"
            )
            params.update({"after_null": after[0], "after_epoch": after[1]})
            params.update(
                {
                    f"after_{column}": after[index + 2]
                    for index, column in enumerate(order_columns)
                }
            )
        order_by = (
            "fetched_at IS NULL, COALESCE(-extract(epoch FROM fetched_at), 0), "
            + ", ".join(order_columns)
        )
        count_query, rows_query = self._page_sql(
            "hub.game_stat_latest",
            where,
            order_by,
            count_where=count_where,
        )
        result = await self._fetch_page(
            count_query=count_query, rows_query=rows_query, params=params
        )

        def statistic_cursor(row: dict[str, Any]) -> dict[str, Any]:
            fetched_at = row.get("fetched_at")
            return {
                "values": [
                    fetched_at is None,
                    -fetched_at.timestamp() if fetched_at is not None else 0,
                    *(row.get(column) or "" for column in order_columns),
                ]
            }

        return self._keyset_result(result, limit=limit, cursor=statistic_cursor)

    async def archetypes(
        self,
        *,
        search: str | None,
        player_class: str | None,
        game_type: str | None,
        rank_range: str | None,
        region: str | None,
        after: list[Any] | None,
        limit: int,
        offset: int,
    ) -> PageResult:
        where: list[str] = []
        params: dict[str, Any] = {
            "limit": limit + 1,
            "offset": 0 if after else offset,
        }
        if search:
            where.append("name ILIKE %(search)s")
            params["search"] = f"%{search}%"
        for column, value in (
            ("player_class", player_class),
            ("game_type", game_type),
            ("rank_range", rank_range),
            ("region", region),
        ):
            if value:
                where.append(f"{column} = %({column})s")
                params[column] = value
        count_where = list(where)
        if after:
            if len(after) != 6 or not isinstance(after[0], bool):
                raise RepositoryValidationError(
                    "after is not a valid archetypes cursor"
                )
            where.append(
                "(total_games IS NULL, COALESCE(-total_games, 0), archetype_id, "
                "game_type, rank_range, region) > "
                "(%(after_null)s, %(after_games)s, %(after_id)s, "
                "%(after_game_type)s, %(after_rank)s, %(after_region)s)"
            )
            params.update(
                {
                    "after_null": after[0],
                    "after_games": after[1],
                    "after_id": after[2],
                    "after_game_type": after[3],
                    "after_rank": after[4],
                    "after_region": after[5],
                }
            )
        count_query, rows_query = self._page_sql(
            "hub.archetype_latest",
            where,
            "total_games IS NULL, COALESCE(-total_games, 0), archetype_id, "
            "game_type, rank_range, region",
            count_where=count_where,
        )
        result = await self._fetch_page(
            count_query=count_query, rows_query=rows_query, params=params
        )
        return self._keyset_result(
            result,
            limit=limit,
            cursor=lambda row: {
                "values": [
                    row.get("total_games") is None,
                    -int(row["total_games"])
                    if row.get("total_games") is not None
                    else 0,
                    int(row["archetype_id"]),
                    str(row["game_type"]),
                    str(row["rank_range"]),
                    str(row["region"]),
                ]
            },
        )

    async def battleground_minions(
        self,
        *,
        search: str | None,
        tavern_tier: int | None,
        mmr_percentile: str | None,
        time_range: str | None,
        after: list[Any] | None,
        limit: int,
        offset: int,
    ) -> PageResult:
        where: list[str] = []
        params: dict[str, Any] = {
            "limit": limit + 1,
            "offset": 0 if after else offset,
        }
        if search:
            where.append(
                "(card_id ILIKE %(search)s OR name ILIKE %(search)s OR name_ru ILIKE %(search)s)"
            )
            params["search"] = f"%{search}%"
        if tavern_tier is not None:
            where.append("tavern_tier = %(tavern_tier)s")
            params["tavern_tier"] = tavern_tier
        for column, value in (
            ("mmr_percentile", mmr_percentile),
            ("time_range", time_range),
        ):
            if value:
                where.append(f"{column} = %({column})s")
                params[column] = value
        count_where = list(where)
        if after:
            if len(after) != 7 or not isinstance(after[0], bool):
                raise RepositoryValidationError("after is not a valid minions cursor")
            where.append(
                "(tavern_tier IS NULL, COALESCE(tavern_tier, 0), impact IS NULL, "
                "COALESCE(-impact, 0), dbf_id, mmr_percentile, time_range) > "
                "(%(after_tier_null)s, %(after_tier)s, %(after_impact_null)s, "
                "%(after_impact)s, %(after_dbf)s, %(after_mmr)s, %(after_time)s)"
            )
            params.update(
                {
                    "after_tier_null": after[0],
                    "after_tier": after[1],
                    "after_impact_null": after[2],
                    "after_impact": after[3],
                    "after_dbf": after[4],
                    "after_mmr": after[5],
                    "after_time": after[6],
                }
            )
        count_query, rows_query = self._page_sql(
            "hub.bg_minion_latest",
            where,
            "tavern_tier IS NULL, COALESCE(tavern_tier, 0), impact IS NULL, "
            "COALESCE(-impact, 0), dbf_id, mmr_percentile, time_range",
            count_where=count_where,
        )
        result = await self._fetch_page(
            count_query=count_query, rows_query=rows_query, params=params
        )
        return self._keyset_result(
            result,
            limit=limit,
            cursor=lambda row: {
                "values": [
                    row.get("tavern_tier") is None,
                    int(row.get("tavern_tier") or 0),
                    row.get("impact") is None,
                    -float(row["impact"]) if row.get("impact") is not None else 0,
                    int(row["dbf_id"]),
                    str(row["mmr_percentile"]),
                    str(row["time_range"]),
                ]
            },
        )

    async def sources(
        self,
        *,
        enabled: bool | None,
        state: str | None,
        after: list[Any] | None,
        limit: int,
        offset: int,
    ) -> PageResult:
        where: list[str] = []
        params: dict[str, Any] = {
            "limit": limit + 1,
            "offset": 0 if after else offset,
        }
        if enabled is not None:
            where.append("is_enabled = %(enabled)s")
            params["enabled"] = enabled
        if state:
            where.append("last_run_state = %(state)s")
            params["state"] = state
        base = """(
            WITH latest_datasets AS (
                SELECT DISTINCT ON (source_id)
                       source_id, fetched_at, imported_at, state
                FROM raw.datasets
                ORDER BY source_id, fetched_at DESC NULLS LAST, imported_at DESC
            )
            SELECT
                COALESCE(integration.source_id, dataset.source_id) AS source_id,
                COALESCE(integration.display_name, dataset.source_id) AS display_name,
                COALESCE(integration.source_kind, 'dataset') AS source_kind,
                COALESCE(integration.target_schema, 'raw') AS target_schema,
                COALESCE(integration.sync_mode, 'import') AS sync_mode,
                COALESCE(integration.is_enabled, TRUE) AS is_enabled,
                COALESCE(
                    integration.last_synced_at,
                    dataset.fetched_at,
                    dataset.imported_at
                ) AS last_synced_at,
                integration.last_run_started_at,
                integration.last_run_completed_at,
                COALESCE(integration.last_run_state, dataset.state) AS last_run_state,
                integration.last_rows_read,
                integration.last_rows_written,
                integration.last_error_code,
                floor(extract(epoch FROM now() - COALESCE(
                    integration.last_synced_at,
                    dataset.fetched_at,
                    dataset.imported_at
                )))::bigint AS seconds_since_sync
            FROM hub.integration_status AS integration
            FULL OUTER JOIN latest_datasets AS dataset USING (source_id)
        ) AS all_sources"""
        count_where = list(where)
        if after:
            if len(after) != 2 or not all(isinstance(value, str) for value in after):
                raise RepositoryValidationError("after is not a valid sources cursor")
            where.append(
                "(display_name, source_id) > (%(after_name)s, %(after_source)s)"
            )
            params.update({"after_name": after[0], "after_source": after[1]})
        count_query, rows_query = self._page_sql(
            base,
            where,
            "display_name, source_id",
            count_where=count_where,
        )
        result = await self._fetch_page(
            count_query=count_query, rows_query=rows_query, params=params
        )
        return self._keyset_result(
            result,
            limit=limit,
            cursor=lambda row: {
                "values": [str(row["display_name"]), str(row["source_id"])]
            },
        )

    async def datasets(
        self,
        *,
        source_id: str | None,
        state: str | None,
        after: list[Any] | None,
        limit: int,
        offset: int,
    ) -> PageResult:
        where: list[str] = []
        params: dict[str, Any] = {
            "limit": limit + 1,
            "offset": 0 if after else offset,
        }
        if source_id:
            where.append("source_id = %(source_id)s")
            params["source_id"] = source_id
        if state:
            where.append("state = %(state)s")
            params["state"] = state
        base = """(
            SELECT DISTINCT ON (source_id)
                   source_id, dataset_version, fetched_at, state, imported_at,
                   jsonb_typeof(payload) AS payload_type,
                   pg_column_size(payload)::bigint AS payload_bytes
            FROM raw.datasets
            ORDER BY source_id, fetched_at DESC NULLS LAST, imported_at DESC
        ) AS latest_datasets"""
        count_where = list(where)
        if after:
            if len(after) != 3 or not isinstance(after[0], bool):
                raise RepositoryValidationError("after is not a valid datasets cursor")
            where.append(
                "(fetched_at IS NULL, COALESCE(-extract(epoch FROM fetched_at), 0), "
                "source_id) > (%(after_null)s, %(after_epoch)s, %(after_source)s)"
            )
            params.update(
                {
                    "after_null": after[0],
                    "after_epoch": after[1],
                    "after_source": after[2],
                }
            )
        count_query, rows_query = self._page_sql(
            base,
            where,
            "fetched_at IS NULL, COALESCE(-extract(epoch FROM fetched_at), 0), source_id",
            count_where=count_where,
        )
        result = await self._fetch_page(
            count_query=count_query, rows_query=rows_query, params=params
        )

        def dataset_cursor(row: dict[str, Any]) -> dict[str, Any]:
            fetched_at = row.get("fetched_at")
            return {
                "values": [
                    fetched_at is None,
                    -fetched_at.timestamp() if fetched_at is not None else 0,
                    str(row["source_id"]),
                ]
            }

        return self._keyset_result(result, limit=limit, cursor=dataset_cursor)

    async def dataset(
        self, source_id: str, dataset_version: str | None
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {"source_id": source_id}
        version_filter = ""
        if dataset_version:
            version_filter = " AND dataset_version = %(dataset_version)s"
            params["dataset_version"] = dataset_version
        return await self._fetch_one(
            """
            SELECT source_id, dataset_version, fetched_at, state, imported_at,
                   jsonb_typeof(payload) AS payload_type,
                   pg_column_size(payload)::bigint AS payload_bytes,
                   payload
              FROM raw.datasets
             WHERE source_id = %(source_id)s
            """
            + version_filter
            + " ORDER BY fetched_at DESC NULLS LAST, imported_at DESC LIMIT 1",
            params,
        )

    @staticmethod
    def _collection_parts(collection: str) -> tuple[str, str]:
        match = COLLECTION_RE.fullmatch(collection)
        if not match or match.group("schema") not in ALLOWED_DATABASE_SCHEMAS:
            allowed = ", ".join(sorted(ALLOWED_DATABASE_SCHEMAS))
            raise RepositoryValidationError(
                f"collection must be schema.table in one of: {allowed}"
            )
        return match.group("schema"), match.group("table")

    async def _collection_metadata(
        self, collection: str
    ) -> tuple[list[dict[str, Any]], list[str], str]:
        schema_name, table_name = self._collection_parts(collection)
        try:
            pool = await self._get_pool()
            async with (
                pool.connection() as connection,
                connection.cursor() as cursor,
            ):
                await cursor.execute(
                    """
                    SELECT column_name, data_type, udt_name, is_nullable = 'YES' AS nullable,
                           ordinal_position
                      FROM information_schema.columns
                     WHERE table_schema = %(schema)s AND table_name = %(table)s
                     ORDER BY ordinal_position
                    """,
                    {"schema": schema_name, "table": table_name},
                )
                columns = list(await cursor.fetchall())
                await cursor.execute(
                    """
                    SELECT COALESCE(array_agg(attribute.attname ORDER BY key_position), '{}') AS keys
                      FROM pg_catalog.pg_constraint AS constraint_record
                      JOIN pg_catalog.pg_class AS relation
                        ON relation.oid = constraint_record.conrelid
                      JOIN pg_catalog.pg_namespace AS namespace
                        ON namespace.oid = relation.relnamespace
                      JOIN unnest(constraint_record.conkey) WITH ORDINALITY
                        AS key_column(attribute_number, key_position) ON TRUE
                      JOIN pg_catalog.pg_attribute AS attribute
                        ON attribute.attrelid = relation.oid
                       AND attribute.attnum = key_column.attribute_number
                     WHERE constraint_record.contype = 'p'
                       AND namespace.nspname = %(schema)s
                       AND relation.relname = %(table)s
                    """,
                    {"schema": schema_name, "table": table_name},
                )
                primary_key_row = await cursor.fetchone()
                await cursor.execute(
                    """
                    SELECT table_type
                      FROM information_schema.tables
                     WHERE table_schema = %(schema)s AND table_name = %(table)s
                    """,
                    {"schema": schema_name, "table": table_name},
                )
                table_row = await cursor.fetchone()
        except RepositoryUnavailable:
            raise
        except Exception as exc:
            raise RepositoryUnavailable("Central PostgreSQL query failed") from exc
        if not columns or not table_row:
            raise RepositoryValidationError("collection does not exist")
        return (
            columns,
            list(primary_key_row["keys"] if primary_key_row else []),
            str(table_row["table_type"]),
        )

    async def collections(
        self,
        *,
        schema_name: str | None,
        search: str | None,
        after: list[Any] | None,
        limit: int,
        offset: int,
    ) -> PageResult:
        where = ["table_schema = ANY(%(schemas)s)"]
        params: dict[str, Any] = {
            "schemas": sorted(ALLOWED_DATABASE_SCHEMAS),
            "limit": limit + 1,
            "offset": 0 if after else offset,
        }
        if schema_name:
            if schema_name not in ALLOWED_DATABASE_SCHEMAS:
                raise RepositoryValidationError("schema is not available")
            where.append("table_schema = %(schema)s")
            params["schema"] = schema_name
        if search:
            where.append(
                "(table_name ILIKE %(search)s OR table_schema ILIKE %(search)s)"
            )
            params["search"] = f"%{search}%"
        count_predicate = " AND ".join(where)
        if after:
            if len(after) != 2 or not all(isinstance(value, str) for value in after):
                raise RepositoryValidationError(
                    "after is not a valid collections cursor"
                )
            where.append(
                "(table_schema, table_name) > (%(after_schema)s, %(after_table)s)"
            )
            params.update({"after_schema": after[0], "after_table": after[1]})
        predicate = " AND ".join(where)
        count_query = (
            "SELECT count(*)::bigint AS total FROM information_schema.tables WHERE "
            + count_predicate
        )
        rows_query = (
            """
            SELECT tables.table_schema AS schema_name,
                   tables.table_name AS name,
                   tables.table_schema || '.' || tables.table_name AS collection,
                   tables.table_type,
                   GREATEST(COALESCE(classes.reltuples, 0), 0)::bigint
                       AS estimated_row_count,
                   COALESCE(columns.columns, '[]'::jsonb) AS columns,
                   COALESCE(primary_keys.keys, '[]'::jsonb) AS primary_key
              FROM information_schema.tables AS tables
              LEFT JOIN pg_catalog.pg_namespace AS namespaces
                ON namespaces.nspname = tables.table_schema
              LEFT JOIN pg_catalog.pg_class AS classes
                ON classes.relnamespace = namespaces.oid
               AND classes.relname = tables.table_name
              LEFT JOIN LATERAL (
                    SELECT jsonb_agg(jsonb_build_object(
                               'name', column_name,
                               'data_type', data_type,
                               'database_type', udt_name,
                               'nullable', is_nullable = 'YES'
                           ) ORDER BY ordinal_position) AS columns
                      FROM information_schema.columns
                     WHERE table_schema = tables.table_schema
                       AND table_name = tables.table_name
              ) AS columns ON TRUE
              LEFT JOIN LATERAL (
                    SELECT jsonb_agg(attribute.attname ORDER BY key_position) AS keys
                      FROM pg_catalog.pg_constraint AS constraint_record
                      JOIN unnest(constraint_record.conkey) WITH ORDINALITY
                        AS key_column(attribute_number, key_position) ON TRUE
                      JOIN pg_catalog.pg_attribute AS attribute
                        ON attribute.attrelid = constraint_record.conrelid
                       AND attribute.attnum = key_column.attribute_number
                     WHERE constraint_record.conrelid = classes.oid
                       AND constraint_record.contype = 'p'
              ) AS primary_keys ON TRUE
             WHERE """
            + predicate
            + " ORDER BY tables.table_schema, tables.table_name LIMIT %(limit)s OFFSET %(offset)s"
        )
        result = await self._fetch_page(
            count_query=count_query, rows_query=rows_query, params=params
        )
        return self._keyset_result(
            result,
            limit=limit,
            cursor=lambda row: {"values": [str(row["schema_name"]), str(row["name"])]},
        )

    async def records(
        self,
        *,
        collection: str,
        fields: list[str] | None,
        filters: dict[str, Any] | None,
        order_by: str | None,
        descending: bool,
        after: dict[str, Any] | None,
        limit: int,
        offset: int,
    ) -> PageResult:
        schema_name, table_name = self._collection_parts(collection)
        columns, primary_key, _table_type = await self._collection_metadata(collection)
        available_columns = [str(column["column_name"]) for column in columns]
        available_set = set(available_columns)

        selected_fields = fields or available_columns
        if not selected_fields or len(selected_fields) > 100:
            raise RepositoryValidationError(
                "fields must contain between 1 and 100 columns"
            )
        if len(selected_fields) != len(set(selected_fields)):
            raise RepositoryValidationError("fields must not contain duplicates")
        invalid_fields = sorted(set(selected_fields) - available_set)
        if invalid_fields:
            raise RepositoryValidationError(
                "unknown fields: " + ", ".join(invalid_fields[:5])
            )

        filters = filters or {}
        if len(filters) > 20:
            raise RepositoryValidationError("filters may contain at most 20 fields")
        invalid_filters = sorted(set(filters) - available_set)
        if invalid_filters:
            raise RepositoryValidationError(
                "unknown filter fields: " + ", ".join(invalid_filters[:5])
            )

        sort_field = order_by or (
            primary_key[0] if primary_key else available_columns[0]
        )
        if sort_field not in available_set:
            raise RepositoryValidationError("orderBy is not a collection field")

        table_identifier = sql.Identifier(schema_name, table_name)
        predicate = sql.SQL("TRUE")
        params: dict[str, Any] = {"limit": limit + 1, "offset": offset}
        if filters:
            predicate = sql.SQL("to_jsonb(source_row) @> %(filters)s::jsonb")
            params["filters"] = json.dumps(filters, ensure_ascii=False)
        selected_identifiers = sql.SQL(", ").join(
            sql.Identifier(field) for field in selected_fields
        )
        direction = sql.SQL("DESC") if descending else sql.SQL("ASC")
        cursor_mode = str(after.get("mode")) if after else None
        if cursor_mode not in {None, "keyset", "offset"}:
            raise RepositoryValidationError("after is not a valid records cursor")
        if cursor_mode == "offset":
            cursor_offset = after.get("offset") if after else None
            if not isinstance(cursor_offset, int) or cursor_offset < 0:
                raise RepositoryValidationError("after is not a valid records cursor")
            params["offset"] = cursor_offset
            params["limit"] = limit + 1
        elif cursor_mode == "keyset":
            after_sort = after.get("sort") if after else None
            after_keys = after.get("keys") if after else None
            if not primary_key or not isinstance(after_keys, list):
                raise RepositoryValidationError("after is not a valid records cursor")
            if len(after_keys) != len(primary_key):
                raise RepositoryValidationError("after is not a valid records cursor")
            params["after_sort"] = after_sort
            params["after_sort_null"] = after_sort is None
            for index, value in enumerate(after_keys):
                params[f"after_key_{index}"] = value
            comparison = sql.SQL("<") if descending else sql.SQL(">")
            key_columns = sql.SQL(", ").join(
                sql.Identifier(column) for column in primary_key
            )
            key_parameters = sql.SQL(", ").join(
                sql.Placeholder(f"after_key_{index}")
                for index in range(len(primary_key))
            )
            keyset_predicate = sql.SQL(
                "((NOT %(after_sort_null)s AND ({} {} %(after_sort)s OR {} IS NULL)) "
                "OR ({} IS NOT DISTINCT FROM %(after_sort)s AND ({}) > ({})))"
            ).format(
                sql.Identifier(sort_field),
                comparison,
                sql.Identifier(sort_field),
                sql.Identifier(sort_field),
                key_columns,
                key_parameters,
            )
            predicate = sql.SQL("({}) AND ({})").format(predicate, keyset_predicate)
            params["offset"] = 0
            params["limit"] = limit + 1
        count_query = sql.SQL(
            "SELECT count(*)::bigint AS total FROM {} AS source_row WHERE {}"
        ).format(
            table_identifier,
            (
                sql.SQL("to_jsonb(source_row) @> %(filters)s::jsonb")
                if filters
                else sql.SQL("TRUE")
            ),
        )
        cursor_fields = [sort_field, *primary_key]
        cursor_identifiers = sql.SQL(", ").join(
            sql.Identifier(field) for field in cursor_fields
        )
        tie_breakers = sql.SQL("")
        if primary_key:
            tie_breakers = sql.SQL(", ") + sql.SQL(", ").join(
                sql.Identifier(column) for column in primary_key
            )
        rows_query = sql.SQL(
            "SELECT to_jsonb(result_row) - '__cursor' AS item, "
            "result_row.__cursor AS cursor FROM ("
            "SELECT {}, jsonb_build_array({}) AS __cursor "
            "FROM {} AS source_row WHERE {} "
            "ORDER BY {} {} NULLS LAST{} LIMIT %(limit)s OFFSET %(offset)s"
            ") AS result_row"
        ).format(
            selected_identifiers,
            cursor_identifiers,
            table_identifier,
            predicate,
            sql.Identifier(sort_field),
            direction,
            tie_breakers,
        )
        result = await self._fetch_page(
            count_query=count_query, rows_query=rows_query, params=params
        )
        rows = result.items[:limit]
        items = [dict(row["item"]) for row in rows]
        has_next_page = len(result.items) > limit
        next_cursor: dict[str, Any] | None = None
        if has_next_page and rows:
            cursor_values = list(rows[-1]["cursor"])
            if primary_key:
                next_cursor = {
                    "mode": "keyset",
                    "sort": cursor_values[0],
                    "keys": cursor_values[1:],
                }
            else:
                next_cursor = {
                    "mode": "offset",
                    "offset": int(params["offset"]) + limit,
                }
        return PageResult(
            items=items,
            total=result.total,
            has_next_page=has_next_page,
            next_cursor=next_cursor,
        )

    async def close(self) -> None:
        async with self._pool_lock:
            if self._pool is not None:
                await self._pool.close()
                self._pool = None


_repository: GraphQLRepository = PostgresGraphQLRepository()


def get_graphql_repository() -> GraphQLRepository:
    return _repository


async def close_graphql_repository() -> None:
    await _repository.close()
