<?php
declare(strict_types=1);

const ACQUISITION_REPORT_MAX_BYTES = 1048576;
const ACQUISITION_DETAIL_STATES = ['succeeded', 'absent', 'failed', 'retry', 'running', 'pending', 'not_requested'];

function acquisition_count($value, int $max = 9007199254740991): bool
{
    return is_int($value) && $value >= 0 && $value <= $max;
}

function acquisition_time($value): ?int
{
    if (!is_string($value) || !preg_match('/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/D', $value)) return null;
    $time = strtotime($value);
    return $time !== false && gmdate('Y-m-d\TH:i:s\Z', $time) === $value ? $time : null;
}

function analytics_acquisition_read(): array
{
    // A server-owned path only. Browser input can never select a file or URL.
    $path = getenv('HS_ACQUISITION_PANEL_REPORT');
    if ($path === false || $path === '') return ['state' => 'not_configured', 'payload' => null];
    $unavailable = ['state' => 'unavailable', 'payload' => null];
    if ($path[0] !== '/' || is_link($path) || !is_file($path)) return $unavailable;
    $handle = @fopen($path, 'rb');
    if ($handle === false) return $unavailable;
    try {
        $stat = fstat($handle);
        if (!$stat || ($stat['mode'] & 0170000) !== 0100000 || $stat['size'] > ACQUISITION_REPORT_MAX_BYTES) return $unavailable;
        $body = stream_get_contents($handle, ACQUISITION_REPORT_MAX_BYTES + 1);
        if (!is_string($body) || strlen($body) > ACQUISITION_REPORT_MAX_BYTES) return $unavailable;
        $payload = json_decode($body, true, 32, JSON_THROW_ON_ERROR);
        return ['state' => 'available', 'payload' => $payload];
    } catch (Throwable $exception) {
        return $unavailable; // Never expose the path or artifact/error contents.
    } finally {
        fclose($handle);
    }
}

function acquisition_valid_observation(array $row, int $now): bool
{
    $coverage = $row['coverage'] ?? null;
    if (!is_array($coverage) || ($coverage['source_id'] ?? null) !== ($row['source_id'] ?? null)) return false;
    if (!is_string($coverage['scope_id'] ?? null) || !preg_match('/^[a-f0-9]{64}$/D', $coverage['scope_id'])) return false;
    if (!is_string($coverage['snapshot_id'] ?? null) || trim($coverage['snapshot_id']) === '' || strlen($coverage['snapshot_id']) > 1024) return false;
    if (!is_array($coverage['query'] ?? null) || count($coverage['query']) > 50 || !is_string($coverage['fragment'] ?? null) || strlen($coverage['fragment']) > 1024) return false;
    foreach ($coverage['query'] as $key => $values) {
        if (strlen((string)$key) > 128 || !is_array($values) || count($values) > 20) return false;
        foreach ($values as $value) if (!is_string($value) || strlen($value) > 1024) return false;
    }
    if (($coverage['patch_id'] ?? null) !== null && (!is_string($coverage['patch_id']) || trim($coverage['patch_id']) === '' || strlen($coverage['patch_id']) > 1024)) return false;
    foreach (['credits_spent', 'unknown_cost_attempts'] as $key) {
        if (($row[$key] ?? null) !== null && !acquisition_count($row[$key])) return false;
    }
    $observed = $row['observed_at'] ?? null;
    if ($observed !== null && (acquisition_time($observed) === null || acquisition_time($observed) > $now + 5)) return false;
    if (!is_bool($coverage['listing_complete'] ?? null) || !is_bool($coverage['view_confirmed'] ?? null)) return false;
    if (($coverage['status'] ?? null) === 'unknown') {
        foreach (['found', 'expected_count', 'listing_percent', 'details', 'unresolved_details'] as $key) {
            if (!array_key_exists($key, $coverage) || $coverage[$key] !== null) return false;
        }
        return !$coverage['listing_complete'] && !$coverage['view_confirmed'];
    }
    if ($observed === null) return false;
    $found = $coverage['found'] ?? null;
    $expected = $coverage['expected_count'] ?? null;
    $counts = $coverage['details'] ?? null;
    if (!acquisition_count($found, 100000) || !is_array($counts) || count($counts) !== count(ACQUISITION_DETAIL_STATES)) return false;
    if ($expected !== null && (!acquisition_count($expected) || $expected < $found)) return false;
    foreach (ACQUISITION_DETAIL_STATES as $key) if (!acquisition_count($counts[$key] ?? null, $found)) return false;
    if (array_sum($counts) !== $found) return false;
    if ($coverage['listing_complete'] && $expected !== null && $found !== $expected) return false;
    $unresolved = $found - $counts['succeeded'] - $counts['absent'];
    $state = $coverage['listing_complete'] && $coverage['view_confirmed'] && $unresolved === 0 ? 'complete_for_view' : 'partial';
    $percent = $expected ? 100 * $found / $expected : null;
    $actualPercent = $coverage['listing_percent'] ?? null;
    return ($coverage['status'] ?? null) === $state
        && ($coverage['unresolved_details'] ?? null) === $unresolved
        // Python rounds decimal ties differently from PHP. Accept the half-cent
        // precision of a two-decimal producer without weakening count checks.
        && ($percent === null ? $actualPercent === null : ((is_float($actualPercent) || is_int($actualPercent)) && abs($actualPercent - $percent) <= 0.005000001));
}

