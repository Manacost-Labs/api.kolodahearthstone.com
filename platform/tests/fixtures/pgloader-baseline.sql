-- Minimal pgloader baseline for migration CI.
--
-- Production catalog/analytics tables below are created by pgloader from the
-- legacy MariaDB/SQLite stores, not by platform/sql. Migrations 002+ build
-- views and indexes on top of them, so a fresh database needs these shells
-- before platform/scripts/apply-migrations.sh can run. Generated from a
-- schema-only dump of production (no data, owners, grants, indexes or
-- triggers). Regenerate when a migration starts referencing another
-- pgloader-owned table.

CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE analytics.archetype_snapshots (
    id bigint NOT NULL,
    run_id bigint,
    archetype_id bigint,
    source text,
    game_type text,
    rank_range text,
    region text,
    summary_time_range text,
    deck_time_range text,
    mulligan_time_range text,
    fetched_at text,
    as_of_popularity text,
    as_of_matchups text,
    as_of_decks text,
    as_of_mulligan text,
    total_games bigint,
    win_rate real,
    pct_of_class real,
    pct_of_total real,
    raw_json jsonb
);

CREATE SEQUENCE analytics.archetype_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE analytics.archetype_snapshots_id_seq OWNED BY analytics.archetype_snapshots.id;

CREATE TABLE analytics.bg_minion_snapshots (
    id bigint NOT NULL,
    run_id bigint,
    dbf_id bigint,
    source text,
    mmr_percentile text,
    time_range text,
    fetched_at text,
    tavern_tier bigint,
    impact real,
    combat_winrate real,
    popularity real,
    games_with_minion bigint,
    games_without_minion bigint,
    avg_placement_with real,
    avg_placement_without real,
    raw_json jsonb
);

CREATE SEQUENCE analytics.bg_minion_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE analytics.bg_minion_snapshots_id_seq OWNED BY analytics.bg_minion_snapshots.id;

CREATE TABLE analytics.bg_minions (
    dbf_id bigint NOT NULL,
    card_id text,
    name text,
    name_ru text,
    tavern_tier bigint,
    card_type text,
    rarity text,
    first_seen_at text,
    updated_at text,
    raw_card_json jsonb
);

CREATE TABLE analytics.card_popularity_history (
    id bigint NOT NULL,
    source_id text,
    class text,
    archetype text,
    card_name text,
    popularity real,
    recorded_at text
);

CREATE SEQUENCE analytics.card_popularity_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE analytics.card_popularity_history_id_seq OWNED BY analytics.card_popularity_history.id;

CREATE TABLE analytics.hsreplay_archetypes (
    archetype_id bigint NOT NULL,
    name text,
    slug text,
    player_class text,
    class_name text,
    url text,
    format text DEFAULT 'standard'::text,
    first_seen_at text,
    updated_at text
);

CREATE TABLE catalog.battlegrounds_cards (
    id bigint NOT NULL,
    name character varying(160) NOT NULL,
    name_en character varying(160) DEFAULT NULL::character varying,
    card_id character varying(64) NOT NULL,
    dbf bigint,
    card_type character varying(16) DEFAULT 'minion'::character varying NOT NULL,
    variant_kind character varying(16) DEFAULT 'base'::character varying NOT NULL,
    base_dbf bigint,
    base_card_id character varying(64) DEFAULT NULL::character varying,
    premium_dbf bigint,
    tavern_tier smallint,
    creature_type character varying(32) DEFAULT NULL::character varying,
    attack smallint,
    health smallint,
    in_pool boolean DEFAULT true NOT NULL,
    duos_only boolean DEFAULT false NOT NULL,
    card_image character varying(255) DEFAULT NULL::character varying,
    golden_image character varying(255) DEFAULT NULL::character varying,
    art_image character varying(255) DEFAULT NULL::character varying,
    framed_image character varying(255) DEFAULT NULL::character varying,
    notes text,
    source character varying(32) DEFAULT NULL::character varying,
    source_hash character(64) DEFAULT NULL::bpchar,
    first_seen_at timestamp with time zone,
    last_seen_at timestamp with time zone,
    changed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone
);

CREATE SEQUENCE catalog.battlegrounds_cards_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE catalog.battlegrounds_cards_id_seq OWNED BY catalog.battlegrounds_cards.id;

