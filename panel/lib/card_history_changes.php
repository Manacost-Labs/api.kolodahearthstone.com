<?php
declare(strict_types=1);

require_once __DIR__ . '/card_history.php';

/**
 * Patch-window feed over catalog_card_versions: which cards really changed.
 *
 * A version row exists for every change of the stored record, including
 * service-only ones (local image mirrors, source hashes and payloads,
 * timestamps, case/whitespace flapping of provider text). The feed compares
 * only the player-visible fields below after normalizing them, so those rows
 * never make a card "changed".
 */

const CARD_HISTORY_CHANGES_MAX_DAYS = 14;
const CARD_HISTORY_CHANGES_ENTITIES = ['constructed_card', 'card'];
/** Version ids grow with observed_at; concurrent inserts may be a few rows out of order. */
const CARD_HISTORY_WAVES_MAX_DAYS = 31;
const CARD_HISTORY_WAVE_GAP_HOURS = 18;
const CARD_HISTORY_ID_SKEW = 300;
const CARD_HISTORY_SCAN_CHUNK = 400;
const CARD_HISTORY_SCAN_LIMIT = 60000;
const CARD_HISTORY_EVENTS = "'baseline','changed','renamed'";

/** kind: int | bool | enum (case-insensitive) | name (case-insensitive text) | text. */
const CARD_HISTORY_CHANGE_FIELDS = [
    'constructed_card' => [
        'name_ru' => 'name', 'name_en' => 'name',
        'mana_cost' => 'int', 'attack' => 'int', 'health' => 'int', 'durability' => 'int', 'armor' => 'int',
        'text_ru' => 'text', 'text_en' => 'text',
        'card_type' => 'enum', 'rarity' => 'enum', 'class_slug' => 'enum', 'minion_type' => 'enum',
        'spell_school' => 'enum', 'collectible' => 'bool', 'card_set' => 'enum',
    ],
    // Battlegrounds text is a derived field: see catalog_history_bg_notes().
    'card' => [
        'name' => 'name', 'name_en' => 'name',
        'tavern_tier' => 'int', 'attack' => 'int', 'health' => 'int',
        'text_ru' => 'text', 'text_en' => 'text',
        'card_type' => 'enum', 'creature_type' => 'enum', 'in_pool' => 'bool', 'duos_only' => 'bool',
    ],
];
const CARD_HISTORY_GROUP_FIELDS = [
    'constructed_card' => ['class_slug', 'card_type', 'rarity', 'collectible', 'card_set'],
    'card' => ['card_type', 'tavern_tier', 'creature_type'],
];
const CARD_HISTORY_IMAGE_FIELD = ['constructed_card' => 'image_url', 'card' => 'card_image'];

/** @return array{0: DateTimeImmutable, 1: DateTimeImmutable} UTC [from, to). */
function catalog_history_changes_window(array $query, ?DateTimeImmutable $now = null): array
{
    $now = ($now ?? new DateTimeImmutable('now', new DateTimeZone('UTC')))->setTimezone(new DateTimeZone('UTC'));
    if (!isset($query['from']) || !is_string($query['from']) || $query['from'] === '') {
        throw new InvalidArgumentException('Параметр from обязателен: время UTC, например 2026-11-03T15:55:00Z.');
    }
    $from = catalog_history_parse_instant($query['from'], 'from');
    $to = isset($query['to']) && $query['to'] !== '' ? catalog_history_parse_instant((string)$query['to'], 'to') : $now;
    if ($to <= $from) throw new InvalidArgumentException('Параметр to должен быть позже from.');
    if ($to->getTimestamp() - $from->getTimestamp() > CARD_HISTORY_CHANGES_MAX_DAYS * 86400) {
        throw new InvalidArgumentException('Окно не может быть длиннее ' . CARD_HISTORY_CHANGES_MAX_DAYS . ' суток.');
    }
    return [$from, $to];
}

