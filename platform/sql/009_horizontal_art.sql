BEGIN;

CREATE TABLE IF NOT EXISTS catalog.horizontal_art_assets (
    entity_type varchar(32) NOT NULL,
    entity_id varchar(160) NOT NULL,
    source_url varchar(1024) NOT NULL,
    source_kind varchar(16) NOT NULL,
    source_signature char(64) NOT NULL,
    local_image_url varchar(512),
    image_sha256 char(64),
    recipe_version varchar(64) NOT NULL,
    status varchar(16) NOT NULL,
    last_error text,
    generated_at timestamptz,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS horizontal_art_assets_status_idx
    ON catalog.horizontal_art_assets (status, entity_type);

CREATE OR REPLACE VIEW hub.card_catalog AS
SELECT
    'battlegrounds'::text AS collection,
    card.card_id::text AS card_id,
    card.dbf::bigint AS dbf,
    card.name::text AS name_ru,
    card.name_en::text AS name_en,
    card.card_type::text AS card_type,
    NULL::integer AS mana_cost,
    card.attack::integer AS attack,
    card.health::integer AS health,
    COALESCE(card.framed_image, card.card_image, card.art_image)::text AS image_url,
    card.in_pool::boolean AS is_active,
    card.updated_at::timestamptz AS updated_at,
    CASE
        WHEN art.local_image_url LIKE 'http%' THEN art.local_image_url
        WHEN art.local_image_url IS NOT NULL THEN
            'https://api.kolodahearthstone.com' || art.local_image_url
            || '?v=' || extract(epoch FROM art.generated_at)::bigint
        ELSE NULL
    END::text AS horizontal_image_url
FROM catalog.battlegrounds_cards AS card
LEFT JOIN catalog.horizontal_art_assets AS art
  ON art.entity_type = 'battleground_card'
 AND art.entity_id = card.card_id
 AND art.status = 'ready'
WHERE card.variant_kind = 'base'
UNION ALL
SELECT
    'constructed'::text,
    card.card_id::text,
    card.dbf::bigint,
    card.name_ru::text,
    card.name_en::text,
    card.card_type::text,
    card.mana_cost::integer,
    card.attack::integer,
    card.health::integer,
    COALESCE(card.local_image_url, card.image_url, card.crop_image_url)::text,
    card.collectible::boolean,
    card.updated_at::timestamptz,
    CASE
        WHEN art.local_image_url LIKE 'http%' THEN art.local_image_url
        WHEN art.local_image_url IS NOT NULL THEN
            'https://api.kolodahearthstone.com' || art.local_image_url
            || '?v=' || extract(epoch FROM art.generated_at)::bigint
        ELSE NULL
    END::text
FROM catalog.constructed_cards AS card
LEFT JOIN catalog.horizontal_art_assets AS art
  ON art.entity_type = 'constructed_card'
 AND art.entity_id = card.card_id
 AND art.status = 'ready';

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
    snapshot.fetched_at::timestamptz AS fetched_at,
    CASE
        WHEN art.local_image_url LIKE 'http%' THEN art.local_image_url
        WHEN art.local_image_url IS NOT NULL THEN
            'https://api.kolodahearthstone.com' || art.local_image_url
            || '?v=' || extract(epoch FROM art.generated_at)::bigint
        ELSE NULL
    END::text AS horizontal_image_url
FROM analytics.bg_minion_snapshots AS snapshot
JOIN analytics.bg_minions AS minion ON minion.dbf_id = snapshot.dbf_id
LEFT JOIN catalog.horizontal_art_assets AS art
  ON art.entity_type = 'battleground_card'
 AND art.entity_id = minion.card_id
 AND art.status = 'ready'
ORDER BY
    snapshot.dbf_id,
    snapshot.mmr_percentile,
    snapshot.time_range,
    snapshot.fetched_at::timestamptz DESC,
    snapshot.id DESC;

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
    )) AS metadata,
    card.horizontal_image_url::text AS horizontal_image_url
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
    )),
    CASE
        WHEN art.local_image_url LIKE 'http%' THEN art.local_image_url
        WHEN art.local_image_url IS NOT NULL THEN
            'https://api.kolodahearthstone.com' || art.local_image_url
            || '?v=' || extract(epoch FROM art.generated_at)::bigint
        ELSE NULL
    END::text
FROM catalog.battlegrounds_heroes AS hero
LEFT JOIN catalog.horizontal_art_assets AS art
  ON art.entity_type = 'hero'
 AND art.entity_id = hero.card_id
 AND art.status = 'ready'
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
    )),
    minion.horizontal_image_url::text
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
    )),
    NULL::text
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
    )),
    NULL::text
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
    jsonb_build_object('sourceKind', 'dataset', 'targetSchema', 'raw'),
    NULL::text
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
VALUES ('009_horizontal_art')
ON CONFLICT (version) DO NOTHING;

COMMIT;
