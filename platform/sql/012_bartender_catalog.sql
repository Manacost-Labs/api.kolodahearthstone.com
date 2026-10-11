BEGIN;
CREATE TABLE IF NOT EXISTS catalog.battlegrounds_bartender_skins (
    card_id varchar(96) PRIMARY KEY,
    dbf bigint UNIQUE,
    parent_card_id varchar(96) NOT NULL,
    name_en varchar(255) NOT NULL,
    name_ru varchar(255),
    image_url varchar(512),
    status varchar(32) NOT NULL,
    source varchar(80) NOT NULL,
    source_payload_json text,
    source_hash char(64),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
INSERT INTO platform.schema_migrations (version) VALUES ('012_bartender_catalog')
ON CONFLICT (version) DO NOTHING;
COMMIT;