function catalog_history_parse_instant(string $value, string $name): DateTimeImmutable
{
    $value = trim($value);
    // A "+" in an unescaped query string arrives as a space: 15:55:00 00:00.
    $value = preg_replace('/^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?) (\d{2}:?\d{2})$/', '$1+$2', $value);
    if (!preg_match('/^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/i', $value)) {
        throw new InvalidArgumentException("Параметр $name: ожидается дата-время UTC, например 2026-11-03T15:55:00Z.");
    }
    try {
        $date = new DateTimeImmutable($value, new DateTimeZone('UTC'));
    } catch (Exception $error) {
        throw new InvalidArgumentException("Параметр $name: некорректная дата.");
    }
    if ($date->format('Y-m-d') !== substr($value, 0, 10)) {
        throw new InvalidArgumentException("Параметр $name: несуществующая дата.");
    }
    return $date->setTimezone(new DateTimeZone('UTC'));
}

function catalog_history_clean_text(mixed $value): ?string
{
    if ($value === null || is_array($value)) return null;
    $text = (string)$value;
    // Provider flapping inserts a lone "&" ("менее,& атакует") and re-wraps lines.
    $text = preg_replace('/&(?![a-z]{2,8};|#\d{2,6};)/i', '', $text);
    $text = html_entity_decode($text, ENT_QUOTES | ENT_HTML5, 'UTF-8');
    $text = preg_replace('/^\[x\]/', '', trim($text));
    $text = trim((string)preg_replace('/[\s\x{00A0}]+/u', ' ', $text));
    return $text === '' ? null : $text;
}

/** @return array{0: ?string, 1: ?string} Battlegrounds RU and EN text from the "notes" column. */
function catalog_history_bg_notes(mixed $notes): array
{
    if (!is_string($notes) || trim($notes) === '') return [null, null];
    $parts = preg_split('/\R(?=Механики:|EN:)/u', $notes);
    $ru = $en = null;
    foreach ($parts as $index => $part) {
        if (str_starts_with($part, 'EN:')) $en = substr($part, 3);
        elseif (!str_starts_with($part, 'Механики:') && $index === 0) $ru = $part;
    }
    return [catalog_history_clean_text($ru), catalog_history_clean_text($en)];
}

function catalog_history_normalize(string $kind, mixed $value): int|string|bool|null
{
    if ($value === null || $value === '') return null;
    switch ($kind) {
        case 'int':
            return is_numeric($value) ? (int)$value : null;
        case 'bool':
            return in_array($value, [1, '1', true, 'true'], true) ? true : (in_array($value, [0, '0', false, 'false'], true) ? false : null);
        case 'enum':
            return is_scalar($value) ? strtolower(trim((string)$value)) : null;
        default:
            return catalog_history_clean_text($value);
    }
}

/** Normalized, player-visible fields of one stored snapshot. */
function catalog_history_projection(string $entity, array $snapshot): array
{
    $projection = [];
    if ($entity === 'card') [$snapshot['text_ru'], $snapshot['text_en']] = catalog_history_bg_notes($snapshot['notes'] ?? null);
    foreach (CARD_HISTORY_CHANGE_FIELDS[$entity] as $field => $kind) {
        $projection[$field] = catalog_history_normalize($kind, $snapshot[$field] ?? null);
    }
    $projection['_golden'] = ($snapshot['variant_kind'] ?? 'base') === 'golden';
    return $projection;
}

function catalog_history_field_equal(string $kind, mixed $a, mixed $b): bool
{
    if ($kind === 'name' && is_string($a) && is_string($b)) return mb_strtolower($a) === mb_strtolower($b);
    return $a === $b;
}

/** Fields that differ. A value missing on either side is lost data, not a change. */
function catalog_history_diff(string $entity, array $before, array $after): array
{
    $changed = [];
    foreach (CARD_HISTORY_CHANGE_FIELDS[$entity] as $field => $kind) {
        if ($before[$field] === null || $after[$field] === null) continue;
        if (!catalog_history_field_equal($kind, $before[$field], $after[$field])) $changed[] = $field;
    }
    return $changed;
}

