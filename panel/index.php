<?php
declare(strict_types=1);

require __DIR__ . '/lib/auth.php';
require __DIR__ . '/lib/api_tokens.php';
require __DIR__ . '/lib/parser_control.php';

$panelUser = panel_require_auth();

$config = require __DIR__ . '/config.php';

function db(array $config): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) {
        return $pdo;
    }

    $pdo = new PDO($config['db']['dsn'], $config['db']['user'], $config['db']['password'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);

    return $pdo;
}

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

function panel_attach_horizontal_art(PDO $pdo, array $rows, string $entityType, callable $entityId): array
{
    if (!$rows) {
        return [];
    }

    $ids = [];
    foreach ($rows as $row) {
        $id = trim((string)$entityId($row));
        if ($id !== '') {
            $ids[$id] = true;
        }
    }
    if (!$ids) {
        return $rows;
    }

    $params = ['entity_type' => $entityType];
    $placeholders = [];
    foreach (array_keys($ids) as $index => $id) {
        $name = 'horizontal_panel_id_' . $index;
        $placeholders[] = ':' . $name;
        $params[$name] = $id;
    }

    try {
        $stmt = $pdo->prepare(
            'SELECT entity_id, local_image_url, generated_at '
            . 'FROM horizontal_art_assets '
            . "WHERE status = 'ready' AND entity_type = :entity_type "
            . 'AND entity_id IN (' . implode(',', $placeholders) . ')'
        );
        $stmt->execute($params);
        $assets = [];
        foreach ($stmt->fetchAll() as $asset) {
            $assets[(string)$asset['entity_id']] = panel_absolute_asset_url(
                $asset['local_image_url'] ?? null,
                $asset['generated_at'] ?? null
            );
        }
    } catch (Throwable $e) {
        // The catalogue remains usable while an older database is being migrated.
        $assets = [];
    }

    foreach ($rows as &$row) {
        $id = trim((string)$entityId($row));
        $row['horizontal_image_url'] = $assets[$id] ?? null;
    }
    unset($row);

    return $rows;
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

function csrf(): string
{
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
    }

    return $_SESSION['csrf'];
}

function require_csrf(): void
{
    $sent = $_POST['csrf'] ?? '';
    if (!is_string($sent) || !hash_equals(csrf(), $sent)) {
        throw new RuntimeException('Сессия формы устарела. Обновите страницу и отправьте карту еще раз.');
    }
}

