from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from ..parser_control import load_resolved_public_dataset
from ..sources import SOURCES
from ..storage import load_status
from .models import ApiMeta, Envelope, freshest_timestamp, timestamp_is_stale

router = APIRouter(prefix="/v1/system", tags=["v1-system"])


class SourceSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    site: str
    category: str
    url: str
    has_dataset: bool
    dataset_fetched_at: str | None = None


class DatasetSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_id: str
    has_dataset: bool
    fetched_at: str | None = None
    state: str | None = None


@router.get(
    "/sources",
    response_model=Envelope[list[SourceSummary]],
    response_model_exclude_none=True,
)
def sources(
    site: str | None = Query(None, min_length=1, max_length=80),
    category: str | None = Query(None, min_length=1, max_length=80),
) -> Envelope[list[SourceSummary]]:
    selected = [
        source
        for source in SOURCES
        if (not site or source.site == site) and (not category or source.category == category)
    ]
    rows: list[dict[str, Any]] = []
    for source in selected:
        dataset = load_resolved_public_dataset(source.id)
        rows.append(
            {
                "id": source.id,
                "site": source.site,
                "category": source.category,
                "url": source.url,
                "has_dataset": dataset is not None,
                "dataset_fetched_at": dataset.get("fetched_at") if dataset else None,
            }
        )
    fetched_at = freshest_timestamp(rows, "dataset_fetched_at")
    return Envelope(
        data=[SourceSummary.model_validate(row) for row in rows],
        meta=ApiMeta(
            source_id="source_registry",
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at),
            count=len(rows),
        ),
    )


@router.get(
    "/datasets",
    response_model=Envelope[list[DatasetSummary]],
    response_model_exclude_none=True,
)
def datasets() -> Envelope[list[DatasetSummary]]:
    rows: list[dict[str, Any]] = []
    for source in SOURCES:
        dataset = load_resolved_public_dataset(source.id)
        status = load_status(source.id) or {}
        rows.append(
            {
                "source_id": source.id,
                "has_dataset": dataset is not None,
                "fetched_at": dataset.get("fetched_at") if dataset else None,
                "state": status.get("state"),
            }
        )
    fetched_at = freshest_timestamp(rows, "fetched_at")
    return Envelope(
        data=[DatasetSummary.model_validate(row) for row in rows],
        meta=ApiMeta(
            source_id="all_datasets",
            fetched_at=fetched_at,
            stale=timestamp_is_stale(fetched_at),
            count=len(rows),
        ),
    )


@router.get("/health", response_model=Envelope[dict[str, Any]])
def health() -> Envelope[dict[str, Any]]:
    from ..main import cached_health_diagnostics

    diagnostics = cached_health_diagnostics()
    fetched_at = freshest_timestamp(
        [
            {
                "fetched_at": (
                    load_resolved_public_dataset(source.id) or {}
                ).get("fetched_at")
            }
            for source in SOURCES
        ],
        "fetched_at",
    )
    return Envelope(
        data=diagnostics,
        meta=ApiMeta(
            source_id="system_health",
            fetched_at=fetched_at,
            stale=bool(diagnostics.get("stale_count")),
            count=len(SOURCES),
        ),
    )
