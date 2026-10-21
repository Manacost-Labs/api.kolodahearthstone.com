"""Per-call ledger of paid scrape providers (Decodo, Scrape.do, Firecrawl, Scrapfly).

One row per provider HTTP call. Rows hold no URL, query string, key or body:
only the target host and a truncated SHA-256 of the URL. Writes are
best-effort: any ledger failure is logged by type and never reaches the
parser. ``summary`` builds the dashboard-style aggregates for ``/ops``.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .storage import root_dir

logger = logging.getLogger(__name__)

DB_NAME = "provider-usage.sqlite3"
RETENTION_DAYS = 90
_PRUNE_INTERVAL_SECONDS = 3600.0
_BUSY_TIMEOUT_SECONDS = 0.25
_MAX_REASON_LENGTH = 64

PROVIDERS = ("decodo", "scrape_do", "firecrawl", "scrapfly")
OUTCOMES = ("success", "failed")

# Billing unit and default price per unit, overridden by
# HS_<PROVIDER>_USD_PER_UNIT. Decodo matches the account dashboard ($1.05 for
# 1,052 successful requests); Scrape.do is the Pro plan its /info reports
# (1.25M credits for $99); Firecrawl and Scrapfly are entry plan list prices.
PRICING: dict[str, tuple[str, float]] = {
    "decodo": ("request", 0.001),
    "scrape_do": ("credit", 99.0 / 1_250_000),
    "firecrawl": ("credit", 16.0 / 3_000),
    "scrapfly": ("credit", 30.0 / 200_000),
}

_source_id: ContextVar[str | None] = ContextVar("provider_usage_source", default=None)
_schema_lock = threading.Lock()
_local = threading.local()
_schema_ready: set[str] = set()
_last_prune: dict[str, float] = {}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    provider TEXT NOT NULL,
    source_id TEXT,
    host TEXT,
    url_hash TEXT,
    profile TEXT,
    render INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failed')),
    reason TEXT,
    cost REAL NOT NULL DEFAULT 0,
    cost_unit TEXT,
    cost_basis TEXT,
    account TEXT
);
CREATE INDEX IF NOT EXISTS calls_day_provider ON calls (day, provider);
CREATE INDEX IF NOT EXISTS calls_source_day ON calls (source_id, day);
"""


def db_path() -> Path:
    return root_dir() / DB_NAME


@contextmanager
def usage_source(source_id: str | None) -> Iterator[None]:
    """Attribute provider calls made inside the block to ``source_id``."""
    token = _source_id.set(source_id)
    try:
        yield
    finally:
        _source_id.reset(token)


def current_source_id() -> str | None:
    source_id = _source_id.get()
    if source_id:
        return source_id
    try:
        from .meta_intelligence.budget import _scope

        scope = _scope.get()
    except Exception:  # noqa: BLE001 - attribution is optional
        return None
    value = scope.get("source_id") if isinstance(scope, dict) else None
    return str(value) if value else None


def _url_identity(url: str) -> tuple[str | None, str | None]:
    try:
        host = (urlsplit(url).hostname or "").casefold() or None
    except ValueError:
        host = None
    digest = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()[:16]
    return host, digest


def _connect(path: Path) -> sqlite3.Connection:
    key = str(path)
    if not path.exists():
        _schema_ready.discard(key)
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    # A lost last row on power failure is acceptable; an fsync per call is not.
    conn.execute("PRAGMA synchronous=NORMAL")
    if key not in _schema_ready:
        with _schema_lock:
            if key not in _schema_ready:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA)
                _schema_ready.add(key)
    return conn


def _writer(path: Path) -> sqlite3.Connection:
    """Per-thread connection kept open: closing the last WAL connection
    checkpoints the log, which costs an fsync on every provider call."""
    cached = getattr(_local, "writer", None)
    if cached is not None:
        cached_path, conn = cached
        if cached_path == str(path) and path.exists():
            return conn
        conn.close()
        _local.writer = None
    conn = _connect(path)
    _local.writer = (str(path), conn)
    return conn


def _drop_writer() -> None:
    cached = getattr(_local, "writer", None)
    _local.writer = None
    if cached is not None:
        try:
            cached[1].close()
        except sqlite3.Error:
            pass


