"""Optional Editor Team pass over the radar's reader texts.

Contract: ManacostTeam ``docs/RADAR_EDITOR_CONTRACT.md``. ``POST /v2/edit``
in ``ANALYSIS`` mode with the ``news`` profile, a revision brief and the run's
name glossary as a source snapshot; one text per request, at most four in
parallel, 600 s per request, 15 minutes for the whole stage. The edit is used
only when ``accepted and checks_complete and fact_check_passed`` and the text
is not empty; otherwise the radar's own text stays. The editor never blocks a
run: no URL, a failed ``/health``, a timeout or bad JSON all keep the original.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 5.0
REQUEST_TIMEOUT = 600.0
STAGE_BUDGET_SECONDS = 900.0
CONCURRENCY = 4
# Measured 2026-10-25: 5 of 22 texts accepted for $4.77, mostly
# no_measurable_improvement; three verdicts keep the pass affordable.
DEFAULT_MAX_VERDICTS = 3

REVISION_BRIEF = """Это текст радара нерфов и баффов Hearthstone для сайта, голос Манакоста. Задание:
1. Названия карт, классов и архетипов — только по-русски, строго по глоссарию из источника radar-glossary. Английские дубли в скобках убрать, английские названия заменить русскими из глоссария, транслитерацию заменить русским названием карты. Названия не из глоссария не переводить наугад.
2. Ранги по локализации: Алмаз, Легенда («от Алмаза 4 до Легенды», «на Легенде»).
3. Служебные слова пайплайна (имена полей, «п.п.») заменить обычной речью.
4. Коротко и аккуратно: убрать повтор цифр и второстепенные подробности, оставить цифры, на которых держится вывод. Один абзац.
5. Английские цитаты из соцсетей пересказать по-русски.
Не добавлять новых фактов, чисел, сроков, причин и прогнозов; степень уверенности сохранить."""


def editor_url() -> str | None:
    value = os.environ.get("RADAR_EDITOR_URL", "").strip().rstrip("/")
    return value or None


def max_verdicts() -> int:
    try:
        return max(
            0,
            int(os.environ.get("RADAR_EDITOR_MAX_VERDICTS", str(DEFAULT_MAX_VERDICTS))),
        )
    except ValueError:
        return DEFAULT_MAX_VERDICTS


@dataclass
class EditJob:
    key: str  # e.g. "summary" or "nerfs:card:BE_085:verdict"
    text: str
    glossary: str


@dataclass
class EditResult:
    key: str
    text: str
    applied: bool
    status: str | None = None
    rejection_reasons: list[str] = field(default_factory=list)
    prompt_version: str | None = None
    seconds: float = 0.0
    request_id: str | None = None
    error: str | None = None

    def report(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "applied": self.applied,
            "status": self.status,
            "rejectionReasons": self.rejection_reasons,
            "promptVersion": self.prompt_version,
            "seconds": round(self.seconds, 1),
        }
        if not self.applied:
            item["requestId"] = self.request_id
            if self.error:
                item["error"] = self.error
        return item


def _snapshot(glossary: str) -> dict[str, Any]:
    return {
        "source_id": "radar-glossary",
        "url": "https://api.hearthstonejson.com/v1/latest/ruRU/cards.collectible.json",
        "title": "Глоссарий русских названий для радара",
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "access": "snapshot",
        "text": glossary,
        "sha256": hashlib.sha256(glossary.encode("utf-8")).hexdigest(),
    }


def _edit_one(client: httpx.Client, base: str, job: EditJob, run_id: str) -> EditResult:
    request_id = f"radar-{run_id}-{job.key}"[:120]
    started = time.monotonic()
    body: dict[str, Any] = {
        "text": job.text,
        "candidate_text": job.text,
        "revise_candidate": True,
        "revision_brief": REVISION_BRIEF,
        "mode": "edit",
        "game": "hearthstone",
        "profile": "news",
        "editorial_mode": "ANALYSIS",
    }
    if job.glossary.strip():
        body["source_snapshots"] = [_snapshot(job.glossary)]
    try:
        response = client.post(
            f"{base}/v2/edit",
            json=body,
            headers={"X-Request-ID": request_id},
            timeout=REQUEST_TIMEOUT,
        )
        data = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as exc:
        return EditResult(
            job.key,
            job.text,
            False,
            seconds=time.monotonic() - started,
            request_id=request_id,
            error=type(exc).__name__,
        )
    text = str(data.get("text") or "")
    applied = (
        response.status_code == 200
        and data.get("accepted") is True
        and data.get("checks_complete") is True
        and data.get("fact_check_passed") is True
        and bool(text.strip())
    )
    return EditResult(
        job.key,
        " ".join(text.split()) if applied else job.text,  # one paragraph on the card
        applied,
        status=data.get("status")
        or (f"http_{response.status_code}" if response.status_code != 200 else None),
        rejection_reasons=[str(r) for r in data.get("rejection_reasons") or []],
        prompt_version=data.get("prompt_version"),
        seconds=time.monotonic() - started,
        request_id=request_id,
    )


def edit_texts(
    jobs: Sequence[EditJob],
    *,
    run_id: str,
    base_url: str | None = None,
    transport: httpx.BaseTransport | None = None,
    budget_seconds: float = STAGE_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, EditResult], dict[str, Any]]:
    """Edited texts by job key (originals where the editor declined) and a stage report."""
    base = (base_url or editor_url() or "").rstrip("/")
    report: dict[str, Any] = {
        "enabled": bool(base),
        "texts": len(jobs),
        "applied": 0,
        "rejected": 0,
        "skipped": 0,
        "seconds": 0.0,
    }
    results = {
        job.key: EditResult(job.key, job.text, False, status="skipped") for job in jobs
    }
    if not base or not jobs:
        report["skipped"] = len(jobs)
        return results, report
    started = clock()
    with httpx.Client(transport=transport) as client:
        try:
            health = client.get(f"{base}/health", timeout=HEALTH_TIMEOUT)
            healthy = health.status_code == 200 and health.json().get("ok") is True
        except (httpx.HTTPError, ValueError):
            healthy = False
        if not healthy:
            report.update(skipped=len(jobs), error="editor health check failed")
            return results, report
        deadline = started + budget_seconds
        pool = ThreadPoolExecutor(max_workers=CONCURRENCY)
        futures = {
            pool.submit(_edit_one, client, base, job, run_id): job.key for job in jobs
        }
        try:
            for future in as_completed(futures, timeout=max(1.0, deadline - clock())):
                result = future.result()
                results[result.key] = result
        except TimeoutError:
            report["error"] = "editor stage budget exhausted"
        finally:
            # Unfinished edits keep the original text; never wait past the budget.
            pool.shutdown(wait=False, cancel_futures=True)
    report["applied"] = sum(1 for r in results.values() if r.applied)
    report["rejected"] = sum(
        1 for r in results.values() if not r.applied and r.status != "skipped"
    )
    report["skipped"] = sum(1 for r in results.values() if r.status == "skipped")
    report["seconds"] = round(clock() - started, 1)
    return results, report


def glossary_for(text: str, glossary: Any) -> str:
    """The glossary lines whose names appear in this text (either language)."""
    return glossary.as_lines(glossary.relevant(text)) if glossary is not None else ""


def jobs_for_payload(
    payload: Mapping[str, Any], glossary: Any, *, limit: int | None = None
) -> list[EditJob]:
    """Summary plus the verdicts of the most confident candidates."""
    jobs = [
        EditJob(
            "summary",
            payload.get("summary") or "",
            glossary_for(payload.get("summary") or "", glossary),
        )
    ]
    ranked = sorted(
        ((side, c) for side in ("nerfs", "buffs") for c in payload.get(side) or []),
        key=lambda item: -int(item[1].get("confidence") or 0),
    )[: (max_verdicts() if limit is None else limit)]
    for side, candidate in ranked:
        verdict = candidate.get("verdict") or ""
        if verdict.strip():
            jobs.append(
                EditJob(
                    f"{side}:{candidate['ref']}:verdict",
                    verdict,
                    glossary_for(verdict, glossary),
                )
            )
    return [job for job in jobs if job.text.strip()]
