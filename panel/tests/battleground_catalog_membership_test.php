<?php
declare(strict_types=1);
// Evaluate only the pure eligibility function. Never execute importer/network code.
$source = file_get_contents(__DIR__ . '/../scripts/scan_cards.php');
$start = strpos($source, 'function is_battlegrounds_catalog_card(');
$end = strpos($source, 'function normalize_card(', $start);
eval(substr($source, $start, $end - $start));
$cases = [
    [['set'=>'BATTLEGROUNDS', 'type'=>'MINION'], [], true],
    [['set'=>'EXPERT1', 'type'=>'MINION', 'battlegroundsNormalDbfId'=>108851], [], true],
    [['set'=>'BASIC', 'type'=>'MINION', 'battlegroundsPremiumDbfId'=>57340], [], true],
    [['set'=>'KARA', 'type'=>'MINION'], ['battlegroundsNormalDbfId'=>108851], true],
    [['set'=>'EXPERT1', 'type'=>'MINION'], [], false],
    [['set'=>'EXPERT1', 'type'=>'MINION', 'battlegroundsNormalDbfId'=>true], [], false],
    [['set'=>'EXPERT1', 'type'=>'MINION', 'battlegroundsNormalDbfId'=>0], [], false],
    [['set'=>'EXPERT1', 'type'=>'MINION', 'battlegroundsNormalDbfId'=>-1], [], false],
    [['set'=>'EXPERT1', 'type'=>'SPELL', 'battlegroundsNormalDbfId'=>108851], [], false],
];
foreach ($cases as [$ru, $en, $expected]) {
    if (is_battlegrounds_catalog_card($ru, $en) !== $expected) throw new RuntimeException('BG membership regression');
}
if (strpos($source, 'if (!is_battlegrounds_catalog_card($ru, $en))') === false) throw new RuntimeException('Eligibility gate not wired into normalization');
echo "OK: original-set BG variants accepted only with authoritative relationships\n";
$start = strpos($source, 'function resolve_bg_base_card_id(');
$end = strpos($source, 'function upsert_card(', $start);
eval(substr($source, $start, $end - $start));
class BaseLinkStatement extends PDOStatement {
    public array $parameters=[];
    public function __construct() {}
    public function execute(?array $params=null): bool {$this->parameters=$params;return true;}
    public function fetchColumn(int $column=0): mixed {return $this->parameters['dbf']===108851?'BG_CLASSIC':false;}
}
class BaseLinkPDO extends PDO {
    public function __construct() {}
    public function prepare(string $query,array $options=[]):PDOStatement|false {return new BaseLinkStatement();}
}
$pdo=new BaseLinkPDO();
if(resolve_bg_base_card_id($pdo,['variant_kind'=>'golden','base_dbf'=>108851])!=='BG_CLASSIC')throw new RuntimeException('Known base link cleared during normalization');
if(resolve_bg_base_card_id($pdo,['variant_kind'=>'base'])!==null)throw new RuntimeException('Base card treated as premium');
echo "OK: known premium base links remain stable before the versioned write\n";
