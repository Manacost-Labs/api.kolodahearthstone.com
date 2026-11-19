<?php
declare(strict_types=1);
require __DIR__ . '/../lib/card_history_changes.php';

function check(bool $condition, string $message): void
{
    if (!$condition) throw new RuntimeException($message);
}

class ChangesTestStatement extends PDOStatement
{
    public array $rows = [];
    private Closure $answer;
    public function __construct(Closure $answer) { $this->answer = $answer; }
    public function execute(?array $params = null): bool { $this->rows = ($this->answer)($params ?? []); return true; }
    public function fetch(int $mode = PDO::FETCH_DEFAULT, int $cursorOrientation = PDO::FETCH_ORI_NEXT, int $cursorOffset = 0): mixed { return $this->rows[0] ?? false; }
    public function fetchAll(int $mode = PDO::FETCH_DEFAULT, mixed ...$args): array { return $this->rows; }
}

/** In-memory catalog_card_versions / catalog_card_version_media answering the exact query shapes. */
class ChangesTestPDO extends PDO
{
    public array $versions = [];
    public array $media = [];
    public array $queries = [];
    public int $rangeRows = 0;
    private int $nextId = 1;
    public function __construct() {}

    public function add(string $entity, string $card, string $observedAt, array $snapshot, string $event = 'changed', ?int $id = null): int
    {
        $id ??= $this->nextId;
        $this->nextId = max($this->nextId, $id) + 1;
        $this->versions[$id] = ['id' => $id, 'entity' => $entity, 'card_id' => $card, 'observed_at' => $observedAt . '.000000',
            'event_kind' => $event, 'snapshot_json' => json_encode($snapshot, JSON_UNESCAPED_UNICODE)];
        ksort($this->versions);
        return $id;
    }

    public function query(string $query, ?int $fetchMode = null, mixed ...$fetchModeArgs): PDOStatement|false
    {
        check(str_contains($query, 'MIN(id)'), 'Unexpected unbound query');
        $ids = array_keys($this->versions);
        $stmt = new ChangesTestStatement(fn() => [['min_id' => $ids ? min($ids) : null, 'max_id' => $ids ? max($ids) : null]]);
        $stmt->execute();
        return $stmt;
    }

    public function prepare(string $query, array $options = []): PDOStatement|false
    {
        $this->queries[] = $query;
        if (str_contains($query, ' AS hit ')) return new ChangesTestStatement(function (array $p): array {
            foreach ($this->versions as $row) {
                if ($row['id'] >= $p['id']) return [['hit' => (int)($row['observed_at'] >= $p['at'])]];
            }
            return [];
        });
        if (str_contains($query, 'catalog_card_version_media')) return new ChangesTestStatement(function (array $p): array {
            $rows = [];
            foreach ($this->media as $row) if (in_array($row['version_id'], $p, true)) $rows[] = $row;
            return $rows;
        });
        if (str_contains($query, 'GROUP BY card_id')) return new ChangesTestStatement(function (array $p) use ($query): array {
            check(str_contains($query, 'observed_at < :from_at'), 'The before lookup must be bounded by the window start');
            $cards = array_filter($p, fn($key) => str_starts_with((string)$key, 'card'), ARRAY_FILTER_USE_KEY);
            $latest = [];
            foreach ($this->versions as $row) {
                if ($row['entity'] === $p['entity'] && in_array($row['card_id'], $cards, true) && $row['observed_at'] < $p['from_at']
                    && in_array($row['event_kind'], ['baseline', 'changed', 'renamed'], true)) $latest[$row['card_id']] = $row;
            }
            return array_values($latest);
        });
        check(str_contains($query, 'id > :after AND id <= :high'), 'Version scan must use an id range');
        return new ChangesTestStatement(function (array $p): array {
            $entities = array_filter($p, fn($key) => str_starts_with((string)$key, 'entity'), ARRAY_FILTER_USE_KEY);
            $rows = [];
            foreach ($this->versions as $row) {
                if ($row['id'] > $p['after'] && $row['id'] <= $p['high']) $this->rangeRows++;
                if ($row['id'] > $p['after'] && $row['id'] <= $p['high'] && $row['observed_at'] >= $p['from_at'] && $row['observed_at'] < $p['to_at']
                    && in_array($row['entity'], $entities, true) && in_array($row['event_kind'], ['baseline', 'changed', 'renamed'], true)) $rows[] = $row;
            }
            return array_slice($rows, 0, CARD_HISTORY_SCAN_CHUNK);
        });
    }
}

