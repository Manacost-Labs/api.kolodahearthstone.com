<?php
declare(strict_types=1);
require __DIR__ . '/../lib/bartender_catalog.php';
$skin = bartender_skin_to_api(['card_id'=>'TB_BaconShopBob_SKIN_A', 'dbf'=>'1', 'parent_card_id'=>'TB_BaconShopBob',
    'name_ru'=>null, 'name_en'=>'Ragnaros', 'image_url'=>null, 'status'=>'catalog_only', 'source'=>'hearthstonejson.com', 'updated_at'=>'2026-10-11']);
if ($skin['dbf'] !== 1 || $skin['media_verified'] || $skin['image_url'] !== null || $skin['status'] !== 'catalog_only') throw new RuntimeException('Catalog-only bartender must not claim complete media');
if ($skin['parent_card_id'] !== 'TB_BaconShopBob') throw new RuntimeException('Bartender must remain separate from playable heroes');
echo "OK: separate bartender catalog and explicit media coverage\n";
