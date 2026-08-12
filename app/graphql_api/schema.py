from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import strawberry
from fastapi import Request
from graphql import GraphQLError
from strawberry.extensions import MaxAliasesLimiter, MaxTokensLimiter, QueryDepthLimiter
from strawberry.fastapi import BaseContext
from strawberry.scalars import JSON
from strawberry.types import Info

from .. import config
from ..api_tokens import ApiTokenError, authenticate_api_token, extract_api_token
from .repository import (
    GraphQLRepository,
    PageResult,
    RepositoryUnavailable,
    RepositoryValidationError,
)

MAX_PAGE_SIZE = 200
MAX_OFFSET = 100_000


class GraphQLContext(BaseContext):
    def __init__(self, repository: GraphQLRepository, request: Request) -> None:
        self.repository = repository
        self.request = request


@strawberry.type
class PageInfo:
    limit: int
    offset: int
    total: int
    has_next_page: bool


@strawberry.type
class ApiHealth:
    status: str
    database_connected: bool
    checked_at: datetime
    source_count: int
    unhealthy_source_count: int
    latest_sync_at: datetime | None


@strawberry.type
class Card:
    collection: str
    card_id: str
    dbf: int | None
    name_ru: str | None
    name_en: str | None
    card_type: str | None
    mana_cost: int | None
    attack: int | None
    health: int | None
    image_url: str | None
    is_active: bool | None
    updated_at: datetime | None


@strawberry.type
class CardConnection:
    items: list[Card]
    page_info: PageInfo


@strawberry.type
class BattlegroundHero:
    card_id: str
    dbf: int
    hero_id: int | None
    name_en: str
    name_ru: str | None
    health: int | None
    armor: int | None
    duos_armor: int | None
    armor_text: str | None
    race: str | None
    hero_description: str | None
    hero_image_url: str | None
    hero_full_art_url: str | None
    hero_power_dbf: int | None
    hero_power: JSON | None
    buddy_dbf: int | None
    buddy: JSON | None
    availability: JSON | None
    status: str
    fetched_at: datetime | None
    updated_at: datetime | None


@strawberry.type
class BattlegroundHeroConnection:
    items: list[BattlegroundHero]
    page_info: PageInfo


@strawberry.type
class GameStatistic:
    source_id: str
    dataset_version: str
    domain: str
    snapshot_entity_type: str
    format_name: str | None
    rank_range: str | None
    period: str | None
    mode: str | None
    rating_bracket: str | None
    patch: str | None
    snapshot_source_url: str | None
    fetched_at: datetime | None
    metadata: JSON | None
    entity_key: str
    entity_type: str
    card_id: str | None
    dbf_id: int | None
    name: str | None
    name_ru: str | None
    class_name: str | None
    tier: str | None
    games: int | None
    win_rate: float | None
    popularity: float | None
    pick_rate: float | None
    avg_placement: float | None
    score: float | None
    image_url: str | None
    source_url: str | None
    metrics: JSON | None


@strawberry.type
class GameStatisticConnection:
    items: list[GameStatistic]
    page_info: PageInfo


@strawberry.type
class Archetype:
    snapshot_id: int
    archetype_id: int
    name: str
    player_class: str | None
    game_type: str
    rank_range: str
    region: str
    total_games: int | None
    win_rate: float | None
    pct_of_class: float | None
    pct_of_total: float | None
    fetched_at: datetime | None


@strawberry.type
class ArchetypeConnection:
    items: list[Archetype]
    page_info: PageInfo


@strawberry.type
class BattlegroundMinion:
    snapshot_id: int
    dbf_id: int
    card_id: str | None
    name: str | None
    name_ru: str | None
    tavern_tier: int | None
    mmr_percentile: str
    time_range: str
    impact: float | None
    combat_winrate: float | None
    popularity: float | None
    games_with_minion: int | None
    avg_placement_with: float | None
    fetched_at: datetime | None


@strawberry.type
class BattlegroundMinionConnection:
    items: list[BattlegroundMinion]
    page_info: PageInfo


