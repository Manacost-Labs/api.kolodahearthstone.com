<?php
declare(strict_types=1);

$config = require __DIR__ . '/../config.php';

const API_BASE_URL = 'https://api.kolodahearthstone.com';
const API_VERSION = '1.14.0';
const DEFAULT_PER_PAGE = 50;
const MAX_PER_PAGE = 200;

header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, HEAD, OPTIONS');
header('Access-Control-Allow-Headers: Accept, Content-Type');
header('X-Content-Type-Options: nosniff');

if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') {
    http_response_code(204);
    exit;
}

if (!in_array(($_SERVER['REQUEST_METHOD'] ?? 'GET'), ['GET', 'HEAD'], true)) {
    respond_error('method_not_allowed', 'API поддерживает только GET, HEAD и OPTIONS-запросы.', 405);
}

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

function card_type_label($type): ?string
{
    $type = (string)$type;
    $types = card_types();

    return $types[$type] ?? null;
}

function creature_type_label($type): ?string
{
    $type = (string)$type;
    $types = creature_types();

    return $types[$type] ?? null;
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

function mechanic_label(string $mechanic): string
{
    $labels = mechanic_labels();

    return $labels[$mechanic] ?? $mechanic;
}

function mechanics_from_notes(?string $notes): array
{
    if ($notes === null || $notes === '') {
        return [];
    }
    if (!preg_match('/Механики:\s*([^\r\n]+)/u', $notes, $matches)) {
        return [];
    }

    $mechanics = [];
    foreach (explode(',', $matches[1]) as $rawMechanic) {
        $mechanic = trim($rawMechanic);
        if ($mechanic === '') {
            continue;
        }
        $mechanics[$mechanic] = [
            'slug' => $mechanic,
            'name_ru' => mechanic_label($mechanic),
        ];
    }

    return array_values($mechanics);
}

function respond(array $payload, int $status = 200): void
{
    http_response_code($status);
    if (!is_head_request()) {
        echo encode_json($payload);
    }
    exit;
}

function is_head_request(): bool
{
    return ($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'HEAD';
}

function encode_json(array $payload): string
{
    $json = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
    if (!is_string($json)) {
        respond_error('internal_error', 'Не удалось сериализовать JSON.', 500);
    }

    return $json;
}

function http_date(?string $mysqlDate): ?string
{
    if ($mysqlDate === null || $mysqlDate === '') {
        return null;
    }

    $date = DateTimeImmutable::createFromFormat('Y-m-d H:i:s', $mysqlDate, new DateTimeZone('UTC'));
    if (!$date) {
        $date = new DateTimeImmutable($mysqlDate, new DateTimeZone('UTC'));
    }

    return $date->setTimezone(new DateTimeZone('GMT'))->format(DATE_RFC7231);
}

function timestamp_from_http_date(?string $httpDate): ?int
{
    if ($httpDate === null || trim($httpDate) === '') {
        return null;
    }

    $timestamp = strtotime($httpDate);

    return $timestamp === false ? null : $timestamp;
}

function request_etag_matches(string $etag): bool
{
    $header = $_SERVER['HTTP_IF_NONE_MATCH'] ?? '';
    if (!is_string($header) || trim($header) === '') {
        return false;
    }
    foreach (explode(',', $header) as $candidate) {
        $candidate = trim($candidate);
        if ($candidate === '*' || $candidate === $etag || $candidate === 'W/' . $etag) {
            return true;
        }
    }

    return false;
}

function respond_cached(array $payload, ?string $lastModified = null, int $maxAge = 300): void
{
    $json = encode_json($payload);
    $etag = '"' . hash('sha256', $json) . '"';
    $lastModifiedHttp = http_date($lastModified);

    header('Cache-Control: public, max-age=' . $maxAge . ', stale-while-revalidate=60');
    header('ETag: ' . $etag);
    if ($lastModifiedHttp !== null) {
        header('Last-Modified: ' . $lastModifiedHttp);
    }

    $notModified = request_etag_matches($etag);
    if (!$notModified && $lastModifiedHttp !== null) {
        $clientTimestamp = timestamp_from_http_date($_SERVER['HTTP_IF_MODIFIED_SINCE'] ?? null);
        $serverTimestamp = timestamp_from_http_date($lastModifiedHttp);
        $notModified = $clientTimestamp !== null && $serverTimestamp !== null && $clientTimestamp >= $serverTimestamp;
    }

    if ($notModified) {
        http_response_code(304);
        exit;
    }

    http_response_code(200);
    if (!is_head_request()) {
        echo $json;
    }
    exit;
}

function respond_error(string $code, string $message, int $status = 400, array $details = []): void
{
    respond([
        'error' => array_filter([
            'code' => $code,
            'message' => $message,
            'details' => $details ?: null,
        ], static function ($value) {
            return $value !== null;
        }),
    ], $status);
}

function bool_param(?string $value, string $name): ?int
{
    if ($value === null || $value === '') {
        return null;
    }
    if (!in_array($value, ['0', '1'], true)) {
        respond_error('invalid_parameter', "Параметр {$name} должен быть 0 или 1.");
    }

    return (int)$value;
}

function int_param(?string $value, string $name, ?int $min = null, ?int $max = null): ?int
{
    if ($value === null || $value === '') {
        return null;
    }
    if (!preg_match('/^-?\d+$/', $value)) {
        respond_error('invalid_parameter', "Параметр {$name} должен быть целым числом.");
    }

    $int = (int)$value;
    if ($min !== null && $int < $min) {
        respond_error('invalid_parameter', "Параметр {$name} должен быть не меньше {$min}.");
    }
    if ($max !== null && $int > $max) {
        respond_error('invalid_parameter', "Параметр {$name} должен быть не больше {$max}.");
    }

    return $int;
}

function datetime_param(?string $value, string $name): ?string
{
    if ($value === null || trim($value) === '') {
        return null;
    }

    try {
        $date = new DateTimeImmutable(trim($value), new DateTimeZone('UTC'));
    } catch (Throwable $e) {
        respond_error('invalid_parameter', "Параметр {$name} должен быть датой ISO 8601, например 2026-06-12T00:00:00Z.");
    }

    return $date->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i:s');
}

function absolute_url(?string $url, ?string $version = null): ?string
{
    $url = trim((string)$url);
    if ($url === '') {
        return null;
    }
    if (preg_match('~^https?://~i', $url)) {
        return $url;
    }

    $absolute = API_BASE_URL . '/' . ltrim($url, '/');
    $version = trim((string)$version);
    if ($version === '') {
        return $absolute;
    }

    return $absolute . (strpos($absolute, '?') === false ? '?' : '&') . 'v=' . rawurlencode($version);
}

function attach_horizontal_art(PDO $pdo, array $rows, string $entityType, callable $entityId): array
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
        $name = 'horizontal_id_' . $index;
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
            $assets[(string)$asset['entity_id']] = absolute_url(
                (string)$asset['local_image_url'],
                $asset['generated_at'] ?? null
            );
        }
    } catch (Throwable $e) {
        $assets = [];
    }

    foreach ($rows as &$row) {
        $id = trim((string)$entityId($row));
        $row['horizontal_image_url'] = $assets[$id] ?? null;
    }
    unset($row);

    return $rows;
}

function attach_horizontal_art_one(PDO $pdo, array $row, string $entityType, callable $entityId): array
{
    return attach_horizontal_art($pdo, [$row], $entityType, $entityId)[0];
}

function json_field($value, array $default = []): array
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

