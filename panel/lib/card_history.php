<?php
declare(strict_types=1);

const CARD_HISTORY_ENTITIES = ['constructed_card', 'card', 'hero', 'hero_skin', 'library_card', 'timewarped_card', 'pet', 'coin', 'bartender_skin'];

/** Full normalized snapshots from the moment version capture was installed. */
function catalog_card_history(PDO $pdo, string $entity, string $cardId, int $page = 1, int $perPage = 50): array
{
    if (!in_array($entity, CARD_HISTORY_ENTITIES, true) || $cardId === '' || strlen($cardId) > 191) {
        throw new InvalidArgumentException('Invalid history identity');
    }
    $page = max(1, min(1000000, $page));
    $perPage = max(1, min(200, $perPage));
    $parameters = ['entity' => $entity, 'card_id' => $cardId];
    $stmt = $pdo->prepare('SELECT COUNT(*) AS total, MIN(observed_at) AS first_observed_at, MAX(observed_at) AS last_observed_at FROM catalog_card_versions WHERE entity = :entity AND card_id = :card_id');
    $stmt->execute($parameters);
    $summary = $stmt->fetch(PDO::FETCH_ASSOC);
    $offset = ($page - 1) * $perPage;
    $stmt = $pdo->prepare('SELECT id, observed_at, event_kind, previous_hash, snapshot_hash, snapshot_json FROM catalog_card_versions WHERE entity = :entity AND card_id = :card_id ORDER BY id DESC LIMIT ' . $perPage . ' OFFSET ' . $offset);
    $stmt->execute($parameters);
    $versions = [];
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $snapshot = json_decode((string)$row['snapshot_json'], true, 512, JSON_THROW_ON_ERROR);
        // The database column stores source JSON as text; expose structured data.
        if (isset($snapshot['source_payload_json']) && is_string($snapshot['source_payload_json'])) {
            $snapshot['source_payload'] = json_decode($snapshot['source_payload_json'], true);
            unset($snapshot['source_payload_json']);
        }
        $versions[] = [
            'version_id' => (int)$row['id'], 'observed_at' => $row['observed_at'],
            'previous_card_id' => $snapshot['_previous_card_id'] ?? null,
            'event' => $row['event_kind'], 'previous_hash' => $row['previous_hash'],
            'snapshot_hash' => $row['snapshot_hash'], 'snapshot' => $snapshot,
        ];
    }
    return ['entity' => $entity, 'card_id' => $cardId, 'versions' => $versions,
        'coverage' => ['first_observed_at' => $summary['first_observed_at'],
            'last_observed_at' => $summary['last_observed_at'],
            'before_first_observation' => 'unknown', 'timestamps' => 'observed_at',
            'image_bytes_archived' => false, 'legacy_bg_changes_included' => false],
        'pagination' => ['page' => $page, 'per_page' => $perPage, 'total' => (int)$summary['total'],
            'total_pages' => (int)ceil((int)$summary['total'] / $perPage)],
    ];
}

/** Original HSJ/Blizzard snapshots; this older log has incomplete coverage. */
function catalog_legacy_bg_history(PDO $pdo, string $cardId, int $page = 1, int $perPage = 50): array
{
    if ($cardId === '' || strlen($cardId) > 64) throw new InvalidArgumentException('Invalid history identity');
    $page = max(1, min(1000000, $page));
    $perPage = max(1, min(200, $perPage));
    $stmt = $pdo->prepare('SELECT COUNT(*) AS total, MIN(created_at) AS first_observed_at, MAX(created_at) AS last_observed_at FROM battlegrounds_card_changes WHERE card_id = :card_id');
    $stmt->execute(['card_id'=>$cardId]);
    $summary = $stmt->fetch(PDO::FETCH_ASSOC);
    $stmt = $pdo->prepare('SELECT id, source, old_hash, new_hash, change_type, payload_json, created_at FROM battlegrounds_card_changes WHERE card_id = :card_id ORDER BY id DESC LIMIT ' . $perPage . ' OFFSET ' . (($page - 1) * $perPage));
    $stmt->execute(['card_id'=>$cardId]);
    $versions = [];
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $versions[] = ['version_id'=>(int)$row['id'], 'observed_at'=>$row['created_at'],
            'source'=>$row['source'], 'event'=>$row['change_type'], 'previous_hash'=>$row['old_hash'],
            'snapshot_hash'=>$row['new_hash'], 'snapshot'=>json_decode((string)$row['payload_json'], true),
            'snapshot_kind'=>'upstream_payload'];
    }
    return ['entity'=>'card', 'card_id'=>$cardId, 'versions'=>$versions,
        'coverage'=>['first_observed_at'=>$summary['first_observed_at'], 'last_observed_at'=>$summary['last_observed_at'],
            'complete'=>false, 'timestamps'=>'observed_at', 'image_bytes_archived'=>false],
        'pagination'=>['page'=>$page, 'per_page'=>$perPage, 'total'=>(int)$summary['total'],
            'total_pages'=>(int)ceil((int)$summary['total'] / $perPage)]];
}
