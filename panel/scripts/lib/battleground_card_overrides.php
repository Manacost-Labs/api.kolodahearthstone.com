<?php
declare(strict_types=1);

// Manual corrections of Battlegrounds cards made in the panel.
//
// The HearthstoneJSON import rewrites every card on each run. A correction is
// stored per field together with the upstream value it replaced, so the import
// can tell three cases apart:
//   - upstream still has the replaced value: keep the manual value;
//   - upstream now matches the manual value: the correction is no longer needed;
//   - upstream changed to something else: a newer game patch wins and the
//     correction is released into the change log for review.

const BG_CARD_OVERRIDE_FIELDS = [
    'name', 'name_en', 'card_type', 'tavern_tier', 'creature_type',
    'attack', 'health', 'in_pool', 'duos_only', 'notes',
];
const BG_CARD_OVERRIDE_INT_FIELDS = ['tavern_tier', 'attack', 'health'];
const BG_CARD_OVERRIDE_FLAG_FIELDS = ['in_pool', 'duos_only'];

function bg_card_override_normalize(string $field, $value)
{
    if (in_array($field, BG_CARD_OVERRIDE_FLAG_FIELDS, true)) {
        return (int)(bool)$value;
    }
    if (in_array($field, BG_CARD_OVERRIDE_INT_FIELDS, true)) {
        return $value === null || trim((string)$value) === '' ? null : (int)$value;
    }
    // Browsers submit textarea line breaks as CRLF; the import writes LF.
    $text = trim(str_replace(["\r\n", "\r"], "\n", (string)($value ?? '')));

    return $text === '' ? null : $text;
}

