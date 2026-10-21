<?php
declare(strict_types=1);

const CARD_TILE_RECIPE = '2-hires-fit-title';
// Tiles of the previous recipe stay valid until the sync job replaces them.
const CARD_TILE_LEGACY_RECIPES = ['1-app-png'];
const CARD_TILE_FIELDS = [
    'name', 'name_ru', 'name_en', 'name_russian', 'pet_name_ru', 'variant_name',
    'pet_name', 'coin_name_ru', 'coin_name_en', 'card_type', 'tavern_tier', 'tier',
    'mana_cost', 'cost', 'rarity', 'creature_type', 'creature_types', 'races',
    'minion_type', 'race', '_tile_art_path', '_tile_art_version',
];

/** Recipe 8 stores a 640x128 rendition beside each 320x64 file. */
function horizontal_art_2x_path(?string $path): ?string
{
    $path = (string)$path;
    return preg_match('~^/uploads/horizontal-art/r\d+/[A-Za-z0-9_]+/[A-Za-z0-9_.-]+\.webp$~D', $path)
        ? substr($path, 0, -5) . '@2x.webp'
        : null;
}

function card_tile_response_mtime(?int $modified = null): int
{
    static $mtime = 0;
    if ($modified !== null) $mtime = max($mtime, $modified);
    return $mtime;
}

function card_tile_signature(array $row, string $entityType, array $art, string $recipe = CARD_TILE_RECIPE): string
{
    $row['_tile_art_path'] = $art['local_image_url'] ?? '';
    $row['_tile_art_version'] = $art['generated_at'] ?? '';
    $values = [$recipe, $entityType];
    foreach (CARD_TILE_FIELDS as $field) {
        $value = $row[$field] ?? '';
        $values[] = is_scalar($value) ? (string)$value : '';
    }
    return hash('sha256', implode("\0", $values));
}

function card_tile_asset_url(array $row, string $entityType, string $id, array $art, ?string $root = null): ?string
{
    if (!in_array($entityType, ['battleground_card', 'constructed_card', 'hero', 'hero_skin', 'pet', 'coin', 'timewarped_card', 'library_card'], true)) {
        return null;
    }
    $root = $root ?? dirname(__DIR__);
    $prefix = '/uploads/card-tiles/' . $entityType . '/';
    $manifestPath = $root . $prefix . 'manifest.json';
    $recordPath = $root . $prefix . 'records/' . hash('sha256', $id) . '.json';
    static $records = [];
    if (!array_key_exists($recordPath, $records)) {
        $contents = is_file($recordPath) ? file_get_contents($recordPath) : false;
        $records[$recordPath] = $contents !== false ? json_decode($contents, true) : null;
    }
    $entry = $records[$recordPath];
    // A complete per-card index avoids decoding the entire catalog per request.
    // Legacy installations retain the existing manifest fallback.
    static $manifests = [];
    if ($entry === null && !is_file($root . $prefix . 'records/.complete')) {
        if (!array_key_exists($manifestPath, $manifests)) {
            $contents = is_file($manifestPath) ? file_get_contents($manifestPath) : false;
            $decoded = $contents !== false ? json_decode($contents, true) : null;
            $manifests[$manifestPath] = is_array($decoded) ? $decoded : [];
        }
        $entry = $manifests[$manifestPath]['assets'][$id] ?? null;
    }
    if (!is_array($entry) || !is_string($entry['signature'] ?? null) || !is_string($entry['path'] ?? null)) {
        return null;
    }
    $current = false;
    foreach (array_merge([CARD_TILE_RECIPE], CARD_TILE_LEGACY_RECIPES) as $recipe) {
        $current = $current || hash_equals(card_tile_signature($row, $entityType, $art, $recipe), (string)$entry['signature']);
    }
    if (!$current) {
        return null;
    }
    $path = (string)$entry['path'];
    $filename = substr($path, strlen($prefix));
    if (strpos($path, $prefix) !== 0 || !preg_match('/^[A-Za-z0-9_.-]+-[a-f0-9]{16}\.png$/D', $filename)) {
        return null;
    }
    // Offloaded tiles are links into the S3 mount; lstat avoids a remote stat
    // per card on every API response.
    if (!is_link($root . $path) && !is_file($root . $path)) return null;
    card_tile_response_mtime((int)@filemtime(is_file($recordPath) ? $recordPath : $manifestPath));
    return 'https://api.kolodahearthstone.com' . $path;
}
