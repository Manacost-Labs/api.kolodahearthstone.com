<?php
declare(strict_types=1);

// Presentation helpers only. No database, session, upload or credential access.

function h($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function format_release_date_ru($value): string
{
    $value = trim((string)$value);
    if (!preg_match('/^(\d{4})-(\d{2})-(\d{2})/', $value, $matches)) {
        return '—';
    }

    $year = (int)$matches[1];
    $month = (int)$matches[2];
    $day = (int)$matches[3];
    $months = [
        1 => 'января',
        2 => 'февраля',
        3 => 'марта',
        4 => 'апреля',
        5 => 'мая',
        6 => 'июня',
        7 => 'июля',
        8 => 'августа',
        9 => 'сентября',
        10 => 'октября',
        11 => 'ноября',
        12 => 'декабря',
    ];
    if (!checkdate($month, $day, $year)) {
        return '—';
    }

    return $day . ' ' . $months[$month] . ' ' . $year;
}

function versioned_asset($url, $updatedAt = null): string
{
    $url = (string)$url;
    if ($url === '') {
        return '';
    }

    $version = $updatedAt ? strtotime((string)$updatedAt) : time();
    if (!$version) {
        $version = time();
    }

    return $url . (strpos($url, '?') === false ? '?' : '&') . 'v=' . $version;
}

function panel_absolute_asset_url($url, $version = null): ?string
{
    $url = trim((string)$url);
    if ($url === '') {
        return null;
    }
    if (!preg_match('~^https?://~i', $url)) {
        $url = 'https://api.kolodahearthstone.com/' . ltrim($url, '/');
    }

    $version = trim((string)$version);
    if ($version !== '') {
        $url .= (strpos($url, '?') === false ? '?' : '&') . 'v=' . rawurlencode($version);
    }

    return $url;
}

function horizontal_art_preview($url, string $label): string
{
    $url = trim((string)$url);
    if ($url === '') {
        return '';
    }

    $safeUrl = h($url);
    $safeLabel = h($label);
    $tooltip = h($label . "\nГоризонтальный crop · 320×64 WebP");

    return '<figure class="horizontal-art-preview">'
        . '<button type="button" class="horizontal-art-button" data-preview="' . $safeUrl . '" '
        . 'data-tooltip="' . $tooltip . '" aria-label="Открыть горизонтальный crop: ' . $safeLabel . '">'
        . '<img src="' . $safeUrl . '" alt="" loading="lazy" decoding="async" width="160" height="32">'
        . '</button><figcaption><span>Crop 320×64</span>'
        . '<a href="' . $safeUrl . '" target="_blank" rel="noopener" '
        . 'aria-label="Открыть URL горизонтального crop: ' . $safeLabel . '">URL</a>'
        . '</figcaption></figure>';
}

function creature_types(): array
{
    return [
        'all' => 'Общие',
        'undead' => 'Нежить',
        'dragon' => 'Дракон',
        'mech' => 'Механизм',
        'murloc' => 'Мурлок',
        'demon' => 'Демон',
        'quilboar' => 'Свинобраз',
        'naga' => 'Нага',
        'pirate' => 'Пират',
        'beast' => 'Зверь',
        'elemental' => 'Элементаль',
    ];
}

function card_types(): array
{
    return [
        'minion' => 'Существо',
        'spell' => 'Заклинание',
    ];
}

function filter_card_types(): array
{
    return card_types() + ['hero' => 'Герой', 'hero_skin' => 'Скины героев', 'pet' => 'Питомцы', 'coin' => 'Монетки', 'timewarped' => 'Хрономальные', 'constructed' => 'Стандарт/Вольный'] + library_types();
}

function card_type_label($type): string
{
    $types = filter_card_types();
    $type = (string)$type;

    return $types[$type] ?? 'Существо';
}

function timewarped_type_label($type): string
{
    $type = (string)$type;
    if ($type === 'minion') {
        return 'Существо';
    }
    if ($type === 'spell') {
        return 'Заклинание таверны';
    }
    if ($type === 'hero_power') {
        return 'Сила героя';
    }

    return $type !== '' ? $type : 'Карта';
}

function constructed_format_label(string $format): string
{
    return [
        'standard' => 'Стандартный',
        'wild' => 'Вольный',
        'all' => 'Стандартный и Вольный',
    ][$format] ?? $format;
}

function constructed_card_type_label($type): string
{
    $type = strtoupper((string)$type);
    return [
        'MINION' => 'Существо',
        'SPELL' => 'Заклинание',
        'WEAPON' => 'Оружие',
        'HERO' => 'Герой',
        'HERO_POWER' => 'Сила героя',
        'LOCATION' => 'Локация',
    ][$type] ?? ($type !== '' ? $type : 'Карта');
}

function library_types(): array
{
    return [
        'anomaly' => 'Аномалии',
        'quest' => 'Квесты',
        'darkmoon_prize' => 'Призы Ярмарки',
        'reward' => 'Награды',
        'trinket' => 'Аксессуары',
    ];
}

function library_type_label($type): string
{
    $types = library_types();
    $type = (string)$type;

    return $types[$type] ?? $type;
}

function creature_type_label($type): string
{
    $types = creature_types();
    $type = (string)$type;

    return $types[$type] ?? '';
}

function mechanic_labels(): array
{
    return [
        'AURA' => 'Аура',
        'AVENGE' => 'Месть',
        'BACON_RALLY' => 'Боевой раж',
        'BACON_SPELLCRAFT_ID' => 'Чародейство',
        'BATTLECRY' => 'Боевой клич',
        'CHOOSE_ONE' => 'Выберите эффект',
        'DEATHRATTLE' => 'Предсмертный хрип',
        'DISCOVER' => 'Раскопка',
        'DIVINE_SHIELD' => 'Божественный щит',
        'END_OF_TURN_TRIGGER' => 'В конце хода',
        'InvisibleDeathrattle' => 'Скрытый предсмертный хрип',
        'MAGNETIC' => 'Магнетизм',
        'POISONOUS' => 'Яд',
        'REBORN' => 'Перерождение',
        'START_OF_COMBAT' => 'Начало боя',
        'STEALTH' => 'Маскировка',
        'TAUNT' => 'Провокация',
        'TRIGGER_VISUAL' => 'Срабатывающий эффект',
        'VENOMOUS' => 'Токсичность',
        'WINDFURY' => 'Неистовство ветра',
    ];
}

function card_mechanics(?string $notes): array
{
    $notes = (string)$notes;
    if ($notes === '' || !preg_match('/(?:^|\R)Механики:\s*([^\r\n]+)/u', $notes, $matches)) {
        return [];
    }

    $labels = mechanic_labels();
    $seen = [];
    $mechanics = [];
    foreach (explode(',', $matches[1]) as $slug) {
        $slug = trim($slug);
        if ($slug === '' || isset($seen[$slug])) {
            continue;
        }
        $seen[$slug] = true;
        $mechanics[] = [
            'slug' => $slug,
            'label' => $labels[$slug] ?? $slug,
        ];
    }

    return $mechanics;
}

function card_tooltip(array $card): string
{
    $parts = [];
    $parts[] = (string)$card['name'];
    if (!empty($card['name_en'])) {
        $parts[] = (string)$card['name_en'];
    }
    $meta = [];
    $meta[] = card_type_label($card['card_type'] ?? 'minion');
    if (!empty($card['tavern_tier'])) {
        $meta[] = 'Таверна ' . (int)$card['tavern_tier'];
    }
    if ($card['attack'] !== null && $card['health'] !== null) {
        $meta[] = (int)$card['attack'] . '/' . (int)$card['health'];
    }
    $type = creature_type_label($card['creature_type'] ?? '');
    if ($type !== '') {
        $meta[] = $type;
    }
    $meta[] = !empty($card['in_pool']) ? 'В пуле' : 'Не в пуле';
    if (!empty($card['duos_only'])) {
        $meta[] = 'Только дуо';
    }
    $parts[] = implode(' · ', $meta);
    if (!empty($card['notes'])) {
        $notes = trim((string)$card['notes']);
        if (mb_strlen($notes, 'UTF-8') > 260) {
            $notes = mb_substr($notes, 0, 260, 'UTF-8') . '...';
        }
        $parts[] = $notes;
    }

    return implode("\n", array_filter($parts, static fn($value) => $value !== ''));
}

function card_search_text(array $card): string
{
    $mechanics = card_mechanics($card['notes'] ?? null);
    $mechanicsText = implode(' ', array_map(
        static fn(array $mechanic): string => $mechanic['slug'] . ' ' . $mechanic['label'],
        $mechanics
    ));

    return mb_strtolower(implode(' ', [
        $card['name'] ?? '',
        $card['name_en'] ?? '',
        $card['card_id'] ?? '',
        $card['dbf'] ?? '',
        card_type_label($card['card_type'] ?? 'minion'),
        creature_type_label($card['creature_type'] ?? ''),
        $mechanicsText,
    ]), 'UTF-8');
}

function constructed_card_tooltip(array $card): string
{
    $parts = [];
    $parts[] = (string)($card['name_ru'] ?: $card['name_en'] ?: $card['card_id']);
    if (!empty($card['name_en']) && $card['name_en'] !== $card['name_ru']) {
        $parts[] = (string)$card['name_en'];
    }
    $meta = [];
    if (!empty($card['formats'])) {
        $meta[] = implode(', ', array_map('constructed_format_label', array_filter(explode(',', (string)$card['formats']))));
    }
    $meta[] = constructed_card_type_label($card['card_type'] ?? '');
    if (!empty($card['card_set'])) {
        $meta[] = (string)$card['card_set'];
    }
    if (!empty($card['class_slug'])) {
        $meta[] = (string)$card['class_slug'];
    }
    if ($card['mana_cost'] !== null) {
        $meta[] = 'Мана ' . (int)$card['mana_cost'];
    }
    if ($card['attack'] !== null || $card['health'] !== null) {
        $meta[] = ($card['attack'] ?? '—') . '/' . ($card['health'] ?? '—');
    }
    $parts[] = implode(' · ', array_filter($meta, static fn($value): bool => (string)$value !== ''));
    $text = trim(strip_tags((string)($card['text_ru'] ?: $card['text_en'] ?: '')));
    if ($text !== '') {
        if (mb_strlen($text, 'UTF-8') > 260) {
            $text = mb_substr($text, 0, 260, 'UTF-8') . '...';
        }
        $parts[] = $text;
    }

    return implode("\n", array_filter($parts, static fn($value): bool => (string)$value !== ''));
}

function constructed_card_search_text(array $card): string
{
    return mb_strtolower(implode(' ', [
        $card['name_ru'] ?? '',
        $card['name_en'] ?? '',
        $card['card_id'] ?? '',
        $card['dbf'] ?? '',
        $card['card_type'] ?? '',
        constructed_card_type_label($card['card_type'] ?? ''),
        $card['card_set'] ?? '',
        $card['class_slug'] ?? '',
        $card['rarity'] ?? '',
        $card['artist'] ?? '',
        strip_tags((string)($card['text_ru'] ?? '')),
        strip_tags((string)($card['text_en'] ?? '')),
    ]), 'UTF-8');
}

function json_array($value, array $default = []): array
{
    if ($value === null || $value === '') {
        return $default;
    }
    if (is_array($value)) {
        return $value;
    }

    $decoded = json_decode((string)$value, true);

    return is_array($decoded) ? $decoded : $default;
}

function compact_text($value): string
{
    if ($value === null || is_bool($value)) {
        return $value === true ? 'true' : ($value === false ? 'false' : '');
    }
    if (is_scalar($value)) {
        return trim((string)$value);
    }
    if (!is_array($value)) {
        return '';
    }

    $parts = [];
    foreach ($value as $item) {
        $text = compact_text($item);
        if ($text !== '') {
            $parts[] = $text;
        }
    }

    return implode(' ', $parts);
}

function constructed_related_heading_ru(?string $heading): string
{
    $heading = trim((string)$heading);
    $labels = [
        'Related cards' => 'Сопутствующие карты',
        'Generated cards' => 'Создаваемые карты',
        'Cast spells' => 'Варианты задания',
        'Generated Rewards' => 'Награды',
        'Combined Reward' => 'Объединённая награда',
        'Fabled tokens' => 'Формы героя саги',
        'Tokens' => 'Токены',
        'Modules' => 'Модули',
        'Art pieces' => 'Варианты арта',
        'Hero power' => 'Сила героя',
        'Hero powers' => 'Силы героя',
        'Additional hero powers' => 'Дополнительные силы героя',
        'Quest rewards' => 'Награды за задания',
        'Rewards' => 'Награды',
        'Treasures' => 'Сокровища',
        'Forms' => 'Формы',
    ];

    return $labels[$heading] ?? ($heading !== '' ? $heading : 'Сопутствующие карты');
}

function wiki_sound_count(?array $meta): int
{
    $count = 0;
    foreach (json_array($meta['sounds_json'] ?? null) as $group) {
        $count += count($group['clips'] ?? []);
    }

    return $count;
}

function card_sound_count(array $card): int
{
    $count = 0;
    foreach (($card['sounds'] ?? []) as $group) {
        if (is_array($group)) {
            $count += count($group['clips'] ?? []);
        }
    }

    return $count;
}

function card_gallery_count(array $card): int
{
    return count(array_filter($card['gallery'] ?? [], static fn($item): bool => is_array($item)));
}

function wiki_related_count(?array $meta): int
{
    $count = 0;
    foreach (json_array($meta['related_cards_json'] ?? null) as $group) {
        $count += count($group['cards'] ?? []);
    }

    return $count;
}

function wiki_status_label(?array $meta): string
{
    if (!$meta) {
        return 'Нет';
    }
    $status = (string)($meta['status'] ?? '');
    if ($status === 'ok') {
        return 'OK';
    }
    if ($status === 'missing') {
        return 'Нет на wiki';
    }

    return 'Ошибка';
}

function wiki_status_class(?array $meta): string
{
    if (!$meta) {
        return ' empty';
    }
    $status = (string)($meta['status'] ?? '');
    if ($status === 'ok') {
        return '';
    }
    if ($status === 'missing') {
        return ' missing';
    }

    return ' error';
}
