<?php
declare(strict_types=1);

function panel_catalog_sort_options(): array
{
    return ['default'=>'По умолчанию', 'name_asc'=>'Название: А → Я',
        'name_desc'=>'Название: Я → А', 'updated_desc'=>'Сначала обновлённые'];
}

function panel_catalog_sort($value): string
{
    return is_string($value) && isset(panel_catalog_sort_options()[$value]) ? $value : 'default';
}

/** SQL identifiers and directions are fixed in code, never interpolated from GET. */
function panel_catalog_order(string $cardType, string $sort, bool $outer = false): string
{
    $prefix = $cardType === 'constructed' ? ($outer ? 'page.' : 'c.') : '';
    $key = $cardType === 'pet' ? 'variant_id' : (in_array($cardType, ['', 'minion', 'spell'], true) ? 'id' : 'card_id');
    $tie = $prefix . $key . ' ASC';
    $defaults = [
        'hero'=>'name_en ASC',
        'hero_skin'=>'class_name_en IS NULL, class_name_en ASC, name_en ASC',
        'pet'=>'pet_id ASC, level IS NULL, level ASC',
        'coin'=>'cosmetic_sort_order IS NULL, cosmetic_sort_order ASC, coin_name_en ASC',
        'timewarped'=>'tavern_tier IS NULL, tavern_tier ASC, card_type ASC, name_en ASC',
        'constructed'=>"{$prefix}name_ru IS NULL, {$prefix}name_ru ASC, {$prefix}name_en ASC",
    ];
    $libraries = ['anomaly','quest','darkmoon_prize','reward','trinket'];
    $default = $defaults[$cardType] ?? (in_array($cardType, $libraries, true)
        ? 'in_pool DESC, sort_order IS NULL, sort_order ASC, name_ru ASC'
        : 'in_pool DESC, tavern_tier IS NULL, tavern_tier ASC, name ASC');
    $sort = panel_catalog_sort($sort);
    if ($sort === 'default') return $default . ', ' . $tie;
    if ($sort === 'updated_desc') return "{$prefix}updated_at IS NULL, {$prefix}updated_at DESC, " . $tie;
    $names = ['hero_skin'=>['name_en'], 'pet'=>['variant_name'], 'coin'=>['coin_name_en']];
    $columns = $names[$cardType] ?? (in_array($cardType, ['', 'minion', 'spell'], true) ? ['name','name_en'] : ['name_ru','name_en']);
    $parts = array_map(static function (string $column) use ($prefix): string { return "NULLIF({$prefix}{$column}, '')"; }, $columns);
    $name = count($parts) === 1 ? $parts[0] : 'COALESCE(' . implode(', ', $parts) . ')';
    return $name . ' IS NULL, ' . $name . ($sort === 'name_desc' ? ' DESC, ' : ' ASC, ') . $tie;
}

/** GET page-jump forms carry only catalogue view state, never actions or secrets. */
function panel_catalog_page_fields(array $query): array
{
    $fields = [];
    foreach (['q','card_type','tier','creature_type','pool','duos','media','rarity','constructed_format','per_page','sort'] as $name) {
        if (isset($query[$name]) && is_scalar($query[$name]) && (string)$query[$name] !== '') $fields[$name] = (string)$query[$name];
    }
    return $fields;
}
