BEGIN;
-- HSGuru streamers, their decks, past broadcasts and who is live (source
-- hsguru_streamers). SQLite app.hsguru_streamers_store owns the rows;
-- scripts/stage-analytics.py mirrors them incrementally. Column names and
-- order must equal the SQLite tables, and the first primary-key column must be
-- bigint so the delta can bucket by id. Types follow the loader conversion:
-- INTEGER -> bigint, TEXT -> text, *_json -> jsonb. Streamers and decks are
-- updated in place, the live table is replaced on every run and old VODs and
-- runs age out, so none of these tables is append-only in the delta contract.
CREATE TABLE IF NOT EXISTS analytics.hsguru_streamers (
    id bigint PRIMARY KEY,
    display_name text NOT NULL,
    login text,
    twitch_url text,
    youtube_url text,
    x_url text,
    links_json jsonb,
    avatar_url text,
    description text,
    followers bigint,
    language text,
    first_seen_at text NOT NULL,
    last_played_at text,
    decks_total bigint NOT NULL,
    profile_state text NOT NULL,
    profile_checked_at text,
    profile_error text,
    updated_at text NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.hsguru_streamer_decks (
    id bigint PRIMARY KEY,
    streamer_id bigint NOT NULL,
    deck_id bigint NOT NULL,
    format text NOT NULL,
    deck_code text NOT NULL,
    archetype text NOT NULL,
    player_class text,
    peak_rank bigint,
    latest_rank bigint,
    worst_rank bigint,
    wins bigint,
    losses bigint,
    last_played_at text NOT NULL,
    first_seen_at text NOT NULL,
    updated_at text NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.hsguru_streamer_vods (
    id bigint PRIMARY KEY,
    streamer_id bigint NOT NULL,
    title text,
    url text NOT NULL,
    started_at text NOT NULL,
    duration_seconds bigint,
    views bigint,
    thumbnail_url text,
    seen_at text NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.hsguru_streamer_live (
    id bigint PRIMARY KEY,
    run_id bigint NOT NULL,
    login text NOT NULL,
    display_name text NOT NULL,
    streamer_id bigint,
    platform text NOT NULL,
    title text,
    viewers bigint,
    mode text,
    language text,
    started_at text,
    thumbnail_url text,
    stream_url text NOT NULL,
    observed_at text NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.hsguru_streamer_runs (
    id bigint PRIMARY KEY,
    started_at text NOT NULL,
    finished_at text,
    state text NOT NULL,
    live_streams bigint,
    deck_pages bigint NOT NULL,
    deck_rows bigint NOT NULL,
    decks_new bigint NOT NULL,
    decks_updated bigint NOT NULL,
    decks_cutoff text,
    decks_complete bigint NOT NULL,
    profiles_attempted bigint NOT NULL,
    profiles_ok bigint NOT NULL,
    scrape_do_credits bigint NOT NULL,
    stop_reason text,
    detail_json jsonb
);

CREATE INDEX IF NOT EXISTS hsguru_streamers_login_idx
    ON analytics.hsguru_streamers (login);
CREATE INDEX IF NOT EXISTS hsguru_streamer_decks_streamer_idx
    ON analytics.hsguru_streamer_decks (streamer_id, last_played_at DESC);
CREATE INDEX IF NOT EXISTS hsguru_streamer_decks_played_idx
    ON analytics.hsguru_streamer_decks (last_played_at DESC);
CREATE INDEX IF NOT EXISTS hsguru_streamer_vods_streamer_idx
    ON analytics.hsguru_streamer_vods (streamer_id, started_at DESC);

-- Every streamer deck with the streamer's name and channel links.
CREATE OR REPLACE VIEW hub.hsguru_streamer_decks_latest AS
SELECT
    deck.streamer_id,
    streamer.display_name,
    streamer.login,
    streamer.twitch_url,
    streamer.youtube_url,
    streamer.x_url,
    deck.deck_id,
    deck.format,
    deck.deck_code,
    deck.archetype,
    deck.player_class,
    deck.peak_rank,
    deck.latest_rank,
    deck.worst_rank,
    deck.wins,
    deck.losses,
    deck.last_played_at::timestamptz AS last_played_at
FROM analytics.hsguru_streamer_decks AS deck
JOIN analytics.hsguru_streamers AS streamer ON streamer.id = deck.streamer_id;

GRANT SELECT ON hub.hsguru_streamer_decks_latest TO hs_data_reader;

INSERT INTO platform.schema_migrations (version) VALUES ('018_hsguru_streamers')
ON CONFLICT (version) DO NOTHING;
COMMIT;
