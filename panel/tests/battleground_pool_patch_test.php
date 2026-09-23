<?php
declare(strict_types=1);

require dirname(__DIR__) . '/scripts/lib/battleground_pool_patch.php';

function check_pool(bool $condition, string $message): void
{
    if (!$condition) throw new RuntimeException($message);
}

$cards = [
    ['id' => 'OLD', 'set' => 'BATTLEGROUNDS', 'type' => 'MINION', 'isBattlegroundsPoolMinion' => true],
    ['id' => 'RETURN', 'set' => 'BATTLEGROUNDS', 'type' => 'MINION'],
    ['id' => 'TOKEN', 'set' => 'BATTLEGROUNDS', 'type' => 'MINION'],
    ['id' => 'GOLD', 'set' => 'BATTLEGROUNDS', 'type' => 'MINION', 'battlegroundsNormalDbfId' => 1],
];
$patch = [
    'patch' => 'test',
    'source_pool_sha256' => battleground_pool_signature($cards),
    'cards' => ['OLD' => ['in_pool' => false], 'RETURN' => ['in_pool' => true]],
];
$updated = apply_battleground_pool_patch($cards, $patch);
check_pool($updated[0]['isBattlegroundsPoolMinion'] === false, 'Removed card must leave the pool');
check_pool($updated[1]['isBattlegroundsPoolMinion'] === true, 'Returning card must enter the pool');
check_pool(!isset($updated[2]['isBattlegroundsPoolMinion']), 'Generated cards must stay outside the pool');
check_pool(!isset($updated[3]['isBattlegroundsPoolMinion']), 'Golden flags must retain source semantics');
check_pool($cards[0]['isBattlegroundsPoolMinion'] === true, 'Source must remain untouched');
check_pool($updated[1]['_poolCorrection']['patch'] === 'test', 'Correction must retain provenance');
check_pool(battleground_pool_signature(array_reverse($cards)) === $patch['source_pool_sha256'], 'Order and locale must not change the signature');
check_pool(apply_battleground_pool_patch($updated, $patch) === $updated, 'Corrected upstream must need no override');

$changed = $cards;
$changed[] = ['id' => 'NEW_PATCH', 'set' => 'BATTLEGROUNDS', 'type' => 'MINION'];
try {
    apply_battleground_pool_patch($changed, $patch);
    throw new LogicException('Unknown upstream pool must not receive a stale correction');
} catch (RuntimeException $error) {
    check_pool(str_contains($error->getMessage(), 'review'), 'Unknown snapshot must request patch review');
}
$changed[0]['isBattlegroundsPoolMinion'] = false;
$changed[1]['isBattlegroundsPoolMinion'] = true;
check_pool(apply_battleground_pool_patch($changed, $patch) === $changed, 'Upstream catch-up must retire the correction');

$manifest = json_decode(file_get_contents(dirname(__DIR__) . '/data/battleground-pool-36.6.1.json'), true, 512, JSON_THROW_ON_ERROR);
check_pool(count(array_filter($manifest['cards'], fn($c) => $c['reason'] === 'removed')) === 35, 'Official removals must remain complete');
check_pool(count(array_filter($manifest['cards'], fn($c) => $c['reason'] === 'returning')) === 22, 'Returning Tavern cards exclude three generated Volumizers');
check_pool($manifest['cards']['BGDUO_700']['in_pool'] && $manifest['cards']['BGDUO_701']['in_pool'], 'New Duo cards must enter the pool');
check_pool(!$manifest['cards']['BGFYM_000']['in_pool'] && !$manifest['cards']['BG34_170t']['in_pool'], 'Deities and Volumizers are generated cards');
check_pool(!$manifest['cards']['BG36_360']['in_pool'] && $manifest['cards']['BG36_360t3']['in_pool'], 'Dark Paradox uses playable versions, not its placeholder');
echo "Battleground pool patch tests passed\n";
