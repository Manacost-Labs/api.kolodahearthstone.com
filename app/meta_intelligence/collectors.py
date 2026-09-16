from __future__ import annotations

import asyncio
import fcntl
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from ..firecrawl_backend import scrape_source_with_options
from ..config import build_id
from ..hsguru_archetype_analysis import parse_card_stats_html, parse_class_matchups_html
from .parsers import parse_decks_html, parse_meta_html
from ..parser import parse_html
from ..source_validators import validate_structured
from ..source_state import SourceState
from ..sources import Source
from ..storage import load_dataset, load_status, save_dataset, save_status
from .budget import BudgetExceeded, acquisition_scope, allowed, summary, validate_attempt
from .models import Settings, canonical, digest, now_iso, timestamp
from .selectors import Job, detail_jobs, hot_jobs
from .service import archetypes, scopes
from .signals import recompute
from .snapshots import ingest
from .store import Store


def enqueue_details(store: Store, patch: str, settings: Settings) -> int:
    """Strong early/confirmed popularity evidence can prioritize bounded research."""
    count = 0
    with store.connect(write=True) as conn:
        signals = conn.execute("SELECT * FROM signals WHERE state IN ('early','confirmed') AND kind='popularity_rise' AND rank='legend' AND period='past_week' AND patch=?", (patch,)).fetchall()
        for signal in signals:
            evidence = json.loads(signal["evidence"])
            popularity = evidence.get("values", {}).get("popularity", [])
            if len(popularity)<2 or popularity[-1]-popularity[0] < 2*settings.popularity_pp:
                continue
            job = Job("details",signal["format"],"legend",archetype=signal["archetype"])
            latest = conn.execute("SELECT fetched_at FROM snapshots WHERE source_id=? AND patch=? ORDER BY fetched_at DESC LIMIT 1", (job.source_id,patch)).fetchone()
            if latest and (datetime.now(UTC)-datetime.fromisoformat(latest[0])).total_seconds()<86400:
                continue
            identity = digest(["event-details",signal["id"],evidence["snapshot_ids"][-1]])
            inserted = conn.execute("INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?,?)", (identity,"event_details",canonical({**asdict(job),"patch":patch,"signal_id":signal["id"]}),now_iso(),None,"queued"))
            count += inserted.rowcount
    return count


async def collect_events(store: Store, patch: str, settings: Settings) -> list[dict]:
    with store.connect() as conn:
        queued = conn.execute("SELECT * FROM jobs WHERE kind='event_details' AND state='queued' ORDER BY created_at LIMIT ?", (settings.event_limit,)).fetchall()
    results = []
    for entry in queued:
        if not allowed("detail",summary(store,settings)["budget_exposure"],settings):
            break
        payload = json.loads(entry["payload"])
        event_patch = payload.pop("patch")
        signal_id = payload.pop("signal_id")
        job = Job(**payload)
        with store.connect() as conn:
            signal = conn.execute("SELECT state,evidence FROM signals WHERE id=?",(signal_id,)).fetchone()
        latest = store.snapshots(source_id=job.source_id,limit=1)
        recent = bool(latest and latest[0]["patch"] == patch and (datetime.now(UTC)-datetime.fromisoformat(latest[0]["fetched_at"])).total_seconds()<86400)
        if event_patch != patch or recent or not signal or signal["state"] not in {"early","confirmed"}:
            with store.connect(write=True) as conn:
                conn.execute("UPDATE jobs SET state='obsolete',finished_at=? WHERE id=?",(now_iso(),entry["id"]))
            continue
        with store.connect(write=True) as conn:
            claimed = conn.execute("UPDATE jobs SET state='running' WHERE id=? AND state='queued'",(entry["id"],)).rowcount
        if not claimed:
            continue
        result = await collect_job(job,patch,store,settings)
        results.append(result)
        with store.connect(write=True) as conn:
            conn.execute("UPDATE jobs SET state=?,finished_at=? WHERE id=?",(result["state"],now_iso(),entry["id"]))
    return results


def next_deck_page(html: str, current_url: str) -> int | None:
    """Follow only an explicit next-page link, on the same HSGuru listing."""
    soup = BeautifulSoup(html, "lxml")
    link = soup.select_one('a[rel~="next"], a[aria-label="Next"], a[aria-label="Next page"]')
    if not link or not link.get("href"):
        return None
    target = urlparse(urljoin(current_url,str(link["href"])))
    original = urlparse(current_url)
    if target.scheme != "https" or target.hostname != original.hostname or target.path != "/decks":
        return None
    query, context = parse_qs(target.query), parse_qs(original.query)
    for name in ("format", "rank", "period", "player_deck_archetype[]"):
        if name in query and query[name] != context.get(name):
            return None
    value = query.get("page", [""])[0]
    return int(value) if value.isdigit() and 0 < int(value) <= 1000 else None


