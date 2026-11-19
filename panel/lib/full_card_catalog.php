<?php
declare(strict_types=1);

/** Catalog identity is independent from collectible status and live format membership. */
function full_catalog_card_to_api(array $row): array
{
    $ru = json_decode($row['payload_ru'], true, 512, JSON_THROW_ON_ERROR);
    $en = json_decode($row['payload_en'], true, 512, JSON_THROW_ON_ERROR);
    $card = array_replace($en, $ru);
    $renderable = in_array($card['type'] ?? '', FULL_CATALOG_HSJ_RENDER_TYPES, true);
    return [
        'card_id' => $row['card_id'], 'dbf' => (int)$row['dbf'],
        'collectible' => (bool)$row['collectible'], 'formats' => [],
        'name' => ['ru' => $ru['name'] ?? null, 'en' => $en['name'] ?? null],
        'text' => ['ru' => $ru['text'] ?? null, 'en' => $en['text'] ?? null],
        'flavor' => ['ru' => $ru['flavor'] ?? null, 'en' => $en['flavor'] ?? null],
        'card_type' => ['slug' => $card['type'] ?? null],
        'card_set' => $card['set'] ?? null, 'class' => $card['cardClass'] ?? null,
        'rarity' => $card['rarity'] ?? null, 'mana_cost' => $card['cost'] ?? null,
        'attack' => $card['attack'] ?? null, 'health' => $card['health'] ?? null,
        'durability' => $card['durability'] ?? null, 'armor' => $card['armor'] ?? null,
        'mechanics' => $card['mechanics'] ?? [],
        'images' => ['card' => $renderable
            ? 'https://art.hearthstonejson.com/v1/render/latest/ruRU/512x/' . rawurlencode($row['card_id']) . '.png'
            : null],
        'media_verified' => false, 'source' => 'hearthstonejson.com',
        'catalog_only' => true, 'updated_at' => $row['updated_at'],
        'hearthstonejson' => ['ruRU' => $ru, 'enUS' => $en],
    ];
}

const FULL_CATALOG_LOCALES = ['ruRU', 'enUS', 'plPL', 'esES'];

/** Validates `?locale`; null when absent. Case-sensitive, no silent fallback. */
function full_catalog_locale(array $query): ?string
{
    if (!array_key_exists('locale', $query)) return null;
    $locale = $query['locale'];
    if (!is_string($locale) || !in_array($locale, FULL_CATALOG_LOCALES, true)) {
        throw new InvalidArgumentException('Unsupported locale; expected one of ' . implode(', ', FULL_CATALOG_LOCALES));
    }
    return $locale;
}

const FULL_CATALOG_HSJ_RENDER_TYPES = ['MINION', 'SPELL', 'WEAPON', 'HERO', 'HERO_POWER', 'LOCATION'];
const FULL_CATALOG_RENDER_BASE = 'https://api.kolodahearthstone.com/uploads/card-renders/';

/**
 * Render manifest of one locale written by the card-renders loader (var/card-renders/{locale}.index.json).
 * Read at most once per request; a missing, unreadable or invalid file is an empty index. Render files
 * themselves are never touched (after the S3 offload they are symlinks), the index is the source of truth.
 *
 * @return array{renders: array<string,string>, missing: array<string,true>, skip: array<string,true>, mtime: ?int}
 */
function full_catalog_render_index(string $locale): array
{
    static $cache = [];
    if (isset($cache[$locale])) return $cache[$locale];
    $index = ['renders' => [], 'missing' => [], 'skip' => [], 'mtime' => null];
    $path = dirname(__DIR__) . '/var/card-renders/' . $locale . '.index.json';
    $handle = @fopen($path, 'rb');
    if ($handle !== false) {
        $stat = fstat($handle);
        // The mtime counts even when the manifest turns out invalid: the body then changes too (HSJ fallback).
        $index['mtime'] = is_array($stat) ? (int)$stat['mtime'] : null;
        $raw = stream_get_contents($handle, 64 * 1024 * 1024);
        fclose($handle);
        $data = is_string($raw) ? json_decode($raw, true) : null;
        if (is_array($data) && ($data['version'] ?? null) === 1
            && ($data['locale'] ?? null) === $locale && ($data['size'] ?? null) === '512x') {
            foreach (is_array($data['renders'] ?? null) ? $data['renders'] : [] as $id => $v) {
                if (is_string($v) && preg_match('/^[a-f0-9]{12}$/D', $v)) $index['renders'][(string)$id] = $v;
            }
            foreach (is_array($data['missing'] ?? null) ? $data['missing'] : [] as $id) {
                if (is_string($id)) $index['missing'][$id] = true;
            }
            foreach (is_array($data['skip_types'] ?? null) ? $data['skip_types'] : [] as $type) {
                if (is_string($type)) $index['skip'][$type] = true;
            }
        }
    }
    return $cache[$locale] = $index;
}

