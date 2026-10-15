<?php
declare(strict_types=1);

/** Catalog identity is independent from collectible status and live format membership. */
function full_catalog_card_to_api(array $row): array
{
    $ru = json_decode($row['payload_ru'], true, 512, JSON_THROW_ON_ERROR);
    $en = json_decode($row['payload_en'], true, 512, JSON_THROW_ON_ERROR);
    $card = array_replace($en, $ru);
    $renderable = in_array($card['type'] ?? '', ['MINION', 'SPELL', 'WEAPON', 'HERO', 'HERO_POWER', 'LOCATION'], true);
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

function full_catalog_find(PDO $pdo, string $key, string $value): ?array
{
    if (!in_array($key, ['card_id', 'dbf'], true)) {
        throw new InvalidArgumentException('Invalid catalog key');
    }
    try {
        $stmt = $pdo->prepare('SELECT * FROM hearthstone_card_catalog WHERE ' . $key . ' = :value LIMIT 1');
        $stmt->execute(['value' => $value]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        return $row ? full_catalog_card_to_api($row) : null;
    } catch (PDOException $error) {
        // Existing card endpoints remain available during schema rollout.
        if (($error->errorInfo[0] ?? '') === '42S02') return null;
        throw $error;
    }
}

function full_catalog_listing(PDO $pdo, array $query): array
{
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
        $where[] = $column . ' = :' . $key;
        $params[$key] = $key === 'collectible' ? $value : strtoupper($value);
    }
    $q = $query['q'] ?? '';
    if (!is_string($q) || strlen($q) > 512) throw new InvalidArgumentException('Invalid search');
    if ($q !== '') {
        $where[] = '(card_id LIKE :id OR name_ru LIKE :ru OR name_en LIKE :en)';
        $params += ['id' => '%' . $q . '%', 'ru' => '%' . $q . '%', 'en' => '%' . $q . '%'];
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
    $stmt = $pdo->prepare('SELECT COUNT(*) AS total, MAX(updated_at) AS updated_at FROM hearthstone_card_catalog' . $filter);
    $stmt->execute($params);
    $summary = $stmt->fetch(PDO::FETCH_ASSOC);
    $stmt = $pdo->prepare('SELECT * FROM hearthstone_card_catalog' . $filter . ' ORDER BY card_id LIMIT :limit OFFSET :offset');
    foreach ($params as $key => $value) $stmt->bindValue(':' . $key, $value);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', ($page - 1) * $perPage, PDO::PARAM_INT);
    $stmt->execute();
    $total = (int)$summary['total'];
    $pages = max(1, (int)ceil($total / $perPage));
    return ['data' => array_map('full_catalog_card_to_api', $stmt->fetchAll(PDO::FETCH_ASSOC)),
        'last_updated' => $summary['updated_at'],
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
        $card = full_catalog_find($pdo, $key, $value);
        if (!$card) respond_error('not_found', 'Карта не найдена.', 404);
        respond_cached(['data' => $card], $card['updated_at']);
    }
}
