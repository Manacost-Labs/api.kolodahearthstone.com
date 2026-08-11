from __future__ import annotations

import secrets
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    api_key,
    cors_allowed_origins,
    orchestrator_api_key,
    python_environment,
)
from .demo import build_demo_view, build_overview
from .fetcher import refresh_sources
from .http_observability import RequestObservabilityMiddleware, generic_server_error
from .public_cache import PublicCacheMiddleware
from .routers.arena import router as arena_v1_router
from .routers.bg import router as bg_v1_router
from .routers.constructed import router as constructed_v1_router
from .routers.hsguru_meta import router as hsguru_meta_v1_router
from .routers.system import router as system_v1_router
from .source_state import SourceState
from .sources import SOURCE_BY_ID, SOURCES
from .storage import load_dataset, load_status, root_dir, save_dataset, save_status
from .trinket_slices import (
    DEFAULT_TRINKET_MMR,
    DEFAULT_TRINKET_TIME_RANGE,
    TRINKET_SLICE_SOURCE_IDS,
    source_ids_for_trinket_slice,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
ACTIVE_TRINKET_SOURCE_IDS = {
    "hsreplay_battlegrounds_trinkets_lesser",
    "hsreplay_battlegrounds_trinkets_greater",
} | TRINKET_SLICE_SOURCE_IDS
_HEALTH_CACHE_SECONDS = 15.0
_health_cache_lock = threading.Lock()
_health_cache_at = 0.0
_health_cache_payload: dict[str, Any] | None = None
_STANDARD_PUBLICATION_REVISION_HEADER = "X-HS-Publication-Representation"
_PUBLICATION_UNAVAILABLE_HEADERS = {
    "Retry-After": "60",
    "Cache-Control": "no-store",
}

app = FastAPI(
    title="Hearthstone Data API",
    version="0.1.0",
    description="Cached API for configured Hearthstone public data sources.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"],
    allow_headers=["Accept", "Content-Type", "X-API-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(PublicCacheMiddleware)
app.include_router(constructed_v1_router)
app.include_router(bg_v1_router)
app.include_router(arena_v1_router)
app.include_router(system_v1_router)
app.include_router(hsguru_meta_v1_router)


@app.on_event("startup")
def start_parser_control_worker() -> None:
    # Queued runs are persisted in the data directory. Starting the worker as
    # part of application startup resumes them after an API process restart.
    from .parser_control import parser_run_worker

    parser_run_worker().start()


@app.on_event("startup")
def bootstrap_standard_cards_publication() -> None:
    """Create the protected publication channel from an existing valid cache.

    This one-time compatibility bridge avoids a public outage on the first
    deployment of the publication store. It never publishes an unvalidated
    legacy snapshot and never replaces an already healthy published LKG.
    """

    from .dataset_publication_store import (
        STANDARD_CARDS_SOURCE_ID,
        DatasetPublicationStore,
        PublicationUnavailable,
        validate_and_publish_standard_cards_candidate,
        validate_standard_cards_snapshot,
    )
    from .refresh_log import log_action

    store = DatasetPublicationStore()

    def audit(action: str, **kwargs: Any) -> None:
        try:
            log_action(action, source_id=STANDARD_CARDS_SOURCE_ID, **kwargs)
        except Exception:
            # Recovery has its own durable manifest. Telemetry is best effort
            # and cannot invert or crash a completed startup repair.
            pass

    publication_failure_reason: str | None = None
    try:
        store.read_published(STANDARD_CARDS_SOURCE_ID)
        return
    except PublicationUnavailable as exc:
        publication_failure_reason = exc.reason
        audit(
            "dataset.publication.bootstrap.repair",
            level="warn",
            detail=exc.reason,
        )
    try:
        intact_pointer = store.pointer_dataset_version(STANDARD_CARDS_SOURCE_ID)
    except PublicationUnavailable:
        intact_pointer = None
    if (
        intact_pointer is not None
        and publication_failure_reason == "published_corrupt"
    ):
        # A valid manifest whose selected immutable file is damaged requires an
        # explicit administrator rollback. Startup must not silently select N-1.
        audit(
            "dataset.publication.bootstrap.skip",
            level="warn",
            detail="manifest pointer is intact; administrator recovery required",
            extra={"dataset_version": intact_pointer},
        )
        return

    source = SOURCE_BY_ID[STANDARD_CARDS_SOURCE_ID]
    recovery_candidates: list[tuple[str, str, dict[str, Any]]] = []
    for version, immutable in store.immutable_recovery_candidates(
        STANDARD_CARDS_SOURCE_ID
    ):
        recovery_candidates.append(("immutable", version, immutable))
    try:
        mutable = load_dataset(STANDARD_CARDS_SOURCE_ID)
    except Exception as exc:
        audit(
            "dataset.publication.bootstrap.skip",
            level="warn",
            detail=f"legacy cache unreadable: {type(exc).__name__}",
        )
        mutable = None
    if isinstance(mutable, dict):
        from .dataset_publication_store import dataset_version

        recovery_candidates.append(("mutable", dataset_version(mutable), mutable))

    valid_candidates: list[tuple[float, int, str, dict[str, Any]]] = []
    seen_versions: set[str] = set()
    for origin, version, candidate_dataset in recovery_candidates:
        if version in seen_versions:
            continue
        seen_versions.add(version)
        validation = validate_standard_cards_snapshot(source, candidate_dataset)
        if not validation.accepted:
            continue
        try:
            candidate_time = datetime.fromisoformat(
                str(candidate_dataset.get("fetched_at") or "").replace("Z", "+00:00")
            )
            if candidate_time.tzinfo is None:
                candidate_time = candidate_time.replace(tzinfo=UTC)
            timestamp = candidate_time.astimezone(UTC).timestamp()
        except (TypeError, ValueError):
            continue
        valid_candidates.append(
            (timestamp, 1 if origin == "immutable" else 0, origin, candidate_dataset)
        )
    if not valid_candidates:
        audit(
            "dataset.publication.bootstrap.skip",
            level="warn",
            detail="no fully valid recovery candidate",
        )
        return
    valid_candidates.sort(reverse=True, key=lambda row: (row[0], row[1]))
    selected_timestamp, _immutable_preference, _origin, legacy = valid_candidates[0]
    # Ordering/regression checks during recovery may only use a snapshot that
    # passed the same full semantic and temporal validation as the selected one.
    # A checksum-valid but semantically broken immutable file must never become
    # the baseline that rejects a good repair candidate.
    validated_previous = next(
        (
            candidate_dataset
            for timestamp, _preference, _candidate_origin, candidate_dataset in valid_candidates[1:]
            if timestamp < selected_timestamp
        ),
        None,
    )

    if (
        intact_pointer is not None
        and publication_failure_reason != "published_from_future"
    ):
        try:
            current = store.read_published_unbounded(STANDARD_CARDS_SOURCE_ID)
            current_time = datetime.fromisoformat(
                str((current or {}).get("fetched_at") or "").replace("Z", "+00:00")
            )
            if current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=UTC)
            if selected_timestamp <= current_time.astimezone(UTC).timestamp():
                audit(
                    "dataset.publication.bootstrap.skip",
                    level="warn",
                    detail="no newer fully valid recovery candidate",
                    extra={"dataset_version": intact_pointer},
                )
                return
        except (PublicationUnavailable, TypeError, ValueError):
            pass
    try:
        decision = validate_and_publish_standard_cards_candidate(
            source,
            legacy,
            store=store,
            validated_previous=validated_previous,
        )
    except Exception as exc:
        audit(
            "dataset.publication.bootstrap.fail",
            level="error",
            detail=str(exc)[:1000],
            error_type=type(exc).__name__,
        )
        return
    audit(
        "dataset.publication.bootstrap.ok"
        if decision.accepted
        else "dataset.publication.bootstrap.reject",
        level="info" if decision.accepted else "warn",
        detail=decision.reason,
        extra={"dataset_version": decision.dataset_version},
    )


@app.middleware("http")
async def no_cache_ui(request: Request, call_next):
    standard_dataset_path = "/datasets/hsreplay_cards_legend_1d"
    exact_publication_cache = (
        request.method in {"GET", "HEAD"}
        and request.url.path == standard_dataset_path
    )
    requested_etag = request.headers.get("if-none-match")
    forwarded_request = request
    if exact_publication_cache and requested_etag:
        # The generic cache middleware derives revisions from the mutable
        # ingestion cache. Strip the conditional header for this one protected
        # route, then evaluate it below against the exact LKG response revision.
        scope = dict(request.scope)
        scope["headers"] = [
            (name, value)
            for name, value in request.scope.get("headers", [])
            if name.lower() != b"if-none-match"
        ]
        forwarded_request = Request(scope, receive=request.receive)

    response = await call_next(forwarded_request)
    if exact_publication_cache:
        revision = response.headers.get(_STANDARD_PUBLICATION_REVISION_HEADER)
        if _STANDARD_PUBLICATION_REVISION_HEADER in response.headers:
            del response.headers[_STANDARD_PUBLICATION_REVISION_HEADER]
        if revision and 200 <= response.status_code < 300:
            from .public_cache import PUBLIC_CACHE_CONTROL, build_etag

            etag = build_etag(
                request.url.path,
                request.scope.get("query_string") or b"",
                revision,
            )
            response.headers["ETag"] = etag
            response.headers["Cache-Control"] = PUBLIC_CACHE_CONTROL
            normalized = etag.removeprefix("W/")
            matches = bool(requested_etag) and any(
                candidate.strip().removeprefix("W/") in {"*", normalized}
                for candidate in requested_etag.split(",")
            )
            if matches:
                headers = dict(response.headers)
                headers.pop("content-length", None)
                headers.pop("content-type", None)
                return Response(status_code=304, headers=headers)
    if request.url.path.startswith("/admin"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif request.url.path.startswith("/ui"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Keep request correlation outside CORS, cache, and UI cache-control middleware
# so every HTTP response, including preflights and handled errors, gets an ID.
app.add_middleware(RequestObservabilityMiddleware)
app.add_exception_handler(Exception, generic_server_error)


if WEB_DIR.is_dir():
    app.mount("/ui/assets", StaticFiles(directory=WEB_DIR), name="ui-assets")


def require_admin(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = api_key()
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API key is not configured")
    if x_api_key and secrets.compare_digest(x_api_key, expected):
        return
    raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


def require_orchestrator(
    x_orchestrator_key: Annotated[
        str | None,
        Header(alias="X-Orchestrator-Key"),
    ] = None,
) -> None:
    expected = orchestrator_api_key()
    admin_token = api_key()
    unsafe_configuration = (
        not expected
        or len(expected) < 32
        or bool(admin_token and secrets.compare_digest(expected, admin_token))
    )
    if unsafe_configuration:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator API key is not safely configured",
        )
    if x_orchestrator_key and secrets.compare_digest(x_orchestrator_key, expected):
        return
    raise HTTPException(
        status_code=401,
        detail="Missing or invalid X-Orchestrator-Key",
    )


@app.get("/")
def redirect_to_ui() -> RedirectResponse:
    return RedirectResponse(url="/ui")


@app.get("/ui")
def demo_index() -> FileResponse:
    index = WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Demo UI not installed")
    return FileResponse(index)


@app.get("/ui/logs")
def ops_logs_ui() -> FileResponse:
    page = WEB_DIR / "logs.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Ops logs UI not installed")
    return FileResponse(page)


@app.get("/ui/technologies")
def technologies_ui() -> FileResponse:
    page = WEB_DIR / "technologies.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="Technologies UI not installed")
    return FileResponse(page)


@app.get("/ops/summary", dependencies=[Depends(require_admin)])
def ops_summary(since_hours: float = Query(24.0, ge=1.0, le=168.0)) -> dict:
    from .refresh_log import build_summary

    return build_summary(since_hours=since_hours)


@app.get("/ops/events", dependencies=[Depends(require_admin)])
def ops_events(
    limit: int = Query(200, ge=1, le=2000),
    source_id: str | None = None,
    event: str | None = None,
    action: str | None = None,
    action_group: str | None = None,
    level: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    since_hours: float | None = Query(None, ge=0.1, le=168.0),
) -> dict:
    from .refresh_log import read_events

    return {
        "events": read_events(
            limit=limit,
            source_id=source_id,
            event=event,
            action=action,
            action_group=action_group,
            level=level,
            trace_id=trace_id,
            run_id=run_id,
            since_hours=since_hours,
        )
    }


@app.get("/ops/trace/{trace_id}", dependencies=[Depends(require_admin)])
def ops_trace(trace_id: str) -> dict:
    from .refresh_log import build_trace_timeline

    return build_trace_timeline(trace_id)


@app.get("/ops/run/{run_id}", dependencies=[Depends(require_admin)])
def ops_run(run_id: str) -> dict:
    from .refresh_log import build_run_timeline

    return build_run_timeline(run_id)


@app.get("/ops/health", dependencies=[Depends(require_admin)])
def ops_health() -> dict:
    return cached_health_diagnostics()


@app.get("/demo/overview")
def demo_overview() -> dict:
    return build_overview()


@app.get("/demo/view/{source_id}")
def demo_view(source_id: str) -> dict:
    if source_id not in SOURCE_BY_ID:
        raise HTTPException(status_code=404, detail="Unknown source")
    result = build_demo_view(source_id)
    if result.get("unavailable"):
        raise HTTPException(
            status_code=503,
            detail={
                "message": result.get("message"),
                "source_id": source_id,
                "reason": result.get("reason"),
            },
            headers=_PUBLICATION_UNAVAILABLE_HEADERS,
        )
    return result


def source_payload(source_id: str) -> dict:
    from .parser_control import load_resolved_public_dataset

    source = SOURCE_BY_ID[source_id]
    status = load_status(source_id)
    dataset = load_resolved_public_dataset(source_id)
    return {
        "id": source.id,
        "site": source.site,
        "category": source.category,
        "url": source.url,
        "fetch_url": source.fetch_url,
        "fragment": source.fragment,
        "description": source.description,
        "status": status,
        "has_dataset": dataset is not None,
        "dataset_fetched_at": dataset.get("fetched_at") if dataset else None,
        "semantic_quality": _semantic_dataset_quality(source_id, dataset),
    }


def _semantic_dataset_quality(
    source_id: str,
    dataset: dict[str, Any] | None,
) -> dict[str, Any] | None:
    structured = (((dataset or {}).get("data") or {}).get("structured") or {})
    if not structured:
        return None
    from .source_contracts import contract_quality_report
    from .source_validators import validate_structured

    semantic_report = validate_structured(source_id, structured)
    contract_report = contract_quality_report(source_id, structured)
    semantic_issues = [
        {
            "code": issue.code,
            "message": issue.message,
            "field": issue.field,
            "severity": issue.severity,
        }
        for issue in semantic_report.issues
    ]
    contract_issues = [
        {
            "code": "source_contract.failed",
            "message": warning,
            "field": None,
            "severity": "error",
        }
        for warning in contract_report.get("warnings") or []
    ]
    score_candidates = [semantic_report.score]
    if isinstance(contract_report.get("quality_score"), (int, float)):
        score_candidates.append(float(contract_report["quality_score"]))
    return {
        "ok": semantic_report.ok and bool(contract_report.get("ok")),
        "score": min(score_candidates),
        "issues": [*semantic_issues, *contract_issues],
        "metrics": semantic_report.metrics,
        "contract": contract_report,
    }


def _active_trinkets_only(structured: dict[str, Any]) -> dict[str, Any]:
    trinkets = structured.get("trinkets")
    if not isinstance(trinkets, list):
        return structured
    active = [
        row
        for row in trinkets
        if isinstance(row, dict) and (row.get("pick_rate") or row.get("avg_placement"))
    ]
    filtered = dict(structured)
    filtered["trinkets"] = active
    filtered["active_trinkets"] = len(active)
    filtered["hidden_inactive_trinkets"] = len(trinkets) - len(active)
    return filtered


def public_dataset_payload(
    source_id: str,
    dataset: dict[str, Any],
    *,
    publication_read: Any | None = None,
) -> dict[str, Any]:
    payload = dict(dataset)
    if source_id in ACTIVE_TRINKET_SOURCE_IDS:
        data = dict(payload.get("data") or {})
        for key in ("structured", "hsreplay_extracted"):
            if isinstance(data.get(key), dict):
                data[key] = _active_trinkets_only(data[key])
        payload["data"] = data

    # This metadata describes the exact document selected by
    # resolve_public_dataset(), not the current mutable admin policy. Consumers
    # can therefore distinguish an early publication from a stable baseline
    # without a race against a later mode switch.
    from .parser_control import dataset_publication_mode

    mode = dataset_publication_mode(dataset)
    payload["publication"] = {
        "schema_version": 1,
        "source_id": source_id,
        "mode": mode,
        "channel": mode,
        "published_at": dataset.get("fetched_at"),
    }
    if publication_read is not None:
        payload["publication"].update(
            {
                "storage_channel": "published_lkg",
                "published_at": publication_read.published_at,
                "dataset_version": publication_read.dataset_version,
                "stale": publication_read.stale,
                "fallback_reason": publication_read.fallback_reason,
            }
        )
    return payload


def _trinket_rows_for_api(source_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    from .structured import enrich_trinket_variant_fields

    dataset = load_dataset(source_id) or {}
    structured = (dataset.get("data") or {}).get("structured") or {}
    rows = structured.get("trinkets") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if active_only and not (row.get("pick_rate") or row.get("avg_placement")):
            continue
        trinket_type = str(row.get("trinket_tier") or row.get("type") or "").strip()
        if not trinket_type:
            trinket_type = "Lesser" if source_id.endswith("_lesser") else "Greater"
        item = enrich_trinket_variant_fields(dict(row), trinket_type=trinket_type)
        item["source_id"] = source_id
        item["source_url"] = SOURCE_BY_ID[source_id].url
        out.append(item)
    return out


@app.get("/api/bg/trinkets")
def bg_trinkets(
    trinket_tier: str = Query("all", pattern="^(all|lesser|greater)$"),
    active_only: bool = Query(True),
    mmr: str = Query(
        DEFAULT_TRINKET_MMR,
        pattern="^(ALL|TOP_50_PERCENT|TOP_20_PERCENT|TOP_5_PERCENT|TOP_1_PERCENT)$",
    ),
    time_range: str = Query(
        DEFAULT_TRINKET_TIME_RANGE,
        alias="timeRange",
        pattern="^(CURRENT_BATTLEGROUNDS_PATCH|LAST_7_DAYS)$",
    ),
) -> dict[str, Any]:
    source_ids = list(source_ids_for_trinket_slice(mmr, time_range))
    rows: list[dict[str, Any]] = []
    fetched_at: list[str] = []
    for source_id in source_ids:
        dataset = load_dataset(source_id)
        if dataset and dataset.get("fetched_at"):
            fetched_at.append(str(dataset["fetched_at"]))
        rows.extend(_trinket_rows_for_api(source_id, active_only=active_only))
    if trinket_tier != "all":
        expected_tier = trinket_tier.lower()
        rows = [
            row
            for row in rows
            if str(row.get("trinket_tier") or row.get("type") or "").strip().lower()
            == expected_tier
        ]
    rows.sort(
        key=lambda row: (
            row.get("trinket_tier") or "",
            str(row.get("tier") or "Z"),
            float(row.get("avg_placement") or 99),
            row.get("name") or "",
            row.get("tribe") or "",
        )
    )
    return {
        "type": "bg_trinkets",
        "count": len(rows),
        "active_only": active_only,
        "trinket_tier": trinket_tier,
        "mmr": mmr,
        "timeRange": time_range,
        "games_note": (
            "games is the minimum integer sample compatible with HSReplay's "
            "rounded placement distribution"
        ),
        "fetched_at": max(fetched_at) if fetched_at else None,
        "trinkets": rows,
    }


@app.get("/system/technologies")
def system_technologies() -> dict:
    from .tech_stack import build_technologies_payload

    return build_technologies_payload()


@app.get("/firecrawl/hsreplay/map")
def firecrawl_hsreplay_map() -> dict:
    from .firecrawl_map import load_hsreplay_map

    payload = load_hsreplay_map()
    if payload is None:
        raise HTTPException(status_code=404, detail="HSReplay Firecrawl map has not been generated yet")
    return payload


@app.get("/firecrawl/hsreplay/index")
def firecrawl_hsreplay_index() -> dict:
    from .firecrawl_map import load_hsreplay_index

    payload = load_hsreplay_index()
    if payload is None:
        raise HTTPException(status_code=404, detail="HSReplay Firecrawl index has not been generated yet")
    return payload


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "serving_ok": True,
        "degraded": False,
        "checked_at": datetime.now(UTC).isoformat(),
    }


def build_health_diagnostics() -> dict:
    from .parser_control import resolve_public_dataset
    from .post_patch_policy import EARLY_SOURCE_IDS

    statuses: list[dict[str, Any] | None] = []
    status_errors: list[Exception | None] = []
    for source in SOURCES:
        try:
            statuses.append(load_status(source.id))
            status_errors.append(None)
        except (OSError, UnicodeError, ValueError) as exc:
            statuses.append(None)
            status_errors.append(exc)
    states: dict[str, int] = {}
    cached_sources: list[str] = []
    cached_after_failure_sources: list[str] = []
    hard_failed_sources: list[str] = []
    semantic_failed_sources: list[str] = []
    semantic_failures: list[dict[str, Any]] = []
    publication_failed_sources: list[str] = []
    publication_failures: list[dict[str, Any]] = []
    publication_stale_sources: list[str] = []
    publication_stale_details: list[dict[str, Any]] = []
    for source, status, status_error in zip(
        SOURCES, statuses, status_errors, strict=True
    ):
        dataset_error: Exception | None = None
        try:
            dataset = load_dataset(source.id)
        except (OSError, UnicodeError, ValueError) as exc:
            dataset = None
            dataset_error = exc
        state = status["state"] if status else SourceState.NEVER_FETCHED
        states[state] = states.get(state, 0) + 1
        if status and status.get("serving_cached_dataset"):
            cached_sources.append(source.id)
            if status.get("last_refresh_state") not in (None, SourceState.OK):
                cached_after_failure_sources.append(source.id)
        served_dataset = dataset
        standard_publication_usable = False
        if source.id == "hsreplay_cards_legend_1d":
            from .dataset_publication_store import (
                DatasetPublicationStore,
                PublicationUnavailable,
            )

            try:
                publication_read = DatasetPublicationStore().read_published(
                    source.id,
                    current_dataset=dataset,
                    current_error=dataset_error,
                    status=status,
                )
                standard_publication_usable = True
                served_dataset = publication_read.dataset
                fallback_reason = publication_read.fallback_reason
                if fallback_reason is None and status_error is not None:
                    fallback_reason = "status_corrupt"
                elif fallback_reason is None and status is None:
                    fallback_reason = "status_missing"
                if fallback_reason is not None:
                    publication_stale_sources.append(source.id)
                    publication_stale_details.append(
                        {
                            "source_id": source.id,
                            "fallback_reason": fallback_reason,
                            "dataset_version": publication_read.dataset_version,
                        }
                    )
            except PublicationUnavailable as exc:
                publication_failed_sources.append(source.id)
                publication_failures.append(
                    {
                        "source_id": source.id,
                        "reason": exc.reason,
                        "detail": exc.detail,
                    }
                )
        elif source.id in EARLY_SOURCE_IDS and isinstance(dataset, dict):
            served_dataset = resolve_public_dataset(source.id, dataset)
            if served_dataset is None:
                publication_failed_sources.append(source.id)
                publication_failures.append(
                    {
                        "source_id": source.id,
                        "reason": "stable_baseline_unavailable",
                        "detail": (
                            "Mutable dataset is provisional but no stable public "
                            "baseline is available"
                        ),
                    }
                )
        if state != SourceState.OK and not standard_publication_usable:
            hard_failed_sources.append(source.id)
        semantic_quality = _semantic_dataset_quality(source.id, served_dataset)
        if semantic_quality and not semantic_quality["ok"]:
            semantic_failed_sources.append(source.id)
            semantic_failures.append(
                {
                    "source_id": source.id,
                    "score": semantic_quality["score"],
                    "issues": semantic_quality["issues"],
                    "metrics": semantic_quality["metrics"],
                }
            )

    from .stale_monitor import find_stale_sources

    freshness_monitor_errors: list[str] = []
    try:
        stale_sources = find_stale_sources(include_ok=True)
    except Exception as exc:
        # The source loop above already loaded every file defensively. The
        # legacy monitor re-reads them, so a corrupt compatibility file must
        # degrade freshness diagnostics rather than crash the health route.
        stale_sources = []
        freshness_monitor_errors.append(
            f"{type(exc).__name__}: {exc}"[:1000]
        )
    stale_ids = [str(item["source_id"]) for item in stale_sources]
    serving_ok = (
        not hard_failed_sources
        and not semantic_failed_sources
        and not publication_failed_sources
    )
    freshness_ok = (
        not stale_ids
        and not cached_sources
        and not publication_stale_sources
        and not freshness_monitor_errors
    )
    return {
        "ok": serving_ok,
        "serving_ok": serving_ok,
        "freshness_ok": freshness_ok,
        "degraded": not (serving_ok and freshness_ok),
        "data_dir": str(root_dir()),
        "sources": len(SOURCES),
        "states": states,
        "hard_failed_sources": hard_failed_sources,
        "semantic_failed_sources": semantic_failed_sources,
        "semantic_failures": semantic_failures,
        "publication_failed_sources": publication_failed_sources,
        "publication_failures": publication_failures,
        "publication_stale_sources": publication_stale_sources,
        "publication_stale_details": publication_stale_details,
        "cached_sources": cached_sources,
        "cached_after_failure_sources": cached_after_failure_sources,
        "stale_sources": stale_ids,
        "stale_count": len(stale_ids),
        "freshness_monitor_errors": freshness_monitor_errors,
        "cached_count": len(cached_sources),
        "cached_after_failure_count": len(cached_after_failure_sources),
    }


def cached_health_diagnostics() -> dict:
    """Bound repeated health polling while keeping tests and CLI checks exact."""
    global _health_cache_at, _health_cache_payload
    if python_environment() == "test":
        return build_health_diagnostics()
    now = time.monotonic()
    with _health_cache_lock:
        if _health_cache_payload is not None and now - _health_cache_at < _HEALTH_CACHE_SECONDS:
            return _health_cache_payload
        payload = build_health_diagnostics()
        _health_cache_payload = payload
        _health_cache_at = now
        return payload


@app.get("/health/premium", dependencies=[Depends(require_admin)])
async def premium_health(live: bool = Query(False)) -> dict:
    from .premium_auth_health import build_premium_auth_health

    return await build_premium_auth_health(live=live)


@app.get("/sources")
def list_sources(site: str | None = None, category: str | None = None) -> dict:
    sources = SOURCES
    if site:
        sources = tuple(source for source in sources if source.site == site)
    if category:
        sources = tuple(source for source in sources if source.category == category)
    return {"sources": [source_payload(source.id) for source in sources]}


@app.get("/sources/{source_id}")
def get_source(source_id: str) -> dict:
    if source_id not in SOURCE_BY_ID:
        raise HTTPException(status_code=404, detail="Unknown source")
    return source_payload(source_id)


@app.get("/datasets")
def list_datasets() -> dict:
    from .parser_control import load_resolved_public_dataset

    return {
        "datasets": [
            {
                "source_id": source.id,
                "has_dataset": load_resolved_public_dataset(source.id) is not None,
                "status": load_status(source.id),
            }
            for source in SOURCES
        ]
    }


@app.head("/datasets/{source_id}", include_in_schema=False)
@app.get("/datasets/{source_id}")
def get_dataset(source_id: str, response: Response) -> dict:
    if source_id not in SOURCE_BY_ID:
        raise HTTPException(status_code=404, detail="Unknown source")
    from .dataset_publication_store import (
        STANDARD_CARDS_SOURCE_ID,
        DatasetPublicationStore,
        PublicationUnavailable,
    )

    if source_id == STANDARD_CARDS_SOURCE_ID:
        try:
            publication_read = DatasetPublicationStore().read_published(source_id)
        except PublicationUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "Published Standard card statistics are unavailable",
                    "source_id": source_id,
                    "reason": exc.reason,
                },
                headers=_PUBLICATION_UNAVAILABLE_HEADERS,
            ) from exc
        dataset = publication_read.dataset
        response.headers[_STANDARD_PUBLICATION_REVISION_HEADER] = (
            publication_read.representation_revision
        )
        return public_dataset_payload(
            source_id,
            dataset,
            publication_read=publication_read,
        )
    dataset = load_dataset(source_id)
    if dataset is None:
        status = load_status(source_id)
        raise HTTPException(
            status_code=404,
            detail={"message": "No successful dataset cached yet", "status": status},
        )
    from .parser_control import resolve_public_dataset

    published = resolve_public_dataset(source_id, dataset)
    if published is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Stable dataset is not available yet",
                "source_id": source_id,
                "publication_mode": "stable",
            },
        )
    return public_dataset_payload(source_id, published)


