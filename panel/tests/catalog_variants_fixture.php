<?php
declare(strict_types=1);
// Synthetic records, real production view. No database, session, credentials or writes.
set_error_handler(static function (int $severity, string $message, string $file, int $line): bool {
    throw new ErrorException($message, 0, $severity, $file, $line);
});
$action = 'list';
require __DIR__ . '/shell_fixture.php';
require __DIR__ . '/catalog_fixture_state.php';
function csrf(): string { return 'fixture-card-csrf'; }
// Explicit fixture schema; unknown fields fail loudly when the actual view changes.
$record = array_fill_keys([
    'actor', 'animated_asset_json', 'animated_diamond_url', 'animated_image_url', 'armor',
    'armor_text', 'art_image', 'artist', 'as_hero', 'attack',
    'availability_json', 'ban_lists_json', 'buddy_json', 'card_changes_json', 'card_id',
    'card_image', 'card_image_url', 'card_name_en', 'card_name_ru', 'card_set',
    'card_type', 'categories_json', 'changed_at', 'character_name', 'class_name_en',
    'class_name_ru', 'class_slug', 'coin_name_en', 'cosmetic_sort_order', 'creature_type',
    'crop_image_url', 'dbf', 'diamond_animated_json', 'diamond_cards_json', 'duos_armor',
    'duos_only', 'end_screen_background_url', 'error', 'external_links_json', 'fetched_at',
    'flavor_ru', 'flavor_text', 'formats', 'framed_image', 'full_art_height',
    'full_art_url', 'full_art_width', 'full_tags_json', 'gallery_json', 'generated_by_cards_json',
    'golden_cards_json', 'golden_image', 'golden_image_url', 'golden_name_en', 'golden_name_ru',
    'golden_text_en', 'golden_text_ru', 'group_name_ru', 'health', 'hero_full_art_url',
    'hero_image_url', 'hero_power_json', 'hero_skins_json', 'horizontal_image_url', 'id',
    'image_diamond_url', 'image_gold_url', 'image_signature_url', 'image_url', 'in_pool',
    'level', 'local_crop_image_url', 'local_full_art_url', 'local_gold_image_url', 'local_image_url',
    'mana_cost', 'minion_type', 'name', 'name_en', 'name_ru',
    'notes', 'page_url', 'patch_changes_json', 'pet_id', 'pet_name',
    'primary_category_en', 'primary_category_ru', 'race', 'rarity', 'rarity_name_en',
    'rarity_name_ru', 'rarity_slug', 'related_card_ids_json', 'related_cards_json', 'release_date',
    'signature_cards_json', 'sounds_json', 'source', 'spell_school', 'static_image_url',
    'status', 'tags_json', 'tavern_tier', 'text_en', 'text_ru',
    'tier_name_ru', 'tier_value', 'updated_at', 'variant_id', 'variant_name',
    'wiki_image_url', 'wiki_mechanics_json', 'wiki_page_url', 'wiki_tags_json',
], '');
$image = isset($_GET['no_media']) ? '' : '/tests/catalog-art.svg';
$gallery = $image ? [['file_url'=>$image, 'thumb_url'=>$image, 'caption'=>'Тестовый арт']] : [];
$record = array_replace($record, [
    'id'=>42, 'dbf'=>10001, 'card_id'=>'BG_FIXTURE_1',
    'name'=>'Мурлок-разведчик', 'name_ru'=>'Мурлок-разведчик', 'name_en'=>'Scout Murloc',
    'card_name_ru'=>'Счастливая монетка', 'card_name_en'=>'Lucky Coin', 'coin_name_en'=>'Lucky Coin',
    'pet_name'=>'Лунный лис', 'variant_name'=>'Лунный лис · Первый уровень', 'pet_id'=>10, 'variant_id'=>1, 'level'=>1,
    'character_name'=>'Джайна', 'class_name_ru'=>'Маг', 'rarity_name_ru'=>'Легендарный',
    'primary_category_ru'=>'Герои Азерота', 'rarity_slug'=>'legendary',
    'release_date'=>'2026-08-01', 'updated_at'=>'2026-09-01', 'fetched_at'=>'2026-09-01',
    'attack'=>2, 'health'=>3, 'mana_cost'=>1, 'tavern_tier'=>1, 'tier_value'=>1, 'tier_name_ru'=>'Малый',
    'card_type'=>$showLibrary ? $libraryType : ($showConstructed ? 'MINION' : ($cardType === 'spell' ? 'spell' : 'minion')),
    'creature_type'=>'murloc', 'in_pool'=>1, 'duos_only'=>0, 'armor'=>15, 'duos_armor'=>12,
    'formats'=>'standard,wild', 'class_slug'=>'NEUTRAL', 'card_set'=>'CORE',
    'text_ru'=>'Боевой клич: получите +1 к атаке.', 'notes'=>'Боевой клич',
    'artist'=>'Тестовый художник', 'source'=>'fixture', 'status'=>'ok',
    'gallery_json'=>json_encode($gallery), 'wiki_page_url'=>'#fixture-wiki', 'page_url'=>'#fixture-wiki',
]);
foreach ([
    'card_image', 'art_image', 'framed_image', 'golden_image', 'card_image_url', 'crop_image_url',
    'golden_image_url', 'image_url', 'local_full_art_url', 'local_image_url', 'local_crop_image_url',
    'horizontal_image_url', 'hero_image_url', 'hero_full_art_url', 'static_image_url',
    'full_art_url', 'end_screen_background_url',
] as $key) $record[$key] = $image;
$related = ['name'=>'Ледяное пламя', 'text'=>'Наносит 1 ед. урона.', 'image'=>$image, 'gallery'=>$gallery];
$record['hero_power_json'] = json_encode($related);
$record['buddy_json'] = json_encode($related);
$record['hero_skins_json'] = json_encode([['cards'=>[['title'=>'Ледяная Джайна', 'image_url'=>$image, 'card_id'=>'SKIN_FIXTURE']]]]);
if ($showHeroSkins) $record['name_en'] = 'Ледяная Джайна';
$second = array_replace($record, ['id'=>43, 'dbf'=>10002, 'card_id'=>'BG_FIXTURE_2',
    'name'=>'Древний хранитель далёких северных земель', 'name_ru'=>'Древний хранитель далёких северных земель',
    'name_en'=>'Ancient Guardian of the Northern Lands', 'variant_name'=>'Лунный лис · Северное сияние',
    'coin_name_en'=>'Coin of the Northern Lights', 'variant_id'=>2, 'level'=>2]);
