<?php
declare(strict_types=1);

function battleground_pool_signature(array $cards): string
{
    $rows = [];
    foreach ($cards as $card) {
        if (($card['set'] ?? '') !== 'BATTLEGROUNDS' || ($card['type'] ?? '') !== 'MINION'
            || !empty($card['battlegroundsNormalDbfId'])) continue;
        $rows[] = $card['id'] . ':' . (!empty($card['isBattlegroundsPoolMinion']) ? '1' : '0');
    }
    sort($rows, SORT_STRING);
    return hash('sha256', implode("\n", $rows));
}

/**
 * Correct only the reviewed pre-hotfix pool. A new, conflicting upstream roster
 * must be reviewed before importing, rather than silently undoing the hotfix.
 * Once the upstream agrees with the official changes, no correction is applied.
 */
function apply_battleground_pool_patch(array $cards, array $patch): array
{
    $byId = [];
    foreach ($cards as $card) $byId[$card['id']] = $card;
    $matchesUpstream = true;
    foreach ($patch['cards'] as $id => $entry) {
        if (!isset($byId[$id]) || !is_bool($entry['in_pool'] ?? null)) {
            throw new RuntimeException('Battleground pool patch requires review: missing card or invalid status ' . $id);
        }
        if (!empty($byId[$id]['isBattlegroundsPoolMinion']) !== $entry['in_pool']) $matchesUpstream = false;
    }
    if ($matchesUpstream) return $cards;
    if (battleground_pool_signature($cards) !== $patch['source_pool_sha256']) {
        throw new RuntimeException('Battleground source pool changed; review patch ' . $patch['patch'] . ' before importing');
    }
    foreach ($cards as &$card) {
        $entry = $patch['cards'][$card['id']] ?? null;
        if ($entry === null || !empty($card['isBattlegroundsPoolMinion']) === $entry['in_pool']) continue;
        $card['_poolCorrection'] = [
            'patch' => $patch['patch'],
            'source' => $patch['source'] ?? null,
            'original_in_pool' => !empty($card['isBattlegroundsPoolMinion']),
        ];
        $card['isBattlegroundsPoolMinion'] = $entry['in_pool'];
    }
    unset($card);
    return $cards;
}
