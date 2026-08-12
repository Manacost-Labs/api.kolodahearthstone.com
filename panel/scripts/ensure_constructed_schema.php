<?php
declare(strict_types=1);

$config = require __DIR__ . '/../config.php';

$pdo = new PDO($config['db']['dsn'], $config['db']['user'], $config['db']['password'], [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES => false,
]);

function ensure_column(PDO $pdo, string $table, string $column, string $definition, ?string $after = null): void
{
    $stmt = $pdo->prepare("
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = :table_name
          AND COLUMN_NAME = :column_name
    ");
    $stmt->execute(['table_name' => $table, 'column_name' => $column]);
    if ((int)$stmt->fetchColumn() > 0) {
        return;
    }

    $sql = "ALTER TABLE `{$table}` ADD COLUMN `{$column}` {$definition}";
    if ($after !== null && $after !== '') {
        $sql .= " AFTER `{$after}`";
    }
    $pdo->exec($sql);
}

$pdo->exec("
    CREATE TABLE IF NOT EXISTS constructed_formats (
        format_slug VARCHAR(32) NOT NULL,
        name_ru VARCHAR(80) NOT NULL,
        name_en VARCHAR(80) NOT NULL,
        description_ru TEXT DEFAULT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (format_slug)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
");

$pdo->exec("
    CREATE TABLE IF NOT EXISTS constructed_cards (
        card_id VARCHAR(64) NOT NULL,
        dbf INT UNSIGNED DEFAULT NULL,
        slug VARCHAR(160) DEFAULT NULL,
        collectible TINYINT(1) NOT NULL DEFAULT 0,
        name_ru VARCHAR(255) DEFAULT NULL,
        name_en VARCHAR(255) DEFAULT NULL,
        text_ru TEXT DEFAULT NULL,
        text_en TEXT DEFAULT NULL,
        flavor_ru TEXT DEFAULT NULL,
        flavor_en TEXT DEFAULT NULL,
        card_set VARCHAR(80) DEFAULT NULL,
        card_set_id INT UNSIGNED DEFAULT NULL,
        card_type VARCHAR(80) DEFAULT NULL,
        card_type_id SMALLINT UNSIGNED DEFAULT NULL,
        rarity VARCHAR(80) DEFAULT NULL,
        rarity_id SMALLINT UNSIGNED DEFAULT NULL,
        class_slug VARCHAR(80) DEFAULT NULL,
        class_id SMALLINT UNSIGNED DEFAULT NULL,
        multi_class_json JSON DEFAULT NULL,
        minion_type VARCHAR(80) DEFAULT NULL,
        minion_type_id SMALLINT UNSIGNED DEFAULT NULL,
        spell_school VARCHAR(80) DEFAULT NULL,
        spell_school_id SMALLINT UNSIGNED DEFAULT NULL,
        mana_cost SMALLINT DEFAULT NULL,
        attack SMALLINT DEFAULT NULL,
        health SMALLINT DEFAULT NULL,
        durability SMALLINT DEFAULT NULL,
        armor SMALLINT DEFAULT NULL,
        artist VARCHAR(255) DEFAULT NULL,
        image_url VARCHAR(512) DEFAULT NULL,
        image_gold_url VARCHAR(512) DEFAULT NULL,
        image_signature_url VARCHAR(512) DEFAULT NULL,
        image_diamond_url VARCHAR(512) DEFAULT NULL,
        animated_gold_url VARCHAR(512) DEFAULT NULL,
        animated_signature_url VARCHAR(512) DEFAULT NULL,
        animated_diamond_url VARCHAR(512) DEFAULT NULL,
        crop_image_url VARCHAR(512) DEFAULT NULL,
        local_image_url VARCHAR(512) DEFAULT NULL,
        local_gold_image_url VARCHAR(512) DEFAULT NULL,
        local_crop_image_url VARCHAR(512) DEFAULT NULL,
        wiki_full_art_title VARCHAR(255) DEFAULT NULL,
        wiki_full_art_url VARCHAR(512) DEFAULT NULL,
        local_wiki_full_art_url VARCHAR(512) DEFAULT NULL,
        wiki_full_art_file_page_url VARCHAR(512) DEFAULT NULL,
        wiki_full_art_width INT UNSIGNED DEFAULT NULL,
        wiki_full_art_height INT UNSIGNED DEFAULT NULL,
        wiki_full_art_size INT UNSIGNED DEFAULT NULL,
        wiki_full_art_sha1 CHAR(40) DEFAULT NULL,
        wiki_full_art_mime VARCHAR(80) DEFAULT NULL,
        wiki_full_art_fetched_at TIMESTAMP NULL DEFAULT NULL,
        mechanics_json JSON DEFAULT NULL,
        referenced_tags_json JSON DEFAULT NULL,
        keyword_ids_json JSON DEFAULT NULL,
        wiki_page_title VARCHAR(255) DEFAULT NULL,
        wiki_page_url VARCHAR(512) DEFAULT NULL,
        source VARCHAR(64) NOT NULL DEFAULT 'pending',
        source_payload_json JSON DEFAULT NULL,
        source_hash CHAR(64) DEFAULT NULL,
        first_seen_at TIMESTAMP NULL DEFAULT NULL,
        last_seen_at TIMESTAMP NULL DEFAULT NULL,
        changed_at TIMESTAMP NULL DEFAULT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (card_id),
        UNIQUE KEY uniq_constructed_dbf (dbf),
        KEY idx_constructed_collectible (collectible),
        KEY idx_constructed_set (card_set),
        KEY idx_constructed_type (card_type),
        KEY idx_constructed_class (class_slug),
        KEY idx_constructed_rarity (rarity),
        KEY idx_constructed_changed_at (changed_at),
        KEY idx_constructed_updated_at (updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
");

$pdo->exec("
    CREATE TABLE IF NOT EXISTS constructed_format_cards (
        format_slug VARCHAR(32) NOT NULL,
        card_id VARCHAR(64) NOT NULL,
        dbf INT UNSIGNED DEFAULT NULL,
        in_format TINYINT(1) NOT NULL DEFAULT 1,
        availability_status VARCHAR(32) NOT NULL DEFAULT 'available',
        rotation_year SMALLINT UNSIGNED DEFAULT NULL,
        source VARCHAR(64) NOT NULL DEFAULT 'pending',
        source_payload_json JSON DEFAULT NULL,
        source_hash CHAR(64) DEFAULT NULL,
        first_seen_at TIMESTAMP NULL DEFAULT NULL,
        last_seen_at TIMESTAMP NULL DEFAULT NULL,
        changed_at TIMESTAMP NULL DEFAULT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (format_slug, card_id),
        KEY idx_constructed_format_dbf (format_slug, dbf),
        KEY idx_constructed_format_available (format_slug, in_format, availability_status),
        KEY idx_constructed_format_changed_at (changed_at),
        CONSTRAINT fk_constructed_format_card
            FOREIGN KEY (card_id) REFERENCES constructed_cards (card_id)
            ON UPDATE CASCADE
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
");

$pdo->exec("
    CREATE TABLE IF NOT EXISTS constructed_import_runs (
        id INT UNSIGNED NOT NULL AUTO_INCREMENT,
        job VARCHAR(64) NOT NULL,
        format_slug VARCHAR(32) DEFAULT NULL,
        source VARCHAR(64) NOT NULL,
        started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP NULL DEFAULT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'running',
        scanned INT UNSIGNED NOT NULL DEFAULT 0,
        inserted INT UNSIGNED NOT NULL DEFAULT 0,
        updated INT UNSIGNED NOT NULL DEFAULT 0,
        changed INT UNSIGNED NOT NULL DEFAULT 0,
        error TEXT DEFAULT NULL,
        PRIMARY KEY (id),
        KEY idx_constructed_runs_job_started (job, started_at),
        KEY idx_constructed_runs_format_started (format_slug, started_at),
        KEY idx_constructed_runs_status (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
");

$pdo->exec("
    CREATE TABLE IF NOT EXISTS constructed_card_wiki_meta (
        card_id VARCHAR(64) NOT NULL,
        dbf INT UNSIGNED DEFAULT NULL,
        wiki_page_title VARCHAR(255) DEFAULT NULL,
        wiki_page_url VARCHAR(512) DEFAULT NULL,
        wiki_mechanics_json JSON DEFAULT NULL,
        wiki_tags_json JSON DEFAULT NULL,
        ban_lists_json JSON DEFAULT NULL,
        gallery_json JSON DEFAULT NULL,
        patch_changes_json JSON DEFAULT NULL,
        external_links_json JSON DEFAULT NULL,
        related_cards_json JSON DEFAULT NULL,
        related_card_ids_json JSON DEFAULT NULL,
        generated_card_pools_json JSON DEFAULT NULL,
        generated_card_ids_json JSON DEFAULT NULL,
        sounds_json JSON DEFAULT NULL,
        golden_cards_json JSON DEFAULT NULL,
        signature_cards_json JSON DEFAULT NULL,
        diamond_cards_json JSON DEFAULT NULL,
        golden_animated_json JSON DEFAULT NULL,
        signature_animated_json JSON DEFAULT NULL,
        diamond_animated_json JSON DEFAULT NULL,
        source_payload_json JSON DEFAULT NULL,
        source_hash CHAR(64) DEFAULT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'ok',
        error TEXT DEFAULT NULL,
        fetched_at TIMESTAMP NULL DEFAULT NULL,
        changed_at TIMESTAMP NULL DEFAULT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (card_id),
        KEY idx_constructed_wiki_dbf (dbf),
        KEY idx_constructed_wiki_status (status),
        KEY idx_constructed_wiki_changed_at (changed_at),
        CONSTRAINT fk_constructed_wiki_card
            FOREIGN KEY (card_id) REFERENCES constructed_cards (card_id)
            ON UPDATE CASCADE
            ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
");

ensure_column($pdo, 'constructed_cards', 'image_signature_url', 'VARCHAR(512) DEFAULT NULL', 'image_gold_url');
ensure_column($pdo, 'constructed_cards', 'image_diamond_url', 'VARCHAR(512) DEFAULT NULL', 'image_signature_url');
ensure_column($pdo, 'constructed_cards', 'animated_gold_url', 'VARCHAR(512) DEFAULT NULL', 'image_diamond_url');
ensure_column($pdo, 'constructed_cards', 'animated_signature_url', 'VARCHAR(512) DEFAULT NULL', 'animated_gold_url');
ensure_column($pdo, 'constructed_cards', 'animated_diamond_url', 'VARCHAR(512) DEFAULT NULL', 'animated_signature_url');
ensure_column($pdo, 'constructed_cards', 'wiki_full_art_title', 'VARCHAR(255) DEFAULT NULL', 'local_crop_image_url');
ensure_column($pdo, 'constructed_cards', 'wiki_full_art_url', 'VARCHAR(512) DEFAULT NULL', 'wiki_full_art_title');
ensure_column($pdo, 'constructed_cards', 'local_wiki_full_art_url', 'VARCHAR(512) DEFAULT NULL', 'wiki_full_art_url');
ensure_column($pdo, 'constructed_cards', 'wiki_full_art_file_page_url', 'VARCHAR(512) DEFAULT NULL', 'local_wiki_full_art_url');
ensure_column($pdo, 'constructed_cards', 'wiki_full_art_width', 'INT UNSIGNED DEFAULT NULL', 'wiki_full_art_file_page_url');
ensure_column($pdo, 'constructed_cards', 'wiki_full_art_height', 'INT UNSIGNED DEFAULT NULL', 'wiki_full_art_width');
ensure_column($pdo, 'constructed_cards', 'wiki_full_art_size', 'INT UNSIGNED DEFAULT NULL', 'wiki_full_art_height');
ensure_column($pdo, 'constructed_cards', 'wiki_full_art_sha1', 'CHAR(40) DEFAULT NULL', 'wiki_full_art_size');
ensure_column($pdo, 'constructed_cards', 'wiki_full_art_mime', 'VARCHAR(80) DEFAULT NULL', 'wiki_full_art_sha1');
ensure_column($pdo, 'constructed_cards', 'wiki_full_art_fetched_at', 'TIMESTAMP NULL DEFAULT NULL', 'wiki_full_art_mime');
ensure_column($pdo, 'constructed_card_wiki_meta', 'diamond_cards_json', 'JSON DEFAULT NULL', 'signature_cards_json');
ensure_column($pdo, 'constructed_card_wiki_meta', 'generated_card_pools_json', 'JSON DEFAULT NULL', 'related_card_ids_json');
ensure_column($pdo, 'constructed_card_wiki_meta', 'generated_card_ids_json', 'JSON DEFAULT NULL', 'generated_card_pools_json');
ensure_column($pdo, 'constructed_card_wiki_meta', 'golden_animated_json', 'JSON DEFAULT NULL', 'diamond_cards_json');
ensure_column($pdo, 'constructed_card_wiki_meta', 'signature_animated_json', 'JSON DEFAULT NULL', 'golden_animated_json');
ensure_column($pdo, 'constructed_card_wiki_meta', 'diamond_animated_json', 'JSON DEFAULT NULL', 'signature_animated_json');

$pdo->exec("
    CREATE TABLE IF NOT EXISTS constructed_diamond_cards (
        base_card_id VARCHAR(64) NOT NULL,
        base_dbf INT UNSIGNED DEFAULT NULL,
        diamond_card_id VARCHAR(80) NOT NULL,
        diamond_dbf INT UNSIGNED DEFAULT NULL,
        name_en VARCHAR(255) DEFAULT NULL,
        name_ru VARCHAR(255) DEFAULT NULL,
        card_set_id INT UNSIGNED DEFAULT NULL,
        card_set VARCHAR(80) DEFAULT NULL,
        card_type VARCHAR(80) DEFAULT NULL,
        rarity VARCHAR(80) DEFAULT NULL,
        class_slug VARCHAR(80) DEFAULT NULL,
        mana_cost SMALLINT DEFAULT NULL,
        collectible TINYINT(1) NOT NULL DEFAULT 0,
        section_slug VARCHAR(32) NOT NULL,
        section_name_ru VARCHAR(80) NOT NULL,
        in_standard TINYINT(1) NOT NULL DEFAULT 0,
        in_wild TINYINT(1) NOT NULL DEFAULT 0,
        image_url VARCHAR(512) DEFAULT NULL,
        animated_url VARCHAR(512) DEFAULT NULL,
        animated_source VARCHAR(64) DEFAULT NULL,
        hearthpwn_url VARCHAR(512) DEFAULT NULL,
        wiki_page_title VARCHAR(255) DEFAULT NULL,
        wiki_page_url VARCHAR(512) DEFAULT NULL,
        source VARCHAR(64) NOT NULL,
        source_payload_json JSON DEFAULT NULL,
        source_hash CHAR(64) DEFAULT NULL,
        fetched_at TIMESTAMP NULL DEFAULT NULL,
        changed_at TIMESTAMP NULL DEFAULT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (base_card_id),
        KEY idx_diamond_base_dbf (base_dbf),
        KEY idx_diamond_section (section_slug),
        KEY idx_diamond_standard (in_standard),
        KEY idx_diamond_wild (in_wild),
        KEY idx_diamond_changed_at (changed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
");

$pdo->exec("
    CREATE OR REPLACE VIEW constructed_standard_cards AS
    SELECT c.*, f.in_format, f.availability_status, f.rotation_year, f.changed_at AS format_changed_at
    FROM constructed_cards c
    INNER JOIN constructed_format_cards f ON f.card_id = c.card_id
    WHERE f.format_slug = 'standard'
");

$pdo->exec("
    CREATE OR REPLACE VIEW constructed_wild_cards AS
    SELECT c.*, f.in_format, f.availability_status, f.rotation_year, f.changed_at AS format_changed_at
    FROM constructed_cards c
    INNER JOIN constructed_format_cards f ON f.card_id = c.card_id
    WHERE f.format_slug = 'wild'
");

foreach (['standard', 'wild'] as $format) {
    $stmt = $pdo->prepare("
        INSERT IGNORE INTO constructed_import_runs (job, format_slug, source, status, scanned, inserted, updated, changed, finished_at)
        VALUES ('schema', :format_slug, 'local', 'ok', 0, 0, 0, 0, CURRENT_TIMESTAMP)
    ");
    $stmt->execute(['format_slug' => $format]);
}

$formatStmt = $pdo->prepare("
    INSERT INTO constructed_formats (format_slug, name_ru, name_en, description_ru)
    VALUES (:format_slug, :name_ru, :name_en, :description_ru)
    ON DUPLICATE KEY UPDATE
        name_ru = VALUES(name_ru),
        name_en = VALUES(name_en),
        description_ru = VALUES(description_ru)
");
$formatStmt->execute([
    'format_slug' => 'standard',
    'name_ru' => 'Стандартный',
    'name_en' => 'Standard',
    'description_ru' => 'Карты Hearthstone, доступные в стандартном формате.',
]);
$formatStmt->execute([
    'format_slug' => 'wild',
    'name_ru' => 'Вольный',
    'name_en' => 'Wild',
    'description_ru' => 'Карты Hearthstone, доступные в вольном формате.',
]);

echo json_encode([
    'ok' => true,
    'tables' => [
        'constructed_formats',
        'constructed_cards',
        'constructed_format_cards',
        'constructed_card_wiki_meta',
        'constructed_diamond_cards',
        'constructed_import_runs',
    ],
    'views' => [
        'constructed_standard_cards',
        'constructed_wild_cards',
    ],
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . PHP_EOL;
