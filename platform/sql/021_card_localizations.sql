BEGIN;
-- MariaDB hearthstone_card_localizations (panel/sql/card_localizations.sql) is
-- copied into catalog_stage by the shadow loader; the merge refuses source
-- tables without a mirror, so this migration must reach production before the
-- MariaDB table is created. Column order and types follow the loader's
-- conversion: INT UNSIGNED -> bigint, MEDIUMTEXT -> text, TIMESTAMP -> timestamptz.
CREATE TABLE IF NOT EXISTS catalog.hearthstone_card_localizations (
    card_id varchar(64) NOT NULL,
    locale varchar(8) NOT NULL,
    dbf bigint NOT NULL,
    name varchar(255),
    payload text NOT NULL,
    source_hash char(64) NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (card_id, locale),
    UNIQUE (dbf, locale)
);
CREATE INDEX IF NOT EXISTS hearthstone_card_localizations_locale_name_idx
    ON catalog.hearthstone_card_localizations (locale, name);
INSERT INTO platform.schema_migrations (version) VALUES ('021_card_localizations')
ON CONFLICT (version) DO NOTHING;
COMMIT;
