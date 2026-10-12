<?php
declare(strict_types=1);

/** Only repair explicit reciprocal HSJ relations, without changing existing cards. */
function missing_bg_variant_plan(PDO $pdo, array $ruCards, array $enCards): array
{
    $byDbf = [];
    $ruById = [];
    foreach ($enCards as $card) {
        if (is_array($card) && isset($card['dbfId'])) $byDbf[(int)$card['dbfId']] = $card;
    }
    foreach ($ruCards as $card) {
        if (is_array($card) && isset($card['id'])) $ruById[(string)$card['id']] = $card;
    }
    $rows = $pdo->query("SELECT b.card_id, b.dbf, b.premium_dbf FROM battlegrounds_cards b LEFT JOIN battlegrounds_cards g ON g.dbf=b.premium_dbf WHERE b.variant_kind='base' AND b.premium_dbf IS NOT NULL AND g.dbf IS NULL ORDER BY b.card_id")->fetchAll(PDO::FETCH_ASSOC);
    $plan = [];
    foreach ($rows as $row) {
        $en = $byDbf[(int)$row['premium_dbf']] ?? null;
        if (!$en || ($en['type'] ?? null) !== 'MINION' || empty($en['id'])) continue;
        if ((int)($en['battlegroundsNormalDbfId'] ?? 0) !== (int)$row['dbf']) continue;
        $ru = $ruById[(string)$en['id']] ?? $en;
        if ((int)($ru['dbfId'] ?? 0) !== (int)$row['premium_dbf'] || (int)($ru['battlegroundsNormalDbfId'] ?? 0) !== (int)$row['dbf']) continue;
        $plan[] = ['base_card_id'=>$row['card_id'], 'premium_card_id'=>$en['id'], 'premium_dbf'=>(int)$row['premium_dbf'], 'ru'=>$ru, 'en'=>$en];
    }
    return $plan;
}

if (realpath($_SERVER['SCRIPT_FILENAME'] ?? '') === __FILE__) {
    $options = getopt('', ['apply', 'ru:', 'en:']);
    if (empty($options['ru']) || empty($options['en'])) {
        fwrite(STDERR, "Usage: repair_missing_bg_variants.php --ru public-ru.json --en public-en.json [--apply]\n");
        exit(2);
    }
    $ru = json_decode(file_get_contents($options['ru']), true, 512, JSON_THROW_ON_ERROR);
    $en = json_decode(file_get_contents($options['en']), true, 512, JSON_THROW_ON_ERROR);
    $config = require '/etc/api-kolodahearthstone/panel-config.php';
    $pdo = new PDO($config['db']['dsn'], $config['db']['user'], $config['db']['password'], [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION]);
    $plan = missing_bg_variant_plan($pdo, $ru, $en);
    if (isset($options['apply'])) {
        define('KOLODA_SCAN_LIBRARY_ONLY', true);
        require __DIR__ . '/scan_cards.php';
        $pdo->beginTransaction();
        try {
            foreach ($plan as $entry) {
                $card = normalize_card($entry['ru'], $entry['en']);
                if (!$card || $card['variant_kind'] !== 'golden' || $card['dbf'] !== $entry['premium_dbf']) throw new RuntimeException('Variant normalization mismatch');
                $card['base_card_id'] = $entry['base_card_id'];
                $card['in_pool'] = 0; // Triple forms never enter the purchasable base pool.
                upsert_card($pdo, $card);
            }
            $pdo->commit();
        } catch (Throwable $error) {
            $pdo->rollBack();
            throw $error;
        }
    }
    echo json_encode(['applied'=>isset($options['apply']), 'missing_variants'=>count($plan), 'cards'=>array_map(static fn($row)=>['base_card_id'=>$row['base_card_id'],'premium_card_id'=>$row['premium_card_id']],$plan)], JSON_UNESCAPED_SLASHES), PHP_EOL;
}
