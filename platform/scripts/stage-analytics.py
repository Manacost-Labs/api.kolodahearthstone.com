#!/usr/bin/env python3
"""Stage changed public SQLite primary-key ranges; never mutate canonical rows.

Checkpoints are committed by merge-analytics.sql in the catalogue/analytics
transaction. A consistent read snapshot detects corrections anywhere in history.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import tempfile
import time
import uuid

SOURCE = Path('/srv/hs-data-api/data/hs_parses.db')
PLATFORM = Path(__file__).resolve().parents[1]
BUCKET_WIDTH = 8192
ENCODING_VERSION = 1
TABLES = (
    'archetype_deck_cards', 'archetype_decks', 'archetype_matchups',
    'archetype_mulligan', 'archetype_refresh_runs', 'archetype_snapshots',
    'archetype_time_series', 'bg_minion_refresh_runs', 'bg_minion_round_stats',
    'bg_minion_snapshots', 'bg_minions', 'card_popularity_history', 'decks',
    'fetch_log', 'hearthstone_patches', 'hsguru_archetype_history', 'hsreplay_archetypes',
)
APPEND_ONLY = frozenset((
    'archetype_deck_cards', 'archetype_decks', 'archetype_matchups',
    'archetype_mulligan', 'archetype_snapshots', 'archetype_time_series',
    'bg_minion_round_stats', 'bg_minion_snapshots', 'card_popularity_history',
    'hsguru_archetype_history',
))


def identifier(value):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value):
        raise ValueError('Unexpected database identifier')
    return '"' + value + '"'


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def application_name():
    value = os.environ.get('HS_DATA_SYNC_APPLICATION_NAME', '')
    if not value:
        return 'hsdp-manual'
    if not re.fullmatch(r'hsdp-sync-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', value):
        raise ValueError('Invalid scheduled synchronization application name')
    return value


def target_command():
    compose = PLATFORM / 'postgres'
    return ['sudo', '-n', 'docker', 'compose', '--project-directory', str(compose),
            '-f', str(compose / 'docker-compose.yml'), 'exec', '-e',
            'PGAPPNAME=' + application_name(), '-T', 'postgres', 'sh', '-eu', '-c',
            'psql -X -qAt -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -U "$POSTGRES_USER" -d hs_data']


def query(command, sql):
    result = subprocess.run(command, input=sql, text=True, capture_output=True, timeout=120)
    if result.returncode:
        # COPY errors can include complete row values. Never echo database stderr.
        raise RuntimeError('PostgreSQL analytics preflight failed')
    return result.stdout.strip()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def manifest_digest(chunks):
    return digest([[bucket, record['row_count'], record['digest']]
                   for bucket, record in sorted(chunks.items())])


def target_metadata(command):
    names = ','.join(literal(t) for t in TABLES)
    metadata = json.loads(query(command, f"""