def _prune(conn: sqlite3.Connection, key: str, now: datetime) -> None:
    monotonic = time.monotonic()
    last = _last_prune.get(key)
    if last is not None and monotonic - last < _PRUNE_INTERVAL_SECONDS:
        return
    _last_prune[key] = monotonic
    cutoff = (now - timedelta(days=RETENTION_DAYS)).date().isoformat()
    conn.execute("DELETE FROM calls WHERE day < ?", (cutoff,))


def _reason(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = "".join(ch if ch.isalnum() or ch in "_.:-" else "_" for ch in value)
    return cleaned[:_MAX_REASON_LENGTH] or None


def record_call(
    provider: str,
    *,
    url: str,
    started: float,
    status: object = None,
    outcome: str | None = None,
    reason: str | None = None,
    content: object = None,
    cost: object = 0,
    cost_basis: str = "reported",
    profile: str | None = None,
    render: bool = False,
    account: object = None,
    source_id: str | None = None,
) -> int | None:
    """Store one provider call; ``started`` is a ``time.monotonic()`` mark.

    ``outcome`` defaults to ``success`` for a 2xx ``status`` and ``failed``
    (``target_<status>``) otherwise; ``content`` is the body or its size.
    Every value is validated here, so a malformed provider result only skips
    the row. Returns the row id for :func:`mark_rejected`. Never raises.
    """
    try:
        if provider not in PROVIDERS:
            raise ValueError("unknown provider")
        http_status = (
            int(status)
            if isinstance(status, int) and not isinstance(status, bool)
            else None
        )
        if outcome is None:
            outcome = (
                "success"
                if http_status is not None and 200 <= http_status <= 299
                else "failed"
            )
            if outcome == "failed" and reason is None:
                reason = f"target_{http_status}" if http_status else "no_status"
        if outcome not in OUTCOMES:
            raise ValueError("unknown outcome")
        if isinstance(content, str):
            size = len(content.encode("utf-8", errors="replace"))
        elif isinstance(content, (bytes, bytearray)):
            size = len(content)
        elif isinstance(content, int) and not isinstance(content, bool):
            size = max(0, content)
        else:
            size = 0
        amount = float(cost) if isinstance(cost, (int, float)) else 0.0
        if not math.isfinite(amount) or amount < 0:
            amount = 0.0
        now = datetime.now(UTC)
        host, digest = _url_identity(url)
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        path = db_path()
        conn = _writer(path)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO calls (
                        ts, day, provider, source_id, host, url_hash, profile,
                        render, http_status, duration_ms, bytes, outcome, reason,
                        cost, cost_unit, cost_basis, account
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now.isoformat(timespec="seconds"),
                        now.date().isoformat(),
                        provider,
                        source_id or current_source_id(),
                        host,
                        digest,
                        profile,
                        int(bool(render)),
                        http_status,
                        duration_ms,
                        size,
                        outcome,
                        _reason(reason) if outcome == "failed" else None,
                        amount,
                        PRICING[provider][0],
                        cost_basis,
                        account[:64] if isinstance(account, str) and account else None,
                    ),
                )
                _prune(conn, str(path), now)
        except sqlite3.Error:
            _drop_writer()
            raise
        return int(cursor.lastrowid) if cursor.lastrowid is not None else None
    except Exception as exc:  # noqa: BLE001 - accounting must not break parsing
        logger.warning("provider usage write skipped: %s", type(exc).__name__)
        return None


def mark_rejected(call_id: object, reason: str = "content_rejected") -> None:
    """Turn an accepted-by-provider call into a failure after content checks."""
    if not isinstance(call_id, int) or isinstance(call_id, bool):
        return
    try:
        conn = _writer(db_path())
        try:
            with conn:
                conn.execute(
                    "UPDATE calls SET outcome='failed', reason=? "
                    "WHERE id=? AND outcome='success'",
                    (_reason(reason), call_id),
                )
        except sqlite3.Error:
            _drop_writer()
            raise
    except Exception as exc:  # noqa: BLE001 - accounting must not break parsing
        logger.warning("provider usage update skipped: %s", type(exc).__name__)


def unit_prices() -> dict[str, dict[str, Any]]:
    prices: dict[str, dict[str, Any]] = {}
    for provider, (unit, default) in PRICING.items():
        raw = os.environ.get(f"HS_{provider.upper()}_USD_PER_UNIT", "").strip()
        basis = "default"
        price = default
        if raw:
            try:
                parsed = float(raw)
            except ValueError:
                parsed = -1.0
            if math.isfinite(parsed) and parsed >= 0:
                price, basis = parsed, "configured"
        prices[provider] = {"unit": unit, "usd_per_unit": price, "basis": basis}
    return prices


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return int(ordered[index])


