#!/usr/bin/env python3
"""Fill verified catalog gaps, without changing live formats or pool gates.

Default: a read-only plan. --apply commits one transaction; install version
capture first. HSJ is a free source; this command never invokes a paid provider.
Run under the same global sync lock as the other catalog jobs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.request

from catalog_card_text import repair_texts
from catalog_integrity import (archival_bg_bases, bartender_supplements, bg_skin_variants, library_supplements,
                               merge_embedded_skins, payload_json, skin_variants, timewarped_supplements)
from card_history_schema import identifier
from sync_constructed_cards import connect_db, load_php_config, stable_hash
from wiki_http import fetch_bytes

BARTENDER_DDL = """CREATE TABLE IF NOT EXISTS battlegrounds_bartender_skins (
 card_id VARCHAR(96) PRIMARY KEY, dbf INT UNSIGNED UNIQUE,
 parent_card_id VARCHAR(96) NOT NULL, name_en VARCHAR(255) NOT NULL,
 name_ru VARCHAR(255) NULL, image_url VARCHAR(512) NULL,
 status VARCHAR(32) NOT NULL DEFAULT 'catalog_only', source VARCHAR(80) NOT NULL,
 source_payload_json LONGTEXT CHECK (JSON_VALID(source_payload_json)), source_hash CHAR(64),
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


def upstream(locale, path=None):
    if path:
        return json.loads(Path(path).read_text())
    request = urllib.request.Request("https://api.hearthstonejson.com/v1/latest/" + locale + "/cards.json")
    return json.loads(fetch_bytes(request))


def build_plan(snapshot, ru, en):
    skins, unresolved_skins = skin_variants(snapshot['hero_skins'], en)
    media = {row['skin_card_id']: row.get('image_url') for row in snapshot['battlegrounds_hero_skins']}
    bg_skins, unresolved_bg = bg_skin_variants(snapshot['battlegrounds_hero_skins'], snapshot['battlegrounds_heroes'], en, media.get)
    # Titles are localized independently of shared image identities.
    ru_by_id = {x['id']: x for x in ru}
    for row in bg_skins:
        row['title'] = ru_by_id.get(row['skin_card_id'], {}).get('name') or row['title']
    bartenders = bartender_supplements(snapshot.get('battlegrounds_bartender_skins', []), ru, en)
    archival_bases, unresolved_bases = archival_bg_bases(snapshot.get('battlegrounds_cards', []), ru, en)
    known_bartenders = {row['card_id'] for row in bartenders + snapshot.get('battlegrounds_bartender_skins', [])}
    repairs = []
    en_by_id = {x['id']: x for x in en}
    for row in snapshot['constructed_cards']:
        card_id = row['card_id']
        new_ru, new_en, evidence = repair_texts(card_id, row.get('text_ru'), row.get('text_en'),
                    ru_by_id.get(card_id, {}).get('text'), en_by_id.get(card_id, {}).get('text'))
        if evidence:
            repairs.append({'card_id': card_id, 'text_ru': new_ru, 'text_en': new_en, 'evidence': evidence})
    return {'hero_skins': skins, 'bg_skins': bg_skins, 'bartenders': bartenders,
            'archival_bases': archival_bases,
            'libraries': library_supplements(snapshot['battlegrounds_library_cards'], ru, en),
            'timewarped': timewarped_supplements(snapshot['battlegrounds_timewarped_cards'], ru, en),
            'text_repairs': repairs, 'unresolved': {'hero_skins': unresolved_skins, 'bg_bases': unresolved_bases,
                            'bg_skins': [x for x in unresolved_bg if x not in known_bartenders]}}


def read_snapshot(connection):
    tables = ['hero_skins', 'battlegrounds_hero_skins', 'battlegrounds_heroes',
              'battlegrounds_library_cards', 'battlegrounds_timewarped_cards', 'constructed_cards', 'battlegrounds_cards']
    snapshot = {}
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute('SELECT * FROM ' + identifier(table))
            snapshot[table] = list(cursor.fetchall())
        cursor.execute("SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='battlegrounds_bartender_skins'")
        if cursor.fetchone()['n']:
            cursor.execute('SELECT * FROM battlegrounds_bartender_skins')
            snapshot['battlegrounds_bartender_skins'] = list(cursor.fetchall())
    return snapshot


def insert_rows(cursor, table, rows):
    cursor.execute('SHOW COLUMNS FROM ' + identifier(table))
    columns = {row['Field'] for row in cursor.fetchall()}
    for row in rows:
        keys = [key for key in row if key in columns and key != 'id']
        cursor.execute('INSERT INTO ' + identifier(table) + ' (' + ','.join(map(identifier, keys)) + ') VALUES ('
                       + ','.join(['%s'] * len(keys)) + ')', [row[key] for key in keys])


def apply_plan(connection, plan):
    # DDL is deliberately outside this transaction and must be prepared before
    # deploying; the data repair itself is atomic and records every change.
    connection.begin()
    try:
        with connection.cursor() as cursor:
            for table, key in [('hero_skins','hero_skins'), ('battlegrounds_hero_skins','bg_skins'),
                ('battlegrounds_bartender_skins','bartenders'), ('battlegrounds_library_cards','libraries'),
                ('battlegrounds_timewarped_cards','timewarped'), ('battlegrounds_cards','archival_bases')]:
                insert_rows(cursor, table, plan[key])
            for base in plan['archival_bases']:
                cursor.execute("UPDATE battlegrounds_cards SET base_card_id=%s WHERE variant_kind='golden' AND base_dbf=%s AND base_card_id IS NULL", (base['card_id'], base['dbf']))
            by_hero = {}
            for row in plan['bg_skins']:
                by_hero.setdefault(row['hero_card_id'], []).append(row)
            for card_id, additions in by_hero.items():
                cursor.execute('SELECT hero_skins_json FROM battlegrounds_heroes WHERE card_id=%s FOR UPDATE', (card_id,))
                old = json.loads(cursor.fetchone()['hero_skins_json'] or '[]')
                merged = merge_embedded_skins(old, additions)
                cursor.execute('UPDATE battlegrounds_heroes SET hero_skins_json=%s WHERE card_id=%s', (payload_json(merged), card_id))
            for repair in plan['text_repairs']:
                cursor.execute('SELECT source_payload_json FROM constructed_cards WHERE card_id=%s FOR UPDATE', (repair['card_id'],))
                payload = json.loads(cursor.fetchone()['source_payload_json'] or '{}')
                payload['text_repair'] = repair['evidence']
                cursor.execute('UPDATE constructed_cards SET text_ru=%s,text_en=%s,source_payload_json=%s,source_hash=%s,changed_at=CURRENT_TIMESTAMP WHERE card_id=%s',
                        (repair['text_ru'], repair['text_en'], payload_json(payload), stable_hash(payload), repair['card_id']))
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--hsj-ru')
    parser.add_argument('--hsj-en')
    parser.add_argument('--snapshot', help='Offline catalog snapshot for planning only')
    args = parser.parse_args()
    if args.apply and args.snapshot:
        parser.error('Offline snapshots cannot be applied')
    ru, en = upstream('ruRU', args.hsj_ru), upstream('enUS', args.hsj_en)
    connection = None
    try:
        if args.snapshot:
            snapshot = json.loads(Path(args.snapshot).read_text())
        else:
            connection = connect_db(load_php_config())
            snapshot = read_snapshot(connection)
        plan = build_plan(snapshot, ru, en)
        if args.apply:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS n FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME='cv1_constructed_cards_update'")
                if not cursor.fetchone()['n']:
                    raise RuntimeError('Install card history before repairing the catalog')
            apply_plan(connection, plan)
        print(json.dumps({'applied': args.apply, 'counts': {key: len(value) for key, value in plan.items() if key != 'unresolved'}, 'unresolved': plan['unresolved']}, ensure_ascii=False))
    finally:
        if connection:
            connection.close()


if __name__ == '__main__':
    main()
