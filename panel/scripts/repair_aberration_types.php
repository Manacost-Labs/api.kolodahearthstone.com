<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/battleground_creature_types.php';
$config = require __DIR__ . '/../config.php';
$apply = in_array('--apply', $argv, true);
$pdo = new PDO($config['db']['dsn'], $config['db']['user'], $config['db']['password'], [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES => false,
]);
try {
    if ($apply) $pdo->beginTransaction();
    $sql = "SELECT c.card_id, c.card_type, c.creature_type, c.in_pool, c.duos_only,
        c.variant_kind, h.payload_json FROM battlegrounds_cards c
        JOIN battlegrounds_card_changes h ON h.id = (
            SELECT MAX(id) FROM battlegrounds_card_changes WHERE card_id = c.card_id
        ) WHERE c.card_type = 'minion' AND (c.creature_type IS NULL OR c.creature_type = '')";
    $rows = $pdo->query($sql . ($apply ? ' FOR UPDATE' : ''))->fetchAll();
    $plan = [];
    foreach ($rows as $row) {
        $payload = json_decode($row['payload_json'] ?? '', true);
        if (!is_array($payload) || !battleground_aberration_repair($row, $payload)) continue;
        unset($row['payload_json']);
        $plan[] = $row;
    }
    if ($apply) {
        $update = $pdo->prepare("UPDATE battlegrounds_cards SET creature_type = 'aberration'
            WHERE card_id = ? AND (creature_type IS NULL OR creature_type = '')");
        foreach ($plan as $row) {
            $update->execute([$row['card_id']]);
            if ($update->rowCount() !== 1) throw new RuntimeException('Catalog changed during repair');
        }
        $pdo->commit();
    }
    echo json_encode(['mode'=>$apply ? 'applied' : 'plan', 'count'=>count($plan), 'cards'=>$plan], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . "\n";
} catch (Throwable $error) {
    if ($pdo->inTransaction()) $pdo->rollBack();
    fwrite(STDERR, "Aberration repair failed; no transaction changes were kept.\n");
    exit(1);
}