def _aggregate(
    rows: list[sqlite3.Row], prices: dict[str, dict[str, Any]], *, with_unit: bool
) -> dict[str, Any]:
    total = len(rows)
    success = sum(1 for row in rows if row["outcome"] == "success")
    durations = [int(row["duration_ms"]) for row in rows]
    cost_usd = sum(
        float(row["cost"]) * float(prices[row["provider"]]["usd_per_unit"])
        for row in rows
    )
    item: dict[str, Any] = {
        "total": total,
        "success": success,
        "failed": total - success,
        "success_rate": round(success * 100 / total, 1) if total else None,
        "avg_ms": round(sum(durations) / total) if total else None,
        "p95_ms": _percentile(durations, 0.95),
        "bytes": sum(int(row["bytes"]) for row in rows),
        "cost_usd": round(cost_usd, 4),
        "usd_per_1k_success": round(cost_usd * 1000 / success, 3) if success else None,
    }
    if with_unit:
        item["cost_units"] = round(sum(float(row["cost"]) for row in rows), 2)
    return item


def summary(
    *,
    days: int = 30,
    provider: str | None = None,
    source_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Decodo-dashboard style totals per provider and per UTC day."""
    now = now or datetime.now(UTC)
    days = max(1, min(RETENTION_DAYS, int(days)))
    last_day = now.astimezone(UTC).date()
    first_day = last_day - timedelta(days=days - 1)
    if provider is not None and provider not in PROVIDERS:
        raise ValueError("unknown provider")
    prices = unit_prices()
    clauses = ["day >= ?", "day <= ?"]
    params: list[Any] = [first_day.isoformat(), last_day.isoformat()]
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if source_id:
        clauses.append("source_id = ?")
        params.append(source_id)
    path = db_path()
    rows: list[sqlite3.Row] = []
    first_recorded_at = None
    if path.exists():
        conn = _connect(path)
        try:
            rows = conn.execute(
                "SELECT day, provider, source_id, outcome, reason, duration_ms, "
                "bytes, cost FROM calls WHERE " + " AND ".join(clauses),
                params,
            ).fetchall()
            first = conn.execute("SELECT MIN(ts) FROM calls").fetchone()
            first_recorded_at = first[0] if first else None
        finally:
            conn.close()

    by_provider: dict[str, list[sqlite3.Row]] = {}
    by_day: dict[tuple[str, str], list[sqlite3.Row]] = {}
    by_source: dict[str, list[sqlite3.Row]] = {}
    reasons: dict[tuple[str, str], int] = {}
    for row in rows:
        by_provider.setdefault(row["provider"], []).append(row)
        by_day.setdefault((row["day"], row["provider"]), []).append(row)
        by_source.setdefault(row["source_id"] or "", []).append(row)
        if row["outcome"] == "failed":
            key = (row["provider"], row["reason"] or "unknown")
            reasons[key] = reasons.get(key, 0) + 1

    providers = [
        {
            "provider": name,
            "cost_unit": prices[name]["unit"],
            **_aggregate(by_provider[name], prices, with_unit=True),
        }
        for name in PROVIDERS
        if name in by_provider
    ]
    daily = [
        {"day": day, "provider": name, **_aggregate(group, prices, with_unit=True)}
        for (day, name), group in sorted(by_day.items())
    ]
    sources = sorted(
        (
            {"source_id": key or None, **_aggregate(group, prices, with_unit=False)}
            for key, group in by_source.items()
        ),
        key=lambda item: (-item["cost_usd"], -item["total"]),
    )[:50]
    failure_reasons = [
        {"provider": name, "reason": reason, "count": count}
        for (name, reason), count in sorted(
            reasons.items(), key=lambda entry: (-entry[1], entry[0])
        )[:20]
    ]
    return {
        "generated_at": now.astimezone(UTC).isoformat(timespec="seconds"),
        "days": days,
        "from": first_day.isoformat(),
        "to": last_day.isoformat(),
        "timezone": "UTC",
        "filters": {"provider": provider, "source_id": source_id},
        "first_recorded_at": first_recorded_at,
        "retention_days": RETENTION_DAYS,
        "pricing": prices,
        "totals": _aggregate(rows, prices, with_unit=False),
        "providers": providers,
        "daily": daily,
        "sources": sources,
        "failure_reasons": failure_reasons,
    }