function acquisition_observation_index(array $artifact, int $now): ?array
{
    $payload = $artifact['payload'] ?? null;
    if (($artifact['state'] ?? null) !== 'available' || !is_array($payload) || ($payload['schema_version'] ?? null) !== 1) return null;
    $generated = acquisition_time($payload['generated_at'] ?? null);
    if ($generated === null || $generated > $now + 5 || !is_array($payload['sources'] ?? null) || count($payload['sources']) > 1000) return null;
    $indexed = [];
    foreach ($payload['sources'] as $row) {
        if (!is_array($row) || !is_string($row['source_id'] ?? null) || isset($indexed[$row['source_id']])) return null;
        if (!acquisition_valid_observation($row, $now)) return null;
        if (($row['observed_at'] ?? null) !== null && acquisition_time($row['observed_at']) > $generated + 5) return null;
        $indexed[$row['source_id']] = $row;
    }
    return $indexed;
}

function analytics_acquisition_normalize(array $definition, array $fetch, array $artifact, array $query): array
{
    $now = time();
    $indexed = acquisition_observation_index($artifact, $now);
    $catalog = $fetch['payload']['sources'] ?? null;
    if (!is_array($catalog) || !$catalog || count($catalog) > 1000) throw new RuntimeException('Реестр источников недоступен.');
    $rows = [];
    $totals = ['complete_for_view' => 0, 'partial' => 0, 'unknown' => 0, 'stale' => 0];
    $seen = [];
    foreach ($catalog as $source) {
        $id = is_array($source) ? ($source['source_id'] ?? null) : null;
        if (!is_string($id) || !preg_match('/^[a-z][a-z0-9_]{0,127}$/D', $id) || isset($seen[$id]) || !is_string($source['site'] ?? null) || !is_string($source['description'] ?? null)) throw new RuntimeException('Реестр источников некорректен.');
        $seen[$id] = true;
        $item = $indexed[$id] ?? null;
        $coverage = $item['coverage'] ?? null;
        $observed = $item['observed_at'] ?? null;
        $state = $coverage['status'] ?? 'unknown';
        if ($state !== 'unknown' && $now - acquisition_time($observed) > 86400) $state = 'stale';
        $totals[$state]++;
        $counts = $coverage['details'] ?? null;
        $found = $coverage['found'] ?? null;
        $expected = $coverage['expected_count'] ?? null;
        $rows[] = [
            'source' => $id, 'site' => (string)($source['site'] ?? ''), 'description' => (string)($source['description'] ?? ''),
            'state_code' => $state, 'state' => ['unknown' => 'Не проверено', 'partial' => 'Частичный снимок', 'complete_for_view' => 'Полный снимок', 'stale' => 'Снимок старше суток'][$state],
            'found' => $found, 'expected_count' => $expected,
            'listing' => $found === null ? 'Не измерено' : (string)$found . ' / ' . ($expected === null ? '?' : (string)$expected),
            'detail_progress' => $counts === null ? 'Не измерено' : (string)$counts['succeeded'] . ' / ' . (string)$found,
            'unresolved_details' => $coverage['unresolved_details'] ?? null,
            'observed_at' => $observed, 'snapshot_id' => $observed === null ? null : ($coverage['snapshot_id'] ?? null),
            'patch_id' => $coverage['patch_id'] ?? null, 'scope_id' => $observed === null ? null : ($coverage['scope_id'] ?? null),
            'query' => $coverage['query'] ?? null, 'fragment' => $coverage['fragment'] ?? null,
            'listing_complete' => $coverage['listing_complete'] ?? null, 'view_confirmed' => $coverage['view_confirmed'] ?? null,
            'detail_states' => $counts, 'credits_spent' => $item['credits_spent'] ?? null,
            'unknown_cost_attempts' => $item['unknown_cost_attempts'] ?? null,
            'evidence_note' => 'Состояние на момент наблюдения. Не подтверждает свежесть, публикацию, другие фильтры или работу исполнителя сейчас.',
        ];
    }
    $order = ['partial' => 0, 'stale' => 1, 'unknown' => 2, 'complete_for_view' => 3];
    usort($rows, static function (array $a, array $b) use ($order): int {
        return ($order[$a['state_code']] <=> $order[$b['state_code']]) ?: strcmp($a['source'], $b['source']);
    });
    $search = trim((string)($query['q'] ?? ''));
    $filter = $query['coverage_state'] ?? 'all';
    $rows = array_values(array_filter($rows, static function (array $row) use ($search, $filter): bool {
        return ($filter === 'all' || $row['state_code'] === $filter)
            && ($search === '' || stripos($row['source'] . ' ' . $row['site'] . ' ' . $row['description'], $search) !== false);
    }));
    $warnings = $indexed === null ? ['Отчёт покрытия не подключён или недоступен. Статусы источников не оценивались.'] : [];
    if ($indexed !== null && (array_diff_key($seen, $indexed) || array_diff_key($indexed, $seen))) $warnings[] = 'Состав отчёта отличается от реестра; отсутствующие источники не проверены.';
    if ($totals['stale']) $warnings[] = 'Есть снимки старше суток. Их очередь и показатели могут измениться.';
    $result = analytics_result_shell('acquisition', $definition, $fetch, [
        ['label' => 'Источников', 'value' => count($catalog)],
        ['label' => 'Полных снимков', 'value' => $totals['complete_for_view']],
        ['label' => 'Частичных', 'value' => $totals['partial']],
        ['label' => 'Не проверено', 'value' => $totals['unknown']],
        ['label' => 'Старше суток', 'value' => $totals['stale']],
    ], [
        analytics_column('source', 'Источник', 'code'), analytics_column('state', 'Покрытие', 'status'),
        analytics_column('listing', 'Найдено / ожидается'), analytics_column('detail_progress', 'Подробности / найдено'),
        analytics_column('unresolved_details', 'Не завершено', 'number'), analytics_column('observed_at', 'Наблюдение · UTC', 'date'),
    ], $rows, ['warnings' => $warnings]);
    $result['meta']['updated_at'] = $indexed === null ? null : $artifact['payload']['generated_at'];
    $result['meta']['total'] = count($rows);
    $result['meta']['report_state'] = $indexed === null ? 'unavailable' : 'available';
    return $result;
}
