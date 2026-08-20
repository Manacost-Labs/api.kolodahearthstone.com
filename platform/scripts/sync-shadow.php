#!/usr/bin/env php
<?php
declare(strict_types=1);

const PLATFORM_DIR = __DIR__ . '/..';

function runProcess(array $command, string $stdin = ''): string
{
    $process = proc_open(
        $command,
        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']],
        $pipes,
        null,
        null,
        ['bypass_shell' => true],
    );
    if (!is_resource($process)) {
        throw new RuntimeException('Unable to start shadow synchronization process');
    }
    fwrite($pipes[0], $stdin);
    fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1]);
    $stderr = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    $exitCode = proc_close($process);
    if ($exitCode !== 0) {
        throw new RuntimeException(trim((string)$stderr) ?: 'Shadow synchronization failed');
    }
    return trim((string)$stdout . PHP_EOL . (string)$stderr);
}

$lock = fopen('/tmp/hs-data-platform-shadow-sync.lock', 'c');
if ($lock === false || !flock($lock, LOCK_EX | LOCK_NB)) {
    fwrite(STDOUT, 'Another shadow synchronization is already running.' . PHP_EOL);
    exit(0);
}

try {
    $composeDir = PLATFORM_DIR . '/postgres';
    runProcess(
        [
            '/usr/bin/sudo', '-n', 'docker', 'compose',
            '--project-directory', $composeDir,
            '-f', $composeDir . '/docker-compose.yml',
            'exec', '-T', 'postgres', 'sh', '-eu', '-c',
            'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data',
        ],
        "DROP SCHEMA IF EXISTS catalog_stage CASCADE;\n" .
        "DROP SCHEMA IF EXISTS analytics_stage CASCADE;\n",
    );

    $stageOutput = runProcess([PHP_BINARY, __DIR__ . '/bootstrap-shadow.php', 'stage']);
    if ($stageOutput !== '') {
        fwrite(STDOUT, $stageOutput . PHP_EOL);
    }

    $mergeSql = <<<'SQL'
BEGIN;
SET LOCAL session_replication_role = replica;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15min';

DO $json_columns$
DECLARE
    column_record record;
BEGIN
    FOR column_record IN
        SELECT table_schema, table_name, column_name
          FROM information_schema.columns
         WHERE table_schema IN ('catalog_stage', 'analytics_stage')
           AND data_type IN ('text', 'character varying', 'character')
           AND (
               column_name LIKE '%\_json' ESCAPE '\'
               OR column_name IN ('raw_json', 'raw_card_json', 'raw_deck_list', 'raw_deck_sideboard')
           )
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I ALTER COLUMN %I DROP DEFAULT',
            column_record.table_schema, column_record.table_name, column_record.column_name
        );
        EXECUTE format(
            'ALTER TABLE %I.%I ALTER COLUMN %I TYPE jsonb USING '
            'CASE WHEN %I IS NULL OR btrim(%I) = '''' THEN NULL ELSE %I::jsonb END',
            column_record.table_schema, column_record.table_name, column_record.column_name,
            column_record.column_name, column_record.column_name, column_record.column_name
        );
    END LOOP;
END
$json_columns$;

DO $merge$
DECLARE
    source_schema text;
    target_schema text;
    table_record record;
    source_columns text[];
    target_columns text[];
    source_types text[];
    target_types text[];
    column_list text;
    primary_keys text[];
    conflict_list text;
    update_list text;
    update_condition text;
    key_condition text;
    source_count bigint;
    target_count bigint;
    append_only_tables text[] := ARRAY[
        'analytics.archetype_deck_cards',
        'analytics.archetype_decks',
        'analytics.archetype_matchups',
        'analytics.archetype_mulligan',
        'analytics.archetype_snapshots',
        'analytics.archetype_time_series',
        'analytics.bg_minion_round_stats',
        'analytics.bg_minion_snapshots',
        'analytics.card_popularity_history',
        'analytics.hsguru_archetype_history'
    ];
    monotonic_id_tables text[] := ARRAY[
        'analytics.archetype_decks',
        'analytics.archetype_snapshots',
        'analytics.bg_minion_snapshots',
        'analytics.card_popularity_history',
        'analytics.hsguru_archetype_history'
    ];
    authoritative_replace_tables text[] := ARRAY[
        'catalog.battlegrounds_card_wiki_related'
    ];
BEGIN
    FOREACH source_schema IN ARRAY ARRAY['catalog_stage', 'analytics_stage'] LOOP
        target_schema := replace(source_schema, '_stage', '');
        FOR table_record IN
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = source_schema
               AND table_type = 'BASE TABLE'
             ORDER BY table_name
        LOOP
            IF NOT EXISTS (
                SELECT 1
                  FROM information_schema.tables
                 WHERE table_schema = target_schema
                   AND table_name = table_record.table_name
                   AND table_type = 'BASE TABLE'
            ) THEN
                RAISE EXCEPTION 'Target table %.% is missing', target_schema, table_record.table_name;
            END IF;

            SELECT array_agg(column_name ORDER BY ordinal_position)
              INTO source_columns
              FROM information_schema.columns
             WHERE table_schema = source_schema
               AND table_name = table_record.table_name;
            SELECT array_agg(column_name ORDER BY ordinal_position)
              INTO target_columns
              FROM information_schema.columns
             WHERE table_schema = target_schema
               AND table_name = table_record.table_name;
            IF source_columns IS DISTINCT FROM target_columns THEN
                RAISE EXCEPTION 'Column mismatch for %.%', target_schema, table_record.table_name;
            END IF;
            SELECT array_agg(udt_name ORDER BY ordinal_position)
              INTO source_types
              FROM information_schema.columns
             WHERE table_schema = source_schema
               AND table_name = table_record.table_name;
            SELECT array_agg(udt_name ORDER BY ordinal_position)
              INTO target_types
              FROM information_schema.columns
             WHERE table_schema = target_schema
               AND table_name = table_record.table_name;
            IF source_types IS DISTINCT FROM target_types THEN
                RAISE EXCEPTION 'Column type mismatch for %.%', target_schema, table_record.table_name;
            END IF;

            SELECT string_agg(format('%I', column_name), ', ' ORDER BY ordinal_position)
              INTO column_list
              FROM information_schema.columns
             WHERE table_schema = target_schema
               AND table_name = table_record.table_name;
            SELECT array_agg(attribute.attname ORDER BY key_position)
              INTO primary_keys
              FROM pg_catalog.pg_constraint AS constraint_record
              JOIN pg_catalog.pg_class AS relation
                ON relation.oid = constraint_record.conrelid
              JOIN pg_catalog.pg_namespace AS namespace
                ON namespace.oid = relation.relnamespace
              JOIN unnest(constraint_record.conkey) WITH ORDINALITY
                AS key_column(attribute_number, key_position) ON TRUE
              JOIN pg_catalog.pg_attribute AS attribute
                ON attribute.attrelid = relation.oid
               AND attribute.attnum = key_column.attribute_number
             WHERE constraint_record.contype = 'p'
               AND namespace.nspname = target_schema
               AND relation.relname = table_record.table_name;

            IF primary_keys IS NULL OR cardinality(primary_keys) = 0 THEN
                EXECUTE format('TRUNCATE TABLE %I.%I', target_schema, table_record.table_name);
                EXECUTE format(
                    'INSERT INTO %I.%I (%s) SELECT %s FROM %I.%I',
                    target_schema, table_record.table_name, column_list, column_list,
                    source_schema, table_record.table_name
                );
                CONTINUE;
            END IF;

            -- MariaDB periodically deletes and recreates these rows, so the
            -- same natural relationship can legitimately receive a new id.
            -- A primary-key upsert would then collide with the natural unique
            -- index. Replace the small authoritative snapshot atomically.
            IF format('%s.%s', target_schema, table_record.table_name) = ANY(authoritative_replace_tables) THEN
                EXECUTE format('DELETE FROM %I.%I', target_schema, table_record.table_name);
                EXECUTE format(
                    'INSERT INTO %I.%I (%s) SELECT %s FROM %I.%I',
                    target_schema, table_record.table_name, column_list, column_list,
                    source_schema, table_record.table_name
                );
                CONTINUE;
            END IF;

            IF format('%s.%s', target_schema, table_record.table_name) = ANY(append_only_tables) THEN
                EXECUTE format('SELECT count(*) FROM %I.%I', source_schema, table_record.table_name)
                   INTO source_count;
                EXECUTE format('SELECT count(*) FROM %I.%I', target_schema, table_record.table_name)
                   INTO target_count;
                IF source_count = target_count THEN
                    CONTINUE;
                END IF;
                IF target_count < source_count
                   AND format('%s.%s', target_schema, table_record.table_name) = ANY(monotonic_id_tables)
                THEN
                    EXECUTE format(
                        'INSERT INTO %I.%I (%s) SELECT %s FROM %I.%I AS source '
                        'WHERE source.id > COALESCE((SELECT max(id) FROM %I.%I), -1) '
                        'ON CONFLICT (id) DO NOTHING',
                        target_schema, table_record.table_name, column_list, column_list,
                        source_schema, table_record.table_name,
                        target_schema, table_record.table_name
                    );
                    EXECUTE format('SELECT count(*) FROM %I.%I', target_schema, table_record.table_name)
                       INTO target_count;
                    IF source_count = target_count THEN
                        CONTINUE;
                    END IF;
                END IF;
            END IF;

            SELECT string_agg(format('%I', key_name), ', ' ORDER BY key_position)
              INTO conflict_list
              FROM unnest(primary_keys) WITH ORDINALITY AS keys(key_name, key_position);
            SELECT string_agg(format('%1$I = EXCLUDED.%1$I', column_name), ', ' ORDER BY ordinal_position),
                   string_agg(format('target.%1$I IS DISTINCT FROM EXCLUDED.%1$I', column_name), ' OR ' ORDER BY ordinal_position)
              INTO update_list, update_condition
              FROM information_schema.columns
             WHERE table_schema = target_schema
               AND table_name = table_record.table_name
               AND NOT (column_name = ANY(primary_keys));
            SELECT string_agg(format('target.%1$I = source.%1$I', key_name), ' AND ' ORDER BY key_position)
              INTO key_condition
              FROM unnest(primary_keys) WITH ORDINALITY AS keys(key_name, key_position);

            IF update_list IS NULL THEN
                EXECUTE format(
                    'INSERT INTO %I.%I AS target (%s) SELECT %s FROM %I.%I '
                    'ON CONFLICT (%s) DO NOTHING',
                    target_schema, table_record.table_name, column_list, column_list,
                    source_schema, table_record.table_name, conflict_list
                );
            ELSE
                EXECUTE format(
                    'INSERT INTO %I.%I AS target (%s) SELECT %s FROM %I.%I '
                    'ON CONFLICT (%s) DO UPDATE SET %s WHERE %s',
                    target_schema, table_record.table_name, column_list, column_list,
                    source_schema, table_record.table_name, conflict_list,
                    update_list, update_condition
                );
            END IF;
            IF format('%s.%s', target_schema, table_record.table_name) = ANY(append_only_tables) THEN
                CONTINUE;
            END IF;
            EXECUTE format(
                'DELETE FROM %I.%I AS target WHERE NOT EXISTS ('
                'SELECT 1 FROM %I.%I AS source WHERE %s)',
                target_schema, table_record.table_name,
                source_schema, table_record.table_name, key_condition
            );
        END LOOP;
    END LOOP;
END
$merge$;

DROP SCHEMA catalog_stage CASCADE;
DROP SCHEMA analytics_stage CASCADE;
COMMIT;
SQL;
    $mergeOutput = runProcess(
        [
            '/usr/bin/sudo', '-n', 'docker', 'compose',
            '--project-directory', $composeDir,
            '-f', $composeDir . '/docker-compose.yml',
            'exec', '-T', 'postgres', 'sh', '-eu', '-c',
            'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d hs_data',
        ],
        $mergeSql . PHP_EOL,
    );
    if ($mergeOutput !== '') {
        fwrite(STDOUT, $mergeOutput . PHP_EOL);
    }
    $verifyOutput = runProcess([__DIR__ . '/verify-data.php']);
    fwrite(STDOUT, $verifyOutput . PHP_EOL);
    fwrite(STDOUT, 'Atomic shadow synchronization completed.' . PHP_EOL);
} catch (Throwable $exception) {
    fwrite(STDERR, $exception->getMessage() . PHP_EOL);
    exit(1);
} finally {
    if (is_resource($lock)) {
        flock($lock, LOCK_UN);
        fclose($lock);
    }
}
