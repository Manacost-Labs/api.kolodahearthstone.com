<?php
declare(strict_types=1);
require __DIR__ . '/../lib/full_card_catalog.php';
set_error_handler(static function (int $severity, string $message): never { throw new RuntimeException($message); });
$row = ['card_id'=>'BE_222t', 'dbf'=>'130406', 'collectible'=>'0', 'updated_at'=>'2026-10-15 00:00:00',
    'payload_ru'=>json_encode(['id'=>'BE_222t','name'=>'Безднокрыл','type'=>'MINION','set'=>'BE','attack'=>8]),
    'payload_en'=>json_encode(['id'=>'BE_222t','name'=>'Voidwing','type'=>'MINION','set'=>'BE','attack'=>8])];
$card = full_catalog_card_to_api($row);
if ($card['collectible'] || $card['formats'] !== [] || !$card['catalog_only']) throw new RuntimeException('Invented membership');
if ($card['name']['ru'] !== 'Безднокрыл' || $card['dbf'] !== 130406 || $card['attack'] !== 8) throw new RuntimeException('Lost token fields');
if (!str_ends_with($card['images']['card'], '/BE_222t.png') || $card['media_verified']) throw new RuntimeException('Incorrect media provenance');
$row['payload_ru'] = $row['payload_en'] = json_encode(['type'=>'ENCHANTMENT']);
if (full_catalog_card_to_api($row)['images']['card'] !== null) throw new RuntimeException('Fabricated effect image');

class CatalogTestPDO extends PDO { public function __construct() {} }
$pdo = new CatalogTestPDO();
foreach ([['page'=>[]], ['per_page'=>201], ['page'=>0], ['collectible'=>'false'], ['q'=>[]], ['set'=>[]]] as $query) {
    try { full_catalog_listing($pdo, $query); throw new RuntimeException('Accepted invalid query'); }
    catch (InvalidArgumentException $expected) {}
}
try { full_catalog_find($pdo, 'injected_column', 'x'); throw new RuntimeException('Accepted unsafe key'); }
catch (InvalidArgumentException $expected) {}
echo "OK: complete catalog tokens, provenance and query validation\n";
