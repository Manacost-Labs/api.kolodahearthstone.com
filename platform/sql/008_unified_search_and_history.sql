BEGIN;

CREATE INDEX IF NOT EXISTS hsreplay_archetypes_name_trgm_idx
    ON analytics.hsreplay_archetypes USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS bg_minions_name_trgm_idx
    ON analytics.bg_minions USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS bg_minions_name_ru_trgm_idx
    ON analytics.bg_minions USING gin (name_ru gin_trgm_ops);
CREATE INDEX IF NOT EXISTS platform_data_sources_display_name_trgm_idx
    ON platform.data_sources USING gin (display_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS game_stat_rows_entity_history_idx
    ON analytics.game_stat_rows (entity_key, snapshot_id);
CREATE INDEX IF NOT EXISTS game_stat_snapshots_patch_history_idx
    ON analytics.game_stat_snapshots (
        patch,
        domain,
        format_name,
        rank_range,
        mode,
        fetched_at DESC,
        id DESC
    );

CREATE OR REPLACE VIEW hub.unified_search AS
SELECT
    'card'::text AS kind,
    card.collection || ':' || card.card_id AS entity_id,
    COALESCE(card.name_ru, card.name_en, card.card_id)::text AS name,
    card.name_ru::text AS name_ru,
    concat_ws(' · ', card.collection, card.card_type)::text AS subtitle,
    card.image_url::text AS image_url,
    card.collection::text AS source_id,
    card.updated_at::timestamptz AS updated_at,
    jsonb_strip_nulls(jsonb_build_object(
        'cardId', card.card_id,
        'dbfId', card.dbf,
        'manaCost', card.mana_cost,
        'attack', card.attack,
        'health', card.health,
        'active', card.is_active
    )) AS metadata
FROM hub.card_catalog AS card
UNION ALL
SELECT
    'hero',
    hero.card_id::text,
    COALESCE(hero.name_ru, hero.name_en, hero.card_id)::text,
    hero.name_ru::text,
    concat_ws(' · ', 'Battlegrounds', hero.status)::text,
    COALESCE(hero.hero_image_url, hero.hero_full_art_url)::text,
    'battlegrounds_heroes',
    hero.updated_at::timestamptz,
    jsonb_strip_nulls(jsonb_build_object(
        'dbfId', hero.dbf,
        'heroId', hero.hero_id,
        'armor', hero.armor,
        'duosArmor', hero.duos_armor,
        'race', hero.race
    ))
FROM catalog.battlegrounds_heroes AS hero
UNION ALL
SELECT
    'minion',
    minion.dbf_id::text,
    COALESCE(minion.name_ru, minion.name, minion.card_id, minion.dbf_id::text)::text,
    minion.name_ru::text,
    concat_ws(' · ', 'Battlegrounds', 'Tier ' || minion.tavern_tier)::text,
    NULL::text,
    'bg_minions',
    minion.fetched_at::timestamptz,
    jsonb_strip_nulls(jsonb_build_object(
        'cardId', minion.card_id,
        'dbfId', minion.dbf_id,
        'tavernTier', minion.tavern_tier
    ))
FROM (
    SELECT DISTINCT ON (dbf_id) *
    FROM hub.bg_minion_latest
    ORDER BY dbf_id, fetched_at DESC NULLS LAST, snapshot_id DESC
) AS minion
UNION ALL
SELECT
    'archetype',
    archetype.archetype_id::text,
    archetype.name::text,
    NULL::text,
    concat_ws(' · ', archetype.player_class, archetype.game_type)::text,
    NULL::text,
    'hsreplay_archetypes',
    archetype.fetched_at::timestamptz,
    jsonb_strip_nulls(jsonb_build_object(
        'archetypeId', archetype.archetype_id,
        'playerClass', archetype.player_class,
        'gameType', archetype.game_type
    ))
FROM (
    SELECT DISTINCT ON (archetype_id) *
    FROM hub.archetype_latest
    ORDER BY archetype_id, fetched_at DESC NULLS LAST, snapshot_id DESC
) AS archetype
UNION ALL
SELECT
    'source',
    source.source_id::text,
    source.display_name::text,
    NULL::text,
    concat_ws(' · ', source.source_kind, source.target_schema)::text,
    NULL::text,
    source.source_id::text,
    source.last_synced_at::timestamptz,
    jsonb_strip_nulls(jsonb_build_object(
        'sourceKind', source.source_kind,
        'targetSchema', source.target_schema,
        'syncMode', source.sync_mode,
        'enabled', source.is_enabled
    ))
FROM platform.data_sources AS source
UNION ALL
SELECT
    'source',
    dataset.source_id::text,
    dataset.source_id::text,
    NULL::text,
    'raw dataset'::text,
    NULL::text,
    dataset.source_id::text,
    dataset.fetched_at::timestamptz,
    jsonb_build_object('sourceKind', 'dataset', 'targetSchema', 'raw')
FROM (
    SELECT DISTINCT ON (source_id) source_id, fetched_at
    FROM raw.datasets
    WHERE NOT EXISTS (
        SELECT 1 FROM platform.data_sources AS source
        WHERE source.source_id = raw.datasets.source_id
    )
    ORDER BY source_id, fetched_at DESC NULLS LAST, imported_at DESC
) AS dataset;

INSERT INTO platform.schema_migrations (version)
VALUES ('008_unified_search_and_history')
ON CONFLICT (version) DO NOTHING;

COMMIT;