CREATE TABLE catalog.battlegrounds_heroes (
    card_id character varying(64) NOT NULL,
    dbf bigint NOT NULL,
    hero_id bigint,
    name_en character varying(160) NOT NULL,
    name_ru character varying(160) DEFAULT NULL::character varying,
    health smallint,
    armor smallint,
    duos_armor smallint,
    armor_text character varying(64) DEFAULT NULL::character varying,
    artist character varying(255) DEFAULT NULL::character varying,
    race character varying(128) DEFAULT NULL::character varying,
    character_name character varying(160) DEFAULT NULL::character varying,
    as_hero text,
    hero_description text,
    hero_image_url character varying(512) DEFAULT NULL::character varying,
    hero_full_art_url character varying(512) DEFAULT NULL::character varying,
    wiki_page_title character varying(255) DEFAULT NULL::character varying,
    wiki_page_url character varying(512) DEFAULT NULL::character varying,
    hero_power_dbf bigint,
    hero_power_json jsonb,
    buddy_dbf bigint,
    buddy_json jsonb,
    availability_json jsonb,
    hero_skins_json jsonb,
    gallery_json jsonb,
    card_changes_json jsonb,
    external_links_json jsonb,
    source_payload_json jsonb,
    source_hash character(64) DEFAULT NULL::bpchar,
    status character varying(16) DEFAULT 'ok'::character varying NOT NULL,
    error text,
    fetched_at timestamp with time zone,
    changed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone
);

CREATE TABLE catalog.constructed_cards (
    card_id character varying(64) NOT NULL,
    dbf bigint,
    slug character varying(160) DEFAULT NULL::character varying,
    collectible boolean DEFAULT false NOT NULL,
    name_ru character varying(255) DEFAULT NULL::character varying,
    name_en character varying(255) DEFAULT NULL::character varying,
    text_ru text,
    text_en text,
    flavor_ru text,
    flavor_en text,
    card_set character varying(80) DEFAULT NULL::character varying,
    card_set_id bigint,
    card_type character varying(80) DEFAULT NULL::character varying,
    card_type_id integer,
    rarity character varying(80) DEFAULT NULL::character varying,
    rarity_id integer,
    class_slug character varying(80) DEFAULT NULL::character varying,
    class_id integer,
    multi_class_json jsonb,
    minion_type character varying(80) DEFAULT NULL::character varying,
    minion_type_id integer,
    spell_school character varying(80) DEFAULT NULL::character varying,
    spell_school_id integer,
    mana_cost smallint,
    attack smallint,
    health smallint,
    durability smallint,
    armor smallint,
    artist character varying(255) DEFAULT NULL::character varying,
    image_url character varying(512) DEFAULT NULL::character varying,
    image_gold_url character varying(512) DEFAULT NULL::character varying,
    image_signature_url character varying(512) DEFAULT NULL::character varying,
    image_diamond_url character varying(512) DEFAULT NULL::character varying,
    animated_gold_url character varying(512) DEFAULT NULL::character varying,
    animated_signature_url character varying(512) DEFAULT NULL::character varying,
    animated_diamond_url character varying(512) DEFAULT NULL::character varying,
    crop_image_url character varying(512) DEFAULT NULL::character varying,
    local_image_url character varying(512) DEFAULT NULL::character varying,
    local_gold_image_url character varying(512) DEFAULT NULL::character varying,
    local_crop_image_url character varying(512) DEFAULT NULL::character varying,
    wiki_full_art_title character varying(255) DEFAULT NULL::character varying,
    wiki_full_art_url character varying(512) DEFAULT NULL::character varying,
    local_wiki_full_art_url character varying(512) DEFAULT NULL::character varying,
    wiki_full_art_file_page_url character varying(512) DEFAULT NULL::character varying,
    wiki_full_art_width bigint,
    wiki_full_art_height bigint,
    wiki_full_art_size bigint,
    wiki_full_art_sha1 character(40) DEFAULT NULL::bpchar,
    wiki_full_art_mime character varying(80) DEFAULT NULL::character varying,
    wiki_full_art_fetched_at timestamp with time zone,
    mechanics_json jsonb,
    referenced_tags_json jsonb,
    keyword_ids_json jsonb,
    wiki_page_title character varying(255) DEFAULT NULL::character varying,
    wiki_page_url character varying(512) DEFAULT NULL::character varying,
    source character varying(64) DEFAULT 'pending'::character varying NOT NULL,
    source_payload_json jsonb,
    source_hash character(64) DEFAULT NULL::bpchar,
    first_seen_at timestamp with time zone,
    last_seen_at timestamp with time zone,
    changed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone
);

