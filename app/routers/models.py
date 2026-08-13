from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiMeta(BaseModel):
    source_id: str
    fetched_at: str | None = None
    stale: bool
    serving_cached_dataset: bool | None = None
    cached_after_failure: bool | None = None
    fresh_candidate_published: bool | None = None
    count: int = Field(ge=0)
    limit: int | None = Field(default=None, ge=1)
    offset: int | None = Field(default=None, ge=0)


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: ApiMeta


class FlexibleRow(BaseModel):
    model_config = ConfigDict(extra="allow")


class DeckRow(FlexibleRow):
    id: int | None = None
    source_id: str | None = None
    title: str | None = None
    archetype: str | None = None
    class_name: str | None = Field(default=None, alias="class")
    format: str | None = None
    deck_code: str | None = None
    win_rate: float | None = None
    updated_at: str | None = None


class ArchetypeRow(FlexibleRow):
    archetype_id: int
    name: str
    player_class: str | None = None
    win_rate: float | None = None
    total_games: int | None = None
    fetched_at: str | None = None


class BgHeroRow(FlexibleRow):
    hero: str
    dbfId: int | None = None
    pick_rate: str | float | None = None
    avg_placement: str | float | None = None
    tier: str | None = None


class BgMinionRow(FlexibleRow):
    dbf_id: int
    name: str
    card_id: str | None = None
    tavern_tier: int | None = None
    popularity: float | None = None
    combat_winrate: float | None = None
    fetched_at: str | None = None


class ArenaClassRow(FlexibleRow):
    class_name: str = Field(alias="class")
    win_rate: float | None = None
    pick_rate: float | None = None
    pct_7_plus: float | None = None
    num_drafts: int | None = None


def freshest_timestamp(rows: list[dict[str, Any]], *fields: str) -> str | None:
    values = [
        str(row.get(field))
        for row in rows
        for field in fields
        if row.get(field)
    ]
    return max(values) if values else None


def timestamp_is_stale(fetched_at: str | None, *, max_age_hours: float = 24.0) -> bool:
    if not fetched_at:
        return True
    try:
        parsed = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    except ValueError:
        return True
    return (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() > max_age_hours * 3600
