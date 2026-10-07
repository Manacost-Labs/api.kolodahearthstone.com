BEGIN;

-- Mirror of the MariaDB table written by the panel and scan_cards.php.
-- The shadow sync refuses unknown stage tables, so this must be applied
-- before the first panel edit creates the source table.
CREATE TABLE IF NOT EXISTS catalog.battlegrounds_card_overrides (
    card_id varchar(64) NOT NULL,
    field_name varchar(32) NOT NULL,
    manual_value text,
    upstream_value text,
    created_by varchar(64) NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (card_id, field_name)
);

INSERT INTO platform.schema_migrations (version)
VALUES ('010_battlegrounds_card_overrides')
ON CONFLICT (version) DO NOTHING;

COMMIT;
