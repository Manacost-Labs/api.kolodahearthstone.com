<?php
declare(strict_types=1);

// Battlegrounds minion/hero images and the arena class_slug, on saved responses; no network.
// The uploads directory is a private temp tree, the analytics cache is pre-seeded.
// Not sys_get_temp_dir() here: PHP caches its first answer, and the cache must follow TMPDIR below.
$root = '/tmp/analytics-bg-images-test-' . bin2hex(random_bytes(4));
$uploads = $root . '/uploads';
foreach (['framed', 'cards'] as $dir) {
    mkdir($uploads . '/' . $dir, 0700, true);
}
mkdir($root . '/tmp', 0700, true);
putenv('TMPDIR=' . $root . '/tmp');
putenv('HS_DATA_API_INTERNAL_URL=http://127.0.0.1:9');
putenv('ANALYTICS_UPLOADS_DIR=' . $uploads);

require dirname(__DIR__) . '/lib/analytics.php';

function img_same($expected, $actual, string $message): void
{
    if ($expected !== $actual) {
        throw new RuntimeException($message . "\nExpected: " . var_export($expected, true) . "\nActual: " . var_export($actual, true));
    }
}

function img_seed(string $path, array $query, array $payload): void
{
    $url = 'http://127.0.0.1:9' . $path . ($query ? '?' . http_build_query($query, '', '&', PHP_QUERY_RFC3986) : '');
    file_put_contents(analytics_cache_directory() . '/' . hash('sha256', $url) . '.json', json_encode($payload));
}

function img_seed_remote(string $url, array $payload): void
{
    file_put_contents(analytics_cache_directory() . '/remote-' . hash('sha256', $url) . '.json', json_encode($payload));
}

function img_by(array $rows, string $key, string $id): array
{
    foreach ($rows as $row) {
        if (($row[$key] ?? null) === $id) {
            return $row;
        }
    }
    throw new RuntimeException('row not found: ' . $id);
}

const IMG_ORIGIN = 'https://api.kolodahearthstone.com';

// Minions: the catalog source (framed, then the raw card); no file means an empty URL.
file_put_contents($uploads . '/framed/BG_A.png', 'png');
file_put_contents($uploads . '/framed/BG_AB.png', 'png');
file_put_contents($uploads . '/cards/BG_AB.png', 'png');
file_put_contents($uploads . '/cards/BG_B.png', 'png');
file_put_contents($uploads . '/framed/BG_EMPTY.png', '');
img_seed('/datasets/hsreplay_battlegrounds_minions', [], [
    'fetched_at' => '2026-11-19T09:00:00+00:00',
    'data' => ['structured' => ['minions' => [
        ['id' => 'BG_A', 'name' => 'A', 'tavern_tier' => 1],
        ['id' => 'BG_B', 'name' => 'B', 'tavern_tier' => 1],
        ['id' => 'BG_AB', 'name' => 'AB', 'tavern_tier' => 1],
        ['id' => 'BG_C', 'name' => 'C', 'tavern_tier' => 1],
        ['id' => 'BG_EMPTY', 'name' => 'Empty file', 'tavern_tier' => 1],
        ['id' => 'BG/../x', 'name' => 'Path', 'tavern_tier' => 1],
    ]]],
]);
$minions = analytics_module_response('bg_minions', []);
img_same(IMG_ORIGIN . '/uploads/framed/BG_A.png', img_by($minions['rows'], 'card_id', 'BG_A')['image_url'], 'framed art is preferred');
img_same(IMG_ORIGIN . '/uploads/cards/BG_B.png', img_by($minions['rows'], 'card_id', 'BG_B')['image_url'], 'the raw card is the fallback');
img_same(IMG_ORIGIN . '/uploads/framed/BG_AB.png', img_by($minions['rows'], 'card_id', 'BG_AB')['image_url'], 'framed beats the raw card');
img_same('', img_by($minions['rows'], 'card_id', 'BG_C')['image_url'], 'no file: empty URL, so the panel draws a placeholder');
img_same('', img_by($minions['rows'], 'card_id', 'BG_EMPTY')['image_url'], 'an empty file is not an image');
img_same('', img_by($minions['rows'], 'card_id', 'BG/../x')['image_url'], 'an id with a path never reaches the file system');
img_same('card', img_by($minions['rows'], 'card_id', 'BG_A')['image_kind'], 'image_kind stays');

