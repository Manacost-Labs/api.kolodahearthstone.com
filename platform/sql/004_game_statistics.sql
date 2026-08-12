BEGIN;

CREATE TABLE IF NOT EXISTS analytics.game_stat_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_key text NOT NULL UNIQUE,
    source_id text NOT NULL,
    dataset_version text NOT NULL,
    domain text NOT NULL CHECK (domain IN (
        'constructed_meta',
        'constructed_archetypes',
        'constructed_cards',
        'bg_heroes',
        'arena_cards'
    )),
    entity_type text NOT NULL,
    format_name text NOT NULL DEFAULT 'all',
    rank_range text NOT NULL DEFAULT 'all',
    period text NOT NULL DEFAULT 'current',
    mode text NOT NULL DEFAULT 'default',
    rating_bracket text NOT NULL DEFAULT 'all',
    patch text,
    source_url text,
    fetched_at timestamptz NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS game_stat_snapshots_dimensions_idx
    ON analytics.game_stat_snapshots (
        domain, format_name, rank_range, period, mode, rating_bracket, fetched_at DESC
    );
CREATE INDEX IF NOT EXISTS game_stat_snapshots_source_idx
    ON analytics.game_stat_snapshots (source_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS analytics.game_stat_rows (
    snapshot_id bigint NOT NULL REFERENCES analytics.game_stat_snapshots(id) ON DELETE CASCADE,
    entity_key text NOT NULL,
    entity_type text NOT NULL,
    card_id text,
    dbf_id bigint,
    name text,
    name_ru text,
    class_name text,
    tier text,
    games bigint,
    win_rate numeric(10, 4),
    popularity numeric(10, 4),
    pick_rate numeric(10, 4),
    avg_placement numeric(10, 4),
    score numeric(14, 4),
    image_url text,
    source_url text,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, entity_key)
);

CREATE INDEX IF NOT EXISTS game_stat_rows_name_trgm_idx
    ON analytics.game_stat_rows USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS game_stat_rows_name_ru_trgm_idx
    ON analytics.game_stat_rows USING gin (name_ru gin_trgm_ops);
CREATE INDEX IF NOT EXISTS game_stat_rows_card_idx
    ON analytics.game_stat_rows (card_id, dbf_id);
CREATE INDEX IF NOT EXISTS game_stat_rows_metrics_idx
    ON analytics.game_stat_rows USING gin (metrics jsonb_path_ops);

CREATE OR REPLACE VIEW hub.game_stat_snapshot_latest AS
SELECT DISTINCT ON (
    source_id, domain, entity_type, format_name, rank_range, period, mode, rating_bracket
)
    id,
    snapshot_key,
    source_id,
    dataset_version,
    domain,
    entity_type,
    format_name,
    rank_range,
    period,
    mode,
    rating_bracket,
    patch,
    source_url,
    fetched_at,
    imported_at,
    metadata
FROM analytics.game_stat_snapshots
ORDER BY
    source_id, domain, entity_type, format_name, rank_range, period, mode, rating_bracket,
    fetched_at DESC, imported_at DESC, id DESC;

CREATE OR REPLACE VIEW hub.game_stat_latest AS
SELECT
    snapshot.source_id,
    snapshot.dataset_version,
    snapshot.domain,
    snapshot.entity_type AS snapshot_entity_type,
    snapshot.format_name,
    snapshot.rank_range,
    snapshot.period,
    snapshot.mode,
    snapshot.rating_bracket,
    snapshot.patch,
    snapshot.source_url AS snapshot_source_url,
    snapshot.fetched_at,
    snapshot.metadata,
    row.entity_key,
    row.entity_type,
    row.card_id,
    row.dbf_id,
    row.name,
    row.name_ru,
    row.class_name,
    row.tier,
    row.games,
    row.win_rate,
    row.popularity,
    row.pick_rate,
    row.avg_placement,
    row.score,
    row.image_url,
    row.source_url,
    row.metrics
FROM hub.game_stat_snapshot_latest AS snapshot
JOIN analytics.game_stat_rows AS row ON row.snapshot_id = snapshot.id;

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
SELECT 'analytics', 'Battlegrounds minion snapshots', count(*)::bigint FROM analytics.bg_minion_snapshots
UNION ALL
SELECT 'analytics', 'Unified game-stat snapshots', count(*)::bigint FROM analytics.game_stat_snapshots
UNION ALL
SELECT 'analytics', 'Unified game-stat rows', count(*)::bigint FROM analytics.game_stat_rows;

INSERT INTO platform.data_sources (source_id, source_kind, display_name, target_schema, sync_mode)
VALUES ('statistics-normalized', 'json', 'Normalized game statistics', 'analytics', 'shadow')
ON CONFLICT (source_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    target_schema = EXCLUDED.target_schema,
    sync_mode = EXCLUDED.sync_mode,
    updated_at = now();

INSERT INTO platform.schema_migrations (version)
VALUES ('004_game_statistics')
ON CONFLICT (version) DO NOTHING;

COMMIT;
