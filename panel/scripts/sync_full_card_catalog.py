#!/usr/bin/env python3
"""Publish the complete free HSJSON catalog without changing live format pools.

Writes hearthstone_card_catalog (ruRU/enUS) and hearthstone_card_localizations
(ruRU, enUS, plPL, esES) in one transaction.

Default is read-only. Prepare full_card_catalog.sql and card_localizations.sql
separately, then use --apply with the dry-run total. Run under the panel's
existing global sync lock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from sync_constructed_related_cards import connect_db, load_php_config

MAX_BYTES = 32 * 1024 * 1024
# Allow-list instead of a CHECK/ENUM: a new locale (esMX) needs no ALTER.
LOCALES = ("ruRU", "enUS", "plPL", "esES")
MIN_PUBLISHED = 10000
MIN_KEPT_SHARE = 0.95


def load_snapshot(locale, path=None):
    if path:
        content = Path(path).read_bytes()
    else:
        request = urllib.request.Request(
            f"https://api.hearthstonejson.com/v1/latest/{locale}/cards.json",
            headers={"User-Agent": "KolodaHearthstone-full-catalog/1.0"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("Snapshot exceeds 32 MiB")
    return json.loads(content)


def index_snapshot(payload, minimum=10000):
    if not isinstance(payload, list) or not minimum <= len(payload) <= 100000:
        raise ValueError("Incomplete or oversized card catalog")
    result, dbfs = {}, set()
    for card in payload:
        if not isinstance(card, dict):
            raise TypeError("Invalid card")
        card_id, dbf = card.get("id"), card.get("dbfId")
        if (
            not isinstance(card_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", card_id)
            or type(dbf) is not int
            or not 0 < dbf <= 4294967295
            or card_id in result
            or dbf in dbfs
        ):
            raise ValueError("Invalid or conflicting catalog identity")
        result[card_id] = card
        dbfs.add(dbf)
    return result


def canonical(card):
    return json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def catalog_rows(ru, en, minimum=10000):
    ru_index, en_index = index_snapshot(ru, minimum), index_snapshot(en, minimum)
    if ru_index.keys() != en_index.keys():
        raise ValueError("Locale snapshots have different identities")
    rows = []
    for card_id, english in sorted(en_index.items()):
        russian = ru_index[card_id]
        if russian["dbfId"] != english["dbfId"]:
            raise ValueError("Locale dbf identity mismatch")
        payload_ru, payload_en = canonical(russian), canonical(english)
        rows.append(
            {
                "card_id": card_id,
                "dbf": english["dbfId"],
                "collectible": int(bool(english.get("collectible"))),
                "name_ru": russian.get("name"),
                "name_en": english.get("name"),
                "card_type": english.get("type"),
                "card_set": english.get("set"),
                "payload_ru": payload_ru,
                "payload_en": payload_en,
                "source_hash": hashlib.sha256(
                    (payload_ru + "\n" + payload_en).encode()
                ).hexdigest(),
            }
        )
    return rows


def localization_rows(snapshots, minimum=10000):
    """Rows for hearthstone_card_localizations, keyed by locale.

    Publication gate: every locale must be a complete snapshot with exactly the
    id -> dbfId identities of enUS; otherwise nothing is published.
    """
    missing = [locale for locale in LOCALES if locale not in snapshots]
    if missing:
        raise ValueError("Missing locale snapshots: " + ", ".join(missing))
    indexes = {l: index_snapshot(snapshots[l], minimum) for l in LOCALES}
    reference = {card_id: card["dbfId"] for card_id, card in indexes["enUS"].items()}
    result = {}
    for locale in LOCALES:
        identities = {card_id: card["dbfId"] for card_id, card in indexes[locale].items()}
        if identities != reference:
            raise ValueError(
                f"Locale {locale} has {len(identities)} ids, enUS has {len(reference)}, "
                "or their id/dbf identities differ"
            )
        rows = []
        for card_id, card in sorted(indexes[locale].items()):
            payload = canonical(card)
            name = card.get("name")
            rows.append(
                {
                    "card_id": card_id,
                    "locale": locale,
                    "dbf": card["dbfId"],
                    "name": name if isinstance(name, str) else None,
                    "payload": payload,
                    "source_hash": hashlib.sha256(payload.encode()).hexdigest(),
                }
            )
        result[locale] = rows
    return result


def plan(rows, existing):
    return {
        "insert": sum(row["card_id"] not in existing for row in rows),
        "update": sum(
            row["card_id"] in existing
            and existing[row["card_id"]] != row["source_hash"]
            for row in rows
        ),
        "unchanged": sum(
            existing.get(row["card_id"]) == row["source_hash"] for row in rows
        ),
    }


def plan_localizations(rows_by_locale, existing):
    return {
        locale: plan(rows, existing.get(locale, {}))
        for locale, rows in rows_by_locale.items()
    }


def read_localizations(cursor):
    try:
        cursor.execute(
            "SELECT card_id, locale, dbf, source_hash FROM hearthstone_card_localizations"
        )
        fetched = cursor.fetchall()
    except Exception as error:
        if error.args and error.args[0] == 1146:
            raise RuntimeError(
                "Table hearthstone_card_localizations is missing: apply "
                "panel/sql/card_localizations.sql first (the loader never creates tables)"
            ) from error
        raise
    existing, identities = {}, {}
    for row in fetched:
        existing.setdefault(row["locale"], {})[row["card_id"]] = row["source_hash"]
        identities[(row["locale"], row["dbf"])] = row["card_id"]
    return existing, identities


LOCALIZATION_COLUMNS = ("card_id", "locale", "dbf", "name", "payload", "source_hash")


def publish(connection, rows, localizations=None):
    """Write the catalog and, optionally, locale rows in a single transaction.

    Only locale rows that are new or changed are sent: with an unchanged HSJ
    snapshot a refresh writes nothing to hearthstone_card_localizations.
    """
    columns = tuple(rows[0])
    names = ",".join(columns)
    values = ",".join("%(" + key + ")s" for key in columns)
    updates = ",".join(
        key + "=VALUES(" + key + ")" for key in columns if key != "card_id"
    )
    # Evaluate the hash comparison before assigning source_hash; unchanged rows
    # keep their timestamp so conditional HTTP requests remain useful.
    sql = (
        f"INSERT INTO hearthstone_card_catalog ({names}) VALUES ({values}) "
        "ON DUPLICATE KEY UPDATE updated_at=IF(source_hash<>VALUES(source_hash),CURRENT_TIMESTAMP,updated_at),"
        + updates
    )
    loc_sql = (
        "INSERT INTO hearthstone_card_localizations ("
        + ",".join(LOCALIZATION_COLUMNS)
        + ") VALUES ("
        + ",".join("%(" + key + ")s" for key in LOCALIZATION_COLUMNS)
        + ") ON DUPLICATE KEY UPDATE "
        "updated_at=IF(source_hash<>VALUES(source_hash),CURRENT_TIMESTAMP,updated_at),"
        + ",".join(
            key + "=VALUES(" + key + ")"
            for key in LOCALIZATION_COLUMNS
            if key not in ("card_id", "locale")
        )
    )
    connection.begin()
    try:
        with connection.cursor() as cursor:
            for offset in range(0, len(rows), 200):
                cursor.executemany(sql, rows[offset : offset + 200])
            for locale_rows in (localizations or {}).values():
                for offset in range(0, len(locale_rows), 200):
                    cursor.executemany(loc_sql, locale_rows[offset : offset + 200])
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def execute(
    connection,
    rows,
    localizations,
    *,
    apply,
    refresh,
    expect_total,
    min_published=MIN_PUBLISHED,
):
    """Plan (and with apply=True publish) the catalog and the 4 locale tables."""
    if refresh and not apply:
        raise ValueError("--refresh requires --apply")
    if apply and not refresh and expect_total != len(rows):
        raise ValueError("--apply requires --expect-total matching the dry-run snapshot")
    with connection.cursor() as cursor:
        cursor.execute("SELECT card_id, dbf, source_hash FROM hearthstone_card_catalog")
        published = cursor.fetchall()
        existing = {row["card_id"]: row["source_hash"] for row in published}
        identities = {row["dbf"]: row["card_id"] for row in published}
        existing_loc, loc_identities = read_localizations(cursor)
    if any(
        row["dbf"] in identities and identities[row["dbf"]] != row["card_id"]
        for row in rows
    ):
        raise ValueError("Published dbf identity conflicts with the new snapshot")
    if any(
        loc_identities.get((row["locale"], row["dbf"]), row["card_id"]) != row["card_id"]
        for locale_rows in localizations.values()
        for row in locale_rows
    ):
        raise ValueError(
            "Published locale dbf identity conflicts with the new snapshot"
        )
    if refresh:
        if (
            len(existing) < min_published
            or len(set(existing) & {r["card_id"] for r in rows})
            < len(existing) * MIN_KEPT_SHARE
        ):
            raise ValueError(
                "Scheduled refresh requires at least 95% of the published catalog"
            )
        if not existing_loc:
            raise ValueError(
                "hearthstone_card_localizations is empty: run the initial "
                "--apply --expect-total COUNT first (refresh needs published locales)"
            )
        for locale, locale_rows in localizations.items():
            published_ids = existing_loc.get(locale, {})
            kept = len(published_ids.keys() & {r["card_id"] for r in locale_rows})
            if len(published_ids) < min_published or kept < len(published_ids) * MIN_KEPT_SHARE:
                raise ValueError(
                    f"Scheduled refresh requires at least 95% of the published "
                    f"{locale} localizations ({kept} of {len(published_ids)} kept)"
                )
    result = {
        "total": len(rows),
        **plan(rows, existing),
        "localizations": plan_localizations(localizations, existing_loc),
        "applied": apply,
    }
    if apply:
        changed = {
            locale: [
                row
                for row in locale_rows
                if existing_loc.get(locale, {}).get(row["card_id"]) != row["source_hash"]
            ]
            for locale, locale_rows in localizations.items()
        }
        publish(connection, rows, changed)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for locale in LOCALES:
        parser.add_argument(f"--hsj-{locale[:2].lower()}", dest=f"hsj_{locale}")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-total", type=int)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Guarded refresh of an already published catalog",
    )
    args = parser.parse_args()
    if args.refresh and not args.apply:
        parser.error("--refresh requires --apply")
    snapshots = {
        locale: load_snapshot(locale, getattr(args, f"hsj_{locale}"))
        for locale in LOCALES
    }
    rows = catalog_rows(snapshots["ruRU"], snapshots["enUS"])
    localizations = localization_rows(snapshots)
    if args.apply and not args.refresh and args.expect_total != len(rows):
        parser.error("--apply requires --expect-total matching the dry-run snapshot")
    connection = connect_db(load_php_config())
    try:
        result = execute(
            connection,
            rows,
            localizations,
            apply=args.apply,
            refresh=args.refresh,
            expect_total=args.expect_total,
        )
        print(json.dumps(result))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