def _control_expected_revision(payload: dict[str, Any]) -> int:
    value = payload.get("expectedRevision")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise HTTPException(status_code=422, detail="expectedRevision must be a positive integer")
    return value


def _control_list_of_strings(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HTTPException(status_code=422, detail=f"{key} must be an array of strings")
    return [item.strip() for item in value if item.strip()]


def _orchestrator_list_of_identifiers(
    payload: dict[str, Any],
    key: str,
    *,
    maximum: int,
) -> list[str]:
    values = _control_list_of_strings(payload, key)
    allowed = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
    )
    if len(values) > maximum:
        raise HTTPException(status_code=422, detail=f"{key} contains too many entries")
    if any(
        len(value) > 120 or any(character not in allowed for character in value)
        for value in values
    ):
        raise HTTPException(status_code=422, detail=f"{key} contains an invalid identifier")
    return values


def _raise_parser_control_http_error(exc: Exception) -> None:
    from .parser_control import (
        InvalidControlRequest,
        ParserControlStorageError,
        RevisionConflict,
    )

    if isinstance(exc, RevisionConflict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REVISION_CONFLICT",
                "message": str(exc),
                "expectedRevision": exc.expected,
                "currentRevision": exc.current,
            },
        ) from exc
    if isinstance(exc, InvalidControlRequest):
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(exc)},
        ) from exc
    if isinstance(exc, ParserControlStorageError):
        raise HTTPException(
            status_code=503,
            detail={"code": "CONTROL_STORAGE_UNAVAILABLE", "message": str(exc)},
        ) from exc
    raise exc