function constructed(array $override = []): array
{
    return $override + ['card_id' => 'X', 'name_ru' => 'Карта', 'name_en' => 'Card', 'mana_cost' => 3, 'attack' => 2, 'health' => 3,
        'text_ru' => '<b>Боевой клич:</b> получите 1 карту.', 'text_en' => '<b>Battlecry:</b> Draw a card.', 'card_type' => 'minion',
        'rarity' => 'rare', 'class_slug' => 'mage', 'collectible' => 1, 'card_set' => 'CORE', 'local_image_url' => '/u/X.png',
        'source_hash' => 'h1', 'source_payload_json' => '{"a":1}', 'updated_at' => '2026-11-01 00:00:00'];
}

function battlegrounds(array $override = []): array
{
    return $override + ['card_id' => 'BG1', 'name' => 'Страж', 'name_en' => 'Guard', 'card_type' => 'minion', 'creature_type' => 'demon',
        'tavern_tier' => 4, 'attack' => 5, 'health' => 3, 'in_pool' => 1, 'variant_kind' => 'base',
        'notes' => "Предсмертный хрип: существа получают +2/+2.\nМеханики: DEATHRATTLE\nEN: Deathrattle: Give minions +2/+2.",
        'source_hash' => 'h1'];
}

[$from, $to] = catalog_history_changes_window(['from' => '2026-11-03 15:55', 'to' => '2026-11-05T00:00:00Z']);
$pdo = new ChangesTestPDO();
// Before the window.
$pdo->add('constructed_card', 'BE_036', '2026-10-13 20:29:02', constructed(['name_ru' => 'МАТРИАРХ', 'mana_cost' => 9]));
$pdo->add('constructed_card', 'NOISE', '2026-10-13 20:29:02', constructed());
$pdo->add('constructed_card', 'HEALTH', '2026-10-13 20:29:02', constructed(['name_ru' => 'Рыцарь-гибрид', 'health' => 3]));
$pdo->add('card', 'BG36_362', '2026-10-11 23:57:19', battlegrounds(['attack' => 5]));
$pdo->add('card', 'BG_TEXT', '2026-10-11 23:57:19', battlegrounds());
$pdo->add('card', 'BG_TEXT_G', '2026-10-11 23:57:19', battlegrounds(['variant_kind' => 'golden', 'attack' => 10]));
$pdo->add('constructed_card', 'REVERT', '2026-10-13 20:29:02', constructed(['attack' => 4]));
$pdo->add('constructed_card', 'LOSTDATA', '2026-10-13 20:29:02', constructed());
$pdo->add('constructed_card', 'OUTSIDE', '2026-10-13 20:29:02', constructed());
// Inside the window.
$pdo->add('constructed_card', 'BE_036', '2026-11-03 20:29:37', constructed(['name_ru' => 'МАТРИАРХ', 'mana_cost' => 10, 'local_image_url' => '/u/new.png', 'source_hash' => 'h2']));
$pdo->add('constructed_card', 'BE_036', '2026-11-04 12:00:00', constructed(['name_ru' => 'МАТРИАРХ', 'mana_cost' => 10, 'source_hash' => 'h3', 'updated_at' => '2026-11-04 12:00:00']));
// Service-only: mirror switch, new hash/payload, provider text flapping and case flapping.
$pdo->add('constructed_card', 'NOISE', '2026-11-03 18:00:00', constructed(['local_image_url' => '/uploads/mirror.png', 'source_hash' => 'h2']));
$pdo->add('constructed_card', 'NOISE', '2026-11-03 19:00:00', constructed(['text_ru' => '<b>Боевой клич:</b>& получите 1  карту.', 'text_en' => "<b>Battlecry:</b> Draw\u{00A0}a card.", 'rarity' => 'RARE', 'class_slug' => 'MAGE', 'card_type' => 'MINION', 'source_payload_json' => '{"a":2}']));
$pdo->add('constructed_card', 'HEALTH', '2026-11-04 01:00:00', constructed(['name_ru' => 'Рыцарь-гибрид', 'health' => 4, 'source_hash' => 'h9']));
$pdo->add('card', 'BG36_362', '2026-11-04 08:13:07', battlegrounds(['attack' => 6]));
$pdo->add('card', 'BG36_362', '2026-11-04 09:22:32', battlegrounds(['attack' => 6, 'source_hash' => 'h5']));
$pdo->add('card', 'BG_TEXT', '2026-11-04 08:13:07', battlegrounds(['notes' => "[x]Предсмертный хрип: существа\nполучают +2/+3.\nМеханики: DEATHRATTLE, TRIGGER_VISUAL\nEN: [x]Deathrattle: Give minions\n+2/+3."]));
$pdo->add('card', 'BG_TEXT_G', '2026-11-04 08:13:07', battlegrounds(['variant_kind' => 'golden', 'attack' => 20]));
$pdo->add('constructed_card', 'REVERT', '2026-11-03 17:00:00', constructed(['attack' => 6]));
$pdo->add('constructed_card', 'REVERT', '2026-11-04 17:00:00', constructed(['attack' => 4]));
$pdo->add('constructed_card', 'LOSTDATA', '2026-11-03 17:00:00', constructed(['mana_cost' => null, 'attack' => null, 'health' => null, 'text_ru' => null]));
$pdo->add('constructed_card', 'NEWCARD', '2026-11-03 17:00:00', constructed(), 'new');
$pdo->add('constructed_card', 'FIRSTSEEN', '2026-11-03 17:00:00', constructed(['mana_cost' => 1]), 'baseline');
$pdo->add('constructed_card', 'FIRSTSEEN', '2026-11-03 17:00:01', constructed(['mana_cost' => 2]));
// After the window: must not be reported or used as "after".
$pdo->add('constructed_card', 'OUTSIDE', '2026-11-05 00:00:00', constructed(['mana_cost' => 7]));
$pdo->add('constructed_card', 'BE_036', '2026-11-08 06:48:23', constructed(['name_ru' => 'МАТРИАРХ', 'mana_cost' => 5]));
$pdo->media = [
    ['version_id' => 1, 'field_name' => 'image_url', 'status' => 'archived', 'reason' => null, 'archive_url' => '/uploads/card-history-media/before.png',
        'sha256' => str_repeat('a', 64), 'byte_size' => '10', 'mime_type' => 'image/png', 'evidence' => 'captured_after_observation', 'captured_at' => '2026-11-15 06:40:18'],
    ['version_id' => 10, 'field_name' => 'image_url', 'status' => 'archived', 'reason' => null, 'archive_url' => '/uploads/card-history-media/after.png',
        'sha256' => str_repeat('b', 64), 'byte_size' => '10', 'mime_type' => 'image/png', 'evidence' => 'captured_after_observation', 'captured_at' => '2026-11-15 06:40:18'],
    ['version_id' => 10, 'field_name' => 'local_image_url', 'status' => 'archived', 'reason' => null, 'archive_url' => '/uploads/card-history-media/mirror.png',
        'sha256' => str_repeat('c', 64), 'byte_size' => '10', 'mime_type' => 'image/png', 'evidence' => 'captured_after_observation', 'captured_at' => '2026-11-15 06:40:18'],
];

