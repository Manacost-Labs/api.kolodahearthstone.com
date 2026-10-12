CREATE TABLE IF NOT EXISTS catalog.catalog_card_version_media (
    version_id bigint NOT NULL REFERENCES catalog.catalog_card_versions(id),
    field_name varchar(64) NOT NULL,
    source_url text NOT NULL,
    status varchar(24) NOT NULL,
    reason varchar(64),
    archive_url varchar(255),
    sha256 char(64),
    byte_size numeric,
    mime_type varchar(64),
    evidence varchar(40),
    captured_at timestamptz(6) NOT NULL,
    PRIMARY KEY(version_id, field_name)
);
-- pgloader preserves unsigned BIGINT sizes as numeric and MySQL DATETIME as
-- timestamptz. Upgrade the initial migration without losing existing values.
ALTER TABLE catalog.catalog_card_version_media
    ALTER COLUMN byte_size TYPE numeric USING byte_size::numeric;
DO $media_timestamp$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'catalog' AND table_name = 'catalog_card_version_media'
          AND column_name = 'captured_at' AND data_type = 'timestamp without time zone'
    ) THEN
        ALTER TABLE catalog.catalog_card_version_media
            ALTER COLUMN captured_at TYPE timestamptz(6)
            USING captured_at AT TIME ZONE 'UTC';
    END IF;
END
$media_timestamp$;
CREATE INDEX IF NOT EXISTS version_media_status_idx
    ON catalog.catalog_card_version_media(status, captured_at);
