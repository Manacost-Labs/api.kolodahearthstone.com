BEGIN;
-- HSReplay card choice statistics (source hsreplay_card_choices).
-- SQLite app.hsreplay_card_choices_store owns the rows;
-- scripts/stage-analytics.py mirrors them incrementally. Column names and
-- order must equal the SQLite tables, and the first primary-key column must be
-- bigint so the delta can bucket by id. Types follow the loader conversion:
-- INTEGER -> bigint, REAL -> real, TEXT -> text, *_json -> jsonb. Targets are
-- updated in place and old snapshots age out in SQLite, so none of these
-- tables is append-only in the delta contract.
CREATE TABLE IF NOT EXISTS analytics.hsreplay_card_choice_targets (
    id bigint PRIMARY KEY,
    target_key text NOT NULL,
    card_id text NOT NULL,
    dbf_id bigint NOT NULL,
    format text NOT NULL,
    rank text NOT NULL,
    kind text NOT NULL,
    tier bigint NOT NULL,
    priority bigint NOT NULL,
    times_played bigint,
    active bigint NOT NULL,
    planned_at text NOT NULL,
    state text NOT NULL,
    last_attempt_at text,
    last_success_at text,
    last_changed_at text,
    consecutive_failures bigint NOT NULL,
    next_attempt_after text,
    latest_snapshot_id bigint,
    last_error text
);
CREATE TABLE IF NOT EXISTS analytics.hsreplay_card_choice_snapshots (
    id bigint PRIMARY KEY,
    target_id bigint NOT NULL,
    card_id text NOT NULL,
    dbf_id bigint NOT NULL,
    format text NOT NULL,
    rank text NOT NULL,
    fetched_at text NOT NULL,
    as_of text,
    rows_total bigint NOT NULL,
    unidentified_rows bigint NOT NULL,
    content_hash text NOT NULL,
    source_url text NOT NULL,
    backend text NOT NULL,
    request_credits bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.hsreplay_card_choice_rows (
    id bigint PRIMARY KEY,
    snapshot_id bigint NOT NULL,
    position bigint NOT NULL,
    dbf_id bigint NOT NULL,
    card_id text,
    popularity real,
    win_rate real
);
CREATE TABLE IF NOT EXISTS analytics.hsreplay_card_choice_runs (
    id bigint PRIMARY KEY,
    started_at text NOT NULL,
    finished_at text,
    state text NOT NULL,
    premium_state text,
    targets_planned bigint NOT NULL,
    targets_due bigint NOT NULL,
    attempted bigint NOT NULL,
    changed bigint NOT NULL,
    unchanged bigint NOT NULL,
    no_data bigint NOT NULL,
    failed bigint NOT NULL,
    requests bigint NOT NULL,
    scrape_do_credits bigint NOT NULL,
    stop_reason text,
    detail_json jsonb
);

CREATE INDEX IF NOT EXISTS hsreplay_card_choice_targets_card_idx
    ON analytics.hsreplay_card_choice_targets (card_id, format, rank);
CREATE INDEX IF NOT EXISTS hsreplay_card_choice_snapshots_target_idx
    ON analytics.hsreplay_card_choice_snapshots (target_id, id DESC);
CREATE INDEX IF NOT EXISTS hsreplay_card_choice_rows_snapshot_idx
    ON analytics.hsreplay_card_choice_rows (snapshot_id, position);
CREATE INDEX IF NOT EXISTS hsreplay_card_choice_runs_finished_idx
    ON analytics.hsreplay_card_choice_runs (finished_at, id DESC);

-- Latest pick row of every option of every stored card, format and rank.
CREATE OR REPLACE VIEW hub.hsreplay_card_choices_latest AS
SELECT
    target.id AS target_id,
    target.card_id,
    target.dbf_id,
    target.format,
    target.rank,
    target.kind,
    target.active = 1 AS active,
    target.last_success_at::timestamptz AS checked_at,
    snapshot.id AS snapshot_id,
    snapshot.fetched_at::timestamptz AS changed_at,
    snapshot.as_of,
    choice.position,
    choice.dbf_id AS option_dbf_id,
    choice.card_id AS option_card_id,
    choice.popularity,
    choice.win_rate
FROM analytics.hsreplay_card_choice_targets AS target
JOIN analytics.hsreplay_card_choice_snapshots AS snapshot
    ON snapshot.id = target.latest_snapshot_id
JOIN analytics.hsreplay_card_choice_rows AS choice ON choice.snapshot_id = snapshot.id;

GRANT SELECT ON hub.hsreplay_card_choices_latest TO hs_data_reader;

INSERT INTO platform.schema_migrations (version) VALUES ('020_hsreplay_card_choices')
ON CONFLICT (version) DO NOTHING;
COMMIT;
