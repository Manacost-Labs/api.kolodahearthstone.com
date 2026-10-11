from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from ..graphql_api.repository import RepositoryUnavailable
from ..material_search import fetch_material, search_materials

router = APIRouter(prefix="/v1", tags=["material-search"])


class Material(BaseModel):
    id: str
    kind: str
    title: str
    subtitle: str | None = None
    text: str
    image_url: str | None = None
    source_id: str | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any]
    url: str


class SearchMeta(BaseModel):
    mode: str
    fallback_reason: str | None = None
    model: str | None = None
    indexed_at: str | None = None
    elapsed_ms: float
    count: int


class SearchResponse(BaseModel):
    items: list[Material]
    meta: SearchMeta


@router.get("/search", response_model=SearchResponse)
async def search(
    response: Response,
    q: str = Query(min_length=2, max_length=240),
    kind: Annotated[list[str] | None, Query()] = None,
    limit: int = Query(10, ge=1, le=50),
    mode: str = Query("hybrid", pattern="^(hybrid|lexical)$"),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await search_materials(q, kind, limit, mode)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    except RepositoryUnavailable:
        raise HTTPException(503, detail="Material catalog unavailable") from None


@router.get("/materials/{material_id}", response_model=Material)
async def material(material_id: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        item = await fetch_material(material_id)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None
    except RepositoryUnavailable:
        raise HTTPException(503, detail="Material catalog unavailable") from None
    if item is None:
        raise HTTPException(404, detail="Material not found")
    return item
