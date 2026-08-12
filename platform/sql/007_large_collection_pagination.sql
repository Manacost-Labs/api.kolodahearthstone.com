BEGIN;

CREATE INDEX IF NOT EXISTS battlegrounds_heroes_cursor_idx
    ON catalog.battlegrounds_heroes (
        (COALESCE(name_ru, name_en, '')),
        card_id
    );

CREATE INDEX IF NOT EXISTS game_stat_snapshots_cursor_idx
    ON analytics.game_stat_snapshots (
        fetched_at DESC,
        source_id,
        dataset_version,
        domain,
        entity_type,
        format_name,
        rank_range,
        period,
        mode,
        rating_bracket,
        id
    );

CREATE INDEX IF NOT EXISTS archetype_snapshots_cursor_idx
    ON analytics.archetype_snapshots (
        total_games DESC,
        archetype_id,
        game_type,
        rank_range,
        region,
        id
    );

CREATE INDEX IF NOT EXISTS bg_minion_snapshots_cursor_idx
    ON analytics.bg_minion_snapshots (
        tavern_tier,
        impact DESC,
        dbf_id,
        mmr_percentile,
        time_range,
        id
    );

CREATE INDEX IF NOT EXISTS platform_sources_cursor_idx
    ON platform.data_sources (display_name, source_id);

CREATE INDEX IF NOT EXISTS raw_datasets_global_cursor_idx
    ON raw.datasets (fetched_at DESC, source_id, imported_at DESC);

INSERT INTO platform.schema_migrations (version)
VALUES ('007_large_collection_pagination')
ON CONFLICT (version) DO NOTHING;

COMMIT;
