<?php
declare(strict_types=1);

/** The existing source history retains spell prices from each card scan. */
function panel_next_attach_purchase_costs(PDO $pdo, array $records, string $cardType): array
{
    if (!$records || !in_array($cardType, ['', 'minion', 'spell'], true)) return $records;
    $ids = array_values(array_unique(array_filter(array_column($records, 'card_id'), 'is_string')));
    if (!$ids) return $records;
    $placeholders = implode(',', array_fill(0, count($ids), '?'));
    $stmt = $pdo->prepare("SELECT history.card_id, history.payload_json
        FROM battlegrounds_card_changes AS history
        JOIN (SELECT card_id, MAX(id) AS id FROM battlegrounds_card_changes
              WHERE source = 'hearthstonejson' AND card_id IN ($placeholders)
              GROUP BY card_id) AS latest ON history.id = latest.id");
    $stmt->execute($ids);
    $costs = [];
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) as $source) {
        $payload = json_decode((string)$source['payload_json'], true);
        if (!is_array($payload)) continue;
        $value = $payload['ru']['cost'] ?? $payload['en']['cost'] ?? null;
        if (!is_int($value) && !(is_string($value) && preg_match('/^\d+$/D', $value))) continue;
        $cost = filter_var($value, FILTER_VALIDATE_INT);
        if ($cost !== false && $cost >= 0) $costs[$source['card_id']] = $cost;
    }
    foreach ($records as &$record) {
        if (($record['card_type'] ?? '') === 'spell') $record['cost'] = $costs[$record['card_id'] ?? ''] ?? null;
    }
    unset($record);
    return $records;
}

function panel_next_enrich_records(array $records, string $cardType, array $golden, array $wiki): array
{
    foreach ($records as &$record) {
        if (in_array($cardType, ['', 'minion', 'spell'], true)) {
            $record['golden_variant'] = $golden[(int)($record['dbf'] ?? 0)] ?? null;
            $record['wiki'] = $wiki[(string)($record['card_id'] ?? '')] ?? null;
        } elseif ($cardType === 'constructed') {
            $record['wiki'] = $wiki[(string)($record['card_id'] ?? '')] ?? null;
        }
    }
    return $records;
}

/** Explicit presentation contract: never serialize a scope or config wholesale. */
function panel_next_page(array $input): array
{
    $keys = [
        'action', 'title', 'csrf', 'logoutCsrf', 'parserCsrf', 'message', 'error',
        'cardType', 'records', 'form', 'page', 'perPage', 'totalPages', 'total',
        'from', 'to', 'categories', 'tribes', 'mediaLabels', 'rarities', 'activeFilters',
    ];
    $result = ['ok' => empty($input['error']), 'version' => 1];
    foreach ($keys as $key) {
        $result[$key] = $input[$key] ?? null;
    }
    $result['user'] = [
        'id' => (int)($input['user']['id'] ?? 0),
        'login' => (string)($input['user']['login'] ?? ''),
    ];
    $extra = $input['extra'] ?? [];
    foreach (['terms', 'termLabels', 'tokens', 'managerId', 'tokenConfigured', 'tokenError', 'issueNonce', 'scopeCatalog', 'issuedToken'] as $key) {
        if (array_key_exists($key, $extra)) {
            $result[$key] = $extra[$key];
        }
    }
    return $result;
}

function panel_next_json(array $payload): void
{
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: private, no-store, max-age=0');
    header('Pragma: no-cache');
    header('X-Content-Type-Options: nosniff');
    if (empty($payload['ok']) && ($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST' && http_response_code() < 400) {
        http_response_code(422);
    }
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
    exit;
}
