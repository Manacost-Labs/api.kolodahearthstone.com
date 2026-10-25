BEGIN;
-- HSGuru mulligan guide and class matchups of archetypes and decks (source
-- hsguru_mulligan). SQLite app.hsguru_mulligan_store owns the rows;
-- scripts/stage-analytics.py mirrors them incrementally. Column names and
-- order must equal the SQLite tables, and the first primary-key column must be
-- bigint so the delta can bucket by id. Types follow the loader conversion:
-- INTEGER -> bigint, REAL -> real, TEXT -> text, *_json -> jsonb. Targets are
-- updated in place and old snapshots age out in SQLite, so none of these
-- tables is append-only in the delta contract.
CREATE TABLE IF NOT EXISTS analytics.hsguru_stat_targets (
    id bigint PRIMARY KEY,
    target_key text NOT NULL,
    kind text NOT NULL,
    subject_type text NOT NULL,
    format text NOT NULL,
    archetype text NOT NULL,
    deck_id bigint,
    rank text NOT NULL,
    period text NOT NULL,
    coin text NOT NULL,
    opponent_class text NOT NULL,
    tier bigint NOT NULL,
    expected_games bigint,
    active bigint NOT NULL,
    planned_at text NOT NULL,
    state text NOT NULL,
    last_attempt_at text,
    last_success_at text,
    last_changed_at text,
    last_games bigint,
    success_expected_games bigint,
    consecutive_failures bigint NOT NULL,
    next_attempt_after text,
    latest_snapshot_id bigint,
    last_error text
);
CREATE TABLE IF NOT EXISTS analytics.hsguru_stat_snapshots (
    id bigint PRIMARY KEY,
    target_id bigint NOT NULL,
    kind text NOT NULL,
    subject_type text NOT NULL,
    format text NOT NULL,
    archetype text NOT NULL,
    deck_id bigint,
    rank text NOT NULL,
    period text NOT NULL,
    coin text NOT NULL,
    opponent_class text NOT NULL,
    fetched_at text NOT NULL,
    games bigint,
    winrate real,
    rows_total bigint NOT NULL,
    content_hash text NOT NULL,
    source_url text NOT NULL,
    backend text NOT NULL,
    request_credits bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.hsguru_mulligan_cards (
    id bigint PRIMARY KEY,
    snapshot_id bigint NOT NULL,
    position bigint NOT NULL,
    card_id text,
    dbf_id bigint,
    card_name text NOT NULL,
    mulligan_impact real,
    mulligan_count bigint,
    drawn_impact real,
    drawn_count bigint,
    not_drawn_impact real,
    not_drawn_count bigint,
    kept_impact real,
    kept_count bigint
);
CREATE TABLE IF NOT EXISTS analytics.hsguru_class_matchups (
    id bigint PRIMARY KEY,
    snapshot_id bigint NOT NULL,
    opponent_class text NOT NULL,
    winrate real,
    games bigint NOT NULL,
    share_pct real
);
CREATE TABLE IF NOT EXISTS analytics.hsguru_stat_runs (
    id bigint PRIMARY KEY,
    started_at text NOT NULL,
    finished_at text,
    state text NOT NULL,
    targets_planned bigint NOT NULL,
    targets_due bigint NOT NULL,
    attempted bigint NOT NULL,
    changed bigint NOT NULL,
    unchanged bigint NOT NULL,
    no_data bigint NOT NULL,
    failed bigint NOT NULL,
    pages_free bigint NOT NULL,
    pages_scrape_do bigint NOT NULL,
    scrape_do_credits bigint NOT NULL,
    stop_reason text,
    detail_json jsonb
);

CREATE INDEX IF NOT EXISTS hsguru_stat_targets_subject_idx
    ON analytics.hsguru_stat_targets (subject_type, format, archetype, deck_id);
CREATE INDEX IF NOT EXISTS hsguru_stat_targets_slice_idx
    ON analytics.hsguru_stat_targets (kind, rank, period, coin, opponent_class);
CREATE INDEX IF NOT EXISTS hsguru_stat_snapshots_target_idx
    ON analytics.hsguru_stat_snapshots (target_id, id DESC);
CREATE INDEX IF NOT EXISTS hsguru_mulligan_cards_snapshot_idx
    ON analytics.hsguru_mulligan_cards (snapshot_id, position);
CREATE INDEX IF NOT EXISTS hsguru_class_matchups_snapshot_idx
    ON analytics.hsguru_class_matchups (snapshot_id, opponent_class);

-- Latest mulligan row of every card for every stored slice.
CREATE OR REPLACE VIEW hub.hsguru_mulligan_latest AS
SELECT
    target.id AS target_id,
    target.subject_type,
    target.format,
    target.archetype,
    target.deck_id,
    target.rank,
    target.period,
    target.coin,
    target.opponent_class,
    target.active = 1 AS active,
    target.last_success_at::timestamptz AS checked_at,
    snapshot.id AS snapshot_id,
    snapshot.fetched_at::timestamptz AS changed_at,
    snapshot.games,
    card.position,
    card.card_id,
    card.dbf_id,
    card.card_name,
    card.mulligan_impact,
    card.mulligan_count,
    card.drawn_impact,
    card.drawn_count,
    card.not_drawn_impact,
    card.not_drawn_count,
    card.kept_impact,
    card.kept_count
FROM analytics.hsguru_stat_targets AS target
JOIN analytics.hsguru_stat_snapshots AS snapshot ON snapshot.id = target.latest_snapshot_id
JOIN analytics.hsguru_mulligan_cards AS card ON card.snapshot_id = snapshot.id
WHERE target.kind = 'cards';

-- Latest per-class winrate for every stored archetype/deck slice.
CREATE OR REPLACE VIEW hub.hsguru_class_matchups_latest AS
SELECT
    target.id AS target_id,
    target.subject_type,
    target.format,
    target.archetype,
    target.deck_id,
    target.rank,
    target.period,
    target.coin,
    target.active = 1 AS active,
    target.last_success_at::timestamptz AS checked_at,
    snapshot.id AS snapshot_id,
    snapshot.fetched_at::timestamptz AS changed_at,
    snapshot.games AS total_games,
    snapshot.winrate AS total_winrate,
    matchup.opponent_class,
    matchup.winrate,
    matchup.games,
    matchup.share_pct
FROM analytics.hsguru_stat_targets AS target
JOIN analytics.hsguru_stat_snapshots AS snapshot ON snapshot.id = target.latest_snapshot_id
JOIN analytics.hsguru_class_matchups AS matchup ON matchup.snapshot_id = snapshot.id
WHERE target.kind = 'matchups';

GRANT SELECT ON hub.hsguru_mulligan_latest, hub.hsguru_class_matchups_latest TO hs_data_reader;

INSERT INTO platform.schema_migrations (version) VALUES ('017_hsguru_mulligan')
ON CONFLICT (version) DO NOTHING;
COMMIT;
