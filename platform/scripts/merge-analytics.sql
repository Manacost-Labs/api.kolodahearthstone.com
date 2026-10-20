-- Included inside sync-shadow.php's catalogue + analytics transaction.
-- Durable checkpoints must never advance before the canonical merge commits.
CREATE TABLE IF NOT EXISTS platform.analytics_shadow_state (
    table_name text PRIMARY KEY,
    generation text NOT NULL,
    source_identity text NOT NULL,
    shape_hash text NOT NULL,
    manifest_hash text NOT NULL,
    source_rows bigint NOT NULL CHECK (source_rows >= 0),
    target_rows bigint NOT NULL CHECK (target_rows >= 0),
    target_oid bigint NOT NULL,
    target_relfilenode bigint NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS platform.analytics_shadow_chunks (
    table_name text NOT NULL,
    bucket bigint NOT NULL,
    row_count bigint NOT NULL CHECK (row_count > 0),
    digest text NOT NULL,
    PRIMARY KEY (table_name, bucket)
);
CREATE TEMP TABLE analytics_merge_result (
    table_name text, rows_read bigint, rows_staged bigint, chunks_changed bigint,
    rows_written bigint, rows_deleted bigint
) ON COMMIT DROP;

DO $analytics_delta$
DECLARE
    expected_tables text[] := ARRAY[
        'archetype_deck_cards', 'archetype_decks', 'archetype_matchups',
        'archetype_mulligan', 'archetype_refresh_runs', 'archetype_snapshots',
        'archetype_time_series', 'bg_minion_refresh_runs', 'bg_minion_round_stats',
        'bg_minion_snapshots', 'bg_minions', 'card_popularity_history', 'decks',
        'fetch_log', 'hearthstone_patches', 'hsguru_archetype_history', 'hsreplay_archetypes'
    ];
    append_only_tables text[] := ARRAY[
        'archetype_deck_cards', 'archetype_decks', 'archetype_matchups',
        'archetype_mulligan', 'archetype_snapshots', 'archetype_time_series',
        'bg_minion_round_stats', 'bg_minion_snapshots', 'card_popularity_history',
        'hsguru_archetype_history'
    ];
    actual_tables text[];
    plan record;
    target_relation regclass;
    target_columns jsonb;
    stage_columns jsonb;
    primary_keys text[];
    expected_bucket_column text;
    current_generation text;
    column_list text;
    incoming_list text;
    conflict_list text;
    join_condition text;
    source_difference text;
    conflict_difference text;
    update_list text;
    conflict_action text;
    actual_count bigint;
    written_count bigint;
    deleted_count bigint;
    target_count bigint;
    deletion_scope text;
BEGIN
    SELECT array_agg(table_name ORDER BY table_name) INTO actual_tables
      FROM analytics_sync_stage.tables;
    IF actual_tables IS NULL OR cardinality(actual_tables) <> cardinality(expected_tables)
       OR NOT actual_tables @> expected_tables THEN
        RAISE EXCEPTION 'Incomplete analytics stage manifest';
    END IF;
    SELECT array_agg(table_name ORDER BY table_name) INTO actual_tables
      FROM information_schema.tables WHERE table_schema='analytics_stage' AND table_type='BASE TABLE';
    IF actual_tables IS NULL OR cardinality(actual_tables) <> cardinality(expected_tables)
       OR NOT actual_tables @> expected_tables THEN
        RAISE EXCEPTION 'Unexpected or missing analytics stage tables';
    END IF;
    IF EXISTS (SELECT 1 FROM analytics_sync_stage.chunks WHERE NOT table_name = ANY(expected_tables))
       OR EXISTS (SELECT 1 FROM analytics_sync_stage.changed_chunks WHERE NOT table_name = ANY(expected_tables))
       OR (SELECT count(DISTINCT generation) FROM analytics_sync_stage.tables) <> 1 THEN
        RAISE EXCEPTION 'Inconsistent analytics stage generation';
    END IF;

    -- Check every stage before canonical writes. UNLOGGED data can disappear
    -- after a server crash; empty/truncated staging must never become deletions.
    FOR plan IN SELECT * FROM analytics_sync_stage.tables ORDER BY table_name LOOP
        target_relation := to_regclass(format('analytics.%I', plan.table_name));
        EXECUTE format('LOCK TABLE analytics.%I IN SHARE ROW EXCLUSIVE MODE', plan.table_name);
        IF target_relation::oid::bigint <> plan.target_oid
           OR pg_relation_filenode(target_relation)::bigint <> plan.target_relfilenode THEN
            RAISE EXCEPTION 'Analytics target storage changed: %', plan.table_name;
        END IF;
        SELECT jsonb_agg(jsonb_build_object('name',a.attname,'type',ty.typname,
                   'typmod',a.atttypmod,'not_null',a.attnotnull) ORDER BY a.attnum)
          INTO target_columns FROM pg_attribute a JOIN pg_type ty ON ty.oid=a.atttypid
         WHERE a.attrelid=target_relation AND a.attnum>0 AND NOT a.attisdropped;
        SELECT jsonb_agg(jsonb_build_object('name',a.attname,'type',ty.typname,
                   'typmod',a.atttypmod,'not_null',a.attnotnull) ORDER BY a.attnum)
          INTO stage_columns FROM pg_attribute a JOIN pg_type ty ON ty.oid=a.atttypid
         WHERE a.attrelid=to_regclass(format('analytics_stage.%I',plan.table_name))
           AND a.attnum>0 AND NOT a.attisdropped;
        SELECT array_agg(a.attname ORDER BY k.position) INTO primary_keys
          FROM pg_constraint p CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(number,position)
          JOIN pg_attribute a ON a.attrelid=p.conrelid AND a.attnum=k.number
         WHERE p.conrelid=target_relation AND p.contype='p';
        IF target_columns IS DISTINCT FROM plan.columns
           OR stage_columns IS DISTINCT FROM plan.columns
           OR to_jsonb(primary_keys) IS DISTINCT FROM plan.primary_keys THEN
            RAISE EXCEPTION 'Analytics target/stage shape changed: %', plan.table_name;
        END IF;
        SELECT CASE WHEN c->>'type'='int8' THEN primary_keys[1] ELSE NULL END
          INTO expected_bucket_column FROM jsonb_array_elements(target_columns) c
         WHERE c->>'name'=primary_keys[1];
        IF plan.bucket_column IS DISTINCT FROM expected_bucket_column OR plan.bucket_width <> 8192
           OR plan.append_only IS DISTINCT FROM (plan.table_name = ANY(append_only_tables)) THEN
            RAISE EXCEPTION 'Invalid analytics chunk policy: %', plan.table_name;
        END IF;
        SELECT generation INTO current_generation FROM platform.analytics_shadow_state
         WHERE table_name=plan.table_name;
        IF current_generation IS DISTINCT FROM plan.base_generation THEN
            RAISE EXCEPTION 'Stale analytics checkpoint generation: %', plan.table_name;
        END IF;
        EXECUTE format('SELECT count(*) FROM analytics.%I', plan.table_name) INTO actual_count;
        IF actual_count <> plan.target_rows_before THEN
            RAISE EXCEPTION 'Analytics target changed during staging: %', plan.table_name;
        END IF;
        EXECUTE format('SELECT count(*) FROM analytics_stage.%I', plan.table_name) INTO actual_count;
        IF actual_count <> plan.rows_staged
           OR plan.source_rows <> (SELECT coalesce(sum(row_count),0) FROM analytics_sync_stage.chunks WHERE table_name=plan.table_name)
           OR plan.rows_staged <> (SELECT coalesce(sum(c.row_count),0) FROM analytics_sync_stage.chunks c
                 JOIN analytics_sync_stage.changed_chunks changed USING(table_name,bucket)
                WHERE c.table_name=plan.table_name)
           OR plan.chunks_changed <> (SELECT count(*) FROM analytics_sync_stage.changed_chunks WHERE table_name=plan.table_name)
           OR (plan.full_reconcile AND plan.rows_staged <> plan.source_rows) THEN
            RAISE EXCEPTION 'Incomplete analytics staged rows: %', plan.table_name;
        END IF;
        IF plan.bucket_column IS NOT NULL THEN
            EXECUTE format(
                'SELECT count(*) FROM analytics_stage.%1$I incoming WHERE NOT EXISTS ('
                'SELECT 1 FROM analytics_sync_stage.changed_chunks c WHERE c.table_name=%2$L '
                'AND c.bucket = CASE WHEN incoming.%3$I < 0 '
                'THEN (incoming.%3$I + 1) / 8192 - 1 ELSE incoming.%3$I / 8192 END)',
                plan.table_name, plan.table_name, plan.bucket_column) INTO actual_count;
            IF actual_count <> 0 THEN
                RAISE EXCEPTION 'Analytics staged rows outside changed chunks: %', plan.table_name;
            END IF;
        ELSIF EXISTS (SELECT 1 FROM analytics_sync_stage.chunks WHERE table_name=plan.table_name AND bucket<>0)
           OR EXISTS (SELECT 1 FROM analytics_sync_stage.changed_chunks WHERE table_name=plan.table_name AND bucket<>0) THEN
            RAISE EXCEPTION 'Invalid string-key analytics chunk: %', plan.table_name;
        END IF;
    END LOOP;

    FOR plan IN SELECT * FROM analytics_sync_stage.tables ORDER BY table_name LOOP
        SELECT array_agg(value ORDER BY position) INTO primary_keys
          FROM jsonb_array_elements_text(plan.primary_keys) WITH ORDINALITY k(value,position);
        SELECT string_agg(format('%I',c->>'name'), ', ' ORDER BY position),
               string_agg(format('incoming.%I',c->>'name'), ', ' ORDER BY position)
          INTO column_list, incoming_list
          FROM jsonb_array_elements(plan.columns) WITH ORDINALITY cols(c,position);
        SELECT string_agg(format('%I',k), ', ' ORDER BY position),
               string_agg(format('target.%1$I = incoming.%1$I',k), ' AND ' ORDER BY position)
          INTO conflict_list, join_condition FROM unnest(primary_keys) WITH ORDINALITY keys(k,position);
        SELECT string_agg(format('%1$I = EXCLUDED.%1$I',c->>'name'), ', ' ORDER BY position),
               string_agg(format('target.%1$I IS DISTINCT FROM incoming.%1$I',c->>'name'), ' OR ' ORDER BY position),
               string_agg(format('target.%1$I IS DISTINCT FROM EXCLUDED.%1$I',c->>'name'), ' OR ' ORDER BY position)
          INTO update_list, source_difference, conflict_difference
          FROM jsonb_array_elements(plan.columns) WITH ORDINALITY cols(c,position)
         WHERE NOT (c->>'name' = ANY(primary_keys));
        conflict_action := CASE WHEN update_list IS NULL THEN 'DO NOTHING'
            ELSE format('DO UPDATE SET %s WHERE %s', update_list, conflict_difference) END;
        EXECUTE format(
            'INSERT INTO analytics.%I AS target (%s) SELECT %s FROM analytics_stage.%I AS incoming '
            'LEFT JOIN analytics.%I AS stored ON %s WHERE stored.%I IS NULL OR (%s) '
            'ON CONFLICT (%s) %s',
            plan.table_name, column_list, incoming_list, plan.table_name,
            plan.table_name, replace(join_condition,'target.','stored.'), primary_keys[1],
            coalesce(replace(source_difference,'target.','stored.'),'FALSE'),
            conflict_list, conflict_action);
        GET DIAGNOSTICS written_count = ROW_COUNT;

        deleted_count := 0;
        IF NOT plan.append_only THEN
            deletion_scope := 'TRUE';
            IF NOT plan.full_reconcile THEN
                IF plan.bucket_column IS NULL THEN
                    deletion_scope := format('EXISTS (SELECT 1 FROM analytics_sync_stage.changed_chunks c WHERE c.table_name=%L)', plan.table_name);
                ELSE
                    deletion_scope := format(
                        'EXISTS (SELECT 1 FROM analytics_sync_stage.changed_chunks c WHERE c.table_name=%L '
                        'AND target.%I BETWEEN c.bucket * 8192 AND c.bucket * 8192 + 8191)',
                        plan.table_name, plan.bucket_column);
                END IF;
            END IF;
            EXECUTE format(
                'DELETE FROM analytics.%I AS target WHERE (%s) AND NOT EXISTS ('
                'SELECT 1 FROM analytics_stage.%I AS incoming WHERE %s)',
                plan.table_name, deletion_scope, plan.table_name, join_condition);
            GET DIAGNOSTICS deleted_count = ROW_COUNT;
        END IF;
        EXECUTE format('SELECT count(*) FROM analytics.%I', plan.table_name) INTO target_count;
        IF NOT plan.append_only AND target_count <> plan.source_rows THEN
            RAISE EXCEPTION 'Authoritative analytics count mismatch after merge: %', plan.table_name;
        END IF;

        DELETE FROM platform.analytics_shadow_chunks WHERE table_name=plan.table_name;
        INSERT INTO platform.analytics_shadow_chunks (table_name,bucket,row_count,digest)
        SELECT table_name,bucket,row_count,digest FROM analytics_sync_stage.chunks WHERE table_name=plan.table_name;
        INSERT INTO platform.analytics_shadow_state (
            table_name,generation,source_identity,shape_hash,manifest_hash,source_rows,
            target_rows,target_oid,target_relfilenode,synced_at
        ) VALUES (
            plan.table_name,plan.generation,plan.source_identity,plan.shape_hash,plan.manifest_hash,
            plan.source_rows,target_count,plan.target_oid,plan.target_relfilenode,now()
        ) ON CONFLICT(table_name) DO UPDATE SET
            generation=EXCLUDED.generation, source_identity=EXCLUDED.source_identity,
            shape_hash=EXCLUDED.shape_hash, manifest_hash=EXCLUDED.manifest_hash,
            source_rows=EXCLUDED.source_rows, target_rows=EXCLUDED.target_rows,
            target_oid=EXCLUDED.target_oid, target_relfilenode=EXCLUDED.target_relfilenode,
            synced_at=EXCLUDED.synced_at;
        INSERT INTO analytics_merge_result VALUES (
            plan.table_name,plan.source_rows,plan.rows_staged,plan.chunks_changed,written_count,deleted_count
        );
    END LOOP;
END
$analytics_delta$;

SELECT jsonb_build_object(
    'event','analytics_delta_completed', 'tables',count(*),
    'rows_read',sum(rows_read), 'rows_staged',sum(rows_staged),
    'chunks_changed',sum(chunks_changed), 'rows_written',sum(rows_written),
    'rows_deleted',sum(rows_deleted)
) FROM analytics_merge_result;