CREATE TABLE catalog.hero_skins (
    card_id character varying(96) NOT NULL,
    dbf bigint,
    release_date date,
    page_title character varying(255) NOT NULL,
    page_url character varying(512) NOT NULL,
    name_en character varying(255) NOT NULL,
    class_id integer,
    class_slug character varying(64) DEFAULT NULL::character varying,
    class_name_en character varying(80) DEFAULT NULL::character varying,
    class_name_ru character varying(80) DEFAULT NULL::character varying,
    health smallint,
    character_name character varying(255) DEFAULT NULL::character varying,
    actor character varying(255) DEFAULT NULL::character varying,
    artist character varying(255) DEFAULT NULL::character varying,
    rarity_slug character varying(48) DEFAULT NULL::character varying,
    rarity_name_en character varying(80) DEFAULT NULL::character varying,
    rarity_name_ru character varying(80) DEFAULT NULL::character varying,
    primary_category_slug character varying(96) DEFAULT NULL::character varying,
    primary_category_en character varying(160) DEFAULT NULL::character varying,
    primary_category_ru character varying(160) DEFAULT NULL::character varying,
    categories_json jsonb,
    tags_json jsonb,
    static_image_file character varying(255) DEFAULT NULL::character varying,
    static_image_url character varying(512) DEFAULT NULL::character varying,
    animated_image_file character varying(255) DEFAULT NULL::character varying,
    animated_image_url character varying(512) DEFAULT NULL::character varying,
    animated_asset_json jsonb,
    full_art_file character varying(255) DEFAULT NULL::character varying,
    full_art_url character varying(512) DEFAULT NULL::character varying,
    gallery_json jsonb,
    sounds_json jsonb,
    source_payload_json jsonb,
    source_hash character(64) DEFAULT NULL::bpchar,
    status character varying(32) DEFAULT 'ok'::character varying NOT NULL,
    error text,
    fetched_at timestamp with time zone,
    changed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone
);

ALTER TABLE ONLY analytics.archetype_snapshots ALTER COLUMN id SET DEFAULT nextval('analytics.archetype_snapshots_id_seq'::regclass);

ALTER TABLE ONLY analytics.bg_minion_snapshots ALTER COLUMN id SET DEFAULT nextval('analytics.bg_minion_snapshots_id_seq'::regclass);

ALTER TABLE ONLY analytics.card_popularity_history ALTER COLUMN id SET DEFAULT nextval('analytics.card_popularity_history_id_seq'::regclass);

ALTER TABLE ONLY catalog.battlegrounds_cards ALTER COLUMN id SET DEFAULT nextval('catalog.battlegrounds_cards_id_seq'::regclass);

ALTER TABLE ONLY analytics.card_popularity_history
    ADD CONSTRAINT idx_813116_card_popularity_history_pkey PRIMARY KEY (id);

ALTER TABLE ONLY analytics.hsreplay_archetypes
    ADD CONSTRAINT idx_813131_hsreplay_archetypes_pkey PRIMARY KEY (archetype_id);

ALTER TABLE ONLY analytics.archetype_snapshots
    ADD CONSTRAINT idx_813138_archetype_snapshots_pkey PRIMARY KEY (id);

ALTER TABLE ONLY analytics.bg_minions
    ADD CONSTRAINT idx_813183_bg_minions_pkey PRIMARY KEY (dbf_id);

ALTER TABLE ONLY analytics.bg_minion_snapshots
    ADD CONSTRAINT idx_813189_bg_minion_snapshots_pkey PRIMARY KEY (id);

ALTER TABLE ONLY catalog.battlegrounds_cards
    ADD CONSTRAINT idx_785665_primary PRIMARY KEY (id);

ALTER TABLE ONLY catalog.battlegrounds_heroes
    ADD CONSTRAINT idx_785770_primary PRIMARY KEY (card_id);

ALTER TABLE ONLY catalog.constructed_cards
    ADD CONSTRAINT idx_785959_primary PRIMARY KEY (card_id);

ALTER TABLE ONLY catalog.hero_skins
    ADD CONSTRAINT idx_786097_primary PRIMARY KEY (card_id);
