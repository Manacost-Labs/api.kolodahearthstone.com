BEGIN;
-- HSReplay deck mulligan guide (mulligan v2, source hsreplay_mulligan).
-- SQLite app.hsreplay_mulligan_store owns the rows;
-- scripts/stage-analytics.py mirrors them incrementally. Column names and
-- order must equal the SQLite tables, and the first primary-key column must be
-- bigint so the delta can bucket by id. Types follow the loader conversion:
-- INTEGER -> bigint, REAL -> real, TEXT -> text, *_json -> jsonb. The deck pool
-- and targets are updated in place and old snapshots age out in SQLite, so none
-- of these tables is append-only in the delta contract.
CREATE TABLE IF NOT EXISTS analytics.hsreplay_mulligan_decks (
    id bigint PRIMARY KEY,
    format text NOT NULL,
    rank text NOT NULL,
    deck_id text NOT NULL,
    archetype_id bigint,
    archetype_name text,
    player_class text,
    total_games bigint,
    win_rate real,
    deck_list text,
    pick_reason text NOT NULL,
    position bigint NOT NULL,
    active bigint NOT NULL,
    listed_at text NOT NULL,
    as_of text
);
CREATE TABLE IF NOT EXISTS analytics.hsreplay_mulligan_targets (
    id bigint PRIMARY KEY,
    target_key text NOT NULL,
    deck_id text NOT NULL,
    format text NOT NULL,
    rank text NOT NULL,
    initiative text NOT NULL,
    period text NOT NULL,
    tier bigint NOT NULL,
    premium bigint NOT NULL,
    active bigint NOT NULL,
    planned_at text NOT NULL,
    state text NOT NULL,
    last_attempt_at text,
    last_success_at text,
    last_changed_at text,
    last_games bigint,
    consecutive_failures bigint NOT NULL,
    next_attempt_after text,
    latest_snapshot_id bigint,
    last_error text
);
CREATE TABLE IF NOT EXISTS analytics.hsreplay_mulligan_snapshots (
    id bigint PRIMARY KEY,
    target_id bigint NOT NULL,
    deck_id text NOT NULL,
    format text NOT NULL,
    rank text NOT NULL,
    initiative text NOT NULL,
    period text NOT NULL,
    fetched_at text NOT NULL,
    games bigint,
    base_winrate real,
    scope text,
    as_of text,
    groups_total bigint NOT NULL,
    rows_total bigint NOT NULL,
    content_hash text NOT NULL,
    source_url text NOT NULL,
    backend text NOT NULL,
    request_credits bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.hsreplay_mulligan_groups (
    id bigint PRIMARY KEY,
    snapshot_id bigint NOT NULL,
    opponent_class text NOT NULL,
    games bigint,
    base_winrate real,
    scope text,
    as_of text,
    deck_games bigint,
    deck_winrate real,
    archetype_games bigint,
    archetype_winrate real,
    class_games bigint,
    class_winrate real
);
CREATE TABLE IF NOT EXISTS analytics.hsreplay_mulligan_cards (
    id bigint PRIMARY KEY,
    snapshot_id bigint NOT NULL,
    opponent_class text NOT NULL,
    position bigint NOT NULL,
    dbf_id bigint NOT NULL,
    card_id text,
    card_name text,
    keep_percentage real,
    opening_hand_winrate real,
    winrate_when_drawn real,
    winrate_when_played real,
    avg_turns_in_hand real,
    avg_turn_played_on real
);
CREATE TABLE IF NOT EXISTS analytics.hsreplay_mulligan_runs (
    id bigint PRIMARY KEY,
    started_at text NOT NULL,
    finished_at text,
    state text NOT NULL,
    premium_state text,
    decks_listed bigint NOT NULL,
    targets_planned bigint NOT NULL,
    targets_due bigint NOT NULL,
    attempted bigint NOT NULL,
    changed bigint NOT NULL,
    unchanged bigint NOT NULL,
    no_data bigint NOT NULL,
    failed bigint NOT NULL,
    skipped_premium bigint NOT NULL,
    requests bigint NOT NULL,
    scrape_do_credits bigint NOT NULL,
    stop_reason text,
    detail_json jsonb
);

CREATE INDEX IF NOT EXISTS hsreplay_mulligan_decks_list_idx
    ON analytics.hsreplay_mulligan_decks (format, rank, active, position);
CREATE INDEX IF NOT EXISTS hsreplay_mulligan_targets_slice_idx
    ON analytics.hsreplay_mulligan_targets (deck_id, format, rank, initiative, period);
CREATE INDEX IF NOT EXISTS hsreplay_mulligan_snapshots_target_idx
    ON analytics.hsreplay_mulligan_snapshots (target_id, id DESC);
CREATE INDEX IF NOT EXISTS hsreplay_mulligan_groups_snapshot_idx
    ON analytics.hsreplay_mulligan_groups (snapshot_id, opponent_class);
CREATE INDEX IF NOT EXISTS hsreplay_mulligan_cards_snapshot_idx
    ON analytics.hsreplay_mulligan_cards (snapshot_id, opponent_class, position);
CREATE INDEX IF NOT EXISTS hsreplay_mulligan_runs_finished_idx
    ON analytics.hsreplay_mulligan_runs (finished_at, id DESC);

-- Latest mulligan row of every card, opponent class and stored deck slice.
CREATE OR REPLACE VIEW hub.hsreplay_mulligan_latest AS
SELECT
    target.id AS target_id,
    target.deck_id,
    target.format,
    target.rank,
    target.initiative,
    target.period,
    target.premium = 1 AS premium,
    target.active = 1 AS active,
    target.last_success_at::timestamptz AS checked_at,
    snapshot.id AS snapshot_id,
    snapshot.fetched_at::timestamptz AS changed_at,
    grp.opponent_class,
    grp.games,
    grp.base_winrate,
    grp.scope,
    card.position,
    card.dbf_id,
    card.card_id,
    card.card_name,
    card.keep_percentage,
    card.opening_hand_winrate,
    card.winrate_when_drawn,
    card.winrate_when_played,
    card.avg_turns_in_hand,
    card.avg_turn_played_on
FROM analytics.hsreplay_mulligan_targets AS target
JOIN analytics.hsreplay_mulligan_snapshots AS snapshot
    ON snapshot.id = target.latest_snapshot_id
JOIN analytics.hsreplay_mulligan_groups AS grp ON grp.snapshot_id = snapshot.id
JOIN analytics.hsreplay_mulligan_cards AS card
    ON card.snapshot_id = snapshot.id AND card.opponent_class = grp.opponent_class;

GRANT SELECT ON hub.hsreplay_mulligan_latest TO hs_data_reader;

INSERT INTO platform.schema_migrations (version) VALUES ('019_hsreplay_mulligan')
ON CONFLICT (version) DO NOTHING;
COMMIT;
