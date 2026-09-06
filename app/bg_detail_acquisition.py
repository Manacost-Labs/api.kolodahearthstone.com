"""Opt-in BG detail repair: explicit queue, no publication or scheduled jobs."""

import asyncio
import math
import re
import time

from bs4 import BeautifulSoup, NavigableString

from .acquisition_coverage import ListingEvidence, SourceView, coverage_report
from .acquisition_detail_queue import DetailQueue
from .battlegrounds_comps_parse import parse_hsreplay_comp_detail_markdown
from .firecrawl_backend import _html_to_markdown
from .scrape_do_backend import (
    ScrapeDoAccountError,
    ScrapeDoContentError,
    ScrapeDoRequestError,
    ScrapeDoScrape,
    scrape_url,
)
from .scrapers.http_resilience import is_session_blocked
from .source_state import SourceState
from .sources import SOURCE_BY_ID

_COMP_URL = re.compile(
    r"https://hsreplay\.net/battlegrounds/comps/([1-9][0-9]*)/[A-Za-z0-9_-]+/?"
)


def _view(view: SourceView) -> None:
    expected = SOURCE_BY_ID["hsreplay_battlegrounds_comps"]
    if view.source.id != expected.id or view.source.url != expected.url:
        raise ValueError("unsupported BG acquisition view")


def _identity(entity_id: object, url: object) -> int:
    match = _COMP_URL.fullmatch(url) if isinstance(url, str) else None
    if match is None or entity_id != f"hsreplay-{match.group(1)}":
        raise ValueError("invalid BG entity URL identity")
    return int(match.group(1))


def _usable(detail: dict) -> bool:
    cards = detail.get("main_cards")
    return (
        isinstance(cards, list)
        and 0 < len(cards) <= 100
        and all(
            isinstance(card, dict)
            and isinstance(card.get("name"), str)
            and bool(card["name"].strip())
            and isinstance(card.get("card_id"), str)
            and re.fullmatch(r"[A-Za-z0-9_]{1,128}", card["card_id"])
            for card in cards
        )
        and isinstance(detail.get("how_to_play"), str)
        and bool(detail["how_to_play"].strip())
    )


def _detail_markdown(html: str) -> str:
    # The generic converter drops images, but BG cards are encoded in their
    # art URLs. Preserve only the known public image format before conversion.
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all("img"):
        src = str(node.get("src") or "")
        if re.fullmatch(
            r"https://art\.hearthstonejson\.com/v1/(?:bgs/latest/[A-Za-z_]+/)?256x/[A-Za-z0-9_]+\.(?:png|webp)",
            src,
        ):
            name = re.sub(r"[\[\]()\r\n]", " ", str(node.get("alt") or "")).strip()[
                :200
            ]
            node.replace_with(NavigableString(f"![{name}]({src})"))
    return _html_to_markdown(str(soup))


def _rows(view: SourceView, rows: list[dict]) -> None:
    _view(view)
    if not isinstance(rows, list) or len(rows) > 10_000:
        raise ValueError("invalid BG listing")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("invalid BG listing row")
        comp_id = _identity(row.get("id"), row.get("url"))
        if type(row.get("comp_id")) is not int or row["comp_id"] != comp_id:
            raise ValueError("invalid BG composition identity")
        if row["id"] in seen:
            raise ValueError("duplicate BG listing row")
        seen.add(row["id"])


def prepare_bg_details(queue: DetailQueue, view: SourceView, rows: list[dict]) -> int:
    _rows(view, rows)  # Validate entire batch before creating a single obligation.
    return queue.enqueue(
        view.scope_id, [(row["id"], row["url"]) for row in rows if not _usable(row)]
    )