function load_wiki_term_translations(PDO $pdo): array
{
    $translations = ['mechanic' => [], 'tag' => []];
    try {
        $stmt = $pdo->query("
            SELECT term_type, term_en, term_ru
            FROM battlegrounds_wiki_term_translations
            WHERE term_ru IS NOT NULL AND term_ru <> ''
        ");
    } catch (Throwable $e) {
        return $translations;
    }

    foreach ($stmt->fetchAll() as $row) {
        $type = (string)($row['term_type'] ?? '');
        $termEn = (string)($row['term_en'] ?? '');
        $termRu = (string)($row['term_ru'] ?? '');
        if (!isset($translations[$type]) || $termEn === '' || $termRu === '') {
            continue;
        }
        $translations[$type][$termEn] = $termRu;
    }

    return $translations;
}

function localized_wiki_terms(array $terms, array $translations, string $type): array
{
    $typeTranslations = $translations[$type] ?? [];
    $localized = [];
    foreach ($terms as $term) {
        $termEn = trim((string)$term);
        if ($termEn === '') {
            continue;
        }
        $localized[] = [
            'name_en' => $termEn,
            'name_ru' => isset($typeTranslations[$termEn]) && $typeTranslations[$termEn] !== '' ? (string)$typeTranslations[$termEn] : null,
        ];
    }

    return $localized;
}

function wiki_meta_to_api(?array $meta, array $termTranslations = []): ?array
{
    if (!$meta) {
        return null;
    }

    $status = (string)($meta['status'] ?? 'ok');
    $wikiMechanics = json_field($meta['wiki_mechanics_json'] ?? null);
    $wikiTags = json_field($meta['wiki_tags_json'] ?? null);
    $payload = [
        'status' => $status,
        'source' => 'hearthstone.wiki.gg',
        'page' => [
            'title' => $meta['wiki_page_title'] !== null && $meta['wiki_page_title'] !== '' ? (string)$meta['wiki_page_title'] : null,
            'url' => $meta['wiki_page_url'] !== null && $meta['wiki_page_url'] !== '' ? (string)$meta['wiki_page_url'] : null,
        ],
        'artist' => $meta['artist'] !== null && $meta['artist'] !== '' ? (string)$meta['artist'] : null,
        'race' => $meta['race'] !== null && $meta['race'] !== '' ? (string)$meta['race'] : null,
        'minion_type' => $meta['minion_type'] !== null && $meta['minion_type'] !== '' ? (string)$meta['minion_type'] : null,
        'wiki_mechanics' => $wikiMechanics,
        'wiki_mechanics_localized' => localized_wiki_terms($wikiMechanics, $termTranslations, 'mechanic'),
        'wiki_tags' => $wikiTags,
        'wiki_tags_localized' => localized_wiki_terms($wikiTags, $termTranslations, 'tag'),
        'availability' => json_field($meta['availability_json'] ?? null, [
            'formats' => [],
            'exclusions' => [],
            'notes' => [],
            'page_entries' => [],
        ]),
        'sounds' => json_field($meta['sounds_json'] ?? null),
        'external_links' => json_field($meta['external_links_json'] ?? null),
        'related_cards' => json_field($meta['related_cards_json'] ?? null),
        'related_card_ids' => json_field($meta['related_card_ids_json'] ?? null),
        'card_changes' => json_field($meta['card_changes_json'] ?? null),
        'fetched_at' => $meta['fetched_at'] !== null ? (string)$meta['fetched_at'] : null,
        'changed_at' => $meta['changed_at'] !== null ? (string)$meta['changed_at'] : null,
    ];

    if ($status !== 'ok') {
        $payload['error'] = $meta['error'] !== null && $meta['error'] !== '' ? (string)$meta['error'] : null;
    }

    return $payload;
}

function include_wiki_requested(): bool
{
    $include = strtolower((string)($_GET['include'] ?? ''));
    if ($include === '') {
        return false;
    }

    return in_array('wiki', array_filter(array_map('trim', explode(',', $include))), true);
}

function load_wiki_meta_map(PDO $pdo, array $cardIds): array
{
    $cardIds = array_values(array_unique(array_filter(array_map('strval', $cardIds))));
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

function load_wiki_meta(PDO $pdo, string $cardId): ?array
{
    $stmt = $pdo->prepare('SELECT * FROM battlegrounds_card_wiki_meta WHERE card_id = :card_id LIMIT 1');
    $stmt->execute(['card_id' => $cardId]);
    $row = $stmt->fetch();

    return $row ?: null;
}

function load_constructed_wiki_meta_map(PDO $pdo, array $cardIds): array
{
    $cardIds = array_values(array_unique(array_filter(array_map('strval', $cardIds))));
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

function load_constructed_wiki_meta(PDO $pdo, string $cardId): ?array
{
    $stmt = $pdo->prepare('SELECT * FROM constructed_card_wiki_meta WHERE card_id = :card_id LIMIT 1');
    $stmt->execute(['card_id' => $cardId]);
    $row = $stmt->fetch();

    return $row ?: null;
}

function max_timestamp(?string $current, ?string $candidate): ?string
{
    if ($candidate === null || $candidate === '') {
        return $current;
    }
    if ($current === null || $current === '') {
        return $candidate;
    }

    $currentTime = strtotime($current);
    $candidateTime = strtotime($candidate);
    if ($currentTime === false || $candidateTime === false) {
        return $current;
    }

    return $candidateTime > $currentTime ? $candidate : $current;
}

function golden_variant_to_api(array $card): array
{
    $imageVersion = (string)($card['updated_at'] ?? '');

    return [
        'card_id' => (string)$card['card_id'],
        'dbf' => $card['dbf'] !== null ? (int)$card['dbf'] : null,
        'name' => [
            'ru' => (string)$card['name'],
            'en' => $card['name_en'] !== null && $card['name_en'] !== '' ? (string)$card['name_en'] : null,
        ],
        'attack' => $card['attack'] !== null ? (int)$card['attack'] : null,
        'health' => $card['health'] !== null ? (int)$card['health'] : null,
        'mechanics' => mechanics_from_notes($card['notes'] ?? null),
        'text_ru' => $card['notes'] !== null && $card['notes'] !== '' ? (string)$card['notes'] : null,
        'images' => [
            'card' => absolute_url($card['card_image'] ?? null, $imageVersion),
            'art' => absolute_url($card['art_image'] ?? null, $imageVersion),
            'framed' => absolute_url($card['framed_image'] ?? null, $imageVersion),
            'horizontal' => $card['horizontal_image_url'] ?? null,
        ],
        'updated_at' => (string)$card['updated_at'],
    ];
}

function load_golden_variant_map(PDO $pdo, array $cards): array
{
    $baseDbfs = [];
    foreach ($cards as $card) {
        if (($card['variant_kind'] ?? 'base') === 'base' && $card['dbf'] !== null) {
            $baseDbfs[] = (int)$card['dbf'];
        }
    }
    $baseDbfs = array_values(array_unique($baseDbfs));
    if (!$baseDbfs) {
        return [];
    }

    $placeholders = implode(',', array_fill(0, count($baseDbfs), '?'));
    $stmt = $pdo->prepare(
        "SELECT * FROM battlegrounds_cards WHERE variant_kind = 'golden' AND base_dbf IN ($placeholders)"
    );
    $stmt->execute($baseDbfs);

    $variants = attach_horizontal_art(
        $pdo,
        $stmt->fetchAll(),
        'battleground_card',
        static fn(array $row): string => (string)$row['card_id']
    );
    $map = [];
    foreach ($variants as $row) {
        $map[(int)$row['base_dbf']] = $row;
    }
    return $map;
}

function card_to_api(
    array $card,
    ?array $wikiMeta = null,
    bool $includeWiki = false,
    array $termTranslations = [],
    ?array $goldenVariant = null
): array
{
    $creatureType = $card['creature_type'] !== null ? (string)$card['creature_type'] : null;
    $imageVersion = (string)($card['updated_at'] ?? '');

    $payload = [
        'id' => (int)$card['id'],
        'card_id' => (string)$card['card_id'],
        'dbf' => $card['dbf'] !== null ? (int)$card['dbf'] : null,
        'variant' => [
            'kind' => (string)($card['variant_kind'] ?? 'base'),
            'base_card_id' => $card['base_card_id'] !== null && $card['base_card_id'] !== '' ? (string)$card['base_card_id'] : null,
            'base_dbf' => $card['base_dbf'] !== null ? (int)$card['base_dbf'] : null,
            'premium_dbf' => $card['premium_dbf'] !== null ? (int)$card['premium_dbf'] : null,
        ],
        'card_type' => [
            'slug' => (string)($card['card_type'] ?? 'minion'),
            'name_ru' => card_type_label($card['card_type'] ?? 'minion') ?? 'Существо',
        ],
        'name' => [
            'ru' => (string)$card['name'],
            'en' => $card['name_en'] !== null && $card['name_en'] !== '' ? (string)$card['name_en'] : null,
        ],
        'tavern_tier' => $card['tavern_tier'] !== null ? (int)$card['tavern_tier'] : null,
        'creature_type' => $creatureType ? [
            'slug' => $creatureType,
            'name_ru' => creature_type_label($creatureType),
        ] : null,
        'attack' => $card['attack'] !== null ? (int)$card['attack'] : null,
        'health' => $card['health'] !== null ? (int)$card['health'] : null,
        'in_pool' => (bool)$card['in_pool'],
        'duos_only' => (bool)$card['duos_only'],
        'mechanics' => mechanics_from_notes($card['notes'] ?? null),
        'text_ru' => $card['notes'] !== null && $card['notes'] !== '' ? (string)$card['notes'] : null,
        'images' => [
            'card' => absolute_url($card['card_image'] ?? null, $imageVersion),
            'golden' => absolute_url($card['golden_image'] ?? null, $imageVersion),
            'art' => absolute_url($card['art_image'] ?? null, $imageVersion),
            'framed' => absolute_url($card['framed_image'] ?? null, $imageVersion),
            'horizontal' => $card['horizontal_image_url'] ?? null,
        ],
        'wiki_page' => $wikiMeta ? [
            'title' => $wikiMeta['wiki_page_title'] !== null && $wikiMeta['wiki_page_title'] !== '' ? (string)$wikiMeta['wiki_page_title'] : null,
            'url' => $wikiMeta['wiki_page_url'] !== null && $wikiMeta['wiki_page_url'] !== '' ? (string)$wikiMeta['wiki_page_url'] : null,
        ] : null,
        'created_at' => (string)$card['created_at'],
        'updated_at' => (string)$card['updated_at'],
    ];

    if ($goldenVariant !== null) {
        $payload['golden_variant'] = golden_variant_to_api($goldenVariant);
    } else {
        $payload['golden_variant'] = null;
    }

    if ($includeWiki) {
        $payload['wiki'] = wiki_meta_to_api($wikiMeta, $termTranslations);
    }

    return $payload;
}

function constructed_card_type_label(?string $type): string
{
    $labels = [
        'minion' => 'Существо',
        'spell' => 'Заклинание',
        'weapon' => 'Оружие',
        'hero' => 'Герой',
        'hero_power' => 'Сила героя',
        'location' => 'Локация',
    ];
    $type = $type !== null ? strtolower($type) : '';

    return $labels[$type] ?? ($type !== '' ? $type : 'Карта');
}

function constructed_formats_from_csv(?string $formats): array
{
    $labels = [
        'standard' => ['slug' => 'standard', 'name_ru' => 'Стандартный', 'name_en' => 'Standard'],
        'wild' => ['slug' => 'wild', 'name_ru' => 'Вольный', 'name_en' => 'Wild'],
    ];
    $result = [];
    foreach (array_filter(array_map('trim', explode(',', (string)$formats))) as $slug) {
        $result[] = $labels[$slug] ?? ['slug' => $slug, 'name_ru' => $slug, 'name_en' => $slug];
    }

    return $result;
}

function constructed_wiki_meta_to_api(?array $meta, array $termTranslations = []): ?array
{
    if (!$meta) {
        return null;
    }

    $status = (string)($meta['status'] ?? 'ok');
    $wikiMechanics = json_field($meta['wiki_mechanics_json'] ?? null);
    $wikiTags = json_field($meta['wiki_tags_json'] ?? null);
    $payload = [
        'status' => $status,
        'source' => 'hearthstone.wiki.gg',
        'page' => [
            'title' => $meta['wiki_page_title'] !== null && $meta['wiki_page_title'] !== '' ? (string)$meta['wiki_page_title'] : null,
            'url' => $meta['wiki_page_url'] !== null && $meta['wiki_page_url'] !== '' ? (string)$meta['wiki_page_url'] : null,
        ],
        'wiki_mechanics' => $wikiMechanics,
        'wiki_mechanics_localized' => localized_wiki_terms($wikiMechanics, $termTranslations, 'mechanic'),
        'wiki_tags' => $wikiTags,
        'wiki_tags_localized' => localized_wiki_terms($wikiTags, $termTranslations, 'tag'),
        'ban_lists' => json_field($meta['ban_lists_json'] ?? null),
        'gallery' => json_field($meta['gallery_json'] ?? null),
        'patch_changes' => json_field($meta['patch_changes_json'] ?? null),
        'external_links' => json_field($meta['external_links_json'] ?? null),
        'related_cards' => json_field($meta['related_cards_json'] ?? null),
        'related_card_ids' => json_field($meta['related_card_ids_json'] ?? null),
        'generated_card_pools' => json_field($meta['generated_card_pools_json'] ?? null),
        'generated_card_ids' => json_field($meta['generated_card_ids_json'] ?? null),
        'sounds' => json_field($meta['sounds_json'] ?? null),
        'golden_cards' => json_field($meta['golden_cards_json'] ?? null),
        'signature_cards' => json_field($meta['signature_cards_json'] ?? null),
        'diamond_cards' => json_field($meta['diamond_cards_json'] ?? null),
        'animated' => [
            'golden' => json_field($meta['golden_animated_json'] ?? null),
            'signature' => json_field($meta['signature_animated_json'] ?? null),
            'diamond' => json_field($meta['diamond_animated_json'] ?? null),
        ],
        'fetched_at' => $meta['fetched_at'] !== null ? (string)$meta['fetched_at'] : null,
        'changed_at' => $meta['changed_at'] !== null ? (string)$meta['changed_at'] : null,
    ];

    if ($status !== 'ok') {
        $payload['error'] = $meta['error'] !== null && $meta['error'] !== '' ? (string)$meta['error'] : null;
    }

    return $payload;
}

function constructed_card_to_api(
    array $card,
    ?array $wikiMeta = null,
    bool $includeWiki = false,
    array $termTranslations = [],
    array $relatedGroups = []
): array
{
    $cropImage = $card['local_crop_image_url'] !== null && $card['local_crop_image_url'] !== ''
        ? absolute_url($card['local_crop_image_url'], $card['updated_at'] ?? null)
        : ($card['crop_image_url'] !== null && $card['crop_image_url'] !== '' ? (string)$card['crop_image_url'] : null);
    $wikiFullArt = $card['local_wiki_full_art_url'] !== null && $card['local_wiki_full_art_url'] !== ''
        ? absolute_url($card['local_wiki_full_art_url'], $card['wiki_full_art_fetched_at'] ?? $card['updated_at'] ?? null)
        : ($card['wiki_full_art_url'] !== null && $card['wiki_full_art_url'] !== '' ? (string)$card['wiki_full_art_url'] : null);
    $payload = [
        'card_id' => (string)$card['card_id'],
        'dbf' => $card['dbf'] !== null ? (int)$card['dbf'] : null,
        'slug' => $card['slug'] !== null && $card['slug'] !== '' ? (string)$card['slug'] : null,
        'collectible' => (bool)$card['collectible'],
        'formats' => constructed_formats_from_csv($card['formats'] ?? null),
        'name' => [
            'ru' => $card['name_ru'] !== null && $card['name_ru'] !== '' ? (string)$card['name_ru'] : null,
            'en' => $card['name_en'] !== null && $card['name_en'] !== '' ? (string)$card['name_en'] : null,
        ],
        'text' => [
            'ru' => $card['text_ru'] !== null && $card['text_ru'] !== '' ? (string)$card['text_ru'] : null,
            'en' => $card['text_en'] !== null && $card['text_en'] !== '' ? (string)$card['text_en'] : null,
        ],
        'flavor' => [
            'ru' => $card['flavor_ru'] !== null && $card['flavor_ru'] !== '' ? (string)$card['flavor_ru'] : null,
            'en' => $card['flavor_en'] !== null && $card['flavor_en'] !== '' ? (string)$card['flavor_en'] : null,
        ],
        'card_set' => $card['card_set'] !== null && $card['card_set'] !== '' ? (string)$card['card_set'] : null,
        'card_type' => [
            'slug' => $card['card_type'] !== null && $card['card_type'] !== '' ? (string)$card['card_type'] : null,
            'name_ru' => constructed_card_type_label($card['card_type'] !== null ? (string)$card['card_type'] : null),
        ],
        'rarity' => $card['rarity'] !== null && $card['rarity'] !== '' ? (string)$card['rarity'] : null,
        'class' => $card['class_slug'] !== null && $card['class_slug'] !== '' ? (string)$card['class_slug'] : null,
        'multi_class' => json_field($card['multi_class_json'] ?? null),
        'minion_type' => $card['minion_type'] !== null && $card['minion_type'] !== '' ? (string)$card['minion_type'] : null,
        'spell_school' => $card['spell_school'] !== null && $card['spell_school'] !== '' ? (string)$card['spell_school'] : null,
        'mana_cost' => $card['mana_cost'] !== null ? (int)$card['mana_cost'] : null,
        'attack' => $card['attack'] !== null ? (int)$card['attack'] : null,
        'health' => $card['health'] !== null ? (int)$card['health'] : null,
        'durability' => $card['durability'] !== null ? (int)$card['durability'] : null,
        'armor' => $card['armor'] !== null ? (int)$card['armor'] : null,
        'artist' => $card['artist'] !== null && $card['artist'] !== '' ? (string)$card['artist'] : null,
        'images' => [
            'card' => $card['local_image_url'] !== null && $card['local_image_url'] !== '' ? absolute_url($card['local_image_url'], $card['updated_at'] ?? null) : ($card['image_url'] !== null && $card['image_url'] !== '' ? (string)$card['image_url'] : null),
            'art' => $wikiFullArt,
            'art_metadata' => $wikiFullArt ? [
                'source' => 'hearthstone.wiki.gg',
                'file_title' => $card['wiki_full_art_title'] !== null && $card['wiki_full_art_title'] !== '' ? (string)$card['wiki_full_art_title'] : null,
                'file_page_url' => $card['wiki_full_art_file_page_url'] !== null && $card['wiki_full_art_file_page_url'] !== '' ? (string)$card['wiki_full_art_file_page_url'] : null,
                'width' => $card['wiki_full_art_width'] !== null ? (int)$card['wiki_full_art_width'] : null,
                'height' => $card['wiki_full_art_height'] !== null ? (int)$card['wiki_full_art_height'] : null,
                'size_bytes' => $card['wiki_full_art_size'] !== null ? (int)$card['wiki_full_art_size'] : null,
                'sha1' => $card['wiki_full_art_sha1'] !== null && $card['wiki_full_art_sha1'] !== '' ? (string)$card['wiki_full_art_sha1'] : null,
                'mime' => $card['wiki_full_art_mime'] !== null && $card['wiki_full_art_mime'] !== '' ? (string)$card['wiki_full_art_mime'] : null,
            ] : null,
            'golden' => $card['local_gold_image_url'] !== null && $card['local_gold_image_url'] !== '' ? absolute_url($card['local_gold_image_url'], $card['updated_at'] ?? null) : ($card['image_gold_url'] !== null && $card['image_gold_url'] !== '' ? (string)$card['image_gold_url'] : null),
            'signature' => $card['image_signature_url'] !== null && $card['image_signature_url'] !== '' ? (string)$card['image_signature_url'] : null,
            'diamond' => $card['image_diamond_url'] !== null && $card['image_diamond_url'] !== '' ? (string)$card['image_diamond_url'] : null,
            'crop' => $cropImage,
            'horizontal' => $card['horizontal_image_url'] ?? null,
            'animated' => [
                'golden' => $card['animated_gold_url'] !== null && $card['animated_gold_url'] !== '' ? (string)$card['animated_gold_url'] : null,
                'signature' => $card['animated_signature_url'] !== null && $card['animated_signature_url'] !== '' ? (string)$card['animated_signature_url'] : null,
                'diamond' => $card['animated_diamond_url'] !== null && $card['animated_diamond_url'] !== '' ? (string)$card['animated_diamond_url'] : null,
            ],
        ],
        'mechanics' => json_field($card['mechanics_json'] ?? null),
        'referenced_tags' => json_field($card['referenced_tags_json'] ?? null),
        'keyword_ids' => json_field($card['keyword_ids_json'] ?? null),
        'source' => $card['source'] !== null && $card['source'] !== '' ? (string)$card['source'] : null,
        'wiki_page' => [
            'title' => $wikiMeta && $wikiMeta['wiki_page_title'] !== null && $wikiMeta['wiki_page_title'] !== '' ? (string)$wikiMeta['wiki_page_title'] : ($card['wiki_page_title'] !== null && $card['wiki_page_title'] !== '' ? (string)$card['wiki_page_title'] : null),
            'url' => $wikiMeta && $wikiMeta['wiki_page_url'] !== null && $wikiMeta['wiki_page_url'] !== '' ? (string)$wikiMeta['wiki_page_url'] : ($card['wiki_page_url'] !== null && $card['wiki_page_url'] !== '' ? (string)$card['wiki_page_url'] : null),
        ],
        'created_at' => (string)$card['created_at'],
        'updated_at' => (string)$card['updated_at'],
    ];

    if ($includeWiki) {
        $payload['wiki'] = constructed_wiki_meta_to_api($wikiMeta, $termTranslations);
    }
    if ($relatedGroups) {
        $payload['related_cards_localized'] = $relatedGroups;
    }

    return $payload;
}

function constructed_related_heading_ru(?string $heading): string
{
    $heading = trim((string)$heading);
    $labels = [
        'Related cards' => 'Сопутствующие карты',
        'Generated cards' => 'Создаваемые карты',
        'Choice cards' => 'Способности Титана',
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
        'Fantastic Treasure choices' => 'Варианты фантастических сокровищ',
        'Lackey cards' => 'Лакеи',
        'Dream cards' => 'Карты Сна',
        'Corrupted Dream cards' => 'Искажённые карты Сна',
        "Cards that improve C'Thun" => "Карты, усиливающие К'Туна",
        "Cards with C'Thun-related conditions" => "Карты с условиями К'Туна",
        'Generated Quest Rewards' => 'Создаваемые награды за задания',
        'Active Anomalies' => 'Активные аномалии',
        'Inactive Anomalies' => 'Неактивные аномалии',
    ];

    return $labels[$heading] ?? ($heading !== '' ? $heading : 'Сопутствующие карты');
}

function load_constructed_related_groups(PDO $pdo, ?array $wikiMeta): array
{
    if (!$wikiMeta) {
        return [];
    }
    $sourceGroups = json_field($wikiMeta['related_cards_json'] ?? null);
    if (!$sourceGroups || !is_array($sourceGroups)) {
        return [];
    }

    $cardIds = [];
    foreach ($sourceGroups as $group) {
        foreach (($group['cards'] ?? []) as $related) {
            $cardId = trim((string)($related['card_id'] ?? ''));
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
    $stmt = $pdo->prepare(
        'SELECT c.*, GROUP_CONCAT(DISTINCT f.format_slug ORDER BY f.format_slug) AS formats
         FROM constructed_cards c
         LEFT JOIN constructed_format_cards f ON f.card_id = c.card_id AND f.in_format = 1
         WHERE c.card_id IN (' . $placeholders . ')
         GROUP BY c.card_id'
    );
    $stmt->execute($ids);
    $cardMap = [];
    foreach ($stmt->fetchAll() as $card) {
        $cardMap[(string)$card['card_id']] = $card;
    }

    $result = [];
    foreach ($sourceGroups as $group) {
        $cards = [];
        foreach (($group['cards'] ?? []) as $related) {
            $cardId = trim((string)($related['card_id'] ?? ''));
            if ($cardId === '') {
                continue;
            }
            if (isset($cardMap[$cardId])) {
                $payload = constructed_card_to_api($cardMap[$cardId]);
            } else {
                $wikiImage = isset($related['image_url']) && trim((string)$related['image_url']) !== ''
                    ? (string)$related['image_url']
                    : null;
                $wikiTitle = isset($related['title']) && trim((string)$related['title']) !== ''
                    ? (string)$related['title']
                    : null;
                $payload = [
                    'card_id' => $cardId,
                    'dbf' => null,
                    'collectible' => false,
                    'formats' => [],
                    'name' => ['ru' => null, 'en' => $wikiTitle],
                    'text' => ['ru' => null, 'en' => null],
                    'flavor' => ['ru' => null, 'en' => null],
                    'card_set' => null,
                    'card_type' => ['slug' => null, 'name_ru' => null],
                    'rarity' => null,
                    'class' => null,
                    'multi_class' => [],
                    'minion_type' => null,
                    'spell_school' => null,
                    'mana_cost' => null,
                    'attack' => null,
                    'health' => null,
                    'durability' => null,
                    'armor' => null,
                    'artist' => null,
                    'images' => [
                        'card' => $wikiImage,
                        'art' => null,
                        'art_metadata' => null,
                        'golden' => null,
                        'signature' => null,
                        'diamond' => null,
                        'crop' => null,
                        'animated' => [
                            'golden' => null,
                            'signature' => null,
                            'diamond' => null,
                        ],
                    ],
                    'mechanics' => [],
                    'referenced_tags' => [],
                    'keyword_ids' => [],
                    'source' => 'wiki_relation_pending',
                    'wiki_page' => [
                        'title' => $wikiTitle,
                        'url' => isset($related['url']) && $related['url'] !== ''
                            ? (string)$related['url']
                            : null,
                    ],
                    'pending_localization' => true,
                    'created_at' => null,
                    'updated_at' => null,
                ];
            }
            $payload['relationship'] = [
                'wiki_title' => isset($related['title']) && $related['title'] !== '' ? (string)$related['title'] : null,
                'wiki_url' => isset($related['url']) && $related['url'] !== '' ? (string)$related['url'] : null,
            ];
            $cards[] = $payload;
        }
        if ($cards) {
            $heading = isset($group['heading']) ? (string)$group['heading'] : '';
            $result[] = [
                'heading' => [
                    'ru' => constructed_related_heading_ru($heading),
                    'en' => $heading !== '' ? $heading : null,
                ],
                'cards' => $cards,
            ];
        }
    }

    return $result;
}

function diamond_card_to_api(array $card): array
{
    return [
        'base_card' => [
            'card_id' => (string)$card['base_card_id'],
            'dbf' => $card['base_dbf'] !== null ? (int)$card['base_dbf'] : null,
        ],
        'diamond_card' => [
            'card_id' => (string)$card['diamond_card_id'],
            'dbf' => $card['diamond_dbf'] !== null ? (int)$card['diamond_dbf'] : null,
        ],
        'name' => [
            'ru' => $card['name_ru'] !== null && $card['name_ru'] !== '' ? (string)$card['name_ru'] : null,
            'en' => $card['name_en'] !== null && $card['name_en'] !== '' ? (string)$card['name_en'] : null,
        ],
        'card_set' => [
            'slug' => $card['card_set'] !== null && $card['card_set'] !== '' ? (string)$card['card_set'] : null,
            'id' => $card['card_set_id'] !== null ? (int)$card['card_set_id'] : null,
        ],
        'card_type' => [
            'slug' => $card['card_type'] !== null && $card['card_type'] !== '' ? (string)$card['card_type'] : null,
            'name_ru' => constructed_card_type_label($card['card_type'] !== null ? (string)$card['card_type'] : null),
        ],
        'rarity' => $card['rarity'] !== null && $card['rarity'] !== '' ? (string)$card['rarity'] : null,
        'class' => $card['class_slug'] !== null && $card['class_slug'] !== '' ? (string)$card['class_slug'] : null,
        'mana_cost' => $card['mana_cost'] !== null ? (int)$card['mana_cost'] : null,
        'collectible' => (bool)$card['collectible'],
        'section' => [
            'slug' => (string)$card['section_slug'],
            'name_ru' => (string)$card['section_name_ru'],
        ],
        'formats' => [
            'standard' => (bool)$card['in_standard'],
            'wild' => (bool)$card['in_wild'],
        ],
        'images' => [
            'diamond' => $card['image_url'] !== null && $card['image_url'] !== '' ? (string)$card['image_url'] : null,
            'animated_diamond' => $card['animated_url'] !== null && $card['animated_url'] !== '' ? (string)$card['animated_url'] : null,
        ],
        'animated_source' => $card['animated_source'] !== null && $card['animated_source'] !== '' ? (string)$card['animated_source'] : null,
        'hearthpwn_url' => $card['hearthpwn_url'] !== null && $card['hearthpwn_url'] !== '' ? (string)$card['hearthpwn_url'] : null,
        'wiki_page' => [
            'title' => $card['wiki_page_title'] !== null && $card['wiki_page_title'] !== '' ? (string)$card['wiki_page_title'] : null,
            'url' => $card['wiki_page_url'] !== null && $card['wiki_page_url'] !== '' ? (string)$card['wiki_page_url'] : null,
        ],
        'source' => (string)$card['source'],
        'fetched_at' => $card['fetched_at'] !== null ? (string)$card['fetched_at'] : null,
        'changed_at' => $card['changed_at'] !== null ? (string)$card['changed_at'] : null,
        'created_at' => (string)$card['created_at'],
        'updated_at' => (string)$card['updated_at'],
    ];
}

function hero_card_json($value): ?array
{
    $payload = json_field($value);
    if (!$payload) {
        return null;
    }
    $image = null;
    if (isset($payload['image']) && $payload['image'] !== '') {
        $image = (string)$payload['image'];
    } elseif (isset($payload['crop_image']) && $payload['crop_image'] !== '') {
        $image = (string)$payload['crop_image'];
    }

    return array_filter([
        'dbf' => isset($payload['dbf']) ? (int)$payload['dbf'] : null,
        'name' => isset($payload['name']) && $payload['name'] !== '' ? (string)$payload['name'] : null,
        'text' => isset($payload['text']) && $payload['text'] !== '' ? (string)$payload['text'] : null,
        'image' => $image,
        'image_gold' => isset($payload['image_gold']) && $payload['image_gold'] !== '' ? (string)$payload['image_gold'] : null,
        'crop_image' => isset($payload['crop_image']) && $payload['crop_image'] !== '' ? (string)$payload['crop_image'] : null,
        'golden' => isset($payload['golden']) && is_array($payload['golden']) ? array_filter([
            'dbf' => isset($payload['golden']['dbf']) ? (int)$payload['golden']['dbf'] : null,
            'card_id' => isset($payload['golden']['card_id']) && $payload['golden']['card_id'] !== '' ? (string)$payload['golden']['card_id'] : null,
            'name' => isset($payload['golden']['name']) && $payload['golden']['name'] !== '' ? (string)$payload['golden']['name'] : null,
            'text' => isset($payload['golden']['text']) && $payload['golden']['text'] !== '' ? (string)$payload['golden']['text'] : null,
            'image' => isset($payload['golden']['image']) && $payload['golden']['image'] !== '' ? (string)$payload['golden']['image'] : null,
            'card_type' => isset($payload['golden']['card_type']) && $payload['golden']['card_type'] !== '' ? (string)$payload['golden']['card_type'] : null,
        ], static function ($value) {
            return $value !== null;
        }) : null,
        'artist' => isset($payload['artist']) && $payload['artist'] !== '' ? (string)$payload['artist'] : null,
        'parent_id' => isset($payload['parent_id']) ? (int)$payload['parent_id'] : null,
        'card_type_id' => isset($payload['card_type_id']) ? (int)$payload['card_type_id'] : null,
    ], static function ($value) {
        return $value !== null;
    });
}

function hero_to_api(array $hero): array
{
    return [
        'card_id' => (string)$hero['card_id'],
        'dbf' => $hero['dbf'] !== null ? (int)$hero['dbf'] : null,
        'hero_id' => $hero['hero_id'] !== null ? (int)$hero['hero_id'] : null,
        'name' => [
            'ru' => $hero['name_ru'] !== null && $hero['name_ru'] !== '' ? (string)$hero['name_ru'] : null,
            'en' => $hero['name_en'] !== null && $hero['name_en'] !== '' ? (string)$hero['name_en'] : null,
        ],
        'health' => $hero['health'] !== null ? (int)$hero['health'] : null,
        'armor' => [
            'normal' => $hero['armor'] !== null ? (int)$hero['armor'] : null,
            'duos' => $hero['duos_armor'] !== null ? (int)$hero['duos_armor'] : null,
            'text' => $hero['armor_text'] !== null && $hero['armor_text'] !== '' ? (string)$hero['armor_text'] : null,
        ],
        'artist' => $hero['artist'] !== null && $hero['artist'] !== '' ? (string)$hero['artist'] : null,
        'race' => $hero['race'] !== null && $hero['race'] !== '' ? (string)$hero['race'] : null,
        'character' => [
            'name' => $hero['character_name'] !== null && $hero['character_name'] !== '' ? (string)$hero['character_name'] : null,
            'as_hero' => $hero['as_hero'] !== null && $hero['as_hero'] !== '' ? (string)$hero['as_hero'] : null,
            'description' => $hero['hero_description'] !== null && $hero['hero_description'] !== '' ? (string)$hero['hero_description'] : null,
        ],
        'images' => [
            'hero' => $hero['hero_image_url'] !== null && $hero['hero_image_url'] !== '' ? (string)$hero['hero_image_url'] : null,
            'full_art' => $hero['hero_full_art_url'] !== null && $hero['hero_full_art_url'] !== '' ? (string)$hero['hero_full_art_url'] : null,
            'horizontal' => $hero['horizontal_image_url'] ?? null,
        ],
        'hero_power' => [
            'dbf' => $hero['hero_power_dbf'] !== null ? (int)$hero['hero_power_dbf'] : null,
            'card' => hero_card_json($hero['hero_power_json'] ?? null),
        ],
        'buddy' => [
            'dbf' => $hero['buddy_dbf'] !== null ? (int)$hero['buddy_dbf'] : null,
            'card' => hero_card_json($hero['buddy_json'] ?? null),
        ],
        'wiki' => [
            'status' => (string)($hero['status'] ?? 'ok'),
            'page' => [
                'title' => $hero['wiki_page_title'] !== null && $hero['wiki_page_title'] !== '' ? (string)$hero['wiki_page_title'] : null,
                'url' => $hero['wiki_page_url'] !== null && $hero['wiki_page_url'] !== '' ? (string)$hero['wiki_page_url'] : null,
            ],
            'availability' => json_field($hero['availability_json'] ?? null),
            'hero_skins' => json_field($hero['hero_skins_json'] ?? null),
            'gallery' => json_field($hero['gallery_json'] ?? null),
            'card_changes' => json_field($hero['card_changes_json'] ?? null),
            'external_links' => json_field($hero['external_links_json'] ?? null),
            'fetched_at' => $hero['fetched_at'] !== null ? (string)$hero['fetched_at'] : null,
            'changed_at' => $hero['changed_at'] !== null ? (string)$hero['changed_at'] : null,
            'error' => $hero['error'] !== null && $hero['error'] !== '' ? (string)$hero['error'] : null,
        ],
        'created_at' => (string)$hero['created_at'],
        'updated_at' => (string)$hero['updated_at'],
    ];
}

function hero_skin_to_api(array $skin): array
{
    return [
        'card_id' => (string)$skin['card_id'],
        'dbf' => $skin['dbf'] !== null ? (int)$skin['dbf'] : null,
        'release_date' => $skin['release_date'] !== null && $skin['release_date'] !== '' ? (string)$skin['release_date'] : null,
        'name' => [
            'en' => (string)$skin['name_en'],
        ],
        'class' => [
            'id' => $skin['class_id'] !== null ? (int)$skin['class_id'] : null,
            'slug' => $skin['class_slug'] !== null && $skin['class_slug'] !== '' ? (string)$skin['class_slug'] : null,
            'name_en' => $skin['class_name_en'] !== null && $skin['class_name_en'] !== '' ? (string)$skin['class_name_en'] : null,
            'name_ru' => $skin['class_name_ru'] !== null && $skin['class_name_ru'] !== '' ? (string)$skin['class_name_ru'] : null,
        ],
        'health' => $skin['health'] !== null ? (int)$skin['health'] : null,
        'character' => $skin['character_name'] !== null && $skin['character_name'] !== '' ? (string)$skin['character_name'] : null,
        'actor' => $skin['actor'] !== null && $skin['actor'] !== '' ? (string)$skin['actor'] : null,
        'artist' => $skin['artist'] !== null && $skin['artist'] !== '' ? (string)$skin['artist'] : null,
        'category' => [
            'slug' => $skin['primary_category_slug'] !== null && $skin['primary_category_slug'] !== '' ? (string)$skin['primary_category_slug'] : null,
            'name_en' => $skin['primary_category_en'] !== null && $skin['primary_category_en'] !== '' ? (string)$skin['primary_category_en'] : null,
            'name_ru' => $skin['primary_category_ru'] !== null && $skin['primary_category_ru'] !== '' ? (string)$skin['primary_category_ru'] : null,
        ],
        'rarity' => [
            'slug' => $skin['rarity_slug'] !== null && $skin['rarity_slug'] !== '' ? (string)$skin['rarity_slug'] : null,
            'name_en' => $skin['rarity_name_en'] !== null && $skin['rarity_name_en'] !== '' ? (string)$skin['rarity_name_en'] : null,
            'name_ru' => $skin['rarity_name_ru'] !== null && $skin['rarity_name_ru'] !== '' ? (string)$skin['rarity_name_ru'] : null,
        ],
        'categories' => json_field($skin['categories_json'] ?? null),
        'tags' => json_field($skin['tags_json'] ?? null),
        'images' => [
            'static' => $skin['static_image_url'] !== null && $skin['static_image_url'] !== '' ? (string)$skin['static_image_url'] : null,
            'animated' => $skin['animated_image_url'] !== null && $skin['animated_image_url'] !== '' ? (string)$skin['animated_image_url'] : null,
            'animated_assets' => json_field($skin['animated_asset_json'] ?? null),
            'full_art' => $skin['full_art_url'] !== null && $skin['full_art_url'] !== '' ? (string)$skin['full_art_url'] : null,
            'horizontal' => $skin['horizontal_image_url'] ?? null,
        ],
        'gallery' => json_field($skin['gallery_json'] ?? null),
        'sounds' => json_field($skin['sounds_json'] ?? null),
        'wiki_page' => [
            'title' => (string)$skin['page_title'],
            'url' => (string)$skin['page_url'],
        ],
        'status' => (string)($skin['status'] ?? 'ok'),
        'error' => $skin['error'] !== null && $skin['error'] !== '' ? (string)$skin['error'] : null,
        'fetched_at' => $skin['fetched_at'] !== null ? (string)$skin['fetched_at'] : null,
        'changed_at' => $skin['changed_at'] !== null ? (string)$skin['changed_at'] : null,
        'created_at' => (string)$skin['created_at'],
        'updated_at' => (string)$skin['updated_at'],
    ];
}

function hero_skin_summary_to_api(array $skin): array
{
    $payload = hero_skin_to_api($skin);
    unset(
        $payload['health'],
        $payload['character'],
        $payload['actor'],
        $payload['artist'],
        $payload['gallery'],
        $payload['sounds'],
        $payload['wiki_page'],
        $payload['status'],
        $payload['error'],
        $payload['fetched_at'],
        $payload['changed_at'],
        $payload['images']['animated_assets'],
        $payload['images']['full_art']
    );
    return $payload;
}

function pet_to_api(array $pet): array
{
    return [
        'pet' => [
            'id' => (int)$pet['pet_id'],
            'name' => (string)$pet['pet_name'],
        ],
        'variant' => [
            'id' => (int)$pet['variant_id'],
            'name' => (string)$pet['variant_name'],
            'level' => $pet['level'] !== null ? (int)$pet['level'] : null,
        ],
        'card_id' => $pet['card_id'] !== null && $pet['card_id'] !== '' ? (string)$pet['card_id'] : null,
        'dbf' => $pet['dbf'] !== null ? (int)$pet['dbf'] : null,
        'release_date' => $pet['release_date'] !== null && $pet['release_date'] !== '' ? (string)$pet['release_date'] : null,
        'images' => [
            'card' => $pet['card_image_url'] !== null && $pet['card_image_url'] !== '' ? (string)$pet['card_image_url'] : null,
            'end_screen_background' => $pet['end_screen_background_url'] !== null && $pet['end_screen_background_url'] !== '' ? (string)$pet['end_screen_background_url'] : null,
            'horizontal' => $pet['horizontal_image_url'] ?? null,
        ],
        'gallery' => json_field($pet['gallery_json'] ?? null),
        'wiki_page' => [
            'title' => $pet['page_title'] !== null && $pet['page_title'] !== '' ? (string)$pet['page_title'] : null,
            'url' => $pet['page_url'] !== null && $pet['page_url'] !== '' ? (string)$pet['page_url'] : null,
        ],
        'status' => (string)($pet['status'] ?? 'ok'),
        'error' => $pet['error'] !== null && $pet['error'] !== '' ? (string)$pet['error'] : null,
        'source' => (string)$pet['source'],
        'fetched_at' => $pet['fetched_at'] !== null ? (string)$pet['fetched_at'] : null,
        'changed_at' => $pet['changed_at'] !== null ? (string)$pet['changed_at'] : null,
        'created_at' => (string)$pet['created_at'],
        'updated_at' => (string)$pet['updated_at'],
    ];
}

function pet_summary_to_api(array $pet): array
{
    $payload = pet_to_api($pet);
    unset(
        $payload['images']['end_screen_background'],
        $payload['gallery'],
        $payload['wiki_page'],
        $payload['status'],
        $payload['error'],
        $payload['source'],
        $payload['fetched_at'],
        $payload['changed_at'],
    );
    return $payload;
}

function timewarped_card_type_label(?string $type): string
{
    if ($type === 'minion') {
        return 'Существо';
    }
    if ($type === 'spell') {
        return 'Заклинание таверны';
    }
    if ($type === 'hero_power') {
        return 'Сила героя';
    }

    return $type !== null && $type !== '' ? $type : 'Карта';
}

function timewarped_card_to_api(array $card, array $termTranslations = []): array
{
    $wikiMechanics = json_field($card['wiki_mechanics_json'] ?? null);
    $wikiTags = json_field($card['wiki_tags_json'] ?? null);

    return [
        'card_id' => (string)$card['card_id'],
        'dbf' => $card['dbf'] !== null ? (int)$card['dbf'] : null,
        'category' => [
            'slug' => 'timewarped',
            'name_ru' => 'Хрономальные карты',
        ],
        'card_type' => [
            'slug' => $card['card_type'] !== null && $card['card_type'] !== '' ? (string)$card['card_type'] : null,
            'name_ru' => timewarped_card_type_label($card['card_type'] !== null ? (string)$card['card_type'] : null),
        ],
        'name' => [
            'ru' => $card['name_ru'] !== null && $card['name_ru'] !== '' ? (string)$card['name_ru'] : null,
            'en' => (string)$card['name_en'],
        ],
        'text' => [
            'ru' => $card['text_ru'] !== null && $card['text_ru'] !== '' ? (string)$card['text_ru'] : null,
            'en' => $card['text_en'] !== null && $card['text_en'] !== '' ? (string)$card['text_en'] : null,
        ],
        'tavern_tier' => $card['tavern_tier'] !== null ? (int)$card['tavern_tier'] : null,
        'cost' => $card['cost'] !== null ? (int)$card['cost'] : null,
        'attack' => $card['attack'] !== null ? (int)$card['attack'] : null,
        'health' => $card['health'] !== null ? (int)$card['health'] : null,
        'minion_type' => $card['minion_type'] !== null && $card['minion_type'] !== '' ? (string)$card['minion_type'] : null,
        'race' => $card['race'] !== null && $card['race'] !== '' ? (string)$card['race'] : null,
        'artist' => $card['artist'] !== null && $card['artist'] !== '' ? (string)$card['artist'] : null,
        'images' => [
            'card' => $card['card_image_url'] !== null && $card['card_image_url'] !== '' ? (string)$card['card_image_url'] : null,
            'golden' => $card['golden_image_url'] !== null && $card['golden_image_url'] !== '' ? (string)$card['golden_image_url'] : null,
            'art' => $card['art_image_url'] !== null && $card['art_image_url'] !== '' ? (string)$card['art_image_url'] : null,
            'horizontal' => $card['horizontal_image_url'] ?? null,
        ],
        'golden' => [
            'card_id' => $card['golden_card_id'] !== null && $card['golden_card_id'] !== '' ? (string)$card['golden_card_id'] : null,
            'dbf' => $card['golden_dbf'] !== null ? (int)$card['golden_dbf'] : null,
            'name' => [
                'ru' => $card['golden_name_ru'] !== null && $card['golden_name_ru'] !== '' ? (string)$card['golden_name_ru'] : null,
                'en' => $card['golden_name_en'] !== null && $card['golden_name_en'] !== '' ? (string)$card['golden_name_en'] : null,
            ],
            'text' => [
                'ru' => $card['golden_text_ru'] !== null && $card['golden_text_ru'] !== '' ? (string)$card['golden_text_ru'] : null,
                'en' => $card['golden_text_en'] !== null && $card['golden_text_en'] !== '' ? (string)$card['golden_text_en'] : null,
            ],
            'image' => $card['golden_image_url'] !== null && $card['golden_image_url'] !== '' ? (string)$card['golden_image_url'] : null,
        ],
        'wiki' => [
            'status' => (string)($card['status'] ?? 'ok'),
            'source' => 'hearthstone.wiki.gg',
            'page' => [
                'title' => $card['wiki_page_title'] !== null && $card['wiki_page_title'] !== '' ? (string)$card['wiki_page_title'] : null,
                'url' => $card['wiki_page_url'] !== null && $card['wiki_page_url'] !== '' ? (string)$card['wiki_page_url'] : null,
            ],
            'wiki_mechanics' => $wikiMechanics,
            'wiki_mechanics_localized' => localized_wiki_terms($wikiMechanics, $termTranslations, 'mechanic'),
            'wiki_tags' => $wikiTags,
            'wiki_tags_localized' => localized_wiki_terms($wikiTags, $termTranslations, 'tag'),
            'availability' => json_field($card['availability_json'] ?? null),
            'related_cards' => json_field($card['related_cards_json'] ?? null),
            'related_card_ids' => json_field($card['related_card_ids_json'] ?? null),
            'sounds' => json_field($card['sounds_json'] ?? null),
            'gallery' => json_field($card['gallery_json'] ?? null),
            'card_changes' => json_field($card['card_changes_json'] ?? null),
            'external_links' => json_field($card['external_links_json'] ?? null),
            'full_tags' => json_field($card['full_tags_json'] ?? null),
            'fetched_at' => $card['fetched_at'] !== null ? (string)$card['fetched_at'] : null,
            'changed_at' => $card['changed_at'] !== null ? (string)$card['changed_at'] : null,
            'error' => $card['error'] !== null && $card['error'] !== '' ? (string)$card['error'] : null,
        ],
        'created_at' => (string)$card['created_at'],
        'updated_at' => (string)$card['updated_at'],
    ];
}

function library_configs(): array
{
    return [
        'anomaly' => ['name_ru' => 'Аномалии', 'plural_ru' => 'аномалий'],
        'dark_gift' => ['name_ru' => 'Темные дары', 'plural_ru' => 'темных даров'],
        'quest' => ['name_ru' => 'Квесты', 'plural_ru' => 'квестов'],
        'darkmoon_prize' => ['name_ru' => 'Призы Ярмарки Новолуния', 'plural_ru' => 'призов'],
        'reward' => ['name_ru' => 'Награды', 'plural_ru' => 'наград'],
        'trinket' => ['name_ru' => 'Аксессуары', 'plural_ru' => 'аксессуаров'],
    ];
}

function normalize_library_slug(string $library): ?string
{
    $library = trim(strtolower(rawurldecode($library)));
    $aliases = [
        'anomalies' => 'anomaly',
        'anomaly' => 'anomaly',
        'dark-gifts' => 'dark_gift',
        'dark_gifts' => 'dark_gift',
        'dark-gift' => 'dark_gift',
        'dark_gift' => 'dark_gift',
        'quests' => 'quest',
        'quest' => 'quest',
        'darkmoon-prizes' => 'darkmoon_prize',
        'darkmoon_prizes' => 'darkmoon_prize',
        'darkmoon-prize' => 'darkmoon_prize',
        'darkmoon_prize' => 'darkmoon_prize',
        'rewards' => 'reward',
        'reward' => 'reward',
        'trinkets' => 'trinket',
        'trinket' => 'trinket',
        'accessories' => 'trinket',
    ];

    return $aliases[$library] ?? null;
}

function library_card_to_api(array $card): array
{
    $configs = library_configs();
    $library = (string)$card['library'];

    return [
        'library' => [
            'slug' => $library,
            'name_ru' => $configs[$library]['name_ru'] ?? $library,
        ],
        'card_id' => (string)$card['card_id'],
        'dbf' => $card['dbf'] !== null ? (int)$card['dbf'] : null,
        'name' => [
            'ru' => (string)$card['name_ru'],
            'en' => $card['name_en'] !== null && $card['name_en'] !== '' ? (string)$card['name_en'] : null,
        ],
        'text' => [
            'ru' => $card['text_ru'] !== null && $card['text_ru'] !== '' ? (string)$card['text_ru'] : null,
            'en' => $card['text_en'] !== null && $card['text_en'] !== '' ? (string)$card['text_en'] : null,
        ],
        'images' => [
            'card' => $card['image_url'] !== null && $card['image_url'] !== '' ? (string)$card['image_url'] : null,
            'golden' => $card['image_gold_url'] !== null && $card['image_gold_url'] !== '' ? (string)$card['image_gold_url'] : null,
            'crop' => $card['crop_image_url'] !== null && $card['crop_image_url'] !== '' ? (string)$card['crop_image_url'] : null,
            'full_art' => isset($card['local_full_art_url']) && $card['local_full_art_url'] !== null && $card['local_full_art_url'] !== ''
                ? absolute_url((string)$card['local_full_art_url'], $card['full_art_fetched_at'] ?? $card['updated_at'] ?? null)
                : null,
            'full_art_source' => isset($card['full_art_source_url']) && $card['full_art_source_url'] !== null && $card['full_art_source_url'] !== ''
                ? (string)$card['full_art_source_url']
                : null,
            'horizontal' => $card['horizontal_image_url'] ?? null,
        ],
        'full_art' => [
            'source' => isset($card['full_art_source']) && $card['full_art_source'] !== null && $card['full_art_source'] !== '' ? (string)$card['full_art_source'] : null,
            'width' => isset($card['full_art_width']) && $card['full_art_width'] !== null ? (int)$card['full_art_width'] : null,
            'height' => isset($card['full_art_height']) && $card['full_art_height'] !== null ? (int)$card['full_art_height'] : null,
            'size' => isset($card['full_art_size']) && $card['full_art_size'] !== null ? (int)$card['full_art_size'] : null,
            'sha1' => isset($card['full_art_sha1']) && $card['full_art_sha1'] !== null && $card['full_art_sha1'] !== '' ? (string)$card['full_art_sha1'] : null,
            'mime' => isset($card['full_art_mime']) && $card['full_art_mime'] !== null && $card['full_art_mime'] !== '' ? (string)$card['full_art_mime'] : null,
            'fetched_at' => isset($card['full_art_fetched_at']) && $card['full_art_fetched_at'] !== null ? (string)$card['full_art_fetched_at'] : null,
        ],
        'artist' => $card['artist'] !== null && $card['artist'] !== '' ? (string)$card['artist'] : null,
        'card_type' => [
            'slug' => $card['card_type'] !== null && $card['card_type'] !== '' ? (string)$card['card_type'] : null,
            'id' => $card['card_type_id'] !== null ? (int)$card['card_type_id'] : null,
        ],
        'mana_cost' => $card['mana_cost'] !== null ? (int)$card['mana_cost'] : null,
        'in_pool' => (bool)$card['in_pool'],
        'pool_status' => (string)$card['pool_status'],
        'group' => [
            'slug' => isset($card['group_slug']) && $card['group_slug'] !== null && $card['group_slug'] !== '' ? (string)$card['group_slug'] : null,
            'name_ru' => isset($card['group_name_ru']) && $card['group_name_ru'] !== null && $card['group_name_ru'] !== '' ? (string)$card['group_name_ru'] : null,
        ],
        'tier' => [
            'value' => isset($card['tier_value']) && $card['tier_value'] !== null ? (int)$card['tier_value'] : null,
            'slug' => isset($card['tier_slug']) && $card['tier_slug'] !== null && $card['tier_slug'] !== '' ? (string)$card['tier_slug'] : null,
            'name_ru' => isset($card['tier_name_ru']) && $card['tier_name_ru'] !== null && $card['tier_name_ru'] !== '' ? (string)$card['tier_name_ru'] : null,
        ],
        'wiki_page' => [
            'title' => $card['wiki_page_title'] !== null && $card['wiki_page_title'] !== '' ? (string)$card['wiki_page_title'] : null,
            'url' => $card['wiki_page_url'] !== null && $card['wiki_page_url'] !== '' ? (string)$card['wiki_page_url'] : null,
        ],
        'source' => (string)$card['source'],
        'fetched_at' => $card['fetched_at'] !== null ? (string)$card['fetched_at'] : null,
        'changed_at' => $card['changed_at'] !== null ? (string)$card['changed_at'] : null,
        'created_at' => (string)$card['created_at'],
        'updated_at' => (string)$card['updated_at'],
    ];
}

function coin_to_api(array $coin): array
{
    return [
        'card_id' => (string)$coin['card_id'],
        'dbf' => $coin['dbf'] !== null ? (int)$coin['dbf'] : null,
        'release_date' => $coin['release_date'] !== null && $coin['release_date'] !== '' ? (string)$coin['release_date'] : null,
        'name' => [
            'coin_en' => (string)$coin['coin_name_en'],
            'card_ru' => $coin['card_name_ru'] !== null && $coin['card_name_ru'] !== '' ? (string)$coin['card_name_ru'] : null,
            'card_en' => $coin['card_name_en'] !== null && $coin['card_name_en'] !== '' ? (string)$coin['card_name_en'] : null,
        ],
        'text' => [
            'ru' => $coin['text_ru'] !== null && $coin['text_ru'] !== '' ? (string)$coin['text_ru'] : null,
            'en' => $coin['text_en'] !== null && $coin['text_en'] !== '' ? (string)$coin['text_en'] : null,
        ],
        'flavor' => $coin['flavor_text'] !== null && $coin['flavor_text'] !== '' ? (string)$coin['flavor_text'] : null,
        'artist' => $coin['artist'] !== null && $coin['artist'] !== '' ? (string)$coin['artist'] : null,
        'images' => [
            'card' => $coin['image_url'] !== null && $coin['image_url'] !== '' ? (string)$coin['image_url'] : null,
            'golden' => $coin['image_gold_url'] !== null && $coin['image_gold_url'] !== '' ? (string)$coin['image_gold_url'] : null,
            'crop' => $coin['crop_image_url'] !== null && $coin['crop_image_url'] !== '' ? (string)$coin['crop_image_url'] : null,
            'wiki' => $coin['wiki_image_url'] !== null && $coin['wiki_image_url'] !== '' ? (string)$coin['wiki_image_url'] : null,
            'horizontal' => $coin['horizontal_image_url'] ?? null,
        ],
        'cosmetic_sort_order' => $coin['cosmetic_sort_order'] !== null ? (int)$coin['cosmetic_sort_order'] : null,
        'generated_by_card_ids' => json_field($coin['generated_by_card_ids_json'] ?? null),
        'related_card_ids' => json_field($coin['related_card_ids_json'] ?? null),
        'generated_by_cards' => json_field($coin['generated_by_cards_json'] ?? null),
        'related_cards' => json_field($coin['related_cards_json'] ?? null),
        'wiki_page' => [
            'title' => $coin['wiki_page_title'] !== null && $coin['wiki_page_title'] !== '' ? (string)$coin['wiki_page_title'] : null,
            'url' => $coin['wiki_page_url'] !== null && $coin['wiki_page_url'] !== '' ? (string)$coin['wiki_page_url'] : null,
        ],
        'source' => (string)$coin['source'],
        'fetched_at' => $coin['fetched_at'] !== null ? (string)$coin['fetched_at'] : null,
        'changed_at' => $coin['changed_at'] !== null ? (string)$coin['changed_at'] : null,
        'created_at' => (string)$coin['created_at'],
        'updated_at' => (string)$coin['updated_at'],
    ];
}

function coin_summary_to_api(array $coin): array
{
    $payload = coin_to_api($coin);
    unset(
        $payload['flavor'],
        $payload['artist'],
        $payload['images']['golden'],
        $payload['images']['wiki'],
        $payload['generated_by_card_ids'],
        $payload['related_card_ids'],
        $payload['generated_by_cards'],
        $payload['related_cards'],
        $payload['wiki_page'],
        $payload['source'],
        $payload['fetched_at'],
        $payload['changed_at'],
    );
    return $payload;
}

function bind_params(PDOStatement $stmt, array $params): void
{
    foreach ($params as $key => $value) {
        $stmt->bindValue(':' . $key, $value, is_int($value) ? PDO::PARAM_INT : PDO::PARAM_STR);
    }
}

function route_path(): string
{
    $path = parse_url($_SERVER['REQUEST_URI'] ?? '/api/v1', PHP_URL_PATH);
    if (!is_string($path) || $path === '') {
        return '/api/v1';
    }

    return '/' . trim($path, '/');
}

function api_index(PDO $pdo): void
{
    $total = (int)$pdo->query('SELECT COUNT(*) FROM battlegrounds_cards')->fetchColumn();
    $heroesTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok'")->fetchColumn();
    $timewarpedTotal = (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_timewarped_cards WHERE status = 'ok'")->fetchColumn();
    $libraryTotal = (int)$pdo->query('SELECT COUNT(*) FROM battlegrounds_library_cards')->fetchColumn();
    $constructedTotal = (int)$pdo->query('SELECT COUNT(*) FROM constructed_cards')->fetchColumn();
    $constructedWikiTotal = (int)$pdo->query("SELECT COUNT(*) FROM constructed_card_wiki_meta WHERE status = 'ok'")->fetchColumn();
    $diamondTotal = (int)$pdo->query('SELECT COUNT(*) FROM constructed_diamond_cards')->fetchColumn();
    $heroSkinsTotal = (int)$pdo->query("SELECT COUNT(*) FROM hero_skins WHERE status IN ('ok', 'partial')")->fetchColumn();
    $petsTotal = (int)$pdo->query("SELECT COUNT(*) FROM hearthstone_pets WHERE status IN ('ok', 'partial')")->fetchColumn();
    $coinsTotal = (int)$pdo->query('SELECT COUNT(*) FROM hearthstone_coins')->fetchColumn();
    $lastUpdated = $pdo->query('SELECT MAX(updated_at) FROM battlegrounds_cards')->fetchColumn() ?: null;
    $heroLastUpdated = $pdo->query("SELECT MAX(updated_at) FROM battlegrounds_heroes WHERE status = 'ok'")->fetchColumn() ?: null;
    $timewarpedLastUpdated = $pdo->query("SELECT MAX(updated_at) FROM battlegrounds_timewarped_cards WHERE status = 'ok'")->fetchColumn() ?: null;
    $libraryLastUpdated = $pdo->query('SELECT MAX(updated_at) FROM battlegrounds_library_cards')->fetchColumn() ?: null;
    $constructedLastUpdated = $pdo->query('SELECT MAX(updated_at) FROM constructed_cards')->fetchColumn() ?: null;
    $diamondLastUpdated = $pdo->query('SELECT MAX(updated_at) FROM constructed_diamond_cards')->fetchColumn() ?: null;
    $heroSkinsLastUpdated = $pdo->query("SELECT MAX(updated_at) FROM hero_skins WHERE status IN ('ok', 'partial')")->fetchColumn() ?: null;
    $petsLastUpdated = $pdo->query("SELECT MAX(updated_at) FROM hearthstone_pets WHERE status IN ('ok', 'partial')")->fetchColumn() ?: null;
    $coinsLastUpdated = $pdo->query('SELECT MAX(updated_at) FROM hearthstone_coins')->fetchColumn() ?: null;
    if (is_string($heroLastUpdated)) {
        $lastUpdated = max_timestamp(is_string($lastUpdated) ? $lastUpdated : null, $heroLastUpdated);
    }
    if (is_string($timewarpedLastUpdated)) {
        $lastUpdated = max_timestamp(is_string($lastUpdated) ? $lastUpdated : null, $timewarpedLastUpdated);
    }
    if (is_string($libraryLastUpdated)) {
        $lastUpdated = max_timestamp(is_string($lastUpdated) ? $lastUpdated : null, $libraryLastUpdated);
    }
    if (is_string($constructedLastUpdated)) {
        $lastUpdated = max_timestamp(is_string($lastUpdated) ? $lastUpdated : null, $constructedLastUpdated);
    }
    if (is_string($diamondLastUpdated)) {
        $lastUpdated = max_timestamp(is_string($lastUpdated) ? $lastUpdated : null, $diamondLastUpdated);
    }
    if (is_string($heroSkinsLastUpdated)) {
        $lastUpdated = max_timestamp(is_string($lastUpdated) ? $lastUpdated : null, $heroSkinsLastUpdated);
    }
    if (is_string($petsLastUpdated)) {
        $lastUpdated = max_timestamp(is_string($lastUpdated) ? $lastUpdated : null, $petsLastUpdated);
    }
    if (is_string($coinsLastUpdated)) {
        $lastUpdated = max_timestamp(is_string($lastUpdated) ? $lastUpdated : null, $coinsLastUpdated);
    }
    respond_cached([
        'name' => 'HS Battlegrounds RU API',
        'version' => API_VERSION,
        'status' => 'ok',
        'cards_total' => $total,
        'heroes_total' => $heroesTotal,
        'timewarped_total' => $timewarpedTotal,
        'library_total' => $libraryTotal,
        'constructed_total' => $constructedTotal,
        'constructed_wiki_total' => $constructedWikiTotal,
        'diamond_cards_total' => $diamondTotal,
        'hero_skins_total' => $heroSkinsTotal,
        'pets_total' => $petsTotal,
        'coins_total' => $coinsTotal,
        'endpoints' => [
            'GET /api/v1' => 'Описание API.',
            'GET /api/v1/cards' => 'Список карт с фильтрами и пагинацией.',
            'GET /api/v1/cards?card_type=spell' => 'Список заклинаний Полей сражений.',
            'GET /api/v1/cards?include=wiki' => 'Список карт с wiki-метаданными, если они синхронизированы.',
            'GET /api/v1/cards/{card_id}' => 'Одна карта по card_id.',
            'GET /api/v1/cards/{card_id}/wiki' => 'Wiki-метаданные карты по card_id.',
            'GET /api/v1/cards/by-dbf/{dbf}' => 'Одна карта по dbf.',
            'GET /api/v1/heroes' => 'Список героев Полей сражений.',
            'GET /api/v1/heroes/{card_id}' => 'Один герой по card_id.',
            'GET /api/v1/heroes/by-dbf/{dbf}' => 'Один герой по dbf.',
            'GET /api/v1/hero-skins' => 'Список скинов героев Hearthstone со static/animated/full art, Gallery и Sounds.',
            'GET /api/v1/hero-skins/{card_id}' => 'Один скин героя по card_id.',
            'GET /api/v1/hero-skins/by-dbf/{dbf}' => 'Один скин героя по dbf.',
            'GET /api/v1/pets' => 'Список питомцев Hearthstone с вариантами, Gallery и end screen background.',
            'GET /api/v1/pets/{card_id}' => 'Один вариант питомца по card_id.',
            'GET /api/v1/pets/by-dbf/{dbf}' => 'Один вариант питомца по dbf.',
            'GET /api/v1/coins' => 'Список косметических монеток The Coin с картинками и связями Generated by / Related with.',
            'GET /api/v1/coins/{card_id}' => 'Одна косметическая монетка по card_id.',
            'GET /api/v1/coins/by-dbf/{dbf}' => 'Одна косметическая монетка по dbf.',
            'GET /api/v1/timewarped-cards' => 'Список хрономальных карт Timewarped Tavern.',
            'GET /api/v1/timewarped-cards/{card_id}' => 'Одна хрономальная карта по card_id.',
            'GET /api/v1/timewarped-cards/by-dbf/{dbf}' => 'Одна хрономальная карта по dbf.',
            'GET /api/v1/constructed-cards?format=standard' => 'Список карт стандартного формата.',
            'GET /api/v1/constructed-cards?format=wild&include=wiki' => 'Список карт вольного формата с wiki-метаданными.',
            'GET /api/v1/constructed-cards/{card_id}' => 'Карта Standard/Wild или её сопутствующая карта по card_id; images.art — оригинальный Wiki full art, images.crop — игровой crop.',
            'GET /api/v1/constructed-cards/{card_id}/wiki' => 'Wiki-метаданные карты Standard/Wild.',
            'GET /api/v1/constructed-cards/by-dbf/{dbf}' => 'Одна карта Standard/Wild по dbf.',
            'GET /api/v1/diamond-cards' => 'Библиотека алмазных карт с обычными card_id/dbf и картинками.',
            'GET /api/v1/diamond-cards/{card_id}' => 'Одна алмазная карта по обычному или diamond card_id.',
            'GET /api/v1/anomalies' => 'Библиотека аномалий с русскими названиями и статусом пула.',
            'GET /api/v1/quests' => 'Библиотека квестов с разделением на доступные и удаленные.',
            'GET /api/v1/dark-gifts' => 'Библиотека Темных даров Полей сражений.',
            'GET /api/v1/darkmoon-prizes' => 'Библиотека призов Ярмарки Новолуния.',
            'GET /api/v1/rewards' => 'Библиотека наград Полей сражений.',
            'GET /api/v1/trinkets' => 'Библиотека аксессуаров с разделением на малые и большие.',
            'GET /api/v1/libraries/{library}' => 'Универсальный доступ к библиотекам anomaly, quest, darkmoon_prize, reward, trinket.',
            'GET /api/v1/meta' => 'Типы существ, уровни таверны и счетчики.',
        ],
    ], is_string($lastUpdated) ? $lastUpdated : null);
}

function api_meta(PDO $pdo): void
{
    $totals = [
        'cards' => (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_cards WHERE variant_kind = 'base'")->fetchColumn(),
        'card_records' => (int)$pdo->query('SELECT COUNT(*) FROM battlegrounds_cards')->fetchColumn(),
        'golden_variants' => (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_cards WHERE variant_kind = 'golden'")->fetchColumn(),
        'minions' => (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_cards WHERE variant_kind = 'base' AND card_type = 'minion'")->fetchColumn(),
        'spells' => (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_cards WHERE variant_kind = 'base' AND card_type = 'spell'")->fetchColumn(),
        'heroes' => (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_heroes WHERE status = 'ok'")->fetchColumn(),
        'timewarped' => (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_timewarped_cards WHERE status = 'ok'")->fetchColumn(),
        'libraries' => (int)$pdo->query('SELECT COUNT(*) FROM battlegrounds_library_cards')->fetchColumn(),
        'constructed' => (int)$pdo->query('SELECT COUNT(*) FROM constructed_cards')->fetchColumn(),
        'diamond_cards' => (int)$pdo->query('SELECT COUNT(*) FROM constructed_diamond_cards')->fetchColumn(),
        'hero_skins' => (int)$pdo->query("SELECT COUNT(*) FROM hero_skins WHERE status IN ('ok', 'partial')")->fetchColumn(),
        'pets' => (int)$pdo->query("SELECT COUNT(*) FROM hearthstone_pets WHERE status IN ('ok', 'partial')")->fetchColumn(),
        'coins' => (int)$pdo->query('SELECT COUNT(*) FROM hearthstone_coins')->fetchColumn(),
        'constructed_standard' => (int)$pdo->query("SELECT COUNT(*) FROM constructed_format_cards WHERE format_slug = 'standard' AND in_format = 1")->fetchColumn(),
        'constructed_wild' => (int)$pdo->query("SELECT COUNT(*) FROM constructed_format_cards WHERE format_slug = 'wild' AND in_format = 1")->fetchColumn(),
        'constructed_wiki_ok' => (int)$pdo->query("SELECT COUNT(*) FROM constructed_card_wiki_meta WHERE status = 'ok'")->fetchColumn(),
        'in_pool' => (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_cards WHERE variant_kind = 'base' AND in_pool = 1")->fetchColumn(),
        'duos_only' => (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_cards WHERE variant_kind = 'base' AND duos_only = 1")->fetchColumn(),
        'with_framed_image' => (int)$pdo->query("SELECT COUNT(*) FROM battlegrounds_cards WHERE variant_kind = 'base' AND framed_image IS NOT NULL AND framed_image <> ''")->fetchColumn(),
    ];

    $tiers = [];
    $tierStmt = $pdo->query("SELECT tavern_tier, COUNT(*) AS count FROM battlegrounds_cards WHERE variant_kind = 'base' GROUP BY tavern_tier ORDER BY tavern_tier IS NULL, tavern_tier");
    foreach ($tierStmt->fetchAll() as $row) {
        $tiers[] = [
            'tavern_tier' => $row['tavern_tier'] !== null ? (int)$row['tavern_tier'] : null,
            'count' => (int)$row['count'],
        ];
    }

    $types = [];
    $typeStmt = $pdo->query("SELECT creature_type, COUNT(*) AS count FROM battlegrounds_cards WHERE variant_kind = 'base' GROUP BY creature_type ORDER BY creature_type IS NULL, creature_type");
    foreach ($typeStmt->fetchAll() as $row) {
        $slug = $row['creature_type'] !== null ? (string)$row['creature_type'] : null;
        $types[] = [
            'slug' => $slug,
            'name_ru' => $slug ? creature_type_label($slug) : null,
            'count' => (int)$row['count'],
        ];
    }

    $lastUpdated = $pdo->query('SELECT MAX(updated_at) FROM battlegrounds_cards')->fetchColumn() ?: null;
    respond_cached([
        'totals' => $totals,
        'libraries' => array_map(static function (string $slug, array $config): array {
            return ['slug' => $slug, 'name_ru' => $config['name_ru']];
        }, array_keys(library_configs()), array_values(library_configs())),
        'creature_types' => array_map(static function (string $slug, string $label): array {
            return ['slug' => $slug, 'name_ru' => $label];
        }, array_keys(creature_types()), array_values(creature_types())),
        'card_types' => array_map(static function (string $slug, string $label): array {
            return ['slug' => $slug, 'name_ru' => $label];
        }, array_keys(card_types()), array_values(card_types())),
        'mechanics' => array_map(static function (string $slug, string $label): array {
            return ['slug' => $slug, 'name_ru' => $label];
        }, array_keys(mechanic_labels()), array_values(mechanic_labels())),
        'tavern_tiers' => [1, 2, 3, 4, 5, 6, 7],
        'counts_by_tier' => $tiers,
        'counts_by_creature_type' => $types,
    ], is_string($lastUpdated) ? $lastUpdated : null);
}

function api_cards(PDO $pdo): void
{
    $includeWiki = include_wiki_requested();
    $includeVariants = bool_param(isset($_GET['include_variants']) ? (string)$_GET['include_variants'] : null, 'include_variants') ?? false;
    $q = trim((string)($_GET['q'] ?? ''));
    $tier = int_param(isset($_GET['tier']) ? (string)$_GET['tier'] : null, 'tier', 1, 7);
    $dbf = int_param(isset($_GET['dbf']) ? (string)$_GET['dbf'] : null, 'dbf', 0, null);
    $inPool = bool_param(isset($_GET['in_pool']) ? (string)$_GET['in_pool'] : null, 'in_pool');
    $duosOnly = bool_param(isset($_GET['duos_only']) ? (string)$_GET['duos_only'] : null, 'duos_only');
    $updatedSince = datetime_param(isset($_GET['updated_since']) ? (string)$_GET['updated_since'] : null, 'updated_since');
    $cardType = trim((string)($_GET['card_type'] ?? ''));
    $creatureType = trim((string)($_GET['creature_type'] ?? ''));
    $page = int_param(isset($_GET['page']) ? (string)$_GET['page'] : '1', 'page', 1, null) ?? 1;
    $perPage = int_param(isset($_GET['per_page']) ? (string)$_GET['per_page'] : (string)DEFAULT_PER_PAGE, 'per_page', 1, MAX_PER_PAGE) ?? DEFAULT_PER_PAGE;

    $where = [];
    $params = [];

    if (!$includeVariants) {
        $where[] = "variant_kind = 'base'";
    }

    if ($q !== '') {
        $where[] = '(name LIKE :q_name OR name_en LIKE :q_name_en OR card_id LIKE :q_card_id OR dbf LIKE :q_dbf OR notes LIKE :q_notes)';
        $params['q_name'] = '%' . $q . '%';
        $params['q_name_en'] = '%' . $q . '%';
        $params['q_card_id'] = '%' . $q . '%';
        $params['q_dbf'] = '%' . $q . '%';
        $params['q_notes'] = '%' . $q . '%';
    }
    if ($tier !== null) {
        $where[] = 'tavern_tier = :tier';
        $params['tier'] = $tier;
    }
    if ($dbf !== null) {
        $where[] = 'dbf = :dbf';
        $params['dbf'] = $dbf;
    }
    if ($cardType !== '') {
        if (!array_key_exists($cardType, card_types())) {
            respond_error('invalid_parameter', 'Неизвестный card_type.', 400, [
                'allowed' => array_keys(card_types()),
            ]);
        }
        $where[] = 'card_type = :card_type';
        $params['card_type'] = $cardType;
    }
    if ($creatureType !== '') {
        if (!array_key_exists($creatureType, creature_types())) {
            respond_error('invalid_parameter', 'Неизвестный creature_type.', 400, [
                'allowed' => array_keys(creature_types()),
            ]);
        }
        $where[] = 'creature_type = :creature_type';
        $params['creature_type'] = $creatureType;
    }
    if ($inPool !== null) {
        $where[] = 'in_pool = :in_pool';
        $params['in_pool'] = $inPool;
    }
    if ($duosOnly !== null) {
        $where[] = 'duos_only = :duos_only';
        $params['duos_only'] = $duosOnly;
    }
    if ($updatedSince !== null) {
        $where[] = 'updated_at >= :updated_since';
        $params['updated_since'] = $updatedSince;
    }

    $whereSql = $where ? ' WHERE ' . implode(' AND ', $where) : '';
    $countStmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(updated_at) AS last_updated FROM battlegrounds_cards' . $whereSql);
    bind_params($countStmt, $params);
    $countStmt->execute();
    $countRow = $countStmt->fetch();
    $total = (int)($countRow['total'] ?? 0);
    $lastUpdated = isset($countRow['last_updated']) && is_string($countRow['last_updated']) ? $countRow['last_updated'] : null;
    $totalPages = max(1, (int)ceil($total / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_cards' . $whereSql
        . ' ORDER BY in_pool DESC, tavern_tier IS NULL, tavern_tier ASC, name ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $cards = $stmt->fetchAll();
    $cards = attach_horizontal_art(
        $pdo,
        $cards,
        'battleground_card',
        static fn(array $row): string => (string)$row['card_id']
    );
    $goldenVariantMap = load_golden_variant_map($pdo, $cards);
    $wikiMap = load_wiki_meta_map($pdo, array_column($cards, 'card_id'));
    $termTranslations = $includeWiki ? load_wiki_term_translations($pdo) : [];

    if ($includeWiki) {
        foreach ($wikiMap as $wikiMeta) {
            $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['updated_at']) ? (string)$wikiMeta['updated_at'] : null);
            $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['changed_at']) ? (string)$wikiMeta['changed_at'] : null);
        }
    }

    respond_cached([
        'data' => array_map(static function (array $card) use ($includeWiki, $wikiMap, $termTranslations, $goldenVariantMap): array {
            $cardId = (string)$card['card_id'];
            $dbf = $card['dbf'] !== null ? (int)$card['dbf'] : 0;
            return card_to_api(
                $card,
                $wikiMap[$cardId] ?? null,
                $includeWiki,
                $termTranslations,
                $goldenVariantMap[$dbf] ?? null
            );
        }, $cards),
        'pagination' => [
            'page' => $page,
            'per_page' => $perPage,
            'total' => $total,
            'total_pages' => $totalPages,
            'has_next' => $page < $totalPages,
            'has_prev' => $page > 1,
        ],
    ], $lastUpdated);
}

function api_card(PDO $pdo, string $cardId): void
{
    $includeWiki = include_wiki_requested();
    $cardId = rawurldecode($cardId);
    if ($cardId === '') {
        respond_error('not_found', 'Карта не найдена.', 404);
    }

    $stmt = $pdo->prepare('SELECT * FROM battlegrounds_cards WHERE card_id = :card_id LIMIT 1');
    $stmt->execute(['card_id' => $cardId]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Карта не найдена.', 404);
    }
    $card = attach_horizontal_art_one(
        $pdo,
        $card,
        'battleground_card',
        static fn(array $row): string => (string)$row['card_id']
    );

    $wikiMeta = load_wiki_meta($pdo, (string)$card['card_id']);
    $lastUpdated = (string)$card['updated_at'];
    if ($includeWiki && $wikiMeta) {
        $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['updated_at']) ? (string)$wikiMeta['updated_at'] : null) ?? $lastUpdated;
        $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['changed_at']) ? (string)$wikiMeta['changed_at'] : null) ?? $lastUpdated;
    }

    $termTranslations = $includeWiki ? load_wiki_term_translations($pdo) : [];
    $goldenVariantMap = load_golden_variant_map($pdo, [$card]);
    $dbf = $card['dbf'] !== null ? (int)$card['dbf'] : 0;
    respond_cached([
        'data' => card_to_api($card, $wikiMeta, $includeWiki, $termTranslations, $goldenVariantMap[$dbf] ?? null),
    ], $lastUpdated);
}

function api_card_by_dbf(PDO $pdo, string $dbf): void
{
    $includeWiki = include_wiki_requested();
    if (!preg_match('/^\d+$/', $dbf)) {
        respond_error('invalid_parameter', 'dbf должен быть целым неотрицательным числом.', 400);
    }

    $stmt = $pdo->prepare('SELECT * FROM battlegrounds_cards WHERE dbf = :dbf LIMIT 1');
    $stmt->execute(['dbf' => (int)$dbf]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Карта с таким dbf не найдена.', 404);
    }
    $card = attach_horizontal_art_one(
        $pdo,
        $card,
        'battleground_card',
        static fn(array $row): string => (string)$row['card_id']
    );

    $wikiMeta = load_wiki_meta($pdo, (string)$card['card_id']);
    $lastUpdated = (string)$card['updated_at'];
    if ($includeWiki && $wikiMeta) {
        $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['updated_at']) ? (string)$wikiMeta['updated_at'] : null) ?? $lastUpdated;
        $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['changed_at']) ? (string)$wikiMeta['changed_at'] : null) ?? $lastUpdated;
    }

    $termTranslations = $includeWiki ? load_wiki_term_translations($pdo) : [];
    $goldenVariantMap = load_golden_variant_map($pdo, [$card]);
    $cardDbf = $card['dbf'] !== null ? (int)$card['dbf'] : 0;
    respond_cached([
        'data' => card_to_api($card, $wikiMeta, $includeWiki, $termTranslations, $goldenVariantMap[$cardDbf] ?? null),
    ], $lastUpdated);
}

function api_card_wiki(PDO $pdo, string $cardId): void
{
    $cardId = rawurldecode($cardId);
    if ($cardId === '') {
        respond_error('not_found', 'Карта не найдена.', 404);
    }

    $stmt = $pdo->prepare('SELECT card_id, updated_at FROM battlegrounds_cards WHERE card_id = :card_id LIMIT 1');
    $stmt->execute(['card_id' => $cardId]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Карта не найдена.', 404);
    }

    $wikiMeta = load_wiki_meta($pdo, (string)$card['card_id']);
    if (!$wikiMeta) {
        respond_error('not_found', 'Wiki-метаданные для карты еще не синхронизированы.', 404);
    }

    $lastUpdated = max_timestamp((string)$card['updated_at'], isset($wikiMeta['updated_at']) ? (string)$wikiMeta['updated_at'] : null);
    $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['changed_at']) ? (string)$wikiMeta['changed_at'] : null);

    respond_cached(['data' => wiki_meta_to_api($wikiMeta, load_wiki_term_translations($pdo))], $lastUpdated);
}

function api_card_by_dbf_wiki(PDO $pdo, string $dbf): void
{
    if (!preg_match('/^\d+$/', $dbf)) {
        respond_error('invalid_parameter', 'dbf должен быть целым неотрицательным числом.', 400);
    }

    $stmt = $pdo->prepare('SELECT card_id, updated_at FROM battlegrounds_cards WHERE dbf = :dbf LIMIT 1');
    $stmt->execute(['dbf' => (int)$dbf]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Карта с таким dbf не найдена.', 404);
    }

    api_card_wiki($pdo, (string)$card['card_id']);
}

function api_constructed_cards(PDO $pdo): void
{
    $includeWiki = include_wiki_requested();
    $q = trim((string)($_GET['q'] ?? ''));
    $format = trim(strtolower((string)($_GET['format'] ?? 'all')));
    $dbf = int_param(isset($_GET['dbf']) ? (string)$_GET['dbf'] : null, 'dbf', 0, null);
    $collectible = bool_param(isset($_GET['collectible']) ? (string)$_GET['collectible'] : null, 'collectible');
    $updatedSince = datetime_param(isset($_GET['updated_since']) ? (string)$_GET['updated_since'] : null, 'updated_since');
    $cardType = strtoupper(trim((string)($_GET['card_type'] ?? '')));
    $class = strtoupper(trim((string)($_GET['class'] ?? '')));
    $set = strtoupper(trim((string)($_GET['set'] ?? '')));
    $page = int_param(isset($_GET['page']) ? (string)$_GET['page'] : '1', 'page', 1, null) ?? 1;
    $perPage = int_param(isset($_GET['per_page']) ? (string)$_GET['per_page'] : (string)DEFAULT_PER_PAGE, 'per_page', 1, MAX_PER_PAGE) ?? DEFAULT_PER_PAGE;

    if (!in_array($format, ['all', 'standard', 'wild'], true)) {
        respond_error('invalid_parameter', 'format должен быть all, standard или wild.');
    }

    $where = ['EXISTS (SELECT 1 FROM constructed_format_cards active_format WHERE active_format.card_id = c.card_id AND active_format.in_format = 1)'];
    $params = [];
    if ($format !== 'all') {
        $where[] = 'EXISTS (SELECT 1 FROM constructed_format_cards ff WHERE ff.card_id = c.card_id AND ff.format_slug = :format AND ff.in_format = 1)';
        $params['format'] = $format;
    }
    if ($q !== '') {
        $where[] = '(c.name_ru LIKE :q OR c.name_en LIKE :q OR c.card_id LIKE :q OR c.dbf LIKE :q OR c.text_ru LIKE :q OR c.text_en LIKE :q OR c.flavor_ru LIKE :q OR c.flavor_en LIKE :q)';
        $params['q'] = '%' . $q . '%';
    }
    if ($dbf !== null) {
        $where[] = 'c.dbf = :dbf';
        $params['dbf'] = $dbf;
    }
    if ($collectible !== null) {
        $where[] = 'c.collectible = :collectible';
        $params['collectible'] = $collectible;
    }
    if ($cardType !== '') {
        $where[] = 'c.card_type = :card_type';
        $params['card_type'] = $cardType;
    }
    if ($class !== '') {
        $where[] = 'c.class_slug = :class_slug';
        $params['class_slug'] = $class;
    }
    if ($set !== '') {
        $where[] = 'c.card_set = :card_set';
        $params['card_set'] = $set;
    }
    if ($updatedSince !== null) {
        $where[] = 'c.updated_at >= :updated_since';
        $params['updated_since'] = $updatedSince;
    }

    $whereSql = ' WHERE ' . implode(' AND ', $where);
    $fromSql = ' FROM constructed_cards c';
    $countStmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(c.updated_at) AS last_updated' . $fromSql . $whereSql);
    bind_params($countStmt, $params);
    $countStmt->execute();
    $countRow = $countStmt->fetch();
    $total = (int)($countRow['total'] ?? 0);
    $lastUpdated = isset($countRow['last_updated']) && is_string($countRow['last_updated']) ? $countRow['last_updated'] : null;
    $totalPages = max(1, (int)ceil($total / $perPage));
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
    bind_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $cards = $stmt->fetchAll();
    $cards = attach_horizontal_art(
        $pdo,
        $cards,
        'constructed_card',
        static fn(array $row): string => (string)$row['card_id']
    );
    $wikiMap = $includeWiki ? load_constructed_wiki_meta_map($pdo, array_column($cards, 'card_id')) : [];
    $termTranslations = $includeWiki ? load_wiki_term_translations($pdo) : [];

    if ($includeWiki) {
        foreach ($wikiMap as $wikiMeta) {
            $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['updated_at']) ? (string)$wikiMeta['updated_at'] : null);
            $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['changed_at']) ? (string)$wikiMeta['changed_at'] : null);
        }
    }

    respond_cached([
        'data' => array_map(static function (array $card) use ($includeWiki, $wikiMap, $termTranslations): array {
            $cardId = (string)$card['card_id'];
            return constructed_card_to_api($card, $wikiMap[$cardId] ?? null, $includeWiki, $termTranslations);
        }, $cards),
        'pagination' => [
            'page' => $page,
            'per_page' => $perPage,
            'total' => $total,
            'total_pages' => $totalPages,
            'has_next' => $page < $totalPages,
            'has_prev' => $page > 1,
        ],
    ], $lastUpdated);
}

