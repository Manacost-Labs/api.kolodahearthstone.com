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
        'total' => "SELECT COUNT(*) AS total FROM battlegrounds_cards WHERE variant_kind = 'base'",
        'heroTotal' => "SELECT COUNT(*) AS heroTotal FROM battlegrounds_heroes WHERE status = 'ok'",
        'heroSkinsTotal' => "SELECT COUNT(*) AS heroSkinsTotal FROM hero_skins WHERE status IN ('ok', 'partial')",
        'petsTotal' => "SELECT COUNT(*) AS petsTotal FROM hearthstone_pets WHERE status IN ('ok', 'partial')",
        'coinsTotal' => 'SELECT COUNT(*) AS coinsTotal FROM hearthstone_coins',
        'timewarpedTotal' => "SELECT COUNT(*) AS timewarpedTotal FROM battlegrounds_timewarped_cards WHERE status = 'ok'",
        'constructedTotal' => 'SELECT COUNT(*) AS constructedTotal FROM constructed_cards',
    ];
    if ($action === 'list') {
        // One pass over the active table, including its navigation total. COUNT
        // (unlike SUM) returns zero for empty input; NULL predicates stay excluded.
        if ($cardType === 'hero') {
            $queries['heroTotal'] = "SELECT COUNT(*) AS heroTotal,
                COUNT(CASE WHEN buddy_dbf IS NOT NULL THEN 1 END) AS heroBuddyTotal,
                COUNT(CASE WHEN COALESCE(JSON_LENGTH(JSON_EXTRACT(hero_power_json, '$.gallery')), 0) > 0 THEN 1 END) AS heroPowerGalleryTotal,
                COUNT(CASE WHEN buddy_dbf IS NOT NULL AND COALESCE(JSON_LENGTH(JSON_EXTRACT(buddy_json, '$.gallery')), 0) > 0 THEN 1 END) AS buddyGalleryTotal,
                COUNT(CASE WHEN buddy_dbf IS NOT NULL AND COALESCE(JSON_LENGTH(JSON_EXTRACT(buddy_json, '$.sounds')), 0) > 0 THEN 1 END) AS buddySoundsTotal,
                COUNT(CASE WHEN JSON_EXTRACT(hero_power_json, '$.wiki_fetch_error') IS NOT NULL OR JSON_EXTRACT(buddy_json, '$.wiki_fetch_error') IS NOT NULL THEN 1 END) AS heroWikiErrorTotal
                FROM battlegrounds_heroes WHERE status = 'ok'";
        } elseif ($cardType === 'hero_skin') {
            $queries['heroSkinsTotal'] = "SELECT COUNT(*) AS heroSkinsTotal,
                COUNT(CASE WHEN animated_image_url IS NOT NULL AND animated_image_url <> '' THEN 1 END) AS heroSkinsAnimatedTotal,
                COUNT(CASE WHEN COALESCE(JSON_LENGTH(gallery_json), 0) > 0 THEN 1 END) AS heroSkinsGalleryTotal,
                COUNT(CASE WHEN COALESCE(JSON_LENGTH(sounds_json), 0) > 0 THEN 1 END) AS heroSkinsSoundsTotal
                FROM hero_skins WHERE status IN ('ok', 'partial')";
        } elseif ($cardType === 'pet') {
            $queries['petsTotal'] = "SELECT COUNT(*) AS petsTotal,
                COUNT(CASE WHEN COALESCE(JSON_LENGTH(gallery_json), 0) > 0 THEN 1 END) AS petsGalleryTotal,
                COUNT(CASE WHEN end_screen_background_url IS NOT NULL AND end_screen_background_url <> '' THEN 1 END) AS petsBackgroundTotal,
                COUNT(DISTINCT pet_id) AS petFamiliesTotal
                FROM hearthstone_pets WHERE status IN ('ok', 'partial')";
        } elseif ($cardType === 'constructed') {
            $queries['constructedTotal'] = "SELECT COUNT(*) AS constructedTotal,
                COUNT(CASE WHEN image_diamond_url IS NOT NULL AND image_diamond_url <> '' THEN 1 END) AS constructedDiamondTotal,
                COUNT(CASE WHEN animated_diamond_url IS NOT NULL AND animated_diamond_url <> '' THEN 1 END) AS constructedAnimatedDiamondTotal
                FROM constructed_cards";
            $queries['formats'] = "SELECT
                COUNT(CASE WHEN format_slug = 'standard' THEN 1 END) AS constructedStandardTotal,
                COUNT(CASE WHEN format_slug = 'wild' THEN 1 END) AS constructedWildTotal
                FROM constructed_format_cards WHERE in_format = 1 AND format_slug IN ('standard', 'wild')";
            $queries['wiki'] = "SELECT COUNT(*) AS constructedWikiTotal FROM constructed_card_wiki_meta WHERE status = 'ok'";
        }
    }
    $counts = [];
    foreach ($queries as $sql) {
        foreach ($pdo->query($sql)->fetch(PDO::FETCH_ASSOC) as $name => $value) $counts[$name] = (int)$value;
    }
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
