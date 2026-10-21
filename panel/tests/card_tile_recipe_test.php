<?php
declare(strict_types=1);

require __DIR__ . '/../lib/card_tile_assets.php';

function check(bool $condition, string $message): void
{
    if (!$condition) { fwrite(STDERR, "FAIL: $message\n"); exit(1); }
}

check(horizontal_art_2x_path('/uploads/horizontal-art/r8/constructed_card/EX1_001.webp')
    === '/uploads/horizontal-art/r8/constructed_card/EX1_001@2x.webp', 'recipe 8 art has a 2x companion');
check(horizontal_art_2x_path('/uploads/horizontal-art/constructed_card/EX1_001.webp') === null,
    'older recipes have no 2x rendition');
check(horizontal_art_2x_path('/uploads/horizontal-art/r8/../x.webp') === null, 'paths stay inside the art tree');
check(horizontal_art_2x_path(null) === null, 'missing art has no 2x rendition');

$root = sys_get_temp_dir() . '/tile-recipe-' . bin2hex(random_bytes(4));
$prefix = '/uploads/card-tiles/hero/';
mkdir($root . $prefix . 'records', 0777, true);
touch($root . $prefix . 'records/.complete');
$row = ['name' => 'Hero'];
$art = ['local_image_url' => '/uploads/horizontal-art/hero/H1.webp', 'generated_at' => '1'];
$path = $prefix . 'H1-0123456789abcdef.png';
touch($root . $path);
// Records are cached per process, so each case uses its own card ID.
$cases = ['H1' => CARD_TILE_RECIPE, 'H2' => CARD_TILE_LEGACY_RECIPES[0], 'H3' => '0-unknown'];
foreach ($cases as $id => $recipe) {
    file_put_contents($root . $prefix . 'records/' . hash('sha256', $id) . '.json',
        json_encode(['signature' => card_tile_signature($row, 'hero', $art, $recipe), 'path' => $path]));
}
check(card_tile_asset_url($row, 'hero', 'H1', $art, $root) !== null, 'current tiles are published');
check(card_tile_asset_url($row, 'hero', 'H2', $art, $root) !== null, 'legacy tiles remain valid until replaced');
check(card_tile_asset_url($row, 'hero', 'H3', $art, $root) === null, 'unknown recipes are rejected');
echo "ok\n";
