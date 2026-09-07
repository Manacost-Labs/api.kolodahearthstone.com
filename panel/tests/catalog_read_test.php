<?php
declare(strict_types=1);
require __DIR__ . '/../lib/catalog_read.php';

function check_reads(bool $ok, string $message): void {
    if (!$ok) throw new RuntimeException($message);
}
// No connection: the recorder fails if a non-list page tries to fetch catalogue rows.
class CatalogCountStatement extends PDOStatement {
    public $sql;
    public function __construct(string $sql) { $this->sql = $sql; }
    #[ReturnTypeWillChange]
    public function fetchColumn($column = 0) { return '42'; }
    #[ReturnTypeWillChange]
    public function fetch($mode = PDO::FETCH_ASSOC, $orientation = PDO::FETCH_ORI_NEXT, $offset = 0) {
        if (preg_match_all('/AS ([a-zA-Z]+Total|total)\b/', $this->sql, $matches)) {
            return array_fill_keys($matches[1], '42');
        }
        return ['generated_by_card_ids_json'=>'["A","B"]', 'related_card_ids_json'=>'["C"]'];
    }
    #[ReturnTypeWillChange]
    public function fetchAll($mode = PDO::FETCH_ASSOC, $className = null, $ctorArgs = null, ...$args) {
        return [['rarity_slug'=>'legendary', 'total'=>'4']];
    }
}
class CatalogCountPDO extends PDO {
    public $queries = [];
    public function __construct() {}
    #[ReturnTypeWillChange]
    public function query($query, $fetchMode = null, ...$args) {
        $this->queries[] = $query;
        return new CatalogCountStatement($query);
    }
    #[ReturnTypeWillChange]
    public function prepare($query, $options = []) {
        throw new RuntimeException('Unexpected catalogue row fetch on another workspace');
    }
}
$nav = ['total','heroTotal','heroSkinsTotal','petsTotal','coinsTotal','timewarpedTotal','constructedTotal'];
$budget = [''=>7, 'minion'=>7, 'spell'=>7, 'hero'=>7, 'hero_skin'=>8, 'pet'=>7,
    'coin'=>8, 'constructed'=>9, 'timewarped'=>7, 'anomaly'=>7, 'quest'=>7, 'darkmoon_prize'=>7, 'reward'=>7, 'trinket'=>7];
foreach ($budget as $section => $count) {
    $pdo = new CatalogCountPDO();
    $stats = panel_catalog_counts($pdo, 'list', $section);
    check_reads(count($pdo->queries) === $count, 'Bounded summary query count: ' . $section);
    foreach ($nav as $name) check_reads($stats[$name] === 42, 'Preserve sidebar total ' . $name);
    if ($section === 'hero_skin') check_reads($stats['heroSkinRarityTotals'] === ['legendary'=>4], 'Rarity totals preserved');
    if ($section === 'coin') check_reads($stats['coinGeneratedByTotal'] === 2 && $stats['coinRelatedTotal'] === 1, 'Coin relations preserved');
    if (!in_array($section, ['hero','hero_skin','pet'], true)) {
        check_reads(strpos(implode(' ', $pdo->queries), 'JSON_') === false, 'No unrelated JSON coverage scans');
    }
    echo 'summary ' . ($section ?: 'bg') . ': queries=' . count($pdo->queries) . "\n";
}
// Execute just the real read-dispatch block, never index.php auth/config/POST routes.
$source = file_get_contents(__DIR__ . '/../index.php');
$start = strpos($source, '$heroes = [];');
$end = strpos($source, '$pageFrom =', $start);
check_reads($start !== false && $end !== false, 'Production read-dispatch boundaries found');
$dispatch = substr($source, $start, $end - $start);
foreach (['analytics', 'parsers', 'api_tokens', 'new', 'edit', 'wiki_terms'] as $action) {
    $pdo = new CatalogCountPDO();
    $cardType = 'hero_skin'; // Stray catalogue parameters must not load data in other workspaces.
    $showHeroes = $showPets = $showCoins = $showTimewarped = $showConstructed = $showLibrary = false;
    $showHeroSkins = true;
    $stats = panel_catalog_counts($pdo, $action, $cardType);
    check_reads(count($pdo->queries) === 7 && array_keys($stats) === $nav, 'Other pages only get sidebar totals');
    $pdo->queries = [];
    eval($dispatch);
    check_reads(count($pdo->queries) === 7, 'Real dispatcher skips list/count/enrichment on ' . $action);
    check_reads($filteredTotal === 0 && $totalPages === 1 && $offset === 0, 'Safe unused pagination state');
    check_reads($cards === [] && $heroSkins === [], 'No unused catalogue rows');
}
echo "OK: production read budgets without database access\n";
