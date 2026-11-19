-- Card texts in 4 locales (ruRU, enUS, plPL, esES), one row per (card, locale).
-- Separate from hearthstone_card_catalog and from the history-triggered mode
-- tables. Allowed locales are an allow-list in sync_full_card_catalog.py, not a
-- CHECK/ENUM: adding esMX later needs no ALTER. Written by the same loader and
-- in the same transaction as hearthstone_card_catalog.
CREATE TABLE IF NOT EXISTS hearthstone_card_localizations (
    card_id VARCHAR(64) NOT NULL,
    locale VARCHAR(8) NOT NULL,
    dbf INT UNSIGNED NOT NULL,
    name VARCHAR(255) NULL,
    payload MEDIUMTEXT NOT NULL CHECK (JSON_VALID(payload)),
    source_hash CHAR(64) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (card_id, locale),
    UNIQUE KEY uq_card_localizations_dbf_locale (dbf, locale),
    KEY idx_card_localizations_locale_name (locale, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