function bg_card_override_encode(string $field, $value): string
{
    return json_encode(bg_card_override_normalize($field, $value), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
}

function bg_card_override_decode(string $field, ?string $encoded)
{
    return bg_card_override_normalize($field, $encoded === null ? null : json_decode($encoded, true, 512, JSON_THROW_ON_ERROR));
}

/**
 * Compares a panel submission with the stored row.
 *
 * @param array<string, array{manual_value: ?string, upstream_value: ?string}> $overrides
 * @return array{upsert: array<string, array{manual: mixed, upstream: mixed}>, delete: list<string>, edits: array<string, array{from: mixed, to: mixed}>}
 */
function bg_card_override_changes(array $current, array $submitted, array $overrides): array
{
    $result = ['upsert' => [], 'delete' => [], 'edits' => []];
    foreach (BG_CARD_OVERRIDE_FIELDS as $field) {
        if (!array_key_exists($field, $submitted)) {
            continue;
        }
        $before = bg_card_override_normalize($field, $current[$field] ?? null);
        $after = bg_card_override_normalize($field, $submitted[$field]);
        if ($before === $after) {
            continue;
        }
        $result['edits'][$field] = ['from' => $before, 'to' => $after];
        $upstream = isset($overrides[$field])
            ? bg_card_override_decode($field, $overrides[$field]['upstream_value'] ?? null)
            : $before;
        if ($after === $upstream) {
            // Typing the upstream value back simply removes the correction.
            if (isset($overrides[$field])) {
                $result['delete'][] = $field;
            }
            continue;
        }
        $result['upsert'][$field] = ['manual' => $after, 'upstream' => $upstream];
    }

    return $result;
}

/**
 * Applies stored corrections to a freshly imported card.
 *
 * @param array<string, array{manual_value: ?string, upstream_value: ?string}> $overrides
 * @return array{card: array, kept: list<string>, settled: list<string>, released: array<string, array{manual: mixed, replaced: mixed, upstream: mixed}>}
 */
function bg_card_override_apply(array $card, array $overrides): array
{
    $result = ['card' => $card, 'kept' => [], 'settled' => [], 'released' => []];
    foreach ($overrides as $field => $override) {
        if (!in_array($field, BG_CARD_OVERRIDE_FIELDS, true) || !array_key_exists($field, $card)) {
            continue;
        }
        $incoming = bg_card_override_normalize($field, $card[$field]);
        $manual = bg_card_override_decode($field, $override['manual_value'] ?? null);
        $replaced = bg_card_override_decode($field, $override['upstream_value'] ?? null);
        if ($incoming === $manual) {
            $result['settled'][] = $field;
        } elseif ($incoming === $replaced) {
            $result['card'][$field] = $manual;
            $result['kept'][] = $field;
        } else {
            $result['released'][$field] = ['manual' => $manual, 'replaced' => $replaced, 'upstream' => $incoming];
        }
    }

    return $result;
}

function bg_card_overrides_ensure_schema(PDO $pdo): void
{
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS battlegrounds_card_overrides (
            card_id VARCHAR(64) NOT NULL,
            field_name VARCHAR(32) NOT NULL,
            manual_value TEXT DEFAULT NULL,
            upstream_value TEXT DEFAULT NULL,
            created_by VARCHAR(64) NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (card_id, field_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ");
}

/** @return array<string, array<string, array{manual_value: ?string, upstream_value: ?string}>> card_id => field => row */
function bg_card_overrides_load_all(PDO $pdo): array
{
    $rows = $pdo->query('SELECT card_id, field_name, manual_value, upstream_value FROM battlegrounds_card_overrides')->fetchAll(PDO::FETCH_ASSOC);
    $byCard = [];
    foreach ($rows as $row) {
        $byCard[(string)$row['card_id']][(string)$row['field_name']] = [
            'manual_value' => $row['manual_value'],
            'upstream_value' => $row['upstream_value'],
        ];
    }

    return $byCard;
}

/** @return array<string, array{manual_value: ?string, upstream_value: ?string}> */
function bg_card_overrides_load(PDO $pdo, string $cardId): array
{
    $stmt = $pdo->prepare('SELECT field_name, manual_value, upstream_value FROM battlegrounds_card_overrides WHERE card_id = ?');
    $stmt->execute([$cardId]);
    $overrides = [];
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $overrides[(string)$row['field_name']] = [
            'manual_value' => $row['manual_value'],
            'upstream_value' => $row['upstream_value'],
        ];
    }

    return $overrides;
}

function bg_card_overrides_delete(PDO $pdo, string $cardId, array $fields): void
{
    $stmt = $pdo->prepare('DELETE FROM battlegrounds_card_overrides WHERE card_id = ? AND field_name = ?');
    foreach ($fields as $field) {
        $stmt->execute([$cardId, $field]);
    }
}

function bg_card_overrides_store(PDO $pdo, string $cardId, array $changes, string $author): void
{
    bg_card_overrides_delete($pdo, $cardId, array_merge($changes['delete'], array_keys($changes['upsert'])));
    $insert = $pdo->prepare('
        INSERT INTO battlegrounds_card_overrides (card_id, field_name, manual_value, upstream_value, created_by)
        VALUES (?, ?, ?, ?, ?)
    ');
    foreach ($changes['upsert'] as $field => $value) {
        $insert->execute([
            $cardId,
            $field,
            bg_card_override_encode($field, $value['manual']),
            bg_card_override_encode($field, $value['upstream']),
            mb_substr($author, 0, 64),
        ]);
    }
}

/** Persists a panel edit: corrections, a renamed card_id and an audit entry. */
function bg_card_overrides_record_edit(PDO $pdo, string $oldCardId, string $newCardId, array $changes, string $author): void
{
    if ($oldCardId !== $newCardId) {
        $pdo->prepare('DELETE FROM battlegrounds_card_overrides WHERE card_id = ?')->execute([$newCardId]);
        $pdo->prepare('UPDATE battlegrounds_card_overrides SET card_id = ? WHERE card_id = ?')->execute([$newCardId, $oldCardId]);
    }
    bg_card_overrides_store($pdo, $newCardId, $changes, $author);
    if ($changes['edits'] !== []) {
        bg_card_overrides_log($pdo, $newCardId, 'panel', 'manual_edit', [
            'author' => $author,
            'fields' => $changes['edits'],
            'protected' => array_keys($changes['upsert']),
        ]);
    }
}

// change_type is VARCHAR(16): manual_edit, manual_settled, manual_released.
function bg_card_overrides_log(PDO $pdo, string $cardId, string $source, string $type, array $payload, ?string $sourceHash = null): void
{
    $stmt = $pdo->prepare('
        INSERT INTO battlegrounds_card_changes (card_id, source, old_hash, new_hash, change_type, payload_json)
        VALUES (?, ?, NULL, ?, ?, ?)
    ');
    $stmt->execute([
        $cardId,
        $source,
        $sourceHash,
        $type,
        json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR),
    ]);
}