$result = catalog_card_history_changes($pdo, CARD_HISTORY_CHANGES_ENTITIES, $from, $to);
$byId = [];
foreach ($result['changes'] as $change) $byId[$change['card_id']] = $change;
check(array_keys($byId) === ['BE_036', 'FIRSTSEEN', 'HEALTH', 'BG36_362', 'BG_TEXT'],
    'Only cards with real changes belong in the feed: ' . implode(',', array_keys($byId)));
check($result['window']['from'] === '2026-11-03T15:55:00Z' && $result['window']['to'] === '2026-11-05T00:00:00Z', 'Window must be echoed in UTC');

$matriarch = $byId['BE_036'];
check($matriarch['entity'] === 'constructed_card' && $matriarch['name_ru'] === 'МАТРИАРХ', 'Identity and name');
check($matriarch['changed_fields'] === ['mana_cost'], 'Mirror and hash changes are not card changes');
check($matriarch['fields']['mana_cost'] === ['before' => 9, 'after' => 10], 'Before and after values');
check($matriarch['before']['version_id'] === 1 && $matriarch['after']['version_id'] === 10, 'before = last version before from, after = last in-window version with a real change');
check($matriarch['after']['observed_at'] === '2026-11-03 20:29:37.000000', 'Later service-only and out-of-window versions are not "after"');
check($matriarch['before']['image']['url'] === '/uploads/card-history-media/before.png' && $matriarch['after']['image']['url'] === '/uploads/card-history-media/after.png', 'Archived images come from the entity media key');
check($matriarch['after']['image']['field'] === 'image_url' && $matriarch['after']['image']['sha256'] === str_repeat('b', 64), 'Image evidence');
check($matriarch['group']['class_slug'] === 'mage' && $matriarch['group']['collectible'] === true, 'Grouping data');

