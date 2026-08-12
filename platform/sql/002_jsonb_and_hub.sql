BEGIN;

CREATE SCHEMA IF NOT EXISTS hub;

DO $migration$
DECLARE
    column_record record;
BEGIN
    FOR column_record IN
        SELECT table_schema, table_name, column_name
        FROM information_schema.columns
        WHERE table_schema IN ('catalog', 'analytics')
          AND data_type IN ('text', 'character varying', 'character')
          AND (
              column_name LIKE '%\_json' ESCAPE '\'
              OR column_name IN ('raw_json', 'raw_card_json', 'raw_deck_list', 'raw_deck_sideboard')
          )
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I ALTER COLUMN %I DROP DEFAULT',
            column_record.table_schema,
            column_record.table_name,
            column_record.column_name
        );
        EXECUTE format(
            'ALTER TABLE %I.%I ALTER COLUMN %I TYPE jsonb USING '
            'CASE WHEN %I IS NULL OR btrim(%I) = '''' THEN NULL ELSE %I::jsonb END',
            column_record.table_schema,
            column_record.table_name,
            column_record.column_name,
            column_record.column_name,
            column_record.column_name,
            column_record.column_name
        );
    END LOOP;
END
$migration$;

CREATE INDEX IF NOT EXISTS battlegrounds_cards_name_trgm_idx
    ON catalog.battlegrounds_cards USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS battlegrounds_cards_name_en_trgm_idx
    ON catalog.battlegrounds_cards USING gin (name_en gin_trgm_ops);
CREATE INDEX IF NOT EXISTS constructed_cards_name_ru_trgm_idx
    ON catalog.constructed_cards USING gin (name_ru gin_trgm_ops);
CREATE INDEX IF NOT EXISTS constructed_cards_name_en_trgm_idx
    ON catalog.constructed_cards USING gin (name_en gin_trgm_ops);
CREATE INDEX IF NOT EXISTS battlegrounds_heroes_name_ru_trgm_idx
    ON catalog.battlegrounds_heroes USING gin (name_ru gin_trgm_ops);
CREATE INDEX IF NOT EXISTS battlegrounds_heroes_name_en_trgm_idx
    ON catalog.battlegrounds_heroes USING gin (name_en gin_trgm_ops);

CREATE OR REPLACE VIEW hub.card_catalog AS
SELECT
    'battlegrounds'::text AS collection,
    card_id::text AS card_id,
    dbf::bigint AS dbf,
    name::text AS name_ru,
    name_en::text AS name_en,
    card_type::text AS card_type,
    NULL::integer AS mana_cost,
    attack::integer AS attack,
    health::integer AS health,
    COALESCE(framed_image, card_image, art_image)::text AS image_url,
    in_pool::boolean AS is_active,
    updated_at::timestamptz AS updated_at
FROM catalog.battlegrounds_cards
WHERE variant_kind = 'base'
UNION ALL
SELECT
    'constructed'::text,
    card_id::text,
    dbf::bigint,
    name_ru::text,
    name_en::text,
    card_type::text,
    mana_cost::integer,
    attack::integer,
    health::integer,
    COALESCE(local_image_url, image_url, crop_image_url)::text,
    collectible::boolean,
    updated_at::timestamptz
FROM catalog.constructed_cards;

CREATE OR REPLACE VIEW hub.archetype_latest AS
SELECT DISTINCT ON (snapshot.archetype_id, snapshot.game_type, snapshot.rank_range, snapshot.region)
    snapshot.id AS snapshot_id,
    snapshot.archetype_id,
    archetype.name,
    archetype.player_class,
    snapshot.game_type,
    snapshot.rank_range,
    snapshot.region,
    snapshot.total_games,
    snapshot.win_rate,
    snapshot.pct_of_class,
    snapshot.pct_of_total,
    snapshot.fetched_at::timestamptz AS fetched_at
FROM analytics.archetype_snapshots AS snapshot
JOIN analytics.hsreplay_archetypes AS archetype
  ON archetype.archetype_id = snapshot.archetype_id
ORDER BY
    snapshot.archetype_id,
    snapshot.game_type,
    snapshot.rank_range,
    snapshot.region,
    snapshot.fetched_at::timestamptz DESC,
    snapshot.id DESC;

CREATE OR REPLACE VIEW hub.bg_minion_latest AS
SELECT DISTINCT ON (snapshot.dbf_id, snapshot.mmr_percentile, snapshot.time_range)
    snapshot.id AS snapshot_id,
    snapshot.dbf_id,
    minion.card_id,
    minion.name,
    minion.name_ru,
    snapshot.tavern_tier,
    snapshot.mmr_percentile,
    snapshot.time_range,
    snapshot.impact,
    snapshot.combat_winrate,
    snapshot.popularity,
    snapshot.games_with_minion,
    snapshot.avg_placement_with,
    snapshot.fetched_at::timestamptz AS fetched_at
FROM analytics.bg_minion_snapshots AS snapshot
JOIN analytics.bg_minions AS minion ON minion.dbf_id = snapshot.dbf_id
ORDER BY
    snapshot.dbf_id,
    snapshot.mmr_percentile,
    snapshot.time_range,
    snapshot.fetched_at::timestamptz DESC,
    snapshot.id DESC;

CREATE OR REPLACE VIEW hub.data_overview AS
SELECT 'catalog'::text AS area, 'Battlegrounds cards'::text AS dataset, count(*)::bigint AS records
FROM catalog.battlegrounds_cards
UNION ALL
SELECT 'catalog', 'Constructed cards', count(*)::bigint FROM catalog.constructed_cards
UNION ALL
SELECT 'catalog', 'Battlegrounds heroes', count(*)::bigint FROM catalog.battlegrounds_heroes
UNION ALL
SELECT 'catalog', 'Hero skins', count(*)::bigint FROM catalog.hero_skins
UNION ALL
SELECT 'analytics', 'Card popularity history', count(*)::bigint FROM analytics.card_popularity_history
UNION ALL
SELECT 'analytics', 'Archetype snapshots', count(*)::bigint FROM analytics.archetype_snapshots
UNION ALL
SELECT 'analytics', 'Battlegrounds minion snapshots', count(*)::bigint FROM analytics.bg_minion_snapshots;

CREATE OR REPLACE VIEW hub.integration_status AS
SELECT
    source.source_id,
    source.display_name,
    source.source_kind,
    source.target_schema,
    source.sync_mode,
    source.is_enabled,
    source.last_synced_at,
    latest.started_at AS last_run_started_at,
    latest.completed_at AS last_run_completed_at,
    latest.state AS last_run_state,
    latest.rows_read AS last_rows_read,
    latest.rows_written AS last_rows_written,
    latest.error_code AS last_error_code,
    CASE
        WHEN source.last_synced_at IS NULL THEN NULL
        ELSE floor(extract(epoch FROM (now() - source.last_synced_at)))::bigint
    END AS seconds_since_sync
FROM platform.data_sources AS source
LEFT JOIN LATERAL (
    SELECT run.started_at, run.completed_at, run.state, run.rows_read,
           run.rows_written, run.error_code
    FROM platform.sync_runs AS run
    WHERE run.source_id = source.source_id
    ORDER BY run.started_at DESC, run.id DESC
    LIMIT 1
) AS latest ON true;

INSERT INTO platform.schema_migrations (version)
VALUES ('002_jsonb_and_hub')
ON CONFLICT (version) DO NOTHING;

COMMIT;