/** Carry forward last known values over fields a later record lacks. */
function catalog_history_overlay(array $base, array $next): array
{
    foreach ($next as $field => $value) {
        if ($value !== null) $base[$field] = $value;
    }
    return $base;
}

function catalog_history_in_list(string $prefix, array $values, array &$bindings): string
{
    $names = [];
    foreach (array_values($values) as $index => $value) {
        $names[] = ':' . $prefix . $index;
        $bindings[$prefix . $index] = $value;
    }
    return implode(',', $names);
}

/**
 * Smallest id n such that the first stored version with id >= n was observed at
 * or after $time. ~17 primary-key probes instead of scanning the table.
 */
function catalog_history_first_id_at(PDO $pdo, string $time, int $minId, int $maxId): int
{
    $stmt = $pdo->prepare('SELECT observed_at >= :at AS hit FROM catalog_card_versions WHERE id >= :id ORDER BY id LIMIT 1');
    $low = $minId;
    $high = $maxId + 1;
    while ($low < $high) {
        $mid = intdiv($low + $high, 2);
        $stmt->execute(['at' => $time, 'id' => $mid]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        if (!$row || (int)$row['hit'] === 1) $high = $mid;
        else $low = $mid + 1;
    }
    return $low;
}

/** Rows are decoded once into projections so memory stays bounded for a 14-day window. */
function catalog_history_version(string $entity, array $row): ?array
{
    $snapshot = json_decode((string)$row['snapshot_json'], true);
    if (!is_array($snapshot)) return null;
    $projection = catalog_history_projection($entity, $snapshot);
    $golden = $projection['_golden'];
    unset($projection['_golden']);
    return ['id' => (int)$row['id'], 'observed_at' => (string)$row['observed_at'], 'event' => (string)$row['event_kind'],
        'p' => $projection, 'golden' => $golden];
}

/**
 * Version rows of the window as per-card timelines of normalized projections.
 * Rows are located by id range and decoded chunk by chunk, so memory stays
 * bounded by the projections, not by the 10 KB snapshots.
 *
 * @return array{0: array, 1: int, 2: ?string} [entity => card_id => versions, rows scanned, last observed_at]
 */
function catalog_history_scan(PDO $pdo, array $entities, string $fromSql, string $toSql): array
{
    $timeline = [];
    $scanned = 0;
    $lastObserved = null;
    $bounds = $pdo->query('SELECT MIN(id) AS min_id, MAX(id) AS max_id FROM catalog_card_versions')->fetch(PDO::FETCH_ASSOC);
    if (!$bounds || $bounds['min_id'] === null) return [$timeline, $scanned, $lastObserved];
    $minId = (int)$bounds['min_id'];
    $maxId = (int)$bounds['max_id'];
    $low = max($minId, catalog_history_first_id_at($pdo, $fromSql, $minId, $maxId) - CARD_HISTORY_ID_SKEW);
    $high = catalog_history_first_id_at($pdo, $toSql, $minId, $maxId) + CARD_HISTORY_ID_SKEW;
    $bindings = [];
    $list = catalog_history_in_list('entity', $entities, $bindings);
    $stmt = $pdo->prepare('SELECT id, entity, card_id, observed_at, event_kind, snapshot_json FROM catalog_card_versions '
        . 'WHERE id > :after AND id <= :high AND observed_at >= :from_at AND observed_at < :to_at '
        . "AND entity IN ($list) AND event_kind IN (" . CARD_HISTORY_EVENTS . ') ORDER BY id LIMIT ' . CARD_HISTORY_SCAN_CHUNK);
    $after = $low - 1;
    do {
        $stmt->execute($bindings + ['after' => $after, 'high' => $high, 'from_at' => $fromSql, 'to_at' => $toSql]);
        $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
        foreach ($rows as $row) {
            $after = (int)$row['id'];
            if (++$scanned > CARD_HISTORY_SCAN_LIMIT) throw new LengthException('Too many versions in the window');
            $version = catalog_history_version((string)$row['entity'], $row);
            if ($version === null || $version['golden']) continue;
            $timeline[(string)$row['entity']][(string)$row['card_id']][] = $version;
            if ($lastObserved === null || $version['observed_at'] > $lastObserved) $lastObserved = $version['observed_at'];
        }
    } while (count($rows) === CARD_HISTORY_SCAN_CHUNK);
    return [$timeline, $scanned, $lastObserved];
}

function catalog_card_history_changes(PDO $pdo, array $entities, DateTimeImmutable $from, DateTimeImmutable $to,
    int $page = 1, int $perPage = 50): array
{
    foreach ($entities as $entity) {
        if (!in_array($entity, CARD_HISTORY_CHANGES_ENTITIES, true)) throw new InvalidArgumentException('Invalid history entity');
    }
    $entities = array_values(array_unique($entities));
    $page = max(1, min(1000000, $page));
    $perPage = max(1, min(200, $perPage));
    $fromSql = $from->format('Y-m-d H:i:s.u');
    $toSql = $to->format('Y-m-d H:i:s.u');

    [$timeline, $scanned, $lastObserved] = catalog_history_scan($pdo, $entities, $fromSql, $toSql);

    $changes = [];
    foreach ($entities as $entity) {
        $cards = $timeline[$entity] ?? [];
        if (!$cards) continue;
        $befores = catalog_history_before_versions($pdo, $entity, array_keys($cards), $fromSql);
        foreach ($cards as $cardId => $versions) {
            $change = catalog_history_card_change($entity, (string)$cardId, $versions, $befores[$cardId] ?? null);
            if ($change !== null) $changes[] = $change;
        }
    }
    usort($changes, static fn(array $a, array $b): int => [array_search($a['entity'], CARD_HISTORY_CHANGES_ENTITIES), $a['card_id']]
        <=> [array_search($b['entity'], CARD_HISTORY_CHANGES_ENTITIES), $b['card_id']]);
    $total = count($changes);
    $pageChanges = array_slice($changes, ($page - 1) * $perPage, $perPage);

    $ids = [];
    foreach ($pageChanges as $change) array_push($ids, $change['before']['version_id'], $change['after']['version_id']);
    $media = catalog_history_media($pdo, array_values(array_unique($ids)));
    foreach ($pageChanges as &$change) {
        foreach (['before', 'after'] as $side) {
            $change[$side]['image'] = catalog_history_changes_image(
                $media[$change[$side]['version_id']][CARD_HISTORY_IMAGE_FIELD[$change['entity']]] ?? null,
                CARD_HISTORY_IMAGE_FIELD[$change['entity']]);
        }
    }
    unset($change);

    return ['window' => ['from' => $from->format('Y-m-d\TH:i:s\Z'), 'to' => $to->format('Y-m-d\TH:i:s\Z'), 'to_exclusive' => true],
        'entities' => $entities, 'changes' => $pageChanges,
        'coverage' => ['before_first_observation' => 'unknown', 'timestamps' => 'observed_at',
            'ignored' => 'service_fields_and_text_formatting', 'versions_scanned' => $scanned,
            'last_observed_at' => $lastObserved,
            'battlegrounds_text' => 'from_snapshot_notes'],
        'pagination' => ['page' => $page, 'per_page' => $perPage, 'total' => $total,
            'total_pages' => (int)ceil($total / $perPage)]];
}

/** Last stored version of each card observed before $fromSql, one bounded query per chunk. */
function catalog_history_before_versions(PDO $pdo, string $entity, array $cardIds, string $fromSql): array
{
    $result = [];
    foreach (array_chunk(array_map('strval', $cardIds), 200) as $chunk) {
        $bindings = ['entity' => $entity, 'from_at' => $fromSql];
        $list = catalog_history_in_list('card', $chunk, $bindings);
        $stmt = $pdo->prepare('SELECT v.id, v.entity, v.card_id, v.observed_at, v.event_kind, v.snapshot_json FROM catalog_card_versions v '
            . 'JOIN (SELECT MAX(id) AS id FROM catalog_card_versions WHERE entity = :entity AND card_id IN (' . $list . ') '
            . 'AND observed_at < :from_at AND event_kind IN (' . CARD_HISTORY_EVENTS . ') GROUP BY card_id) latest ON latest.id = v.id');
        $stmt->execute($bindings);
        foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
            $version = catalog_history_version($entity, $row);
            if ($version !== null) $result[(string)$row['card_id']] = $version;
        }
    }
    return $result;
}