function load_constructed_card(PDO $pdo, string $whereSql, array $params): ?array
{
    $stmt = $pdo->prepare(
        'SELECT c.*, GROUP_CONCAT(DISTINCT f.format_slug ORDER BY f.format_slug) AS formats
        FROM constructed_cards c
        LEFT JOIN constructed_format_cards f ON f.card_id = c.card_id AND f.in_format = 1
        WHERE ' . $whereSql . '
        GROUP BY c.card_id
        LIMIT 1'
    );
    $stmt->execute($params);
    $card = $stmt->fetch();

    return $card ? attach_horizontal_art_one(
        $pdo,
        $card,
        'constructed_card',
        static fn(array $row): string => (string)$row['card_id']
    ) : null;
}

function api_constructed_card(PDO $pdo, string $cardId): void
{
    $includeWiki = include_wiki_requested();
    $cardId = rawurldecode($cardId);
    if ($cardId === '') {
        respond_error('not_found', 'Карта Standard/Wild не найдена.', 404);
    }

    $card = load_constructed_card($pdo, 'c.card_id = :card_id', ['card_id' => $cardId]);
    if (!$card) {
        respond_error('not_found', 'Карта Standard/Wild не найдена.', 404);
    }

    $wikiMeta = load_constructed_wiki_meta($pdo, (string)$card['card_id']);
    $lastUpdated = (string)$card['updated_at'];
    if ($includeWiki && $wikiMeta) {
        $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['updated_at']) ? (string)$wikiMeta['updated_at'] : null) ?? $lastUpdated;
        $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['changed_at']) ? (string)$wikiMeta['changed_at'] : null) ?? $lastUpdated;
    }

    $termTranslations = $includeWiki ? load_wiki_term_translations($pdo) : [];
    $relatedGroups = load_constructed_related_groups($pdo, $wikiMeta);
    foreach ($relatedGroups as $group) {
        foreach (($group['cards'] ?? []) as $relatedCard) {
            $lastUpdated = max_timestamp($lastUpdated, $relatedCard['updated_at'] ?? null) ?? $lastUpdated;
        }
    }
    respond_cached(
        ['data' => constructed_card_to_api($card, $wikiMeta, $includeWiki, $termTranslations, $relatedGroups)],
        $lastUpdated
    );
}

