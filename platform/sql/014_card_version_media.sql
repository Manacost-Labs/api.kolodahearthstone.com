CREATE TABLE IF NOT EXISTS catalog.catalog_card_version_media (
    version_id bigint NOT NULL REFERENCES catalog.catalog_card_versions(id),
    field_name varchar(64) NOT NULL,
    source_url text NOT NULL,
    status varchar(24) NOT NULL,
    reason varchar(64),
    archive_url varchar(255),
    sha256 char(64),
    byte_size bigint,
    mime_type varchar(64),
    evidence varchar(40),
    captured_at timestamp NOT NULL,
    PRIMARY KEY(version_id, field_name)
);
CREATE INDEX IF NOT EXISTS version_media_status_idx
    ON catalog.catalog_card_version_media(status, captured_at);