function int_or_null($value): ?int
{
    if ($value === null || $value === '') {
        return null;
    }

    return (int)$value;
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

function card_type_or_default($value): string
{
    $value = trim((string)$value);
    if ($value === '') {
        return 'minion';
    }
    if (!array_key_exists($value, card_types())) {
        throw new RuntimeException('Неверный тип карты.');
    }

    return $value;
}

function creature_type_label($type): string
{
    $types = creature_types();
    $type = (string)$type;

    return $types[$type] ?? '';
}

function creature_type_or_null($value): ?string
{
    $value = trim((string)$value);
    if ($value === '') {
        return null;
    }
    if (!array_key_exists($value, creature_types())) {
        throw new RuntimeException('Неверный тип существа.');
    }

    return $value;
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

function ensure_upload_dirs(array $config): void
{
    foreach (['cards', 'golden', 'art', 'framed'] as $dir) {
        $path = $config['upload_dir'] . '/' . $dir;
        if (!is_dir($path)) {
            mkdir($path, 0755, true);
        }
    }
}

function upload_image(array $config, string $field, string $kind, ?string $current): ?string
{
    if (empty($_FILES[$field]) || !is_array($_FILES[$field]) || ($_FILES[$field]['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_NO_FILE) {
        return $current;
    }

    $file = $_FILES[$field];
    $error = (int)($file['error'] ?? UPLOAD_ERR_OK);
    if ($error !== UPLOAD_ERR_OK) {
        $messages = [
            UPLOAD_ERR_INI_SIZE => 'Файл слишком большой для текущего лимита PHP.',
            UPLOAD_ERR_FORM_SIZE => 'Файл слишком большой для формы.',
            UPLOAD_ERR_PARTIAL => 'Файл загрузился не полностью. Попробуйте еще раз.',
            UPLOAD_ERR_NO_TMP_DIR => 'На сервере не найдена временная папка для загрузок.',
            UPLOAD_ERR_CANT_WRITE => 'Сервер не смог записать временный файл.',
            UPLOAD_ERR_EXTENSION => 'PHP-расширение остановило загрузку файла.',
        ];
        throw new RuntimeException($messages[$error] ?? 'Не удалось загрузить файл.');
    }
    if (($file['size'] ?? 0) > $config['max_upload_bytes']) {
        throw new RuntimeException('Файл слишком большой. Максимум 16 МБ.');
    }

    $tmp = (string)$file['tmp_name'];
    $info = @getimagesize($tmp);
    if (!$info || empty($info['mime'])) {
        throw new RuntimeException('Файл должен быть изображением.');
    }

    $extensions = [
        'image/png' => 'png',
        'image/jpeg' => 'jpg',
        'image/webp' => 'webp',
    ];
    if (!isset($extensions[$info['mime']])) {
        throw new RuntimeException('Поддерживаются только PNG, JPG и WEBP.');
    }

    ensure_upload_dirs($config);
    $name = date('Ymd-His') . '-' . bin2hex(random_bytes(6)) . '.' . $extensions[$info['mime']];
    $relative = '/' . $kind . '/' . $name;
    $target = $config['upload_dir'] . $relative;
    if (!move_uploaded_file($tmp, $target)) {
        if (!is_writable(dirname($target))) {
            throw new RuntimeException('Папка загрузок недоступна для записи.');
        }
        throw new RuntimeException('Не удалось сохранить файл.');
    }
    chmod($target, 0644);

    return $config['upload_url'] . $relative;
}

function find_card(PDO $pdo, int $id): ?array
{
    $stmt = $pdo->prepare('SELECT * FROM battlegrounds_cards WHERE id = ?');
    $stmt->execute([$id]);
    $row = $stmt->fetch();

    return $row ?: null;
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

function ensure_wiki_term_schema(PDO $pdo): void
{
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS battlegrounds_wiki_term_translations (
            term_type VARCHAR(32) NOT NULL,
            term_en VARCHAR(255) NOT NULL,
            term_ru VARCHAR(255) DEFAULT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (term_type, term_en),
            KEY idx_term_type (term_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ");
    try {
        $pdo->exec("
            ALTER TABLE battlegrounds_wiki_term_translations
            MODIFY term_en VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
        ");
    } catch (Throwable $e) {
        // Older MySQL variants can reject no-op collation changes; the table still works.
    }
}

function wiki_term_type_labels(): array
{
    return [
        'mechanic' => 'Wiki mechanics',
        'tag' => 'Wiki tags',
        'full_tag' => 'Full tags',
    ];
}

function collect_wiki_terms_from_column(PDO $pdo, string $table, string $column, string $termType, array &$seen, ?string $where = null): void
{
    $whereSql = $where ? ' WHERE ' . $where : '';
    try {
        $rows = $pdo->query("SELECT `$column` AS terms_json FROM `$table`$whereSql")->fetchAll();
    } catch (Throwable $e) {
        return;
    }

    foreach ($rows as $row) {
        foreach (json_array($row['terms_json'] ?? null) as $term) {
            $term = trim((string)$term);
            if ($term !== '') {
                $seen[$termType][$term] = true;
            }
        }
    }
}

function sync_wiki_terms(PDO $pdo): void
{
    ensure_wiki_term_schema($pdo);
    $seen = array_fill_keys(array_keys(wiki_term_type_labels()), []);
    collect_wiki_terms_from_column($pdo, 'battlegrounds_card_wiki_meta', 'wiki_mechanics_json', 'mechanic', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'battlegrounds_card_wiki_meta', 'wiki_tags_json', 'tag', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'battlegrounds_timewarped_cards', 'wiki_mechanics_json', 'mechanic', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'battlegrounds_timewarped_cards', 'wiki_tags_json', 'tag', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'battlegrounds_timewarped_cards', 'full_tags_json', 'full_tag', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'constructed_card_wiki_meta', 'wiki_mechanics_json', 'mechanic', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'constructed_card_wiki_meta', 'wiki_tags_json', 'tag', $seen, "status = 'ok'");

    $stmt = $pdo->prepare("
        INSERT IGNORE INTO battlegrounds_wiki_term_translations (term_type, term_en)
        VALUES (:term_type, :term_en)
    ");
    foreach ($seen as $type => $terms) {
        foreach (array_keys($terms) as $term) {
            $stmt->execute([
                'term_type' => $type,
                'term_en' => $term,
            ]);
        }
    }
}

function wiki_term_groups(PDO $pdo): array
{
    sync_wiki_terms($pdo);
    $groups = array_fill_keys(array_keys(wiki_term_type_labels()), []);
    $stmt = $pdo->query("
        SELECT term_type, term_en, term_ru, updated_at
        FROM battlegrounds_wiki_term_translations
        ORDER BY term_type, term_en
    ");
    foreach ($stmt->fetchAll() as $row) {
        $type = (string)$row['term_type'];
        if (!isset($groups[$type])) {
            $groups[$type] = [];
        }
        $groups[$type][] = $row;
    }

    return $groups;
}

function load_wiki_meta_map(PDO $pdo, array $cards): array
{
    $cardIds = array_values(array_unique(array_filter(array_map(
        static fn(array $card): string => (string)($card['card_id'] ?? ''),
        $cards
    ))));
    if (!$cardIds) {
        return [];
    }

    $placeholders = implode(',', array_fill(0, count($cardIds), '?'));
    $stmt = $pdo->prepare('SELECT * FROM battlegrounds_card_wiki_meta WHERE card_id IN (' . $placeholders . ')');
    $stmt->execute($cardIds);

    $map = [];
    foreach ($stmt->fetchAll() as $row) {
        $map[(string)$row['card_id']] = $row;
    }

    return $map;
}

/**
 * Return the official golden/tripled row for every base card on the current page.
 * Golden cards remain separate database records because their stats and text can
 * differ, but the admin catalogue presents them as variants of the base card.
 */
function load_golden_variant_map(PDO $pdo, array $cards): array
{
    $baseDbfs = array_values(array_unique(array_filter(array_map(
        static fn(array $card): ?int => $card['dbf'] !== null ? (int)$card['dbf'] : null,
        $cards
    ))));
    if (!$baseDbfs) {
        return [];
    }

    $placeholders = implode(',', array_fill(0, count($baseDbfs), '?'));
    $stmt = $pdo->prepare(
        "SELECT * FROM battlegrounds_cards WHERE variant_kind = 'golden' AND base_dbf IN ($placeholders)"
    );
    $stmt->execute($baseDbfs);

    $map = [];
    foreach ($stmt->fetchAll() as $row) {
        $map[(int)$row['base_dbf']] = $row;
    }
    return $map;
}

function load_constructed_wiki_meta_map(PDO $pdo, array $cards): array
{
    $cardIds = array_values(array_unique(array_filter(array_map(
        static fn(array $card): string => (string)($card['card_id'] ?? ''),
        $cards
    ))));
    if (!$cardIds) {
        return [];
    }

    $placeholders = implode(',', array_fill(0, count($cardIds), '?'));
    $stmt = $pdo->prepare('SELECT * FROM constructed_card_wiki_meta WHERE card_id IN (' . $placeholders . ')');
    $stmt->execute($cardIds);

    $map = [];
    foreach ($stmt->fetchAll() as $row) {
        $map[(string)$row['card_id']] = $row;
    }

    return $map;
}

function load_constructed_related_card_map(PDO $pdo, array $wikiMetaMap): array
{
    $cardIds = [];
    foreach ($wikiMetaMap as $meta) {
        foreach (json_array($meta['related_card_ids_json'] ?? null) as $cardId) {
            $cardId = trim((string)$cardId);
            if ($cardId !== '') {
                $cardIds[$cardId] = true;
            }
        }
    }
    if (!$cardIds) {
        return [];
    }

    $ids = array_keys($cardIds);
    $placeholders = implode(',', array_fill(0, count($ids), '?'));
    $stmt = $pdo->prepare('SELECT * FROM constructed_cards WHERE card_id IN (' . $placeholders . ')');
    $stmt->execute($ids);

    $map = [];
    foreach ($stmt->fetchAll() as $row) {
        $map[(string)$row['card_id']] = $row;
    }

    return $map;
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

function bind_statement_params(PDOStatement $stmt, array $params): void
{
    foreach ($params as $key => $value) {
        $stmt->bindValue(':' . $key, $value, is_int($value) ? PDO::PARAM_INT : PDO::PARAM_STR);
    }
}

function query_url(array $overrides = []): string
{
    $query = $_GET;
    unset($query['action'], $query['id']);
    $query = array_merge($query, $overrides);

    foreach ($query as $key => $value) {
        if ($value === null || $value === '' || ($key === 'page' && (int)$value <= 1)) {
            unset($query[$key]);
        }
    }

    $queryString = http_build_query($query);

    return '/' . ($queryString === '' ? '' : '?' . $queryString);
}

$pdo = db($config);
$action = $_POST['action'] ?? $_GET['action'] ?? 'list';
$message = '';
$error = '';
$issuedApiToken = null;
$apiTokens = [];
$apiTokenLoadError = '';
$apiTokenManagerConfig = null;
$apiTokenIssueNonce = '';

try {
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST') {
        require_csrf();
        if ($action === 'issue_api_token') {
            $action = 'api_tokens';
            $formNonce = $_POST['form_nonce'] ?? '';
            if (!panel_consume_state($_SESSION, 'api_token_issue', $formNonce, null, 30 * 60)) {
                throw new RuntimeException('Форма выпуска уже использована или устарела. Обновите страницу.');
            }
            $issuePayload = panel_api_token_normalize_issue_input([
                'name' => $_POST['name'] ?? '',
                'scopes' => $_POST['scopes'] ?? [],
                'expires_in_days' => $_POST['expires_in_days'] ?? '',
            ]);
            if (!panel_api_token_consume_issue_budget($_SESSION)) {
                throw new RuntimeException('За короткое время выпущено слишком много токенов. Повторите через 15 минут.');
            }
            $issuedApiToken = panel_api_token_issue($issuePayload);
            panel_auth_audit('api_token_issued', [
                'user_id' => $panelUser['id'] ?? 0,
                'token_id' => $issuedApiToken['id'],
                'scopes' => $issuedApiToken['scopes'],
                'expires_at' => $issuedApiToken['expires_at'],
            ]);
            $message = 'Токен выпущен. Скопируйте секрет сейчас — повторно он не показывается.';
        } elseif ($action === 'revoke_api_token') {
            $action = 'api_tokens';
            $tokenId = trim((string)($_POST['token_id'] ?? ''));
            panel_api_token_revoke($tokenId);
            panel_auth_audit('api_token_revoked', [
                'user_id' => $panelUser['id'] ?? 0,
                'token_id' => $tokenId,
            ]);
            $message = 'Токен отозван и больше не даёт доступ к API.';
        } elseif ($action === 'save') {
            $id = int_or_null($_POST['id'] ?? null);
            $current = $id ? find_card($pdo, $id) : null;
            $cardImage = upload_image($config, 'card_image_file', 'cards', $current['card_image'] ?? null);
            $goldenImage = upload_image($config, 'golden_image_file', 'golden', $current['golden_image'] ?? null);
            $artImage = upload_image($config, 'art_image_file', 'art', $current['art_image'] ?? null);
            $framedImage = upload_image($config, 'framed_image_file', 'framed', $current['framed_image'] ?? null);

            $data = [
                'name' => trim((string)($_POST['name'] ?? '')),
                'name_en' => trim((string)($_POST['name_en'] ?? '')),
                'card_id' => trim((string)($_POST['card_id'] ?? '')),
                'dbf' => int_or_null($_POST['dbf'] ?? null),
                'card_type' => card_type_or_default($_POST['card_type'] ?? 'minion'),
                'tavern_tier' => int_or_null($_POST['tavern_tier'] ?? null),
                'creature_type' => creature_type_or_null($_POST['creature_type'] ?? null),
                'attack' => int_or_null($_POST['attack'] ?? null),
                'health' => int_or_null($_POST['health'] ?? null),
                'in_pool' => isset($_POST['in_pool']) ? 1 : 0,
                'duos_only' => isset($_POST['duos_only']) ? 1 : 0,
                'card_image' => $cardImage,
                'golden_image' => $goldenImage,
                'art_image' => $artImage,
                'framed_image' => $framedImage,
                'notes' => trim((string)($_POST['notes'] ?? '')),
            ];

            if ($data['name'] === '' || $data['card_id'] === '') {
                throw new RuntimeException('Название карты и card_id обязательны.');
            }
            if ($data['tavern_tier'] !== null && ($data['tavern_tier'] < 1 || $data['tavern_tier'] > 7)) {
                throw new RuntimeException('Уровень таверны должен быть от 1 до 7.');
            }

            if ($id) {
                $stmt = $pdo->prepare(
                    'UPDATE battlegrounds_cards
                     SET name=:name, name_en=:name_en, card_id=:card_id, dbf=:dbf, tavern_tier=:tavern_tier,
                         card_type=:card_type, creature_type=:creature_type, attack=:attack, health=:health,
                         in_pool=:in_pool, duos_only=:duos_only,
                         card_image=:card_image, golden_image=:golden_image, art_image=:art_image, framed_image=:framed_image, notes=:notes
                     WHERE id=:id'
                );
                $stmt->execute($data + ['id' => $id]);
                $message = 'Карта обновлена.';
            } else {
                $stmt = $pdo->prepare('SELECT * FROM battlegrounds_cards WHERE card_id = ? LIMIT 1');
                $stmt->execute([$data['card_id']]);
                $existing = $stmt->fetch();

                if ($existing) {
                    $data['card_image'] = $data['card_image'] ?: $existing['card_image'];
                    $data['golden_image'] = $data['golden_image'] ?: $existing['golden_image'];
                    $data['art_image'] = $data['art_image'] ?: $existing['art_image'];
                    $data['framed_image'] = $data['framed_image'] ?: $existing['framed_image'];
                    $stmt = $pdo->prepare(
                        'UPDATE battlegrounds_cards
                         SET name=:name, name_en=:name_en, dbf=:dbf, tavern_tier=:tavern_tier,
                             card_type=:card_type, creature_type=:creature_type, attack=:attack, health=:health,
                             in_pool=:in_pool, duos_only=:duos_only,
                             card_image=:card_image, golden_image=:golden_image, art_image=:art_image, framed_image=:framed_image, notes=:notes
                         WHERE card_id=:card_id'
                    );
                    $stmt->execute($data);
                    $message = 'Карта с таким card_id уже была в базе, я обновил существующую запись.';
                } else {
                    $stmt = $pdo->prepare(
                        'INSERT INTO battlegrounds_cards
                         (name, name_en, card_id, dbf, card_type, tavern_tier, creature_type, attack, health, in_pool, duos_only, card_image, golden_image, art_image, framed_image, notes)
                         VALUES (:name, :name_en, :card_id, :dbf, :card_type, :tavern_tier, :creature_type, :attack, :health, :in_pool, :duos_only, :card_image, :golden_image, :art_image, :framed_image, :notes)'
                    );
                    $stmt->execute($data);
                    $message = 'Карта добавлена.';
                }
            }
            $action = 'list';
        } elseif ($action === 'delete') {
            $id = (int)($_POST['id'] ?? 0);
            $stmt = $pdo->prepare('DELETE FROM battlegrounds_cards WHERE id = ?');
            $stmt->execute([$id]);
            $message = 'Карта удалена.';
            $action = 'list';
        } elseif ($action === 'save_wiki_terms') {
            ensure_wiki_term_schema($pdo);
            $terms = $_POST['terms'] ?? [];
            if (!is_array($terms)) {
                throw new RuntimeException('Некорректный список терминов.');
            }
            $stmt = $pdo->prepare("
                UPDATE battlegrounds_wiki_term_translations
                SET term_ru = :term_ru
                WHERE term_type = :term_type AND term_en = :term_en
            ");
            $allowedTermTypes = array_keys(wiki_term_type_labels());
            foreach ($terms as $type => $items) {
                if (!in_array($type, $allowedTermTypes, true) || !is_array($items)) {
                    continue;
                }
                foreach ($items as $termEn => $termPayload) {
                    if (is_array($termPayload)) {
                        $termEn = trim((string)($termPayload['en'] ?? ''));
                        $termRu = $termPayload['ru'] ?? '';
                    } else {
                        $termEn = trim((string)$termEn);
                        $termRu = $termPayload;
                    }
                    if ($termEn === '') {
                        continue;
                    }
                    $termRu = trim((string)$termRu);
                    $stmt->execute([
                        'term_type' => $type,
                        'term_en' => $termEn,
                        'term_ru' => $termRu === '' ? null : $termRu,
                    ]);
                }
            }
            $message = 'Переводы Wiki terms сохранены.';
            $action = 'wiki_terms';
        }
    }
} catch (Throwable $e) {
    $error = $e->getMessage();
    if (in_array($action, ['issue_api_token', 'revoke_api_token'], true)) {
        $action = 'api_tokens';
    }
}

if ($action === 'api_tokens') {
    $apiTokenManagerConfig = panel_api_token_manager_config();
    if ($apiTokenManagerConfig !== null) {
        try {
            $apiTokens = panel_api_token_list();
        } catch (Throwable $exception) {
            $apiTokenLoadError = $exception->getMessage();
        }
    }
    $apiTokenIssueNonce = panel_issue_state($_SESSION, 'api_token_issue');
}

$editCard = null;
if ($action === 'edit') {
    $editCard = find_card($pdo, (int)($_GET['id'] ?? 0));
}

$q = trim((string)($_GET['q'] ?? ''));
$cardType = trim((string)($_GET['card_type'] ?? ''));
$constructedFormat = trim(strtolower((string)($_GET['constructed_format'] ?? 'all')));
$tier = trim((string)($_GET['tier'] ?? ''));
$creatureType = trim((string)($_GET['creature_type'] ?? ''));
$pool = trim((string)($_GET['pool'] ?? ''));
$duos = trim((string)($_GET['duos'] ?? ''));
$media = trim((string)($_GET['media'] ?? ''));
$skinRarity = strtolower(trim((string)($_GET['rarity'] ?? '')));
$perPage = (int)($_GET['per_page'] ?? 50);
if (!in_array($perPage, [25, 50, 100, 150], true)) {
    $perPage = 50;
}
$where = [];
$params = [];
if ($cardType !== '') {
    if (!array_key_exists($cardType, filter_card_types())) {
        $cardType = '';
    }
}
$showHeroes = $cardType === 'hero';
$showHeroSkins = $cardType === 'hero_skin';
$showPets = $cardType === 'pet';
$showCoins = $cardType === 'coin';
$showTimewarped = $cardType === 'timewarped';
$showConstructed = $cardType === 'constructed';
$showLibrary = array_key_exists($cardType, library_types());
$libraryType = $showLibrary ? $cardType : '';
if (!in_array($constructedFormat, ['all', 'standard', 'wild'], true)) {
    $constructedFormat = 'all';
}

if ($q !== '') {
    if ($showHeroSkins) {
        $where[] = '(name_en LIKE :q_skin_name OR card_id LIKE :q_skin_card_id OR dbf LIKE :q_skin_dbf OR character_name LIKE :q_skin_character OR actor LIKE :q_skin_actor OR artist LIKE :q_skin_artist OR rarity_slug LIKE :q_skin_rarity OR rarity_name_en LIKE :q_skin_rarity_en OR rarity_name_ru LIKE :q_skin_rarity_ru OR primary_category_en LIKE :q_skin_category OR primary_category_ru LIKE :q_skin_category_ru OR tags_json LIKE :q_skin_tags)';
        $params['q_skin_name'] = '%' . $q . '%';
        $params['q_skin_card_id'] = '%' . $q . '%';
        $params['q_skin_dbf'] = '%' . $q . '%';
        $params['q_skin_character'] = '%' . $q . '%';
        $params['q_skin_actor'] = '%' . $q . '%';
        $params['q_skin_artist'] = '%' . $q . '%';
        $params['q_skin_rarity'] = '%' . $q . '%';
        $params['q_skin_rarity_en'] = '%' . $q . '%';
        $params['q_skin_rarity_ru'] = '%' . $q . '%';
        $params['q_skin_category'] = '%' . $q . '%';
        $params['q_skin_category_ru'] = '%' . $q . '%';
        $params['q_skin_tags'] = '%' . $q . '%';
    } elseif ($showHeroes) {
        $where[] = '(name_en LIKE :q_hero_name_en OR name_ru LIKE :q_hero_name_ru OR card_id LIKE :q_hero_card_id OR dbf LIKE :q_hero_dbf OR artist LIKE :q_hero_artist OR as_hero LIKE :q_hero_as_hero OR hero_description LIKE :q_hero_description OR hero_power_json LIKE :q_hero_power OR buddy_json LIKE :q_hero_buddy)';
        $params['q_hero_name_en'] = '%' . $q . '%';
        $params['q_hero_name_ru'] = '%' . $q . '%';
        $params['q_hero_card_id'] = '%' . $q . '%';
        $params['q_hero_dbf'] = '%' . $q . '%';
        $params['q_hero_artist'] = '%' . $q . '%';
        $params['q_hero_as_hero'] = '%' . $q . '%';
        $params['q_hero_description'] = '%' . $q . '%';
        $params['q_hero_power'] = '%' . $q . '%';
        $params['q_hero_buddy'] = '%' . $q . '%';
    } elseif ($showTimewarped) {
        $where[] = '(name_en LIKE :q_tw_name_en OR name_ru LIKE :q_tw_name_ru OR card_id LIKE :q_tw_card_id OR dbf LIKE :q_tw_dbf OR text_en LIKE :q_tw_text_en OR text_ru LIKE :q_tw_text_ru OR artist LIKE :q_tw_artist OR minion_type LIKE :q_tw_minion_type OR race LIKE :q_tw_race OR wiki_mechanics_json LIKE :q_tw_mechanics OR wiki_tags_json LIKE :q_tw_tags)';
        $params['q_tw_name_en'] = '%' . $q . '%';
        $params['q_tw_name_ru'] = '%' . $q . '%';
        $params['q_tw_card_id'] = '%' . $q . '%';
        $params['q_tw_dbf'] = '%' . $q . '%';
        $params['q_tw_text_en'] = '%' . $q . '%';
        $params['q_tw_text_ru'] = '%' . $q . '%';
        $params['q_tw_artist'] = '%' . $q . '%';
        $params['q_tw_minion_type'] = '%' . $q . '%';
        $params['q_tw_race'] = '%' . $q . '%';
        $params['q_tw_mechanics'] = '%' . $q . '%';
        $params['q_tw_tags'] = '%' . $q . '%';
    } elseif ($showConstructed) {
        $where[] = '(c.name_ru LIKE :q_name_ru OR c.name_en LIKE :q_name_en OR c.card_id LIKE :q_card_id OR c.dbf LIKE :q_dbf OR c.text_ru LIKE :q_text_ru OR c.text_en LIKE :q_text_en OR c.flavor_ru LIKE :q_flavor_ru OR c.flavor_en LIKE :q_flavor_en OR c.artist LIKE :q_artist OR c.card_set LIKE :q_card_set OR c.class_slug LIKE :q_class OR c.minion_type LIKE :q_minion_type OR c.spell_school LIKE :q_spell_school OR c.rarity LIKE :q_rarity OR c.mechanics_json LIKE :q_mechanics OR c.referenced_tags_json LIKE :q_tags)';
        $params['q_name_ru'] = '%' . $q . '%';
        $params['q_name_en'] = '%' . $q . '%';
        $params['q_card_id'] = '%' . $q . '%';
        $params['q_dbf'] = '%' . $q . '%';
        $params['q_text_ru'] = '%' . $q . '%';
        $params['q_text_en'] = '%' . $q . '%';
        $params['q_flavor_ru'] = '%' . $q . '%';
        $params['q_flavor_en'] = '%' . $q . '%';
        $params['q_artist'] = '%' . $q . '%';
        $params['q_card_set'] = '%' . $q . '%';
        $params['q_class'] = '%' . $q . '%';
        $params['q_minion_type'] = '%' . $q . '%';
        $params['q_spell_school'] = '%' . $q . '%';
        $params['q_rarity'] = '%' . $q . '%';
        $params['q_mechanics'] = '%' . $q . '%';
        $params['q_tags'] = '%' . $q . '%';
    } elseif ($showPets) {
        $where[] = '(pet_name LIKE :q_pet_name OR variant_name LIKE :q_pet_variant OR card_id LIKE :q_pet_card_id OR dbf LIKE :q_pet_dbf OR page_title LIKE :q_pet_page)';
        $params['q_pet_name'] = '%' . $q . '%';
        $params['q_pet_variant'] = '%' . $q . '%';
        $params['q_pet_card_id'] = '%' . $q . '%';
        $params['q_pet_dbf'] = '%' . $q . '%';
        $params['q_pet_page'] = '%' . $q . '%';
    } elseif ($showCoins) {
        $where[] = '(coin_name_en LIKE :q_coin_name OR card_name_ru LIKE :q_coin_name_ru OR card_name_en LIKE :q_coin_name_en OR card_id LIKE :q_coin_card_id OR dbf LIKE :q_coin_dbf OR artist LIKE :q_coin_artist)';
        $params['q_coin_name'] = '%' . $q . '%';
        $params['q_coin_name_ru'] = '%' . $q . '%';
        $params['q_coin_name_en'] = '%' . $q . '%';
        $params['q_coin_card_id'] = '%' . $q . '%';
        $params['q_coin_dbf'] = '%' . $q . '%';
        $params['q_coin_artist'] = '%' . $q . '%';
    } elseif ($showLibrary) {
        $where[] = '(name_en LIKE :q_lib_name_en OR name_ru LIKE :q_lib_name_ru OR card_id LIKE :q_lib_card_id OR dbf LIKE :q_lib_dbf OR text_en LIKE :q_lib_text_en OR text_ru LIKE :q_lib_text_ru OR artist LIKE :q_lib_artist OR group_name_ru LIKE :q_lib_group OR tier_name_ru LIKE :q_lib_tier OR card_type LIKE :q_lib_card_type)';
        $params['q_lib_name_en'] = '%' . $q . '%';
        $params['q_lib_name_ru'] = '%' . $q . '%';
        $params['q_lib_card_id'] = '%' . $q . '%';
        $params['q_lib_dbf'] = '%' . $q . '%';
        $params['q_lib_text_en'] = '%' . $q . '%';
        $params['q_lib_text_ru'] = '%' . $q . '%';
        $params['q_lib_artist'] = '%' . $q . '%';
        $params['q_lib_group'] = '%' . $q . '%';
        $params['q_lib_tier'] = '%' . $q . '%';
        $params['q_lib_card_type'] = '%' . $q . '%';
    } else {
        $where[] = '(name LIKE :q_name OR name_en LIKE :q_name_en OR card_id LIKE :q_card_id OR dbf LIKE :q_dbf OR card_type LIKE :q_card_type OR creature_type LIKE :q_creature_type OR notes LIKE :q_notes OR EXISTS (
            SELECT 1
            FROM battlegrounds_cards variant_search
            WHERE variant_search.variant_kind = \'golden\'
              AND variant_search.base_dbf = battlegrounds_cards.dbf
              AND (variant_search.name LIKE :q_variant_name OR variant_search.name_en LIKE :q_variant_name_en OR variant_search.card_id LIKE :q_variant_card_id OR variant_search.dbf LIKE :q_variant_dbf OR variant_search.notes LIKE :q_variant_notes)
        ))';
        $params['q_name'] = '%' . $q . '%';
        $params['q_name_en'] = '%' . $q . '%';
        $params['q_card_id'] = '%' . $q . '%';
        $params['q_dbf'] = '%' . $q . '%';
        $params['q_card_type'] = '%' . $q . '%';
        $params['q_creature_type'] = '%' . $q . '%';
        $params['q_notes'] = '%' . $q . '%';
        $params['q_variant_name'] = '%' . $q . '%';
        $params['q_variant_name_en'] = '%' . $q . '%';
        $params['q_variant_card_id'] = '%' . $q . '%';
        $params['q_variant_dbf'] = '%' . $q . '%';
        $params['q_variant_notes'] = '%' . $q . '%';
    }
}

if (!$showHeroes && !$showHeroSkins && !$showPets && !$showCoins && !$showTimewarped && !$showConstructed && !$showLibrary) {
    // Golden/tripled cards are variants, not independent catalogue entries.
    $where[] = "variant_kind = 'base'";
    if ($cardType !== '') {
        $where[] = 'card_type = :card_type';
        $params['card_type'] = $cardType;
    }
    if ($tier !== '') {
        $where[] = 'tavern_tier = :tier';
        $params['tier'] = (int)$tier;
    }
    if ($creatureType !== '') {
        if (!array_key_exists($creatureType, creature_types())) {
            $creatureType = '';
        } else {
            $where[] = 'creature_type = :creature_type';
            $params['creature_type'] = $creatureType;
        }
    }
    if ($pool !== '') {
        if (!in_array($pool, ['0', '1'], true)) {
            $pool = '';
        } else {
            $where[] = 'in_pool = :in_pool';
            $params['in_pool'] = (int)$pool;
        }
    }
    if ($duos !== '') {
        if (!in_array($duos, ['0', '1'], true)) {
            $duos = '';
        } else {
            $where[] = 'duos_only = :duos_only';
            $params['duos_only'] = (int)$duos;
        }
    }
} elseif ($showHeroSkins) {
    if ($media !== '') {
        $skinMediaFilters = [
            'animated' => "animated_image_url IS NOT NULL AND animated_image_url <> ''",
            'gallery' => "COALESCE(JSON_LENGTH(gallery_json), 0) > 0",
            'sounds' => "COALESCE(JSON_LENGTH(sounds_json), 0) > 0",
            'partial' => "status = 'partial'",
        ];
        if (isset($skinMediaFilters[$media])) {
            $where[] = $skinMediaFilters[$media];
        } else {
            $media = '';
        }
    }
    if ($skinRarity !== '') {
        if (!in_array($skinRarity, ['basic', 'lite', 'full', 'diamond', 'legendary', 'mythic', 'unknown'], true)) {
            $skinRarity = '';
        } else {
            $where[] = 'rarity_slug = :skin_rarity';
            $params['skin_rarity'] = $skinRarity;
        }
    }
    $where[] = "status IN ('ok', 'partial')";
    if ($tier !== '' || $creatureType !== '' || $pool !== '' || $duos !== '') {
        $tier = '';
        $creatureType = '';
        $pool = '';
        $duos = '';
    }
} elseif ($showPets) {
    if ($media !== '') {
        $petMediaFilters = [
            'gallery' => "COALESCE(JSON_LENGTH(gallery_json), 0) > 0",
            'background' => "end_screen_background_url IS NOT NULL AND end_screen_background_url <> ''",
        ];
        if (isset($petMediaFilters[$media])) {
            $where[] = $petMediaFilters[$media];
        } else {
            $media = '';
        }
    }
    if ($tier !== '') {
        if (!in_array($tier, ['1', '2', '3', '4'], true)) {
            $tier = '';
        } else {
            $where[] = 'level = :level';
            $params['level'] = (int)$tier;
        }
    }
    $where[] = "status IN ('ok', 'partial')";
    if ($creatureType !== '' || $pool !== '' || $duos !== '') {
        $creatureType = '';
        $pool = '';
        $duos = '';
    }
} elseif ($showCoins) {
    if ($tier !== '' || $creatureType !== '' || $pool !== '' || $duos !== '' || $media !== '') {
        $tier = '';
        $creatureType = '';
        $pool = '';
        $duos = '';
        $media = '';
    }
} elseif ($showHeroes) {
    if ($media !== '') {
        $heroMediaFilters = [
            'has_buddy' => 'buddy_dbf IS NOT NULL',
            'no_buddy' => 'buddy_dbf IS NULL',
            'hero_power_art' => "COALESCE(JSON_LENGTH(JSON_EXTRACT(hero_power_json, '$.gallery')), 0) > 0",
            'buddy_art' => "buddy_dbf IS NOT NULL AND COALESCE(JSON_LENGTH(JSON_EXTRACT(buddy_json, '$.gallery')), 0) > 0",
            'buddy_sounds' => "buddy_dbf IS NOT NULL AND COALESCE(JSON_LENGTH(JSON_EXTRACT(buddy_json, '$.sounds')), 0) > 0",
            'wiki_error' => "(JSON_EXTRACT(hero_power_json, '$.wiki_fetch_error') IS NOT NULL OR JSON_EXTRACT(buddy_json, '$.wiki_fetch_error') IS NOT NULL)",
        ];
        if (isset($heroMediaFilters[$media])) {
            $where[] = $heroMediaFilters[$media];
        } else {
            $media = '';
        }
    }
    if ($tier !== '' || $creatureType !== '' || $pool !== '' || $duos !== '') {
        $tier = '';
        $creatureType = '';
        $pool = '';
        $duos = '';
    }
} elseif ($showTimewarped) {
    if ($tier !== '') {
        $where[] = 'tavern_tier = :tier';
        $params['tier'] = (int)$tier;
    }
    if ($creatureType !== '') {
        if (!array_key_exists($creatureType, creature_types())) {
            $creatureType = '';
        } else {
            $where[] = '(LOWER(minion_type) = :creature_type_minion OR LOWER(race) = :creature_type_race)';
            $params['creature_type_minion'] = $creatureType;
            $params['creature_type_race'] = $creatureType;
        }
    }
    if ($pool !== '' || $duos !== '') {
        $pool = '';
        $duos = '';
    }
    $where[] = "status = 'ok'";
} elseif ($showConstructed) {
    $where[] = 'EXISTS (SELECT 1 FROM constructed_format_cards active_format WHERE active_format.card_id = c.card_id AND active_format.in_format = 1)';
    if ($constructedFormat !== 'all') {
        $where[] = 'EXISTS (SELECT 1 FROM constructed_format_cards ff WHERE ff.card_id = c.card_id AND ff.format_slug = :constructed_format AND ff.in_format = 1)';
        $params['constructed_format'] = $constructedFormat;
    }
    $constructedMedia = [
        'golden' => "(c.image_gold_url IS NOT NULL AND c.image_gold_url <> '')",
        'signature' => "(c.image_signature_url IS NOT NULL AND c.image_signature_url <> '')",
        'diamond' => "(c.image_diamond_url IS NOT NULL AND c.image_diamond_url <> '')",
        'animated_diamond' => "(c.animated_diamond_url IS NOT NULL AND c.animated_diamond_url <> '')",
    ];
    if ($media !== '') {
        if (!isset($constructedMedia[$media])) {
            $media = '';
        } else {
            $where[] = $constructedMedia[$media];
        }
    }
    if ($tier !== '' || $creatureType !== '' || $pool !== '' || $duos !== '') {
        $tier = '';
        $creatureType = '';
        $pool = '';
        $duos = '';
    }
} else {
    $where[] = 'library = :library';
    $params['library'] = $libraryType;
    if ($libraryType === 'darkmoon_prize' && $tier !== '') {
        if (!in_array($tier, ['1', '2', '3', '4'], true)) {
            $tier = '';
        } else {
            $where[] = 'tier_value = :tier';
            $params['tier'] = (int)$tier;
        }
    }
    if ($pool !== '') {
        if (!in_array($pool, ['0', '1'], true)) {
            $pool = '';
        } else {
            $where[] = 'in_pool = :in_pool';
            $params['in_pool'] = (int)$pool;
        }
    }
    if (($libraryType !== 'darkmoon_prize' && $tier !== '') || $creatureType !== '' || $duos !== '') {
        if ($libraryType !== 'darkmoon_prize') {
            $tier = '';
        }
        $creatureType = '';
        $duos = '';
    }
}
$page = max(1, (int)($_GET['page'] ?? 1));
$whereSql = $where ? ' WHERE ' . implode(' AND ', $where) : '';

$heroes = [];
$heroSkins = [];
$pets = [];
$coins = [];
$timewarpedCards = [];
$constructedCards = [];
$libraryCards = [];
$cards = [];
$goldenVariantMap = [];
$wikiMetaMap = [];
$constructedWikiMetaMap = [];
$constructedRelatedCardMap = [];
if ($showHeroSkins) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM hero_skins' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM hero_skins' . $whereSql
        . ' ORDER BY class_name_en IS NULL, class_name_en ASC, name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $heroSkins = $stmt->fetchAll();
    $heroSkins = panel_attach_horizontal_art(
        $pdo,
        $heroSkins,
        'hero_skin',
        static fn(array $row): string => (string)$row['card_id']
    );
} elseif ($showPets) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM hearthstone_pets' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM hearthstone_pets' . $whereSql
        . ' ORDER BY pet_id ASC, level IS NULL, level ASC, variant_id ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $pets = $stmt->fetchAll();
    $pets = panel_attach_horizontal_art(
        $pdo,
        $pets,
        'pet',
        static fn(array $row): string => (string)($row['card_id'] ?: 'variant:' . $row['variant_id'])
    );
} elseif ($showCoins) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM hearthstone_coins' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM hearthstone_coins' . $whereSql
        . ' ORDER BY cosmetic_sort_order IS NULL, cosmetic_sort_order ASC, coin_name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $coins = $stmt->fetchAll();
    $coins = panel_attach_horizontal_art(
        $pdo,
        $coins,
        'coin',
        static fn(array $row): string => (string)$row['card_id']
    );
} elseif ($showHeroes) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM battlegrounds_heroes' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_heroes' . $whereSql
        . ' ORDER BY name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $heroes = $stmt->fetchAll();
    $heroes = panel_attach_horizontal_art(
        $pdo,
        $heroes,
        'hero',
        static fn(array $row): string => (string)$row['card_id']
    );
} elseif ($showTimewarped) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM battlegrounds_timewarped_cards' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_timewarped_cards' . $whereSql
        . ' ORDER BY tavern_tier IS NULL, tavern_tier ASC, card_type ASC, name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $timewarpedCards = $stmt->fetchAll();
    $timewarpedCards = panel_attach_horizontal_art(
        $pdo,
        $timewarpedCards,
        'timewarped_card',
        static fn(array $row): string => (string)$row['card_id']
    );
} elseif ($showConstructed) {
    $fromSql = ' FROM constructed_cards c';
    $countStmt = $pdo->prepare('SELECT COUNT(*)' . $fromSql . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT card.*,
                (SELECT GROUP_CONCAT(DISTINCT active_format.format_slug ORDER BY active_format.format_slug)
                 FROM constructed_format_cards active_format
                 WHERE active_format.card_id = card.card_id AND active_format.in_format = 1) AS formats
            FROM (
                SELECT c.card_id, c.name_ru, c.name_en'
        . $fromSql . $whereSql
        . ' ORDER BY c.name_ru IS NULL, c.name_ru ASC, c.name_en ASC, c.card_id ASC
                LIMIT :limit OFFSET :offset
            ) page
            INNER JOIN constructed_cards card ON card.card_id = page.card_id
            ORDER BY page.name_ru IS NULL, page.name_ru ASC, page.name_en ASC, page.card_id ASC';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $constructedCards = $stmt->fetchAll();
    $constructedCards = panel_attach_horizontal_art(
        $pdo,
        $constructedCards,
        'constructed_card',
        static fn(array $row): string => (string)$row['card_id']
    );
    $constructedWikiMetaMap = load_constructed_wiki_meta_map($pdo, $constructedCards);
    $constructedRelatedCardMap = load_constructed_related_card_map($pdo, $constructedWikiMetaMap);
} elseif ($showLibrary) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM battlegrounds_library_cards' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_library_cards' . $whereSql
        . ' ORDER BY in_pool DESC, sort_order IS NULL, sort_order ASC, name_ru ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $libraryCards = $stmt->fetchAll();
    $libraryCards = panel_attach_horizontal_art(
        $pdo,
        $libraryCards,
        'library_card',
        static fn(array $row): string => (string)$row['library'] . ':' . (string)$row['card_id']
    );
} else {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM battlegrounds_cards' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_cards' . $whereSql
        . ' ORDER BY in_pool DESC, tavern_tier IS NULL, tavern_tier ASC, name ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $cards = $stmt->fetchAll();
    $cards = panel_attach_horizontal_art(
        $pdo,
        $cards,
        'battleground_card',
        static fn(array $row): string => (string)$row['card_id']
    );
    $goldenVariantMap = load_golden_variant_map($pdo, $cards);
    $wikiMetaMap = load_wiki_meta_map($pdo, $cards);
}

$total = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_cards WHERE variant_kind = 'base'")->fetchColumn();
$goldenVariantTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_cards WHERE variant_kind = 'golden'")->fetchColumn();
$heroTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok'")->fetchColumn();
$heroSkinsTotal = (int)$pdo->query("SELECT COUNT(*) FROM hero_skins WHERE status IN ('ok', 'partial')")->fetchColumn();
$heroSkinsAnimatedTotal = (int)$pdo->query("SELECT COUNT(*) FROM hero_skins WHERE status IN ('ok', 'partial') AND animated_image_url IS NOT NULL AND animated_image_url <> ''")->fetchColumn();
$heroSkinsGalleryTotal = (int)$pdo->query("SELECT COUNT(*) FROM hero_skins WHERE status IN ('ok', 'partial') AND COALESCE(JSON_LENGTH(gallery_json), 0) > 0")->fetchColumn();
$heroSkinsSoundsTotal = (int)$pdo->query("SELECT COUNT(*) FROM hero_skins WHERE status IN ('ok', 'partial') AND COALESCE(JSON_LENGTH(sounds_json), 0) > 0")->fetchColumn();
$heroSkinRarityTotals = [];
foreach ($pdo->query("SELECT COALESCE(rarity_slug, 'unknown') AS rarity_slug, COUNT(*) AS total FROM hero_skins WHERE status IN ('ok', 'partial') GROUP BY COALESCE(rarity_slug, 'unknown')") as $row) {
    $heroSkinRarityTotals[(string)$row['rarity_slug']] = (int)$row['total'];
}
$petsTotal = (int)$pdo->query("SELECT COUNT(*) FROM hearthstone_pets WHERE status IN ('ok', 'partial')")->fetchColumn();
$petsGalleryTotal = (int)$pdo->query("SELECT COUNT(*) FROM hearthstone_pets WHERE status IN ('ok', 'partial') AND COALESCE(JSON_LENGTH(gallery_json), 0) > 0")->fetchColumn();
$petsBackgroundTotal = (int)$pdo->query("SELECT COUNT(*) FROM hearthstone_pets WHERE status IN ('ok', 'partial') AND end_screen_background_url IS NOT NULL AND end_screen_background_url <> ''")->fetchColumn();
$petFamiliesTotal = (int)$pdo->query("SELECT COUNT(DISTINCT pet_id) FROM hearthstone_pets WHERE status IN ('ok', 'partial')")->fetchColumn();
$coinsTotal = (int)$pdo->query('SELECT COUNT(*) FROM hearthstone_coins')->fetchColumn();
$coinRelationsRow = $pdo->query('SELECT generated_by_card_ids_json, related_card_ids_json FROM hearthstone_coins ORDER BY cosmetic_sort_order ASC LIMIT 1')->fetch();
$coinGeneratedByTotal = $coinRelationsRow ? count(json_array($coinRelationsRow['generated_by_card_ids_json'] ?? null)) : 0;
$coinRelatedTotal = $coinRelationsRow ? count(json_array($coinRelationsRow['related_card_ids_json'] ?? null)) : 0;
$timewarpedTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_timewarped_cards WHERE status = 'ok'")->fetchColumn();
$constructedTotal = (int)$pdo->query('SELECT COUNT(*) FROM constructed_cards')->fetchColumn();
$constructedStandardTotal = (int)$pdo->query("SELECT COUNT(*) FROM constructed_format_cards WHERE format_slug = 'standard' AND in_format = 1")->fetchColumn();
$constructedWildTotal = (int)$pdo->query("SELECT COUNT(*) FROM constructed_format_cards WHERE format_slug = 'wild' AND in_format = 1")->fetchColumn();
$constructedWikiTotal = (int)$pdo->query("SELECT COUNT(*) FROM constructed_card_wiki_meta WHERE status = 'ok'")->fetchColumn();
$constructedDiamondTotal = (int)$pdo->query("SELECT COUNT(*) FROM constructed_cards WHERE image_diamond_url IS NOT NULL AND image_diamond_url <> ''")->fetchColumn();
$constructedAnimatedDiamondTotal = (int)$pdo->query("SELECT COUNT(*) FROM constructed_cards WHERE animated_diamond_url IS NOT NULL AND animated_diamond_url <> ''")->fetchColumn();
$libraryTotal = (int)$pdo->query('SELECT COUNT(*) FROM battlegrounds_library_cards')->fetchColumn();
$listingTotal = $showHeroSkins ? $heroSkinsTotal : ($showPets ? $petsTotal : ($showCoins ? $coinsTotal : ($showHeroes ? $heroTotal : ($showTimewarped ? $timewarpedTotal : ($showConstructed ? ($constructedFormat === 'standard' ? $constructedStandardTotal : ($constructedFormat === 'wild' ? $constructedWildTotal : $constructedTotal)) : ($showLibrary ? (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_library_cards WHERE library = " . $pdo->quote($libraryType))->fetchColumn() : $total))))));
$inPoolTotal = (int)$pdo->query('SELECT COUNT(*) FROM battlegrounds_cards WHERE in_pool = 1')->fetchColumn();
$duosOnlyTotal = (int)$pdo->query('SELECT COUNT(*) FROM battlegrounds_cards WHERE duos_only = 1')->fetchColumn();
$wikiMetaTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_card_wiki_meta WHERE status = 'ok'")->fetchColumn();
$wikiMinionTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_cards WHERE card_type = 'minion'")->fetchColumn();
$heroBuddyTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok' AND buddy_dbf IS NOT NULL")->fetchColumn();
$heroPowerGalleryTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok' AND COALESCE(JSON_LENGTH(JSON_EXTRACT(hero_power_json, '$.gallery')), 0) > 0")->fetchColumn();
$buddyGalleryTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok' AND buddy_dbf IS NOT NULL AND COALESCE(JSON_LENGTH(JSON_EXTRACT(buddy_json, '$.gallery')), 0) > 0")->fetchColumn();
$buddySoundsTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok' AND buddy_dbf IS NOT NULL AND COALESCE(JSON_LENGTH(JSON_EXTRACT(buddy_json, '$.sounds')), 0) > 0")->fetchColumn();
$heroWikiErrorTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok' AND (JSON_EXTRACT(hero_power_json, '$.wiki_fetch_error') IS NOT NULL OR JSON_EXTRACT(buddy_json, '$.wiki_fetch_error') IS NOT NULL)")->fetchColumn();
$pageFrom = $filteredTotal === 0 ? 0 : $offset + 1;
$pageTo = min($offset + ($showHeroSkins ? count($heroSkins) : ($showPets ? count($pets) : ($showCoins ? count($coins) : ($showHeroes ? count($heroes) : ($showTimewarped ? count($timewarpedCards) : ($showConstructed ? count($constructedCards) : ($showLibrary ? count($libraryCards) : count($cards)))))))), $filteredTotal);
$pageWindowStart = max(1, $page - 2);
$pageWindowEnd = min($totalPages, $page + 2);
$showAnalyticsDashboard = $action === 'analytics';
$showApiTokens = $action === 'api_tokens';
$showParserControl = $action === 'parsers';
$resetUrl = $cardType !== '' ? '/?card_type=' . rawurlencode($cardType) : '/';
$mediaLabels = [
    'has_buddy' => 'С компаньоном',
    'no_buddy' => 'Без компаньона',
    'hero_power_art' => 'Есть арт силы',
    'buddy_art' => 'Есть арт компаньона',
    'buddy_sounds' => 'Есть звуки компаньона',
    'wiki_error' => 'Ошибки wiki',
];
$skinMediaLabels = [
    'animated' => 'Есть Animated',
    'gallery' => 'Есть Gallery',
    'sounds' => 'Есть Sounds',
    'partial' => 'Частично разобрано',
];
$skinRarityLabels = [
    'basic' => 'Базовый',
    'lite' => 'Lite',
    'full' => 'Full',
    'diamond' => 'Diamond',
    'legendary' => 'Легендарный',
    'mythic' => 'Мифический',
    'unknown' => 'Не указана',
];
$activeFilters = [];
if ($q !== '') {
    $activeFilters[] = ['label' => 'Поиск: ' . $q, 'href' => query_url(['q' => null, 'page' => null])];
}
if ($tier !== '') {
    $activeFilters[] = ['label' => ($showLibrary && $libraryType === 'darkmoon_prize' ? 'Тир ' : 'Таверна ') . $tier, 'href' => query_url(['tier' => null, 'page' => null])];
}
if ($creatureType !== '') {
    $activeFilters[] = ['label' => creature_type_label($creatureType), 'href' => query_url(['creature_type' => null, 'page' => null])];
}
if ($pool !== '') {
    $activeFilters[] = ['label' => $pool === '1' ? 'В пуле' : 'Не в пуле', 'href' => query_url(['pool' => null, 'page' => null])];
}
if ($duos !== '') {
    $activeFilters[] = ['label' => $duos === '1' ? 'Только дуо' : 'Не только дуо', 'href' => query_url(['duos' => null, 'page' => null])];
}
if ($showHeroes && $media !== '') {
    $activeFilters[] = ['label' => $mediaLabels[$media] ?? $media, 'href' => query_url(['media' => null, 'page' => null])];
}
if ($showHeroSkins && $media !== '') {
    $activeFilters[] = ['label' => $skinMediaLabels[$media] ?? $media, 'href' => query_url(['media' => null, 'page' => null])];
}
if ($showHeroSkins && $skinRarity !== '') {
    $activeFilters[] = ['label' => 'Редкость: ' . ($skinRarityLabels[$skinRarity] ?? $skinRarity), 'href' => query_url(['rarity' => null, 'page' => null])];
}
if ($showPets && $media !== '') {
    $petMediaLabels = ['gallery' => 'Есть Gallery', 'background' => 'Есть End screen'];
    $activeFilters[] = ['label' => $petMediaLabels[$media] ?? $media, 'href' => query_url(['media' => null, 'page' => null])];
}
if ($showConstructed && $constructedFormat !== 'all') {
    $activeFilters[] = ['label' => constructed_format_label($constructedFormat), 'href' => query_url(['constructed_format' => null, 'page' => null])];
}
if ($showConstructed && $media !== '') {
    $constructedMediaLabels = [
        'golden' => 'Есть Golden',
        'signature' => 'Есть Signature',
        'diamond' => 'Есть Diamond',
        'animated_diamond' => 'Есть Animated Diamond',
    ];
    $activeFilters[] = ['label' => $constructedMediaLabels[$media] ?? $media, 'href' => query_url(['media' => null, 'page' => null])];
}
$form = $editCard ?: [
    'id' => '',
    'name' => '',
    'name_en' => '',
    'card_id' => '',
    'dbf' => '',
    'card_type' => 'minion',
    'tavern_tier' => '',
    'creature_type' => '',
    'attack' => '',
    'health' => '',
    'in_pool' => 1,
    'duos_only' => 0,
    'card_image' => '',
    'golden_image' => '',
    'art_image' => '',
    'framed_image' => '',
    'notes' => '',
];
$workspaceTitle = $action === 'parsers'
    ? 'Парсеры'
    : ($action === 'api_tokens'
    ? 'API-токены'
    : ($action === 'analytics'
        ? 'Обзор и мета'
    : ($action === 'wiki_terms'
        ? 'Переводы Wiki'
        : ($action === 'new'
            ? 'Новая карта'
            : ($editCard
                ? 'Редактировать карту'
                : ($showHeroSkins ? 'Скины героев' : ($showPets ? 'Питомцы' : ($showCoins ? 'Монетки' : ($showHeroes ? 'Герои' : ($showTimewarped ? 'Хрономальные карты' : ($showConstructed ? 'Стандартные и вольные карты' : ($showLibrary ? library_type_label($libraryType) : 'Карты Полей сражений'))))))))))));
$workspaceSection = $showApiTokens
    ? 'Доступ'
    : ($showParserControl
        ? 'Операции'
        : ($showAnalyticsDashboard ? 'Аналитика' : 'База данных'));
?>
<!doctype html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex,nofollow">
    <title>HS Data · Управление базой Hearthstone</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%232563eb'/%3E%3Ctext x='32' y='40' text-anchor='middle' font-family='system-ui,sans-serif' font-size='25' font-weight='800' fill='white'%3EHS%3C/text%3E%3C/svg%3E">
    <link rel="stylesheet" href="/assets/style.css?v=34">
    <script src="/assets/panel-ui.js?v=2" defer></script>
    <script src="/assets/parsing-reliability.js?v=7" defer></script>
    <script src="/assets/analytics.js?v=11" defer></script>
    <script src="/assets/parser-control-view.js?v=2" defer></script>
    <script src="/assets/parser-control.js?v=2" defer></script>
</head>
<body data-page="<?= h($action) ?>">
<main class="shell">
    <aside class="sidebar" aria-label="Навигация по базе">
        <div class="sidebar-brand">
            <span class="brand-mark" aria-hidden="true">
                <svg viewBox="0 0 24 24" focusable="false">
                    <path d="M12 2.4a9.6 9.6 0 1 0 9.6 9.6c0-2.9-1.3-5.6-3.4-7.4.4 1.6-.1 3.3-1.3 4.4-1.2 1.1-3 1.3-4.4.6-1.2-.6-1.9-1.9-1.7-3.2.2-1 1-1.8 2-2-2.6-.2-5 1.3-6 3.7-1 2.4-.3 5.2 1.8 6.8 1.8 1.4 4.4 1.4 6.2 0 1.3-1 1.9-2.7 1.4-4.3.8 1.2.8 2.9 0 4.1-1.2 1.9-3.8 2.6-5.8 1.5-1.2-.6-2-1.7-2.3-3 1.1 1.1 2.8 1.4 4.2.7 1-.5 1.6-1.5 1.5-2.6-.1-.8-.6-1.5-1.3-1.8.4.8.1 1.8-.6 2.2-.9.5-2 .1-2.5-.8-.6-1.1-.3-2.5.7-3.3 1.2-1 3-1 4.2-.1 1.7 1.2 2.3 3.4 1.5 5.3-.9 2.3-3.5 3.5-5.8 2.7-2.8-.9-4.3-4-3.4-6.8C7.4 5.3 9.5 3.2 12 2.4Z"/>
                </svg>
            </span>
            <div>
                <strong>HS Data</strong>
                <p>центр управления данными</p>
            </div>
            <button class="sidebar-toggle" type="button" aria-controls="sidebarNav" aria-expanded="false" data-sidebar-toggle>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
                <span>Меню</span>
            </button>
        </div>

        <nav class="side-nav" id="sidebarNav">
            <section class="side-section">
                <h2>Основное</h2>
                <a class="side-link<?= !$cardType && $action === 'list' ? ' active' : '' ?>" href="/">
                    <span>Карты BG</span><b><?= $total ?></b>
                </a>
                <a class="side-link<?= $showHeroes ? ' active' : '' ?>" href="/?card_type=hero">
                    <span>Герои</span><b><?= $heroTotal ?></b>
                </a>
                <a class="side-link<?= $showHeroSkins ? ' active' : '' ?>" href="/?card_type=hero_skin">
                    <span>Скины героев</span><b><?= $heroSkinsTotal ?></b>
                </a>
                <a class="side-link<?= $showPets ? ' active' : '' ?>" href="/?card_type=pet">
                    <span>Питомцы</span><b><?= $petsTotal ?></b>
                </a>
                <a class="side-link<?= $showCoins ? ' active' : '' ?>" href="/?card_type=coin">
                    <span>Монетки</span><b><?= $coinsTotal ?></b>
                </a>
            </section>

            <section class="side-section">
                <h2>Статистика</h2>
                <a class="side-link<?= $action === 'analytics' ? ' active' : '' ?>" href="/?action=analytics#statistics">
                    <span>Обзор и мета</span><b>Live</b>
                </a>
            </section>

            <section class="side-section">
                <h2>Операции</h2>
                <a class="side-link<?= $showParserControl ? ' active' : '' ?>" href="/?action=parsers">
                    <span>Парсеры</span><b>Live</b>
                </a>
            </section>

            <section class="side-section">
                <h2>Доступ</h2>
                <a class="side-link<?= $showApiTokens ? ' active' : '' ?>" href="/?action=api_tokens">
                    <span>API-токены</span><b>v1</b>
                </a>
            </section>

            <section class="side-section">
                <h2>Коллекции</h2>
                <a class="side-link<?= $showTimewarped ? ' active' : '' ?>" href="/?card_type=timewarped">
                    <span>Хрономальные</span><b><?= $timewarpedTotal ?></b>
                </a>
                <a class="side-link<?= $showConstructed ? ' active' : '' ?>" href="/?card_type=constructed">
                    <span>Стандарт / Вольный</span><b><?= $constructedTotal ?></b>
                </a>
                <a class="side-link<?= $cardType === 'anomaly' ? ' active' : '' ?>" href="/?card_type=anomaly">
                    <span>Аномалии</span>
                </a>
                <a class="side-link<?= $cardType === 'quest' ? ' active' : '' ?>" href="/?card_type=quest">
                    <span>Квесты</span>
                </a>
                <a class="side-link<?= $cardType === 'darkmoon_prize' ? ' active' : '' ?>" href="/?card_type=darkmoon_prize">
                    <span>Призы</span>
                </a>
                <a class="side-link<?= $cardType === 'reward' ? ' active' : '' ?>" href="/?card_type=reward">
                    <span>Награды</span>
                </a>
                <a class="side-link<?= $cardType === 'trinket' ? ' active' : '' ?>" href="/?card_type=trinket">
                    <span>Аксессуары</span>
                </a>
            </section>

        </nav>

        <div class="sidebar-footer">
            <div class="theme-switcher" aria-label="Тема панели">
                <button class="theme-button" type="button" data-theme-option="light">Светлая</button>
                <button class="theme-button" type="button" data-theme-option="dark">Темная</button>
                <button class="theme-button" type="button" data-theme-option="tavern">Таверна</button>
                <button class="theme-button" type="button" data-theme-option="arcane">Аркана</button>
            </div>
        </div>
    </aside>

    <section class="workspace">
        <header class="topbar">
            <div class="topbar-copy">
                <span class="topbar-context"><?= h($workspaceSection) ?></span>
                <div>
                    <h1><?= h($workspaceTitle) ?></h1>
                    <?php if ($action === 'list'): ?>
                        <span class="result-range"><?= $pageFrom ?>–<?= $pageTo ?> из <?= $filteredTotal ?></span>
                    <?php endif; ?>
                </div>
            </div>
            <div class="topbar-actions">
                <button class="topbar-command" type="button" data-command-open aria-haspopup="dialog" aria-keyshortcuts="Control+K Meta+K">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20 20-4.3-4.3m2.3-5.2a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z"/></svg>
                    <span>Быстрый переход</span>
                    <kbd>⌘ K</kbd>
                </button>
                <div class="panel-account" aria-label="Аккаунт администратора">
                    <span class="panel-account-name"><i aria-hidden="true"></i>GitHub · <?= h($panelUser['login']) ?></span>
                    <form action="/auth/logout" method="post">
                        <input type="hidden" name="csrf" value="<?= h(panel_logout_csrf_token()) ?>">
                        <button class="panel-logout" type="submit">Выйти</button>
                    </form>
                </div>
            </div>
        </header>

    <?php if ($message): ?><div class="notice"><?= h($message) ?></div><?php endif; ?>
    <?php if ($error): ?><div class="notice error"><?= h($error) ?></div><?php endif; ?>

    <?php if ($showAnalyticsDashboard): ?>
        <?php require __DIR__ . '/partials/analytics-dashboard.php'; ?>
    <?php endif; ?>

    <?php if ($showParserControl): ?>
        <?php require __DIR__ . '/partials/parser-control.php'; ?>
    <?php endif; ?>

    <?php if ($showApiTokens): ?>
        <?php require __DIR__ . '/partials/api-token-manager.php'; ?>
    <?php endif; ?>

    <?php if ($action === 'wiki_terms'): ?>
        <?php
        $wikiTermGroups = wiki_term_groups($pdo);
        $wikiTermLabels = wiki_term_type_labels();
        $wikiTermStats = [];
        $wikiTermTotal = 0;
        $wikiTermMissingTotal = 0;
        foreach ($wikiTermGroups as $termType => $termRows) {
            $missing = 0;
            foreach ($termRows as $termRow) {
                if (trim((string)($termRow['term_ru'] ?? '')) === '') {
                    $missing++;
                }
            }
            $count = count($termRows);
            $wikiTermTotal += $count;
            $wikiTermMissingTotal += $missing;
            $wikiTermStats[$termType] = [
                'label' => $wikiTermLabels[$termType] ?? $termType,
                'total' => $count,
                'missing' => $missing,
                'done' => $count - $missing,
            ];
        }
        ?>
        <section class="panel terms-panel" data-terms-page>
            <div class="list-head">
                <div>
                    <h2>Переводы Wiki</h2>
                    <p class="muted">Заполните русские названия для английских Wiki mechanics, Wiki tags и Full tags. API сразу отдаст mechanics/tags в локализованных полях.</p>
                </div>
                <a class="button secondary" href="<?= h(query_url(['action' => null])) ?>">Назад к базе</a>
            </div>
            <div class="terms-summary-grid" aria-label="Покрытие переводов Wiki">
                <div class="term-stat is-total">
                    <span>Всего терминов</span>
                    <b><?= $wikiTermTotal ?></b>
                </div>
                <div class="term-stat<?= $wikiTermMissingTotal > 0 ? ' is-warn' : ' is-ok' ?>">
                    <span>Без перевода</span>
                    <b><?= $wikiTermMissingTotal ?></b>
                </div>
                <?php foreach ($wikiTermStats as $stat): ?>
                    <div class="term-stat">
                        <span><?= h($stat['label']) ?></span>
                        <b><?= $stat['done'] ?>/<?= $stat['total'] ?></b>
                    </div>
                <?php endforeach; ?>
            </div>
            <div class="terms-toolbar">
                <label class="term-search">
                    <span>Поиск термина</span>
                    <input type="search" placeholder="Battlecry, Race, BG..." data-term-filter autocomplete="off">
                </label>
                <div class="term-status-filter" aria-label="Фильтр переводов">
                    <button class="button ghost active" type="button" data-term-status="all">Все</button>
                    <button class="button ghost" type="button" data-term-status="missing">Без перевода</button>
                    <button class="button ghost" type="button" data-term-status="translated">Переведенные</button>
                </div>
            </div>
            <form method="post" class="terms-form">
                <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
                <input type="hidden" name="action" value="save_wiki_terms">
                <div class="term-grid">
                    <?php foreach ($wikiTermLabels as $termType => $termTitle): ?>
                        <section class="term-column" data-term-section="<?= h($termType) ?>">
                            <div class="term-column-head">
                                <h3><?= h($termTitle) ?></h3>
                                <?php $stat = $wikiTermStats[$termType] ?? ['done' => 0, 'total' => 0, 'missing' => 0]; ?>
                                <span><?= (int)$stat['done'] ?>/<?= (int)$stat['total'] ?> готово<?php if ((int)$stat['missing'] > 0): ?> · <?= (int)$stat['missing'] ?> пусто<?php endif; ?></span>
                            </div>
                            <?php if (!empty($wikiTermGroups[$termType])): ?>
                                <?php foreach ($wikiTermGroups[$termType] as $termIndex => $termRow): ?>
                                    <?php
                                    $termRu = trim((string)($termRow['term_ru'] ?? ''));
                                    $termStatus = $termRu === '' ? 'missing' : 'translated';
                                    $termSearchText = mb_strtolower(($termRow['term_en'] ?? '') . ' ' . $termRu . ' ' . $termTitle, 'UTF-8');
                                    ?>
                                    <label class="term-row" data-term-row data-term-status="<?= h($termStatus) ?>" data-term-text="<?= h($termSearchText) ?>">
                                        <span class="term-source">
                                            <code><?= h($termRow['term_en']) ?></code>
                                            <small><?= h($termTitle) ?></small>
                                        </span>
                                        <input type="hidden" name="terms[<?= h($termType) ?>][<?= (int)$termIndex ?>][en]" value="<?= h($termRow['term_en']) ?>">
                                        <input
                                            name="terms[<?= h($termType) ?>][<?= (int)$termIndex ?>][ru]"
                                            value="<?= h($termRu) ?>"
                                            placeholder="Русский перевод"
                                        >
                                    </label>
                                <?php endforeach; ?>
                            <?php else: ?>
                                <p class="muted">Пока нет терминов этого типа.</p>
                            <?php endif; ?>
                        </section>
                    <?php endforeach; ?>
                </div>
                <div class="actions">
                    <button class="button" type="submit">Сохранить переводы</button>
                </div>
            </form>
        </section>
    <?php endif; ?>

    <?php if (in_array($action, ['new', 'edit'], true) && !$showHeroes && !$showHeroSkins && !$showPets && !$showCoins && !$showTimewarped && !$showConstructed && !$showLibrary): ?>
    <section class="panel entry-panel<?= $editCard ? ' is-editing' : '' ?>" id="add-card">
        <div class="entry-head">
            <div>
                <h2><?= $editCard ? 'Редактировать карту' : 'Добавить карту' ?></h2>
                <p class="muted"><?= $editCard ? 'Измените данные и сохраните запись.' : 'Заполните основные поля новой записи.' ?></p>
            </div>
            <a class="button ghost" href="/">Вернуться к таблице</a>
        </div>
        <form method="post" enctype="multipart/form-data" class="card-form">
            <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
            <input type="hidden" name="action" value="save">
            <input type="hidden" name="id" value="<?= h($form['id']) ?>">

            <label>Название карты
                <input name="name" value="<?= h($form['name']) ?>" required>
            </label>
            <label>Название EN
                <input name="name_en" value="<?= h($form['name_en']) ?>">
            </label>
            <label>card_id
                <input name="card_id" value="<?= h($form['card_id']) ?>" required placeholder="BG30_123">
            </label>
            <label>dbf
                <input name="dbf" type="number" min="0" value="<?= h($form['dbf']) ?>">
            </label>
            <label>Тип карты
                <select name="card_type">
                    <?php foreach (card_types() as $value => $label): ?>
                        <option value="<?= h($value) ?>"<?= (string)($form['card_type'] ?? 'minion') === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                    <?php endforeach; ?>
                </select>
            </label>
            <label>Уровень таверны
                <input name="tavern_tier" type="number" min="1" max="7" value="<?= h($form['tavern_tier']) ?>">
            </label>
            <label>Тип существа
                <select name="creature_type">
                    <option value="">Без типа</option>
                    <?php foreach (creature_types() as $value => $label): ?>
                        <option value="<?= h($value) ?>"<?= (string)$form['creature_type'] === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                    <?php endforeach; ?>
                </select>
            </label>
            <label>Атака
                <input name="attack" type="number" value="<?= h($form['attack']) ?>">
            </label>
            <label>Здоровье
                <input name="health" type="number" value="<?= h($form['health']) ?>">
            </label>
            <label>Пул
                <span class="checkbox-line">
                    <input name="in_pool" type="checkbox" value="1"<?= !empty($form['in_pool']) ? ' checked' : '' ?>>
                    В текущем пуле
                </span>
            </label>
            <label>Дуо
                <span class="checkbox-line">
                    <input name="duos_only" type="checkbox" value="1"<?= !empty($form['duos_only']) ? ' checked' : '' ?>>
                    Только в дуо
                </span>
            </label>
            <label>Картинка карты
                <input name="card_image_file" type="file" accept="image/png,image/jpeg,image/webp">
            </label>
            <label>Золотая / триплет
                <input name="golden_image_file" type="file" accept="image/png,image/jpeg,image/webp">
            </label>
            <label>Арт карты
                <input name="art_image_file" type="file" accept="image/png,image/jpeg,image/webp">
            </label>
            <label>Арт в рамке
                <input name="framed_image_file" type="file" accept="image/png,image/jpeg,image/webp">
            </label>
            <label class="wide">Заметки
                <textarea name="notes" rows="3"><?= h($form['notes']) ?></textarea>
            </label>

            <div class="preview-row wide">
                <?php if (!empty($form['card_image'])): ?>
                    <figure><img src="<?= h(versioned_asset($form['card_image'], $form['updated_at'] ?? null)) ?>" alt=""><figcaption>Карта</figcaption></figure>
                <?php endif; ?>
                <?php if (!empty($form['golden_image'])): ?>
                    <figure><img src="<?= h(versioned_asset($form['golden_image'], $form['updated_at'] ?? null)) ?>" alt=""><figcaption>Золотая / триплет</figcaption></figure>
                <?php endif; ?>
                <?php if (!empty($form['art_image'])): ?>
                    <figure><img src="<?= h(versioned_asset($form['art_image'], $form['updated_at'] ?? null)) ?>" alt=""><figcaption>Арт</figcaption></figure>
                <?php endif; ?>
                <?php if (!empty($form['framed_image'])): ?>
                    <figure><img src="<?= h(versioned_asset($form['framed_image'], $form['updated_at'] ?? null)) ?>" alt=""><figcaption>Арт в рамке</figcaption></figure>
                <?php endif; ?>
            </div>

            <div class="wide actions">
                <button class="button" type="submit"><?= $editCard ? 'Сохранить' : 'Добавить' ?></button>
                <a class="button secondary" href="/">Отмена</a>
            </div>
        </form>
    </section>
    <?php endif; ?>

    <?php if ($action === 'list'): ?>
    <section class="panel data-panel" id="database-catalogue">
        <div class="list-head">
            <form class="filters" method="get" data-autofilter>
                <label class="filter-search">
                    <span>Поиск</span>
                    <span class="search-field">
                        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20 20-4.3-4.3m2.3-5.2a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z"/></svg>
                        <input type="search" name="q" value="<?= h($q) ?>" placeholder="<?= $showHeroSkins ? 'Скин, character, actor, class, category' : ($showPets ? 'Питомец, вариант, id, dbf' : ($showCoins ? 'Coin, RU, id, dbf, художник' : ($showHeroes ? 'Герой, сила, компаньон, художник' : 'Название, ID, DBF, текст или механика'))) ?>" autocomplete="off" data-filter-search aria-keyshortcuts="/">
                        <kbd aria-hidden="true">/</kbd>
                    </span>
                </label>
                <div class="filter-controls">
                <button class="filter-toggle" type="button" aria-expanded="false" data-filter-toggle>
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16l-6 7v5l-4 2v-7L4 5Z"/></svg>
                    <span>Фильтры</span>
                    <?php if ($activeFilters): ?><b><?= count($activeFilters) ?></b><?php endif; ?>
                </button>
                <select name="card_type" aria-label="Раздел базы">
                    <option value="">Все карты</option>
                    <?php foreach (filter_card_types() as $value => $label): ?>
                        <option value="<?= h($value) ?>"<?= $cardType === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                    <?php endforeach; ?>
                </select>
                <?php if (!$showHeroes && !$showHeroSkins && !$showCoins && !$showConstructed && (!$showLibrary || $libraryType === 'darkmoon_prize')): ?>
                    <select name="tier" aria-label="Уровень таверны">
                        <option value=""><?= $showPets ? 'Все уровни питомца' : ($showLibrary && $libraryType === 'darkmoon_prize' ? 'Все тиры' : 'Все уровни') ?></option>
                        <?php for ($i = 1; $i <= (($showLibrary && $libraryType === 'darkmoon_prize') || $showPets ? 4 : 7); $i++): ?>
                            <option value="<?= $i ?>"<?= $tier === (string)$i ? ' selected' : '' ?>><?= $showPets ? 'Уровень ' : ($showLibrary && $libraryType === 'darkmoon_prize' ? 'Тир ' : 'Таверна ') ?><?= $i ?></option>
                        <?php endfor; ?>
                    </select>
                <?php endif; ?>
                <?php if (!$showHeroes && !$showHeroSkins && !$showPets && !$showCoins && !$showConstructed && !$showLibrary): ?>
                    <select name="creature_type" aria-label="Тип существа">
                        <option value="">Все типы</option>
                        <?php foreach (creature_types() as $value => $label): ?>
                            <option value="<?= h($value) ?>"<?= $creatureType === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                        <?php endforeach; ?>
                    </select>
                <?php endif; ?>
                <?php if ($showConstructed): ?>
                    <select name="constructed_format" aria-label="Формат игры">
                        <option value="all"<?= $constructedFormat === 'all' ? ' selected' : '' ?>>Стандарт + Вольный</option>
                        <option value="standard"<?= $constructedFormat === 'standard' ? ' selected' : '' ?>>Стандартный</option>
                        <option value="wild"<?= $constructedFormat === 'wild' ? ' selected' : '' ?>>Вольный</option>
                    </select>
                    <select name="media" aria-label="Медиа и качество">
                        <option value="">Все качества</option>
                        <option value="golden"<?= $media === 'golden' ? ' selected' : '' ?>>Есть Golden</option>
                        <option value="signature"<?= $media === 'signature' ? ' selected' : '' ?>>Есть Signature</option>
                        <option value="diamond"<?= $media === 'diamond' ? ' selected' : '' ?>>Есть Diamond</option>
                        <option value="animated_diamond"<?= $media === 'animated_diamond' ? ' selected' : '' ?>>Есть Animated Diamond</option>
                    </select>
                <?php endif; ?>
                <?php if ($showHeroes): ?>
                    <select name="media" aria-label="Медиа и качество">
                        <option value="">Все герои</option>
                        <?php foreach ($mediaLabels as $value => $label): ?>
                            <option value="<?= h($value) ?>"<?= $media === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                        <?php endforeach; ?>
                    </select>
                <?php endif; ?>
                <?php if ($showHeroSkins): ?>
                    <select name="rarity" aria-label="Редкость скина">
                        <option value="">Любая редкость</option>
                        <?php foreach ($skinRarityLabels as $value => $label): ?>
                            <option value="<?= h($value) ?>"<?= $skinRarity === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                        <?php endforeach; ?>
                    </select>
                    <select name="media" aria-label="Медиа и качество">
                        <option value="">Все скины</option>
                        <?php foreach ($skinMediaLabels as $value => $label): ?>
                            <option value="<?= h($value) ?>"<?= $media === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                        <?php endforeach; ?>
                    </select>
                <?php endif; ?>
                <?php if ($showPets): ?>
                    <select name="media" aria-label="Медиа и качество">
                        <option value="">Все питомцы</option>
                        <option value="background"<?= $media === 'background' ? ' selected' : '' ?>>Есть end screen</option>
                        <option value="gallery"<?= $media === 'gallery' ? ' selected' : '' ?>>Есть Gallery</option>
                    </select>
                <?php endif; ?>
                <?php if (!$showHeroes && !$showHeroSkins && !$showPets && !$showCoins && !$showTimewarped && !$showConstructed): ?>
                    <select name="pool" aria-label="Статус в пуле">
                        <option value="">Любой пул</option>
                        <option value="1"<?= $pool === '1' ? ' selected' : '' ?>>В пуле</option>
                        <option value="0"<?= $pool === '0' ? ' selected' : '' ?>>Не в пуле</option>
                    </select>
                <?php endif; ?>
                <?php if (!$showHeroes && !$showHeroSkins && !$showPets && !$showCoins && !$showTimewarped && !$showConstructed && !$showLibrary): ?>
                    <select name="duos" aria-label="Режим игры">
                        <option value="">Любой режим</option>
                        <option value="1"<?= $duos === '1' ? ' selected' : '' ?>>Только дуо</option>
                        <option value="0"<?= $duos === '0' ? ' selected' : '' ?>>Не только дуо</option>
                    </select>
                <?php endif; ?>
                <select name="per_page" aria-label="Карт на странице">
                    <option value="25"<?= $perPage === 25 ? ' selected' : '' ?>>25 на странице</option>
                    <option value="50"<?= $perPage === 50 ? ' selected' : '' ?>>50 на странице</option>
                    <option value="100"<?= $perPage === 100 ? ' selected' : '' ?>>100 на странице</option>
                    <option value="150"<?= $perPage === 150 ? ' selected' : '' ?>>150 на странице</option>
                </select>
                <button class="button" type="submit">Найти</button>
                <a class="button ghost" href="<?= h($resetUrl) ?>">Сброс</a>
                <button class="table-density-toggle" type="button" data-table-density aria-pressed="false">Компактно</button>
                <details class="table-column-picker" data-column-picker data-table-target=".cards-table > table" data-storage-key="catalogue-<?= h($cardType !== '' ? $cardType : 'battlegrounds') ?>">
                    <summary>Колонки</summary>
                    <div class="column-picker-menu" data-column-picker-menu></div>
                </details>
                </div>
            </form>
            <?php if ($activeFilters): ?>
                <div class="active-filters" aria-label="Активные фильтры">
                    <?php foreach ($activeFilters as $filter): ?>
                        <a href="<?= h($filter['href']) ?>"><?= h($filter['label']) ?><span aria-hidden="true">×</span></a>
                    <?php endforeach; ?>
                </div>
            <?php endif; ?>
        </div>

        <?php if ($showHeroes): ?>
            <div class="hero-coverage-strip" aria-label="Покрытие медиа героев">
                <span>Силы героя с артами <b><?= $heroPowerGalleryTotal ?>/<?= $heroTotal ?></b></span>
                <span>Компаньоны с артами <b><?= $buddyGalleryTotal ?>/<?= $heroBuddyTotal ?></b></span>
                <span>Компаньоны со звуками <b><?= $buddySoundsTotal ?>/<?= $heroBuddyTotal ?></b></span>
                <span class="<?= $heroWikiErrorTotal > 0 ? 'is-warn' : 'is-ok' ?>">Ошибки wiki <b><?= $heroWikiErrorTotal ?></b></span>
            </div>
        <?php endif; ?>

        <?php if ($showHeroSkins): ?>
            <div class="hero-coverage-strip" aria-label="Покрытие скинов героев">
                <span>Всего скинов <b><?= $heroSkinsTotal ?></b></span>
                <span>Animated <b><?= $heroSkinsAnimatedTotal ?>/<?= $heroSkinsTotal ?></b></span>
                <span>Gallery <b><?= $heroSkinsGalleryTotal ?>/<?= $heroSkinsTotal ?></b></span>
                <span>Sounds <b><?= $heroSkinsSoundsTotal ?>/<?= $heroSkinsTotal ?></b></span>
                <?php foreach ($skinRarityLabels as $slug => $label): ?>
                    <?php if (($heroSkinRarityTotals[$slug] ?? 0) > 0): ?>
                        <span><?= h($label) ?> <b><?= $heroSkinRarityTotals[$slug] ?></b></span>
                    <?php endif; ?>
                <?php endforeach; ?>
            </div>
        <?php endif; ?>

        <?php if ($showPets): ?>
            <div class="hero-coverage-strip" aria-label="Покрытие питомцев">
                <span>Питомцев <b><?= $petFamiliesTotal ?></b></span>
                <span>Вариантов <b><?= $petsTotal ?></b></span>
                <span>End screen <b><?= $petsBackgroundTotal ?>/<?= $petsTotal ?></b></span>
                <span>Gallery <b><?= $petsGalleryTotal ?>/<?= $petsTotal ?></b></span>
            </div>
        <?php endif; ?>

        <?php if ($showCoins): ?>
            <div class="hero-coverage-strip" aria-label="Покрытие монеток">
                <span>Cosmetic Coins <b><?= $coinsTotal ?></b></span>
                <span>Generated by <b><?= $coinGeneratedByTotal ?></b></span>
                <span>Related with <b><?= $coinRelatedTotal ?></b></span>
            </div>
        <?php endif; ?>

        <?php if ($showConstructed): ?>
            <div class="hero-coverage-strip" aria-label="Покрытие Standard/Wild">
                <span>Стандартный <b><?= $constructedStandardTotal ?></b></span>
                <span>Вольный <b><?= $constructedWildTotal ?></b></span>
                <span>Wiki готово <b><?= $constructedWikiTotal ?>/<?= $constructedTotal ?></b></span>
                <span>Diamond <b><?= $constructedDiamondTotal ?></b></span>
                <span>Animated Diamond <b><?= $constructedAnimatedDiamondTotal ?></b></span>
                <span>Формат <b><?= h(constructed_format_label($constructedFormat)) ?></b></span>
            </div>
        <?php endif; ?>

        <?php if ($filteredTotal === 0): ?>
            <section class="catalog-empty" role="status" aria-labelledby="catalogEmptyTitle">
                <span class="catalog-empty-icon" aria-hidden="true">
                    <svg viewBox="0 0 24 24"><path d="m20 20-4.3-4.3m2.3-5.2a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z"/></svg>
                </span>
                <div>
                    <h2 id="catalogEmptyTitle"><?= $activeFilters || $q !== '' ? 'По этим условиям ничего не найдено' : 'В разделе пока нет данных' ?></h2>
                    <p><?= $activeFilters || $q !== '' ? 'Сбросьте часть фильтров или измените поисковый запрос.' : 'Проверьте источник и последний успешный запуск в операционном центре.' ?></p>
                </div>
                <?php if ($activeFilters || $q !== ''): ?>
                    <a class="button secondary" href="<?= h($resetUrl) ?>">Сбросить фильтры</a>
                <?php else: ?>
                    <a class="button secondary" href="/?action=parsers">Проверить парсеры</a>
                <?php endif; ?>
            </section>
        <?php else: ?>
        <?php if ($totalPages > 1): ?>
            <nav class="pagination" aria-label="Страницы карт">
                <?php if ($page > 1): ?>
                    <a class="page-link page-first" href="<?= h(query_url(['page' => 1])) ?>">Первая</a>
                    <a class="page-link page-prev" href="<?= h(query_url(['page' => $page - 1])) ?>">Назад</a>
                <?php else: ?>
                    <span class="page-link page-first disabled">Первая</span>
                    <span class="page-link page-prev disabled">Назад</span>
                <?php endif; ?>

                <?php if ($pageWindowStart > 1): ?>
                    <a class="page-link" href="<?= h(query_url(['page' => 1])) ?>">1</a>
                    <?php if ($pageWindowStart > 2): ?><span class="page-gap">...</span><?php endif; ?>
                <?php endif; ?>

                <?php for ($i = $pageWindowStart; $i <= $pageWindowEnd; $i++): ?>
                    <?php if ($i === $page): ?>
                        <span class="page-link page-number active" aria-current="page"><?= $i ?></span>
                    <?php else: ?>
                        <a class="page-link page-number" href="<?= h(query_url(['page' => $i])) ?>"><?= $i ?></a>
                    <?php endif; ?>
                <?php endfor; ?>

                <?php if ($pageWindowEnd < $totalPages): ?>
                    <?php if ($pageWindowEnd < $totalPages - 1): ?><span class="page-gap">...</span><?php endif; ?>
                    <a class="page-link" href="<?= h(query_url(['page' => $totalPages])) ?>"><?= $totalPages ?></a>
                <?php endif; ?>

                <?php if ($page < $totalPages): ?>
                    <a class="page-link page-next" href="<?= h(query_url(['page' => $page + 1])) ?>">Вперед</a>
                    <a class="page-link page-last" href="<?= h(query_url(['page' => $totalPages])) ?>">Последняя</a>
                <?php else: ?>
                    <span class="page-link page-next disabled">Вперед</span>
                    <span class="page-link page-last disabled">Последняя</span>
                <?php endif; ?>
                <span class="page-summary">Страница <?= $page ?> из <?= $totalPages ?></span>
            </nav>
        <?php endif; ?>

        <?php $tableNavigationTarget = '.cards-table'; $tableNavigationLabel = 'Широкая таблица'; require __DIR__ . '/partials/table-navigation.php'; ?>

        <div class="cards-table">
            <?php if ($showConstructed): ?>
            <table class="constructed-table">
                <thead>
                <tr>
                    <th>Карта RU</th>
                    <th>Card EN</th>
                    <th>Crop</th>
                    <th>Форматы</th>
                    <th>Set</th>
                    <th>Тип</th>
                    <th>Класс</th>
                    <th>Мана</th>
                    <th>Статы</th>
                    <th>Картинки</th>
                    <th>Wiki</th>
                    <th>Gallery</th>
                    <th>Patch changes</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($constructedCards as $card): ?>
                    <?php
                    $wikiMeta = $constructedWikiMetaMap[(string)$card['card_id']] ?? null;
                    $wikiMechanics = json_array($wikiMeta['wiki_mechanics_json'] ?? null);
                    $wikiTags = json_array($wikiMeta['wiki_tags_json'] ?? null);
                    $banLists = json_array($wikiMeta['ban_lists_json'] ?? null);
                    $gallery = json_array($wikiMeta['gallery_json'] ?? null);
                    $patchChanges = json_array($wikiMeta['patch_changes_json'] ?? null);
                    $externalLinks = json_array($wikiMeta['external_links_json'] ?? null);
                    $relatedGroups = json_array($wikiMeta['related_cards_json'] ?? null);
                    $relatedCardIds = json_array($wikiMeta['related_card_ids_json'] ?? null);
                    $sounds = json_array($wikiMeta['sounds_json'] ?? null);
                    $goldenCards = json_array($wikiMeta['golden_cards_json'] ?? null);
                    $signatureCards = json_array($wikiMeta['signature_cards_json'] ?? null);
                    $diamondCards = json_array($wikiMeta['diamond_cards_json'] ?? null);
                    $diamondAnimated = json_array($wikiMeta['diamond_animated_json'] ?? null);
                    $soundCount = wiki_sound_count($wikiMeta);
                    $relatedCount = wiki_related_count($wikiMeta);
                    $cardImage = (string)($card['local_image_url'] ?: $card['image_url'] ?: $card['crop_image_url'] ?: '');
                    $cropImage = (string)($card['local_crop_image_url'] ?: $card['crop_image_url'] ?: '');
                    $goldenImage = (string)($card['local_gold_image_url'] ?: $card['image_gold_url'] ?: ($goldenCards[0]['file_url'] ?? ''));
                    $signatureImage = (string)($card['image_signature_url'] ?: ($signatureCards[0]['file_url'] ?? ''));
                    $diamondImage = (string)($card['image_diamond_url'] ?: ($diamondCards[0]['file_url'] ?? ''));
                    $animatedDiamondImage = (string)($card['animated_diamond_url'] ?: ($diamondAnimated[0]['file_url'] ?? ''));
                    $tooltip = constructed_card_tooltip($card);
                    $formatSlugs = array_filter(explode(',', (string)($card['formats'] ?? '')));
                    ?>
                    <tr data-row data-search="<?= h(constructed_card_search_text($card)) ?>">
                        <td class="card-name" title="<?= h($tooltip) ?>">
                            <?php if ($cardImage): ?>
                                <img
                                    src="<?= h($cardImage) ?>"
                                    alt="<?= h($card['name_ru'] ?: $card['name_en']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    data-preview="<?= h($cardImage) ?>"
                                    data-tooltip="<?= h($tooltip) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-card-image">Нет</span>
                            <?php endif; ?>
                            <span class="card-name-copy">
                                <span><?= h($card['name_ru'] ?: '—') ?></span>
                                <a class="card-stats-link" href="/?action=analytics&amp;stats=card&amp;stats_q=<?= rawurlencode((string)($card['name_en'] ?: $card['name_ru'])) ?>#statistics">Статистика</a>
                            </span>
                        </td>
                        <td class="name-en">
                            <b><?= h($card['name_en'] ?: '—') ?></b>
                            <?php if (!empty($card['text_ru']) || !empty($card['text_en'])): ?>
                                <span class="subtext"><?= h(strip_tags((string)($card['text_ru'] ?: $card['text_en']))) ?></span>
                            <?php endif; ?>
                            <?php if (!empty($card['flavor_ru'])): ?>
                                <span class="subtext flavor-line"><?= h(strip_tags((string)$card['flavor_ru'])) ?></span>
                            <?php endif; ?>
                            <code><?= h($card['card_id']) ?></code>
                            <span class="muted-dash">dbf <?= h($card['dbf'] ?: '—') ?></span>
                        </td>
                        <td><?= horizontal_art_preview($card['horizontal_image_url'] ?? null, (string)($card['name_ru'] ?: $card['name_en'])) ?: '<span class="muted-dash">—</span>' ?></td>
                        <td>
                            <div class="wiki-tags compact-tags">
                                <?php foreach ($formatSlugs as $formatSlug): ?>
                                    <span><?= h(constructed_format_label($formatSlug)) ?></span>
                                <?php endforeach; ?>
                            </div>
                        </td>
                        <td><?= h($card['card_set'] ?: '—') ?></td>
                        <td><span class="type-badge"><?= h(constructed_card_type_label($card['card_type'] ?? '')) ?></span></td>
                        <td><?= h($card['class_slug'] ?: '—') ?></td>
                        <td><?= h($card['mana_cost'] ?? '—') ?></td>
                        <td>
                            <?php if ($card['attack'] !== null || $card['health'] !== null): ?>
                                <?= h(($card['attack'] ?? '—') . ' / ' . ($card['health'] ?? '—')) ?>
                                <?php if (!empty($card['minion_type'])): ?><span class="muted-dash"><?= h($card['minion_type']) ?></span><?php endif; ?>
                                <?php if (!empty($card['spell_school'])): ?><span class="muted-dash"><?= h($card['spell_school']) ?></span><?php endif; ?>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td class="constructed-images">
                            <?php if ($cropImage): ?>
                                <img class="variant-preview" src="<?= h($cropImage) ?>" alt="Арт <?= h($card['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($cropImage) ?>" data-tooltip="<?= h(($card['name_ru'] ?: $card['name_en']) . "\nАрт") ?>">
                            <?php endif; ?>
                            <?php if ($goldenImage): ?>
                                <img class="variant-preview" src="<?= h($goldenImage) ?>" alt="Golden <?= h($card['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($goldenImage) ?>" data-tooltip="<?= h(($card['name_ru'] ?: $card['name_en']) . "\nGolden card") ?>">
                            <?php endif; ?>
                            <?php if ($signatureImage): ?>
                                <img class="variant-preview" src="<?= h($signatureImage) ?>" alt="Signature <?= h($card['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($signatureImage) ?>" data-tooltip="<?= h(($card['name_ru'] ?: $card['name_en']) . "\nSignature card") ?>">
                            <?php endif; ?>
                            <?php if ($diamondImage): ?>
                                <img class="variant-preview diamond-preview" src="<?= h($diamondImage) ?>" alt="Diamond <?= h($card['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($diamondImage) ?>" data-tooltip="<?= h(($card['name_ru'] ?: $card['name_en']) . "\nDiamond card") ?>">
                            <?php endif; ?>
                            <?php if ($animatedDiamondImage): ?>
                                <img class="variant-preview diamond-preview" src="<?= h($animatedDiamondImage) ?>" alt="Animated Diamond <?= h($card['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($animatedDiamondImage) ?>" data-tooltip="<?= h(($card['name_ru'] ?: $card['name_en']) . "\nAnimated Diamond") ?>">
                            <?php endif; ?>
                            <?php if (!$cropImage && !$goldenImage && !$signatureImage && !$diamondImage && !$animatedDiamondImage): ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                        <td class="wiki-cell">
                            <?php if ($wikiMeta): ?>
                                <details class="wiki-details">
                                    <summary>
                                        <span class="wiki-status<?= h(wiki_status_class($wikiMeta)) ?>"><?= h(wiki_status_label($wikiMeta)) ?></span>
                                        <span class="wiki-brief">
                                            <?= h($card['artist'] ?: 'без художника') ?>
                                            <?php if ($gallery): ?> · <?= count($gallery) ?> арт<?php endif; ?>
                                            <?php if ($soundCount > 0): ?> · <?= $soundCount ?> зв.<?php endif; ?>
                                            <?php if ($relatedCount > 0): ?> · <?= $relatedCount ?> связ.<?php endif; ?>
                                        </span>
                                    </summary>
                                    <div class="wiki-panel">
                                        <?php if (($wikiMeta['status'] ?? '') !== 'ok'): ?>
                                            <div class="wiki-muted"><?= h($wikiMeta['error'] ?: 'Wiki-данные еще не синхронизированы.') ?></div>
                                        <?php else: ?>
                                            <div class="wiki-grid">
                                                <div><b>Artist</b><span><?= h($card['artist'] ?: '—') ?></span></div>
                                                <div><b>Rarity</b><span><?= h($card['rarity'] ?: '—') ?></span></div>
                                                <div><b>Fetched</b><span><?= h($wikiMeta['fetched_at'] ?: '—') ?></span></div>
                                                <div><b>Changed</b><span><?= h($wikiMeta['changed_at'] ?: '—') ?></span></div>
                                            </div>
                                            <?php if (!empty($wikiMeta['wiki_page_url'])): ?>
                                                <a class="wiki-link" href="<?= h($wikiMeta['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                            <?php elseif (!empty($card['wiki_page_url'])): ?>
                                                <a class="wiki-link" href="<?= h($card['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                            <?php endif; ?>
                                            <?php if ($wikiMechanics): ?>
                                                <div class="wiki-section"><b>Wiki mechanics</b><div class="wiki-tags"><?php foreach ($wikiMechanics as $item): ?><span><?= h($item) ?></span><?php endforeach; ?></div></div>
                                            <?php endif; ?>
                                            <?php if ($wikiTags): ?>
                                                <div class="wiki-section"><b>Wiki tags</b><div class="wiki-tags"><?php foreach ($wikiTags as $item): ?><span><?= h($item) ?></span><?php endforeach; ?></div></div>
                                            <?php endif; ?>
                                            <?php if ($banLists): ?>
                                                <div class="wiki-section"><b>Ban lists</b><ul class="wiki-list"><?php foreach ($banLists as $ban): ?><li><?= h($ban['text'] ?? compact_text($ban)) ?><?php if (!empty($ban['url'])): ?> <a href="<?= h($ban['url']) ?>" target="_blank" rel="noopener">link</a><?php endif; ?></li><?php endforeach; ?></ul></div>
                                            <?php endif; ?>
                                            <?php if ($relatedGroups): ?>
                                                <div class="wiki-section constructed-related-section">
                                                    <b>Сопутствующие карты</b>
                                                    <?php foreach ($relatedGroups as $group): ?>
                                                        <section class="constructed-related-group">
                                                            <h4><?= h(constructed_related_heading_ru($group['heading'] ?? null)) ?></h4>
                                                            <div class="constructed-related-grid">
                                                                <?php foreach (($group['cards'] ?? []) as $related): ?>
                                                                    <?php
                                                                    $relatedId = trim((string)($related['card_id'] ?? ''));
                                                                    $relatedCard = $relatedId !== '' ? ($constructedRelatedCardMap[$relatedId] ?? null) : null;
                                                                    $relatedImage = $relatedCard
                                                                        ? (string)($relatedCard['local_image_url'] ?: $relatedCard['image_url'] ?: '')
                                                                        : (string)($related['image_url'] ?? '');
                                                                    $relatedArt = $relatedCard
                                                                        ? (string)($relatedCard['local_wiki_full_art_url'] ?: $relatedCard['wiki_full_art_url'] ?: '')
                                                                        : '';
                                                                    $relatedNameRu = $relatedCard
                                                                        ? (string)($relatedCard['name_ru'] ?: $relatedCard['name_en'] ?: ($related['title'] ?? $relatedId))
                                                                        : (string)(($related['title'] ?? '') ?: ($relatedId ?: 'Неизвестная карта'));
                                                                    $relatedNameEn = $relatedCard ? (string)($relatedCard['name_en'] ?? '') : (string)($related['title'] ?? '');
                                                                    $relatedTooltip = $relatedCard ? constructed_card_tooltip($relatedCard) : $relatedNameRu;
                                                                    $relatedWikiUrl = (string)($related['url'] ?? '');
                                                                    ?>
                                                                    <article class="constructed-related-card">
                                                                        <?php if ($relatedImage !== ''): ?>
                                                                            <img
                                                                                src="<?= h($relatedImage) ?>"
                                                                                alt="<?= h($relatedNameRu) ?>"
                                                                                loading="lazy"
                                                                                decoding="async"
                                                                                tabindex="0"
                                                                                role="button"
                                                                                aria-label="Открыть карту <?= h($relatedNameRu) ?> на весь экран"
                                                                                data-preview="<?= h($relatedImage) ?>"
                                                                                data-tooltip="<?= h($relatedTooltip) ?>"
                                                                            >
                                                                        <?php endif; ?>
                                                                        <div class="constructed-related-copy">
                                                                            <strong><?= h($relatedNameRu) ?></strong>
                                                                            <?php if ($relatedNameEn !== '' && $relatedNameEn !== $relatedNameRu): ?>
                                                                                <span><?= h($relatedNameEn) ?></span>
                                                                            <?php endif; ?>
                                                                            <?php if ($relatedCard): ?>
                                                                                <div class="constructed-related-meta" aria-label="Характеристики карты">
                                                                                    <span><?= h(constructed_card_type_label($relatedCard['card_type'] ?? '')) ?></span>
                                                                                    <?php if ($relatedCard['mana_cost'] !== null): ?><span>Мана <?= h($relatedCard['mana_cost']) ?></span><?php endif; ?>
                                                                                    <?php if ($relatedCard['attack'] !== null || $relatedCard['health'] !== null): ?>
                                                                                        <span><?= h(($relatedCard['attack'] ?? '—') . ' / ' . ($relatedCard['health'] ?? '—')) ?></span>
                                                                                    <?php endif; ?>
                                                                                </div>
                                                                            <?php endif; ?>
                                                                            <?php if ($relatedCard && (!empty($relatedCard['text_ru']) || !empty($relatedCard['text_en']))): ?>
                                                                                <p><?= h(strip_tags((string)($relatedCard['text_ru'] ?: $relatedCard['text_en']))) ?></p>
                                                                            <?php endif; ?>
                                                                            <?php if ($relatedArt !== ''): ?>
                                                                                <figure class="constructed-related-art">
                                                                                    <img
                                                                                        src="<?= h($relatedArt) ?>"
                                                                                        alt="Оригинальный Wiki full art карты <?= h($relatedNameRu) ?>"
                                                                                        loading="lazy"
                                                                                        decoding="async"
                                                                                        tabindex="0"
                                                                                        role="button"
                                                                                        aria-label="Открыть оригинальный Wiki full art карты <?= h($relatedNameRu) ?>"
                                                                                        data-preview="<?= h($relatedArt) ?>"
                                                                                        data-tooltip="<?= h($relatedNameRu . "\nОригинальный Wiki full art" . (!empty($relatedCard['wiki_full_art_width']) && !empty($relatedCard['wiki_full_art_height']) ? "\n" . $relatedCard['wiki_full_art_width'] . "×" . $relatedCard['wiki_full_art_height'] : '') . (!empty($relatedCard['artist']) ? "\nХудожник: " . $relatedCard['artist'] : '')) ?>"
                                                                                    >
                                                                                    <figcaption>
                                                                                        <?php if (!empty($relatedCard['wiki_full_art_file_page_url'])): ?>
                                                                                            <a href="<?= h($relatedCard['wiki_full_art_file_page_url']) ?>" target="_blank" rel="noopener">Wiki full art</a>
                                                                                        <?php else: ?>
                                                                                            Wiki full art
                                                                                        <?php endif; ?>
                                                                                        <?php if (!empty($relatedCard['wiki_full_art_width']) && !empty($relatedCard['wiki_full_art_height'])): ?>
                                                                                            · <?= h($relatedCard['wiki_full_art_width']) ?>×<?= h($relatedCard['wiki_full_art_height']) ?>
                                                                                        <?php endif; ?>
                                                                                        <?php if (!empty($relatedCard['artist'])): ?> · <?= h($relatedCard['artist']) ?><?php endif; ?>
                                                                                    </figcaption>
                                                                                </figure>
                                                                            <?php elseif ($relatedCard && !empty($relatedCard['artist'])): ?>
                                                                                <span>Художник: <?= h($relatedCard['artist']) ?></span>
                                                                            <?php endif; ?>
                                                                            <div class="constructed-related-links">
                                                                                <code><?= h($relatedId !== '' ? $relatedId : 'no id') ?></code>
                                                                                <?php if ($relatedWikiUrl !== ''): ?>
                                                                                    <a href="<?= h($relatedWikiUrl) ?>" target="_blank" rel="noopener">Wiki</a>
                                                                                <?php endif; ?>
                                                                            </div>
                                                                            <?php if (!$relatedCard): ?><em>Локализация ожидает импорта</em><?php endif; ?>
                                                                        </div>
                                                                    </article>
                                                                <?php endforeach; ?>
                                                            </div>
                                                        </section>
                                                    <?php endforeach; ?>
                                                </div>
                                            <?php endif; ?>
                                            <?php if ($relatedCardIds): ?>
                                                <div class="wiki-section"><b>Related IDs</b><div class="wiki-tags"><?php foreach ($relatedCardIds as $relatedId): ?><code><?= h($relatedId) ?></code><?php endforeach; ?></div></div>
                                            <?php endif; ?>
                                            <?php if ($sounds): ?>
                                                <div class="wiki-section"><b>Sounds</b><ul class="wiki-list">
                                                    <?php foreach ($sounds as $group): ?>
                                                        <?php foreach (($group['clips'] ?? []) as $clip): ?>
                                                            <li><?= h(($group['heading'] ?? $clip['group'] ?? 'Sound') . ': ' . ($clip['description'] ?? '')) ?> <?php if (!empty($clip['file_url'])): ?><a href="<?= h($clip['file_url']) ?>" target="_blank" rel="noopener"><?= h($clip['file_title'] ?? 'audio') ?></a><?php endif; ?></li>
                                                        <?php endforeach; ?>
                                                    <?php endforeach; ?>
                                                </ul></div>
                                            <?php endif; ?>
                                            <?php if ($externalLinks): ?>
                                                <div class="wiki-section"><b>External links</b><ul class="wiki-list"><?php foreach ($externalLinks as $link): ?><li><a href="<?= h($link['url'] ?? '#') ?>" target="_blank" rel="noopener"><?= h($link['label'] ?? $link['url'] ?? 'link') ?></a></li><?php endforeach; ?></ul></div>
                                            <?php endif; ?>
                                        <?php endif; ?>
                                    </div>
                                </details>
                            <?php else: ?>
                                <span class="wiki-status empty">В очереди</span>
                            <?php endif; ?>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($gallery): ?>
                                <details class="media-details"><summary><?= count($gallery) ?> images</summary><div class="hero-media-grid art-grid">
                                    <?php foreach ($gallery as $item): ?>
                                        <?php
                                        $galleryImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                        $galleryFull = (string)($item['file_url'] ?? $galleryImage);
                                        $galleryTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Gallery image');
                                        ?>
                                        <figure class="hero-media-item art-item">
                                            <?php if ($galleryImage !== ''): ?>
                                                <img src="<?= h($galleryImage) ?>" alt="<?= h($galleryTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($galleryFull) ?>" data-tooltip="<?= h($galleryTitle) ?>">
                                            <?php endif; ?>
                                            <figcaption><a href="<?= h($item['file_page_url'] ?? $galleryFull) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a></figcaption>
                                        </figure>
                                    <?php endforeach; ?>
                                </div></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($patchChanges): ?>
                                <details><summary><?= count($patchChanges) ?> changes</summary><ul class="wiki-list">
                                    <?php foreach ($patchChanges as $changeGroup): ?>
                                        <?php foreach (($changeGroup['entries'] ?? []) as $changeEntry): ?>
                                            <li><?= h(($changeGroup['heading'] ?? 'Changes') . ': ' . ($changeEntry['date'] ?? '') . ' ' . ($changeEntry['patch'] ?? '')) ?> <?php if (!empty($changeEntry['patch_url'])): ?><a href="<?= h($changeEntry['patch_url']) ?>" target="_blank" rel="noopener">patch</a><?php endif; ?> <?php if (!empty($changeEntry['items'])): ?><span><?= h(implode(' ', array_map('strval', $changeEntry['items']))) ?></span><?php endif; ?></li>
                                        <?php endforeach; ?>
                                    <?php endforeach; ?>
                                </ul></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                    </tr>
                <?php endforeach; ?>
                <?php if (!$constructedCards): ?>
                    <tr><td colspan="13" class="empty">Карты Standard/Wild пока не загружены.</td></tr>
                <?php endif; ?>
                </tbody>
            </table>
            <?php elseif ($showLibrary): ?>
            <table class="library-table">
                <thead>
                <tr>
                    <th>Карта RU</th>
                    <?php if ($libraryType === 'trinket'): ?><th>Full art</th><?php endif; ?>
                    <th>Crop</th>
                    <th>Описание</th>
                    <th>card_id</th>
                    <th>dbf</th>
                    <th>Статус</th>
                    <th>Тир</th>
                    <th>Группа</th>
                    <th>Тип</th>
                    <th>Источник</th>
                    <th>Wiki</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($libraryCards as $card): ?>
                    <?php
                    $cardImage = (string)($card['image_url'] ?? '');
                    $fullArtImage = (string)($card['local_full_art_url'] ?? '');
                    $tooltip = trim(implode("\n", array_filter([
                        $card['name_ru'] ?? '',
                        strip_tags((string)($card['text_ru'] ?: '')),
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <tr>
                        <td class="card-name" title="<?= h($tooltip) ?>">
                            <?php if ($cardImage): ?>
                                <img
                                    src="<?= h($cardImage) ?>"
                                    alt="<?= h($card['name_ru']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    data-preview="<?= h($cardImage) ?>"
                                    data-tooltip="<?= h($tooltip) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-card-image">Нет</span>
                            <?php endif; ?>
                            <span><?= h($card['name_ru']) ?></span>
                        </td>
                        <?php if ($libraryType === 'trinket'): ?><td class="library-art-cell">
                            <?php if ($fullArtImage): ?>
                                <figure class="library-art-preview">
                                    <button
                                        type="button"
                                        class="library-art-button"
                                        data-preview="<?= h($fullArtImage) ?>"
                                        data-tooltip="<?= h('Full art · ' . $card['name_ru']) ?>"
                                        aria-label="<?= h('Открыть full art: ' . $card['name_ru']) ?>"
                                    >
                                        <img
                                            src="<?= h($fullArtImage) ?>"
                                            alt=""
                                            loading="lazy"
                                            decoding="async"
                                            width="72"
                                            height="72"
                                        >
                                    </button>
                                    <figcaption><?= h(($card['full_art_width'] ?: 512) . '×' . ($card['full_art_height'] ?: 512)) ?></figcaption>
                                </figure>
                            <?php else: ?>
                                <span class="missing-card-image">Нет</span>
                            <?php endif; ?>
                        </td><?php endif; ?>
                        <td><?= horizontal_art_preview($card['horizontal_image_url'] ?? null, (string)$card['name_ru']) ?: '<span class="muted-dash">—</span>' ?></td>
                        <td class="name-en">
                            <?php if (!empty($card['text_ru'])): ?>
                                <span class="subtext"><?= h(strip_tags((string)$card['text_ru'])) ?></span>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td><code><?= h($card['card_id']) ?></code></td>
                        <td><?= h($card['dbf'] ?: '—') ?></td>
                        <td>
                            <span class="pool-badge<?= !empty($card['in_pool']) ? '' : ' off' ?>">
                                <?= !empty($card['in_pool']) ? 'В пуле' : 'Удалена' ?>
                            </span>
                        </td>
                        <td>
                            <?php if (!empty($card['tier_value'])): ?>
                                <span class="type-badge"><?= h($card['tier_name_ru'] ?: ('Тир ' . $card['tier_value'])) ?></span>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td><span class="type-badge"><?= h($card['group_name_ru'] ?: '—') ?></span></td>
                        <td><span class="type-badge"><?= h($card['card_type'] ?: '—') ?></span></td>
                        <td><?= h($card['source'] ?: '—') ?></td>
                        <td>
                            <?php if (!empty($card['wiki_page_url'])): ?>
                                <a class="wiki-link" href="<?= h($card['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                    </tr>
                <?php endforeach; ?>
                <?php if (!$libraryCards): ?>
                    <tr><td colspan="<?= $libraryType === 'trinket' ? 12 : 11 ?>" class="empty">Записей библиотеки пока нет.</td></tr>
                <?php endif; ?>
                </tbody>
            </table>
            <?php elseif ($showTimewarped): ?>
            <table class="timewarped-table">
                <thead>
                <tr>
                    <th>Карта</th>
                    <th>Card EN</th>
                    <th>Crop</th>
                    <th>CARD_ID</th>
                    <th>DBF</th>
                    <th>Тип</th>
                    <th>Таверна</th>
                    <th>Статы</th>
                    <th>Золотая</th>
                    <th>Wiki</th>
                    <th>Gallery</th>
                    <th>Card changes</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($timewarpedCards as $card): ?>
                    <?php
                    $cardImage = (string)($card['card_image_url'] ?? '');
                    $goldenImage = (string)($card['golden_image_url'] ?? '');
                    $wikiMechanics = json_array($card['wiki_mechanics_json'] ?? null);
                    $wikiTags = json_array($card['wiki_tags_json'] ?? null);
                    $availability = json_array($card['availability_json'] ?? null);
                    $relatedGroups = json_array($card['related_cards_json'] ?? null);
                    $relatedCardIds = json_array($card['related_card_ids_json'] ?? null);
                    $sounds = json_array($card['sounds_json'] ?? null);
                    $gallery = json_array($card['gallery_json'] ?? null);
                    $cardChanges = json_array($card['card_changes_json'] ?? null);
                    $externalLinks = json_array($card['external_links_json'] ?? null);
                    $fullTags = json_array($card['full_tags_json'] ?? null);
                    $soundCount = wiki_sound_count(['sounds_json' => $card['sounds_json'] ?? null]);
                    $relatedCount = wiki_related_count(['related_cards_json' => $card['related_cards_json'] ?? null]);
                    $tooltip = trim(implode("\n", array_filter([
                        $card['name_ru'] ?? '',
                        $card['name_en'] ?? '',
                        strip_tags((string)($card['text_ru'] ?: $card['text_en'] ?: '')),
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <tr>
                        <td class="card-name" title="<?= h($tooltip) ?>">
                            <?php if ($cardImage): ?>
                                <img
                                    src="<?= h($cardImage) ?>"
                                    alt="<?= h($card['name_ru'] ?: $card['name_en']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    data-preview="<?= h($cardImage) ?>"
                                    data-tooltip="<?= h($tooltip) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-card-image">Нет</span>
                            <?php endif; ?>
                            <span><?= h($card['name_ru'] ?: '—') ?></span>
                        </td>
                        <td class="name-en">
                            <b><?= h($card['name_en']) ?></b>
                            <?php if (!empty($card['text_ru']) || !empty($card['text_en'])): ?>
                                <span class="subtext"><?= h(strip_tags((string)($card['text_ru'] ?: $card['text_en']))) ?></span>
                            <?php endif; ?>
                        </td>
                        <td><?= horizontal_art_preview($card['horizontal_image_url'] ?? null, (string)($card['name_ru'] ?: $card['name_en'])) ?: '<span class="muted-dash">—</span>' ?></td>
                        <td><code><?= h($card['card_id']) ?></code></td>
                        <td><?= h($card['dbf']) ?></td>
                        <td><span class="type-badge <?= h($card['card_type'] ?? '') ?>"><?= h(timewarped_type_label($card['card_type'] ?? '')) ?></span></td>
                        <td><?= h($card['tavern_tier'] ?: '—') ?></td>
                        <td>
                            <?php if ($card['attack'] !== null || $card['health'] !== null): ?>
                                <?= h(($card['attack'] ?? '—') . ' / ' . ($card['health'] ?? '—')) ?>
                                <?php if (!empty($card['minion_type'])): ?><span class="muted-dash"><?= h($card['minion_type']) ?></span><?php endif; ?>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if ($goldenImage): ?>
                                <img
                                    class="variant-preview"
                                    src="<?= h($goldenImage) ?>"
                                    alt="Золотая версия <?= h($card['name_en']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    data-preview="<?= h($goldenImage) ?>"
                                    data-tooltip="<?= h(($card['golden_name_ru'] ?: $card['golden_name_en'] ?: $card['name_en']) . "\n" . strip_tags((string)($card['golden_text_ru'] ?: $card['golden_text_en'] ?: ''))) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-mini">Нет</span>
                            <?php endif; ?>
                        </td>
                        <td class="wiki-cell">
                            <details class="wiki-details">
                                <summary>
                                    <span class="wiki-status<?= h(wiki_status_class($card)) ?>"><?= h(wiki_status_label($card)) ?></span>
                                    <span class="wiki-brief">
                                        <?= h($card['artist'] ?: 'без художника') ?>
                                        <?php if ($soundCount > 0): ?> · <?= $soundCount ?> зв.<?php endif; ?>
                                        <?php if ($relatedCount > 0): ?> · <?= $relatedCount ?> связ.<?php endif; ?>
                                    </span>
                                </summary>
                                <div class="wiki-panel">
                                    <div class="wiki-grid">
                                        <div><b>Artist</b><span><?= h($card['artist'] ?: '—') ?></span></div>
                                        <div><b>Race</b><span><?= h($card['race'] ?: '—') ?></span></div>
                                        <div><b>Minion type</b><span><?= h($card['minion_type'] ?: '—') ?></span></div>
                                        <div><b>Fetched</b><span><?= h($card['fetched_at'] ?: '—') ?></span></div>
                                    </div>
                                    <?php if (!empty($card['wiki_page_url'])): ?>
                                        <a class="wiki-link" href="<?= h($card['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                    <?php endif; ?>
                                    <?php if ($wikiMechanics): ?>
                                        <div class="wiki-section"><b>Wiki mechanics</b><div class="wiki-tags"><?php foreach ($wikiMechanics as $item): ?><span><?= h($item) ?></span><?php endforeach; ?></div></div>
                                    <?php endif; ?>
                                    <?php if ($wikiTags): ?>
                                        <div class="wiki-section"><b>Wiki tags</b><div class="wiki-tags"><?php foreach ($wikiTags as $item): ?><span><?= h($item) ?></span><?php endforeach; ?></div></div>
                                    <?php endif; ?>
                                    <?php if (!empty($availability['notes'])): ?>
                                        <div class="wiki-section"><b>Availability</b><ul class="wiki-list"><?php foreach ($availability['notes'] as $note): ?><li><?= h($note) ?></li><?php endforeach; ?></ul></div>
                                    <?php endif; ?>
                                    <?php if ($relatedGroups): ?>
                                        <div class="wiki-section"><b>Related cards</b><ul class="wiki-list">
                                            <?php foreach ($relatedGroups as $group): ?>
                                                <?php foreach (($group['cards'] ?? []) as $related): ?>
                                                    <li><?= h($group['heading'] ?? 'Related') ?>: <code><?= h(($related['card_id'] ?? '') ?: 'no id') ?></code> <?= h($related['title'] ?? '') ?></li>
                                                <?php endforeach; ?>
                                            <?php endforeach; ?>
                                        </ul></div>
                                    <?php endif; ?>
                                    <?php if ($relatedCardIds): ?>
                                        <div class="wiki-section"><b>Related IDs</b><div class="wiki-tags"><?php foreach ($relatedCardIds as $relatedId): ?><code><?= h($relatedId) ?></code><?php endforeach; ?></div></div>
                                    <?php endif; ?>
                                    <?php if ($sounds): ?>
                                        <div class="wiki-section"><b>Sounds</b><ul class="wiki-list">
                                            <?php foreach ($sounds as $group): ?>
                                                <?php foreach (($group['clips'] ?? []) as $clip): ?>
                                                    <li><?= h(($group['heading'] ?? $clip['group'] ?? 'Sound') . ': ' . ($clip['description'] ?? '')) ?> <?php if (!empty($clip['file_url'])): ?><a href="<?= h($clip['file_url']) ?>" target="_blank" rel="noopener"><?= h($clip['file_title'] ?? 'audio') ?></a><?php endif; ?></li>
                                                <?php endforeach; ?>
                                            <?php endforeach; ?>
                                        </ul></div>
                                    <?php endif; ?>
                                    <?php if ($externalLinks): ?>
                                        <div class="wiki-section"><b>External links</b><ul class="wiki-list"><?php foreach ($externalLinks as $link): ?><li><a href="<?= h($link['url'] ?? '#') ?>" target="_blank" rel="noopener"><?= h($link['label'] ?? $link['url'] ?? 'link') ?></a></li><?php endforeach; ?></ul></div>
                                    <?php endif; ?>
                                    <?php if ($fullTags): ?>
                                        <div class="wiki-section"><b>Full tags</b><div class="wiki-tags"><?php foreach ($fullTags as $tag): ?><code><?= h($tag) ?></code><?php endforeach; ?></div></div>
                                    <?php endif; ?>
                                </div>
                            </details>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($gallery): ?>
                                <details class="media-details"><summary><?= count($gallery) ?> images</summary><div class="hero-media-grid art-grid">
                                    <?php foreach ($gallery as $item): ?>
                                        <?php
                                        $galleryImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                        $galleryFull = (string)($item['file_url'] ?? $galleryImage);
                                        $galleryTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Gallery image');
                                        ?>
                                        <figure class="hero-media-item art-item">
                                            <?php if ($galleryImage !== ''): ?>
                                                <img src="<?= h($galleryImage) ?>" alt="<?= h($galleryTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($galleryFull) ?>" data-tooltip="<?= h($galleryTitle) ?>">
                                            <?php endif; ?>
                                            <figcaption><a href="<?= h($item['file_page_url'] ?? $galleryFull) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a></figcaption>
                                        </figure>
                                    <?php endforeach; ?>
                                </div></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($cardChanges): ?>
                                <details><summary><?= count($cardChanges) ?> changes</summary><ul class="wiki-list">
                                    <?php foreach ($cardChanges as $changeGroup): ?>
                                        <?php foreach (($changeGroup['entries'] ?? []) as $changeEntry): ?>
                                            <li><?= h(($changeGroup['heading'] ?? 'Changes') . ': ' . ($changeEntry['date'] ?? '') . ' ' . ($changeEntry['patch'] ?? '')) ?> <?php if (!empty($changeEntry['items'])): ?><span><?= h(implode(' ', array_map('strval', $changeEntry['items']))) ?></span><?php endif; ?></li>
                                        <?php endforeach; ?>
                                    <?php endforeach; ?>
                                </ul></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                    </tr>
                <?php endforeach; ?>
                <?php if (!$timewarpedCards): ?>
                    <tr><td colspan="12" class="empty">Хрономальные карты пока не загружены.</td></tr>
                <?php endif; ?>
                </tbody>
            </table>
            <?php elseif ($showPets): ?>
            <div class="skin-gallery-grid">
                <?php foreach ($pets as $pet): ?>
                    <?php
                    $petGallery = json_array($pet['gallery_json'] ?? null);
                    $petCardImage = (string)($pet['card_image_url'] ?? '');
                    $petBackground = (string)($pet['end_screen_background_url'] ?? '');
                    $petTooltip = trim(implode("\n", array_filter([
                        $pet['variant_name'] ?? '',
                        $pet['pet_name'] ? 'Pet: ' . $pet['pet_name'] : '',
                        $pet['level'] ? 'Level: ' . $pet['level'] : '',
                        $pet['dbf'] ? 'DBF: ' . $pet['dbf'] : '',
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <article class="skin-card pet-card">
                        <div class="skin-card-media">
                            <?php if ($petCardImage !== ''): ?>
                                <img class="skin-portrait pet-portrait-preview" src="<?= h($petCardImage) ?>" alt="<?= h($pet['variant_name']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($petCardImage) ?>" data-tooltip="<?= h($petTooltip) ?>">
                            <?php elseif ($petBackground !== ''): ?>
                                <img class="skin-portrait pet-background-preview" src="<?= h($petBackground) ?>" alt="<?= h($pet['variant_name']) ?> end screen background" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($petBackground) ?>" data-tooltip="<?= h($petTooltip) ?>">
                            <?php else: ?>
                                <span class="missing-card-image">Нет изображения</span>
                            <?php endif; ?>
                            <div class="skin-media-strip">
                                <?php if ($petCardImage !== ''): ?>
                                    <button type="button" data-preview="<?= h($petCardImage) ?>" data-tooltip="Pet card">Card</button>
                                <?php endif; ?>
                                <?php if ($petBackground !== ''): ?>
                                    <button type="button" data-preview="<?= h($petBackground) ?>" data-tooltip="End screen background">End screen</button>
                                <?php endif; ?>
                            </div>
                            <?= horizontal_art_preview($pet['horizontal_image_url'] ?? null, (string)$pet['variant_name']) ?>
                        </div>
                        <div class="skin-card-body">
                            <div class="skin-card-head">
                                <div>
                                    <h3><?= h($pet['variant_name']) ?></h3>
                                    <p><?= h($pet['pet_name']) ?></p>
                                </div>
                                <span class="pool-badge">Ур. <?= h($pet['level'] ?? '—') ?></span>
                            </div>
                            <div class="skin-category-row">
                                <span><?= h($pet['card_id'] ?: '—') ?></span>
                                <code><?= h($pet['dbf'] ?? '—') ?></code>
                            </div>
                            <div class="skin-meta-grid">
                                <div><b>Дата выхода</b><span><?= h(format_release_date_ru($pet['release_date'] ?? null)) ?></span></div>
                                <div><b>Pet ID</b><span><?= h($pet['pet_id']) ?></span></div>
                                <div><b>Variant</b><span><?= h($pet['variant_id']) ?></span></div>
                                <div><b>Background</b><span><?= $petBackground !== '' ? 'yes' : '—' ?></span></div>
                                <div><b>Wiki</b><span><a href="<?= h($pet['page_url']) ?>" target="_blank" rel="noopener">open</a></span></div>
                            </div>
                            <div class="skin-card-details">
                                <?php if ($petGallery): ?>
                                    <details class="media-details"><summary>Gallery · <?= count($petGallery) ?></summary><div class="hero-media-grid art-grid skin-gallery-mini">
                                        <?php foreach ($petGallery as $item): ?>
                                            <?php
                                            $galleryImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                            $galleryFull = (string)($item['file_url'] ?? $galleryImage);
                                            $galleryTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Gallery image');
                                            ?>
                                            <figure class="hero-media-item art-item">
                                                <?php if ($galleryImage !== ''): ?>
                                                    <img src="<?= h($galleryImage) ?>" alt="<?= h($galleryTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($galleryFull) ?>" data-tooltip="<?= h($galleryTitle) ?>">
                                                <?php endif; ?>
                                                <figcaption><a href="<?= h($galleryFull) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a></figcaption>
                                            </figure>
                                        <?php endforeach; ?>
                                    </div></details>
                                <?php endif; ?>
                            </div>
                        </div>
                    </article>
                <?php endforeach; ?>
                <?php if (!$pets): ?>
                    <div class="empty">Питомцы пока не загружены.</div>
                <?php endif; ?>
            </div>
            <?php elseif ($showCoins): ?>
            <div class="skin-gallery-grid">
                <?php foreach ($coins as $coin): ?>
                    <?php
                    $coinImage = (string)($coin['image_url'] ?: $coin['wiki_image_url'] ?: '');
                    $coinCrop = (string)($coin['crop_image_url'] ?? '');
                    $coinGeneratedBy = json_array($coin['generated_by_cards_json'] ?? null);
                    $coinRelated = json_array($coin['related_cards_json'] ?? null);
                    $coinText = trim(strip_tags((string)($coin['text_ru'] ?: $coin['text_en'] ?: '')));
                    $coinTooltip = trim(implode("\n", array_filter([
                        $coin['coin_name_en'] ?? '',
                        $coin['card_name_ru'] ?? '',
                        $coinText,
                        $coin['artist'] ? 'Artist: ' . $coin['artist'] : '',
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <article class="skin-card pet-card">
                        <div class="skin-card-media">
                            <?php if ($coinImage !== ''): ?>
                                <img class="skin-portrait" src="<?= h($coinImage) ?>" alt="<?= h($coin['coin_name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($coinImage) ?>" data-tooltip="<?= h($coinTooltip) ?>">
                            <?php else: ?>
                                <span class="missing-card-image">Нет изображения</span>
                            <?php endif; ?>
                            <div class="skin-media-strip">
                                <?php if ($coinImage !== ''): ?>
                                    <button type="button" data-preview="<?= h($coinImage) ?>" data-tooltip="Coin card">Card</button>
                                <?php endif; ?>
                                <?php if ($coinCrop !== ''): ?>
                                    <button type="button" data-preview="<?= h($coinCrop) ?>" data-tooltip="Crop art">Crop</button>
                                <?php endif; ?>
                            </div>
                            <?= horizontal_art_preview($coin['horizontal_image_url'] ?? null, (string)$coin['coin_name_en']) ?>
                        </div>
                        <div class="skin-card-body">
                            <div class="skin-card-head">
                                <div>
                                    <h3><?= h($coin['coin_name_en']) ?></h3>
                                    <p><?= h($coin['card_name_ru'] ?: $coin['card_name_en'] ?: 'The Coin') ?></p>
                                </div>
                                <span class="pool-badge">Coin</span>
                            </div>
                            <?php if ($coinText !== ''): ?>
                                <p class="subtext"><?= h($coinText) ?></p>
                            <?php endif; ?>
                            <div class="skin-category-row">
                                <span><?= h($coin['artist'] ?: 'Artist unknown') ?></span>
                                <code><?= h($coin['card_id']) ?></code>
                            </div>
                            <div class="skin-meta-grid">
                                <div><b>Дата выхода</b><span><?= h(format_release_date_ru($coin['release_date'] ?? null)) ?></span></div>
                                <div><b>DBF</b><span><?= h($coin['dbf'] ?? '—') ?></span></div>
                                <div><b>Sort</b><span><?= h($coin['cosmetic_sort_order'] ?? '—') ?></span></div>
                                <div><b>Generated by</b><span><?= count($coinGeneratedBy) ?></span></div>
                                <div><b>Related with</b><span><?= count($coinRelated) ?></span></div>
                            </div>
                            <?php if (!empty($coin['flavor_text'])): ?>
                                <p class="subtext"><?= h(strip_tags((string)$coin['flavor_text'])) ?></p>
                            <?php endif; ?>
                            <div class="skin-card-details">
                                <?php if ($coinGeneratedBy): ?>
                                    <details class="media-details"><summary>Generated by · <?= count($coinGeneratedBy) ?></summary><div class="wiki-tags skin-tags">
                                        <?php foreach ($coinGeneratedBy as $linkedCard): ?>
                                            <code title="<?= h((string)($linkedCard['name_en'] ?? $linkedCard['page_title'] ?? '')) ?>"><?= h((string)($linkedCard['card_id'] ?? '')) ?></code>
                                        <?php endforeach; ?>
                                    </div></details>
                                <?php endif; ?>
                                <?php if ($coinRelated): ?>
                                    <details class="media-details"><summary>Related with · <?= count($coinRelated) ?></summary><div class="wiki-tags skin-tags">
                                        <?php foreach ($coinRelated as $linkedCard): ?>
                                            <code title="<?= h((string)($linkedCard['name_en'] ?? $linkedCard['page_title'] ?? '')) ?>"><?= h((string)($linkedCard['card_id'] ?? '')) ?></code>
                                        <?php endforeach; ?>
                                    </div></details>
                                <?php endif; ?>
                                <?php if (!empty($coin['wiki_page_url'])): ?>
                                    <a class="wiki-link" href="<?= h($coin['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                <?php endif; ?>
                            </div>
                        </div>
                    </article>
                <?php endforeach; ?>
                <?php if (!$coins): ?>
                    <div class="empty">Монетки пока не загружены.</div>
                <?php endif; ?>
            </div>
            <?php elseif ($showHeroSkins): ?>
            <div class="skin-gallery-grid">
                <?php foreach ($heroSkins as $skin): ?>
                    <?php
                    $skinCategories = json_array($skin['categories_json'] ?? null);
                    $skinTags = json_array($skin['tags_json'] ?? null);
                    $skinGallery = json_array($skin['gallery_json'] ?? null);
                    $skinSounds = json_array($skin['sounds_json'] ?? null);
                    $skinAnimatedAssets = json_array($skin['animated_asset_json'] ?? null);
                    $skinAnimatedPrimary = (string)($skin['animated_image_url'] ?: $skin['static_image_url'] ?: '');
                    $skinAnimatedType = preg_match('~\.(webm|mp4)(?:\?|$)~i', (string)$skin['animated_image_url']) ? 'video' : 'image';
                    $skinAnimatedLabel = $skinAnimatedType === 'video' ? 'WEBM' : 'GIF';
                    $skinFullArt = (string)($skin['full_art_url'] ?? '');
                    $skinRarityLabel = (string)($skin['rarity_name_ru'] ?: $skin['rarity_name_en'] ?: ($skinRarityLabels[$skin['rarity_slug'] ?? ''] ?? 'Не указана'));
                    $skinPrimaryCategory = (string)($skin['primary_category_ru'] ?: $skin['primary_category_en'] ?: '—');
                    $skinTooltip = trim(implode("\n", array_filter([
                        $skin['name_en'] ?? '',
                        $skin['character_name'] ? 'Character: ' . $skin['character_name'] : '',
                        $skinRarityLabel ? 'Rarity: ' . $skinRarityLabel : '',
                        $skin['actor'] ? 'Actor: ' . $skin['actor'] : '',
                        $skin['artist'] ? 'Artist: ' . $skin['artist'] : '',
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <article class="skin-card">
                        <div class="skin-card-media">
                            <?php if (!empty($skin['static_image_url'])): ?>
                                <img class="skin-portrait" src="<?= h($skin['static_image_url']) ?>" alt="<?= h($skin['name_en']) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($skin['static_image_url']) ?>" data-tooltip="<?= h($skinTooltip) ?>">
                            <?php else: ?>
                                <span class="missing-card-image">Нет static</span>
                            <?php endif; ?>
                            <div class="skin-media-strip">
                                <?php if (!empty($skin['static_image_url'])): ?>
                                    <button type="button" data-preview="<?= h($skin['static_image_url']) ?>" data-tooltip="Static">Static</button>
                                <?php endif; ?>
                                <?php if (!empty($skin['animated_image_url'])): ?>
                                    <button type="button" data-preview="<?= h($skin['animated_image_url']) ?>" data-preview-type="<?= h($skinAnimatedType) ?>" data-tooltip="Animated <?= h($skinAnimatedLabel) ?>"><?= h($skinAnimatedLabel) ?></button>
                                <?php elseif ($skinAnimatedAssets): ?>
                                    <span class="skin-asset-pill" title="<?= h(compact_text($skinAnimatedAssets)) ?>">Asset</span>
                                <?php endif; ?>
                                <?php if ($skinFullArt !== ''): ?>
                                    <button type="button" data-preview="<?= h($skinFullArt) ?>" data-tooltip="Full art">Full art</button>
                                <?php endif; ?>
                            </div>
                            <?= horizontal_art_preview($skin['horizontal_image_url'] ?? null, (string)$skin['name_en']) ?>
                        </div>
                        <div class="skin-card-body">
                            <div class="skin-card-head">
                                <div>
                                    <h3><?= h($skin['name_en']) ?></h3>
                                    <p><?= h($skin['character_name'] ?: 'Character unknown') ?></p>
                                </div>
                                <span class="pool-badge"><?= h($skin['class_name_ru'] ?: $skin['class_name_en'] ?: '—') ?></span>
                            </div>
                            <div class="skin-category-row">
                                <span><?= h($skinRarityLabel) ?> · <?= h($skinPrimaryCategory) ?></span>
                                <code><?= h($skin['card_id']) ?></code>
                            </div>
                            <div class="skin-meta-grid">
                                <div><b>Дата выхода</b><span><?= h(format_release_date_ru($skin['release_date'] ?? null)) ?></span></div>
                                <div><b>Rarity</b><span><?= h($skinRarityLabel) ?></span></div>
                                <div><b>Actor</b><span><?= h($skin['actor'] ?: '—') ?></span></div>
                                <div><b>Artist</b><span><?= h($skin['artist'] ?: '—') ?></span></div>
                                <div><b>DBF</b><span><?= h($skin['dbf'] ?? '—') ?></span></div>
                                <div><b>Wiki</b><span><a href="<?= h($skin['page_url']) ?>" target="_blank" rel="noopener">open</a></span></div>
                            </div>
                            <?php if ($skinTags || count($skinCategories) > 1): ?>
                                <div class="wiki-tags skin-tags">
                                    <?php foreach (array_slice($skinTags, 0, 4) as $tag): ?><code><?= h($tag) ?></code><?php endforeach; ?>
                                    <?php foreach (array_slice($skinCategories, 0, 3) as $category): ?><code><?= h($category['name_ru'] ?? $category['name_en'] ?? $category['slug'] ?? '') ?></code><?php endforeach; ?>
                                </div>
                            <?php endif; ?>
                            <div class="skin-card-details">
                                <?php if ($skinGallery): ?>
                                    <details class="media-details"><summary>Gallery · <?= count($skinGallery) ?></summary><div class="hero-media-grid art-grid skin-gallery-mini">
                                        <?php foreach ($skinGallery as $item): ?>
                                            <?php
                                            $galleryImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                            $galleryFull = (string)($item['file_url'] ?? $galleryImage);
                                            $galleryTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Gallery image');
                                            ?>
                                            <figure class="hero-media-item art-item">
                                                <?php if ($galleryImage !== ''): ?>
                                                    <img src="<?= h($galleryImage) ?>" alt="<?= h($galleryTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($galleryFull) ?>" data-tooltip="<?= h($galleryTitle) ?>">
                                                <?php endif; ?>
                                                <figcaption><a href="<?= h($galleryFull) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a></figcaption>
                                            </figure>
                                        <?php endforeach; ?>
                                    </div></details>
                                <?php endif; ?>
                                <?php if ($skinSounds): ?>
                                    <details class="related-media-details">
                                        <summary>Sounds · <?= count($skinSounds) ?></summary>
                                        <ul class="sound-list skin-sound-list">
                                            <?php foreach (array_slice($skinSounds, 0, 12) as $sound): ?>
                                                <li>
                                                    <span><?= h(($sound['type'] ?? 'Sound') . ': ' . ($sound['transcript'] ?? '')) ?></span>
                                                    <?php if (!empty($sound['file_url'])): ?>
                                                        <audio controls preload="none" src="<?= h($sound['file_url']) ?>"></audio>
                                                    <?php endif; ?>
                                                </li>
                                            <?php endforeach; ?>
                                        </ul>
                                    </details>
                                <?php endif; ?>
                                <?php if ($skinAnimatedAssets): ?>
                                    <details class="related-media-details">
                                        <summary>Animated assets · <?= count($skinAnimatedAssets) ?></summary>
                                        <div class="wiki-tags skin-tags">
                                            <?php foreach ($skinAnimatedAssets as $asset): ?><code><?= h(($asset['kind'] ?? 'asset') . ': ' . ($asset['asset'] ?? '')) ?></code><?php endforeach; ?>
                                        </div>
                                    </details>
                                <?php endif; ?>
                            </div>
                        </div>
                    </article>
                <?php endforeach; ?>
                <?php if (!$heroSkins): ?>
                    <div class="empty">Скины героев пока не загружены.</div>
                <?php endif; ?>
            </div>
            <?php elseif ($showHeroes): ?>
            <table class="heroes-table">
                <thead>
                <tr>
                    <th>Герой</th>
                    <th>Crop</th>
                    <th>RU</th>
                    <th>card_id</th>
                    <th>dbf</th>
                    <th>Armor</th>
                    <th>Сила героя</th>
                    <th>Buddy</th>
                    <th>Wiki</th>
                    <th>Hero skins</th>
                    <th>Gallery</th>
                    <th>Card changes</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($heroes as $hero): ?>
                    <?php
                    $heroPower = json_array($hero['hero_power_json'] ?? null);
                    $buddy = json_array($hero['buddy_json'] ?? null);
                    $skins = json_array($hero['hero_skins_json'] ?? null);
                    $gallery = json_array($hero['gallery_json'] ?? null);
                    $cardChanges = json_array($hero['card_changes_json'] ?? null);
                    $availability = json_array($hero['availability_json'] ?? null);
                    $externalLinks = json_array($hero['external_links_json'] ?? null);
                    $heroPowerImage = (string)($heroPower['image'] ?? $heroPower['crop_image'] ?? '');
                    $buddyImage = (string)($buddy['image'] ?? $buddy['crop_image'] ?? '');
                    $buddyGolden = is_array($buddy['golden'] ?? null) ? $buddy['golden'] : null;
                    $buddyGoldenImage = (string)($buddyGolden['image'] ?? $buddy['image_gold'] ?? '');
                    $heroPowerGallery = is_array($heroPower['gallery'] ?? null) ? $heroPower['gallery'] : [];
                    $heroPowerGalleryCount = card_gallery_count($heroPower);
                    $heroPowerSoundCount = card_sound_count($heroPower);
                    $heroPowerFullArt = (string)($heroPower['full_art_url'] ?? '');
                    $heroPowerWiki = is_array($heroPower['wiki'] ?? null) ? $heroPower['wiki'] : [];
                    $heroPowerPreview = is_array($heroPowerGallery[0] ?? null) ? $heroPowerGallery[0] : [];
                    $heroPowerPreviewImage = (string)($heroPowerPreview['thumb_url'] ?? $heroPowerPreview['file_url'] ?? $heroPowerFullArt);
                    $heroPowerPreviewFull = (string)($heroPowerPreview['file_url'] ?? $heroPowerFullArt ?? $heroPowerPreviewImage);
                    $heroPowerPreviewTitle = (string)($heroPowerPreview['caption'] ?? $heroPowerPreview['file_title'] ?? ($heroPower['name'] ?? 'Hero power art'));
                    $buddyGallery = is_array($buddy['gallery'] ?? null) ? $buddy['gallery'] : [];
                    $buddySounds = is_array($buddy['sounds'] ?? null) ? $buddy['sounds'] : [];
                    $buddyGalleryCount = card_gallery_count($buddy);
                    $buddySoundCount = card_sound_count($buddy);
                    $buddyFullArt = (string)($buddy['full_art_url'] ?? '');
                    $buddyWiki = is_array($buddy['wiki'] ?? null) ? $buddy['wiki'] : [];
                    $buddyPreview = is_array($buddyGallery[0] ?? null) ? $buddyGallery[0] : [];
                    $buddyPreviewImage = (string)($buddyPreview['thumb_url'] ?? $buddyPreview['file_url'] ?? $buddyFullArt);
                    $buddyPreviewFull = (string)($buddyPreview['file_url'] ?? $buddyFullArt ?? $buddyPreviewImage);
                    $buddyPreviewTitle = (string)($buddyPreview['caption'] ?? $buddyPreview['file_title'] ?? ($buddy['name'] ?? 'Buddy art'));
                    $heroTooltip = trim(implode("\n", array_filter([
                        $hero['name_en'] ?? '',
                        $hero['name_ru'] ?? '',
                        $hero['armor_text'] ?? '',
                        $hero['as_hero'] ?? '',
                    ], static fn($value): bool => (string)$value !== '')));
                    ?>
                    <tr>
                        <td class="hero-card-cell">
                            <?php if (!empty($hero['hero_image_url'])): ?>
                                <img
                                    src="<?= h($hero['hero_image_url']) ?>"
                                    alt="<?= h($hero['name_en']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="64"
                                    height="92"
                                    tabindex="0"
                                    role="button"
                                    data-preview="<?= h($hero['hero_image_url']) ?>"
                                    data-tooltip="<?= h($heroTooltip) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-card-image">Нет</span>
                            <?php endif; ?>
                            <span class="card-name-copy">
                                <span><?= h($hero['name_en']) ?></span>
                                <a class="card-stats-link" href="/?action=analytics&amp;stats=card&amp;stats_q=<?= rawurlencode((string)$hero['name_en']) ?>#statistics">Статистика</a>
                            </span>
                        </td>
                        <td><?= horizontal_art_preview($hero['horizontal_image_url'] ?? null, (string)($hero['name_ru'] ?: $hero['name_en'])) ?: '<span class="muted-dash">—</span>' ?></td>
                        <td><?= h($hero['name_ru'] ?: '—') ?></td>
                        <td><code><?= h($hero['card_id']) ?></code></td>
                        <td><?= h($hero['dbf']) ?></td>
                        <td>
                            <span class="pool-badge"><?= h($hero['armor_text'] ?: $hero['armor'] ?: '—') ?></span>
                            <?php if ($hero['duos_armor'] !== null): ?><span class="muted-dash">duos <?= h($hero['duos_armor']) ?></span><?php endif; ?>
                        </td>
                        <td class="hero-power-cell">
                            <?php if ($heroPower): ?>
                                <?php if ($heroPowerImage !== ''): ?>
                                    <img class="hero-mini-image" src="<?= h($heroPowerImage) ?>" alt="<?= h($heroPower['name'] ?? 'Hero power') ?>" loading="lazy" decoding="async">
                                <?php endif; ?>
                                <div class="related-card-block">
                                    <b><?= h($heroPower['name'] ?? '—') ?></b>
                                    <span><?= h(strip_tags((string)($heroPower['text'] ?? ''))) ?></span>
                                    <div class="related-media-badges">
                                        <?php if ($heroPowerGalleryCount > 0): ?><span><?= $heroPowerGalleryCount ?> арт</span><?php endif; ?>
                                        <?php if ($heroPowerSoundCount > 0): ?><span><?= $heroPowerSoundCount ?> зв.</span><?php endif; ?>
                                        <?php if (!empty($heroPowerWiki['page_url'])): ?><a href="<?= h($heroPowerWiki['page_url']) ?>" target="_blank" rel="noopener">wiki</a><?php endif; ?>
                                        <?php if ($heroPowerFullArt !== ''): ?><a href="<?= h($heroPowerFullArt) ?>" target="_blank" rel="noopener">full art</a><?php endif; ?>
                                    </div>
                                    <?php if ($heroPowerPreviewImage !== ''): ?>
                                        <figure class="related-art-preview">
                                            <img src="<?= h($heroPowerPreviewImage) ?>" alt="<?= h($heroPowerPreviewTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($heroPowerPreviewFull) ?>" data-tooltip="<?= h($heroPowerPreviewTitle) ?>">
                                            <figcaption><?= h($heroPowerPreviewTitle) ?></figcaption>
                                        </figure>
                                    <?php endif; ?>
                                    <?php if ($heroPowerGallery): ?>
                                        <details class="related-media-details">
                                            <summary>Все арты силы героя</summary>
                                            <div class="hero-media-grid related-art-grid">
                                                <?php foreach ($heroPowerGallery as $item): ?>
                                                    <?php
                                                    $itemImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                                    $itemFull = (string)($item['file_url'] ?? $itemImage);
                                                    $itemTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Hero power art');
                                                    ?>
                                                    <figure class="hero-media-item art-item">
                                                        <?php if ($itemImage !== ''): ?>
                                                            <img src="<?= h($itemImage) ?>" alt="<?= h($itemTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($itemFull) ?>" data-tooltip="<?= h($itemTitle) ?>">
                                                        <?php endif; ?>
                                                        <figcaption><a href="<?= h($item['file_page_url'] ?? $itemFull) ?>" target="_blank" rel="noopener"><?= h($itemTitle) ?></a></figcaption>
                                                    </figure>
                                                <?php endforeach; ?>
                                            </div>
                                        </details>
                                    <?php endif; ?>
                                </div>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td class="hero-power-cell">
                            <?php if ($buddy): ?>
                                <div class="buddy-versions">
                                    <div class="buddy-version">
                                        <?php if ($buddyImage !== ''): ?>
                                            <img class="hero-mini-image" src="<?= h($buddyImage) ?>" alt="<?= h($buddy['name'] ?? 'Buddy') ?>" loading="lazy" decoding="async">
                                        <?php endif; ?>
                                        <div class="related-card-block">
                                            <b><?= h($buddy['name'] ?? '—') ?></b>
                                            <span><?= h(strip_tags((string)($buddy['text'] ?? ''))) ?></span>
                                            <div class="related-media-badges">
                                                <?php if ($buddyGalleryCount > 0): ?><span><?= $buddyGalleryCount ?> арт</span><?php endif; ?>
                                                <?php if ($buddySoundCount > 0): ?><span><?= $buddySoundCount ?> зв.</span><?php endif; ?>
                                                <?php if (!empty($buddyWiki['page_url'])): ?><a href="<?= h($buddyWiki['page_url']) ?>" target="_blank" rel="noopener">wiki</a><?php endif; ?>
                                                <?php if ($buddyFullArt !== ''): ?><a href="<?= h($buddyFullArt) ?>" target="_blank" rel="noopener">full art</a><?php endif; ?>
                                            </div>
                                            <?php if ($buddyPreviewImage !== ''): ?>
                                                <figure class="related-art-preview">
                                                    <img src="<?= h($buddyPreviewImage) ?>" alt="<?= h($buddyPreviewTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($buddyPreviewFull) ?>" data-tooltip="<?= h($buddyPreviewTitle) ?>">
                                                    <figcaption><?= h($buddyPreviewTitle) ?></figcaption>
                                                </figure>
                                            <?php endif; ?>
                                            <?php if ($buddyGallery): ?>
                                                <details class="related-media-details">
                                                    <summary>Все арты компаньона</summary>
                                                    <div class="hero-media-grid related-art-grid">
                                                        <?php foreach ($buddyGallery as $item): ?>
                                                            <?php
                                                            $itemImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? '');
                                                            $itemFull = (string)($item['file_url'] ?? $itemImage);
                                                            $itemTitle = (string)($item['caption'] ?? $item['file_title'] ?? 'Buddy art');
                                                            ?>
                                                            <figure class="hero-media-item art-item">
                                                                <?php if ($itemImage !== ''): ?>
                                                                    <img src="<?= h($itemImage) ?>" alt="<?= h($itemTitle) ?>" loading="lazy" decoding="async" tabindex="0" role="button" data-preview="<?= h($itemFull) ?>" data-tooltip="<?= h($itemTitle) ?>">
                                                                <?php endif; ?>
                                                                <figcaption><a href="<?= h($item['file_page_url'] ?? $itemFull) ?>" target="_blank" rel="noopener"><?= h($itemTitle) ?></a></figcaption>
                                                            </figure>
                                                        <?php endforeach; ?>
                                                    </div>
                                                </details>
                                            <?php endif; ?>
                                            <?php if ($buddySounds): ?>
                                                <details class="related-media-details">
                                                    <summary>Sounds компаньона</summary>
                                                    <ul class="sound-list">
                                                        <?php foreach ($buddySounds as $soundGroup): ?>
                                                            <?php foreach (($soundGroup['clips'] ?? []) as $clip): ?>
                                                                <li>
                                                                    <span><?= h(($soundGroup['heading'] ?? $clip['group'] ?? 'Sound') . ': ' . ($clip['description'] ?? '')) ?></span>
                                                                    <?php if (!empty($clip['file_url'])): ?>
                                                                        <audio controls preload="none" src="<?= h($clip['file_url']) ?>"></audio>
                                                                    <?php endif; ?>
                                                                </li>
                                                            <?php endforeach; ?>
                                                        <?php endforeach; ?>
                                                    </ul>
                                                </details>
                                            <?php endif; ?>
                                        </div>
                                    </div>
                                    <?php if ($buddyGolden || $buddyGoldenImage !== ''): ?>
                                        <div class="buddy-version buddy-version-golden">
                                            <?php if ($buddyGoldenImage !== ''): ?>
                                                <img class="hero-mini-image" src="<?= h($buddyGoldenImage) ?>" alt="<?= h($buddyGolden['name'] ?? $buddy['name'] ?? 'Золотой buddy') ?>" loading="lazy" decoding="async">
                                            <?php endif; ?>
                                            <div>
                                                <b>Золотая версия: <?= h($buddyGolden['name'] ?? $buddy['name'] ?? '—') ?></b>
                                                <?php if (!empty($buddyGolden['text'])): ?>
                                                    <span><?= h(strip_tags((string)$buddyGolden['text'])) ?></span>
                                                <?php endif; ?>
                                            </div>
                                        </div>
                                    <?php endif; ?>
                                </div>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td class="wiki-cell">
                            <details class="wiki-details">
                                <summary>
                                    <span class="wiki-status<?= h(wiki_status_class($hero)) ?>"><?= h(wiki_status_label($hero)) ?></span>
                                    <?php if (!empty($hero['artist'])): ?><span class="wiki-brief"><?= h($hero['artist']) ?></span><?php endif; ?>
                                </summary>
                                <div class="wiki-panel">
                                    <div class="wiki-grid">
                                        <div><b>Artist</b><span><?= h($hero['artist'] ?: '—') ?></span></div>
                                        <div><b>Character</b><span><?= h($hero['character_name'] ?: '—') ?></span></div>
                                        <div><b>Race</b><span><?= h($hero['race'] ?: '—') ?></span></div>
                                        <div><b>Fetched</b><span><?= h($hero['fetched_at'] ?: '—') ?></span></div>
                                    </div>
                                    <?php if (!empty($hero['wiki_page_url'])): ?>
                                        <a class="wiki-link" href="<?= h($hero['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                    <?php endif; ?>
                                    <?php if (!empty($hero['hero_full_art_url'])): ?>
                                        <a class="wiki-link" href="<?= h($hero['hero_full_art_url']) ?>" target="_blank" rel="noopener">Hero art</a>
                                    <?php endif; ?>
                                    <?php if (!empty($hero['as_hero'])): ?>
                                        <div class="wiki-section"><b>As a hero</b><span><?= h($hero['as_hero']) ?></span></div>
                                    <?php endif; ?>
                                    <?php if (!empty($availability['notes'])): ?>
                                        <div class="wiki-section">
                                            <b>Availability</b>
                                            <ul class="wiki-list">
                                                <?php foreach ($availability['notes'] as $note): ?><li><?= h($note) ?></li><?php endforeach; ?>
                                            </ul>
                                        </div>
                                    <?php endif; ?>
                                    <?php if ($externalLinks): ?>
                                        <div class="wiki-section">
                                            <b>External links</b>
                                            <ul class="wiki-list">
                                                <?php foreach ($externalLinks as $link): ?>
                                                    <li><a href="<?= h($link['url'] ?? '#') ?>" target="_blank" rel="noopener"><?= h($link['label'] ?? $link['url'] ?? 'link') ?></a></li>
                                                <?php endforeach; ?>
                                            </ul>
                                        </div>
                                    <?php endif; ?>
                                </div>
                            </details>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($skins): ?>
                                <?php
                                $skinCards = [];
                                foreach ($skins as $skinGroup) {
                                    foreach (($skinGroup['cards'] ?? []) as $skinCard) {
                                        if (is_array($skinCard)) {
                                            $skinCards[] = $skinCard;
                                        }
                                    }
                                }
                                ?>
                                <?php if ($skinCards): ?>
                                    <details class="media-details">
                                        <summary><?= count($skinCards) ?> skins</summary>
                                        <div class="hero-media-grid">
                                            <?php foreach ($skinCards as $skinCard): ?>
                                                <?php
                                                $skinImage = (string)($skinCard['image_url'] ?? '');
                                                $skinTitle = (string)($skinCard['title'] ?? $skinCard['card_id'] ?? 'Hero skin');
                                                $skinTooltip = trim($skinTitle . "\n" . (string)($skinCard['card_id'] ?? ''));
                                                ?>
                                                <figure class="hero-media-item">
                                                    <?php if ($skinImage !== ''): ?>
                                                        <img
                                                            src="<?= h($skinImage) ?>"
                                                            alt="<?= h($skinTitle) ?>"
                                                            loading="lazy"
                                                            decoding="async"
                                                            tabindex="0"
                                                            role="button"
                                                            data-preview="<?= h($skinImage) ?>"
                                                            data-tooltip="<?= h($skinTooltip) ?>"
                                                        >
                                                    <?php endif; ?>
                                                    <figcaption>
                                                        <?php if (!empty($skinCard['url'])): ?>
                                                            <a href="<?= h($skinCard['url']) ?>" target="_blank" rel="noopener"><?= h(preg_replace('~^Battlegrounds/~', '', $skinTitle)) ?></a>
                                                        <?php else: ?>
                                                            <?= h(preg_replace('~^Battlegrounds/~', '', $skinTitle)) ?>
                                                        <?php endif; ?>
                                                    </figcaption>
                                                </figure>
                                            <?php endforeach; ?>
                                        </div>
                                    </details>
                                <?php else: ?>
                                    <span class="muted-dash">—</span>
                                <?php endif; ?>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($gallery): ?>
                                <details class="media-details"><summary><?= count($gallery) ?> images</summary><div class="hero-media-grid art-grid">
                                    <?php foreach ($gallery as $item): ?>
                                        <?php
                                        $galleryImage = (string)($item['thumb_url'] ?? $item['file_url'] ?? $item['url'] ?? '');
                                        $galleryFull = (string)($item['file_url'] ?? $item['url'] ?? $galleryImage);
                                        $galleryTitle = (string)($item['caption'] ?? $item['file_title'] ?? $item['label'] ?? $item['title'] ?? 'Gallery image');
                                        ?>
                                        <figure class="hero-media-item art-item">
                                            <?php if ($galleryImage !== ''): ?>
                                                <img
                                                    src="<?= h($galleryImage) ?>"
                                                    alt="<?= h($galleryTitle) ?>"
                                                    loading="lazy"
                                                    decoding="async"
                                                    tabindex="0"
                                                    role="button"
                                                    data-preview="<?= h($galleryFull) ?>"
                                                    data-tooltip="<?= h($galleryTitle) ?>"
                                                >
                                            <?php endif; ?>
                                            <figcaption>
                                                <?php if (!empty($item['file_page_url'])): ?>
                                                    <a href="<?= h($item['file_page_url']) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a>
                                                <?php elseif ($galleryFull !== ''): ?>
                                                    <a href="<?= h($galleryFull) ?>" target="_blank" rel="noopener"><?= h($galleryTitle) ?></a>
                                                <?php else: ?>
                                                    <?= h($galleryTitle) ?>
                                                <?php endif; ?>
                                            </figcaption>
                                        </figure>
                                    <?php endforeach; ?>
                                </div></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                        <td class="compact-list-cell">
                            <?php if ($cardChanges): ?>
                                <details><summary><?= count($cardChanges) ?> changes</summary><ul class="wiki-list">
                                    <?php foreach ($cardChanges as $change): ?><li><?= h(compact_text($change)) ?></li><?php endforeach; ?>
                                </ul></details>
                            <?php else: ?><span class="muted-dash">—</span><?php endif; ?>
                        </td>
                    </tr>
                <?php endforeach; ?>
                <?php if (!$heroes): ?>
                    <tr><td colspan="12" class="empty">Герои пока не загружены.</td></tr>
                <?php endif; ?>
                </tbody>
            </table>
            <?php else: ?>
            <table class="battlegrounds-table">
                <thead>
                <tr>
                    <th>Карта</th>
                    <th>Card EN</th>
                    <th>Crop</th>
                    <th>CARD_ID</th>
                    <th>DBF</th>
                    <th>Категория</th>
                    <th>Таверна</th>
                    <th>Тип</th>
                    <th>Атака</th>
                    <th>Здоровье</th>
                    <th>В пуле</th>
                    <th>Дуо</th>
                    <th>Механики</th>
                    <th>Золотая</th>
                    <th>Арт</th>
                    <th>Рамка</th>
                    <th>Wiki</th>
                    <th>Действия</th>
                </tr>
                </thead>
                <tbody>
                <?php foreach ($cards as $card): ?>
                    <?php
                    $tooltip = card_tooltip($card);
                    $cardDbf = $card['dbf'] !== null ? (int)$card['dbf'] : 0;
                    $goldenVariant = $goldenVariantMap[$cardDbf] ?? null;
                    // A golden row is a visual variant of the base card, so its own
                    // technical pool flag must not read as a gameplay restriction.
                    $goldenTooltip = $goldenVariant
                        ? str_replace(' · Не в пуле', '', card_tooltip($goldenVariant))
                        : $tooltip;
                    $cardImage = !empty($card['card_image']) ? versioned_asset($card['card_image'], $card['updated_at']) : '';
                    $goldenImage = $goldenVariant && !empty($goldenVariant['card_image'])
                        ? versioned_asset($goldenVariant['card_image'], $goldenVariant['updated_at'])
                        : (!empty($card['golden_image']) ? versioned_asset($card['golden_image'], $card['updated_at']) : '');
                    $artImage = !empty($card['art_image']) ? versioned_asset($card['art_image'], $card['updated_at']) : '';
                    $framedImage = !empty($card['framed_image']) ? versioned_asset($card['framed_image'], $card['updated_at']) : '';
                    $mechanics = card_mechanics($card['notes'] ?? null);
                    $wikiMeta = $wikiMetaMap[(string)$card['card_id']] ?? null;
                    $wikiMechanics = json_array($wikiMeta['wiki_mechanics_json'] ?? null);
                    $wikiTags = json_array($wikiMeta['wiki_tags_json'] ?? null);
                    $wikiAvailability = json_array($wikiMeta['availability_json'] ?? null);
                    $wikiSounds = json_array($wikiMeta['sounds_json'] ?? null);
                    $wikiExternalLinks = json_array($wikiMeta['external_links_json'] ?? null);
                    $wikiRelatedGroups = json_array($wikiMeta['related_cards_json'] ?? null);
                    $wikiRelatedCardIds = json_array($wikiMeta['related_card_ids_json'] ?? null);
                    $wikiCardChanges = json_array($wikiMeta['card_changes_json'] ?? null);
                    $wikiSoundCount = wiki_sound_count($wikiMeta);
                    $wikiRelatedCount = wiki_related_count($wikiMeta);
                    ?>
                    <tr
                        data-row
                        data-search="<?= h(card_search_text($card)) ?>"
                        data-tier="<?= h($card['tavern_tier']) ?>"
                        data-type="<?= h($card['creature_type']) ?>"
                        data-pool="<?= !empty($card['in_pool']) ? '1' : '0' ?>"
                        data-duos="<?= !empty($card['duos_only']) ? '1' : '0' ?>"
                    >
                        <td class="card-name" title="<?= h($tooltip) ?>">
                            <?php if ($cardImage): ?>
                                <img
                                    src="<?= h($cardImage) ?>"
                                    alt="<?= h($card['name']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    aria-label="Открыть карту <?= h($card['name']) ?> на весь экран"
                                    data-preview="<?= h($cardImage) ?>"
                                    data-tooltip="<?= h($tooltip) ?>"
                                >
                            <?php else: ?>
                                <span class="missing-card-image" title="Нет рендера карты">Нет</span>
                            <?php endif; ?>
                            <span class="card-name-copy">
                                <span><?= h($card['name']) ?></span>
                                <a class="card-stats-link" href="/?action=analytics&amp;stats=card&amp;stats_q=<?= rawurlencode((string)($card['name_en'] ?: $card['name'])) ?>#statistics">Статистика</a>
                            </span>
                        </td>
                        <td class="name-en"><?= h($card['name_en'] ?: '—') ?></td>
                        <td><?= horizontal_art_preview($card['horizontal_image_url'] ?? null, (string)$card['name']) ?: '<span class="muted-dash">—</span>' ?></td>
                        <td><code><?= h($card['card_id']) ?></code></td>
                        <td><?= h($card['dbf']) ?></td>
                        <td><span class="type-badge <?= h($card['card_type'] ?? 'minion') ?>"><?= h(card_type_label($card['card_type'] ?? 'minion')) ?></span></td>
                        <td><?= h($card['tavern_tier']) ?></td>
                        <td><?= h(creature_type_label($card['creature_type'])) ?></td>
                        <td><?= h($card['attack']) ?></td>
                        <td><?= h($card['health']) ?></td>
                        <td>
                            <span class="pool-badge<?= !empty($card['in_pool']) ? '' : ' off' ?>">
                                <?= !empty($card['in_pool']) ? 'Да' : 'Нет' ?>
                            </span>
                        </td>
                        <td>
                            <span class="pool-badge<?= !empty($card['duos_only']) ? ' duos' : ' off' ?>">
                                <?= !empty($card['duos_only']) ? 'Да' : 'Нет' ?>
                            </span>
                        </td>
                        <td class="mechanics-cell">
                            <?php if ($mechanics): ?>
                                <?php foreach ($mechanics as $mechanic): ?>
                                    <span class="mechanic-badge" title="<?= h($mechanic['slug']) ?>"><?= h($mechanic['label']) ?></span>
                                <?php endforeach; ?>
                            <?php else: ?>
                                <span class="muted-dash">—</span>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if ($goldenImage): ?>
                                <img
                                    class="variant-preview"
                                    src="<?= h($goldenImage) ?>"
                                    alt="Золотая версия <?= h($card['name']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="46"
                                    height="70"
                                    tabindex="0"
                                    role="button"
                                    aria-label="Открыть золотую версию <?= h($card['name']) ?> на весь экран"
                                    data-preview="<?= h($goldenImage) ?>"
                                    data-tooltip="<?= h($goldenTooltip . "\nЗолотая / триплет") ?>"
                                >
                            <?php else: ?>
                                <span class="missing-mini">Нет</span>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if ($artImage): ?>
                                <img
                                    class="art-preview variant-preview"
                                    src="<?= h($artImage) ?>"
                                    alt="Арт <?= h($card['name']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="72"
                                    height="48"
                                    tabindex="0"
                                    role="button"
                                    aria-label="Открыть арт <?= h($card['name']) ?> на весь экран"
                                    data-preview="<?= h($artImage) ?>"
                                    data-tooltip="<?= h($tooltip . "\nАрт без рамки") ?>"
                                >
                            <?php else: ?>
                                <span class="missing-mini">Нет</span>
                            <?php endif; ?>
                        </td>
                        <td>
                            <?php if ($framedImage): ?>
                                <img
                                    class="framed-preview"
                                    src="<?= h($framedImage) ?>"
                                    alt="Арт в рамке <?= h($card['name']) ?>"
                                    loading="lazy"
                                    decoding="async"
                                    width="48"
                                    height="56"
                                    tabindex="0"
                                    role="button"
                                    aria-label="Открыть арт в рамке <?= h($card['name']) ?> на весь экран"
                                    data-preview="<?= h($framedImage) ?>"
                                    data-tooltip="<?= h($tooltip . "\nАрт в рамке") ?>"
                                >
                            <?php else: ?>
                                <span class="missing-framed">Нет</span>
                            <?php endif; ?>
                        </td>
                        <td class="wiki-cell">
                            <?php if ($wikiMeta): ?>
                                <details class="wiki-details">
                                    <summary>
                                        <span class="wiki-status<?= h(wiki_status_class($wikiMeta)) ?>"><?= h(wiki_status_label($wikiMeta)) ?></span>
                                        <?php if (($wikiMeta['status'] ?? '') === 'ok'): ?>
                                            <span class="wiki-brief">
                                                <?= h($wikiMeta['artist'] ?: 'без художника') ?>
                                                <?php if (!empty($wikiMeta['race'])): ?> · <?= h($wikiMeta['race']) ?><?php endif; ?>
                                                <?php if ($wikiSoundCount > 0): ?> · <?= $wikiSoundCount ?> зв.<?php endif; ?>
                                                <?php if ($wikiRelatedCount > 0): ?> · <?= $wikiRelatedCount ?> связ.<?php endif; ?>
                                            </span>
                                        <?php endif; ?>
                                    </summary>
                                    <div class="wiki-panel">
                                        <?php if (($wikiMeta['status'] ?? '') !== 'ok'): ?>
                                            <div class="wiki-muted"><?= h($wikiMeta['error'] ?: 'Нет данных wiki для этой карты.') ?></div>
                                        <?php else: ?>
                                            <div class="wiki-grid">
                                                <div><b>Artist</b><span><?= h($wikiMeta['artist'] ?: '—') ?></span></div>
                                                <div><b>Race</b><span><?= h($wikiMeta['race'] ?: '—') ?></span></div>
                                                <div><b>Minion type</b><span><?= h($wikiMeta['minion_type'] ?: '—') ?></span></div>
                                                <div><b>Fetched</b><span><?= h($wikiMeta['fetched_at'] ?: '—') ?></span></div>
                                            </div>

                                            <?php if (!empty($wikiMeta['wiki_page_url'])): ?>
                                                <a class="wiki-link" href="<?= h($wikiMeta['wiki_page_url']) ?>" target="_blank" rel="noopener">Открыть wiki</a>
                                            <?php endif; ?>

                                            <?php if ($wikiMechanics): ?>
                                                <div class="wiki-section">
                                                    <b>Wiki mechanics</b>
                                                    <div class="wiki-tags">
                                                        <?php foreach ($wikiMechanics as $item): ?><span><?= h($item) ?></span><?php endforeach; ?>
                                                    </div>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiTags): ?>
                                                <div class="wiki-section">
                                                    <b>Wiki tags</b>
                                                    <div class="wiki-tags">
                                                        <?php foreach ($wikiTags as $item): ?><span><?= h($item) ?></span><?php endforeach; ?>
                                                    </div>
                                                </div>
                                            <?php endif; ?>

                                            <?php if (!empty($wikiAvailability['notes'])): ?>
                                                <div class="wiki-section">
                                                    <b>Availability</b>
                                                    <ul class="wiki-list">
                                                        <?php foreach ($wikiAvailability['notes'] as $note): ?><li><?= h($note) ?></li><?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiSounds): ?>
                                                <div class="wiki-section">
                                                    <b>Sounds</b>
                                                    <ul class="wiki-list">
                                                        <?php foreach ($wikiSounds as $group): ?>
                                                            <?php foreach (($group['clips'] ?? []) as $clip): ?>
                                                                <li>
                                                                    <span><?= h(($group['heading'] ?? $clip['group'] ?? 'Sound') . ': ' . ($clip['description'] ?? '')) ?></span>
                                                                    <?php if (!empty($clip['file_url'])): ?>
                                                                        <a href="<?= h($clip['file_url']) ?>" target="_blank" rel="noopener"><?= h($clip['file_title'] ?? 'audio') ?></a>
                                                                    <?php endif; ?>
                                                                </li>
                                                            <?php endforeach; ?>
                                                        <?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiExternalLinks): ?>
                                                <div class="wiki-section">
                                                    <b>External links</b>
                                                    <ul class="wiki-list">
                                                        <?php foreach ($wikiExternalLinks as $link): ?>
                                                            <li><a href="<?= h($link['url'] ?? '#') ?>" target="_blank" rel="noopener"><?= h($link['label'] ?? $link['url'] ?? 'link') ?></a></li>
                                                        <?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiRelatedGroups): ?>
                                                <div class="wiki-section">
                                                    <b>Related with</b>
                                                    <ul class="wiki-list">
                                                        <?php foreach ($wikiRelatedGroups as $group): ?>
                                                            <?php foreach (($group['cards'] ?? []) as $related): ?>
                                                                <li>
                                                                    <?= h($group['heading'] ?? 'Related') ?>:
                                                                    <code><?= h(($related['card_id'] ?? '') ?: 'no id') ?></code>
                                                                    <?= h($related['title'] ?? '') ?>
                                                                </li>
                                                            <?php endforeach; ?>
                                                        <?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiRelatedCardIds): ?>
                                                <div class="wiki-section">
                                                    <b>Related IDs</b>
                                                    <div class="wiki-tags">
                                                        <?php foreach ($wikiRelatedCardIds as $relatedId): ?><code><?= h($relatedId) ?></code><?php endforeach; ?>
                                                    </div>
                                                </div>
                                            <?php endif; ?>

                                            <?php if ($wikiCardChanges): ?>
                                                <div class="wiki-section">
                                                    <b>Card changes</b>
                                                    <ul class="wiki-list">
                                                        <?php foreach ($wikiCardChanges as $changeGroup): ?>
                                                            <?php foreach (($changeGroup['entries'] ?? []) as $changeEntry): ?>
                                                                <li>
                                                                    <?= h(($changeGroup['heading'] ?? 'Changes') . ': ' . ($changeEntry['date'] ?? '') . ' ' . ($changeEntry['patch'] ?? '')) ?>
                                                                    <?php if (!empty($changeEntry['patch_url'])): ?><a href="<?= h($changeEntry['patch_url']) ?>" target="_blank" rel="noopener">patch</a><?php endif; ?>
                                                                    <?php if (!empty($changeEntry['items'])): ?>
                                                                        <span><?= h(implode(' ', array_map('strval', $changeEntry['items']))) ?></span>
                                                                    <?php endif; ?>
                                                                </li>
                                                            <?php endforeach; ?>
                                                        <?php endforeach; ?>
                                                    </ul>
                                                </div>
                                            <?php endif; ?>
                                        <?php endif; ?>
                                    </div>
                                </details>
                            <?php else: ?>
                                <span class="wiki-status empty">Нет</span>
                            <?php endif; ?>
                        </td>
                        <td class="row-actions">
                            <a class="mini" href="/?action=edit&id=<?= (int)$card['id'] ?>">Править</a>
                            <form method="post" onsubmit="return confirm('Удалить карту?')">
                                <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
                                <input type="hidden" name="action" value="delete">
                                <input type="hidden" name="id" value="<?= (int)$card['id'] ?>">
                                <button class="mini danger" type="submit">Удалить</button>
                            </form>
                        </td>
                    </tr>
                <?php endforeach; ?>
                <?php if (!$cards): ?>
                    <tr><td colspan="18" class="empty">Карт пока нет.</td></tr>
                <?php endif; ?>
                </tbody>
            </table>
            <?php endif; ?>
        </div>
        <p class="table-scroll-hint"><span aria-hidden="true">←</span> Проведите по таблице в сторону, чтобы увидеть остальные столбцы <span aria-hidden="true">→</span></p>
        <?php if ($totalPages > 1): ?>
            <nav class="pagination bottom" aria-label="Страницы карт">
                <?php if ($page > 1): ?>
                    <a class="page-link page-first" href="<?= h(query_url(['page' => 1])) ?>">Первая</a>
                    <a class="page-link page-prev" href="<?= h(query_url(['page' => $page - 1])) ?>">Назад</a>
                <?php else: ?>
                    <span class="page-link page-first disabled">Первая</span>
                    <span class="page-link page-prev disabled">Назад</span>
                <?php endif; ?>

                <?php if ($pageWindowStart > 1): ?>
                    <a class="page-link" href="<?= h(query_url(['page' => 1])) ?>">1</a>
                    <?php if ($pageWindowStart > 2): ?><span class="page-gap">...</span><?php endif; ?>
                <?php endif; ?>

                <?php for ($i = $pageWindowStart; $i <= $pageWindowEnd; $i++): ?>
                    <?php if ($i === $page): ?>
                        <span class="page-link page-number active" aria-current="page"><?= $i ?></span>
                    <?php else: ?>
                        <a class="page-link page-number" href="<?= h(query_url(['page' => $i])) ?>"><?= $i ?></a>
                    <?php endif; ?>
                <?php endfor; ?>

                <?php if ($pageWindowEnd < $totalPages): ?>
                    <?php if ($pageWindowEnd < $totalPages - 1): ?><span class="page-gap">...</span><?php endif; ?>
                    <a class="page-link" href="<?= h(query_url(['page' => $totalPages])) ?>"><?= $totalPages ?></a>
                <?php endif; ?>

                <?php if ($page < $totalPages): ?>
                    <a class="page-link page-next" href="<?= h(query_url(['page' => $page + 1])) ?>">Вперед</a>
                    <a class="page-link page-last" href="<?= h(query_url(['page' => $totalPages])) ?>">Последняя</a>
                <?php else: ?>
                    <span class="page-link page-next disabled">Вперед</span>
                    <span class="page-link page-last disabled">Последняя</span>
                <?php endif; ?>
                <span class="page-summary">Страница <?= $page ?> из <?= $totalPages ?></span>
            </nav>
        <?php endif; ?>
        <?php endif; ?>
    </section>
    <?php endif; ?>
    </section>
</main>
<div class="card-tooltip" id="cardTooltip" hidden>
    <img src="" alt="">
    <div></div>
</div>
<div class="fullscreen-card" id="fullscreenCard" hidden role="dialog" aria-modal="true" aria-label="Просмотр изображения" aria-describedby="fullscreenMeta" tabindex="-1">
    <button class="fullscreen-close" type="button" data-fullscreen-close>Закрыть</button>
    <img src="" alt="">
    <video controls loop playsinline hidden></video>
    <div class="fullscreen-meta" id="fullscreenMeta"></div>
</div>
<script>
(() => {
    const THEME_KEY = 'bgCardsTheme';
    const themes = new Set(['light', 'dark', 'tavern', 'arcane']);
    const themeButtons = Array.from(document.querySelectorAll('[data-theme-option]'));
    const tooltip = document.getElementById('cardTooltip');
    const tooltipImage = tooltip?.querySelector('img');
    const tooltipText = tooltip?.querySelector('div');
    const fullscreen = document.getElementById('fullscreenCard');
    const fullscreenImage = fullscreen?.querySelector('img');
    const fullscreenVideo = fullscreen?.querySelector('video');
    const fullscreenMeta = fullscreen?.querySelector('.fullscreen-meta');
    const fullscreenClose = fullscreen?.querySelector('[data-fullscreen-close]');
    let lastFullscreenTrigger = null;

    document.querySelectorAll('[data-copy-token]').forEach((button) => {
        button.addEventListener('click', async () => {
            const secret = document.querySelector('[data-token-secret]');
            const status = document.querySelector('[data-copy-token-status]');
            const value = secret?.textContent?.trim() || '';
            if (!value || !navigator.clipboard) {
                if (status) status.textContent = 'Не удалось скопировать автоматически. Выделите токен вручную.';
                return;
            }
            try {
                await navigator.clipboard.writeText(value);
                if (status) status.textContent = 'Токен скопирован.';
                button.textContent = 'Скопировано';
            } catch (error) {
                if (status) status.textContent = 'Браузер запретил копирование. Выделите токен вручную.';
            }
        });
    });

    const saveTheme = (theme) => {
        try {
            window.localStorage.setItem(THEME_KEY, theme);
        } catch (error) {
            // The panel still works if localStorage is unavailable.
        }
    };

    const readTheme = () => {
        try {
            return window.localStorage.getItem(THEME_KEY) || 'dark';
        } catch (error) {
            return 'dark';
        }
    };

    const setTheme = (theme, persist = true) => {
        const nextTheme = themes.has(theme) ? theme : 'light';
        document.documentElement.dataset.theme = nextTheme;
        themeButtons.forEach((button) => {
            const active = button.dataset.themeOption === nextTheme;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        if (persist) saveTheme(nextTheme);
    };

    themeButtons.forEach((button) => {
        button.addEventListener('click', () => setTheme(button.dataset.themeOption || 'light'));
    });
    setTheme(readTheme(), false);

    const sidebar = document.querySelector('.sidebar');
    const sidebarToggle = document.querySelector('[data-sidebar-toggle]');
    sidebarToggle?.addEventListener('click', () => {
        const expanded = sidebar?.classList.toggle('nav-open') || false;
        sidebarToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    });

    const termsPage = document.querySelector('[data-terms-page]');
    if (termsPage) {
        const termSearch = termsPage.querySelector('[data-term-filter]');
        const statusButtons = Array.from(termsPage.querySelectorAll('[data-term-status]'));
        const termRows = Array.from(termsPage.querySelectorAll('[data-term-row]'));
        let activeTermStatus = 'all';

        const applyTermFilters = () => {
            const query = (termSearch?.value || '').trim().toLocaleLowerCase('ru-RU');
            termRows.forEach((row) => {
                const rowStatus = row.dataset.termStatus || 'missing';
                const rowText = row.dataset.termText || '';
                const statusMatch = activeTermStatus === 'all' || rowStatus === activeTermStatus;
                const textMatch = query === '' || rowText.includes(query);
                row.hidden = !(statusMatch && textMatch);
            });
        };

        statusButtons.forEach((button) => {
            button.addEventListener('click', () => {
                activeTermStatus = button.dataset.termStatus || 'all';
                statusButtons.forEach((item) => item.classList.toggle('active', item === button));
                applyTermFilters();
            });
        });
        termSearch?.addEventListener('input', applyTermFilters);
    }

    document.querySelectorAll('[data-autofilter]').forEach((form) => {
        const search = form.querySelector('[data-filter-search]');
        let searchTimer = 0;
        const clearPage = () => {
            const page = form.querySelector('input[name="page"]');
            if (page) page.remove();
        };
        const submitFilters = () => {
            clearPage();
            form.requestSubmit();
        };

        form.querySelectorAll('select').forEach((select) => {
            select.addEventListener('change', submitFilters);
        });
        search?.addEventListener('input', () => {
            window.clearTimeout(searchTimer);
            const value = search.value.trim();
            if (value !== '' && value.length < 2) return;
            searchTimer = window.setTimeout(submitFilters, 520);
        });
        form.addEventListener('submit', clearPage);
    });

    document.querySelectorAll('[data-filter-toggle]').forEach((button) => {
        const controls = button.closest('.filter-controls');
        button.addEventListener('click', () => {
            const expanded = controls?.classList.toggle('filters-open') || false;
            button.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        });
    });

    document.querySelectorAll('[data-table-density]').forEach((button) => {
        const workspace = button.closest('.data-panel, .analytics-hub, .token-list-panel');
        const storageKey = button.dataset.densityKey || (workspace?.classList.contains('analytics-hub')
            ? 'analyticsTableDensity'
            : 'catalogueTableDensity');
        const setDensity = (compact) => {
            workspace?.classList.toggle('is-compact-table', compact);
            button.setAttribute('aria-pressed', compact ? 'true' : 'false');
            button.textContent = compact ? 'Обычно' : 'Компактно';
        };
        try {
            setDensity(window.localStorage.getItem(storageKey) === 'compact');
        } catch (error) {
            setDensity(false);
        }
        button.addEventListener('click', () => {
            const compact = !workspace?.classList.contains('is-compact-table');
            setDensity(compact);
            try {
                window.localStorage.setItem(storageKey, compact ? 'compact' : 'normal');
            } catch (error) {
                // Density remains usable for this page when storage is unavailable.
            }
        });
    });

    // A slash focuses the catalogue search without stealing normal form input.
    const primarySearch = document.querySelector('[data-filter-search]');
    document.addEventListener('keydown', (event) => {
        if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
        const target = event.target;
        if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return;
        event.preventDefault();
        primarySearch?.focus();
    });

    const moveTooltip = (event) => {
        if (!tooltip) return;
        const gap = 18;
        const rect = tooltip.getBoundingClientRect();
        let left = event.clientX + gap;
        let top = event.clientY + gap;
        if (left + rect.width > window.innerWidth - 12) left = event.clientX - rect.width - gap;
        if (top + rect.height > window.innerHeight - 12) top = window.innerHeight - rect.height - 12;
        tooltip.style.left = `${Math.max(12, left)}px`;
        tooltip.style.top = `${Math.max(12, top)}px`;
    };

    let tooltipTarget = null;
    const previewImageFromEvent = (event) => {
        const target = event.target;
        if (!(target instanceof Element)) return null;
        return target.closest('[data-preview]');
    };

    const showTooltip = (image, event) => {
        if (!tooltip || !tooltipImage || !tooltipText || tooltipTarget === image) return;
        tooltipTarget = image;
        if (image.dataset.previewType === 'video') {
            tooltipImage.src = '';
            tooltipImage.hidden = true;
        } else {
            tooltipImage.hidden = false;
            tooltipImage.src = image.dataset.preview || '';
        }
        tooltipText.textContent = image.dataset.tooltip || '';
        tooltip.hidden = false;
        moveTooltip(event);
    };

    const hideTooltip = () => {
        if (!tooltip || !tooltipImage) return;
        tooltipTarget = null;
        tooltip.hidden = true;
        tooltipImage.src = '';
        tooltipImage.hidden = false;
    };

    const openFullscreen = (image) => {
        if (!fullscreen || !fullscreenImage || !fullscreenVideo || !fullscreenMeta) return;
        hideTooltip();
        lastFullscreenTrigger = image instanceof HTMLElement ? image : null;
        const preview = image.dataset.preview || image.src;
        const isVideo = image.dataset.previewType === 'video' || /\.(webm|mp4)(?:\?|$)/i.test(preview);
        fullscreenImage.hidden = isVideo;
        fullscreenVideo.hidden = !isVideo;
        if (isVideo) {
            fullscreenImage.src = '';
            fullscreenVideo.src = preview;
            fullscreenVideo.play().catch(() => {});
        } else {
            fullscreenVideo.pause();
            fullscreenVideo.removeAttribute('src');
            fullscreenVideo.load();
            fullscreenImage.src = preview;
            fullscreenImage.alt = image.alt || 'Карта';
        }
        fullscreenMeta.textContent = image.dataset.tooltip || '';
        fullscreen.hidden = false;
        document.body.classList.add('modal-open');
        fullscreenClose?.focus({preventScroll: true});
    };

    const closeFullscreen = () => {
        if (!fullscreen || !fullscreenImage || !fullscreenVideo || !fullscreenMeta) return;
        fullscreen.hidden = true;
        fullscreenImage.src = '';
        fullscreenImage.hidden = false;
        fullscreenVideo.pause();
        fullscreenVideo.removeAttribute('src');
        fullscreenVideo.load();
        fullscreenVideo.hidden = true;
        fullscreenMeta.textContent = '';
        document.body.classList.remove('modal-open');
        const trigger = lastFullscreenTrigger;
        lastFullscreenTrigger = null;
        trigger?.focus({preventScroll: true});
    };

    document.addEventListener('mouseover', (event) => {
        const image = previewImageFromEvent(event);
        if (image) showTooltip(image, event);
    });
    document.addEventListener('mousemove', (event) => {
        if (tooltipTarget) moveTooltip(event);
    });
    document.addEventListener('mouseout', (event) => {
        if (!tooltipTarget) return;
        const nextTarget = event.relatedTarget;
        if (nextTarget instanceof Node && tooltipTarget.contains(nextTarget)) return;
        hideTooltip();
    });
    document.addEventListener('click', (event) => {
        const image = previewImageFromEvent(event);
        if (image) {
            event.preventDefault();
            openFullscreen(image);
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const image = previewImageFromEvent(event);
        if (!image) return;
        event.preventDefault();
        openFullscreen(image);
    });
    fullscreen?.addEventListener('click', (event) => {
        if (event.target === fullscreen || event.target.closest('[data-fullscreen-close]')) {
            closeFullscreen();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && fullscreen && !fullscreen.hidden) {
            closeFullscreen();
        }
        if (event.key === 'Tab' && fullscreen && !fullscreen.hidden) {
            const focusable = Array.from(fullscreen.querySelectorAll('button:not([disabled]), video:not([hidden])'))
                .filter((element) => element instanceof HTMLElement && element.offsetParent !== null);
            if (!focusable.length) {
                event.preventDefault();
                fullscreen.focus({preventScroll: true});
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    });
})();
</script>
<?php require __DIR__ . '/partials/command-palette.php'; ?>
</body>
</html>