function api_constructed_card_by_dbf(PDO $pdo, string $dbf): void
{
    $includeWiki = include_wiki_requested();
    if (!preg_match('/^\d+$/', $dbf)) {
        respond_error('invalid_parameter', 'dbf должен быть целым неотрицательным числом.', 400);
    }

    $card = load_constructed_card($pdo, 'c.dbf = :dbf', ['dbf' => (int)$dbf]);
    if (!$card) {
        respond_error('not_found', 'Карта Standard/Wild с таким dbf не найдена.', 404);
    }

    $wikiMeta = load_constructed_wiki_meta($pdo, (string)$card['card_id']);
    $lastUpdated = (string)$card['updated_at'];
    if ($includeWiki && $wikiMeta) {
        $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['updated_at']) ? (string)$wikiMeta['updated_at'] : null) ?? $lastUpdated;
        $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['changed_at']) ? (string)$wikiMeta['changed_at'] : null) ?? $lastUpdated;
    }

    $termTranslations = $includeWiki ? load_wiki_term_translations($pdo) : [];
    $relatedGroups = load_constructed_related_groups($pdo, $wikiMeta);
    foreach ($relatedGroups as $group) {
        foreach (($group['cards'] ?? []) as $relatedCard) {
            $lastUpdated = max_timestamp($lastUpdated, $relatedCard['updated_at'] ?? null) ?? $lastUpdated;
        }
    }
    respond_cached(
        ['data' => constructed_card_to_api($card, $wikiMeta, $includeWiki, $termTranslations, $relatedGroups)],
        $lastUpdated
    );
}