async def collect_job(job: Job, patch: str, store: Store, settings: Settings, *, fetch=scrape_source_with_options) -> dict:
    """Separate published Lab datasets; no changes to existing matrix schedules/feeds."""
    if not allowed(job.job_class, summary(store, settings)["budget_exposure"], settings):
        return {"source_id": job.source_id, "state": "budget_skipped"}
    acquired_at = now_iso()
    fmt = {"standard": 2, "wild": 1}[job.format]
    criteria = {"format": job.format, "rank": job.rank, "period": job.period, "patch": patch}
    # Labeling an acquisition with today's patch does not prove that a rolling
    # seven-day sample excludes games from the preceding patch.
    patch_start = os.getenv("HS_META_LAB_PATCH_STARTED_AT", "")
    verified_window = False
    if patch_start and patch == os.getenv("HS_META_LAB_PATCH"):
        verified_window = (timestamp(acquired_at)-timestamp(patch_start)).total_seconds() >= 7*86400
    criteria["patch_scope"] = "single_patch" if job.period == patch or verified_window else "unverified"
    params = {"format": fmt, "rank": job.rank, "period": job.period}
    attempt_ids = []
    backends = set()

    async def page(path: str, extra: dict, parser):
        if not allowed(job.job_class, summary(store, settings)["budget_exposure"], settings):
            raise BudgetExceeded("Lab detail collection throttled")
        source = Source(job.source_id, "https://www.hsguru.com" + path + "?" + urlencode({**params, **extra}), "hsguru", "matchups" if job.kind == "matchups" else "meta", kind="pipeline")
        with acquisition_scope(job.source_id, job_class=job.job_class):
            result = await fetch(source, formats=["html"], only_main_content=False, max_age_ms=0, wait_ms=5000, timeout_ms=60000)
        identity = result.metadata.get("labAcquisitionId")
        try:
            if str(result.metadata.get("cacheState", "")).lower() in {"cached", "cache", "hit", "stale"}:
                raise ValueError("Cached provider evidence is not a fresh acquisition")
            rows = parser(result.html, source)
            if not rows:
                raise ValueError("No complete rows on HSGuru page")
        except Exception:
            validate_attempt(identity, False, store)
            raise
        validate_attempt(identity, True, store)
        if identity:
            attempt_ids.append(identity)
        backends.add(result.backend)
        return rows

    try:
        if job.kind == "meta":
            rows = await page("/meta", {"min_games": 100}, lambda html, src: parse_meta_html(html, format_name=job.format, format_id=fmt, period=job.period, rank=job.rank, minimum_games=100, source_url=src.url))
            structured = {"type": "meta", "strategies": rows}
        elif job.kind == "matchups":
            rows = await page("/matchups", {"min_archetype_sample": 100, "min_matchup_sample": 25}, lambda html, src: (parse_html(src, html).get("structured") or {}).get("matchups", []))
            structured = {"type": "matchups", "matchups": rows}
        else:
            cards = await page("/card-stats", {"archetype": job.archetype, "show_counts": "yes"}, lambda html, _: parse_card_stats_html(html))
            matchups = await page("/archetype/" + quote(job.archetype or "", safe=""), {}, lambda html, _: parse_class_matchups_html(html))
            # Revisit listing statistics daily; deck codes already encode composition,
            # so no paid request for every already-known individual deck is needed.
            decks, visited, page_number = {}, set(), 1
            while page_number and page_number not in visited and len(visited) < settings.deck_pages:
                visited.add(page_number)
                listing = await page("/decks", {"player_deck_archetype[]": job.archetype, "min_games": 50, "page": page_number}, lambda html, src: {
                    "rows": parse_decks_html(html, archetype=job.archetype or "", format_name=job.format, fetched_at=acquired_at, limit=None),
                    "next": next_deck_page(html,src.url),
                })
                if not listing["rows"]:
                    raise ValueError("Empty deck listing page")
                for deck in listing["rows"]:
                    decks[deck.get("deck_code") or deck["url"]] = deck
                page_number = listing["next"]
            structured = {"type": "meta_lab_details", "deck_coverage": "page_limit" if page_number else "available_listing", "listing_pages": len(visited), "archetypes": [{
                "archetype": job.archetype, "format": job.format, "decks": list(decks.values()), "card_stats": cards,
                "class_matchups": matchups, "card_stats_updated_at": acquired_at,
                "matchups_updated_at": acquired_at, "state": "ok",
            }]}
        structured.update(criteria=criteria, expected_hours=24 if job.kind == "details" else 4, schema_version=1,
                          parser_version=f"hsguru/{build_id() or 'unversioned'}:lab-normalizer-v1")
        validation = validate_structured(job.source_id, structured)
        if not validation.ok:
            raise ValueError("Published source validation rejected Lab candidate")
        dataset = {"state": "ok", "fetched_at": acquired_at, "patch": patch, "backend": "+".join(sorted(backends)),
                   "data": {"structured": structured, "acquisition": {"attempt_ids": attempt_ids}}}
        status = {"state": "ok", "fetched_at": acquired_at, "source_id": job.source_id}
        admitted = ingest(store, job.source_id, dataset, status, settings=settings, dry_run=True)
        if admitted["rejected"]:
            raise ValueError("Lab completeness rejected candidate")
        save_dataset(job.source_id, dataset)
        save_status(job.source_id, status)
        admitted = ingest(store, job.source_id, dataset, status, settings=settings)
        return {"source_id": job.source_id, "state": "ok", "snapshots": admitted["snapshots"]}
    except Exception as exc:
        # Persist a failure observation without storing provider exception text/URLs.
        status = {"state": SourceState.FETCH_ERROR, "fetched_at": acquired_at, "source_id": job.source_id, "reason": type(exc).__name__}
        previous = load_dataset(job.source_id)
        if previous:
            ingest(store, job.source_id, previous, status, settings=settings)
        save_status(job.source_id, status)
        return {"source_id": job.source_id, "state": "budget_skipped" if isinstance(exc, BudgetExceeded) else "failed", "reason": type(exc).__name__}