check($byId['HEALTH']['fields']['health'] === ['before' => 3, 'after' => 4] && $byId['HEALTH']['name_ru'] === 'Рыцарь-гибрид', 'Health change');
check($byId['HEALTH']['before']['image']['status'] === 'missing' && $byId['HEALTH']['before']['image']['url'] === null, 'No archived image is reported honestly');
check($byId['BG36_362']['fields']['attack'] === ['before' => 5, 'after' => 6] && $byId['BG36_362']['changed_fields'] === ['attack'], 'Battlegrounds attack change');
check($byId['BG36_362']['group']['tavern_tier'] === 4 && $byId['BG36_362']['name_ru'] === 'Страж', 'Battlegrounds grouping');
check($byId['BG36_362']['before']['image']['field'] === 'card_image', 'Battlegrounds images use card_image');
check($byId['BG_TEXT']['changed_fields'] === ['text_ru', 'text_en'], 'Battlegrounds text comes from notes and ignores re-wrapping');
check($byId['BG_TEXT']['fields']['text_ru'] === ['before' => 'Предсмертный хрип: существа получают +2/+2.', 'after' => 'Предсмертный хрип: существа получают +2/+3.'], 'Battlegrounds RU text');
check(!isset($byId['BG_TEXT_G']), 'Golden variants are derived records');
check(!isset($byId['NOISE']), 'Service-only versions must not produce a change');
check(!isset($byId['REVERT']), 'A change reverted inside the window is not a change');
check(!isset($byId['LOSTDATA']), 'Fields that disappeared are lost data, not a change');
check(!isset($byId['NEWCARD']) && !isset($byId['OUTSIDE']), 'New cards and cards changed outside the window are excluded');
check($byId['FIRSTSEEN']['fields']['mana_cost'] === ['before' => 1, 'after' => 2], 'A window baseline row is the previous state of a card first stored mid-window');
check($result['pagination']['total'] === 5 && $result['coverage']['versions_scanned'] > 0, 'Totals');

// entity filter, pagination, empty windows.
$only = catalog_card_history_changes($pdo, ['card'], $from, $to);
check(array_column($only['changes'], 'card_id') === ['BG36_362', 'BG_TEXT'], 'Entity filter');
$secondPage = catalog_card_history_changes($pdo, CARD_HISTORY_CHANGES_ENTITIES, $from, $to, 2, 2);
check(array_column($secondPage['changes'], 'card_id') === ['HEALTH', 'BG36_362'] && $secondPage['pagination']['total_pages'] === 3, 'Pagination');
[$emptyFrom, $emptyTo] = catalog_history_changes_window(['from' => '2020-01-01', 'to' => '2020-01-02']);
$empty = catalog_card_history_changes($pdo, CARD_HISTORY_CHANGES_ENTITIES, $emptyFrom, $emptyTo);
check($empty['changes'] === [] && $empty['pagination']['total'] === 0 && $empty['coverage']['versions_scanned'] === 0, 'Empty window gives an empty list');
$emptyDb = catalog_card_history_changes(new ChangesTestPDO(), CARD_HISTORY_CHANGES_ENTITIES, $from, $to);
check($emptyDb['changes'] === [], 'Empty history table');
try { catalog_card_history_changes($pdo, ['hero'], $from, $to); throw new RuntimeException('Invalid entity accepted'); } catch (InvalidArgumentException $expected) {}

// Waves: hours with enough real changes, merged when close; noise never counts.
$pdo->add('constructed_card', 'FAR', '2026-10-13 20:29:02', constructed());
$pdo->add('constructed_card', 'FAR', '2026-11-10 12:30:00', constructed(['attack' => 9]));
$pdo->add('constructed_card', 'FAR', '2026-11-10 12:45:00', constructed(['attack' => 9, 'source_hash' => 'z']));
$waveSince = new DateTimeImmutable('2026-11-03 15:55:00', new DateTimeZone('UTC'));
$waveUntil = new DateTimeImmutable('2026-11-12 00:00:00', new DateTimeZone('UTC'));
$waves = catalog_card_history_waves($pdo, $waveSince, $waveUntil, 2);
check(count($waves['waves']) === 1, 'Two busy hours 15 hours apart form one wave; a single change is below the threshold');
check($waves['waves'][0]['from'] === '2026-11-03T17:00:00Z' && $waves['waves'][0]['to'] === '2026-11-04T09:00:00Z', 'Wave bounds are whole UTC hours');
check($waves['waves'][0]['cards'] === 6 && $waves['waves'][0]['by_entity'] === ['constructed_card' => 4, 'card' => 2], 'Wave counts distinct cards with real changes only');
check($waves['waves'][0]['peak_hour']['cards'] === 2 && $waves['waves'][0]['first_change_at'] === '2026-11-03 17:00:00.000000', 'Wave peak and first change');
$all = catalog_card_history_waves($pdo, $waveSince, $waveUntil, 1)['waves'];
check(count($all) === 3 && $all[1]['from'] === '2026-11-08T06:00:00Z' && $all[2]['from'] === '2026-11-10T12:00:00Z' && $all[2]['cards'] === 1, 'Changes more than 18 hours apart are separate waves, service-only rows do not extend them');
check(catalog_card_history_waves($pdo, $emptyFrom, $emptyTo)['waves'] === [], 'No waves without changes');
foreach ([[], ['since' => 'x'], ['since' => '2026-11-05', 'until' => '2026-11-01'], ['since' => '2026-09-01', 'until' => '2026-11-01']] as $query) {
    try {
        catalog_history_waves_window($query, new DateTimeImmutable('2026-11-19', new DateTimeZone('UTC')));
        throw new RuntimeException('Bad waves window accepted: ' . json_encode($query));
    } catch (InvalidArgumentException $expected) {}
}