def bg_detail_report(
    queue: DetailQueue,
    view: SourceView,
    rows: list[dict],
    *,
    listing: ListingEvidence | None = None,
) -> dict:
    _rows(view, rows)
    jobs = {job["entity_id"]: job for job in queue.items(view.scope_id)}
    if set(jobs) - {row["id"] for row in rows}:
        raise ValueError("BG listing changed within snapshot")
    states = {}
    merged = []
    for row in rows:
        job = jobs.get(row["id"])
        payload = job.get("payload") if job else None
        status = (
            "succeeded" if _usable(row) else job["status"] if job else "not_requested"
        )
        if (
            status == "succeeded"
            and not _usable(row)
            and not (isinstance(payload, dict) and _usable(payload))
        ):
            status = "failed"
        states[row["id"]] = status
        detail = (
            payload
            if status == "succeeded" and isinstance(payload, dict) and _usable(payload)
            else {}
        )
        merged.append(
            {
                **row,
                **detail,
                "id": row["id"],
                "comp_id": row["comp_id"],
                "url": row["url"],
                "tier": row.get("tier") or detail.get("tier"),
                "detail_status": status,
            }
        )
    evidence = listing or ListingEvidence(
        view.scope_id, tuple(row["id"] for row in rows)
    )
    if set(evidence.entity_ids) != {row["id"] for row in rows}:
        raise ValueError("listing evidence does not match BG rows")
    return {
        "rows": merged,
        "coverage": coverage_report(view, listing=evidence, detail_states=states),
        "credits_spent": sum(job["credits_spent"] for job in jobs.values()),
        "unknown_cost_attempts": sum(
            job["unknown_cost_attempts"] for job in jobs.values()
        ),
    }


async def drain_bg_details(
    queue: DetailQueue,
    view: SourceView,
    *,
    max_requests: int = 5,
    max_seconds: float = 60,
) -> dict:
    """One Scrape.do call per claimed item; no cascade or immediate retry.

    A cancelled urllib worker thread may still finish upstream: its cost remains
    unknown and its lease/backoff prevents immediate duplicate dispatch.
    """
    _view(view)
    if type(max_requests) is not int or not 0 <= max_requests <= 100:
        raise ValueError("invalid request budget")
    if (
        type(max_seconds) not in (int, float)
        or not math.isfinite(max_seconds)
        or not 5 <= max_seconds <= 120
    ):
        raise ValueError("invalid time budget")
    deadline = time.monotonic() + max_seconds
    requests = 0
    stop_reason = "max_requests"
    while requests < max_requests:
        remaining = deadline - time.monotonic()
        if remaining < 5:  # Scrape.do's minimum configured request timeout.
            stop_reason = "max_seconds"
            break
        claim = queue.claim(view.scope_id, lease_seconds=max_seconds + 180)
        if claim is None:
            stop_reason = "no_due_jobs"
            break
        try:
            _identity(claim.entity_id, claim.url)
        except ValueError:
            queue.finish(
                claim, status="failed", error_code="invalid_target", request_cost=0
            )
            stop_reason = "invalid_target"
            break
        cost = None
        payload = None
        error_code = None
        status = "retry"
        pause = False
        requests += 1
        try:
            scraped = await asyncio.wait_for(
                scrape_url(
                    claim.url,
                    render=True,
                    super_proxy=False,
                    timeout_ms=int(min(remaining, 60) * 1000),
                ),
                timeout=remaining,
            )
            if not isinstance(scraped, ScrapeDoScrape):
                raise TypeError("invalid provider result")
            cost = (
                scraped.request_cost
                if type(scraped.request_cost) is int and scraped.request_cost >= 0
                else None
            )
            if scraped.final_url.rstrip("/") != claim.url.rstrip("/"):
                error_code = "response_scope_mismatch"
            elif (
                is_session_blocked(scraped.status_code, scraped.html)
                or scraped.status_code != 200
            ):
                error_code = "blocked_response"
            else:
                markdown = _detail_markdown(scraped.html)
                detail = parse_hsreplay_comp_detail_markdown(markdown, url=claim.url)
                if not re.search(r"^# .+", markdown, re.MULTILINE) or not _usable(
                    detail
                ):
                    error_code = "invalid_detail"
                else:
                    payload, status = detail, "succeeded"
        except asyncio.CancelledError:
            # Leave the lease intact. Recovery accounts unknown cost only once.
            raise
        except TimeoutError:
            error_code, pause = "fetch_timeout", True
        except ScrapeDoAccountError as exc:
            cost = exc.request_cost or None
            error_code, status, pause = "provider_account", "failed", True
        except ScrapeDoContentError as exc:
            cost = exc.scrape.request_cost
            error_code = "invalid_provider_content"
        except ScrapeDoRequestError as exc:
            cost = exc.request_cost or None
            error_code = "provider_request"
            if not exc.retryable:
                status = "failed"
        except Exception:  # noqa: BLE001 - persist fixed codes, never provider raw errors
            error_code = SourceState.FETCH_ERROR.value
        queue.finish(
            claim,
            status=status,
            payload=payload,
            error_code=error_code,
            request_cost=cost,
            retry_after=180,
        )
        if pause:
            stop_reason = error_code
            break
    return {"requests": requests, "stop_reason": stop_reason}
