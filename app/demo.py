from __future__ import annotations

from typing import Any

from .deck_decode import (
    decode_all_codes_in_text,
    first_deck_code_from_text,
)
from .hsguru_evidence import hsguru_data_evidence
from .source_state import SourceState
from .sources import SOURCE_BY_ID, SOURCES, Source
from .storage import load_dataset, load_status
from .structured import build_structured


def _structured_from_data(source: Source, data: dict[str, Any]) -> dict[str, Any]:
    if data.get("structured"):
        return data["structured"]
    return build_structured(source, data)


def _streamer_decks_view(data: dict[str, Any]) -> dict[str, Any]:
    decks: list[dict[str, Any]] = []
    for table in data.get("tables") or []:
        for row in table.get("objects") or []:
            raw_deck = str(row.get("Deck") or "")
            decoded = decode_all_codes_in_text(raw_deck) or {"ok": False, "cards": []}
            code = decoded.get("code") or first_deck_code_from_text(raw_deck) or ""
            strategy = str(row.get("Streamer") or "Unknown")
            if "###" in raw_deck:
                mid = raw_deck.split("###", 1)[1].strip()
                if " AAE" in mid:
                    strategy = mid.split(" AAE")[0].strip()[:80] or strategy
                else:
                    strategy = mid[:80]
            decks.append(
                {
                    "strategy": strategy,
                    "streamer": row.get("Streamer"),
                    "format": row.get("Format"),
                    "record": row.get("Win - Loss"),
                    "deck_code": code or None,
                    "cards": decoded.get("cards") if decoded.get("ok") else [],
                    "hero": decoded.get("hero"),
                    "decode_ok": decoded.get("ok", False),
                }
            )
    return {
        "type": "streamer_decks",
        "kind": "streamer_decks",
        "title": data.get("title"),
        "decks": decks[:25],
    }


def _active_trinkets_view(view: dict[str, Any]) -> dict[str, Any]:
    if view.get("type") != "bg_trinkets":
        return view
    trinkets = view.get("trinkets")
    if not isinstance(trinkets, list):
        return view
    active = [
        row
        for row in trinkets
        if isinstance(row, dict) and (row.get("pick_rate") or row.get("avg_placement"))
    ]
    filtered = dict(view)
    filtered["trinkets"] = active
    filtered["active_trinkets"] = len(active)
    filtered["hidden_inactive_trinkets"] = len(trinkets) - len(active)
    return filtered


def build_demo_view(source_id: str) -> dict[str, Any]:
    source = SOURCE_BY_ID[source_id]
    status_fallback_reason: str | None = None
    try:
        status = load_status(source_id)
    except (OSError, UnicodeError, ValueError):
        status = None
        status_fallback_reason = "status_corrupt"
    if status is None and status_fallback_reason is None:
        status_fallback_reason = "status_missing"
    publication_read = None
    from .dataset_publication_store import (
        STANDARD_CARDS_SOURCE_ID,
        DatasetPublicationStore,
        PublicationUnavailable,
    )

    if source_id == STANDARD_CARDS_SOURCE_ID:
        try:
            publication_read = DatasetPublicationStore().read_published(source_id)
            dataset = publication_read.dataset
        except PublicationUnavailable as exc:
            return {
                "source_id": source_id,
                "ok": False,
                "unavailable": True,
                "status": status,
                "reason": exc.reason,
                "message": "Опубликованная статистика карт временно недоступна",
            }
    else:
        dataset = load_dataset(source_id)
    if dataset is None:
        return {
            "source_id": source_id,
            "ok": False,
            "status": status,
            "message": "Нет кэшированного датасета",
            **(
                {"data_evidence": hsguru_data_evidence(None)}
                if source.site == "hsguru"
                else {}
            ),
        }

    if source_id != STANDARD_CARDS_SOURCE_ID:
        # Sources explicitly registered for early publication still use the
        # stable/early resolver. Standard cards reject provisional candidates
        # before publication, so its LKG is already the exact public document.
        from .parser_control import resolve_public_dataset

        dataset = resolve_public_dataset(source_id, dataset)
        if dataset is None:
            return {
                "source_id": source_id,
                "ok": False,
                "status": status,
                "message": "Стабильный датасет ещё не доступен",
                **(
                    {"data_evidence": hsguru_data_evidence(None)}
                    if source.site == "hsguru"
                    else {}
                ),
            }

    data = dataset.get("data") or {}
    structured = _structured_from_data(source, data)

    if source.category == "streamer_decks":
        view = _streamer_decks_view(data)
        view["structured"] = structured
    else:
        view = dict(structured)
        view["title"] = data.get("title")
        view["kind"] = structured.get("type", source.category)
        view = _active_trinkets_view(view)
    view["type"] = view.get("type") or view.get("kind")

    result = {
        "source_id": source_id,
        "ok": True,
        "site": source.site,
        "category": source.category,
        "url": source.url,
        "fetched_at": dataset.get("fetched_at"),
        "backend": dataset.get("backend"),
        "status": status,
        "view": view,
    }
    if source.site == "hsguru":
        result["data_evidence"] = hsguru_data_evidence(dataset)
    if publication_read is not None:
        from .parser_control import dataset_publication_mode

        mode = dataset_publication_mode(dataset)
        result["publication"] = {
            "mode": mode,
            "channel": mode,
            "storage_channel": "published_lkg",
            "dataset_version": publication_read.dataset_version,
            "published_at": publication_read.published_at,
            "stale": publication_read.stale or status_fallback_reason is not None,
            "fallback_reason": (
                publication_read.fallback_reason or status_fallback_reason
            ),
            "age_hours": publication_read.age_hours,
        }
    return result


def build_overview() -> dict[str, Any]:
    from .config import source_operationally_enabled, stale_dataset_hours
    from .parser_control import load_resolved_public_dataset
    from .stale_monitor import find_stale_sources

    stale_by_source = {
        str(item.get("source_id")): item
        for item in find_stale_sources(include_ok=True)
        if item.get("source_id")
    }
    default_stale_hours = stale_dataset_hours()
    items: list[dict[str, Any]] = []
    for source in SOURCES:
        status = load_status(source.id) or {}
        dataset = load_resolved_public_dataset(source.id)
        operationally_enabled = source_operationally_enabled(source.id)
        stale = stale_by_source.get(source.id)
        state = status.get("state", SourceState.NEVER_FETCHED)
        if not operationally_enabled:
            state = "disabled"
        items.append(
            {
                "source_id": source.id,
                "site": source.site,
                "category": source.category,
                "description": source.description,
                "state": state,
                "fetched_at": (
                    dataset.get("fetched_at") if dataset else status.get("fetched_at")
                ),
                "has_dataset": dataset is not None,
                "operationally_enabled": operationally_enabled,
                "stale": operationally_enabled and stale is not None,
                "stale_reason": stale.get("reason") if stale else None,
                "stale_hours_threshold": (source.stale_hours or default_stale_hours),
                "serving_cached_dataset": bool(status.get("serving_cached_dataset")),
            }
        )
        if source.site == "hsguru":
            items[-1]["data_evidence"] = hsguru_data_evidence(dataset)
    operational = [item for item in items if item["operationally_enabled"]]
    ok = sum(1 for item in operational if item["state"] == SourceState.OK)
    return {
        "sources": items,
        "ok_count": ok,
        "operational_total": len(operational),
        "disabled_count": len(items) - len(operational),
        "total": len(items),
    }