def _orchestrator_run_view(run: dict[str, Any]) -> dict[str, Any]:
    """Return the minimal non-sensitive run contract exposed to Trigger.dev."""
    from .source_state import SourceState

    source_ids = [
        source_id
        for source_id in run.get("sourceIds") or []
        if isinstance(source_id, str)
    ]
    total = run.get("totalSources")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        total = len(source_ids)

    def count(name: str) -> int:
        value = run.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return 0
        return min(value, total)

    allowed_states = {str(state) for state in SourceState} | {"error"}
    results: list[dict[str, Any]] = []
    for raw in run.get("results") or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("sourceId"), str):
            continue
        state = raw.get("state")
        results.append(
            {
                "sourceId": raw["sourceId"],
                "state": state if state in allowed_states else "error",
                "servingCachedDataset": bool(raw.get("servingCachedDataset")),
            }
        )

    status = run.get("status")
    if status not in {"queued", "running", "succeeded", "partial", "failed"}:
        status = "failed"
    return {
        "id": str(run.get("id") or ""),
        "status": status,
        "sourceIds": source_ids,
        "totalSources": total,
        "completedSources": count("completedSources"),
        "failedSources": count("failedSources"),
        "results": results,
    }


@app.get("/admin/parser-control", dependencies=[Depends(require_admin)])
def get_parser_control() -> dict[str, Any]:
    from .parser_control import parser_control_store

    try:
        return parser_control_store().snapshot()
    except Exception as exc:
        _raise_parser_control_http_error(exc)
        raise


