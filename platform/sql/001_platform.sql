BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS platform;
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS raw;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE TABLE IF NOT EXISTS platform.schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.data_sources (
    source_id text PRIMARY KEY,
    source_kind text NOT NULL CHECK (source_kind IN ('mariadb', 'sqlite', 'json', 'api')),
    display_name text NOT NULL,
    target_schema text NOT NULL,
    sync_mode text NOT NULL DEFAULT 'shadow' CHECK (sync_mode IN ('shadow', 'read', 'write', 'retired')),
    is_enabled boolean NOT NULL DEFAULT true,
    last_synced_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS platform.sync_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id text NOT NULL REFERENCES platform.data_sources(source_id),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    state text NOT NULL CHECK (state IN ('running', 'ok', 'failed')),
    rows_read bigint NOT NULL DEFAULT 0,
    rows_written bigint NOT NULL DEFAULT 0,
    error_code text,
    error_message text,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS sync_runs_source_started_idx
    ON platform.sync_runs (source_id, started_at DESC);

CREATE TABLE IF NOT EXISTS raw.datasets (
    source_id text NOT NULL,
    dataset_version text NOT NULL,
    fetched_at timestamptz,
    state text,
    payload jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, dataset_version)
);

CREATE INDEX IF NOT EXISTS raw_datasets_fetched_idx
    ON raw.datasets (source_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS raw_datasets_payload_idx
    ON raw.datasets USING gin (payload jsonb_path_ops);

INSERT INTO platform.data_sources (source_id, source_kind, display_name, target_schema)
VALUES
    ('catalog-mariadb', 'mariadb', 'Hearthstone catalogues', 'catalog'),
    ('analytics-sqlite', 'sqlite', 'Hearthstone analytics', 'analytics'),
    ('parser-json', 'json', 'Parser datasets', 'raw')
ON CONFLICT (source_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    target_schema = EXCLUDED.target_schema,
    updated_at = now();

INSERT INTO platform.schema_migrations (version)
VALUES ('001_platform')
ON CONFLICT (version) DO NOTHING;

COMMIT;
