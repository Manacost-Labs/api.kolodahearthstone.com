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

/** Both locales must describe a complete, identical catalog before a new pool is accepted. */
function validate_battleground_catalog_snapshots(array $ru, array $en, int $minimumCards = 10000, int $minimumPool = 100): void
{
    $identities = [];
    foreach ([$ru, $en] as $cards) {
        if (count($cards) < $minimumCards) throw new RuntimeException('Incomplete HearthstoneJSON catalog');
        $ids = []; $dbfs = []; $pool = 0;
        foreach ($cards as $card) {
            if (!is_array($card) || !is_string($card['id'] ?? null)
                || preg_match('/^[A-Za-z0-9_]{1,64}$/D', $card['id']) !== 1
                || !is_int($card['dbfId'] ?? null) || $card['dbfId'] <= 0
                || isset($ids[$card['id']]) || isset($dbfs[$card['dbfId']])) {
                throw new RuntimeException('Invalid or duplicate HearthstoneJSON identity');
            }
            $flags = [];
            foreach (['isBattlegroundsPoolMinion', 'isBattlegroundsPoolSpell'] as $flag) {
                if (isset($card[$flag]) && !is_bool($card[$flag])) throw new RuntimeException('Invalid Battleground pool status');
                $flags[] = !empty($card[$flag]);
            }
            $ids[$card['id']] = [$card['dbfId'], $card['set'] ?? null, $card['type'] ?? null,
                $card['battlegroundsNormalDbfId'] ?? null, $flags];
            $dbfs[$card['dbfId']] = true;
            if (($card['type'] ?? '') === 'MINION' && empty($card['battlegroundsNormalDbfId']) && $flags[0]) $pool++;
        }
        if ($pool < $minimumPool) throw new RuntimeException('Incomplete Battleground pool');
        ksort($ids, SORT_STRING);
        $identities[] = $ids;
    }
    if ($identities[0] !== $identities[1]) throw new RuntimeException('HearthstoneJSON locale identities or pools differ');
}

/** A historical hotfix applies only to its exact reviewed upstream roster. */
function apply_battleground_pool_patch(array $cards, array $patch, bool $validatedSnapshot = false): array
{
    if ($validatedSnapshot && battleground_pool_signature($cards) !== $patch['source_pool_sha256']) {
        return $cards;
    }
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
        $card['_poolCorrection'] = ['patch'=>$patch['patch'], 'source'=>$patch['source'] ?? null,
            'original_in_pool'=>!empty($card['isBattlegroundsPoolMinion'])];
        $card['isBattlegroundsPoolMinion'] = $entry['in_pool'];
    }
    unset($card);
    return $cards;
}
