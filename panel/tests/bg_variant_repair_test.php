<?php
declare(strict_types=1);
require __DIR__ . '/../scripts/repair_missing_bg_variants.php';
class VariantStatement extends PDOStatement {
    public function __construct() {}
    public function fetchAll(int $mode = PDO::FETCH_DEFAULT, mixed ...$args): array {
        return [['card_id'=>'BG_CLASSIC','dbf'=>1,'premium_dbf'=>2],['card_id'=>'WRONG_LINK','dbf'=>3,'premium_dbf'=>4]];
    }
}
class VariantPDO extends PDO {
    public function __construct() {}
    public function query(string $query, ?int $fetchMode = null, mixed ...$fetchModeArgs): PDOStatement|false { return new VariantStatement(); }
}
$cards = [['id'=>'TB_BaconUps_FIXTURE','dbfId'=>2,'type'=>'MINION','set'=>'EXPERT1','battlegroundsNormalDbfId'=>1],
    ['id'=>'CONFLICT','dbfId'=>4,'type'=>'MINION','battlegroundsNormalDbfId'=>9]];
$plan = missing_bg_variant_plan(new VariantPDO(), $cards, $cards);
if (count($plan)!==1 || $plan[0]['premium_card_id']!=='TB_BaconUps_FIXTURE') throw new RuntimeException('Non-reciprocal relation accepted');
$cards[0]['type']='SPELL';
if (missing_bg_variant_plan(new VariantPDO(), $cards, $cards)!==[]) throw new RuntimeException('Non-minion premium imported');
echo "OK: missing BG variant repair requires reciprocal public upstream identity\n";
