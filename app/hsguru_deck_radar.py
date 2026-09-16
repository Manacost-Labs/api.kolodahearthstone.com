"""Persist and publish newly observed HSGuru streamer deckstrings.

The first successful source snapshot establishes history without creating
events. Later unseen deckstrings enter as candidates and become confirmed only
after a second distinct source snapshot contains the same exact deckstring.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from hearthstone.deckstrings import Deck

SOURCE_ID = "hsguru_streamer_decks_legend_1000"
EVENT_TYPE = "new_exact_deck"
BETA_KNOWN_VARIANT_SIMILARITY = 0.75
RadarStatus = Literal["candidate", "confirmed"]


@dataclass(frozen=True)
class KnownDeckCatalog:
    """Current HSGuru catalog evidence used to suppress established decks."""

    deck_codes: tuple[str, ...]
    deck_titles: frozenset[str]
    fetched_at: str | None


def ensure_deck_radar_schema(conn: sqlite3.Connection) -> None:
    """Create additive Radar tables and query indexes on an existing database."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hsguru_deck_radar_snapshots (
            source_id TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (source_id, source_fetched_at)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hsguru_deck_radar_observations (
            source_id TEXT NOT NULL,
            source_fetched_at TEXT NOT NULL,
            deck_fingerprint TEXT NOT NULL,
            deck_code TEXT NOT NULL,
            deck_name TEXT,
            streamer TEXT,
            format TEXT,
            source_url TEXT,
            PRIMARY KEY (source_id, source_fetched_at, deck_fingerprint),
            FOREIGN KEY (source_id, source_fetched_at)
                REFERENCES hsguru_deck_radar_snapshots(source_id, source_fetched_at)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hsguru_deck_radar_events (
            deck_fingerprint TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed')),
            deck_code TEXT NOT NULL,
            deck_name TEXT,
            streamer TEXT,
            format TEXT,
            source_url TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            observation_count INTEGER NOT NULL DEFAULT 1 CHECK (observation_count > 0)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hsguru_deck_radar_events_latest "
        "ON hsguru_deck_radar_events(status, last_seen_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hsguru_deck_radar_observations_fingerprint "
        "ON hsguru_deck_radar_observations(deck_fingerprint, source_fetched_at DESC)"
    )


def _deck_fingerprint(deck_code: str) -> str:
    return sha256(deck_code.encode("utf-8")).hexdigest()


def _snapshot_rows(dataset: dict[str, Any]) -> list[dict[str, str | None]]:
    structured = (dataset.get("data") or {}).get("structured") or {}
    raw_rows = structured.get("rows") if isinstance(structured, dict) else None
    if not isinstance(raw_rows, list):
        return []

    rows_by_fingerprint: dict[str, dict[str, str | None]] = {}
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue
        deck_code = str(raw_row.get("deck_code") or "").strip()
        if not deck_code:
            continue
        fingerprint = _deck_fingerprint(deck_code)
        rows_by_fingerprint.setdefault(
            fingerprint,
            {
                "deck_fingerprint": fingerprint,
                "deck_code": deck_code,
                "deck_name": _deck_name(raw_row.get("Deck"), deck_code),
                "streamer": _optional_text(raw_row.get("Streamer")),
                "format": _optional_text(raw_row.get("Format")),
                "source_url": _optional_text(raw_row.get("Deck_url")),
            },
        )
    return list(rows_by_fingerprint.values())


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _deck_name(value: object, deck_code: str) -> str | None:
    """Extract HSGuru's heading without retaining its copied deckstring text."""
    heading = str(value or "").splitlines()[0]
    name = heading.split(deck_code, maxsplit=1)[0].replace("###", "").strip()
    return name or _optional_text(value)


def known_catalog_from_meta_matrix(
    dataset: dict[str, Any] | None,
) -> KnownDeckCatalog | None:
    """Read current HSGuru builds embedded in the published meta matrix."""
    structured = ((dataset or {}).get("data") or {}).get("structured") or {}
    catalog = (
        structured.get("current_catalog") if isinstance(structured, dict) else None
    )
    archetypes = catalog.get("archetypes") if isinstance(catalog, dict) else None
    if not isinstance(archetypes, list):
        return None

    deck_codes: set[str] = set()
    deck_titles: set[str] = set()
    for archetype in archetypes:
        if not isinstance(archetype, dict):
            continue
        archetype_name = _optional_text(archetype.get("archetype"))
        if archetype_name:
            deck_titles.add(archetype_name.casefold())
        decks = archetype.get("decks") or []
        if not isinstance(decks, list):
            continue
        for deck in decks:
            if not isinstance(deck, dict):
                continue
            deck_code = _optional_text(deck.get("deck_code"))
            if deck_code:
                deck_codes.add(deck_code)
            title = _optional_text(deck.get("title"))
            if title:
                deck_titles.add(title.casefold())
    if not deck_codes and not deck_titles:
        return None
    return KnownDeckCatalog(
        deck_codes=tuple(sorted(deck_codes)),
        deck_titles=frozenset(deck_titles),
        fetched_at=_optional_text((dataset or {}).get("fetched_at")),
    )


def _deck_cards(deck_code: str) -> Counter[int] | None:
    try:
        deck = Deck.from_deckstring(deck_code)
    except Exception:  # noqa: BLE001 - malformed deckstrings use library-specific errors
        return None
    return Counter(
        {int(dbf_id): int(count) for dbf_id, count in deck.get_dbf_id_list()}
    )


def _is_known_catalog_variant(
    row: dict[str, str | None],
    catalog: KnownDeckCatalog,
) -> bool:
    deck_code = row["deck_code"]
    if deck_code in catalog.deck_codes:
        return True
    deck_name = _optional_text(row.get("deck_name"))
    if deck_name and deck_name.casefold() in catalog.deck_titles:
        return True
    candidate_cards = _deck_cards(deck_code or "")
    if not candidate_cards:
        return False
    for known_code in catalog.deck_codes:
        known_cards = _deck_cards(known_code)
        if not known_cards:
            continue
        card_ids = set(candidate_cards) | set(known_cards)
        overlap = sum(
            min(candidate_cards[card], known_cards[card]) for card in card_ids
        )
        union = sum(max(candidate_cards[card], known_cards[card]) for card in card_ids)
        if union and overlap / union >= BETA_KNOWN_VARIANT_SIMILARITY:
            return True
    return False


def reconcile_streamer_snapshot(
    dataset: dict[str, Any],
    *,
    connection: sqlite3.Connection | None = None,
    known_catalog: KnownDeckCatalog | None = None,
    require_known_catalog: bool = False,
) -> dict[str, int | bool]:
    """Store one published snapshot and update candidate/confirmed Radar events.

    ``source_fetched_at`` is the upstream publication revision. Its unique
    constraint makes a retry of the same published dataset a no-op.
    """
    source_id = str(dataset.get("source_id") or SOURCE_ID)
    if source_id != SOURCE_ID:
        raise ValueError(f"Deck Radar only accepts {SOURCE_ID}")
    source_fetched_at = _optional_text(dataset.get("fetched_at"))
    if source_fetched_at is None:
        raise ValueError("Deck Radar requires a published source timestamp")

    owns_connection = connection is None
    if owns_connection:
        from .db import get_db_connection, init_db

        init_db()
        connection = get_db_connection()
    assert connection is not None
    ensure_deck_radar_schema(connection)
    rows = _snapshot_rows(dataset)
    result: dict[str, int | bool] = {
        "baseline_created": False,
        "events_created": 0,
        "events_confirmed": 0,
        "events_suppressed_catalog_unavailable": 0,
        "events_suppressed_known_catalog": 0,
        "observations": len(rows),
        "snapshot_recorded": False,
    }
    try:
        with connection:
            already_recorded = connection.execute(
                """
                SELECT 1 FROM hsguru_deck_radar_snapshots
                WHERE source_id = ? AND source_fetched_at = ?
                """,
                (source_id, source_fetched_at),
            ).fetchone()
            if already_recorded is not None:
                result["observations"] = 0
                return result

            prior_snapshots = int(
                connection.execute(
                    "SELECT COUNT(*) FROM hsguru_deck_radar_snapshots WHERE source_id = ?",
                    (source_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO hsguru_deck_radar_snapshots
                    (source_id, source_fetched_at, recorded_at)
                VALUES (?, ?, ?)
                """,
                (source_id, source_fetched_at, source_fetched_at),
            )
            result["snapshot_recorded"] = True
            result["baseline_created"] = prior_snapshots == 0

            for row in rows:
                observed_before = connection.execute(
                    """
                    SELECT 1 FROM hsguru_deck_radar_observations
                    WHERE source_id = ? AND deck_fingerprint = ?
                    LIMIT 1
                    """,
                    (source_id, row["deck_fingerprint"]),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO hsguru_deck_radar_observations (
                        source_id, source_fetched_at, deck_fingerprint, deck_code,
                        deck_name, streamer, format, source_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_id,
                        source_fetched_at,
                        row["deck_fingerprint"],
                        row["deck_code"],
                        row["deck_name"],
                        row["streamer"],
                        row["format"],
                        row["source_url"],
                    ),
                )
                if prior_snapshots == 0:
                    continue

                event = connection.execute(
                    """
                    SELECT status FROM hsguru_deck_radar_events
                    WHERE deck_fingerprint = ?
                    """,
                    (row["deck_fingerprint"],),
                ).fetchone()
                if event is None:
                    if observed_before is not None:
                        continue
                    if require_known_catalog and known_catalog is None:
                        result["events_suppressed_catalog_unavailable"] = (
                            int(result["events_suppressed_catalog_unavailable"]) + 1
                        )
                        continue
                    if known_catalog and _is_known_catalog_variant(row, known_catalog):
                        result["events_suppressed_known_catalog"] = (
                            int(result["events_suppressed_known_catalog"]) + 1
                        )
                        continue
                    connection.execute(
                        """
                        INSERT INTO hsguru_deck_radar_events (
                            deck_fingerprint, event_type, status, deck_code,
                            deck_name, streamer, format, source_url,
                            first_seen_at, last_seen_at, observation_count
                        ) VALUES (?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            row["deck_fingerprint"],
                            EVENT_TYPE,
                            row["deck_code"],
                            row["deck_name"],
                            row["streamer"],
                            row["format"],
                            row["source_url"],
                            source_fetched_at,
                            source_fetched_at,
                        ),
                    )
                    result["events_created"] = int(result["events_created"]) + 1
                    continue

                becoming_confirmed = event["status"] == "candidate"
                connection.execute(
                    """
                    UPDATE hsguru_deck_radar_events
                    SET status = CASE WHEN status = 'candidate' THEN 'confirmed' ELSE status END,
                        deck_name = COALESCE(?, deck_name),
                        streamer = COALESCE(?, streamer),
                        format = COALESCE(?, format),
                        source_url = COALESCE(?, source_url),
                        last_seen_at = ?,
                        observation_count = observation_count + 1
                    WHERE deck_fingerprint = ?
                    """,
                    (
                        row["deck_name"],
                        row["streamer"],
                        row["format"],
                        row["source_url"],
                        source_fetched_at,
                        row["deck_fingerprint"],
                    ),
                )
                if becoming_confirmed:
                    result["events_confirmed"] = int(result["events_confirmed"]) + 1
        return result
    finally:
        if owns_connection:
            connection.close()


def list_deck_radar_events(
    *,
    status: RadarStatus | None = None,
    limit: int = 50,
    offset: int = 0,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Return persisted Radar events, newest observation first."""
    owns_connection = connection is None
    if owns_connection:
        from .db import get_db_connection, init_db

        init_db()
        connection = get_db_connection()
    assert connection is not None
    ensure_deck_radar_schema(connection)
    try:
        where = ""
        params: list[object] = []
        if status is not None:
            where = " WHERE status = ?"
            params.append(status)
        total = int(
            connection.execute(
                "SELECT COUNT(*) FROM hsguru_deck_radar_events" + where, params
            ).fetchone()[0]
        )
        fetched_at_row = connection.execute(
            "SELECT MAX(last_seen_at) FROM hsguru_deck_radar_events" + where,
            params,
        ).fetchone()
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT deck_fingerprint AS fingerprint, event_type, status, deck_code,
                       deck_name, streamer, format, source_url, first_seen_at,
                       last_seen_at, observation_count
                FROM hsguru_deck_radar_events
                """
                + where
                + " ORDER BY last_seen_at DESC, deck_fingerprint ASC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        ]
        return {
            "events": rows,
            "total": total,
            "fetched_at": fetched_at_row[0] if fetched_at_row else None,
        }
    finally:
        if owns_connection:
            connection.close()