$rows = isset($_GET['empty']) ? [] : [$record, $second];
$cards = $constructedCards = $libraryCards = $timewarpedCards = $heroes = $heroSkins = $pets = $coins = $rows;
$wikiMetaMap = $constructedWikiMetaMap = [];
foreach ($rows as $row) {
    $wikiMetaMap[$row['card_id']] = $constructedWikiMetaMap[$row['card_id']] = $row;
}
$goldenVariantMap = $constructedRelatedCardMap = [];
$filteredTotal = count($rows); $pageFrom = $filteredTotal ? 1 : 0; $pageTo = $filteredTotal;
?>
<!doctype html>
<html lang="ru" data-theme="light">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Каталог · все варианты · UI fixture</title>
    <link rel="icon" href="data:,">
    <link rel="stylesheet" href="/assets/style.css">
    <link rel="stylesheet" href="/assets/workspace.css">
    <?php require __DIR__ . '/../partials/page-assets.php'; ?>
</head>
<body>
<main class="shell">
    <?php require __DIR__ . '/../partials/sidebar.php'; ?>
    <section class="workspace" id="main-content" tabindex="-1">
        <?php require __DIR__ . '/../partials/topbar.php'; ?>
        <?php require __DIR__ . '/../partials/catalog-heading.php'; ?>
        <section class="panel data-panel">
            <?php require __DIR__ . '/../partials/catalog-controls.php'; ?>
            <?php if (!$catalogIsGallery): ?>
                <?php $tableNavigationTarget = '.cards-table'; $tableNavigationLabel = 'Широкая таблица'; require __DIR__ . '/../partials/table-navigation.php'; ?>
            <?php endif; ?>
            <?php require __DIR__ . '/../partials/catalog-content.php'; ?>
        </section>
    </section>
</main>
<?php require __DIR__ . '/../partials/media-preview.php'; ?>
<?php require __DIR__ . '/../partials/command-palette.php'; ?>
</body>
</html>
