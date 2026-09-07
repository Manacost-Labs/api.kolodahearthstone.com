<?php
declare(strict_types=1);
require_once __DIR__ . '/catalog_view.php';

/** Fresh navigation totals, plus coverage only for the catalogue being displayed.
 * Queries retain their existing predicates; no persistent cache or schema changes.
 * The fixed result keys are also used as template variables by index.php.
 */
function panel_catalog_counts(PDO $pdo, string $action, string $cardType): array
{
    $queries = [
        'total' => "SELECT COUNT(*) FROM battlegrounds_cards WHERE variant_kind = 'base'",
        'heroTotal' => "SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok'",
        'heroSkinsTotal' => "SELECT COUNT(*) FROM hero_skins WHERE status IN ('ok', 'partial')",
        'petsTotal' => "SELECT COUNT(*) FROM hearthstone_pets WHERE status IN ('ok', 'partial')",
        'coinsTotal' => 'SELECT COUNT(*) FROM hearthstone_coins',
        'timewarpedTotal' => "SELECT COUNT(*) FROM battlegrounds_timewarped_cards WHERE status = 'ok'",
        'constructedTotal' => 'SELECT COUNT(*) FROM constructed_cards',
    ];
    if ($action === 'list') {
        $coverage = [
            'hero' => [
                'heroBuddyTotal' => "SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok' AND buddy_dbf IS NOT NULL",
                'heroPowerGalleryTotal' => "SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok' AND COALESCE(JSON_LENGTH(JSON_EXTRACT(hero_power_json, '$.gallery')), 0) > 0",
                'buddyGalleryTotal' => "SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok' AND buddy_dbf IS NOT NULL AND COALESCE(JSON_LENGTH(JSON_EXTRACT(buddy_json, '$.gallery')), 0) > 0",
                'buddySoundsTotal' => "SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok' AND buddy_dbf IS NOT NULL AND COALESCE(JSON_LENGTH(JSON_EXTRACT(buddy_json, '$.sounds')), 0) > 0",
                'heroWikiErrorTotal' => "SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok' AND (JSON_EXTRACT(hero_power_json, '$.wiki_fetch_error') IS NOT NULL OR JSON_EXTRACT(buddy_json, '$.wiki_fetch_error') IS NOT NULL)",
            ],
            'hero_skin' => [
                'heroSkinsAnimatedTotal' => "SELECT COUNT(*) FROM hero_skins WHERE status IN ('ok', 'partial') AND animated_image_url IS NOT NULL AND animated_image_url <> ''",
                'heroSkinsGalleryTotal' => "SELECT COUNT(*) FROM hero_skins WHERE status IN ('ok', 'partial') AND COALESCE(JSON_LENGTH(gallery_json), 0) > 0",
                'heroSkinsSoundsTotal' => "SELECT COUNT(*) FROM hero_skins WHERE status IN ('ok', 'partial') AND COALESCE(JSON_LENGTH(sounds_json), 0) > 0",
            ],
            'pet' => [
                'petsGalleryTotal' => "SELECT COUNT(*) FROM hearthstone_pets WHERE status IN ('ok', 'partial') AND COALESCE(JSON_LENGTH(gallery_json), 0) > 0",
                'petsBackgroundTotal' => "SELECT COUNT(*) FROM hearthstone_pets WHERE status IN ('ok', 'partial') AND end_screen_background_url IS NOT NULL AND end_screen_background_url <> ''",
                'petFamiliesTotal' => "SELECT COUNT(DISTINCT pet_id) FROM hearthstone_pets WHERE status IN ('ok', 'partial')",
            ],
            'constructed' => [
                'constructedStandardTotal' => "SELECT COUNT(*) FROM constructed_format_cards WHERE format_slug = 'standard' AND in_format = 1",
                'constructedWildTotal' => "SELECT COUNT(*) FROM constructed_format_cards WHERE format_slug = 'wild' AND in_format = 1",
                'constructedWikiTotal' => "SELECT COUNT(*) FROM constructed_card_wiki_meta WHERE status = 'ok'",
                'constructedDiamondTotal' => "SELECT COUNT(*) FROM constructed_cards WHERE image_diamond_url IS NOT NULL AND image_diamond_url <> ''",
                'constructedAnimatedDiamondTotal' => "SELECT COUNT(*) FROM constructed_cards WHERE animated_diamond_url IS NOT NULL AND animated_diamond_url <> ''",
            ],
        ];
        $queries += $coverage[$cardType] ?? [];
    }
    $counts = [];
    foreach ($queries as $name => $sql) $counts[$name] = (int)$pdo->query($sql)->fetchColumn();
    if ($action === 'list' && $cardType === 'hero_skin') {
        $counts['heroSkinRarityTotals'] = [];
        $rows = $pdo->query("SELECT COALESCE(rarity_slug, 'unknown') AS rarity_slug, COUNT(*) AS total FROM hero_skins WHERE status IN ('ok', 'partial') GROUP BY COALESCE(rarity_slug, 'unknown')")->fetchAll();
        foreach ($rows as $row) $counts['heroSkinRarityTotals'][(string)$row['rarity_slug']] = (int)$row['total'];
    }
    if ($action === 'list' && $cardType === 'coin') {
        $row = $pdo->query('SELECT generated_by_card_ids_json, related_card_ids_json FROM hearthstone_coins ORDER BY cosmetic_sort_order ASC LIMIT 1')->fetch();
        $counts['coinGeneratedByTotal'] = $row ? count(json_array($row['generated_by_card_ids_json'] ?? null)) : 0;
        $counts['coinRelatedTotal'] = $row ? count(json_array($row['related_card_ids_json'] ?? null)) : 0;
    }
    return $counts;
}
