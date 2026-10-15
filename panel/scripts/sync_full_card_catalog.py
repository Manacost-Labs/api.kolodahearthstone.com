#!/usr/bin/env python3
"""Publish the complete free HSJSON catalog without changing live format pools.

Default is read-only. Prepare full_card_catalog.sql separately, then use --apply
with the dry-run total. Run under the panel's existing global sync lock.
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


def catalog_rows(ru, en, minimum=10000):
    def index(payload):
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

    ru_index, en_index = index(ru), index(en)
    if ru_index.keys() != en_index.keys():
        raise ValueError("Locale snapshots have different identities")
    rows = []
    for card_id, english in sorted(en_index.items()):
        russian = ru_index[card_id]
        if russian["dbfId"] != english["dbfId"]:
            raise ValueError("Locale dbf identity mismatch")
        payload_ru = json.dumps(
            russian, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        payload_en = json.dumps(
            english, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
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


def publish(connection, rows):
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
    connection.begin()
    try:
        with connection.cursor() as cursor:
            for offset in range(0, len(rows), 200):
                cursor.executemany(sql, rows[offset : offset + 200])
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hsj-ru")
    parser.add_argument("--hsj-en")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-total", type=int)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Guarded refresh of an already published catalog",
    )
    args = parser.parse_args()
    rows = catalog_rows(
        load_snapshot("ruRU", args.hsj_ru), load_snapshot("enUS", args.hsj_en)
    )
    if args.refresh and not args.apply:
        parser.error("--refresh requires --apply")
    if args.apply and not args.refresh and args.expect_total != len(rows):
        parser.error("--apply requires --expect-total matching the dry-run snapshot")
    connection = connect_db(load_php_config())
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT card_id, source_hash FROM hearthstone_card_catalog")
            existing = {row["card_id"]: row["source_hash"] for row in cursor.fetchall()}
            cursor.execute("SELECT card_id, dbf FROM hearthstone_card_catalog")
            identities = {row["dbf"]: row["card_id"] for row in cursor.fetchall()}
        if any(
            row["dbf"] in identities and identities[row["dbf"]] != row["card_id"]
            for row in rows
        ):
            raise ValueError("Published dbf identity conflicts with the new snapshot")
        if args.refresh and (
            len(existing) < 10000
            or len(set(existing) & {r["card_id"] for r in rows}) < len(existing) * 0.95
        ):
            raise ValueError(
                "Scheduled refresh requires at least 95% of the published catalog"
            )
        result = {"total": len(rows), **plan(rows, existing), "applied": args.apply}
        if args.apply:
            publish(connection, rows)
        print(json.dumps(result))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
