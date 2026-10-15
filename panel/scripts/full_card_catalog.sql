-- Separate from live Standard/Wild and Battlegrounds membership tables.
CREATE TABLE IF NOT EXISTS hearthstone_card_catalog (
    card_id VARCHAR(64) NOT NULL PRIMARY KEY,
    dbf INT UNSIGNED NOT NULL UNIQUE,
    collectible TINYINT(1) NOT NULL,
    name_ru VARCHAR(255) NULL,
    name_en VARCHAR(255) NULL,
    card_type VARCHAR(80) NULL,
    card_set VARCHAR(80) NULL,
    payload_ru MEDIUMTEXT NOT NULL CHECK (JSON_VALID(payload_ru)),
    payload_en MEDIUMTEXT NOT NULL CHECK (JSON_VALID(payload_en)),
    source_hash CHAR(64) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_catalog_type (card_type),
    KEY idx_catalog_set (card_set)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