async def run(mode: str, patch: str, store: Store | None = None) -> dict:
    settings, store = Settings.from_env(), store or Store()
    if not settings.enabled:
        raise ValueError("Set HS_META_LAB_ENABLED=1 in the service environment")
    if not patch or patch == "unknown":
        raise ValueError("An explicit verified patch is required for collection")
    with store.connect(write=True):
        pass
    with (store.path.parent / "collector.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "already_running", "results": []}
        spent = summary(store, settings)["budget_exposure"]
        if not summary(store,settings)["noncritical_collection_ready"]:
            raise ValueError("Record verified opening monthly credits before scheduling Lab collection")
        if mode == "hot":
            jobs = hot_jobs(spent, settings)
        else:
            candidates = []
            for scope in scopes(store):
                if scope["rank"] == "legend" and scope["period"] == "past_week" and scope["patch"] == patch:
                    selected = {k: scope[k] for k in ("format", "rank", "period", "patch")}
                    candidates.extend({**r, "format": scope["format"]} for r in archetypes(store, **selected) if r["freshness"]["fresh"])
            jobs = detail_jobs(candidates, spent, settings)
        results = []
        now = datetime.now(UTC)
        slot = now.strftime("%Y-%m-%d") + (f":{now.hour//4}" if mode == "hot" else "")
        for job in jobs:
            if job.kind == "details":
                recent = store.snapshots(source_id=job.source_id,limit=1)
                if recent and recent[0]["patch"] == patch and (now-datetime.fromisoformat(recent[0]["fetched_at"])).total_seconds()<86400:
                    continue
            identity = digest([asdict(job), slot, patch])
            with store.connect(write=True) as conn:
                previous = conn.execute("SELECT state FROM jobs WHERE id=?", (identity,)).fetchone()
                if previous:
                    continue
                conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (identity,job.kind,canonical(asdict(job)),now_iso(),None,"running"))
            result = await collect_job(job, patch, store, settings)
            results.append(result)
            with store.connect(write=True) as conn:
                conn.execute("UPDATE jobs SET state=?,finished_at=? WHERE id=?", (result["state"],now_iso(),identity))
        recompute(store, settings)
        if mode == "hot":
            enqueue_details(store,patch,settings)
            results.extend(await collect_events(store,patch,settings))
            recompute(store,settings)
        return {"state": "ok" if all(r["state"] == "ok" for r in results) else "partial", "results": results}


def sync_existing(store: Store) -> dict:
    from ..storage import root_dir
    from .publications import catalog_publication
    results = []
    for path in sorted((root_dir() / "datasets").glob("hsguru*.json")):
        source = path.stem
        dataset, status = load_dataset(source), load_status(source)
        catalog = catalog_publication(dataset) if dataset else None
        if catalog:
            dataset, status = catalog
        if dataset and status:
            results.append({"source_id": source, **ingest(store, source, dataset, status)})
    recompute(store)
    return {"sources": results}