/**
 * before = last version before the window (or the window's own baseline row, which
 * stores the previous state of a card first seen mid-window); after = last version
 * in the window whose visible fields differ from its predecessor.
 */
function catalog_history_card_change(string $entity, string $cardId, array $versions, ?array $before): ?array
{
    if ($before === null && $versions && $versions[0]['event'] === 'baseline') $before = array_shift($versions);
    if ($before === null) return null;
    $state = $before['p'];
    $lastReal = null;
    foreach ($versions as $version) {
        if (catalog_history_diff($entity, $state, $version['p'])) $lastReal = $version;
        $state = catalog_history_overlay($state, $version['p']);
    }
    if ($lastReal === null) return null;
    $fields = catalog_history_diff($entity, $before['p'], $state);
    if (!$fields) return null;
    $values = [];
    foreach ($fields as $field) $values[$field] = ['before' => $before['p'][$field], 'after' => $state[$field]];
    $group = [];
    foreach (CARD_HISTORY_GROUP_FIELDS[$entity] as $field) $group[$field] = $state[$field] ?? $before['p'][$field] ?? null;
    $nameField = $entity === 'card' ? 'name' : 'name_ru';
    return ['entity' => $entity, 'card_id' => $cardId,
        'name_ru' => $state[$nameField] ?? $before['p'][$nameField] ?? null, 'name_en' => $state['name_en'] ?? $before['p']['name_en'] ?? null,
        'group' => $group, 'changed_fields' => $fields, 'fields' => $values,
        'before' => ['version_id' => $before['id'], 'observed_at' => $before['observed_at'], 'event' => $before['event']],
        'after' => ['version_id' => $lastReal['id'], 'observed_at' => $lastReal['observed_at'], 'event' => $lastReal['event']]];
}

