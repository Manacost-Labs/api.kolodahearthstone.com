<?php
declare(strict_types=1);

function battleground_creature_type_labels(): array
{
    return [
        'all' => 'Общие', 'aberration' => 'Аберрация', 'undead' => 'Нежить',
        'dragon' => 'Дракон', 'mech' => 'Механизм', 'murloc' => 'Мурлок',
        'demon' => 'Демон', 'quilboar' => 'Свинобраз', 'naga' => 'Нага',
        'pirate' => 'Пират', 'beast' => 'Зверь', 'elemental' => 'Элементаль',
    ];
}

function battleground_creature_type(array $card): ?string
{
    $races = array_merge(isset($card['race']) ? [$card['race']] : [],
        is_array($card['races'] ?? null) ? $card['races'] : []);
    $labels = battleground_creature_type_labels();
    foreach ($races as $race) {
        if (!is_string($race)) continue;
        $slug = strtolower($race);
        if ($slug === 'mechanical') $slug = 'mech';
        if (isset($labels[$slug])) return $slug;
    }
    return null;
}

/** Only repair unclassified minions when both available source locales agree. */
function battleground_aberration_repair(array $row, array $payload): bool
{
    if ($row['card_type'] !== 'minion' || !empty($row['creature_type'])) return false;
    $types = [];
    foreach (['ru', 'en'] as $locale) {
        if (!is_array($payload[$locale] ?? null)) continue;
        $type = battleground_creature_type($payload[$locale]);
        if ($type === null) return false;
        $types[] = $type;
    }
    return $types !== [] && count(array_unique($types)) === 1 && $types[0] === 'aberration';
}
