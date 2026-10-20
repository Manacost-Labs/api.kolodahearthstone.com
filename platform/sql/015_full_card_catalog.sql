BEGIN;
-- MariaDB hearthstone_card_catalog (panel/scripts/full_card_catalog.sql) is
-- copied into catalog_stage by the shadow loader; the merge refuses source
-- tables without a mirror. Column types match the loader's conversion.
CREATE TABLE IF NOT EXISTS catalog.hearthstone_card_catalog (
    card_id varchar(64) PRIMARY KEY,
    dbf bigint NOT NULL UNIQUE,
    collectible boolean NOT NULL,
    name_ru varchar(255),
    name_en varchar(255),
    card_type varchar(80),
    card_set varchar(80),
    payload_ru text NOT NULL,
    payload_en text NOT NULL,
    source_hash char(64) NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS hearthstone_card_catalog_type_idx
    ON catalog.hearthstone_card_catalog (card_type);
CREATE INDEX IF NOT EXISTS hearthstone_card_catalog_set_idx
    ON catalog.hearthstone_card_catalog (card_set);
INSERT INTO platform.schema_migrations (version) VALUES ('015_full_card_catalog')
ON CONFLICT (version) DO NOTHING;
COMMIT;