function catalog_history_changes_image(?array $media, string $field): array
{
    if ($media === null) return ['field' => $field, 'status' => 'missing', 'url' => null];
    $archived = ($media['status'] ?? '') === 'archived';
    return ['field' => $field, 'status' => $media['status'] ?? 'missing', 'url' => $archived ? $media['archive_url'] : null,
        'sha256' => $archived ? $media['sha256'] : null, 'mime_type' => $archived ? $media['mime_type'] : null,
        'evidence' => $media['evidence'] ?? null, 'captured_at' => $media['captured_at'] ?? null];
}

/** @return array{0: DateTimeImmutable, 1: DateTimeImmutable} UTC [since, until). */
function catalog_history_waves_window(array $query, ?DateTimeImmutable $now = null): array
{
    $now = ($now ?? new DateTimeImmutable('now', new DateTimeZone('UTC')))->setTimezone(new DateTimeZone('UTC'));
    if (!isset($query['since']) || !is_string($query['since']) || $query['since'] === '') {
        throw new InvalidArgumentException('Параметр since обязателен: время UTC, например 2026-11-01.');
    }
    $since = catalog_history_parse_instant($query['since'], 'since');
    $until = isset($query['until']) && $query['until'] !== '' ? catalog_history_parse_instant((string)$query['until'], 'until') : $now;
    if ($until <= $since) throw new InvalidArgumentException('Параметр until должен быть позже since.');
    if ($until->getTimestamp() - $since->getTimestamp() > CARD_HISTORY_WAVES_MAX_DAYS * 86400) {
        throw new InvalidArgumentException('Период не может быть длиннее ' . CARD_HISTORY_WAVES_MAX_DAYS . ' суток.');
    }
    return [$since, $until];
}