@strawberry.type
class DataSource:
    source_id: str
    display_name: str
    source_kind: str
    target_schema: str
    sync_mode: str
    is_enabled: bool
    last_synced_at: datetime | None
    last_run_started_at: datetime | None
    last_run_completed_at: datetime | None
    last_run_state: str | None
    last_rows_read: int | None
    last_rows_written: int | None
    last_error_code: str | None
    seconds_since_sync: int | None


@strawberry.type
class DataSourceConnection:
    items: list[DataSource]
    page_info: PageInfo


@strawberry.type
class Dataset:
    source_id: str
    dataset_version: str
    fetched_at: datetime | None
    state: str | None
    imported_at: datetime
    payload_type: str | None
    payload_bytes: int


@strawberry.type
class DatasetConnection:
    items: list[Dataset]
    page_info: PageInfo


@strawberry.type
class DatasetSnapshot:
    source_id: str
    dataset_version: str
    fetched_at: datetime | None
    state: str | None
    imported_at: datetime
    payload_type: str | None
    payload_bytes: int
    payload: JSON


@strawberry.type
class CollectionColumn:
    name: str
    data_type: str
    database_type: str
    nullable: bool


@strawberry.type
class DataCollection:
    schema_name: str
    name: str
    collection: str
    table_type: str
    estimated_row_count: int
    columns: list[CollectionColumn]
    primary_key: list[str]


@strawberry.type
class DataCollectionConnection:
    items: list[DataCollection]
    page_info: PageInfo


@strawberry.type
class RecordConnection:
    items: list[JSON]
    page_info: PageInfo


def _validation_error(message: str) -> GraphQLError:
    return GraphQLError(message, extensions={"code": "VALIDATION_ERROR"})