@app.patch("/admin/parser-control/policy", dependencies=[Depends(require_admin)])
def update_parser_control_policy(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from .parser_control import parser_control_store

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be an object")
    try:
        return parser_control_store().update_policy(
            expected_revision=_control_expected_revision(payload),
            mode=str(payload.get("mode") or ""),
            early_until=payload.get("earlyUntil"),
            reason=payload.get("reason"),
            updated_by=str(payload.get("updatedBy") or "admin-api"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        _raise_parser_control_http_error(exc)
        raise


@app.patch("/admin/parser-control/sections", dependencies=[Depends(require_admin)])
def update_parser_control_sections(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from .parser_control import parser_control_store

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be an object")
    rows = payload.get("sections")
    changes: dict[str, bool] = {}
    if isinstance(rows, dict):
        changes = dict(rows)
    elif isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                raise HTTPException(
                    status_code=422,
                    detail="sections entries must contain id and enabled",
                )
            changes[row["id"]] = row.get("enabled")
    else:
        raise HTTPException(status_code=422, detail="sections must be an object or array")
    try:
        return parser_control_store().update_sections(
            expected_revision=_control_expected_revision(payload),
            changes=changes,
            updated_by=str(payload.get("updatedBy") or "admin-api"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        _raise_parser_control_http_error(exc)
        raise


@app.post(
    "/admin/parser-runs",
    dependencies=[Depends(require_admin)],
    status_code=202,
)
def create_parser_run(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from .parser_control import expand_run_selection, parser_run_worker

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be an object")
    try:
        selected = expand_run_selection(
            source_ids=_control_list_of_strings(payload, "sourceIds"),
            section_ids=_control_list_of_strings(payload, "sectionIds"),
        )
        run, deduplicated = parser_run_worker().enqueue(
            source_ids=selected,
            requested_by=str(payload.get("requestedBy") or "admin-api"),
            reason=payload.get("reason"),
        )
        return {"run": run, "deduplicated": deduplicated}
    except HTTPException:
        raise
    except Exception as exc:
        _raise_parser_control_http_error(exc)
        raise


@app.get("/admin/parser-runs", dependencies=[Depends(require_admin)])
def list_parser_runs(limit: int = Query(20, ge=1, le=50)) -> dict[str, Any]:
    from .parser_control import parser_control_store

    try:
        return parser_control_store().list_runs(limit=limit)
    except Exception as exc:
        _raise_parser_control_http_error(exc)
        raise


@app.post(
    "/admin/orchestrator/parser-runs",
    dependencies=[Depends(require_orchestrator)],
    status_code=202,
)
def create_orchestrated_parser_run(
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    from .parser_control import expand_run_selection, parser_run_worker

    allowed_fields = {
        "requestId",
        "sourceIds",
        "sectionIds",
        "reason",
    }
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be an object")
    unexpected = sorted(set(payload) - allowed_fields)
    if unexpected:
        raise HTTPException(
            status_code=422,
            detail={"unexpected_fields": unexpected},
        )
    request_id = payload.get("requestId")
    if not isinstance(request_id, str) or not request_id.strip():
        raise HTTPException(status_code=422, detail="requestId is required")
    reason = payload.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise HTTPException(status_code=422, detail="reason must be a string")
    if isinstance(reason, str) and len(reason) > 500:
        raise HTTPException(status_code=422, detail="reason must not exceed 500 characters")
    try:
        selected = expand_run_selection(
            source_ids=_orchestrator_list_of_identifiers(
                payload,
                "sourceIds",
                maximum=len(SOURCE_BY_ID),
            ),
            section_ids=_orchestrator_list_of_identifiers(
                payload,
                "sectionIds",
                maximum=30,
            ),
        )
        run, deduplicated = parser_run_worker().enqueue(
            source_ids=selected,
            requested_by="trigger.dev",
            reason=reason,
            request_id=request_id,
        )
        return {
            "run": _orchestrator_run_view(run),
            "deduplicated": deduplicated,
        }
    except HTTPException:
        raise
    except Exception as exc:
        _raise_parser_control_http_error(exc)
        raise


@app.get(
    "/admin/orchestrator/parser-runs/{run_id}",
    dependencies=[Depends(require_orchestrator)],
)
def get_orchestrated_parser_run(run_id: str) -> dict[str, Any]:
    from .parser_control import parser_control_store

    if len(run_id) != 32 or any(character not in "0123456789abcdef" for character in run_id):
        raise HTTPException(status_code=404, detail="Parser run not found")
    try:
        run = parser_control_store().get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Parser run not found")
        return {"run": _orchestrator_run_view(run)}
    except HTTPException:
        raise
    except Exception as exc:
        _raise_parser_control_http_error(exc)
        raise


@app.post("/admin/refresh", dependencies=[Depends(require_admin)])
async def refresh(
    source_id: Annotated[list[str] | None, Query()] = None,
) -> dict:
    if source_id:
        missing = [item for item in source_id if item not in SOURCE_BY_ID]
        if missing:
            raise HTTPException(status_code=404, detail={"unknown_sources": missing})
        pipeline = [item for item in source_id if SOURCE_BY_ID[item].kind == "pipeline"]
        if pipeline:
            raise HTTPException(
                status_code=400,
                detail={
                    "pipeline_sources": pipeline,
                    "message": (
                        "Pipeline sources are refreshed by their dedicated systemd "
                        "timers/endpoints, not by /admin/refresh."
                    ),
                },
            )
    return {"results": await refresh_sources(source_id)}


@app.post("/admin/refresh/hsreplay-archetypes", dependencies=[Depends(require_admin)])
async def refresh_hsreplay_archetypes(
    limit: int | None = Query(None, ge=1, le=500),
    rank_range: str = Query("LEGEND", min_length=1, max_length=80),
    game_type: str = Query("RANKED_STANDARD", min_length=1, max_length=80),
    region: str = Query("REGION_EU", min_length=1, max_length=80),
) -> dict:
    from .hsreplay_archetypes_db import (
        export_latest_archetypes_json,
        refresh_hsreplay_archetype_database,
    )

    result = await refresh_hsreplay_archetype_database(
        rank_range=rank_range,
        game_type=game_type,
        region=region,
        limit=limit,
    )
    result["export_path"] = str(export_latest_archetypes_json())
    return result




@app.get("/api/hsreplay/archetypes")
async def hsreplay_archetypes_dictionary(
    hl: str = Query("en", min_length=2, max_length=8),
) -> dict:
    """
    Return HSReplay's complete archetype reference dictionary.

    Consumer: the admin-only `/archetypes/` page in HS-Arena. The upstream
    request must stay in this service because `fetch_hsreplay_json` owns the
    protected HSReplay transport/session; browser-side calls are blocked by
    Cloudflare. This endpoint deliberately returns English canonical names.
    Russian naming is Arena's editorial concern and is applied from its
    `archetype_translations` table, not persisted here.
    """
    from .hsreplay_client import fetch_hsreplay_json

    locale = "ru" if hl.lower().startswith("ru") else "en"
    url = f"https://hsreplay.net/api/v1/archetypes/?hl={locale}"
    payload = await fetch_hsreplay_json(url, source_id="hsreplay_archetypes_dictionary")
    rows = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), list) else payload
    if not isinstance(rows, list):
        rows = []
    archetypes = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        archetypes.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "class": row.get("player_class_name") or row.get("player_class"),
                "url": row.get("url"),
                # A reference archetype can exist for one format only.  Keep
                # these flags so the consuming UI never guesses the format
                # from an English name or a URL.
                "standard": bool(row.get("standard_ccp_signature_core")),
                "wild": bool(row.get("wild_ccp_signature_core")),
            }
        )
    archetypes.sort(key=lambda item: (str(item.get("class") or ""), str(item.get("name") or ""), int(item.get("id") or 0)))
    return {"count": len(archetypes), "hl": locale, "archetypes": archetypes}

@app.post("/admin/refresh/bg-minions-db", dependencies=[Depends(require_admin)])
async def refresh_bg_minions_db() -> dict:
    from .hsreplay_bg_minions_db import (
        export_latest_bg_minions_json,
        refresh_bg_minion_database,
    )

    result = await refresh_bg_minion_database()
    result["export_path"] = str(export_latest_bg_minions_json())
    return result


@app.post("/admin/refresh/bg-hero-details", dependencies=[Depends(require_admin)])
async def refresh_bg_hero_details(
    limit: int | None = Query(None, ge=1, le=500),
    concurrency: int = Query(3, ge=1, le=6),
    mmr: str = Query("TOP_50_PERCENT", min_length=1, max_length=80),
    time_range: str = Query("CURRENT_BATTLEGROUNDS_PATCH", min_length=1, max_length=80),
) -> dict:
    from .hsreplay_bg_hero_details import refresh_bg_hero_details as refresh_details

    return await refresh_details(limit=limit, concurrency=concurrency, mmr=mmr, time_range=time_range)


@app.post("/admin/refresh/hsguru-meta-matrix", dependencies=[Depends(require_admin)])
async def refresh_hsguru_meta_matrix(
    concurrency: int = Query(2, ge=1, le=5),
) -> dict:
    from .hsguru_meta_matrix import refresh_hsguru_meta_matrix as refresh_matrix

    return await refresh_matrix(concurrency=concurrency)


@app.post(
    "/admin/refresh/hsguru-archetype-analysis",
    dependencies=[Depends(require_admin)],
)
async def refresh_hsguru_archetype_analysis(
    concurrency: int = Query(3, ge=1, le=10),
    limit: int | None = Query(None, ge=1, le=500),
) -> dict:
    from .hsguru_archetype_analysis import (
        refresh_hsguru_archetype_analysis as refresh_analysis,
    )

    return await refresh_analysis(concurrency=concurrency, limit=limit)


@app.post("/admin/capture/bg-compositions-screenshot", dependencies=[Depends(require_admin)])
async def capture_bg_compositions_screenshot() -> dict:
    from .hsreplay_bg_screenshots import capture_compositions_screenshot

    return await capture_compositions_screenshot()


@app.get(
    "/admin/datasets/{source_id}/quarantine",
    dependencies=[Depends(require_admin)],
)
def dataset_quarantine(
    source_id: str,
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    from .dataset_publication_store import (
        STANDARD_CARDS_SOURCE_ID,
        DatasetPublicationStore,
    )

    if source_id not in SOURCE_BY_ID:
        raise HTTPException(status_code=404, detail="Unknown source")
    if source_id != STANDARD_CARDS_SOURCE_ID:
        raise HTTPException(
            status_code=404,
            detail="Quarantine channel is not enabled for this source",
        )
    return {
        "source_id": source_id,
        "quarantine": DatasetPublicationStore().list_quarantine(
            source_id, limit=limit
        ),
    }


@app.post(
    "/admin/datasets/{source_id}/publication/rollback",
    dependencies=[Depends(require_admin)],
)
def rollback_dataset_publication(
    source_id: str,
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    from .dataset_publication_store import (
        STANDARD_CARDS_SOURCE_ID,
        DatasetPublicationStore,
        PublicationUnavailable,
        validate_standard_cards_snapshot,
    )
    from .refresh_log import log_action

    if source_id not in SOURCE_BY_ID:
        raise HTTPException(status_code=404, detail="Unknown source")
    if source_id != STANDARD_CARDS_SOURCE_ID:
        raise HTTPException(
            status_code=404,
            detail="Publication rollback is not enabled for this source",
        )
    requested = payload.get("datasetVersion") if isinstance(payload, dict) else None
    if requested is not None and not isinstance(requested, str):
        raise HTTPException(status_code=422, detail="datasetVersion must be a string")

    store = DatasetPublicationStore()
    previous_current: str | None = None
    target_version: str | None = None
    reconciliation = None
    committed = False
    postcommit_sync_error: str | None = None

    def audit_rejection(reason: str, target: str | None) -> str | None:
        try:
            log_action(
                "dataset.publication.rollback.reject",
                source_id=source_id,
                level="warn",
                detail=reason[:1000],
                extra={
                    "from_dataset_version": previous_current,
                    "to_dataset_version": target,
                    "actor": "admin_api_key",
                },
            )
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"[:1000]
        return None

    try:
        with store.publication_transaction(source_id):
            publication_attempt = store.begin_publication_attempt(source_id)
            previous_current = store.pointer_dataset_version(source_id)
            target_version = store.rollback_target_version(
                source_id,
                requested.strip() if isinstance(requested, str) else None,
            )
            target_dataset = store.read_version_dataset(source_id, target_version)
            decision = validate_standard_cards_snapshot(
                SOURCE_BY_ID[source_id], target_dataset
            )
            if not decision.accepted:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "Retained publication failed revalidation",
                        "source_id": source_id,
                        "reason": decision.reason,
                        "dataset_version": target_version,
                    },
                )
            store.rollback_to_version(
                source_id,
                target_version,
                validation={
                    "ok": True,
                    "reason": "admin rollback revalidation passed",
                    "diagnostics": decision.diagnostics,
                },
            )
            committed = True
            try:
                try:
                    status = load_status(source_id)
                except (OSError, UnicodeError, ValueError):
                    status = None
                status = status or {
                    "source_id": source_id,
                    "site": SOURCE_BY_ID[source_id].site,
                    "category": SOURCE_BY_ID[source_id].category,
                    "url": SOURCE_BY_ID[source_id].url,
                }
                status.update(
                    {
                        "state": SourceState.OK,
                        "fetched_at": target_dataset.get("fetched_at"),
                    }
                )
                reconciliation = store.reconcile_current_publication(
                    source_id,
                    candidate_dataset_version=target_version,
                    expected_dataset_version=target_version,
                    status=status,
                    attempt_generation=publication_attempt.generation,
                    attempt_id=publication_attempt.attempt_id,
                    attempt_started_at=publication_attempt.started_at,
                )
            except Exception as exc:
                postcommit_sync_error = f"{type(exc).__name__}: {exc}"[:1000]
    except HTTPException as exc:
        detail = exc.detail
        reason = (
            str(detail.get("reason") or detail.get("message") or "rollback rejected")
            if isinstance(detail, dict)
            else str(detail)
        )
        audit_error = audit_rejection(
            reason,
            target_version
            or (requested if isinstance(requested, str) else None),
        )
        if audit_error and isinstance(detail, dict):
            detail["audit_recorded"] = False
            detail.setdefault("warnings", []).append(audit_error)
        raise
    except (PublicationUnavailable, ValueError) as exc:
        reason = exc.reason if isinstance(exc, PublicationUnavailable) else "invalid_version"
        audit_error = audit_rejection(
            reason,
            target_version
            or (requested if isinstance(requested, str) else None),
        )
        detail = {
            "message": "Publication rollback is unavailable",
            "source_id": source_id,
            "reason": reason,
        }
        if audit_error:
            detail["audit_recorded"] = False
            detail["warnings"] = [audit_error]
        raise HTTPException(
            status_code=409,
            detail=detail,
        ) from exc

    if not committed or target_version is None:
        raise HTTPException(status_code=500, detail="Rollback did not commit")
    cache_synced = reconciliation.cache_synced if reconciliation else False
    status_synced = reconciliation.status_synced if reconciliation else False
    cache_error = (
        next(iter(reconciliation.warnings), None)
        if reconciliation and not cache_synced
        else postcommit_sync_error
    )
    status_error = (
        reconciliation.warnings[-1]
        if reconciliation and not status_synced and reconciliation.warnings
        else postcommit_sync_error if not status_synced else None
    )
    audit_recorded = True
    audit_error: str | None = None
    try:
        log_action(
            "dataset.publication.rollback",
            source_id=source_id,
            level="warn",
            detail="Administrator rolled back the Standard-card publication",
            extra={
                "from_dataset_version": previous_current,
                "to_dataset_version": target_version,
                "cache_synced": cache_synced,
                "cache_error": cache_error,
                "status_synced": status_synced,
                "status_error": status_error,
            },
        )
    except Exception as exc:
        audit_recorded = False
        audit_error = f"{type(exc).__name__}: {exc}"[:1000]
    return {
        "ok": True,
        "source_id": source_id,
        "dataset_version": (
            reconciliation.dataset_version if reconciliation else target_version
        ),
        "previous_dataset_version": previous_current,
        "cache_synced": cache_synced,
        "status_synced": status_synced,
        "audit_recorded": audit_recorded,
        "warnings": [
            warning
            for warning in (cache_error, status_error, audit_error)
            if warning
        ],
    }


@app.put("/admin/datasets/{source_id}", dependencies=[Depends(require_admin)])
def upload_dataset(
    source_id: str,
    payload: Annotated[dict[str, Any], Body()],
) -> dict:
    if source_id not in SOURCE_BY_ID:
        raise HTTPException(status_code=404, detail="Unknown source")
    source = SOURCE_BY_ID[source_id]
    fetched_at = datetime.now(UTC).isoformat()
    dataset = {
        "state": SourceState.OK,
        "source_id": source.id,
        "fetched_at": fetched_at,
        "http_status": None,
        "final_url": source.url,
        "content_length": None,
        "backend": "admin_upload",
        "data": payload,
    }
    from .refresh_log import log_action, runtime_version_info

    dataset["runtime"] = runtime_version_info()
    status = {
        "source_id": source.id,
        "site": source.site,
        "category": source.category,
        "url": source.url,
        "fetch_url": source.fetch_url,
        "fragment": source.fragment,
        "state": SourceState.OK,
        "fetched_at": fetched_at,
        "http_status": None,
        "final_url": source.url,
        "error": None,
        "detail": "Uploaded through the admin ingestion endpoint.",
        "content_length": None,
        "backend": "admin_upload",
    }
    from .dataset_publication_store import (
        STANDARD_CARDS_SOURCE_ID,
        DatasetPublicationStore,
        validate_and_publish_standard_cards_candidate,
    )

    if source_id == STANDARD_CARDS_SOURCE_ID:
        store = DatasetPublicationStore()

        def audit_upload(
            action: str,
            *,
            reason: str,
            published_version: str | None,
            cache_synced: bool,
            status_synced: bool,
        ) -> str | None:
            try:
                log_action(
                    action,
                    source_id=source_id,
                    level="info" if action.endswith(".accept") else "warn",
                    detail=reason[:1000],
                    extra={
                        "actor": "admin_api_key",
                        "candidate_dataset_version": decision.dataset_version,
                        "published_dataset_version": published_version,
                        "cache_synced": cache_synced,
                        "status_synced": status_synced,
                    },
                )
            except Exception as exc:
                return f"{type(exc).__name__}: {exc}"[:1000]
            return None

        publication_attempt = store.begin_publication_attempt(source_id)
        decision = validate_and_publish_standard_cards_candidate(
            source,
            dataset,
            store=store,
            publication_attempt=publication_attempt,
        )
        if not decision.accepted:
            status["state"] = (
                SourceState.PARTIAL
                if decision.rejection_kind == "regression"
                else SourceState.QUALITY_ERROR
            )
            status["detail"] = decision.reason
            rejection_cache_synced = False
            rejection_status_synced = False
            published_version: str | None = None
            try:
                reconciliation = store.reconcile_current_publication(
                    source_id,
                    candidate_dataset_version=decision.dataset_version,
                    expected_dataset_version=None,
                    status=status,
                    attempt_generation=publication_attempt.generation,
                    attempt_id=publication_attempt.attempt_id,
                    attempt_started_at=publication_attempt.started_at,
                )
                rejection_cache_synced = reconciliation.cache_synced
                rejection_status_synced = reconciliation.status_synced
                published_version = reconciliation.dataset_version
            except Exception:
                status["candidate_dataset_version"] = decision.dataset_version
                status["published_dataset_version"] = None
                status.pop("dataset_version", None)
                status = store.record_status_without_publication(
                    source_id,
                    status=status,
                    attempt_generation=publication_attempt.generation,
                    attempt_id=publication_attempt.attempt_id,
                    attempt_started_at=publication_attempt.started_at,
                )
            audit_error = audit_upload(
                "dataset.publication.admin_upload.reject",
                reason=decision.reason,
                published_version=published_version,
                cache_synced=rejection_cache_synced,
                status_synced=rejection_status_synced,
            )
            detail = {
                "message": "Dataset candidate rejected",
                "source_id": source_id,
                "reason": decision.reason,
                "dataset_version": decision.dataset_version,
                "rejection_kind": decision.rejection_kind,
                "audit_recorded": audit_error is None,
            }
            if audit_error:
                detail["warnings"] = [audit_error]
            raise HTTPException(
                status_code=422,
                detail=detail,
            )
        try:
            reconciliation = store.reconcile_current_publication(
                source_id,
                candidate_dataset_version=decision.dataset_version,
                expected_dataset_version=decision.dataset_version,
                status=status,
                attempt_generation=publication_attempt.generation,
                attempt_id=publication_attempt.attempt_id,
                attempt_started_at=publication_attempt.started_at,
            )
        except Exception as exc:
            sync_error = f"{type(exc).__name__}: {exc}"[:1000]
            try:
                authoritative_version = (
                    store.pointer_dataset_version(source_id)
                    or decision.dataset_version
                )
            except Exception:
                authoritative_version = decision.dataset_version
            try:
                log_action(
                    "dataset.publication.sync.fail",
                    source_id=source_id,
                    level="error",
                    detail=sync_error,
                    extra={
                        "candidate_dataset_version": decision.dataset_version,
                        "published_dataset_version": authoritative_version,
                        "operation": "admin_upload",
                    },
                )
            except Exception:
                pass
            audit_error = audit_upload(
                "dataset.publication.admin_upload.accept",
                reason="published; mutable reconciliation degraded",
                published_version=authoritative_version,
                cache_synced=False,
                status_synced=False,
            )
            return {
                "ok": True,
                "degraded": True,
                "source_id": source_id,
                "fetched_at": fetched_at,
                "candidate_dataset_version": decision.dataset_version,
                "dataset_version": authoritative_version,
                "publication_superseded": (
                    authoritative_version != decision.dataset_version
                ),
                "cache_synced": False,
                "status_synced": False,
                "audit_recorded": audit_error is None,
                "warnings": [
                    warning for warning in (sync_error, audit_error) if warning
                ],
            }
        audit_error = audit_upload(
            "dataset.publication.admin_upload.accept",
            reason="published and reconciled",
            published_version=reconciliation.dataset_version,
            cache_synced=reconciliation.cache_synced,
            status_synced=reconciliation.status_synced,
        )
        return {
            "ok": True,
            "source_id": source_id,
            "fetched_at": fetched_at,
            "candidate_dataset_version": decision.dataset_version,
            "dataset_version": reconciliation.dataset_version,
            "publication_superseded": reconciliation.superseded,
            "cache_synced": reconciliation.cache_synced,
            "status_synced": reconciliation.status_synced,
            "audit_recorded": audit_error is None,
            "warnings": [
                *reconciliation.warnings,
                *([audit_error] if audit_error else []),
            ],
        }
    save_dataset(source_id, dataset)
    save_status(source_id, status)
    return {"ok": True, "source_id": source_id, "fetched_at": fetched_at}


@app.get("/api/db/decks")
def db_decks(
    class_name: str | None = Query(None, min_length=1, max_length=80),
    format_name: str | None = Query(None, min_length=1, max_length=80),
    source_id: str | None = Query(None, min_length=1, max_length=120),
    min_win_rate: float | None = None,
    q: str | None = Query(None, min_length=1, max_length=120),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
) -> dict:
    from .db import get_db_connection
    conn = get_db_connection()
    try:
        query = "SELECT * FROM decks WHERE 1=1"
        params = []
        if class_name:
            query += " AND LOWER(class) = LOWER(?)"
            params.append(class_name)
        if format_name:
            query += " AND LOWER(format) = LOWER(?)"
            params.append(format_name)
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        else:
            query += " AND source_id != ?"
            params.append("hsreplay_arena_winning_decks")
        if min_win_rate is not None:
            query += " AND win_rate >= ?"
            params.append(min_win_rate)
        if q:
            query += " AND (title LIKE ? OR archetype LIKE ? OR deck_code LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])

        # Count total
        count_query = query.replace("SELECT *", "SELECT COUNT(*)", 1)
        total = conn.execute(count_query, params).fetchone()[0]

        # Fetch page (sorted by updated_at or win_rate)
        query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        decks = [dict(row) for row in rows]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "decks": decks
        }
    except Exception:
        raise HTTPException(status_code=500, detail="Database query failed")
    finally:
        conn.close()


@app.get("/api/db/archetypes")
def db_archetypes(
    class_name: str | None = Query(None, min_length=1, max_length=80),
    q: str | None = Query(None, min_length=1, max_length=120),
    rank_range: str = Query("LEGEND", min_length=1, max_length=80),
    game_type: str = Query("RANKED_STANDARD", min_length=1, max_length=80),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10000),
) -> dict:
    from .hsreplay_archetypes_db import latest_run, list_archetype_snapshots

    return {
        "latest_run": latest_run(),
        **list_archetype_snapshots(
            rank_range=rank_range,
            game_type=game_type,
            class_name=class_name,
            q=q,
            limit=limit,
            offset=offset,
        ),
    }


@app.get("/api/db/bg/minions")
def db_bg_minions(
    q: str | None = Query(None, min_length=1, max_length=120),
    tavern_tier: int | None = Query(None, ge=1, le=7),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10000),
) -> dict:
    from .hsreplay_bg_minions_db import latest_run, list_minion_snapshots

    return {
        "latest_run": latest_run(),
        **list_minion_snapshots(q=q, tavern_tier=tavern_tier, limit=limit, offset=offset),
    }


@app.get("/api/db/bg/minions/{dbf_id}")
def db_bg_minion_detail(dbf_id: int) -> dict:
    from .hsreplay_bg_minions_db import get_minion_detail

    payload = get_minion_detail(dbf_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Battlegrounds minion snapshot not found")
    return payload


@app.get("/api/db/bg/minions/{dbf_id}/history")
def db_bg_minion_history(
    dbf_id: int,
    limit: int = Query(120, ge=1, le=1000),
) -> dict:
    from .hsreplay_bg_minions_db import get_minion_history

    payload = get_minion_history(dbf_id, limit=limit)
    if payload is None:
        raise HTTPException(status_code=404, detail="Battlegrounds minion not found")
    return payload


@app.get("/api/bg/heroes")
async def api_bg_heroes(
    mode: str = Query("solo", pattern="^(solo|duos)$"),
    q: str | None = Query(None, min_length=1, max_length=120),
    mmr: str = Query("TOP_50_PERCENT", pattern="^(TOP_50_PERCENT|TOP_20_PERCENT|TOP_5_PERCENT|TOP_1_PERCENT)$"),
) -> dict:
    from .hsreplay_bg_hero_details import BG_MMR, fetch_hero_index, list_bg_heroes

    # The stored full detail snapshot is Top 50%. Other MMR ranges are
    # lightweight live index slices, cached by the protected HSReplay client.
    if mmr != BG_MMR:
        payload = await fetch_hero_index(duos=mode == "duos", mmr=mmr)
        rows = payload.get("heroes") or []
        if q:
            needle = q.lower()
            rows = [row for row in rows if needle in str(row.get("hero") or "").lower()]
        return {
            "type": "bg_heroes",
            "mode": mode,
            "count": len(rows),
            "fetched_at": datetime.now(UTC).isoformat(),
            "filters": payload.get("filters"),
            "source": payload.get("source"),
            "heroes": rows,
        }
    return list_bg_heroes(mode=mode, q=q)


@app.get("/api/bg/heroes/duos")
def api_bg_heroes_duos(q: str | None = Query(None, min_length=1, max_length=120)) -> dict:
    from .hsreplay_bg_hero_details import list_bg_heroes

    return list_bg_heroes(mode="duos", q=q)


@app.get("/api/bg/heroes/{dbf_id}")
def api_bg_hero_detail(dbf_id: int) -> dict:
    from .hsreplay_bg_hero_details import get_bg_hero

    payload = get_bg_hero(dbf_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Battlegrounds hero not found")
    return payload


@app.get("/api/bg/heroes/{dbf_id}/tavern-up")
def api_bg_hero_tavern_up(dbf_id: int) -> dict:
    from .hsreplay_bg_hero_details import get_bg_hero

    payload = get_bg_hero(dbf_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Battlegrounds hero not found")
    return {
        "hero": payload.get("hero"),
        "filters": payload.get("filters"),
        "as_of": (payload.get("as_of") or {}).get("tavern_up") if isinstance(payload.get("as_of"), dict) else None,
        "tavern_up": payload.get("tavern_up") or [],
        "tavern_up_by_turn": payload.get("tavern_up_by_turn") or [],
    }


@app.get("/api/bg/heroes/{dbf_id}/hero-power")
def api_bg_hero_power(dbf_id: int) -> dict:
    from .hsreplay_bg_hero_details import get_bg_hero

    payload = get_bg_hero(dbf_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Battlegrounds hero not found")
    return {
        "hero": payload.get("hero"),
        "filters": payload.get("filters"),
        "as_of": (payload.get("as_of") or {}).get("hero_power") if isinstance(payload.get("as_of"), dict) else None,
        "hero_power": payload.get("hero_power") or [],
        "hero_power_by_turn": payload.get("hero_power_by_turn") or [],
    }


@app.get("/api/bg/heroes/{dbf_id}/best-composition")
def api_bg_hero_best_composition(dbf_id: int) -> dict:
    from .hsreplay_bg_hero_details import get_bg_hero

    payload = get_bg_hero(dbf_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Battlegrounds hero not found")
    return {
        "hero": payload.get("hero"),
        "filters": payload.get("filters"),
        "as_of": payload.get("as_of"),
        "best_composition": payload.get("best_composition"),
        "compositions": payload.get("compositions") or [],
    }


@app.get("/api/patches")
def api_patches(
    q: str | None = Query(None, min_length=1, max_length=120),
    match_state: str | None = Query(None, pattern="^(matched|missing_manacost)$"),
    include_content: bool = Query(False),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10000),
) -> dict:
    from .patches_db import list_patches

    return list_patches(
        q=q,
        match_state=match_state,
        include_content=include_content,
        limit=limit,
        offset=offset,
    )


@app.get("/api/patches/{version}")
def api_patch_detail(
    version: str,
    include_content: bool = Query(True),
) -> dict:
    from .patches_db import get_patch

    payload = get_patch(version, include_content=include_content)
    if payload is None:
        raise HTTPException(status_code=404, detail="Patch not found")
    return payload


@app.get("/api/bg/compositions/screenshot/latest")
def bg_compositions_latest_screenshot() -> dict:
    from .hsreplay_bg_screenshots import latest_compositions_screenshot

    payload = latest_compositions_screenshot()
    if payload is None:
        raise HTTPException(status_code=404, detail="No Battlegrounds compositions screenshot captured yet")
    return payload


@app.get("/api/bg/compositions/screenshot/latest/image")
def bg_compositions_latest_screenshot_image() -> FileResponse:
    from .hsreplay_bg_screenshots import latest_compositions_screenshot

    payload = latest_compositions_screenshot()
    if payload is None or not payload.get("image_path"):
        raise HTTPException(status_code=404, detail="No Battlegrounds compositions screenshot image captured yet")
    path = Path(str(payload["image_path"]))
    if not path.exists():
        raise HTTPException(status_code=404, detail="Screenshot image file is missing")
    return FileResponse(path)


@app.get("/api/db/archetypes/{archetype_id}")
def db_archetype_detail(
    archetype_id: int,
    rank_range: str = Query("LEGEND", min_length=1, max_length=80),
    game_type: str = Query("RANKED_STANDARD", min_length=1, max_length=80),
) -> dict:
    from .hsreplay_archetypes_db import get_archetype_detail

    payload = get_archetype_detail(archetype_id, rank_range=rank_range, game_type=game_type)
    if payload is None:
        raise HTTPException(status_code=404, detail="Archetype snapshot not found")
    return payload


@app.get("/api/db/archetypes/{archetype_id}/mulligan")
def db_archetype_mulligan(
    archetype_id: int,
    rank_range: str = Query("LEGEND", min_length=1, max_length=80),
    game_type: str = Query("RANKED_STANDARD", min_length=1, max_length=80),
    display_only: bool = Query(True),
    limit: int = Query(40, ge=1, le=250),
) -> dict:
    from .db import get_db_connection
    from .hsreplay_archetypes_db import get_latest_archetype_snapshot

    snapshot = get_latest_archetype_snapshot(archetype_id, rank_range=rank_range, game_type=game_type)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Archetype snapshot not found")
    conn = get_db_connection()
    try:
        query = "SELECT * FROM archetype_mulligan WHERE snapshot_id = ?"
        params: list[Any] = [snapshot["id"]]
        if display_only:
            query += " AND display_row = 1"
        query += " ORDER BY hsreplay_rank ASC LIMIT ?"
        params.append(limit)
        rows = [dict(row) for row in conn.execute(query, params).fetchall()]
        return {"snapshot": snapshot, "mulligan": rows}
    finally:
        conn.close()


@app.get("/api/db/archetypes/{archetype_id}/matchups")
def db_archetype_matchups(
    archetype_id: int,
    rank_range: str = Query("LEGEND", min_length=1, max_length=80),
    game_type: str = Query("RANKED_STANDARD", min_length=1, max_length=80),
    min_games: int = Query(0, ge=0, le=1000000),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    from .db import get_db_connection
    from .hsreplay_archetypes_db import get_latest_archetype_snapshot

    snapshot = get_latest_archetype_snapshot(archetype_id, rank_range=rank_range, game_type=game_type)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Archetype snapshot not found")
    conn = get_db_connection()
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM archetype_matchups
                WHERE snapshot_id = ? AND COALESCE(total_games, 0) >= ?
                ORDER BY total_games DESC
                LIMIT ?
                """,
                (snapshot["id"], min_games, limit),
            ).fetchall()
        ]
        return {"snapshot": snapshot, "matchups": rows}
    finally:
        conn.close()


@app.get("/api/db/archetypes/{archetype_id}/decks")
def db_archetype_decks(
    archetype_id: int,
    rank_range: str = Query("LEGEND", min_length=1, max_length=80),
    game_type: str = Query("RANKED_STANDARD", min_length=1, max_length=80),
    include_cards: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    from .db import get_db_connection
    from .hsreplay_archetypes_db import (
        get_archetype_deck_cards,
        get_latest_archetype_snapshot,
    )

    snapshot = get_latest_archetype_snapshot(archetype_id, rank_range=rank_range, game_type=game_type)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Archetype snapshot not found")
    conn = get_db_connection()
    try:
        decks = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM archetype_decks
                WHERE snapshot_id = ?
                ORDER BY total_games DESC
                LIMIT ?
                """,
                (snapshot["id"], limit),
            ).fetchall()
        ]
        if include_cards:
            for deck in decks:
                deck["cards"] = get_archetype_deck_cards(int(deck["id"]))
        return {"snapshot": snapshot, "decks": decks}
    finally:
        conn.close()


@app.get("/api/db/archetypes/{archetype_id}/history")
def db_archetype_history(
    archetype_id: int,
    rank_range: str = Query("LEGEND", min_length=1, max_length=80),
    game_type: str = Query("RANKED_STANDARD", min_length=1, max_length=80),
) -> dict:
    from .db import get_db_connection
    from .hsreplay_archetypes_db import get_latest_archetype_snapshot

    snapshot = get_latest_archetype_snapshot(archetype_id, rank_range=rank_range, game_type=game_type)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Archetype snapshot not found")
    conn = get_db_connection()
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT series_name, point_date, value
                FROM archetype_time_series
                WHERE snapshot_id = ?
                ORDER BY series_name, point_date
                """,
                (snapshot["id"],),
            ).fetchall()
        ]
        return {"snapshot": snapshot, "history": rows}
    finally:
        conn.close()


@app.get("/api/db/cards/trends")
def db_card_trends(
    card_name: str = Query(..., min_length=1, max_length=120),
    source_id: str | None = Query(None, min_length=1, max_length=120),
    class_name: str | None = Query(None, min_length=1, max_length=80),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    from .db import get_db_connection
    conn = get_db_connection()
    try:
        query = "SELECT * FROM card_popularity_history WHERE card_name = ?"
        params = [card_name]
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        if class_name:
            query += " AND class = ?"
            params.append(class_name)

        query += " ORDER BY recorded_at ASC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        trends = [dict(row) for row in rows]
        return {
            "card_name": card_name,
            "trends": trends
        }
    except Exception:
        raise HTTPException(status_code=500, detail="Database query failed")
    finally:
        conn.close()
