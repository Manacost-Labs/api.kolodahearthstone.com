-- No transaction boundaries: this also runs inside the atomic shadow merge.
CREATE TABLE IF NOT EXISTS analytics.hsguru_deck_radar_snapshots (
    source_id text NOT NULL,
    source_fetched_at text NOT NULL,
    recorded_at text NOT NULL,
    PRIMARY KEY (source_id, source_fetched_at)
);
CREATE TABLE IF NOT EXISTS analytics.hsguru_deck_radar_observations (
    source_id text NOT NULL,
    source_fetched_at text NOT NULL,
    deck_fingerprint text NOT NULL,
    deck_code text NOT NULL,
    deck_name text,
    streamer text,
    format text,
    source_url text,
    PRIMARY KEY (source_id, source_fetched_at, deck_fingerprint),
    FOREIGN KEY (source_id, source_fetched_at)
        REFERENCES analytics.hsguru_deck_radar_snapshots(source_id, source_fetched_at)
);
CREATE TABLE IF NOT EXISTS analytics.hsguru_deck_radar_events (
    deck_fingerprint text PRIMARY KEY,
    event_type text NOT NULL,
    status text NOT NULL CHECK (status IN ('candidate', 'confirmed')),
    deck_code text NOT NULL,
    deck_name text,
    streamer text,
    format text,
    source_url text,
    first_seen_at text NOT NULL,
    last_seen_at text NOT NULL,
    observation_count bigint NOT NULL DEFAULT 1 CHECK (observation_count > 0)
);
CREATE INDEX IF NOT EXISTS hsguru_deck_radar_events_latest_idx
    ON analytics.hsguru_deck_radar_events(status, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS hsguru_deck_radar_observations_fingerprint_idx
    ON analytics.hsguru_deck_radar_observations(deck_fingerprint, source_fetched_at DESC);