def _normalize_page(limit: int, offset: int) -> tuple[int, int]:
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise _validation_error(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if offset < 0 or offset > MAX_OFFSET:
        raise _validation_error(f"offset must be between 0 and {MAX_OFFSET}")
    return limit, offset


def _normalize_text(value: str | None, field: str, max_length: int = 120) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise _validation_error(f"{field} must be at most {max_length} characters")
    return normalized


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _page_info(result: PageResult, limit: int, offset: int) -> PageInfo:
    return PageInfo(
        limit=limit,
        offset=offset,
        total=result.total,
        has_next_page=offset + len(result.items) < result.total,
    )


def _repository(info: Info[GraphQLContext, None]) -> GraphQLRepository:
    return info.context.repository


def _require_full_access(info: Info[GraphQLContext, None]) -> None:
    try:
        supplied = extract_api_token(
            info.context.request.headers.get("Authorization"),
            info.context.request.headers.get("X-API-Key"),
        )
        authenticate_api_token(
            supplied,
            required_scope="database:read",
            legacy_key=config.api_key(),
        )
    except ApiTokenError as error:
        code = "FORBIDDEN" if error.status_code == 403 else "UNAUTHORIZED"
        raise GraphQLError(
            "Missing or invalid X-API-Key",
            extensions={"code": code},
        ) from None


async def _call_repository(operation: Any) -> Any:
    try:
        return await operation
    except RepositoryValidationError as exc:
        raise _validation_error(str(exc)) from None
    except RepositoryUnavailable:
        raise GraphQLError(
            "The central data store is temporarily unavailable",
            extensions={"code": "SERVICE_UNAVAILABLE"},
        ) from None


def _card(row: dict[str, Any]) -> Card:
    return Card(**row)


def _hero(row: dict[str, Any]) -> BattlegroundHero:
    return BattlegroundHero(
        card_id=row["card_id"],
        dbf=row["dbf"],
        hero_id=row.get("hero_id"),
        name_en=row["name_en"],
        name_ru=row.get("name_ru"),
        health=row.get("health"),
        armor=row.get("armor"),
        duos_armor=row.get("duos_armor"),
        armor_text=row.get("armor_text"),
        race=row.get("race"),
        hero_description=row.get("hero_description"),
        hero_image_url=row.get("hero_image_url"),
        hero_full_art_url=row.get("hero_full_art_url"),
        hero_power_dbf=row.get("hero_power_dbf"),
        hero_power=row.get("hero_power_json"),
        buddy_dbf=row.get("buddy_dbf"),
        buddy=row.get("buddy_json"),
        availability=row.get("availability_json"),
        status=row["status"],
        fetched_at=row.get("fetched_at"),
        updated_at=row.get("updated_at"),
    )


def _statistic(row: dict[str, Any]) -> GameStatistic:
    return GameStatistic(
        **{
            **row,
            "win_rate": _optional_float(row.get("win_rate")),
            "popularity": _optional_float(row.get("popularity")),
            "pick_rate": _optional_float(row.get("pick_rate")),
            "avg_placement": _optional_float(row.get("avg_placement")),
            "score": _optional_float(row.get("score")),
        }
    )


def _archetype(row: dict[str, Any]) -> Archetype:
    return Archetype(
        **{
            **row,
            "win_rate": _optional_float(row.get("win_rate")),
            "pct_of_class": _optional_float(row.get("pct_of_class")),
            "pct_of_total": _optional_float(row.get("pct_of_total")),
        }
    )


def _minion(row: dict[str, Any]) -> BattlegroundMinion:
    return BattlegroundMinion(
        **{
            **row,
            "impact": _optional_float(row.get("impact")),
            "combat_winrate": _optional_float(row.get("combat_winrate")),
            "popularity": _optional_float(row.get("popularity")),
            "avg_placement_with": _optional_float(row.get("avg_placement_with")),
        }
    )


@strawberry.type
class Query:
    @strawberry.field(
        description="Availability and freshness of the central data store."
    )
    async def health(self, info: Info[GraphQLContext, None]) -> ApiHealth:
        row = await _call_repository(_repository(info).health())
        checked_at = row.get("checked_at")
        if not isinstance(checked_at, datetime):
            checked_at = datetime.now().astimezone()
        unhealthy = int(row.get("unhealthy_source_count") or 0)
        return ApiHealth(
            status="ok" if unhealthy == 0 else "degraded",
            database_connected=True,
            checked_at=checked_at,
            source_count=int(row.get("source_count") or 0),
            unhealthy_source_count=unhealthy,
            latest_sync_at=row.get("latest_sync_at"),
        )

    @strawberry.field(
        description="Unified catalog of constructed and Battlegrounds cards."
    )
    async def cards(
        self,
        info: Info[GraphQLContext, None],
        search: str | None = None,
        collection: str | None = None,
        card_type: str | None = None,
        active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> CardConnection:
        limit, offset = _normalize_page(limit, offset)
        result = await _call_repository(
            _repository(info).cards(
                search=_normalize_text(search, "search"),
                collection=_normalize_text(collection, "collection", 40),
                card_type=_normalize_text(card_type, "cardType", 40),
                active=active,
                limit=limit,
                offset=offset,
            )
        )
        return CardConnection(
            items=[_card(row) for row in result.items],
            page_info=_page_info(result, limit, offset),
        )

    @strawberry.field(description="One card by stable Blizzard card ID.")
    async def card(
        self,
        info: Info[GraphQLContext, None],
        card_id: str,
        collection: str | None = None,
    ) -> Card | None:
        normalized_id = _normalize_text(card_id, "cardId", 64)
        if normalized_id is None:
            raise _validation_error("cardId is required")
        row = await _call_repository(
            _repository(info).card(
                normalized_id, _normalize_text(collection, "collection", 40)
            )
        )
        return _card(row) if row else None

    @strawberry.field(
        description="Battlegrounds heroes with hero-shaped artwork and details."
    )
    async def battleground_heroes(
        self,
        info: Info[GraphQLContext, None],
        search: str | None = None,
        status: str | None = "ok",
        limit: int = 50,
        offset: int = 0,
    ) -> BattlegroundHeroConnection:
        limit, offset = _normalize_page(limit, offset)
        result = await _call_repository(
            _repository(info).battleground_heroes(
                search=_normalize_text(search, "search"),
                status=_normalize_text(status, "status", 32),
                limit=limit,
                offset=offset,
            )
        )
        return BattlegroundHeroConnection(
            items=[_hero(row) for row in result.items],
            page_info=_page_info(result, limit, offset),
        )

    @strawberry.field(description="Normalized statistics from every integrated source.")
    async def statistics(
        self,
        info: Info[GraphQLContext, None],
        search: str | None = None,
        domain: str | None = None,
        entity_type: str | None = None,
        source_id: str | None = None,
        format_name: str | None = None,
        rank_range: str | None = None,
        mode: str | None = None,
        patch: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> GameStatisticConnection:
        limit, offset = _normalize_page(limit, offset)
        result = await _call_repository(
            _repository(info).statistics(
                search=_normalize_text(search, "search"),
                domain=_normalize_text(domain, "domain", 64),
                entity_type=_normalize_text(entity_type, "entityType", 64),
                source_id=_normalize_text(source_id, "sourceId", 120),
                format_name=_normalize_text(format_name, "formatName", 64),
                rank_range=_normalize_text(rank_range, "rankRange", 64),
                mode=_normalize_text(mode, "mode", 64),
                patch=_normalize_text(patch, "patch", 64),
                limit=limit,
                offset=offset,
            )
        )
        return GameStatisticConnection(
            items=[_statistic(row) for row in result.items],
            page_info=_page_info(result, limit, offset),
        )

    @strawberry.field(
        description="Latest archetype statistics by rank, format and region."
    )
    async def archetypes(
        self,
        info: Info[GraphQLContext, None],
        search: str | None = None,
        player_class: str | None = None,
        game_type: str | None = None,
        rank_range: str | None = None,
        region: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ArchetypeConnection:
        limit, offset = _normalize_page(limit, offset)
        result = await _call_repository(
            _repository(info).archetypes(
                search=_normalize_text(search, "search"),
                player_class=_normalize_text(player_class, "playerClass", 64),
                game_type=_normalize_text(game_type, "gameType", 64),
                rank_range=_normalize_text(rank_range, "rankRange", 64),
                region=_normalize_text(region, "region", 32),
                limit=limit,
                offset=offset,
            )
        )
        return ArchetypeConnection(
            items=[_archetype(row) for row in result.items],
            page_info=_page_info(result, limit, offset),
        )

    @strawberry.field(
        description="Battlegrounds minion performance by tier and MMR slice."
    )
    async def battleground_minions(
        self,
        info: Info[GraphQLContext, None],
        search: str | None = None,
        tavern_tier: int | None = None,
        mmr_percentile: str | None = None,
        time_range: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> BattlegroundMinionConnection:
        limit, offset = _normalize_page(limit, offset)
        if tavern_tier is not None and not 1 <= tavern_tier <= 7:
            raise _validation_error("tavernTier must be between 1 and 7")
        result = await _call_repository(
            _repository(info).battleground_minions(
                search=_normalize_text(search, "search"),
                tavern_tier=tavern_tier,
                mmr_percentile=_normalize_text(mmr_percentile, "mmrPercentile", 64),
                time_range=_normalize_text(time_range, "timeRange", 64),
                limit=limit,
                offset=offset,
            )
        )
        return BattlegroundMinionConnection(
            items=[_minion(row) for row in result.items],
            page_info=_page_info(result, limit, offset),
        )

    @strawberry.field(
        description="Integrated data sources and their last refresh state."
    )
    async def sources(
        self,
        info: Info[GraphQLContext, None],
        enabled: bool | None = None,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> DataSourceConnection:
        limit, offset = _normalize_page(limit, offset)
        result = await _call_repository(
            _repository(info).sources(
                enabled=enabled,
                state=_normalize_text(state, "state", 32),
                limit=limit,
                offset=offset,
            )
        )
        return DataSourceConnection(
            items=[DataSource(**row) for row in result.items],
            page_info=_page_info(result, limit, offset),
        )

    @strawberry.field(description="Latest imported dataset version for every source.")
    async def datasets(
        self,
        info: Info[GraphQLContext, None],
        source_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> DatasetConnection:
        limit, offset = _normalize_page(limit, offset)
        result = await _call_repository(
            _repository(info).datasets(
                source_id=_normalize_text(source_id, "sourceId", 120),
                state=_normalize_text(state, "state", 32),
                limit=limit,
                offset=offset,
            )
        )
        return DatasetConnection(
            items=[Dataset(**row) for row in result.items],
            page_info=_page_info(result, limit, offset),
        )

    @strawberry.field(
        description="One complete imported dataset for compatibility migrations."
    )
    async def dataset(
        self,
        info: Info[GraphQLContext, None],
        source_id: str,
        dataset_version: str | None = None,
    ) -> DatasetSnapshot | None:
        normalized_source = _normalize_text(source_id, "sourceId", 120)
        if normalized_source is None:
            raise _validation_error("sourceId is required")
        row = await _call_repository(
            _repository(info).dataset(
                normalized_source,
                _normalize_text(dataset_version, "datasetVersion", 128),
            )
        )
        return DatasetSnapshot(**row) if row else None

    @strawberry.field(
        description=("Every table and view in the central store. Requires X-API-Key.")
    )
    async def collections(
        self,
        info: Info[GraphQLContext, None],
        schema_name: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> DataCollectionConnection:
        _require_full_access(info)
        limit, offset = _normalize_page(limit, offset)
        result = await _call_repository(
            _repository(info).collections(
                schema_name=_normalize_text(schema_name, "schemaName", 32),
                search=_normalize_text(search, "search", 120),
                limit=limit,
                offset=offset,
            )
        )
        items = [
            DataCollection(
                schema_name=row["schema_name"],
                name=row["name"],
                collection=row["collection"],
                table_type=row["table_type"],
                estimated_row_count=int(row["estimated_row_count"]),
                columns=[CollectionColumn(**column) for column in row["columns"]],
                primary_key=list(row["primary_key"]),
            )
            for row in result.items
        ]
        return DataCollectionConnection(
            items=items,
            page_info=_page_info(result, limit, offset),
        )

    @strawberry.field(
        description=(
            "Read rows from any central collection with safe fields, filters and "
            "pagination. Requires X-API-Key."
        )
    )
    async def records(
        self,
        info: Info[GraphQLContext, None],
        collection: str,
        fields: list[str] | None = None,
        filters: JSON | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> RecordConnection:
        _require_full_access(info)
        limit, offset = _normalize_page(limit, offset)
        normalized_collection = _normalize_text(collection, "collection", 128)
        if normalized_collection is None:
            raise _validation_error("collection is required")
        normalized_fields: list[str] | None = None
        if fields is not None:
            normalized_fields = []
            for field in fields:
                normalized = _normalize_text(field, "fields", 64)
                if normalized is None:
                    raise _validation_error("fields must not contain empty values")
                normalized_fields.append(normalized)
        if filters is not None and not isinstance(filters, dict):
            raise _validation_error("filters must be a JSON object")
        result = await _call_repository(
            _repository(info).records(
                collection=normalized_collection,
                fields=normalized_fields,
                filters=filters,
                order_by=_normalize_text(order_by, "orderBy", 64),
                descending=descending,
                limit=limit,
                offset=offset,
            )
        )
        return RecordConnection(
            items=result.items,
            page_info=_page_info(result, limit, offset),
        )


schema = strawberry.Schema(
    query=Query,
    extensions=[
        lambda: QueryDepthLimiter(max_depth=8),
        lambda: MaxAliasesLimiter(max_alias_count=20),
        lambda: MaxTokensLimiter(max_token_count=1_500),
    ],
)
