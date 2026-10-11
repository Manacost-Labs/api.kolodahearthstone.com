BEGIN;

-- Install before enabling source triggers: the mirror rejects unknown tables.
CREATE TABLE IF NOT EXISTS catalog.catalog_card_versions (
    id bigint PRIMARY KEY,
    entity varchar(40) NOT NULL,
    card_id varchar(191) NOT NULL,
    observed_at timestamptz NOT NULL,
    event_kind varchar(24) NOT NULL,
    previous_hash char(64),
    snapshot_hash char(64) NOT NULL,
    snapshot_json text NOT NULL CHECK (snapshot_json::jsonb IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS catalog_card_versions_lookup
    ON catalog.catalog_card_versions (entity, card_id, id);

INSERT INTO platform.schema_migrations (version) VALUES ('011_card_versions')
ON CONFLICT (version) DO NOTHING;
COMMIT;