// Heroes: a portrait for every hero, solo and duos.
$heroUrl = 'https://static.zerotoheroes.com/api/bgs/hero-stats/mmr-50/past-three/overview-from-hourly.gz.json';
img_seed_remote($heroUrl, ['lastUpdateDate' => '2026-11-19T09:00:00Z', 'heroStats' => [
    ['heroCardId' => 'TB_BaconShop_HERO_16', 'totalOffered' => 100, 'totalPicked' => 30, 'averagePosition' => 4.0, 'dataPoints' => 10],
]]);
img_seed('/v1/battlegrounds/heroes', ['mode' => 'solo', 'limit' => 500, 'offset' => 0], ['data' => [['id' => 'TB_BaconShop_HERO_16', 'hero' => 'A. F. Kay', 'tier' => 'A']]]);
$solo = analytics_module_response('bg_heroes', []);
img_same('https://art.hearthstonejson.com/v1/256x/TB_BaconShop_HERO_16.jpg', $solo['rows'][0]['image_url'], 'solo hero portrait');
img_same('hero_portrait', $solo['rows'][0]['image_kind'], 'solo hero image kind');

img_seed('/v1/battlegrounds/heroes', ['mode' => 'duos', 'q' => '', 'limit' => 200, 'offset' => 0], ['data' => [
    ['id' => 'TB_BaconShop_HERO_56', 'hero' => 'Alexstrasza', 'tier' => 'A'],
    ['hero' => 'No id'],
]]);
$duos = analytics_module_response('bg_heroes', ['mode' => 'duos']);
img_same('https://art.hearthstonejson.com/v1/256x/TB_BaconShop_HERO_56.jpg', $duos['rows'][0]['image_url'], 'duos hero portrait');
img_same('', $duos['rows'][1]['image_url'], 'duos hero without an id has no image');
img_same('image_url', $duos['columns'][0]['key'], 'duos table shows the portrait first');

// Arena: class_slug equals the HeartPulse public/class_icon/ui file names for all 11 classes.
$icons = ['deathknight', 'demonhunter', 'druid', 'hunter', 'mage', 'paladin', 'priest', 'rogue', 'shaman', 'warlock', 'warrior'];
$rows = [];
foreach (['Deathknight', 'Demonhunter', 'Druid', 'Hunter', 'Mage', 'Paladin', 'Priest', 'Rogue', 'Shaman', 'Warlock', 'Warrior'] as $class) {
    $rows[] = ['class' => $class, 'win_rate' => 50.0];
}
$rows[0] = ['class' => 'Deathknight', 'slug' => 'deathknight', 'class_name' => 'Death Knight'];
$rows[1] = ['class_name' => 'Demon Hunter'];
$rows[2] = ['class' => 'DRUID', 'slug' => ''];
img_seed('/v1/arena/classes', ['limit' => 20, 'offset' => 0], ['data' => $rows]);
$arena = analytics_module_response('arena', []);
$slugs = array_column($arena['rows'], 'class_slug');
sort($slugs);
img_same($icons, $slugs, 'every arena class has an icon slug');
img_same('deathknight', $arena['rows'][0]['class_slug'], 'Deathknight');
img_same('demonhunter', $arena['rows'][1]['class_slug'], 'Demon Hunter from class_name');

img_seed('/v1/arena/classes', ['limit' => 20, 'offset' => 0], ['data' => [['class' => 'Mystery']]]);
img_same('', analytics_module_response('arena', [])['rows'][0]['class_slug'], 'unknown class: empty slug');

// Arena cards: the card class is mapped the same way; neutral has no class icon.
img_seed('/datasets/firestone_arena_cards_normal', [], ['data' => ['structured' => ['cards' => [
    ['card_id' => 'X1', 'name' => 'One', 'cardClass' => 'DEMONHUNTER'],
    ['card_id' => 'X2', 'name' => 'Two', 'cardClass' => 'NEUTRAL'],
]]]]);
$cards = analytics_module_response('arena_cards', []);
img_same(['demonhunter', ''], array_column($cards['rows'], 'class_slug'), 'arena card class slug');

foreach (glob($root . '/{tmp/*/*,uploads/*/*}', GLOB_BRACE) ?: [] as $file) {
    @unlink($file);
}
foreach (glob($root . '/{tmp/*,uploads/*}', GLOB_BRACE | GLOB_ONLYDIR) ?: [] as $dir) {
    @rmdir($dir);
}
foreach ([$root . '/tmp', $root . '/uploads', $root] as $dir) {
    @rmdir($dir);
}

echo "analytics_bg_images_test.php: ok\n";