function api_constructed_card_wiki(PDO $pdo, string $cardId): void
{
    $cardId = rawurldecode($cardId);
    if ($cardId === '') {
        respond_error('not_found', 'Карта Standard/Wild не найдена.', 404);
    }

    $stmt = $pdo->prepare('SELECT card_id, updated_at FROM constructed_cards WHERE card_id = :card_id LIMIT 1');
    $stmt->execute(['card_id' => $cardId]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Карта Standard/Wild не найдена.', 404);
    }

    $wikiMeta = load_constructed_wiki_meta($pdo, (string)$card['card_id']);
    if (!$wikiMeta) {
        respond_error('not_found', 'Wiki-метаданные для карты Standard/Wild еще не синхронизированы.', 404);
    }

    $lastUpdated = max_timestamp((string)$card['updated_at'], isset($wikiMeta['updated_at']) ? (string)$wikiMeta['updated_at'] : null);
    $lastUpdated = max_timestamp($lastUpdated, isset($wikiMeta['changed_at']) ? (string)$wikiMeta['changed_at'] : null);

    respond_cached(['data' => constructed_wiki_meta_to_api($wikiMeta, load_wiki_term_translations($pdo))], $lastUpdated);
}

function api_constructed_card_by_dbf_wiki(PDO $pdo, string $dbf): void
{
    if (!preg_match('/^\d+$/', $dbf)) {
        respond_error('invalid_parameter', 'dbf должен быть целым неотрицательным числом.', 400);
    }

    $stmt = $pdo->prepare('SELECT card_id FROM constructed_cards WHERE dbf = :dbf LIMIT 1');
    $stmt->execute(['dbf' => (int)$dbf]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Карта Standard/Wild с таким dbf не найдена.', 404);
    }

    api_constructed_card_wiki($pdo, (string)$card['card_id']);
}

