#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.deck_decode import first_deck_code_from_text  # noqa: E402
from app.sources import SOURCE_BY_ID  # noqa: E402
from app.storage import load_dataset, save_dataset, save_status  # noqa: E402


SOURCE_ID = "hsguru_streamer_decks_legend_1000"
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
DEFAULT_ENV_FILE = "/etc/hs-data-api.env"
HEADERS = [
    "Deck",
    "Streamer",
    "Format",
    "Peak",
    "Latest",
    "Worst",
    "Win - Loss",
    "Links",
    "Last Played",
]


def _load_env(path: str = DEFAULT_ENV_FILE) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _scrape_html(url: str) -> tuple[str, dict[str, Any]]:
    from app.firecrawl_keys import (
        acquire_firecrawl_key,
        is_firecrawl_credit_error,
        mark_firecrawl_key_exhausted,
        record_firecrawl_credits,
    )

    payload = {
        "url": url,
        "formats": ["html", "markdown"],
        "onlyMainContent": True,
        "maxAge": int(os.environ.get("HS_FIRECRAWL_MAX_AGE_MS", "3000000")),
        "waitFor": int(os.environ.get("HS_FIRECRAWL_WAIT_MS", "5000")),
        "timeout": int(os.environ.get("HS_FIRECRAWL_TIMEOUT_MS", "30000")),
    }
    errors: list[str] = []
    for _ in range(8):
        lease = acquire_firecrawl_key()
        request = urllib.request.Request(
            FIRECRAWL_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {lease.key.key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            if is_firecrawl_credit_error(exc):
                mark_firecrawl_key_exhausted(lease.key.label, reason=str(exc))
                errors.append(f"{lease.key.label}: {exc}")
                continue
            raise
        if not body.get("success"):
            raise RuntimeError(f"Firecrawl scrape failed: {body}")
        data = body.get("data") or {}
        metadata = dict(data.get("metadata") or {})
        if body.get("creditsUsed") is not None and metadata.get("creditsUsed") is None:
            metadata["creditsUsed"] = body.get("creditsUsed")
        try:
            credits_int = int(metadata.get("creditsUsed") or 1)
        except (TypeError, ValueError):
            credits_int = 1
        metadata["firecrawl_key_label"] = lease.key.label
        metadata["firecrawl_key_fingerprint"] = lease.key.fingerprint
        metadata["firecrawl_key_rotation"] = record_firecrawl_credits(
            lease.key.label, max(1, credits_int)
        )
        html = data.get("html") or ""
        if not html:
            raise RuntimeError("Firecrawl response did not include html")
        return html, metadata

    detail = "; ".join(errors) if errors else "no available keys"
    raise RuntimeError(f"Firecrawl scrape failed after key rotation attempts: {detail}")


def _parse_table(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("Streamer decks table not found in Firecrawl html")
    rows: list[dict[str, Any]] = []
    for tr in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
        if cells == HEADERS or len(cells) < len(HEADERS):
            continue
        row = dict(zip(HEADERS, cells[: len(HEADERS)], strict=False))
        deck_code = first_deck_code_from_text(row.get("Deck") or "")
        if deck_code:
            row["deck_code"] = deck_code
        rows.append(row)
    return rows


def _dedupe_key(row: dict[str, Any]) -> str:
    deck_code = str(row.get("deck_code") or "").strip()
    if not deck_code:
        deck_code = first_deck_code_from_text(str(row.get("Deck") or "")) or ""
    if deck_code:
        row["deck_code"] = deck_code
        return f"code:{deck_code}"
    deck = re.sub(r"\s+", " ", str(row.get("Deck") or "")).strip().lower()
    streamer = str(row.get("Streamer") or "").strip().lower()
    fmt = str(row.get("Format") or "").strip().lower()
    return f"row:{fmt}:{streamer}:{deck[:160]}"


def _previous_rows() -> list[dict[str, Any]]:
    dataset = load_dataset(SOURCE_ID) or {}
    data = dataset.get("data") or {}
    tables = data.get("tables") or []
    if not tables:
        return []
    rows = tables[0].get("objects") or []
    return [row for row in rows if isinstance(row, dict)]


def _merge_rows(new_rows: list[dict[str, Any]], previous_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del previous_rows
    limit = int(os.environ.get("HS_FIRECRAWL_STREAMER_DECK_LIMIT", "100"))
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for row in new_rows:
        key = _dedupe_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(row)
        if len(merged) >= limit:
            break
    return merged


def _save(rows: list[dict[str, Any]], *, html: str, metadata: dict[str, Any], started: float) -> dict[str, Any]:
    source = SOURCE_BY_ID[SOURCE_ID]
    fetched_at = _now_iso()
    table_rows = [[row.get(header, "") for header in HEADERS] for row in rows]
    data = {
        "source_id": source.id,
        "site": source.site,
        "category": source.category,
        "url": source.url,
        "fetch_url": source.fetch_url,
        "fragment": source.fragment,
        "title": "Streamer decks",
        "tables": [
            {
                "index": 0,
                "headers": HEADERS,
                "rows": table_rows,
                "objects": rows,
            }
        ],
        "structured": {
            "type": "streamer_decks",
            "rows": rows,
        },
        "firecrawl": {
            "credits_used": metadata.get("creditsUsed"),
            "cache_state": metadata.get("cacheState"),
            "scrape_id": metadata.get("scrapeId"),
        },
    }
    content_length = len(html.encode("utf-8"))
    quality = {
        "table_rows": len(rows),
        "deck_codes": sum(1 for row in rows if row.get("deck_code")),
        "rows_total": len(rows),
        "critical_fields": {
            "Deck": {"filled": sum(1 for row in rows if row.get("Deck")), "total": len(rows), "rate": 1.0 if rows else 0.0},
            "Streamer": {"filled": sum(1 for row in rows if row.get("Streamer")), "total": len(rows), "rate": 1.0 if rows else 0.0},
        },
        "quality_score": 1.0 if rows else 0.0,
        "missing_critical_fields": [],
    }
    payload = {
        "state": "ok",
        "fetched_at": fetched_at,
        "http_status": metadata.get("statusCode") or 200,
        "final_url": metadata.get("ogUrl") or source.fetch_url,
        "content_length": content_length,
        "backend": "firecrawl",
        "used_residential_proxy": False,
        "data": data,
    }
    status = {
        "source_id": source.id,
        "site": source.site,
        "category": source.category,
        "url": source.url,
        "fetch_url": source.fetch_url,
        "fragment": source.fragment,
        "state": "ok",
        "fetched_at": fetched_at,
        "http_status": payload["http_status"],
        "final_url": payload["final_url"],
        "error": None,
        "detail": None,
        "content_length": content_length,
        "backend": "firecrawl",
        "used_residential_proxy": False,
        "quality": quality,
        "quality_score": quality["quality_score"],
        "rows_total": len(rows),
        "firecrawl_credits_used": metadata.get("creditsUsed"),
        "elapsed_sec": round(time.time() - started, 2),
    }
    save_dataset(SOURCE_ID, payload)
    save_status(SOURCE_ID, status)
    return status


def main() -> int:
    started = time.time()
    _load_env()
    from app.resource_locks import ResourceLocked, ResourceLockSet

    try:
        with ResourceLockSet(
            [SOURCE_ID],
            metadata={"operation": "firecrawl-streamer-decks"},
        ):
            source = SOURCE_BY_ID[SOURCE_ID]
            html, metadata = _scrape_html(source.fetch_url)
            new_rows = _parse_table(html)
            if not new_rows:
                raise RuntimeError("Firecrawl streamer decks returned zero rows")
            rows = _merge_rows(new_rows, _previous_rows())
            status = _save(rows, html=html, metadata=metadata, started=started)
    except ResourceLocked as exc:
        print(
            json.dumps(
                {"ok": True, "source_id": SOURCE_ID, **exc.as_outcome()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    try:
        from app.fun_decks import refresh_fun_decks

        fun_status = refresh_fun_decks()
        status = dict(status)
        status["fun_decks"] = fun_status
    except Exception as exc:  # noqa: BLE001 - streamer refresh should still succeed
        status = dict(status)
        status["fun_decks"] = {"ok": False, "error": str(exc)[:500]}
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