/** Localized render link of the requested locale (never the text fallback locale). */
function full_catalog_render_fields(string $cardId, ?string $type, string $locale): array
{
    $index = full_catalog_render_index($locale);
    $id = rawurlencode($cardId);
    if (isset($index['renders'][$cardId]) && !isset($index['skip'][(string)$type])) {
        return ['render_url' => FULL_CATALOG_RENDER_BASE . $locale . '/512x/' . $id . '.webp?v=' . $index['renders'][$cardId],
            'render_media_verified' => true];
    }
    if (isset($index['missing'][$cardId]) || isset($index['skip'][(string)$type])
        || !in_array($type, FULL_CATALOG_HSJ_RENDER_TYPES, true)) {
        return ['render_url' => null, 'render_media_verified' => false];
    }
    return ['render_url' => 'https://art.hearthstonejson.com/v1/render/latest/' . $locale . '/512x/' . $id . '.png',
        'render_media_verified' => false];
}

/** Later of the current Last-Modified value and the locale index mtime (UTC datetime string). */
function full_catalog_render_modified(?string $modified, string $locale): ?string
{
    $mtime = full_catalog_render_index($locale)['mtime'];
    if ($mtime === null) return $modified;
    $stamp = gmdate('Y-m-d H:i:s', $mtime);
    return $modified === null || $stamp > $modified ? $stamp : $modified;
}

/** Adds the additive `localized` block; a missing or unreadable locale row falls back to enUS. */
function full_catalog_with_localized(array $card, array $row, string $locale, ?string $payload): array
{
    $source = null;
    if (is_string($payload) && $payload !== '') {
        try {
            $decoded = json_decode($payload, true, 512, JSON_THROW_ON_ERROR);
            if (is_array($decoded)) $source = $decoded;
        } catch (JsonException) {
        }
    }
    $fallback = $source === null;
    if ($fallback) $source = $card['hearthstonejson']['enUS'];
    $card['localized'] = [
        'locale' => $fallback ? 'enUS' : $locale, 'requested_locale' => $locale, 'fallback' => $fallback,
        'name' => $source['name'] ?? null, 'text' => $source['text'] ?? null, 'flavor' => $source['flavor'] ?? null,
        'collection_text' => $source['collectionText'] ?? null, 'how_to_earn' => $source['howToEarn'] ?? null,
        'how_to_earn_golden' => $source['howToEarnGolden'] ?? null,
        'targeting_arrow_text' => $source['targetingArrowText'] ?? null,
    ] + full_catalog_render_fields((string)$row['card_id'], $card['card_type']['slug'], $locale);
    return $card;
}

function full_catalog_is_missing_table(PDOException $error): bool
{
    return ($error->errorInfo[0] ?? '') === '42S02';
}

function full_catalog_find(PDO $pdo, string $key, string $value, ?string $locale = null, ?string &$modified = null): ?array
{
    if (!in_array($key, ['card_id', 'dbf'], true)) {
        throw new InvalidArgumentException('Invalid catalog key');
    }
    $withLocale = $locale !== null;
    while (true) {
        try {
            $sql = $withLocale
                ? 'SELECT c.*, l.payload AS loc_payload, l.updated_at AS loc_updated_at FROM hearthstone_card_catalog c'
                    . ' LEFT JOIN hearthstone_card_localizations l ON l.card_id = c.card_id AND l.locale = :locale'
                    . ' WHERE c.' . $key . ' = :value LIMIT 1'
                : 'SELECT * FROM hearthstone_card_catalog WHERE ' . $key . ' = :value LIMIT 1';
            $stmt = $pdo->prepare($sql);
            $stmt->execute($withLocale ? ['value' => $value, 'locale' => $locale] : ['value' => $value]);
            $row = $stmt->fetch(PDO::FETCH_ASSOC);
            if (!$row) return null;
            $card = full_catalog_card_to_api($row);
            $modified = $row['updated_at'];
            if ($locale !== null) {
                $card = full_catalog_with_localized($card, $row, $locale, $withLocale ? ($row['loc_payload'] ?? null) : null);
                if (($row['loc_updated_at'] ?? null) !== null && $row['loc_updated_at'] > $modified) $modified = $row['loc_updated_at'];
                $modified = full_catalog_render_modified($modified, $locale);
            }
            return $card;
        } catch (PDOException $error) {
            // Existing card endpoints remain available during schema rollout.
            if (!full_catalog_is_missing_table($error)) throw $error;
            // Localizations table not deployed yet: serve the card with enUS fallback.
            if ($withLocale) { $withLocale = false; continue; }
            return null;
        }
    }
}

