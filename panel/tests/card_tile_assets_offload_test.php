<?php
declare(strict_types=1);

require __DIR__ . '/../lib/card_tile_assets.php';

function check(bool $condition, string $message): void
{
    if (!$condition) { fwrite(STDERR, "FAIL: $message\n"); exit(1); }
}

$root = sys_get_temp_dir() . '/tile-test-' . bin2hex(random_bytes(4));
$prefix = '/uploads/card-tiles/coin/';
mkdir($root . $prefix . 'records', 0777, true);
touch($root . $prefix . 'records/.complete');
$row = ['name' => 'Coin'];
$art = ['local_image_url' => '/uploads/horizontal-art/coin/C1.webp', 'generated_at' => '1'];
$path = $prefix . 'C1-0123456789abcdef.png';
file_put_contents(
    $root . $prefix . 'records/' . hash('sha256', 'C1') . '.json',
    json_encode(['signature' => card_tile_signature($row, 'coin', $art), 'path' => $path])
);

// Offloaded tiles are links into the remote mount. Their target is not
// visible here, but the link alone proves the verified upload exists.
symlink($root . '/missing-remote/C1.png', $root . $path);
check(card_tile_asset_url($row, 'coin', 'C1', $art, $root) === 'https://api.kolodahearthstone.com' . $path,
    'offloaded tile link is published without stat-ing its remote target');

unlink($root . $path);
check(card_tile_asset_url($row, 'coin', 'C1', $art, $root) === null, 'a tile with neither file nor link is not published');
echo "ok\n";