BEGIN READ ONLY;
SET LOCAL statement_timeout = '60s';
SELECT coalesce(jsonb_object_agg(t.table_name, to_jsonb(t)), '{{}}') FROM (
    SELECT c.relname AS table_name, c.oid::bigint AS target_oid,
           pg_relation_filenode(c.oid)::bigint AS target_relfilenode,
           (SELECT jsonb_agg(jsonb_build_object('name',a.attname,'type',ty.typname,
                       'typmod',a.atttypmod,'not_null',a.attnotnull) ORDER BY a.attnum)
              FROM pg_attribute a JOIN pg_type ty ON ty.oid=a.atttypid
             WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns,
           (SELECT jsonb_agg(a.attname ORDER BY k.position)
              FROM pg_constraint p CROSS JOIN LATERAL unnest(p.conkey)
                WITH ORDINALITY k(number,position)
              JOIN pg_attribute a ON a.attrelid=p.conrelid AND a.attnum=k.number
             WHERE p.conrelid=c.oid AND p.contype='p') AS primary_keys
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='analytics' AND c.relkind='r' AND c.relname IN ({names})
) t;
COMMIT;
"""))
    if set(metadata) != set(TABLES):
        raise RuntimeError('An expected analytics target table is missing')
    count_selects = [f'SELECT {literal(t)} AS table_name, count(*) AS target_rows '
                     f'FROM analytics.{identifier(t)}' for t in TABLES]
    counts = json.loads(query(command, "BEGIN READ ONLY; SET LOCAL statement_timeout='60s';\n"
                             + 'SELECT jsonb_object_agg(table_name,target_rows) FROM ('
                             + ' UNION ALL '.join(count_selects) + ') t; COMMIT;'))
    for table in TABLES:
        metadata[table]['target_rows'] = counts[table]
    state_exists, chunks_exist = json.loads(query(command,
        "SELECT jsonb_build_array(to_regclass('platform.analytics_shadow_state') IS NOT NULL, "
        "to_regclass('platform.analytics_shadow_chunks') IS NOT NULL);"))
    if not state_exists and not chunks_exist:
        return metadata, {}, {}
    # Preserve a surviving generation if only the chunk table was lost. The
    # merge must compare with that generation while rebuilding missing chunks.
    state_select = ("SELECT coalesce(jsonb_object_agg(table_name,to_jsonb(t)), '{}') "
                    "FROM platform.analytics_shadow_state t;" if state_exists else "SELECT '{}'::jsonb;")
    chunks_select = ("SELECT coalesce(jsonb_agg(t), '[]') FROM platform.analytics_shadow_chunks t;"
                     if chunks_exist else "SELECT '[]'::jsonb;")
    records = query(command, 'BEGIN READ ONLY;\n' + state_select + '\n' + chunks_select + '\nCOMMIT;').splitlines()
    states = json.loads(records[0])
    chunks = {t: {} for t in TABLES}
    for record in json.loads(records[1]):
        if record['table_name'] in chunks:
            chunks[record['table_name']][record['bucket']] = record
    return metadata, states, chunks


def copy_cell(value, column_type):
    if value is None:
        return None
    if column_type == 'jsonb':
        if not isinstance(value, str):
            raise ValueError('JSON source cell must be text')
        # Preserve the original JSON numeric precision. PostgreSQL validates it.
        return value if value.strip() else None
    if column_type == 'int8':
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and re.fullmatch(r'[+-]?[0-9]+', value):
            number = int(value)
        else:
            raise ValueError('Invalid integer source cell')
        if not -(2**63) <= number < 2**63:
            raise ValueError('Integer source cell is out of range')
        return str(number)
    if column_type == 'float4':
        number = struct.unpack('!f', struct.pack('!f', float(value)))[0]
        if math.isnan(number):
            return 'NaN'
        if math.isinf(number):
            return 'Infinity' if number > 0 else '-Infinity'
        return repr(number)
    if column_type == 'text' and not isinstance(value, bytes):
        return str(value)
    raise ValueError('Unsupported source cell or destination type')


def copy_line(values):
    encoded = []
    for value in values:
        if value is None:
            encoded.append(r'\N')
            continue
        value = str(value)
        if '\0' in value:
            raise ValueError('NUL is not supported by PostgreSQL text')
        # COPY text avoids ambiguity between NULL/empty strings and keeps every
        # record on one physical line, including a literal psql end marker.
        encoded.append(value.replace('\\', '\\\\').replace('\t', '\\t')
                       .replace('\n', '\\n').replace('\r', '\\r')
                       .replace('\b', '\\b').replace('\f', '\\f').replace('\v', '\\v'))
    return ('\t'.join(encoded) + '\n').encode()


class Writer:
    def __init__(self, command):
        self.stdout = tempfile.TemporaryFile()
        self.stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=self.stdout, stderr=self.stderr)

    def write(self, value):
        self.process.stdin.write(value.encode() if isinstance(value, str) else value)

    def finish(self):
        self.process.stdin.close()
        if self.process.wait(timeout=900):
            raise RuntimeError('PostgreSQL analytics staging failed; transaction not published')

    def close(self):
        if not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except BrokenPipeError:
                pass
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.stdout.close()
        self.stderr.close()


def scan_table(connection, table, meta, source_identity, previous, old_chunks, full):
    quoted = identifier(table)
    source_columns = connection.execute(f'PRAGMA table_info({quoted})').fetchall()
    names = [c[1] for c in source_columns]
    keys = [c[1] for c in sorted(source_columns, key=lambda c: c[5]) if c[5]]
    if names != [c['name'] for c in meta['columns']] or not keys or keys != meta['primary_keys']:
        raise RuntimeError('Source/target column or primary-key mismatch: ' + table)
    if any(c['type'] not in ('int8', 'float4', 'text', 'jsonb') for c in meta['columns']):
        raise RuntimeError('Unsupported target column type: ' + table)
    for name in names:
        identifier(name)
    first = names.index(keys[0])
    bucket_column = keys[0] if meta['columns'][first]['type'] == 'int8' else None
    order = ','.join(identifier(k) for k in keys)
    shape = digest([ENCODING_VERSION, BUCKET_WIDTH, source_columns, meta['columns'], keys])
    old_valid = bool(previous and previous['manifest_hash'] == manifest_digest(old_chunks)
                     and previous['source_rows'] == sum(c['row_count'] for c in old_chunks.values()))
    force = full or not old_valid or any(previous.get(k) != value for k, value in (
        ('source_identity', source_identity), ('shape_hash', shape),
        ('target_oid', meta['target_oid']), ('target_relfilenode', meta['target_relfilenode']),
        ('target_rows', meta['target_rows']),
    ))
    chunks = {}
    current_bucket = None
    hasher = None
    count = 0
    for row in connection.execute(f'SELECT * FROM {quoted} ORDER BY {order}'):
        if bucket_column is not None and not isinstance(row[first], int):
            raise RuntimeError('Invalid integer primary key: ' + table)
        bucket = row[first] // BUCKET_WIDTH if bucket_column is not None else 0
        if bucket != current_bucket:
            if hasher is not None:
                chunks[current_bucket] = {'row_count': count, 'digest': hasher.hexdigest()}
            current_bucket, hasher, count = bucket, hashlib.sha256(), 0
        hasher.update(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode())
        hasher.update(b'\n')
        count += 1
    if hasher is not None:
        chunks[current_bucket] = {'row_count': count, 'digest': hasher.hexdigest()}
    changed = set(chunks) if force else {
        bucket for bucket in set(chunks) | set(old_chunks)
        if chunks.get(bucket, {}).get('digest') != old_chunks.get(bucket, {}).get('digest')
        or chunks.get(bucket, {}).get('row_count') != old_chunks.get(bucket, {}).get('row_count')
    }
    return {
        'table_name': table, 'source_identity': source_identity, 'shape_hash': shape,
        'source_rows': sum(c['row_count'] for c in chunks.values()),
        'target_oid': meta['target_oid'], 'target_relfilenode': meta['target_relfilenode'],
        'target_rows_before': meta['target_rows'], 'bucket_column': bucket_column,
        'bucket_width': BUCKET_WIDTH, 'append_only': table in APPEND_ONLY,
        'base_generation': previous.get('generation') if previous else None,
        'full_reconcile': force, 'rows_staged': sum(chunks[b]['row_count'] for b in changed if b in chunks),
        'chunks_changed': len(changed), 'manifest_hash': manifest_digest(chunks),
        'chunks': chunks, 'changed': changed, 'columns': meta['columns'], 'primary_keys': keys,
    }


STAGE_DDL = """
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '12min';
CREATE SCHEMA analytics_stage;
CREATE SCHEMA analytics_sync_stage;
REVOKE ALL ON SCHEMA analytics_stage, analytics_sync_stage FROM PUBLIC;
CREATE UNLOGGED TABLE analytics_sync_stage.tables (
    table_name text PRIMARY KEY, source_identity text NOT NULL, shape_hash text NOT NULL,
    source_rows bigint NOT NULL, target_oid bigint NOT NULL, target_relfilenode bigint NOT NULL,
    target_rows_before bigint NOT NULL, bucket_column text, bucket_width bigint NOT NULL,
    append_only boolean NOT NULL, generation text NOT NULL, base_generation text,
    full_reconcile boolean NOT NULL, rows_staged bigint NOT NULL,
    chunks_changed bigint NOT NULL, manifest_hash text NOT NULL,
    columns jsonb NOT NULL, primary_keys jsonb NOT NULL
);
CREATE UNLOGGED TABLE analytics_sync_stage.chunks (
    table_name text, bucket bigint CHECK (bucket BETWEEN -1125899906842624 AND 1125899906842623),
    row_count bigint NOT NULL CHECK (row_count > 0), digest text NOT NULL,
    PRIMARY KEY(table_name,bucket)
);
CREATE UNLOGGED TABLE analytics_sync_stage.changed_chunks (
    table_name text, bucket bigint CHECK (bucket BETWEEN -1125899906842624 AND 1125899906842623),
    PRIMARY KEY(table_name,bucket)
);
"""


def stage_analytics(source: Path, pg_command: list[str], full: bool = False) -> dict:
    started = time.monotonic()
    source = Path(source).resolve(strict=True)
    before = source.stat()
    metadata, states, old_chunks = target_metadata(pg_command)
    connection = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)
    writer = None
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA cache_size=-8192')
        connection.execute('BEGIN')
        # Pin the read snapshot before inspecting schema or hashing any table.
        connection.execute('SELECT count(*) FROM sqlite_schema').fetchone()
        after = source.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise RuntimeError('SQLite source was replaced during snapshot opening')
        identity = f'{source}:{before.st_dev}:{before.st_ino}'
        plans = [scan_table(connection, t, metadata[t], identity, states.get(t),
                            old_chunks.get(t, {}), full) for t in TABLES]
        generation = str(uuid.uuid4())
        writer = Writer(pg_command)
        writer.write(STAGE_DDL)
        writer.write('COPY analytics_sync_stage.tables FROM STDIN;\n')
        for p in plans:
            writer.write(copy_line([p[k] for k in (
                'table_name', 'source_identity', 'shape_hash', 'source_rows', 'target_oid',
                'target_relfilenode', 'target_rows_before', 'bucket_column', 'bucket_width')]
                + ['t' if p['append_only'] else 'f', generation, p['base_generation'],
                   't' if p['full_reconcile'] else 'f', p['rows_staged'], p['chunks_changed'], p['manifest_hash'],
                   json.dumps(p['columns']), json.dumps(p['primary_keys'])]))
        writer.write('\\.\nCOPY analytics_sync_stage.chunks FROM STDIN;\n')
        for p in plans:
            for bucket, chunk in sorted(p['chunks'].items()):
                writer.write(copy_line([p['table_name'], bucket, chunk['row_count'], chunk['digest']]))
        writer.write('\\.\nCOPY analytics_sync_stage.changed_chunks FROM STDIN;\n')
        for p in plans:
            for bucket in sorted(p['changed']):
                writer.write(copy_line([p['table_name'], bucket]))
        writer.write('\\.\n')
        for p in plans:
            table = identifier(p['table_name'])
            columns = ','.join(identifier(c['name']) for c in p['columns'])
            order = ','.join(identifier(k) for k in p['primary_keys'])
            writer.write(f'CREATE UNLOGGED TABLE analytics_stage.{table} (LIKE analytics.{table});\n')
            writer.write(f'COPY analytics_stage.{table} ({columns}) FROM STDIN;\n')
            copied = 0
            # Re-read changed ranges in the SAME SQLite snapshot used for hashes.
            for bucket in sorted(p['changed'] & set(p['chunks'])):
                where, parameters = '', []
                if p['bucket_column'] is not None:
                    key = identifier(p['bucket_column'])
                    lower = bucket * BUCKET_WIDTH
                    where = f' WHERE {key} >= ? AND {key} < ?'
                    parameters = [lower, min(lower + BUCKET_WIDTH, 2**63 - 1)]
                    if lower + BUCKET_WIDTH >= 2**63:
                        where = f' WHERE {key} >= ?'; parameters = [lower]
                for row in connection.execute(f'SELECT * FROM {table}{where} ORDER BY {order}', parameters):
                    writer.write(copy_line([copy_cell(v, c['type']) for v, c in zip(row, p['columns'])]))
                    copied += 1
            if copied != p['rows_staged']:
                raise RuntimeError('Inconsistent source range count: ' + p['table_name'])
            writer.write('\\.\n')
            writer.write(f'CREATE UNIQUE INDEX ON analytics_stage.{table} ({order});\n')
            writer.write(f'ANALYZE analytics_stage.{table};\n')
        writer.write('ANALYZE analytics_sync_stage.tables;\nANALYZE analytics_sync_stage.changed_chunks;\nCOMMIT;\n')
        writer.finish()
        return {'event': 'analytics_stage_completed', 'rows_read': sum(p['source_rows'] for p in plans),
                'rows_staged': sum(p['rows_staged'] for p in plans),
                'chunks_changed': sum(p['chunks_changed'] for p in plans),
                'seconds': round(time.monotonic() - started, 3),
                'tables': [{k: p[k] for k in ('table_name', 'source_rows', 'rows_staged',
                                             'chunks_changed', 'full_reconcile')} for p in plans]}
    finally:
        if writer is not None:
            writer.close()
        connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='Reconcile all public source rows for recovery')
    parser.add_argument('--list-tables', action='store_true', help='Print the incremental synchronization contract without database access')
    args = parser.parse_args()
    if args.list_tables:
        print(json.dumps(list(TABLES)))
        raise SystemExit(0)
    try:
        print(json.dumps(stage_analytics(SOURCE, target_command(), args.full)), flush=True)
    except Exception as error:
        print(json.dumps({'event': 'analytics_stage_failed', 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(1)