/**
 * "Probable patch" hints: hours in which at least $minCards cards really changed,
 * merged into waves when they are less than CARD_HISTORY_WAVE_GAP_HOURS apart.
 */
function catalog_card_history_waves(PDO $pdo, DateTimeImmutable $since, DateTimeImmutable $until, int $minCards = 5): array
{
    $minCards = max(1, min(1000, $minCards));
    $entities = CARD_HISTORY_CHANGES_ENTITIES;
    $sinceSql = $since->format('Y-m-d H:i:s.u');
    [$timeline, $scanned, $lastObserved] = catalog_history_scan($pdo, $entities, $sinceSql, $until->format('Y-m-d H:i:s.u'));
    $hours = [];
    foreach ($entities as $entity) {
        $cards = $timeline[$entity] ?? [];
        $befores = $cards ? catalog_history_before_versions($pdo, $entity, array_keys($cards), $sinceSql) : [];
        foreach ($cards as $cardId => $versions) {
            $state = ($befores[$cardId] ?? null)['p'] ?? null;
            if ($state === null && $versions && $versions[0]['event'] === 'baseline') $state = array_shift($versions)['p'];
            foreach ($versions as $version) {
                if ($state === null) {
                    $state = $version['p'];
                    continue;
                }
                if (catalog_history_diff($entity, $state, $version['p'])) {
                    $hours[substr($version['observed_at'], 0, 13)][$entity . ':' . $cardId] = $version['observed_at'];
                }
                $state = catalog_history_overlay($state, $version['p']);
            }
        }
    }
    ksort($hours);
    $waves = [];
    $current = null;
    foreach ($hours as $hour => $cards) {
        if (count($cards) < $minCards) continue;
        $start = new DateTimeImmutable($hour . ':00:00', new DateTimeZone('UTC'));
        if ($current !== null && $start->getTimestamp() - $current['end']->getTimestamp() <= CARD_HISTORY_WAVE_GAP_HOURS * 3600) {
            $current['hours'][] = $hour;
            $current['end'] = $start->modify('+1 hour');
        } else {
            if ($current !== null) $waves[] = $current;
            $current = ['start' => $start, 'end' => $start->modify('+1 hour'), 'hours' => [$hour]];
        }
    }
    if ($current !== null) $waves[] = $current;
    $result = [];
    foreach ($waves as $wave) {
        $cards = [];
        $peak = ['hour' => null, 'cards' => 0];
        foreach ($hours as $hour => $hourCards) {
            $at = new DateTimeImmutable($hour . ':00:00', new DateTimeZone('UTC'));
            if ($at < $wave['start'] || $at >= $wave['end']) continue;
            $cards += $hourCards;
            if (count($hourCards) > $peak['cards']) $peak = ['hour' => $at->format('Y-m-d\TH:i:s\Z'), 'cards' => count($hourCards)];
        }
        $byEntity = array_fill_keys($entities, 0);
        foreach (array_keys($cards) as $key) $byEntity[explode(':', $key, 2)[0]]++;
        $result[] = ['from' => $wave['start']->format('Y-m-d\TH:i:s\Z'), 'to' => $wave['end']->format('Y-m-d\TH:i:s\Z'),
            'first_change_at' => min($cards), 'last_change_at' => max($cards),
            'cards' => count($cards), 'by_entity' => $byEntity, 'peak_hour' => $peak];
    }
    return ['since' => $since->format('Y-m-d\TH:i:s\Z'), 'until' => $until->format('Y-m-d\TH:i:s\Z'),
        'min_cards_per_hour' => $minCards, 'waves' => $result,
        'coverage' => ['before_first_observation' => 'unknown', 'timestamps' => 'observed_at', 'versions_scanned' => $scanned,
            'last_observed_at' => $lastObserved]];
}
