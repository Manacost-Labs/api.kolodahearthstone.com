BEGIN;

CREATE TABLE IF NOT EXISTS platform.media_assets (
    relative_path text PRIMARY KEY,
    asset_group text NOT NULL,
    media_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    modified_at timestamptz NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS media_assets_group_path_idx
    ON platform.media_assets (asset_group, relative_path);
CREATE INDEX IF NOT EXISTS media_assets_modified_at_idx
    ON platform.media_assets (modified_at DESC);

INSERT INTO platform.schema_migrations (version)
VALUES ('005_media_assets')
ON CONFLICT (version) DO NOTHING;

COMMIT;