function full_catalog_listing(PDO $pdo, array $query): array
{
    $locale = full_catalog_locale($query);
    $where = ['1=1'];
    $params = [];
    foreach (['card_type', 'set', 'collectible'] as $key) {
        if (!isset($query[$key]) || $query[$key] === '') continue;
        if (!is_scalar($query[$key])) throw new InvalidArgumentException('Invalid filter');
        $value = (string)$query[$key];
        if ($key === 'collectible' && !in_array($value, ['0', '1'], true)) {
            throw new InvalidArgumentException('collectible must be 0 or 1');
        }
        $column = $key === 'set' ? 'card_set' : $key;
        $where[] = 'c.' . $column . ' = :' . $key;
        $params[$key] = $key === 'collectible' ? $value : strtoupper($value);
    }
    $q = $query['q'] ?? '';
    if (!is_string($q) || strlen($q) > 512) throw new InvalidArgumentException('Invalid search');
    if ($q !== '') {
        $like = '%' . $q . '%';
        $params += ['id' => $like, 'ru' => $like, 'en' => $like];
        $search = 'c.card_id LIKE :id OR c.name_ru LIKE :ru OR c.name_en LIKE :en';
        if ($locale !== null) {
            $params['loc'] = $like;
            $search .= ' OR l.name LIKE :loc';
        }
        $where[] = '(' . $search . ')';
    }
    $numbers = [];
    foreach (['page' => [1, 1000000, 1], 'per_page' => [1, 200, 50]] as $key => [$min, $max, $default]) {
        $value = $query[$key] ?? $default;
        if (!is_scalar($value) || filter_var($value, FILTER_VALIDATE_INT) === false || $value < $min || $value > $max) {
            throw new InvalidArgumentException('Invalid pagination');
        }
        $numbers[$key] = (int)$value;
    }
    ['page' => $page, 'per_page' => $perPage] = $numbers;
    $filter = ' WHERE ' . implode(' AND ', $where);
    $withLocale = $locale !== null;
    while (true) {
        $from = ' FROM hearthstone_card_catalog c' . ($withLocale
            ? ' LEFT JOIN hearthstone_card_localizations l ON l.card_id = c.card_id AND l.locale = :locale' : '');
        $bound = $params;
        if ($withLocale) {
            $bound['locale'] = $locale;
        } else {
            // Without the table the locale name cannot be searched; keep catalog-wide matching.
            unset($bound['loc']);
        }
        $filterSql = $filter;
        if (!$withLocale && $locale !== null) {
            $filterSql = str_replace(' OR l.name LIKE :loc', '', $filter);
        }
        try {
            $stmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(c.updated_at) AS updated_at'
                . ($withLocale ? ', MAX(l.updated_at) AS loc_updated_at' : '') . $from . $filterSql);
            $stmt->execute($bound);
            $summary = $stmt->fetch(PDO::FETCH_ASSOC);
            $stmt = $pdo->prepare('SELECT c.*' . ($withLocale ? ', l.payload AS loc_payload, l.updated_at AS loc_updated_at' : '')
                . $from . $filterSql . ' ORDER BY c.card_id LIMIT :limit OFFSET :offset');
            foreach ($bound as $key => $value) $stmt->bindValue(':' . $key, $value);
            $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
            $stmt->bindValue(':offset', ($page - 1) * $perPage, PDO::PARAM_INT);
            $stmt->execute();
            break;
        } catch (PDOException $error) {
            if (!$withLocale || !full_catalog_is_missing_table($error)) throw $error;
            $withLocale = false; // Localizations table not deployed yet: enUS fallback for every card.
        }
    }
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    $total = (int)$summary['total'];
    $pages = max(1, (int)ceil($total / $perPage));
    $lastUpdated = $summary['updated_at'];
    if ($withLocale && ($summary['loc_updated_at'] ?? null) !== null && $summary['loc_updated_at'] > $lastUpdated) {
        $lastUpdated = $summary['loc_updated_at'];
    }
    if ($locale !== null) $lastUpdated = full_catalog_render_modified($lastUpdated, $locale);
    $cards = array_map(static function (array $row) use ($locale, $withLocale): array {
        $card = full_catalog_card_to_api($row);
        return $locale === null ? $card
            : full_catalog_with_localized($card, $row, $locale, $withLocale ? ($row['loc_payload'] ?? null) : null);
    }, $rows);
    return ['data' => $cards,
        'last_updated' => $lastUpdated,
        'pagination' => ['page' => $page, 'per_page' => $perPage, 'total' => $total,
            'total_pages' => $pages, 'has_next' => $page < $pages]];
}

/** Dispatch only the new full-catalog namespace. */
function api_full_card_catalog(PDO $pdo, string $path): void
{
    if ($path === '/api/v1/card-catalog') {
        try {
            $result = full_catalog_listing($pdo, $_GET);
        } catch (InvalidArgumentException $error) {
            respond_error('invalid_parameter', $error->getMessage(), 400);
        }
        $modified = $result['last_updated'];
        unset($result['last_updated']);
        respond_cached($result, $modified);
    }
    if (preg_match('~^/api/v1/card-catalog/(dbf/)?([^/]+)$~', $path, $matches)) {
        $key = $matches[1] === 'dbf/' ? 'dbf' : 'card_id';
        $value = rawurldecode($matches[2]);
        if ($key === 'dbf' && !preg_match('/^[0-9]{1,10}$/D', $value)) {
            respond_error('invalid_parameter', 'Invalid dbf', 400);
        }
        try {
            $locale = full_catalog_locale($_GET);
        } catch (InvalidArgumentException $error) {
            respond_error('invalid_parameter', $error->getMessage(), 400);
        }
        $modified = null;
        $card = full_catalog_find($pdo, $key, $value, $locale, $modified);
        if (!$card) respond_error('not_found', 'Карта не найдена.', 404);
        respond_cached(['data' => $card], $modified ?? $card['updated_at']);
    }
}