// The window is located by id range: 700 old rows are never read by the scan.
$range = new ChangesTestPDO();
$range->add('constructed_card', 'S', '2026-10-01 00:00:00', constructed());
for ($i = 0; $i < 700; $i++) $range->add('constructed_card', 'F' . $i, '2026-10-02 00:00:00', constructed());
$range->add('constructed_card', 'Z', '2026-10-02 00:00:00', constructed());
$range->add('constructed_card', 'S', '2026-11-03 17:00:00', constructed(['mana_cost' => 8]), 'changed', 1000);
$range->add('constructed_card', 'Z', '2026-11-03 16:00:00', constructed(['health' => 9]), 'changed', 1001);
$range->add('constructed_card', 'Z', '2026-11-06 16:00:00', constructed(['health' => 1]), 'changed', 1002);
$found = array_column(catalog_card_history_changes($range, ['constructed_card'], $from, $to)['changes'], 'card_id');
check($found === ['S', 'Z'], 'Window rows are located by id range: ' . implode(',', $found));
check($range->rangeRows > 0 && $range->rangeRows < 400, 'The scan must read an id range, not the table: ' . $range->rangeRows . ' rows');
foreach ($range->queries as $query) {
    check(!preg_match('/WHERE observed_at/', $query), 'No table-wide time filter');
}

// Chunked scanning keeps every row of a large window.
$big = new ChangesTestPDO();
for ($i = 0; $i < 450; $i++) $big->add('constructed_card', 'C' . $i, '2026-10-01 00:00:00', constructed(['mana_cost' => 1]));
for ($i = 0; $i < 450; $i++) $big->add('constructed_card', 'C' . $i, '2026-11-03 18:00:00', constructed(['mana_cost' => 2]));
$bigResult = catalog_card_history_changes($big, ['constructed_card'], $from, $to, 1, 200);
check($bigResult['pagination']['total'] === 450 && count($bigResult['changes']) === 200 && $bigResult['coverage']['versions_scanned'] === 450, 'Chunked scan');

// Request validation: precise 400 reasons, UTC normalization.
foreach ([[], ['from' => 'yesterday'], ['from' => '2026-02-31'], ['from' => '2026-11-03', 'to' => '2026-11-03'],
        ['from' => '2026-11-01', 'to' => '2026-11-16'], ['from' => '2026-11-05', 'to' => '2026-11-03']] as $query) {
    try {
        catalog_history_changes_window($query, new DateTimeImmutable('2026-11-19 12:00:00', new DateTimeZone('UTC')));
        throw new RuntimeException('Bad window accepted: ' . json_encode($query));
    } catch (InvalidArgumentException $expected) {
        check($expected->getMessage() !== '', 'The error explains the problem');
    }
}
[$a, $b] = catalog_history_changes_window(['from' => '2026-11-03T18:55:00 03:00', 'to' => '2026-11-17T00:00:00Z']);
check($a->format('c') === '2026-11-03T15:55:00+00:00' && $b->format('c') === '2026-11-17T00:00:00+00:00', 'Offsets, including an unescaped "+", convert to UTC');
[$a, $b] = catalog_history_changes_window(['from' => '2026-11-18'], new DateTimeImmutable('2026-11-19 12:00:00', new DateTimeZone('UTC')));
check($b->format('c') === '2026-11-19T12:00:00+00:00', 'to defaults to now');
[$a, $b] = catalog_history_changes_window(['from' => '2026-11-03', 'to' => '2026-11-17']);
check($b->getTimestamp() - $a->getTimestamp() === 14 * 86400, 'Exactly 14 days is allowed');

echo "OK: card history change feed ignores service noise, finds the window by id range and returns before/after media\n";