function api_diamond_cards(PDO $pdo): void
{
    $q = trim((string)($_GET['q'] ?? ''));
    $format = trim(strtolower((string)($_GET['format'] ?? 'all')));
    $section = trim(strtolower((string)($_GET['section'] ?? 'all')));
    $hasAnimated = bool_param(isset($_GET['has_animated']) ? (string)$_GET['has_animated'] : null, 'has_animated');
    $updatedSince = datetime_param(isset($_GET['updated_since']) ? (string)$_GET['updated_since'] : null, 'updated_since');
    $page = int_param(isset($_GET['page']) ? (string)$_GET['page'] : '1', 'page', 1, null) ?? 1;
    $perPage = int_param(isset($_GET['per_page']) ? (string)$_GET['per_page'] : (string)DEFAULT_PER_PAGE, 'per_page', 1, MAX_PER_PAGE) ?? DEFAULT_PER_PAGE;

    if (!in_array($format, ['all', 'standard', 'wild'], true)) {
        respond_error('invalid_parameter', 'format должен быть all, standard или wild.');
    }
    if (!in_array($section, ['all', 'collectible', 'uncollectible'], true)) {
        respond_error('invalid_parameter', 'section должен быть all, collectible или uncollectible.');
    }

    $where = [];
    $params = [];
    if ($q !== '') {
        $where[] = '(name_ru LIKE :q OR name_en LIKE :q OR base_card_id LIKE :q OR diamond_card_id LIKE :q OR base_dbf LIKE :q)';
        $params['q'] = '%' . $q . '%';
    }
    if ($format === 'standard') {
        $where[] = 'in_standard = 1';
    } elseif ($format === 'wild') {
        $where[] = 'in_wild = 1';
    }
    if ($section !== 'all') {
        $where[] = 'section_slug = :section';
        $params['section'] = $section;
    }
    if ($hasAnimated !== null) {
        $where[] = $hasAnimated ? "(animated_url IS NOT NULL AND animated_url <> '')" : "(animated_url IS NULL OR animated_url = '')";
    }
    if ($updatedSince !== null) {
        $where[] = 'updated_at >= :updated_since';
        $params['updated_since'] = $updatedSince;
    }

    $whereSql = $where ? ' WHERE ' . implode(' AND ', $where) : '';
    $countStmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(updated_at) AS last_updated FROM constructed_diamond_cards' . $whereSql);
    bind_params($countStmt, $params);
    $countStmt->execute();
    $countRow = $countStmt->fetch();
    $total = (int)($countRow['total'] ?? 0);
    $lastUpdated = isset($countRow['last_updated']) && is_string($countRow['last_updated']) ? $countRow['last_updated'] : null;
    $totalPages = max(1, (int)ceil($total / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $stmt = $pdo->prepare(
        'SELECT * FROM constructed_diamond_cards' . $whereSql .
        ' ORDER BY collectible DESC, name_ru IS NULL, name_ru ASC, name_en ASC LIMIT :limit OFFSET :offset'
    );
    bind_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $cards = $stmt->fetchAll();

    respond_cached([
        'data' => array_map(static fn(array $card): array => diamond_card_to_api($card), $cards),
        'pagination' => [
            'page' => $page,
            'per_page' => $perPage,
            'total' => $total,
            'total_pages' => $totalPages,
            'has_next' => $page < $totalPages,
            'has_prev' => $page > 1,
        ],
    ], $lastUpdated);
}

function api_diamond_card(PDO $pdo, string $cardId): void
{
    $cardId = rawurldecode($cardId);
    if ($cardId === '') {
        respond_error('not_found', 'Алмазная карта не найдена.', 404);
    }
    $stmt = $pdo->prepare('SELECT * FROM constructed_diamond_cards WHERE base_card_id = :base_card_id OR diamond_card_id = :diamond_card_id LIMIT 1');
    $stmt->execute(['base_card_id' => $cardId, 'diamond_card_id' => $cardId]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Алмазная карта не найдена.', 404);
    }

    respond_cached(['data' => diamond_card_to_api($card)], (string)$card['updated_at']);
}

function api_heroes(PDO $pdo): void
{
    $q = trim((string)($_GET['q'] ?? ''));
    $dbf = int_param(isset($_GET['dbf']) ? (string)$_GET['dbf'] : null, 'dbf', 0, null);
    $updatedSince = datetime_param(isset($_GET['updated_since']) ? (string)$_GET['updated_since'] : null, 'updated_since');
    $page = int_param(isset($_GET['page']) ? (string)$_GET['page'] : '1', 'page', 1, null) ?? 1;
    $perPage = int_param(isset($_GET['per_page']) ? (string)$_GET['per_page'] : (string)DEFAULT_PER_PAGE, 'per_page', 1, MAX_PER_PAGE) ?? DEFAULT_PER_PAGE;

    $where = ["status IN ('ok', 'partial')"];
    $params = [];
    if ($q !== '') {
        $where[] = '(name_en LIKE :q_name_en OR name_ru LIKE :q_name_ru OR card_id LIKE :q_card_id OR dbf LIKE :q_dbf OR hero_power_json LIKE :q_power OR buddy_json LIKE :q_buddy)';
        $params['q_name_en'] = '%' . $q . '%';
        $params['q_name_ru'] = '%' . $q . '%';
        $params['q_card_id'] = '%' . $q . '%';
        $params['q_dbf'] = '%' . $q . '%';
        $params['q_power'] = '%' . $q . '%';
        $params['q_buddy'] = '%' . $q . '%';
    }
    if ($dbf !== null) {
        $where[] = 'dbf = :dbf';
        $params['dbf'] = $dbf;
    }
    if ($updatedSince !== null) {
        $where[] = 'updated_at >= :updated_since';
        $params['updated_since'] = $updatedSince;
    }

    $whereSql = ' WHERE ' . implode(' AND ', $where);
    $countStmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(updated_at) AS last_updated FROM battlegrounds_heroes' . $whereSql);
    bind_params($countStmt, $params);
    $countStmt->execute();
    $countRow = $countStmt->fetch();
    $total = (int)($countRow['total'] ?? 0);
    $lastUpdated = isset($countRow['last_updated']) && is_string($countRow['last_updated']) ? $countRow['last_updated'] : null;
    $totalPages = max(1, (int)ceil($total / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_heroes' . $whereSql . ' ORDER BY name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $heroes = $stmt->fetchAll();
    $heroes = attach_horizontal_art(
        $pdo,
        $heroes,
        'hero',
        static fn(array $row): string => (string)$row['card_id']
    );

    respond_cached([
        'data' => array_map(static fn(array $hero): array => hero_to_api($hero), $heroes),
        'pagination' => [
            'page' => $page,
            'per_page' => $perPage,
            'total' => $total,
            'total_pages' => $totalPages,
            'has_next' => $page < $totalPages,
            'has_prev' => $page > 1,
        ],
    ], $lastUpdated);
}

function api_hero(PDO $pdo, string $cardId): void
{
    $cardId = rawurldecode($cardId);
    if ($cardId === '') {
        respond_error('not_found', 'Герой не найден.', 404);
    }

    $stmt = $pdo->prepare("SELECT * FROM battlegrounds_heroes WHERE card_id = :card_id AND status = 'ok' LIMIT 1");
    $stmt->execute(['card_id' => $cardId]);
    $hero = $stmt->fetch();
    if (!$hero) {
        respond_error('not_found', 'Герой не найден или еще не синхронизирован.', 404);
    }
    $hero = attach_horizontal_art_one(
        $pdo,
        $hero,
        'hero',
        static fn(array $row): string => (string)$row['card_id']
    );

    respond_cached(['data' => hero_to_api($hero)], (string)$hero['updated_at']);
}

function api_hero_by_dbf(PDO $pdo, string $dbf): void
{
    if (!preg_match('/^\d+$/', $dbf)) {
        respond_error('invalid_parameter', 'dbf должен быть целым неотрицательным числом.', 400);
    }

    $stmt = $pdo->prepare("SELECT * FROM battlegrounds_heroes WHERE dbf = :dbf AND status = 'ok' LIMIT 1");
    $stmt->execute(['dbf' => (int)$dbf]);
    $hero = $stmt->fetch();
    if (!$hero) {
        respond_error('not_found', 'Герой с таким dbf не найден или еще не синхронизирован.', 404);
    }
    $hero = attach_horizontal_art_one(
        $pdo,
        $hero,
        'hero',
        static fn(array $row): string => (string)$row['card_id']
    );

    respond_cached(['data' => hero_to_api($hero)], (string)$hero['updated_at']);
}

function api_hero_skins(PDO $pdo): void
{
    $q = trim((string)($_GET['q'] ?? ''));
    $dbf = int_param(isset($_GET['dbf']) ? (string)$_GET['dbf'] : null, 'dbf', 0, null);
    $updatedSince = datetime_param(isset($_GET['updated_since']) ? (string)$_GET['updated_since'] : null, 'updated_since');
    $class = strtolower(trim((string)($_GET['class'] ?? '')));
    $category = strtolower(trim((string)($_GET['category'] ?? '')));
    $rarity = strtolower(trim((string)($_GET['rarity'] ?? '')));
    $hasAnimated = bool_param(isset($_GET['has_animated']) ? (string)$_GET['has_animated'] : null, 'has_animated');
    $hasGallery = bool_param(isset($_GET['has_gallery']) ? (string)$_GET['has_gallery'] : null, 'has_gallery');
    $hasSounds = bool_param(isset($_GET['has_sounds']) ? (string)$_GET['has_sounds'] : null, 'has_sounds');
    $page = int_param(isset($_GET['page']) ? (string)$_GET['page'] : '1', 'page', 1, null) ?? 1;
    $perPage = int_param(isset($_GET['per_page']) ? (string)$_GET['per_page'] : (string)DEFAULT_PER_PAGE, 'per_page', 1, MAX_PER_PAGE) ?? DEFAULT_PER_PAGE;
    $summary = strtolower(trim((string)($_GET['view'] ?? ''))) === 'summary';

    $where = ["status IN ('ok', 'partial')"];
    $params = [];
    if ($q !== '') {
        $where[] = '(name_en LIKE :q_skin_name OR card_id LIKE :q_skin_card_id OR dbf LIKE :q_skin_dbf OR character_name LIKE :q_skin_character OR actor LIKE :q_skin_actor OR artist LIKE :q_skin_artist OR rarity_slug LIKE :q_skin_rarity OR rarity_name_en LIKE :q_skin_rarity_name OR rarity_name_ru LIKE :q_skin_rarity_ru OR tags_json LIKE :q_skin_tags OR categories_json LIKE :q_skin_categories)';
        $params['q_skin_name'] = '%' . $q . '%';
        $params['q_skin_card_id'] = '%' . $q . '%';
        $params['q_skin_dbf'] = '%' . $q . '%';
        $params['q_skin_character'] = '%' . $q . '%';
        $params['q_skin_actor'] = '%' . $q . '%';
        $params['q_skin_artist'] = '%' . $q . '%';
        $params['q_skin_rarity'] = '%' . $q . '%';
        $params['q_skin_rarity_name'] = '%' . $q . '%';
        $params['q_skin_rarity_ru'] = '%' . $q . '%';
        $params['q_skin_tags'] = '%' . $q . '%';
        $params['q_skin_categories'] = '%' . $q . '%';
    }
    if ($dbf !== null) {
        $where[] = 'dbf = :dbf';
        $params['dbf'] = $dbf;
    }
    if ($class !== '') {
        $where[] = '(class_slug = :class_slug OR class_name_en = :class_name OR class_name_ru = :class_name_ru)';
        $params['class_slug'] = $class;
        $params['class_name'] = $class;
        $params['class_name_ru'] = $class;
    }
    if ($category !== '') {
        $where[] = '(primary_category_slug = :category OR JSON_CONTAINS(categories_json, JSON_OBJECT("slug", :category_json)))';
        $params['category'] = $category;
        $params['category_json'] = $category;
    }
    if ($rarity !== '') {
        $where[] = 'rarity_slug = :rarity';
        $params['rarity'] = $rarity;
    }
    if ($hasAnimated !== null) {
        $where[] = $hasAnimated
            ? "((animated_image_url IS NOT NULL AND animated_image_url <> '') OR COALESCE(JSON_LENGTH(animated_asset_json), 0) > 0)"
            : "((animated_image_url IS NULL OR animated_image_url = '') AND COALESCE(JSON_LENGTH(animated_asset_json), 0) = 0)";
    }
    if ($hasGallery !== null) {
        $where[] = $hasGallery ? "COALESCE(JSON_LENGTH(gallery_json), 0) > 0" : "COALESCE(JSON_LENGTH(gallery_json), 0) = 0";
    }
    if ($hasSounds !== null) {
        $where[] = $hasSounds ? "COALESCE(JSON_LENGTH(sounds_json), 0) > 0" : "COALESCE(JSON_LENGTH(sounds_json), 0) = 0";
    }
    if ($updatedSince !== null) {
        $where[] = 'updated_at >= :updated_since';
        $params['updated_since'] = $updatedSince;
    }

    $whereSql = ' WHERE ' . implode(' AND ', $where);
    $countStmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(updated_at) AS last_updated FROM hero_skins' . $whereSql);
    bind_params($countStmt, $params);
    $countStmt->execute();
    $countRow = $countStmt->fetch();
    $total = (int)($countRow['total'] ?? 0);
    $lastUpdated = isset($countRow['last_updated']) && is_string($countRow['last_updated']) ? $countRow['last_updated'] : null;
    $totalPages = max(1, (int)ceil($total / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM hero_skins' . $whereSql
        . ' ORDER BY class_name_en IS NULL, class_name_en ASC, name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $skins = $stmt->fetchAll();
    $skins = attach_horizontal_art(
        $pdo,
        $skins,
        'hero_skin',
        static fn(array $row): string => (string)$row['card_id']
    );

    respond_cached([
        'data' => array_map(
            $summary
                ? static fn(array $skin): array => hero_skin_summary_to_api($skin)
                : static fn(array $skin): array => hero_skin_to_api($skin),
            $skins
        ),
        'pagination' => [
            'page' => $page,
            'per_page' => $perPage,
            'total' => $total,
            'total_pages' => $totalPages,
            'has_next' => $page < $totalPages,
            'has_prev' => $page > 1,
        ],
    ], $lastUpdated);
}

function api_hero_skin(PDO $pdo, string $cardId): void
{
    $cardId = rawurldecode($cardId);
    if ($cardId === '') {
        respond_error('not_found', 'Скин героя не найден.', 404);
    }

    $stmt = $pdo->prepare("SELECT * FROM hero_skins WHERE card_id = :card_id AND status IN ('ok', 'partial') LIMIT 1");
    $stmt->execute(['card_id' => $cardId]);
    $skin = $stmt->fetch();
    if (!$skin) {
        respond_error('not_found', 'Скин героя не найден или еще не синхронизирован.', 404);
    }
    $skin = attach_horizontal_art_one(
        $pdo,
        $skin,
        'hero_skin',
        static fn(array $row): string => (string)$row['card_id']
    );

    respond_cached(['data' => hero_skin_to_api($skin)], (string)$skin['updated_at']);
}

function api_hero_skin_by_dbf(PDO $pdo, string $dbf): void
{
    if (!preg_match('/^\d+$/', $dbf)) {
        respond_error('invalid_parameter', 'dbf должен быть целым неотрицательным числом.', 400);
    }

    $stmt = $pdo->prepare("SELECT * FROM hero_skins WHERE dbf = :dbf AND status IN ('ok', 'partial') LIMIT 1");
    $stmt->execute(['dbf' => (int)$dbf]);
    $skin = $stmt->fetch();
    if (!$skin) {
        respond_error('not_found', 'Скин героя с таким dbf не найден или еще не синхронизирован.', 404);
    }
    $skin = attach_horizontal_art_one(
        $pdo,
        $skin,
        'hero_skin',
        static fn(array $row): string => (string)$row['card_id']
    );

    respond_cached(['data' => hero_skin_to_api($skin)], (string)$skin['updated_at']);
}

function api_pets(PDO $pdo): void
{
    $q = trim((string)($_GET['q'] ?? ''));
    $dbf = int_param(isset($_GET['dbf']) ? (string)$_GET['dbf'] : null, 'dbf', 0, null);
    $petId = int_param(isset($_GET['pet_id']) ? (string)$_GET['pet_id'] : null, 'pet_id', 0, null);
    $level = int_param(isset($_GET['level']) ? (string)$_GET['level'] : null, 'level', 1, 4);
    $hasGallery = bool_param(isset($_GET['has_gallery']) ? (string)$_GET['has_gallery'] : null, 'has_gallery');
    $hasBackground = bool_param(isset($_GET['has_background']) ? (string)$_GET['has_background'] : null, 'has_background');
    $updatedSince = datetime_param(isset($_GET['updated_since']) ? (string)$_GET['updated_since'] : null, 'updated_since');
    $page = int_param(isset($_GET['page']) ? (string)$_GET['page'] : '1', 'page', 1, null) ?? 1;
    $perPage = int_param(isset($_GET['per_page']) ? (string)$_GET['per_page'] : (string)DEFAULT_PER_PAGE, 'per_page', 1, MAX_PER_PAGE) ?? DEFAULT_PER_PAGE;
    $summary = strtolower(trim((string)($_GET['view'] ?? ''))) === 'summary';

    $where = ["status IN ('ok', 'partial')"];
    $params = [];
    if ($q !== '') {
        $where[] = '(pet_name LIKE :q_pet OR variant_name LIKE :q_variant OR card_id LIKE :q_card_id OR dbf LIKE :q_dbf OR page_title LIKE :q_page)';
        $params['q_pet'] = '%' . $q . '%';
        $params['q_variant'] = '%' . $q . '%';
        $params['q_card_id'] = '%' . $q . '%';
        $params['q_dbf'] = '%' . $q . '%';
        $params['q_page'] = '%' . $q . '%';
    }
    if ($dbf !== null) {
        $where[] = 'dbf = :dbf';
        $params['dbf'] = $dbf;
    }
    if ($petId !== null) {
        $where[] = 'pet_id = :pet_id';
        $params['pet_id'] = $petId;
    }
    if ($level !== null) {
        $where[] = 'level = :level';
        $params['level'] = $level;
    }
    if ($hasGallery !== null) {
        $where[] = $hasGallery ? "COALESCE(JSON_LENGTH(gallery_json), 0) > 0" : "COALESCE(JSON_LENGTH(gallery_json), 0) = 0";
    }
    if ($hasBackground !== null) {
        $where[] = $hasBackground ? "end_screen_background_url IS NOT NULL AND end_screen_background_url <> ''" : "(end_screen_background_url IS NULL OR end_screen_background_url = '')";
    }
    if ($updatedSince !== null) {
        $where[] = 'updated_at >= :updated_since';
        $params['updated_since'] = $updatedSince;
    }

    $whereSql = ' WHERE ' . implode(' AND ', $where);
    $countStmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(updated_at) AS last_updated FROM hearthstone_pets' . $whereSql);
    bind_params($countStmt, $params);
    $countStmt->execute();
    $countRow = $countStmt->fetch();
    $total = (int)($countRow['total'] ?? 0);
    $lastUpdated = isset($countRow['last_updated']) && is_string($countRow['last_updated']) ? $countRow['last_updated'] : null;
    $totalPages = max(1, (int)ceil($total / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM hearthstone_pets' . $whereSql
        . ' ORDER BY pet_id ASC, level IS NULL, level ASC, variant_id ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $pets = $stmt->fetchAll();
    $petEntityId = static fn(array $row): string => (string)($row['card_id'] ?: 'variant:' . $row['variant_id']);
    $pets = attach_horizontal_art($pdo, $pets, 'pet', $petEntityId);

    respond_cached([
        'data' => array_map(
            $summary
                ? static fn(array $pet): array => pet_summary_to_api($pet)
                : static fn(array $pet): array => pet_to_api($pet),
            $pets
        ),
        'pagination' => [
            'page' => $page,
            'per_page' => $perPage,
            'total' => $total,
            'total_pages' => $totalPages,
            'has_next' => $page < $totalPages,
            'has_prev' => $page > 1,
        ],
    ], $lastUpdated);
}

function api_pet(PDO $pdo, string $cardId): void
{
    $cardId = rawurldecode($cardId);
    if ($cardId === '') {
        respond_error('not_found', 'Питомец не найден.', 404);
    }
    $stmt = $pdo->prepare("SELECT * FROM hearthstone_pets WHERE card_id = :card_id AND status IN ('ok', 'partial') LIMIT 1");
    $stmt->execute(['card_id' => $cardId]);
    $pet = $stmt->fetch();
    if (!$pet) {
        respond_error('not_found', 'Питомец не найден или еще не синхронизирован.', 404);
    }
    $pet = attach_horizontal_art_one(
        $pdo,
        $pet,
        'pet',
        static fn(array $row): string => (string)($row['card_id'] ?: 'variant:' . $row['variant_id'])
    );
    respond_cached(['data' => pet_to_api($pet)], (string)$pet['updated_at']);
}

function api_pet_by_dbf(PDO $pdo, string $dbf): void
{
    if (!preg_match('/^\d+$/', $dbf)) {
        respond_error('invalid_parameter', 'dbf должен быть целым неотрицательным числом.', 400);
    }
    $stmt = $pdo->prepare("SELECT * FROM hearthstone_pets WHERE dbf = :dbf AND status IN ('ok', 'partial') LIMIT 1");
    $stmt->execute(['dbf' => (int)$dbf]);
    $pet = $stmt->fetch();
    if (!$pet) {
        respond_error('not_found', 'Питомец с таким dbf не найден или еще не синхронизирован.', 404);
    }
    $pet = attach_horizontal_art_one(
        $pdo,
        $pet,
        'pet',
        static fn(array $row): string => (string)($row['card_id'] ?: 'variant:' . $row['variant_id'])
    );
    respond_cached(['data' => pet_to_api($pet)], (string)$pet['updated_at']);
}

function api_coins(PDO $pdo): void
{
    $q = trim((string)($_GET['q'] ?? ''));
    $dbf = int_param(isset($_GET['dbf']) ? (string)$_GET['dbf'] : null, 'dbf', 0, null);
    $updatedSince = datetime_param(isset($_GET['updated_since']) ? (string)$_GET['updated_since'] : null, 'updated_since');
    $page = int_param(isset($_GET['page']) ? (string)$_GET['page'] : '1', 'page', 1, null) ?? 1;
    $perPage = int_param(isset($_GET['per_page']) ? (string)$_GET['per_page'] : (string)DEFAULT_PER_PAGE, 'per_page', 1, MAX_PER_PAGE) ?? DEFAULT_PER_PAGE;
    $summary = strtolower(trim((string)($_GET['view'] ?? ''))) === 'summary';

    $where = [];
    $params = [];
    if ($q !== '') {
        $where[] = '(coin_name_en LIKE :q OR card_name_ru LIKE :q OR card_name_en LIKE :q OR card_id LIKE :q OR dbf LIKE :q OR artist LIKE :q)';
        $params['q'] = '%' . $q . '%';
    }
    if ($dbf !== null) {
        $where[] = 'dbf = :dbf';
        $params['dbf'] = $dbf;
    }
    if ($updatedSince !== null) {
        $where[] = 'updated_at >= :updated_since';
        $params['updated_since'] = $updatedSince;
    }

    $whereSql = $where ? ' WHERE ' . implode(' AND ', $where) : '';
    $countStmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(updated_at) AS last_updated FROM hearthstone_coins' . $whereSql);
    bind_params($countStmt, $params);
    $countStmt->execute();
    $countRow = $countStmt->fetch();
    $total = (int)($countRow['total'] ?? 0);
    $lastUpdated = isset($countRow['last_updated']) && is_string($countRow['last_updated']) ? $countRow['last_updated'] : null;
    $totalPages = max(1, (int)ceil($total / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM hearthstone_coins' . $whereSql
        . ' ORDER BY cosmetic_sort_order IS NULL, cosmetic_sort_order ASC, coin_name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $coins = $stmt->fetchAll();
    $coins = attach_horizontal_art(
        $pdo,
        $coins,
        'coin',
        static fn(array $row): string => (string)$row['card_id']
    );

    $relationRow = $pdo->query('SELECT generated_by_card_ids_json, related_card_ids_json, generated_by_cards_json, related_cards_json FROM hearthstone_coins ORDER BY cosmetic_sort_order ASC LIMIT 1')->fetch();

    respond_cached([
        'data' => array_map(
            $summary
                ? static fn(array $coin): array => coin_summary_to_api($coin)
                : static fn(array $coin): array => coin_to_api($coin),
            $coins
        ),
        'relations' => [
            'generated_by_card_ids' => $relationRow ? json_field($relationRow['generated_by_card_ids_json'] ?? null) : [],
            'related_card_ids' => $relationRow ? json_field($relationRow['related_card_ids_json'] ?? null) : [],
            'generated_by_cards' => $relationRow ? json_field($relationRow['generated_by_cards_json'] ?? null) : [],
            'related_cards' => $relationRow ? json_field($relationRow['related_cards_json'] ?? null) : [],
        ],
        'pagination' => [
            'page' => $page,
            'per_page' => $perPage,
            'total' => $total,
            'total_pages' => $totalPages,
            'has_next' => $page < $totalPages,
            'has_prev' => $page > 1,
        ],
    ], $lastUpdated);
}

function api_coin(PDO $pdo, string $cardId): void
{
    $cardId = rawurldecode($cardId);
    if ($cardId === '') {
        respond_error('not_found', 'Монетка не найдена.', 404);
    }
    $stmt = $pdo->prepare('SELECT * FROM hearthstone_coins WHERE card_id = :card_id LIMIT 1');
    $stmt->execute(['card_id' => $cardId]);
    $coin = $stmt->fetch();
    if (!$coin) {
        respond_error('not_found', 'Монетка не найдена или еще не синхронизирована.', 404);
    }
    $coin = attach_horizontal_art_one(
        $pdo,
        $coin,
        'coin',
        static fn(array $row): string => (string)$row['card_id']
    );
    respond_cached(['data' => coin_to_api($coin)], (string)$coin['updated_at']);
}

function api_coin_by_dbf(PDO $pdo, string $dbf): void
{
    if (!preg_match('/^\d+$/', $dbf)) {
        respond_error('invalid_parameter', 'dbf должен быть целым неотрицательным числом.', 400);
    }
    $stmt = $pdo->prepare('SELECT * FROM hearthstone_coins WHERE dbf = :dbf LIMIT 1');
    $stmt->execute(['dbf' => (int)$dbf]);
    $coin = $stmt->fetch();
    if (!$coin) {
        respond_error('not_found', 'Монетка с таким dbf не найдена или еще не синхронизирована.', 404);
    }
    $coin = attach_horizontal_art_one(
        $pdo,
        $coin,
        'coin',
        static fn(array $row): string => (string)$row['card_id']
    );
    respond_cached(['data' => coin_to_api($coin)], (string)$coin['updated_at']);
}

function api_timewarped_cards(PDO $pdo): void
{
    $q = trim((string)($_GET['q'] ?? ''));
    $tier = int_param(isset($_GET['tier']) ? (string)$_GET['tier'] : null, 'tier', 1, 7);
    $dbf = int_param(isset($_GET['dbf']) ? (string)$_GET['dbf'] : null, 'dbf', 0, null);
    $updatedSince = datetime_param(isset($_GET['updated_since']) ? (string)$_GET['updated_since'] : null, 'updated_since');
    $cardType = trim((string)($_GET['card_type'] ?? ''));
    $page = int_param(isset($_GET['page']) ? (string)$_GET['page'] : '1', 'page', 1, null) ?? 1;
    $perPage = int_param(isset($_GET['per_page']) ? (string)$_GET['per_page'] : (string)DEFAULT_PER_PAGE, 'per_page', 1, MAX_PER_PAGE) ?? DEFAULT_PER_PAGE;

    $where = ["status = 'ok'"];
    $params = [];

    if ($q !== '') {
        $where[] = '(name_en LIKE :q OR name_ru LIKE :q OR card_id LIKE :q OR dbf LIKE :q OR text_en LIKE :q OR text_ru LIKE :q)';
        $params['q'] = '%' . $q . '%';
    }
    if ($tier !== null) {
        $where[] = 'tavern_tier = :tier';
        $params['tier'] = $tier;
    }
    if ($dbf !== null) {
        $where[] = 'dbf = :dbf';
        $params['dbf'] = $dbf;
    }
    if ($cardType !== '') {
        $allowed = ['minion', 'spell', 'hero_power'];
        if (!in_array($cardType, $allowed, true)) {
            respond_error('invalid_parameter', 'Неизвестный card_type для timewarped-карт.', 400, [
                'allowed' => $allowed,
            ]);
        }
        $where[] = 'card_type = :card_type';
        $params['card_type'] = $cardType;
    }
    if ($updatedSince !== null) {
        $where[] = 'updated_at >= :updated_since';
        $params['updated_since'] = $updatedSince;
    }

    $whereSql = ' WHERE ' . implode(' AND ', $where);
    $countStmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(updated_at) AS last_updated FROM battlegrounds_timewarped_cards' . $whereSql);
    bind_params($countStmt, $params);
    $countStmt->execute();
    $countRow = $countStmt->fetch();
    $total = (int)($countRow['total'] ?? 0);
    $lastUpdated = isset($countRow['last_updated']) && is_string($countRow['last_updated']) ? $countRow['last_updated'] : null;
    $totalPages = max(1, (int)ceil($total / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_timewarped_cards' . $whereSql
        . ' ORDER BY tavern_tier IS NULL, tavern_tier ASC, card_type ASC, name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $cards = $stmt->fetchAll();
    $cards = attach_horizontal_art(
        $pdo,
        $cards,
        'timewarped_card',
        static fn(array $row): string => (string)$row['card_id']
    );
    $translations = load_wiki_term_translations($pdo);

    respond_cached([
        'data' => array_map(static fn(array $card): array => timewarped_card_to_api($card, $translations), $cards),
        'pagination' => [
            'page' => $page,
            'per_page' => $perPage,
            'total' => $total,
            'total_pages' => $totalPages,
            'has_next' => $page < $totalPages,
            'has_prev' => $page > 1,
        ],
    ], $lastUpdated);
}

function api_timewarped_card(PDO $pdo, string $cardId): void
{
    $cardId = rawurldecode($cardId);
    if ($cardId === '') {
        respond_error('not_found', 'Хрономальная карта не найдена.', 404);
    }

    $stmt = $pdo->prepare("SELECT * FROM battlegrounds_timewarped_cards WHERE card_id = :card_id AND status = 'ok' LIMIT 1");
    $stmt->execute(['card_id' => $cardId]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Хрономальная карта не найдена или еще не синхронизирована.', 404);
    }
    $card = attach_horizontal_art_one(
        $pdo,
        $card,
        'timewarped_card',
        static fn(array $row): string => (string)$row['card_id']
    );

    respond_cached(['data' => timewarped_card_to_api($card, load_wiki_term_translations($pdo))], (string)$card['updated_at']);
}

function api_timewarped_card_by_dbf(PDO $pdo, string $dbf): void
{
    if (!preg_match('/^\d+$/', $dbf)) {
        respond_error('invalid_parameter', 'dbf должен быть целым неотрицательным числом.', 400);
    }

    $stmt = $pdo->prepare("SELECT * FROM battlegrounds_timewarped_cards WHERE dbf = :dbf AND status = 'ok' LIMIT 1");
    $stmt->execute(['dbf' => (int)$dbf]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Хрономальная карта с таким dbf не найдена или еще не синхронизирована.', 404);
    }
    $card = attach_horizontal_art_one(
        $pdo,
        $card,
        'timewarped_card',
        static fn(array $row): string => (string)$row['card_id']
    );

    respond_cached(['data' => timewarped_card_to_api($card, load_wiki_term_translations($pdo))], (string)$card['updated_at']);
}

function api_library_cards(PDO $pdo, string $library): void
{
    $library = normalize_library_slug($library) ?? '';
    if ($library === '') {
        respond_error('invalid_parameter', 'Неизвестная библиотека.', 400, [
            'allowed' => array_keys(library_configs()),
        ]);
    }

    $q = trim((string)($_GET['q'] ?? ''));
    $dbf = int_param(isset($_GET['dbf']) ? (string)$_GET['dbf'] : null, 'dbf', 0, null);
    $inPool = bool_param(isset($_GET['in_pool']) ? (string)$_GET['in_pool'] : null, 'in_pool');
    $status = trim((string)($_GET['status'] ?? ''));
    $group = trim(strtolower((string)($_GET['group'] ?? '')));
    $tier = int_param(isset($_GET['tier']) ? (string)$_GET['tier'] : null, 'tier', 1, 4);
    $updatedSince = datetime_param(isset($_GET['updated_since']) ? (string)$_GET['updated_since'] : null, 'updated_since');
    $page = int_param(isset($_GET['page']) ? (string)$_GET['page'] : '1', 'page', 1, null) ?? 1;
    $perPage = int_param(isset($_GET['per_page']) ? (string)$_GET['per_page'] : (string)DEFAULT_PER_PAGE, 'per_page', 1, MAX_PER_PAGE) ?? DEFAULT_PER_PAGE;

    $where = ['library = :library'];
    $params = ['library' => $library];
    if ($q !== '') {
        $where[] = '(name_ru LIKE :q OR name_en LIKE :q OR card_id LIKE :q OR dbf LIKE :q OR text_ru LIKE :q OR text_en LIKE :q)';
        $params['q'] = '%' . $q . '%';
    }
    if ($dbf !== null) {
        $where[] = 'dbf = :dbf';
        $params['dbf'] = $dbf;
    }
    if ($inPool !== null) {
        $where[] = 'in_pool = :in_pool';
        $params['in_pool'] = $inPool;
    }
    if ($status !== '') {
        if (!in_array($status, ['available', 'removed'], true)) {
            respond_error('invalid_parameter', 'status должен быть available или removed.');
        }
        $where[] = 'pool_status = :status';
        $params['status'] = $status;
    }
    if ($group !== '') {
        if (!in_array($group, ['lesser', 'greater'], true)) {
            respond_error('invalid_parameter', 'group должен быть lesser или greater.');
        }
        $where[] = 'group_slug = :group';
        $params['group'] = $group;
    }
    if ($tier !== null) {
        $where[] = 'tier_value = :tier';
        $params['tier'] = $tier;
    }
    if ($updatedSince !== null) {
        $where[] = 'updated_at >= :updated_since';
        $params['updated_since'] = $updatedSince;
    }

    $whereSql = ' WHERE ' . implode(' AND ', $where);
    $countStmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(updated_at) AS last_updated FROM battlegrounds_library_cards' . $whereSql);
    bind_params($countStmt, $params);
    $countStmt->execute();
    $countRow = $countStmt->fetch();
    $total = (int)($countRow['total'] ?? 0);
    $lastUpdated = isset($countRow['last_updated']) && is_string($countRow['last_updated']) ? $countRow['last_updated'] : null;
    $totalPages = max(1, (int)ceil($total / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_library_cards' . $whereSql
        . ' ORDER BY in_pool DESC, sort_order IS NULL, sort_order ASC, name_ru ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $cards = $stmt->fetchAll();
    $libraryEntityId = static fn(array $row): string => (string)$row['library'] . ':' . (string)$row['card_id'];
    $cards = attach_horizontal_art($pdo, $cards, 'library_card', $libraryEntityId);

    respond_cached([
        'data' => array_map(static fn(array $card): array => library_card_to_api($card), $cards),
        'pagination' => [
            'page' => $page,
            'per_page' => $perPage,
            'total' => $total,
            'total_pages' => $totalPages,
            'has_next' => $page < $totalPages,
            'has_prev' => $page > 1,
        ],
    ], $lastUpdated);
}

function api_library_card(PDO $pdo, string $library, string $cardId): void
{
    $library = normalize_library_slug($library) ?? '';
    $cardId = rawurldecode($cardId);
    if ($library === '' || $cardId === '') {
        respond_error('not_found', 'Запись библиотеки не найдена.', 404);
    }
    $stmt = $pdo->prepare('SELECT * FROM battlegrounds_library_cards WHERE library = :library AND card_id = :card_id LIMIT 1');
    $stmt->execute(['library' => $library, 'card_id' => $cardId]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Запись библиотеки не найдена.', 404);
    }
    $card = attach_horizontal_art_one(
        $pdo,
        $card,
        'library_card',
        static fn(array $row): string => (string)$row['library'] . ':' . (string)$row['card_id']
    );

    respond_cached(['data' => library_card_to_api($card)], (string)$card['updated_at']);
}

function api_library_card_by_dbf(PDO $pdo, string $library, string $dbf): void
{
    $library = normalize_library_slug($library) ?? '';
    if ($library === '' || !preg_match('/^\d+$/', $dbf)) {
        respond_error('invalid_parameter', 'Некорректная библиотека или dbf.', 400);
    }
    $stmt = $pdo->prepare('SELECT * FROM battlegrounds_library_cards WHERE library = :library AND dbf = :dbf LIMIT 1');
    $stmt->execute(['library' => $library, 'dbf' => (int)$dbf]);
    $card = $stmt->fetch();
    if (!$card) {
        respond_error('not_found', 'Запись библиотеки с таким dbf не найдена.', 404);
    }
    $card = attach_horizontal_art_one(
        $pdo,
        $card,
        'library_card',
        static fn(array $row): string => (string)$row['library'] . ':' . (string)$row['card_id']
    );

    respond_cached(['data' => library_card_to_api($card)], (string)$card['updated_at']);
}

try {
    $pdo = db($config);
    $path = route_path();

    if ($path === '/api' || $path === '/api/v1') {
        api_index($pdo);
    }
    if ($path === '/api/v1/meta') {
        api_meta($pdo);
    }
    if ($path === '/api/v1/cards') {
        api_cards($pdo);
    }
    if ($path === '/api/v1/heroes') {
        api_heroes($pdo);
    }
    if ($path === '/api/v1/hero-skins') {
        api_hero_skins($pdo);
    }
    if ($path === '/api/v1/pets') {
        api_pets($pdo);
    }
    if ($path === '/api/v1/coins') {
        api_coins($pdo);
    }
    if ($path === '/api/v1/timewarped-cards' || $path === '/api/v1/chronomal-cards') {
        api_timewarped_cards($pdo);
    }
    if ($path === '/api/v1/constructed-cards') {
        api_constructed_cards($pdo);
    }
    if ($path === '/api/v1/diamond-cards') {
        api_diamond_cards($pdo);
    }
    foreach (['anomalies', 'dark-gifts', 'quests', 'darkmoon-prizes', 'rewards', 'trinkets'] as $libraryPath) {
        if ($path === '/api/v1/' . $libraryPath) {
            api_library_cards($pdo, $libraryPath);
        }
        if (preg_match('~^/api/v1/' . preg_quote($libraryPath, '~') . '/by-dbf/([^/]+)$~', $path, $matches)) {
            api_library_card_by_dbf($pdo, $libraryPath, $matches[1]);
        }
        if (preg_match('~^/api/v1/' . preg_quote($libraryPath, '~') . '/([^/]+)$~', $path, $matches)) {
            api_library_card($pdo, $libraryPath, $matches[1]);
        }
    }
    if (preg_match('~^/api/v1/libraries/([^/]+)$~', $path, $matches)) {
        api_library_cards($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/libraries/([^/]+)/by-dbf/([^/]+)$~', $path, $matches)) {
        api_library_card_by_dbf($pdo, $matches[1], $matches[2]);
    }
    if (preg_match('~^/api/v1/libraries/([^/]+)/([^/]+)$~', $path, $matches)) {
        api_library_card($pdo, $matches[1], $matches[2]);
    }
    if (preg_match('~^/api/v1/timewarped-cards/by-dbf/([^/]+)$~', $path, $matches)
        || preg_match('~^/api/v1/chronomal-cards/by-dbf/([^/]+)$~', $path, $matches)) {
        api_timewarped_card_by_dbf($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/timewarped-cards/([^/]+)$~', $path, $matches)
        || preg_match('~^/api/v1/chronomal-cards/([^/]+)$~', $path, $matches)) {
        api_timewarped_card($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/constructed-cards/by-dbf/([^/]+)/wiki$~', $path, $matches)) {
        api_constructed_card_by_dbf_wiki($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/constructed-cards/by-dbf/([^/]+)$~', $path, $matches)) {
        api_constructed_card_by_dbf($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/constructed-cards/([^/]+)/wiki$~', $path, $matches)) {
        api_constructed_card_wiki($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/constructed-cards/([^/]+)$~', $path, $matches)) {
        api_constructed_card($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/diamond-cards/([^/]+)$~', $path, $matches)) {
        api_diamond_card($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/heroes/by-dbf/([^/]+)$~', $path, $matches)) {
        api_hero_by_dbf($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/hero-skins/by-dbf/([^/]+)$~', $path, $matches)) {
        api_hero_skin_by_dbf($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/pets/by-dbf/([^/]+)$~', $path, $matches)) {
        api_pet_by_dbf($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/coins/by-dbf/([^/]+)$~', $path, $matches)) {
        api_coin_by_dbf($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/coins/([^/]+)$~', $path, $matches)) {
        api_coin($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/pets/([^/]+)$~', $path, $matches)) {
        api_pet($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/hero-skins/([^/]+)$~', $path, $matches)) {
        api_hero_skin($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/heroes/([^/]+)$~', $path, $matches)) {
        api_hero($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/cards/by-dbf/([^/]+)/wiki$~', $path, $matches)) {
        api_card_by_dbf_wiki($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/cards/by-dbf/([^/]+)$~', $path, $matches)) {
        api_card_by_dbf($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/cards/([^/]+)/wiki$~', $path, $matches)) {
        api_card_wiki($pdo, $matches[1]);
    }
    if (preg_match('~^/api/v1/cards/([^/]+)$~', $path, $matches)) {
        api_card($pdo, $matches[1]);
    }

    respond_error('not_found', 'Эндпоинт не найден.', 404);
} catch (Throwable $e) {
    error_log('[db-api] ' . get_class($e) . ': ' . $e->getMessage() . ' in ' . $e->getFile() . ':' . $e->getLine());
    respond_error('internal_error', 'Внутренняя ошибка API.', 500);
}
