<?php
declare(strict_types=1);
require __DIR__ . '/../lib/card_history.php';

class HistoryTestStatement extends PDOStatement
{
    public array $rows;
    public array $bindings = [];
    public function __construct(array $rows) { $this->rows = $rows; }
    public function execute(?array $params = null): bool { $this->bindings = $params ?? []; return true; }
    public function fetch(int $mode = PDO::FETCH_DEFAULT, int $cursorOrientation = PDO::FETCH_ORI_NEXT, int $cursorOffset = 0): mixed { return $this->rows[0]; }
    public function fetchAll(int $mode = PDO::FETCH_DEFAULT, mixed ...$args): array { return $this->rows; }
}
class HistoryTestPDO extends PDO
{
    public array $queries = [];
    public array $statements = [];
    public function __construct() {}
    public function prepare(string $query, array $options = []): PDOStatement|false
    {
        $this->queries[] = $query;
        $rows = strpos($query, 'COUNT(*)') !== false
            ? [['total'=>1, 'first_observed_at'=>'2026-10-11 10:00:00', 'last_observed_at'=>'2026-10-11 11:00:00']]
            : [['id'=>10, 'observed_at'=>'2026-10-11 10:00:00', 'event_kind'=>'baseline', 'previous_hash'=>null, 'snapshot_hash'=>'abc',
                'snapshot_json'=>'{"card_id":"A","mana_cost":3,"source_payload_json":"{\"identity\":1}"}']];
        $stmt = new HistoryTestStatement($rows);
        $this->statements[] = $stmt;
        return $stmt;
    }
}
$pdo = new HistoryTestPDO();
$result = catalog_card_history($pdo, 'constructed_card', "quote' OR 1=1 --", 0, 999);
if ($result['versions'][0]['snapshot']['source_payload'] !== ['identity'=>1]) throw new RuntimeException('Provenance must be structured');
if ($result['coverage']['before_first_observation'] !== 'unknown' || $result['coverage']['image_bytes_archived']) throw new RuntimeException('Historical coverage must be honest');
if ($result['coverage']['last_observed_at'] !== '2026-10-11 11:00:00') throw new RuntimeException('Conditional caches must use the latest version time');
if ($result['pagination']['page'] !== 1 || $result['pagination']['per_page'] !== 200) throw new RuntimeException('Pagination must be bounded');
foreach ($pdo->queries as $query) {
    if (strpos($query, "quote'") !== false) throw new RuntimeException('Card ID interpolated into SQL');
}
if ($pdo->statements[0]->bindings['card_id'] !== "quote' OR 1=1 --") throw new RuntimeException('Missing native PDO binding');
try {
    catalog_card_history($pdo, 'bad_entity', 'A');
    throw new RuntimeException('Invalid entity accepted');
} catch (InvalidArgumentException $expected) {}
echo "OK: card history snapshots, coverage, pagination and native bindings\n";
