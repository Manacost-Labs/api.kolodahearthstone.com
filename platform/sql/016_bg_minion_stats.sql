BEGIN;
-- HSReplay Battlegrounds minion statistics (source hsreplay_battlegrounds_minion_stats).
-- SQLite app.bg_minion_stats_store owns the rows; scripts/stage-analytics.py mirrors
-- them incrementally. Column names and order must equal the SQLite tables, and
-- the first primary-key column must be bigint so the delta can bucket by id.
-- Types follow the loader conversion: INTEGER -> bigint, REAL -> real,
-- TEXT -> text, *_json -> jsonb. Detail tables age out in SQLite (retention),
-- so none of them is append-only in the delta contract.
CREATE TABLE IF NOT EXISTS analytics.bg_minion_stat_snapshots (
    id bigint PRIMARY KEY,
    source_id text NOT NULL,
    mmr_percentile text NOT NULL,
    time_range text NOT NULL,
    fetched_at text NOT NULL,
    list_as_of text,
    positions_as_of text,
    purchases_as_of text,
    medians_as_of text,
    minions_total bigint NOT NULL,
    has_positions bigint NOT NULL,
    pct_players_remaining_json jsonb NOT NULL,
    composition_names_json jsonb
);
CREATE TABLE IF NOT EXISTS analytics.bg_minion_stat_summaries (
    id bigint PRIMARY KEY,
    snapshot_id bigint NOT NULL,
    dbf_id bigint NOT NULL,
    variant text NOT NULL,
    tavern_tier bigint,
    composition_ids_json jsonb,
    impact real,
    combat_winrate real,
    popularity real,
    games_with_minion bigint NOT NULL,
    games_without_minion bigint NOT NULL,
    avg_placement_with real,
    avg_placement_without real,
    wins bigint NOT NULL,
    losses bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.bg_minion_stat_rounds (
    id bigint PRIMARY KEY,
    snapshot_id bigint NOT NULL,
    dbf_id bigint NOT NULL,
    variant text NOT NULL,
    combat_round bigint NOT NULL,
    distinct_games bigint,
    games_with_minion bigint NOT NULL,
    games_without_minion bigint NOT NULL,
    sum_placement_with bigint NOT NULL,
    sum_placement_without bigint NOT NULL,
    wins bigint,
    losses bigint,
    at_least_one real,
    impact real,
    combat_winrate real
);
CREATE TABLE IF NOT EXISTS analytics.bg_minion_stat_positions (
    id bigint PRIMARY KEY,
    snapshot_id bigint NOT NULL,
    dbf_id bigint NOT NULL,
    composition_id bigint NOT NULL,
    board text NOT NULL,
    occurrences bigint NOT NULL,
    sum_placement bigint NOT NULL,
    position_occurrences_json jsonb NOT NULL,
    position_sum_placement_json jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.bg_minion_stat_purchases (
    id bigint PRIMARY KEY,
    snapshot_id bigint NOT NULL,
    dbf_id bigint NOT NULL,
    recruitment_round bigint NOT NULL,
    times_picked_0_owned bigint NOT NULL,
    times_picked_1_owned bigint NOT NULL,
    times_picked_2_owned bigint NOT NULL,
    times_offered_0_owned bigint NOT NULL,
    times_offered_1_owned bigint NOT NULL,
    times_offered_2_owned bigint NOT NULL,
    times_discover_picked_0_owned bigint NOT NULL,
    times_discover_picked_1_owned bigint NOT NULL,
    times_discover_picked_2_owned bigint NOT NULL,
    times_discover_offered_0_owned bigint NOT NULL,
    times_discover_offered_1_owned bigint NOT NULL,
    times_discover_offered_2_owned bigint NOT NULL,
    total_times_offered bigint NOT NULL,
    total_times_discover_offered bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.bg_minion_stat_medians (
    id bigint PRIMARY KEY,
    snapshot_id bigint NOT NULL,
    dbf_id bigint NOT NULL,
    variant text NOT NULL,
    combat_round bigint NOT NULL,
    median_attack real,
    median_health real
);

CREATE INDEX IF NOT EXISTS bg_minion_stat_snapshots_slice_idx
    ON analytics.bg_minion_stat_snapshots (mmr_percentile, time_range, id DESC);
CREATE INDEX IF NOT EXISTS bg_minion_stat_summaries_snapshot_idx
    ON analytics.bg_minion_stat_summaries (snapshot_id, dbf_id);
CREATE INDEX IF NOT EXISTS bg_minion_stat_summaries_dbf_idx
    ON analytics.bg_minion_stat_summaries (dbf_id, snapshot_id);
CREATE INDEX IF NOT EXISTS bg_minion_stat_rounds_snapshot_idx
    ON analytics.bg_minion_stat_rounds (snapshot_id, dbf_id);
CREATE INDEX IF NOT EXISTS bg_minion_stat_positions_snapshot_idx
    ON analytics.bg_minion_stat_positions (snapshot_id, dbf_id, composition_id);
CREATE INDEX IF NOT EXISTS bg_minion_stat_purchases_snapshot_idx
    ON analytics.bg_minion_stat_purchases (snapshot_id, dbf_id);
CREATE INDEX IF NOT EXISTS bg_minion_stat_medians_snapshot_idx
    ON analytics.bg_minion_stat_medians (snapshot_id, dbf_id);

-- Latest totals of every minion per slice, joined with the card catalogue.
CREATE OR REPLACE VIEW hub.bg_minion_stat_latest AS
SELECT
    snapshot.id AS snapshot_id,
    snapshot.mmr_percentile,
    snapshot.time_range,
    snapshot.fetched_at::timestamptz AS fetched_at,
    snapshot.list_as_of,
    summary.dbf_id,
    summary.variant,
    summary.tavern_tier,
    card.card_id,
    card.name::text AS name_ru,
    card.name_en::text AS name_en,
    summary.impact,
    summary.combat_winrate,
    summary.popularity,
    summary.games_with_minion,
    summary.games_without_minion,
    summary.wins,
    summary.losses
FROM analytics.bg_minion_stat_snapshots AS snapshot
JOIN (
    SELECT max(id) AS id
    FROM analytics.bg_minion_stat_snapshots
    GROUP BY mmr_percentile, time_range
) AS latest ON latest.id = snapshot.id
JOIN analytics.bg_minion_stat_summaries AS summary ON summary.snapshot_id = snapshot.id
LEFT JOIN catalog.battlegrounds_cards AS card
  ON card.variant_kind = 'base' AND card.dbf = summary.dbf_id;

-- Every stored snapshot's totals: the "how did it change" series.
CREATE OR REPLACE VIEW hub.bg_minion_stat_history AS
SELECT
    snapshot.id AS snapshot_id,
    snapshot.mmr_percentile,
    snapshot.time_range,
    snapshot.fetched_at::timestamptz AS fetched_at,
    snapshot.list_as_of,
    summary.dbf_id,
    summary.variant,
    summary.impact,
    summary.combat_winrate,
    summary.popularity,
    summary.games_with_minion,
    summary.wins,
    summary.losses
FROM analytics.bg_minion_stat_snapshots AS snapshot
JOIN analytics.bg_minion_stat_summaries AS summary ON summary.snapshot_id = snapshot.id;

GRANT SELECT ON hub.bg_minion_stat_latest, hub.bg_minion_stat_history TO hs_data_reader;

INSERT INTO platform.schema_migrations (version) VALUES ('016_bg_minion_stats')
ON CONFLICT (version) DO NOTHING;
COMMIT;
