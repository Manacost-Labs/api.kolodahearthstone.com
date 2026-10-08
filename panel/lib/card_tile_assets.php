<?php
declare(strict_types=1);

const CARD_TILE_RECIPE = '1-app-png';
const CARD_TILE_FIELDS = [
    'name', 'name_ru', 'name_en', 'name_russian', 'pet_name_ru', 'variant_name',
    'pet_name', 'coin_name_ru', 'coin_name_en', 'card_type', 'tavern_tier', 'tier',
    'mana_cost', 'cost', 'rarity', 'creature_type', 'creature_types', 'races',
    'minion_type', 'race', '_tile_art_path', '_tile_art_version',
];

function card_tile_response_mtime(?int $modified = null): int
{
    static $mtime = 0;
    if ($modified !== null) $mtime = max($mtime, $modified);
    return $mtime;
}

function card_tile_signature(array $row, string $entityType, array $art): string
{
    $row['_tile_art_path'] = $art['local_image_url'] ?? '';
    $row['_tile_art_version'] = $art['generated_at'] ?? '';
    $values = [CARD_TILE_RECIPE, $entityType];
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
    if (!hash_equals(card_tile_signature($row, $entityType, $art), (string)$entry['signature'])) {
        return null;
    }
    $path = (string)$entry['path'];
    $filename = substr($path, strlen($prefix));
    if (strpos($path, $prefix) !== 0 || !preg_match('/^[A-Za-z0-9_.-]+-[a-f0-9]{16}\.png$/D', $filename)) {
        return null;
    }
    if (!is_file($root . $path)) return null;
    card_tile_response_mtime((int)@filemtime(is_file($recordPath) ? $recordPath : $manifestPath));
    return 'https://api.kolodahearthstone.com' . $path;
}
